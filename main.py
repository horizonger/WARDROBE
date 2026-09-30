import sys
import re
import sqlite3
import uuid
import random
import shutil
import time
import traceback
import os
import json
import tempfile
import subprocess
import hashlib
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal, QSettings, QUrl, QSize
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut, QIcon, QPainter, QColor, QPixmap
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QPushButton, QFrame, QScrollArea, QLineEdit,
    QComboBox, QTextEdit, QMessageBox, QStackedWidget, QInputDialog,
    QDialog, QCheckBox, QSizePolicy
)


BASE_DIR = Path(__file__).resolve().parent
LEGACY_DATA_FILE = BASE_DIR / "gardirob.txt"

# Keep user data outside the application folder so installed/upgraded Windows
# builds can replace the executable without touching the user's wardrobe data.
_local_appdata = os.environ.get("LOCALAPPDATA")
if _local_appdata:
    APP_DATA_DIR = Path(_local_appdata) / "WARDROBE"
else:
    APP_DATA_DIR = Path.home() / ".wardrobe"

DB_FILE = APP_DATA_DIR / "wardrobe.db"
BACKUP_DIR = APP_DATA_DIR / "backups"
LOG_DIR = APP_DATA_DIR / "logs"
LEGACY_ARCHIVE_DIR = APP_DATA_DIR / "legacy"
VERSION_FILE = BASE_DIR / "version.json"

def _load_app_version():
    env_version = os.environ.get("WARDROBE_APP_VERSION", "").strip()
    if env_version:
        return env_version.lstrip("v")
    try:
        if VERSION_FILE.exists():
            data = json.loads(VERSION_FILE.read_text(encoding="utf-8"))
            value = str(data.get("version", "")).strip()
            if value:
                return value.lstrip("v")
    except Exception:
        pass
    return "0.0.0"

APP_VERSION = _load_app_version()
UPDATE_CONFIG_FILE = BASE_DIR / "update_config.json"
ICON_DIR = BASE_DIR / "icons"
APP_ICON_FILE = BASE_DIR / "wardrobe.ico"

def _load_update_repository():
    configured = os.environ.get("WARDROBE_GITHUB_REPO", "").strip()
    if configured:
        return configured
    try:
        if UPDATE_CONFIG_FILE.exists():
            data = json.loads(UPDATE_CONFIG_FILE.read_text(encoding="utf-8"))
            value = str(data.get("github_repository", "")).strip()
            if value:
                return value
    except Exception:
        pass
    return ""

UPDATE_REPOSITORY = _load_update_repository()
DB_SCHEMA_VERSION = 1
BACKUP_RETENTION_COUNT = 10
BACKUP_MAX_AGE_DAYS = 90
AUTO_BACKUP_INTERVAL_HOURS = 24


def prepare_user_data():
    """Prepare the Windows user-data directory and perform a one-time move."""
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LEGACY_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    # Keep the backup directory database-only. Legacy TXT artifacts are moved
    # into a separate archive folder so they are never mistaken for real backups.
    for legacy_file in list(BACKUP_DIR.glob("*.txt")):
        try:
            shutil.move(str(legacy_file), str(LEGACY_ARCHIVE_DIR / legacy_file.name))
        except OSError:
            pass

    legacy_db = BASE_DIR / "wardrobe.db"
    if not DB_FILE.exists() and legacy_db.exists():
        source = sqlite3.connect(legacy_db)
        target = sqlite3.connect(DB_FILE)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

    legacy_backups = BASE_DIR / "backups"
    if legacy_backups.exists():
        for legacy_file in legacy_backups.iterdir():
            if not legacy_file.is_file():
                continue
            destination_dir = BACKUP_DIR if legacy_file.suffix.lower() == ".db" else LEGACY_ARCHIVE_DIR
            destination = destination_dir / legacy_file.name
            if not destination.exists():
                try:
                    shutil.copy2(legacy_file, destination)
                except OSError:
                    pass

LIGHT_COLORS = {
    "bg": "#edf1f5", "sidebar": "#f8fafc", "panel": "#ffffff", "panel_soft": "#f7f9fb",
    "border": "#d7dfe7", "text": "#172033", "muted": "#7b8595", "accent": "#39b8c6",
    "accent_dark": "#218f9d", "accent_soft": "#e6f7f8", "danger": "#d95664",
    "danger_soft": "#fff4f5", "warning": "#d79224", "input": "#ffffff",
    "input_text": "#172033", "secondary": "#f5f7f9", "secondary_hover": "#eef2f5",
    "secondary_text": "#586577", "track": "#e0e6ec", "hero": "#ffffff",
    "hero_border": "#d7dfe7", "hover": "#f4f7f9", "nav_hover": "#eef2f5",
    "nav_text": "#697587", "scroll_handle": "#cdd5dd", "dialog": "#ffffff",
    "white": "#ffffff", "soft_hover_border": "#b6dfe3", "delete_hover": "#ffe8eb",
}

DARK_COLORS = {
    "bg": "#0d141c", "sidebar": "#111a24", "panel": "#18232e", "panel_soft": "#1b2733",
    "border": "#30404f", "text": "#edf3f8", "muted": "#9aa8b7", "accent": "#42c6d4",
    "accent_dark": "#67d6de", "accent_soft": "#183b43", "danger": "#ff7180",
    "danger_soft": "#38242a", "warning": "#e4aa4a", "input": "#17212c",
    "input_text": "#edf3f8", "secondary": "#1c2935", "secondary_hover": "#23313e",
    "secondary_text": "#d4dde5", "track": "#2b3947", "hero": "#18232e",
    "hero_border": "#30404f", "hover": "#1d2a37", "nav_hover": "#1d2b38",
    "nav_text": "#bdc8d3", "scroll_handle": "#405061", "dialog": "#0f171f",
    "white": "#ffffff", "soft_hover_border": "#3b7078", "delete_hover": "#47272d",
}


def log_exception(exc_type, exc_value, exc_traceback):
    """Persist uncaught exceptions so a packaged build is diagnosable instead of silently closing."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        log_file = LOG_DIR / "wardrobe.log"
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(f"\n[{stamp}] WARDROBE {APP_VERSION}\n")
            traceback.print_exception(exc_type, exc_value, exc_traceback, file=handle)
    except Exception:
        pass



def parse_version(value):
    """Return a comparable semantic version tuple from values such as v1.2.3."""
    match = re.search(r"(?:^|[^0-9])v?(\d+)\.(\d+)(?:\.(\d+))?(?:[-+].*)?$", str(value).strip())
    if not match:
        match = re.search(r"v?(\d+)\.(\d+)(?:\.(\d+))?", str(value).strip())
    if not match:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))


def format_release_notes(body, limit=1200):
    text = re.sub(r"\r\n?", "\n", body or "").strip()
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text or "No release notes were provided."

def make_stylesheet(c):
    return f"""
QMainWindow, QWidget {{ background: {c['bg']}; color: {c['text']}; font-family: 'Segoe UI'; font-size: 14px; }}
QLabel {{ background: transparent; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 7px; }}
QScrollBar::handle:vertical {{ background: {c['scroll_handle']}; border-radius: 3px; min-height: 28px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QFrame#Sidebar {{ background: {c['sidebar']}; border-right: 1px solid {c['border']}; }}
QLabel#Brand {{ font-size: 22px; font-weight: 800; color: {c['text']}; }}
QLabel#BrandMark {{ background: {c['accent']}; color: {c['white']}; border-radius: 13px; font-size: 16px; font-weight: 800; }}
QLabel#NavSection {{ color: {c['muted']}; font-size: 11px; font-weight: 700; }}
QPushButton#NavButton, QPushButton#NavButtonActive {{ border: none; border-radius: 10px; text-align: left; padding: 10px 13px; font-weight: 650; }}
QPushButton#NavButton {{ background: transparent; color: {c['nav_text']}; }}
QPushButton#NavButton:hover {{ background: {c['nav_hover']}; color: {c['text']}; }}
QPushButton#NavButtonActive {{ background: {c['accent_soft']}; color: {c['accent_dark']}; }}
QLabel#PageTitle {{ font-size: 28px; font-weight: 800; }}
QLabel#Subtitle, QLabel#SectionMuted, QLabel#SmallMuted, QLabel#Status {{ color: {c['muted']}; }}
QLabel#Status {{ font-size: 11px; }}
QLineEdit#GlobalSearch, QLineEdit#Search, QLineEdit#Input, QTextEdit#Input {{ background: {c['input']}; color: {c['input_text']}; border: 1px solid {c['border']}; border-radius: 12px; padding: 10px 13px; selection-background-color: {c['accent_soft']}; selection-color: {c['text']}; }}
QComboBox#Input {{ background: {c['input']}; color: {c['input_text']}; border: 1px solid {c['border']}; border-radius: 12px; padding: 0 42px 0 13px; min-height: 44px; selection-background-color: {c['accent_soft']}; selection-color: {c['text']}; }}
QLineEdit#GlobalSearch:focus, QLineEdit#Search:focus, QLineEdit#Input:focus, QComboBox#Input:focus, QTextEdit#Input:focus {{ border: 1px solid {c['accent']}; }}
QComboBox#Input:hover {{ border: 1px solid {c['soft_hover_border']}; background: {c['hover']}; }}
QComboBox#Input::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right; width: 38px; border: none; border-left: 1px solid {c['border']}; border-top-right-radius: 12px; border-bottom-right-radius: 12px; }}
QComboBox#Input QAbstractItemView {{ background: {c['input']}; color: {c['text']}; border: 1px solid {c['border']}; border-radius: 10px; padding: 6px; selection-background-color: {c['accent_soft']}; selection-color: {c['text']}; outline: none; }}
QComboBox#Input QAbstractItemView::item {{ min-height: 34px; padding: 7px 10px; border-radius: 8px; }}
QComboBox#Input QAbstractItemView::item:hover {{ background: {c['hover']}; }}
QFrame#StatCard, QFrame#CategoryCard, QFrame#ItemCard, QFrame#FormCard, QFrame#FeatureCard, QFrame#OutfitCard, QFrame#TodayHero {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 15px; }}
QFrame#CategoryCard:hover, QFrame#ItemCard:hover, QFrame#OutfitCard:hover, QFrame#FeatureCard:hover {{ background: {c['hover']}; border: 1px solid {c['soft_hover_border']}; }}
QLabel#StatNumber {{ font-size: 29px; font-weight: 800; }}
QLabel#StatLabel {{ color: {c['muted']}; font-size: 11px; font-weight: 700; }}
QLabel#StatNumberCompact {{ font-size: 22px; font-weight: 800; }}
QLabel#StatLabelCompact {{ color: {c['muted']}; font-size: 11px; font-weight: 700; }}
QLabel#SectionTitle {{ font-size: 16px; font-weight: 750; }}
QLabel#CategoryName {{ font-size: 15px; font-weight: 700; }}
QLabel#CategoryCount, QLabel#CategoryDetails, QLabel#CategoryMetric, QLabel#ItemDescription {{ color: {c['muted']}; }}
QLabel#CategoryMetric, QLabel#CategoryCount, QLabel#CategoryDetails {{ font-size: 12px; font-weight: 600; }}
QLabel#Arrow {{ color: {c['accent']}; font-size: 25px; }}
QLabel#FeatureTitle, QLabel#ItemName, QLabel#TodayTitle {{ font-size: 17px; font-weight: 750; }}
QLabel#TodayTitle {{ font-size: 21px; font-weight: 800; }}
QLabel#ItemNumber {{ color: {c['muted']}; font-size: 11px; font-weight: 700; }}
QLabel#ItemColor {{ color: {c['accent_dark']}; font-weight: 600; }}
QLabel#FormLabel {{ font-weight: 650; }}
QLabel#Chip {{ background: transparent; color: {c['accent_dark']}; border: 1px solid {c['border']}; border-radius: 9px; padding: 4px 8px; font-weight: 650; }}
QLabel#EmptyStateTitle {{ font-size: 18px; font-weight: 750; }}
QFrame#TodayHero {{ background: {c['hero']}; border: 1px solid {c['hero_border']}; border-radius: 18px; }}
QFrame#TodayRoleCard {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 14px; min-height: 112px; }}
QLabel#TodayRoleTitle {{ color: {c['muted']}; font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.6px; }}
QLabel#TodayRoleItem {{ font-size: 15px; font-weight: 750; color: {c['text']}; }}
QLabel#TodayRoleMeta {{ color: {c['muted']}; font-size: 11px; }}
QPushButton#Primary {{ background: {c['accent']}; color: {c['white']}; border: none; border-radius: 12px; padding: 0 17px; min-height: 42px; font-weight: 700; }}
QPushButton#Primary:hover {{ background: {c['accent_dark']}; }}
QPushButton#Primary:pressed {{ padding-top: 1px; }}
QPushButton#Secondary {{ background: {c['secondary']}; color: {c['secondary_text']}; border: 1px solid {c['border']}; border-radius: 11px; padding: 0 14px; min-height: 40px; font-weight: 650; }}
QPushButton#Secondary:hover {{ background: {c['secondary_hover']}; border-color: {c['soft_hover_border']}; }}
QPushButton#Secondary:pressed {{ background: {c['border']}; }}
QPushButton#Delete {{ background: {c['danger_soft']}; color: {c['danger']}; border: 1px solid transparent; border-radius: 11px; padding: 0 14px; min-height: 40px; font-weight: 650; }}
QPushButton#Delete:hover {{ background: {c['delete_hover']}; }}
QPushButton#Back, QPushButton#FilterClear {{ background: transparent; color: {c['accent_dark']}; border: none; padding: 7px 2px; font-weight: 650; }}
QPushButton#IconButton {{ background: {c['secondary']}; color: {c['secondary_text']}; border: 1px solid {c['border']}; border-radius: 10px; min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px; font-size: 16px; }}
QPushButton#IconButton:hover {{ background: {c['accent_soft']}; color: {c['accent_dark']}; border-color: {c['soft_hover_border']}; }}
QPushButton#Back, QPushButton#FilterClear {{ background: transparent; color: {c['accent_dark']}; border: none; padding: 6px 0; font-weight: 650; }}
QFrame#UsageTrack {{ background: {c['track']}; border: none; border-radius: 6px; min-height: 12px; max-height: 12px; }}
QFrame#UsageFill {{ background: {c['accent']}; border: none; border-radius: 6px; min-height: 12px; max-height: 12px; }}
QDialog {{ background: {c['dialog']}; color: {c['text']}; }}
QCheckBox {{ spacing: 8px; }}
QMessageBox, QInputDialog {{ background: {c['panel']}; color: {c['text']}; }}
QMessageBox QLabel, QInputDialog QLabel {{ color: {c['text']}; }}
"""


CATEGORY_DISPLAY_NAMES = {
    "ust giyim": "Tops", "üst giyim": "Tops",
    "hırka / katmanlama": "Cardigans & Layers", "hirka / katmanlama": "Cardigans & Layers",
    "pantolonlar": "Pants", "dış giyim": "Outerwear", "dis giyim": "Outerwear",
    "ayakkabılar": "Shoes", "ayakkabilar": "Shoes", "kemerler": "Belts",
    "güneş gözlükleri": "Sunglasses", "gunes gozlukleri": "Sunglasses",
}

ROLE_LABELS = {
    "upper": "Tops", "pants": "Pants", "shoes": "Shoes", "belt": "Belts",
    "glasses": "Sunglasses", "watch": "Wristwatch", "outer": "Outerwear", "layer": "Cardigans & Layers", "accessories": "Accessories", "other": "Other",
}

COLOR_MAP = {
    "siyah": "black", "beyaz": "white", "gri": "grey", "açık mavi": "light blue", "acik mavi": "light blue",
    "mavi": "blue", "bej": "beige", "kahverengi": "brown", "koyu kahve": "dark brown",
    "bordo": "burgundy", "haki": "khaki", "antasit melanj": "charcoal melange", "krem": "cream",
}


def display_category_name(name):
    return CATEGORY_DISPLAY_NAMES.get(name.strip().casefold(), name)


def category_role(name):
    n = name.strip().casefold()
    if "pantolon" in n or "pants" in n or "trouser" in n:
        return "pants"
    if "ayakkab" in n or "shoes" in n or "shoe" in n or "derby" in n or "bot" in n:
        return "shoes"
    if "kemer" in n or "belt" in n:
        return "belt"
    if "güneş" in n or "gunes" in n or "sunglass" in n:
        return "glasses"
    if any(term in n for term in ("saat", "watch", "wristwatch", "wrist watch", "kol saati", "kolsaati")):
        return "watch"
    if "dış giyim" in n or "dis giyim" in n or "outerwear" in n or "outerwear" in n:
        return "outer"
    if "hırka" in n or "hirka" in n or "katman" in n or "layer" in n or "cardigan" in n:
        return "layer"
    if "üst giyim" in n or "ust giyim" in n or "tops" in n or "top" in n or "shirt" in n or "polo" in n or "triko" in n or "quarter zip" in n:
        return "upper"
    accessory_terms = (
        "aksesuar", "accessor", "saat", "watch", "jewelry", "jewellery",
        "takı", "taki", "kolye", "necklace", "bileklik", "bracelet",
        "yüzük", "yuzuk", "ring", "kravat", "tie", "çanta", "canta",
        "bag", "wallet", "cüzdan", "cuzdan", "scarf", "şal", "shal",
    )
    if any(term in n for term in accessory_terms):
        return "accessories"
    # User-created categories are treated as accessories so custom items
    # such as watches/jewelry are immediately available in Outfit Builder.
    return "accessories"


def normalize_color(text):
    value = text.strip().casefold()
    for key, canonical in COLOR_MAP.items():
        if key in value:
            return canonical
    return value


def role_label(role):
    return ROLE_LABELS.get(role, role)


SEASON_OPTIONS = ["Spring", "Summer", "Autumn", "Winter"]
OCCASION_OPTIONS = ["Work", "Casual", "Dinner", "Formal", "Weekend", "Travel"]
STYLE_OPTIONS = ["Classic", "Smart Casual", "Minimal", "Relaxed", "Preppy", "Elegant"]
FIT_OPTIONS = ["Slim", "Regular", "Relaxed", "Oversized", "Unknown"]
AVAILABILITY_OPTIONS = ["Available", "In Laundry", "Unavailable"]


def _split_tags(value):
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def infer_metadata(category_name, name, color, description):
    text = f"{category_name} {name} {description}".casefold()
    role = category_role(category_name)

    # Sensible defaults for existing TXT records and newly added pieces.
    if role in {"outer", "layer"} or any(k in text for k in ("kaban", "coat", "mont", "jacket", "quarter zip")):
        seasons = ["Autumn", "Winter", "Spring"]
    elif role == "glasses":
        seasons = ["Spring", "Summer", "Autumn"]
    elif "triko" in text or "knit" in text or "wool" in text:
        seasons = ["Autumn", "Winter", "Spring"]
    else:
        seasons = ["Spring", "Summer", "Autumn"]

    if any(k in text for k in ("klasik", "classic", "blazer", "derby", "kaban", "formal")):
        occasions = ["Work", "Dinner", "Formal"]
        style = ["Classic", "Elegant"]
        formality = 5
    elif any(k in text for k in ("polo", "chino", "quarter zip", "hirka", "hırka")):
        occasions = ["Work", "Casual", "Weekend"]
        style = ["Smart Casual", "Relaxed"]
        formality = 3
    else:
        occasions = ["Casual", "Weekend", "Work"]
        style = ["Smart Casual", "Minimal"]
        formality = 3

    if "slim fit" in text or "dar kesim" in text:
        fit = "Slim"
    elif "relax fit" in text or "rahat kesim" in text or "oversize" in text:
        fit = "Relaxed"
    elif "regular fit" in text:
        fit = "Regular"
    else:
        fit = "Unknown"

    return {
        "season": ", ".join(seasons),
        "occasion": ", ".join(occasions),
        "style": ", ".join(style),
        "formality": formality,
        "fit": fit,
        "availability": "Available",
    }


def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        child_layout = item.layout()
        child_widget = item.widget()
        if child_layout is not None:
            clear_layout(child_layout)
        elif child_widget is not None:
            child_widget.setParent(None)
            child_widget.deleteLater()


def styled_label(text, object_name):
    label = QLabel(text)
    label.setObjectName(object_name)
    return label


def make_button(text, role="Secondary"):
    button = QPushButton(text)
    button.setObjectName(role)
    button.setCursor(Qt.PointingHandCursor)
    return button


class ClothingItem:
    def __init__(self, number, name, color="", description="", uid=None, metadata=None):
        self.number = int(number)
        self.name = name.strip()
        self.color = color.strip()
        self.description = description.strip()
        self.uid = uid or uuid.uuid4().hex
        defaults = infer_metadata("", self.name, self.color, self.description)
        metadata = metadata or {}
        self.season = metadata.get("season") or defaults["season"]
        self.occasion = metadata.get("occasion") or defaults["occasion"]
        self.style = metadata.get("style") or defaults["style"]
        try:
            self.formality = max(1, min(5, int(metadata.get("formality", defaults["formality"]))))
        except (TypeError, ValueError):
            self.formality = defaults["formality"]
        self.fit = metadata.get("fit") or defaults["fit"]
        self.availability = metadata.get("availability") or "Available"

    def search_text(self):
        return (
            f"{self.name} {self.color} {self.description} {self.season} "
            f"{self.occasion} {self.style} {self.fit} {self.availability}"
        ).casefold()

    def metadata(self):
        return {
            "season": self.season,
            "occasion": self.occasion,
            "style": self.style,
            "formality": self.formality,
            "fit": self.fit,
            "availability": self.availability,
        }

    def to_text(self, number=None):
        number = self.number if number is None else number
        parts = [self.name]
        if self.color:
            parts.append(self.color)
        if self.description:
            parts.append(self.description)
        return f"{number}. " + " - ".join(parts)

    def label(self):
        return " · ".join(p for p in (self.name, self.color) if p)


class Category:
    def __init__(self, name):
        self.name = name.strip()
        self.items = []

    @property
    def count(self):
        return len(self.items)


class WardrobeRepository:
    """SQLite-backed repository with one-time migration from the legacy TXT file."""

    CATEGORY_PATTERN = re.compile(r"^(.*?)\s*\(\s*\d+\s*PARCA\s*\)\s*$", re.I)
    ITEM_PATTERN = re.compile(r"^\s*(\d+)\.\s*(.+?)\s*$")

    def __init__(self, db_path, legacy_path=None):
        self.db_path = Path(db_path)
        self.legacy_path = Path(legacy_path) if legacy_path else None
        self.file_path = self.db_path  # compatibility alias
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self._configure_connection()
        self._create_schema()
        self._apply_schema_migrations()

        self.categories = []
        self.usage_counts = {}
        self.usage_history = []
        self.favorites = set()
        self.saved_outfits = []
        self.stats_month = datetime.now().strftime("%Y-%m")
        self.last_stats_reset = ""
        self._stats_month_present = False
        self._category_map = {}
        self._item_map = {}
        self._uid_map = {}
        self._last_snapshot = None
        self._last_action = None
        self.on_internal_save = None

        migration_version = self._get_setting("migration_version", "")
        if not migration_version:
            if self._is_database_empty() and self.legacy_path and self.legacy_path.exists():
                self._migrate_from_txt(self.legacy_path)
            else:
                with self.conn:
                    self._set_setting("migration_version", "1")
        self.load()
        self._ensure_automatic_backup()

    def _configure_connection(self):
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")

    def _get_schema_version(self):
        row = self.conn.execute("PRAGMA user_version").fetchone()
        return int(row[0]) if row else 0

    def _set_schema_version(self, version):
        version = int(version)
        if version < 0:
            raise ValueError("Database schema version cannot be negative.")
        self.conn.execute(f"PRAGMA user_version = {version}")

    def _backup_before_schema_migration(self, from_version, to_version):
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = BACKUP_DIR / f"pre_migration_v{from_version}_to_v{to_version}_{stamp}.db"
        dest = sqlite3.connect(target)
        try:
            self.conn.commit()
            self.conn.backup(dest)
        finally:
            dest.close()
        return target

    def _apply_schema_migrations(self):
        current = self._get_schema_version()
        if current > DB_SCHEMA_VERSION:
            raise RuntimeError(
                f"This WARDROBE version supports database schema v{DB_SCHEMA_VERSION}, "
                f"but the database is v{current}. Please update the application first."
            )
        if current == DB_SCHEMA_VERSION:
            self._set_setting("db_schema_version", DB_SCHEMA_VERSION)
            self.conn.commit()
            return

        if not self._is_database_empty():
            self._backup_before_schema_migration(current, DB_SCHEMA_VERSION)

        try:
            with self.conn:
                # Version 1 is the stable SQLite schema introduced before
                # versioned migrations. Existing v0 databases already contain
                # these tables, so the first migration records the baseline.
                if current < 1 <= DB_SCHEMA_VERSION:
                    self._set_schema_version(1)
                    current = 1
                self._set_setting("db_schema_version", current)
        except Exception:
            # Leave the original version intact if migration fails.
            raise

    def _create_schema(self):
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS clothing (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid TEXT NOT NULL UNIQUE,
                category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
                number INTEGER NOT NULL UNIQUE,
                name TEXT NOT NULL,
                color TEXT DEFAULT '',
                description TEXT DEFAULT '',
                season TEXT DEFAULT '',
                occasion TEXT DEFAULT '',
                style TEXT DEFAULT '',
                formality INTEGER DEFAULT 3,
                fit TEXT DEFAULT 'Unknown',
                availability TEXT DEFAULT 'Available',
                favorite INTEGER DEFAULT 0,
                wear_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS wear_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid TEXT NOT NULL REFERENCES clothing(uid) ON DELETE CASCADE,
                timestamp TEXT NOT NULL,
                source TEXT DEFAULT 'Manual use'
            );
            CREATE TABLE IF NOT EXISTS outfits (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                favorite INTEGER DEFAULT 0,
                use_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS outfit_items (
                outfit_id INTEGER NOT NULL REFERENCES outfits(id) ON DELETE CASCADE,
                uid TEXT NOT NULL REFERENCES clothing(uid) ON DELETE CASCADE,
                PRIMARY KEY (outfit_id, uid)
            );
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_clothing_category ON clothing(category_id);
            CREATE INDEX IF NOT EXISTS idx_clothing_uid ON clothing(uid);
            CREATE INDEX IF NOT EXISTS idx_history_uid ON wear_history(uid);
            CREATE INDEX IF NOT EXISTS idx_history_timestamp ON wear_history(timestamp);
            """
        )
        self.conn.commit()

    def _is_database_empty(self):
        row = self.conn.execute("SELECT COUNT(*) AS n FROM clothing").fetchone()
        return int(row["n"] or 0) == 0

    def _set_setting(self, key, value):
        self.conn.execute(
            "INSERT INTO app_settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )

    def _get_setting(self, key, default=""):
        row = self.conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def _legacy_parse(self, file_path):
        content = file_path.read_text(encoding="utf-8-sig") if file_path.exists() else ""
        categories = []
        current = None
        metadata_mode = False
        mode = None
        id_rows, fav_rows, usage_rows, history_rows, outfit_rows, metadata_rows = [], [], [], [], [], []
        stats_month = None
        last_stats_reset = ""

        for raw in content.splitlines():
            line = raw.strip()
            if not line:
                continue
            upper = line.upper()
            if upper == "UYGULAMA VERILERI":
                metadata_mode = True
                mode = None
                continue
            if metadata_mode:
                if upper == "ITEM_IDS": mode = "ids"; continue
                if upper in {"ITEM_METADATA", "CLOTHING_METADATA"}: mode = "metadata"; continue
                if upper == "FAVORILER": mode = "fav"; continue
                if upper == "KULLANIM": mode = "usage"; continue
                if upper in {"KULLANIM_GECMISI", "KULLANIM GECMISI"}: mode = "history"; continue
                if upper in {"KAYITLI KOMBİNLER", "KAYITLI KOMBINLER"}: mode = "outfits"; continue
                if upper == "ISTATISTIK": mode = "stats"; continue
                if upper.startswith("OUTFIT|"):
                    parts = line.split("|", 6)
                    if len(parts) == 7:
                        outfit_rows.append(parts)
                    continue
                parts = line.split("|")
                if mode == "ids" and len(parts) >= 2:
                    id_rows.append((parts[0], parts[1]))
                elif mode == "metadata" and len(parts) >= 7:
                    metadata_rows.append(parts[:7])
                elif mode == "fav" and len(parts) >= 2:
                    fav_rows.append((parts[0], parts[1]))
                elif mode == "usage" and len(parts) >= 2:
                    usage_rows.append((parts[0], parts[1]))
                elif mode == "history" and len(parts) >= 3:
                    history_rows.append((parts[0], parts[1], "|".join(parts[2:])))
                elif mode == "outfits" and len(parts) >= 3 and parts[0].isdigit():
                    outfit_rows.append(["OUTFIT", parts[0], parts[1], "0", "0", "", parts[2]])
                elif mode == "stats":
                    key = parts[0].strip().upper() if parts else ""
                    value = parts[1].strip() if len(parts) > 1 else ""
                    if key == "MONTH": stats_month = value
                    elif key == "LAST_RESET": last_stats_reset = value
                continue
            if upper == "GENEL OZET" or line.startswith("="):
                continue
            match = self.CATEGORY_PATTERN.match(line)
            if match:
                current = Category(match.group(1).strip())
                categories.append(current)
                continue
            if current is None:
                continue
            match = self.ITEM_PATTERN.match(line)
            if not match:
                continue
            number = int(match.group(1))
            parts = [p.strip() for p in match.group(2).split(" - ")]
            name = parts[0] if parts else ""
            color = parts[1] if len(parts) > 1 else ""
            description = " - ".join(parts[2:]) if len(parts) > 2 else ""
            if name:
                current.items.append(ClothingItem(number, name, color, description))

        row_map = {}
        for number, uid in id_rows:
            try:
                row_map[int(number)] = uid.strip()
            except ValueError:
                pass
        for c in categories:
            for item in c.items:
                if item.number in row_map and row_map[item.number]:
                    item.uid = row_map[item.number]

        metadata_map = {}
        for row in metadata_rows:
            uid, season, occasion, style, formality, fit, availability = row
            metadata_map[uid] = {
                "season": season,
                "occasion": occasion,
                "style": style,
                "formality": formality,
                "fit": fit,
                "availability": availability,
            }
        for category in categories:
            for item in category.items:
                if item.uid in metadata_map:
                    meta = metadata_map[item.uid]
                    item.season = meta.get("season") or item.season
                    item.occasion = meta.get("occasion") or item.occasion
                    item.style = meta.get("style") or item.style
                    try:
                        item.formality = max(1, min(5, int(meta.get("formality", item.formality))))
                    except (TypeError, ValueError):
                        pass
                    item.fit = meta.get("fit") or item.fit
                    item.availability = meta.get("availability") or item.availability
                else:
                    defaults = infer_metadata(category.name, item.name, item.color, item.description)
                    for key, value in defaults.items():
                        setattr(item, key, value)

        valid = {item.uid for c in categories for item in c.items}
        favorites = {uid for uid, value in fav_rows if uid in valid and value.strip() == "1"}
        usage = {}
        for uid, count in usage_rows:
            try:
                count = max(0, int(count))
            except ValueError:
                continue
            if uid in valid:
                usage[uid] = count
        history = [{"timestamp": ts, "uid": uid, "source": source} for ts, uid, source in history_rows if uid in valid][-500:]
        outfits = []
        for row in outfit_rows:
            try:
                oid = int(row[1]); name = row[2].strip() or f"Outfit {oid}"
                favorite = row[3].strip() == "1"; uses = max(0, int(row[4])); created = row[5].strip()
                uids = [u for u in row[6].split(",") if u in valid]
                if uids:
                    outfits.append({"id": oid, "name": name, "favorite": favorite, "use_count": uses, "created": created, "uids": uids})
            except (ValueError, IndexError):
                continue
        return categories, favorites, usage, history, outfits, (stats_month or datetime.now().strftime("%Y-%m")), last_stats_reset

    def _migrate_from_txt(self, file_path):
        categories, favorites, usage, history, outfits, stats_month, last_stats_reset = self._legacy_parse(file_path)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(file_path, LEGACY_ARCHIVE_DIR / f"legacy_txt_{stamp}.txt")
        except OSError:
            pass

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        with self.conn:
            cat_ids = {}
            for category in categories:
                cur = self.conn.execute("INSERT INTO categories(name) VALUES(?)", (category.name,))
                cat_ids[category.name.casefold()] = cur.lastrowid
                for item in category.items:
                    self.conn.execute(
                        """INSERT INTO clothing
                           (uid, category_id, number, name, color, description, season, occasion, style, formality, fit, availability, favorite, wear_count, created_at, updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            item.uid, cur.lastrowid, item.number, item.name, item.color, item.description,
                            item.season, item.occasion, item.style, item.formality, item.fit, item.availability,
                            1 if item.uid in favorites else 0, int(usage.get(item.uid, 0)), now, now,
                        ),
                    )
            for row in history:
                self.conn.execute("INSERT INTO wear_history(uid,timestamp,source) VALUES(?,?,?)", (row["uid"], row["timestamp"], row["source"]))
            for outfit in outfits:
                self.conn.execute(
                    "INSERT INTO outfits(id,name,favorite,use_count,created_at) VALUES(?,?,?,?,?)",
                    (outfit["id"], outfit["name"], 1 if outfit.get("favorite") else 0, int(outfit.get("use_count", 0)), outfit.get("created", "")),
                )
                for uid in outfit["uids"]:
                    self.conn.execute("INSERT OR IGNORE INTO outfit_items(outfit_id,uid) VALUES(?,?)", (outfit["id"], uid))
            self._set_setting("stats_month", stats_month)
            self._set_setting("last_stats_reset", last_stats_reset)
            self._set_setting("migration_version", "1")
            self._set_setting("legacy_source", str(file_path.name))
        self.conn.commit()

    def close(self):
        try:
            self.conn.commit()
            self.conn.close()
        except Exception:
            pass

    def load(self):
        self.categories = []
        category_rows = self.conn.execute("SELECT id,name FROM categories ORDER BY id").fetchall()
        category_map = {}
        for row in category_rows:
            category = Category(row["name"])
            self.categories.append(category)
            category_map[row["id"]] = category

        item_rows = self.conn.execute(
            "SELECT * FROM clothing ORDER BY number"
        ).fetchall()
        self.usage_counts = {}
        self.favorites = set()
        for row in item_rows:
            item = ClothingItem(
                row["number"], row["name"], row["color"], row["description"], uid=row["uid"],
                metadata={
                    "season": row["season"], "occasion": row["occasion"], "style": row["style"],
                    "formality": row["formality"], "fit": row["fit"], "availability": row["availability"],
                },
            )
            item.category_id = row["category_id"]
            category_map[row["category_id"]].items.append(item)
            self.usage_counts[item.uid] = max(0, int(row["wear_count"] or 0))
            if row["favorite"]:
                self.favorites.add(item.uid)

        self.usage_history = [
            {"timestamp": row["timestamp"], "uid": row["uid"], "source": row["source"]}
            for row in self.conn.execute("SELECT timestamp,uid,source FROM wear_history ORDER BY id DESC LIMIT 500").fetchall()
        ]
        self.usage_history.reverse()

        outfits = []
        outfit_rows = self.conn.execute("SELECT * FROM outfits ORDER BY id").fetchall()
        item_rows_by_outfit = {}
        for row in self.conn.execute("SELECT outfit_id,uid FROM outfit_items ORDER BY outfit_id").fetchall():
            item_rows_by_outfit.setdefault(row["outfit_id"], []).append(row["uid"])
        valid = set(self._all_uids_from_categories())
        for row in outfit_rows:
            uids = [uid for uid in item_rows_by_outfit.get(row["id"], []) if uid in valid]
            if uids:
                outfits.append({
                    "id": row["id"], "name": row["name"], "favorite": bool(row["favorite"]),
                    "use_count": int(row["use_count"] or 0), "created": row["created_at"] or "", "uids": uids,
                })
        self.saved_outfits = outfits
        self.stats_month = self._get_setting("stats_month", datetime.now().strftime("%Y-%m"))
        self.last_stats_reset = self._get_setting("last_stats_reset", "")
        self._stats_month_present = bool(self._get_setting("stats_month", ""))
        self._reindex()

    def _all_uids_from_categories(self):
        return [item.uid for c in self.categories for item in c.items]

    def _reindex(self):
        self._category_map = {c.name.casefold(): c for c in self.categories}
        self._item_map = {item.number: (item, c) for c in self.categories for item in c.items}
        self._uid_map = {item.uid: (item, c) for c in self.categories for item in c.items}

    @property
    def total(self): return sum(c.count for c in self.categories)
    @property
    def total_uses(self): return sum(self.usage_counts.values())
    def all_items(self): return [item for c in self.categories for item in c.items]
    def get_category(self, name):
        key = " ".join(str(name or "").strip().casefold().split())
        if not key:
            return None
        direct = self._category_map.get(key)
        if direct is not None:
            return direct
        for category in self.categories:
            display_key = " ".join(display_category_name(category.name).strip().casefold().split())
            if display_key == key:
                return category
        return None

    def resolve_category_name(self, name):
        clean = " ".join(str(name or "").strip().split())
        if not clean:
            return ""
        category = self.get_category(clean)
        return category.name if category is not None else clean
    def find_item(self, number): return self._item_map.get(number, (None, None))
    def find_item_by_uid(self, uid): return self._uid_map.get(uid, (None, None))
    def next_number(self): return max(self._item_map.keys(), default=0) + 1
    def next_outfit_id(self): return max((o["id"] for o in self.saved_outfits), default=0) + 1

    def _capture_snapshot(self, action):
        state = {
            "categories": [(c.name, [(item.number, item.uid, item.name, item.color, item.description, item.metadata()) for item in c.items]) for c in self.categories],
            "favorites": set(self.favorites),
            "usage_counts": dict(self.usage_counts),
            "usage_history": [dict(x) for x in self.usage_history],
            "saved_outfits": [dict(o, uids=list(o["uids"])) for o in self.saved_outfits],
            "stats_month": self.stats_month,
            "last_stats_reset": self.last_stats_reset,
        }
        self._last_snapshot = state
        self._last_action = action

    def _restore_state(self, state):
        with self.conn:
            self.conn.execute("DELETE FROM outfit_items")
            self.conn.execute("DELETE FROM outfits")
            self.conn.execute("DELETE FROM wear_history")
            self.conn.execute("DELETE FROM clothing")
            self.conn.execute("DELETE FROM categories")
            for name, items in state["categories"]:
                cat_id = self.conn.execute("INSERT INTO categories(name) VALUES(?)", (name,)).lastrowid
                for number, uid, n, color, desc, meta in items:
                    self.conn.execute(
                        """INSERT INTO clothing(uid,category_id,number,name,color,description,season,occasion,style,formality,fit,availability,favorite,wear_count,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (uid, cat_id, number, n, color, desc, meta["season"], meta["occasion"], meta["style"], meta["formality"], meta["fit"], meta["availability"], 1 if uid in state["favorites"] else 0, int(state["usage_counts"].get(uid, 0)), "", ""),
                    )
            for row in state["usage_history"]:
                self.conn.execute("INSERT INTO wear_history(uid,timestamp,source) VALUES(?,?,?)", (row["uid"], row["timestamp"], row["source"]))
            for outfit in state["saved_outfits"]:
                self.conn.execute("INSERT INTO outfits(id,name,favorite,use_count,created_at) VALUES(?,?,?,?,?)", (outfit["id"], outfit["name"], 1 if outfit.get("favorite") else 0, int(outfit.get("use_count", 0)), outfit.get("created", "")))
                for uid in outfit["uids"]:
                    self.conn.execute("INSERT OR IGNORE INTO outfit_items(outfit_id,uid) VALUES(?,?)", (outfit["id"], uid))
            self._set_setting("stats_month", state["stats_month"])
            self._set_setting("last_stats_reset", state["last_stats_reset"])
        self.load()

    def save(self, record_undo=False, action="Change"):
        self._reindex()
        with self.conn:
            for category in self.categories:
                cat_id = self.conn.execute("SELECT id FROM categories WHERE name=?", (category.name,)).fetchone()
                if not cat_id:
                    cat_db_id = self.conn.execute("INSERT INTO categories(name) VALUES(?)", (category.name,)).lastrowid
                else:
                    cat_db_id = cat_id["id"]
                for item in category.items:
                    now = datetime.now().strftime("%Y-%m-%d %H:%M")
                    existing = self.conn.execute("SELECT uid FROM clothing WHERE uid=?", (item.uid,)).fetchone()
                    vals = (cat_db_id, item.number, item.name, item.color, item.description, item.season, item.occasion, item.style, item.formality, item.fit, item.availability, 1 if item.uid in self.favorites else 0, int(self.usage_counts.get(item.uid, 0)), now)
                    if existing:
                        self.conn.execute("""UPDATE clothing SET category_id=?,number=?,name=?,color=?,description=?,season=?,occasion=?,style=?,formality=?,fit=?,availability=?,favorite=?,wear_count=?,updated_at=? WHERE uid=?""", vals + (item.uid,))
                    else:
                        self.conn.execute("""INSERT INTO clothing(category_id,number,name,color,description,season,occasion,style,formality,fit,availability,favorite,wear_count,updated_at,uid) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", vals + (item.uid,))
            existing_category_keys = {" ".join(c.name.strip().casefold().split()) for c in self.categories}
            for row in self.conn.execute("SELECT id,name FROM categories").fetchall():
                key = " ".join(str(row["name"]).strip().casefold().split())
                if key not in existing_category_keys:
                    self.conn.execute("DELETE FROM categories WHERE id=?", (row["id"],))

            existing_uids = set(self._all_uids_from_categories())
            db_uids = [r["uid"] for r in self.conn.execute("SELECT uid FROM clothing").fetchall()]
            for uid in set(db_uids) - existing_uids:
                self.conn.execute("DELETE FROM clothing WHERE uid=?", (uid,))
            existing_outfit_ids = {o["id"] for o in self.saved_outfits}
            for oid in [r["id"] for r in self.conn.execute("SELECT id FROM outfits").fetchall() if r["id"] not in existing_outfit_ids]:
                self.conn.execute("DELETE FROM outfits WHERE id=?", (oid,))
            for outfit in self.saved_outfits:
                self.conn.execute("INSERT INTO outfits(id,name,favorite,use_count,created_at) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,favorite=excluded.favorite,use_count=excluded.use_count,created_at=excluded.created_at", (outfit["id"], outfit["name"], 1 if outfit.get("favorite") else 0, int(outfit.get("use_count", 0)), outfit.get("created", "")))
                self.conn.execute("DELETE FROM outfit_items WHERE outfit_id=?", (outfit["id"],))
                for uid in outfit["uids"]:
                    self.conn.execute("INSERT OR IGNORE INTO outfit_items(outfit_id,uid) VALUES(?,?)", (outfit["id"], uid))
            # Keep clothing usage and wear history in the same SQLite transaction.
            self.conn.execute("DELETE FROM wear_history")
            for row in self.usage_history[-500:]:
                self.conn.execute("INSERT INTO wear_history(uid,timestamp,source) VALUES(?,?,?)", (row["uid"], row["timestamp"], row["source"]))
            self._set_setting("stats_month", self.stats_month)
            self._set_setting("last_stats_reset", self.last_stats_reset)
        self._reindex()
        if self.on_internal_save:
            self.on_internal_save()

    def _verify_backup(self, backup_path):
        backup_path = Path(backup_path)
        if not backup_path.exists() or backup_path.stat().st_size == 0:
            raise ValueError("Backup file is missing or empty.")
        conn = sqlite3.connect(backup_path)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            if not row or str(row[0]).lower() != "ok":
                raise ValueError(f"Backup integrity check failed: {row[0] if row else 'unknown error'}")
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version > DB_SCHEMA_VERSION:
                raise ValueError(
                    f"Backup uses database schema v{version}, but this app supports v{DB_SCHEMA_VERSION}."
                )
        finally:
            conn.close()
        return True

    def _cleanup_backups(self):
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        now = time.time()
        managed = sorted(
            BACKUP_DIR.glob("wardrobe_*.db"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        keep = set(managed[:BACKUP_RETENTION_COUNT])
        for path in managed[BACKUP_RETENTION_COUNT:]:
            try:
                path.unlink()
            except OSError:
                pass
        # Very old managed backups are removed even if the directory has been
        # inactive for a long time. Safety backups created outside this pattern
        # (migration/restore backups) are intentionally retained.
        for path in managed:
            if path not in keep:
                continue
            try:
                if now - path.stat().st_mtime > BACKUP_MAX_AGE_DAYS * 86400:
                    path.unlink()
            except OSError:
                pass

    def _create_database_backup(self, kind="manual"):
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if kind == "auto":
            target = BACKUP_DIR / f"wardrobe_auto_{stamp}.db"
        else:
            target = BACKUP_DIR / f"wardrobe_{stamp}.db"
        temp_target = BACKUP_DIR / f".{target.stem}.tmp.db"
        if temp_target.exists():
            temp_target.unlink()
        dest = sqlite3.connect(temp_target)
        try:
            self.conn.commit()
            self.conn.backup(dest)
            dest.commit()
        finally:
            dest.close()
        self._verify_backup(temp_target)
        os.replace(temp_target, target)
        self._cleanup_backups()
        return target

    def backup_now(self, kind="manual"):
        """Create and validate a complete SQLite backup."""
        return self._create_database_backup(kind)

    def _ensure_automatic_backup(self):
        """Create at most one automatic full backup during the configured interval."""
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backups = sorted(
            BACKUP_DIR.glob("wardrobe_auto_*.db"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if backups and time.time() - backups[0].stat().st_mtime < AUTO_BACKUP_INTERVAL_HOURS * 3600:
            return backups[0]
        try:
            return self._create_database_backup("auto")
        except Exception as exc:
            log_exception(type(exc), exc, exc.__traceback__)
            return None

    def latest_backup(self):
        if not BACKUP_DIR.exists():
            return None
        files = sorted(
            BACKUP_DIR.glob("wardrobe_*.db"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for candidate in files:
            try:
                self._verify_backup(candidate)
                return candidate
            except Exception:
                continue
        return None

    def backup_count(self):
        if not BACKUP_DIR.exists():
            return 0
        return len([p for p in BACKUP_DIR.glob("wardrobe_*.db") if p.is_file()])

    def available_backups(self):
        if not BACKUP_DIR.exists():
            return []
        valid = []
        for candidate in sorted(BACKUP_DIR.glob("wardrobe_*.db"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                self._verify_backup(candidate)
                valid.append(candidate)
            except Exception:
                continue
        return valid

    def restore_backup(self, backup=None):
        backup = Path(backup) if backup else self.latest_backup()
        if not backup or not backup.exists():
            raise ValueError("No database backup was found.")
        if backup.resolve() == self.db_path.resolve():
            raise ValueError("The selected backup is already the active database.")
        self._verify_backup(backup)
        self._capture_snapshot("Restore backup")
        # Preserve the current database before replacing it. This is deliberately
        # not named wardrobe_*.db so it never becomes the next 'latest backup'.
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        pre_restore = BACKUP_DIR / f"pre_restore_{stamp}.db"
        pre_restore_tmp = BACKUP_DIR / f".pre_restore_{stamp}.tmp.db"
        self.conn.commit()
        dest = sqlite3.connect(pre_restore_tmp)
        try:
            self.conn.backup(dest)
            dest.commit()
        finally:
            dest.close()
        self._verify_backup(pre_restore_tmp)
        os.replace(pre_restore_tmp, pre_restore)

        self.close()
        restore_tmp = self.db_path.with_name(f".{self.db_path.stem}.restore.tmp.db")
        if restore_tmp.exists():
            restore_tmp.unlink()
        source = sqlite3.connect(backup)
        target = sqlite3.connect(restore_tmp)
        try:
            source.backup(target)
            target.commit()
        finally:
            target.close()
            source.close()
        self._verify_backup(restore_tmp)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(self.db_path) + suffix)
            if sidecar.exists():
                try:
                    sidecar.unlink()
                except OSError:
                    pass
        os.replace(restore_tmp, self.db_path)
        self.conn = sqlite3.connect(self.db_path)
        self._configure_connection()
        self._create_schema()
        self._apply_schema_migrations()
        self.load()
        if self.on_internal_save:
            self.on_internal_save()
        return backup

    def undo(self):
        if self._last_snapshot is None: raise ValueError("There is nothing to undo.")
        action = self._last_action or "Last change"
        snapshot = self._last_snapshot
        self._last_snapshot = None
        self._restore_state(snapshot)
        self._last_action = None
        return action

    def _ensure_category(self, category_name):
        clean_name = str(category_name or "").strip()
        if not clean_name:
            raise ValueError("Category name cannot be empty.")
        category = self.get_category(clean_name)
        if category is not None:
            return category
        category = Category(clean_name)
        self.categories.append(category)
        self._reindex()
        return category

    def add(self, category_name, name, color, description, metadata=None):
        category = self._ensure_category(category_name)
        self._capture_snapshot("Add clothing")
        metadata = metadata or infer_metadata(category_name, name, color, description)
        category.items.append(ClothingItem(self.next_number(), name, color, description, metadata=metadata))
        self.save(False, "Add clothing")

    def metadata_for(self, uid):
        item, _ = self.find_item_by_uid(uid); return item.metadata() if item else {}

    def set_item_availability(self, uid, availability):
        item, _ = self.find_item_by_uid(uid)
        if not item: raise ValueError("Clothing item was not found.")
        if availability not in AVAILABILITY_OPTIONS: raise ValueError("Invalid availability value.")
        self._capture_snapshot("Update availability")
        item.availability = availability
        self.save(False, "Update availability")

    def delete(self, number):
        item, category = self.find_item(number)
        if not item: raise ValueError("Clothing item was not found.")
        self._capture_snapshot("Delete clothing")
        category.items.remove(item)
        self.usage_counts.pop(item.uid, None); self.favorites.discard(item.uid)
        self.saved_outfits = [{**o, "uids": [u for u in o["uids"] if u != item.uid]} for o in self.saved_outfits]
        self.saved_outfits = [o for o in self.saved_outfits if o["uids"]]
        self.usage_history = [h for h in self.usage_history if h["uid"] != item.uid]
        self.save(False, "Delete clothing")

    def delete_category(self, category_name):
        category = self.get_category(category_name)
        if category is None:
            raise ValueError("Category was not found.")
        self._capture_snapshot("Delete category")
        removed_uids = {item.uid for item in category.items}
        removed_count = len(removed_uids)
        self.categories = [c for c in self.categories if c is not category]
        self.usage_counts = {uid: count for uid, count in self.usage_counts.items() if uid not in removed_uids}
        self.favorites.difference_update(removed_uids)
        self.usage_history = [row for row in self.usage_history if row["uid"] not in removed_uids]
        cleaned_outfits = []
        for outfit in self.saved_outfits:
            uids = [uid for uid in outfit.get("uids", []) if uid not in removed_uids]
            if uids:
                updated = dict(outfit)
                updated["uids"] = uids
                cleaned_outfits.append(updated)
        self.saved_outfits = cleaned_outfits
        self._reindex()
        self.save(False, "Delete category")
        return category.name, removed_count

    def update(self, number, category_name, name, color, description, metadata=None):
        item, old_category = self.find_item(number)
        if not item: raise ValueError("Clothing item was not found.")
        new_category = self._ensure_category(category_name)
        self._capture_snapshot("Edit clothing")
        metadata = metadata or item.metadata()
        if old_category is not new_category:
            old_category.items.remove(item)
            new_category.items.append(ClothingItem(number, name, color, description, uid=item.uid, metadata=metadata))
        else:
            old_category.items[:] = [ClothingItem(number, name, color, description, uid=item.uid, metadata=metadata) if x.uid == item.uid else x for x in old_category.items]
        self.save(False, "Edit clothing")

    def toggle_favorite(self, uid):
        if uid not in self._uid_map: raise ValueError("Clothing item was not found.")
        self._capture_snapshot("Toggle favorite")
        value = uid not in self.favorites
        (self.favorites.add(uid) if value else self.favorites.discard(uid))
        self.save(False, "Toggle favorite")
        return value

    def mark_items_used(self, uids, source="Manual use"):
        clean = []
        for uid in uids:
            if uid and uid not in clean and uid in self._uid_map: clean.append(uid)
        if not clean: return 0
        self._capture_snapshot("Record usage")
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        for uid in clean:
            self.usage_counts[uid] = self.usage_counts.get(uid, 0) + 1
            self.usage_history.append({"timestamp": stamp, "uid": uid, "source": source})
        self.usage_history = self.usage_history[-500:]
        self.save(False, "Record usage")
        return len(clean)

    def mark_item_used(self, uid, source="Manual use"): return self.mark_items_used([uid], source)

    def add_outfit(self, name, uids):
        unique = []
        for uid in uids:
            if uid in self._uid_map and uid not in unique: unique.append(uid)
        if not unique: raise ValueError("Add at least one clothing item to the outfit.")
        self._capture_snapshot("Save outfit")
        oid = self.next_outfit_id()
        self.saved_outfits.append({"id": oid, "name": name.strip() or f"Outfit {oid}", "favorite": False, "use_count": 0, "created": datetime.now().strftime("%Y-%m-%d %H:%M"), "uids": unique})
        self.save(False, "Save outfit")

    def update_outfit(self, outfit_id, name, uids):
        outfit = next((o for o in self.saved_outfits if o["id"] == outfit_id), None)
        if not outfit: raise ValueError("Outfit was not found.")
        unique = []
        for uid in uids:
            if uid in self._uid_map and uid not in unique: unique.append(uid)
        if not unique: raise ValueError("Add at least one clothing item to the outfit.")
        self._capture_snapshot("Edit outfit")
        outfit["name"] = name.strip() or outfit.get("name", f"Outfit {outfit_id}")
        outfit["uids"] = unique
        self.save(False, "Edit outfit")

    def toggle_outfit_favorite(self, outfit_id):
        outfit = next((o for o in self.saved_outfits if o["id"] == outfit_id), None)
        if not outfit: raise ValueError("Outfit was not found.")
        self._capture_snapshot("Toggle outfit favorite")
        outfit["favorite"] = not outfit.get("favorite", False)
        self.save(False, "Toggle outfit favorite")
        return outfit["favorite"]

    def delete_outfit(self, outfit_id):
        if not any(o["id"] == outfit_id for o in self.saved_outfits): raise ValueError("Outfit was not found.")
        self._capture_snapshot("Delete outfit")
        self.saved_outfits = [o for o in self.saved_outfits if o["id"] != outfit_id]
        self.save(False, "Delete outfit")

    def mark_outfit_used(self, outfit_id):
        outfit = next((o for o in self.saved_outfits if o["id"] == outfit_id), None)
        if not outfit: raise ValueError("Outfit was not found.")
        self._capture_snapshot("Record outfit usage")
        outfit["use_count"] = int(outfit.get("use_count", 0)) + 1
        clean = [uid for uid in outfit["uids"] if uid in self._uid_map]
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        for uid in clean:
            self.usage_counts[uid] = self.usage_counts.get(uid, 0) + 1
            self.usage_history.append({"timestamp": stamp, "uid": uid, "source": outfit["name"]})
        self.usage_history = self.usage_history[-500:]
        self.save(False, "Record outfit usage")
        return outfit["use_count"]

    def _archive_statistics(self, month):
        """Archive the pre-reset statistics state as a verified SQLite snapshot.

        Backup folders contain database backups only. The snapshot is not included
        in the normal rolling-backup count because it represents the month before
        a statistics reset.
        """
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        safe_month = (month or datetime.now().strftime("%Y-%m")).replace("/", "-")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = BACKUP_DIR / f"statistics_{safe_month}_{stamp}.db"
        temp_target = BACKUP_DIR / f".{target.stem}.tmp.db"
        try:
            dest = sqlite3.connect(temp_target)
            try:
                self.conn.backup(dest)
            finally:
                dest.close()
            self._verify_backup(temp_target)
            os.replace(temp_target, target)
            return target
        except Exception:
            try:
                if temp_target.exists():
                    temp_target.unlink()
            except OSError:
                pass
            return None

    def reset_statistics(self, action="Reset statistics"):
        self._capture_snapshot(action)
        self._archive_statistics(self.stats_month)
        self.usage_counts = {}
        self.usage_history = []
        for outfit in self.saved_outfits: outfit["use_count"] = 0
        self.stats_month = datetime.now().strftime("%Y-%m")
        self.last_stats_reset = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._stats_month_present = True
        self.save(False, action)
        with self.conn:
            self.conn.execute("DELETE FROM wear_history")

    def ensure_monthly_statistics(self, auto_reset=True):
        current_month = datetime.now().strftime("%Y-%m")
        if self.stats_month == current_month or not auto_reset: return False
        self.reset_statistics("Automatic monthly reset")
        return True

    def items_for_role(self, role, available_only=True):
        items = [item for c in self.categories for item in c.items if category_role(c.name) == role]
        return [item for item in items if item.availability == "Available"] if available_only else items

    @staticmethod
    def matches_any_tag(item, field_name, selected):
        if not selected or selected in {"Any", "Any weather", "Any occasion", "Any style", "Any color"}: return True
        return selected.casefold() in getattr(item, field_name, "").casefold()


class StatCard(QFrame):
    def __init__(self, value=0, label="", compact=False):
        super().__init__()
        self.setObjectName("StatCard")
        if compact:
            box = QHBoxLayout(self)
            box.setContentsMargins(14, 11, 14, 11)
            box.setSpacing(10)
            self.setFixedHeight(88)
        else:
            box = QVBoxLayout(self)
            box.setContentsMargins(16, 14, 16, 14)

        num = QLabel(str(value)); num.setObjectName("StatNumber")
        text = QLabel(label); text.setObjectName("StatLabel")
        if compact:
            num.setObjectName("StatNumberCompact")
            text.setObjectName("StatLabelCompact")
            text.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            box.addWidget(num, 0, Qt.AlignVCenter)
            box.addWidget(text, 1, Qt.AlignVCenter)
        else:
            box.addWidget(num); box.addWidget(text)


class CategoryCard(QFrame):
    clicked = Signal(object)
    def __init__(self, category, main_window):
        super().__init__(); self.category = category; self.main_window = main_window
        self.setObjectName("CategoryCard"); self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(118)
        box = QHBoxLayout(self); box.setContentsMargins(16, 14, 14, 14)
        info = QVBoxLayout(); info.setSpacing(4)
        self.name = QLabel(); self.name.setObjectName("CategoryName")
        info.addWidget(self.name)
        self.metrics = []
        for _ in range(3):
            metric = QLabel(); metric.setObjectName("CategoryMetric")
            self.metrics.append(metric)
            info.addWidget(metric)
        info.addStretch()
        arrow = QLabel("›"); arrow.setObjectName("Arrow"); arrow.setAlignment(Qt.AlignCenter)
        box.addLayout(info, 1); box.addWidget(arrow)
        self.update_category(category)
    def update_category(self, category):
        self.category = category
        items = list(category.items)
        colors = {normalize_color(item.color) for item in items if (item.color or '').strip()}
        types = {item.name.strip().casefold() for item in items if (item.name or '').strip()}
        self.name.setText(display_category_name(category.name))
        color_word = "color" if len(colors) == 1 else "colors"
        type_word = "type" if len(types) == 1 else "types"
        values = [f"{category.count} items", f"{len(colors)} {color_word}", f"{len(types)} {type_word}"]
        for label, value in zip(self.metrics, values):
            label.setText(value)
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton: self.clicked.emit(self.category)
        super().mousePressEvent(event)


class ClothingCard(QFrame):
    def __init__(self, item, main_window):
        super().__init__(); self.item = item; self.main_window = main_window
        self.setObjectName("ItemCard"); self.setCursor(Qt.PointingHandCursor)
        box = QVBoxLayout(self); box.setContentsMargins(14, 14, 14, 12); box.setSpacing(7)
        top = QHBoxLayout(); number = QLabel(f"#{item.number}"); number.setObjectName("ItemNumber")
        self.favorite = QPushButton("★" if item.uid in main_window.repository.favorites else "☆")
        self.favorite.setFlat(True); self.favorite.setCursor(Qt.PointingHandCursor); self.favorite.setFixedWidth(30)
        self.favorite.clicked.connect(lambda: main_window.toggle_favorite(item.uid))
        top.addWidget(number); top.addStretch(); top.addWidget(self.favorite)
        box.addLayout(top)
        name = QLabel(item.name); name.setObjectName("ItemName"); name.setWordWrap(True)
        color = QLabel(item.color or "No color"); color.setObjectName("ItemColor")
        desc = QLabel(item.description or "No description"); desc.setObjectName("ItemDescription"); desc.setWordWrap(True)
        used = QLabel(f"Worn {main_window.repository.usage_counts.get(item.uid, 0)} times"); used.setObjectName("SmallMuted")
        box.addWidget(name); box.addWidget(color); box.addWidget(desc); box.addWidget(used)
        actions = QHBoxLayout(); edit = make_button("Edit"); use = make_button("Worn"); delete = make_button("Delete", "Delete")
        edit.clicked.connect(lambda: main_window.edit_item(item)); use.clicked.connect(lambda: main_window.mark_item_used(item.uid))
        delete.clicked.connect(lambda: main_window.delete_item(item)); actions.addWidget(edit); actions.addWidget(use); actions.addWidget(delete); box.addLayout(actions)
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton: self.main_window.show_item_detail(self.item)
        super().mousePressEvent(event)


class FeatureCard(QFrame):
    def __init__(self, title, text, button_text, callback):
        super().__init__(); self.setObjectName("FeatureCard")
        box = QVBoxLayout(self); box.setContentsMargins(16, 16, 16, 16); box.setSpacing(8)
        label = QLabel(title); label.setObjectName("FeatureTitle")
        body = QLabel(text); body.setObjectName("SectionMuted"); body.setWordWrap(True)
        btn = make_button(button_text, "Primary"); btn.clicked.connect(callback)
        box.addWidget(label); box.addWidget(body, 1); box.addWidget(btn, 0, Qt.AlignLeft)


class HomePage(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.build_ui()

    def build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 4, 8, 8)
        outer.setSpacing(14)

        title = QLabel("Welcome back")
        title.setObjectName("PageTitle")
        subtitle = QLabel("A clear overview of your wardrobe, categories, and favorites.")
        subtitle.setObjectName("Subtitle")
        outer.addWidget(title)
        outer.addWidget(subtitle)

        stats = QHBoxLayout()
        stats.setSpacing(12)
        self.stats = [
            StatCard(0, "Total items"),
            StatCard(0, "Categories"),
            StatCard(0, "Total wears"),
            StatCard(0, "Favorites"),
        ]
        for card in self.stats:
            stats.addWidget(card, 1)
        outer.addLayout(stats)

        lower = QHBoxLayout()
        lower.setSpacing(12)

        category_frame = QFrame()
        category_frame.setObjectName("FormCard")
        category_box = QVBoxLayout(category_frame)
        category_box.setContentsMargins(14, 14, 14, 14)
        category_box.setSpacing(8)
        category_heading = QHBoxLayout()
        category_heading.addWidget(styled_label("Categories", "SectionTitle"))
        category_heading.addStretch()
        category_box.addLayout(category_heading)
        self.category_grid = QGridLayout()
        self.category_grid.setSpacing(10)
        category_box.addLayout(self.category_grid)
        lower.addWidget(category_frame, 3)

        recent = QFrame()
        recent.setObjectName("FormCard")
        recent_box = QVBoxLayout(recent)
        recent_box.setContentsMargins(16, 14, 16, 14)
        recent_box.setSpacing(8)
        recent_box.addWidget(styled_label("Recently worn", "SectionTitle"))
        self.recent_layout = QVBoxLayout()
        self.recent_layout.setSpacing(7)
        recent_box.addLayout(self.recent_layout)
        recent_box.addStretch()
        lower.addWidget(recent, 2)

        outer.addLayout(lower, 1)

    def refresh(self):
        repo = self.main_window.repository
        values = (repo.total, len(repo.categories), repo.total_uses, len(repo.favorites))
        for card, value in zip(self.stats, values):
            number = card.findChild(QLabel, "StatNumber")
            if number:
                number.setText(str(value))

        clear_layout(self.recent_layout)
        recent = list(reversed(repo.usage_history[-5:]))
        if recent:
            for row in recent:
                item, _ = repo.find_item_by_uid(row.get("uid"))
                if not item:
                    continue
                line = QHBoxLayout()
                label = QLabel(item.label())
                label.setObjectName("SmallMuted")
                stamp = QLabel(row.get("timestamp", ""))
                stamp.setObjectName("SmallMuted")
                line.addWidget(label, 1)
                line.addWidget(stamp)
                self.recent_layout.addLayout(line)
        else:
            self.recent_layout.addWidget(styled_label("No wear history yet.", "SectionMuted"))


        clear_layout(self.category_grid)
        columns = 3
        for i, category in enumerate(repo.categories):
            card = CategoryCard(category, self.main_window)
            card.clicked.connect(self.main_window.open_category)
            self.category_grid.addWidget(card, i // columns, i % columns)


class ScrollGrid(QWidget):
    def __init__(self):
        super().__init__(); self.layout = QGridLayout(self); self.layout.setContentsMargins(0, 0, 0, 0); self.layout.setSpacing(12)
    def render(self, widgets, columns=3):
        clear_layout(self.layout)
        for i, widget in enumerate(widgets): self.layout.addWidget(widget, i // columns, i % columns)


class AllItemsPage(QWidget):
    def __init__(self, main_window):
        super().__init__(); self.main_window = main_window; self.build_ui()
    def build_ui(self):
        root = QVBoxLayout(self); root.setContentsMargins(6, 4, 8, 8); root.setSpacing(10)
        row = QHBoxLayout(); title = QLabel("All Clothing"); title.setObjectName("PageTitle"); row.addWidget(title); row.addStretch(); root.addLayout(row)
        self.search = self.main_window.global_search
        filters = QGridLayout()
        filters.setHorizontalSpacing(10)
        filters.setVerticalSpacing(10)
        filters.setColumnStretch(0, 1)
        filters.setColumnStretch(1, 1)
        filters.setColumnStretch(2, 1)
        filters.setColumnStretch(3, 1)

        self.category_filter = QComboBox()
        self.color_filter = QComboBox()
        self.role_filter = QComboBox()
        self.usage_filter = QComboBox()
        self.favorite_filter = QComboBox()
        self.availability_filter = QComboBox()
        self.sort_filter = QComboBox()
        combos = (self.category_filter, self.color_filter, self.role_filter, self.usage_filter, self.favorite_filter, self.availability_filter, self.sort_filter)
        for combo in combos:
            combo.setObjectName("Input")
            combo.setMinimumHeight(42)
            combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            combo.setMinimumContentsLength(12)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMaxVisibleItems(10)
            combo.view().setMinimumWidth(320)

        self.sort_filter.addItems(["Sort", "Name A-Z", "Most worn", "Recently added"])
        self.usage_filter.addItems(["Usage: All", "Unused only", "Worn only"])
        self.favorite_filter.addItems(["Favorites: All", "Favorites only"])
        self.availability_filter.addItems(["Availability: All", "Available", "In Laundry", "Unavailable"])

        clear = make_button("Clear filters", "FilterClear")
        clear.setMinimumHeight(42)
        clear.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        for combo in (self.category_filter, self.color_filter, self.role_filter, self.usage_filter):
            combo.currentIndexChanged.connect(self.refresh_results)
        self.favorite_filter.currentIndexChanged.connect(self.refresh_results)
        self.availability_filter.currentIndexChanged.connect(self.refresh_results)
        self.sort_filter.currentIndexChanged.connect(self.refresh_results)
        clear.clicked.connect(self.clear_filters)

        filters.addWidget(self.category_filter, 0, 0)
        filters.addWidget(self.color_filter, 0, 1)
        filters.addWidget(self.role_filter, 0, 2)
        filters.addWidget(self.usage_filter, 0, 3)
        filters.addWidget(self.favorite_filter, 1, 0)
        filters.addWidget(self.availability_filter, 1, 1)
        filters.addWidget(self.sort_filter, 1, 2)
        filters.addWidget(clear, 1, 3)
        root.addLayout(filters)
        self.count_label = QLabel(); self.count_label.setObjectName("SectionMuted"); root.addWidget(self.count_label)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); body = QWidget(); self.grid = ScrollGrid(); layout = QVBoxLayout(body); layout.setContentsMargins(0, 0, 0, 0); layout.addWidget(self.grid); layout.addStretch(); scroll.setWidget(body); root.addWidget(scroll, 1)
    def refresh(self):
        repo = self.main_window.repository
        current_cat = self.category_filter.currentData()
        current_color = self.color_filter.currentData()
        current_role = self.role_filter.currentData()
        self.category_filter.blockSignals(True); self.color_filter.blockSignals(True); self.role_filter.blockSignals(True)
        self.category_filter.clear(); self.category_filter.addItem("All categories", None)
        for c in repo.categories: self.category_filter.addItem(display_category_name(c.name), c.name)
        colors = sorted({item.color for item in repo.all_items() if item.color}, key=str.casefold)
        self.color_filter.clear(); self.color_filter.addItem("All colors", None)
        for color in colors: self.color_filter.addItem(color, color)
        self.role_filter.clear(); self.role_filter.addItem("All types", None)
        roles = sorted({category_role(c.name) for c in repo.categories}, key=role_label)
        for role in roles: self.role_filter.addItem(ROLE_LABELS.get(role, role), role)
        self.category_filter.blockSignals(False); self.color_filter.blockSignals(False); self.role_filter.blockSignals(False)
        self._restore_combo(self.category_filter, current_cat); self._restore_combo(self.color_filter, current_color); self._restore_combo(self.role_filter, current_role); self.refresh_results()
    @staticmethod
    def _restore_combo(combo, data):
        if data is None: combo.setCurrentIndex(0); return
        idx = combo.findData(data); combo.setCurrentIndex(idx if idx >= 0 else 0)
    def clear_filters(self):
        self.search.clear(); self.category_filter.setCurrentIndex(0); self.color_filter.setCurrentIndex(0); self.role_filter.setCurrentIndex(0); self.usage_filter.setCurrentIndex(0); self.favorite_filter.setCurrentIndex(0); self.availability_filter.setCurrentIndex(0); self.sort_filter.setCurrentIndex(0)
    def refresh_results(self):
        repo = self.main_window.repository; query = self.search.text().strip().casefold(); cat = self.category_filter.currentData(); color = self.color_filter.currentData(); role = self.role_filter.currentData(); usage_mode = self.usage_filter.currentIndex(); favorites_only = self.favorite_filter.currentIndex() == 1; availability = self.availability_filter.currentText(); sort_mode = self.sort_filter.currentIndex()
        rows = []
        for c in repo.categories:
            for item in c.items:
                combined = f"{item.search_text()} {c.name} {display_category_name(c.name)}".casefold()
                if query and query not in combined: continue
                if cat and c.name != cat: continue
                if color and item.color != color: continue
                if role and category_role(c.name) != role: continue
                worn_count = repo.usage_counts.get(item.uid, 0)
                if usage_mode == 1 and worn_count > 0: continue
                if usage_mode == 2 and worn_count <= 0: continue
                if favorites_only and item.uid not in repo.favorites: continue
                if availability != "Availability: All" and item.availability != availability: continue
                rows.append(item)
        if sort_mode == 1: rows.sort(key=lambda x: x.name.casefold())
        elif sort_mode == 2: rows.sort(key=lambda x: (-repo.usage_counts.get(x.uid, 0), x.name.casefold()))
        elif sort_mode == 3: rows.sort(key=lambda x: -x.number)
        self.count_label.setText(f"Showing {len(rows)} of {repo.total} items")
        if not rows:
            self.grid.render([empty_state("No clothing found", "Try clearing the filters or adding a new item.")], 1); return
        self.grid.render([ClothingCard(item, self.main_window) for item in rows], 3)


class UnusedPage(QWidget):
    """Shows clothing items that have never been worn."""
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.build_ui()

    def build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 4, 8, 8)
        root.setSpacing(10)

        row = QHBoxLayout()
        title = QLabel("Unused Clothing")
        title.setObjectName("PageTitle")
        row.addWidget(title)
        row.addStretch()
        root.addLayout(row)
        self.search = self.main_window.global_search

        self.summary = QLabel()
        self.summary.setObjectName("SectionMuted")
        root.addWidget(self.summary)

        self.grid = ScrollGrid()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.grid)
        lay.addStretch()
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

    def refresh(self):
        repo = self.main_window.repository
        query = self.search.text().strip().casefold()
        rows = []
        for category in repo.categories:
            for item in category.items:
                if repo.usage_counts.get(item.uid, 0) > 0:
                    continue
                combined = f"{item.search_text()} {category.name} {display_category_name(category.name)}".casefold()
                if query and query not in combined:
                    continue
                rows.append(item)

        self.summary.setText(f"{len(rows)} unused item{'s' if len(rows) != 1 else ''} · These pieces have never been marked as worn.")
        if not rows:
            self.grid.render([empty_state("Your wardrobe is fully in rotation", "Every clothing item has been worn at least once, or no item matches your search.")], 1)
            return
        rows.sort(key=lambda x: (x.name.casefold(), x.number))
        self.grid.render([ClothingCard(item, self.main_window) for item in rows], 3)


class CategoryPage(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.current_category = None
        self.build_ui()

    def build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 4, 8, 8)
        root.setSpacing(10)

        top = QHBoxLayout()
        self.title = QLabel("Category")
        self.title.setObjectName("PageTitle")
        back = make_button("← Back", "Back")
        back.clicked.connect(self.main_window.show_home)
        top.addWidget(back)
        top.addWidget(self.title)
        top.addStretch()
        # Category search uses the single global search field owned by MainWindow.
        # No second QLineEdit is created here.
        self.search = self.main_window.global_search

        self.add = make_button("+ Add Clothing", "Primary")
        self.add.clicked.connect(self.main_window.show_add)
        top.addWidget(self.add)
        root.addLayout(top)

        self.grid = ScrollGrid()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.grid)
        lay.addStretch()
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

    def show_category(self, category):
        self.current_category = category.name
        self.title.setText(display_category_name(category.name))
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.search.setPlaceholderText(f"Search {display_category_name(category.name).lower()}...")
        self.render_current()

    def render_current(self):
        category = self.main_window.repository.get_category(self.current_category or "")
        if not category:
            self.grid.render([empty_state("Category not found", "Return to the home page.")], 1)
            return
        q = self.search.text().strip().casefold()
        rows = [
            i for i in category.items
            if not q or q in i.search_text() or q in category.name.casefold() or q in display_category_name(category.name).casefold()
        ]
        self.grid.render(
            [ClothingCard(i, self.main_window) for i in rows]
            if rows else [empty_state("No matches", "Try another search.")],
            3,
        )


class OutfitBuilderPage(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.current = {}
        self.editing_outfit_id = None
        self.editing_outfit_name = ""
        self.build_ui()

    def _configure_combo(self, combo):
        combo.setObjectName("Input")
        combo.setMinimumHeight(44)
        combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        combo.setMinimumContentsLength(16)
        combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        combo.setMaxVisibleItems(10)
        combo.setInsertPolicy(QComboBox.NoInsert)
        combo.view().setMinimumWidth(320)

    def _make_role_field(self, role, label):
        card = QFrame()
        card.setObjectName("FormCard")
        card.setMinimumHeight(92)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        box = QVBoxLayout(card)
        box.setContentsMargins(14, 12, 14, 12)
        box.setSpacing(7)

        title = styled_label(label, "FormLabel")
        combo = QComboBox()
        self._configure_combo(combo)
        combo.setMinimumHeight(42)
        combo.setMaximumHeight(42)
        self.combos[role] = combo

        box.addWidget(title)
        box.addWidget(combo)
        return card

    def build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 4, 14, 10)
        root.setSpacing(10)

        title = QLabel("Outfit Builder")
        title.setObjectName("PageTitle")
        root.addWidget(title)

        subtitle = QLabel("Build a look by role. Keep the core pieces balanced and add layers only when needed.")
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        form = QFrame()
        form.setObjectName("FormCard")
        form.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        form_layout = QGridLayout(form)
        form_layout.setContentsMargins(18, 16, 18, 16)
        form_layout.setHorizontalSpacing(14)
        form_layout.setVerticalSpacing(10)
        for col in range(3):
            form_layout.setColumnStretch(col, 1)

        self.combos = {}

        # A three-column grid keeps the whole page balanced instead of
        # letting the final optional selector disturb the two-column layout.
        core_roles = [
            ("upper", "Top"),
            ("pants", "Pants"),
            ("shoes", "Shoes"),
        ]
        finish_roles = [
            ("belt", "Belts"),
            ("layer", "Cardigans & Layers"),
            ("outer", "Outerwear"),
        ]

        core_label = QLabel("Core pieces")
        core_label.setObjectName("SectionTitle")
        form_layout.addWidget(core_label, 0, 0, 1, 3)

        for col, (role, label) in enumerate(core_roles):
            form_layout.addWidget(self._make_role_field(role, label), 1, col)

        finish_label = QLabel("Layers & finishing touches")
        finish_label.setObjectName("SectionTitle")
        form_layout.addWidget(finish_label, 2, 0, 1, 3)

        for col, (role, label) in enumerate(finish_roles):
            form_layout.addWidget(self._make_role_field(role, label), 3, col)

        accessories_label = QLabel("Accessories")
        accessories_label.setObjectName("SectionTitle")
        form_layout.addWidget(accessories_label, 4, 0, 1, 3)

        glasses_field = self._make_role_field("glasses", "Sunglasses")
        watch_field = self._make_role_field("watch", "Wristwatch")
        form_layout.addWidget(glasses_field, 5, 0)
        form_layout.addWidget(watch_field, 5, 1)

        root.addWidget(form)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        random_btn = make_button("Smart Random", "Secondary")
        random_btn.clicked.connect(self.randomize)
        clear_btn = make_button("Clear", "Secondary")
        clear_btn.clicked.connect(self.clear)
        save_btn = make_button("Save Outfit", "Primary")
        save_btn.clicked.connect(self.save_outfit)
        buttons.addWidget(random_btn)
        buttons.addWidget(clear_btn)
        buttons.addStretch(1)
        buttons.addWidget(save_btn)
        root.addLayout(buttons)
        root.addStretch(1)

    def refresh(self):
        repo = self.main_window.repository
        for role, combo in self.combos.items():
            combo.blockSignals(True)
            old = combo.currentData()
            combo.clear()
            combo.addItem("— None —", None)
            for item in repo.items_for_role(role):
                combo.addItem(item.label(), item.uid)
            idx = combo.findData(old)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)

    def set_item(self, item):
        found = self.main_window.repository.find_item_by_uid(item.uid)
        if not found:
            return
        role = category_role(found[1].name)
        if role in self.combos:
            idx = self.combos[role].findData(item.uid)
            if idx >= 0:
                self.combos[role].setCurrentIndex(idx)

    def clear(self):
        for combo in self.combos.values():
            combo.setCurrentIndex(0)

    def randomize(self):
        repo = self.main_window.repository
        for role, combo in self.combos.items():
            items = repo.items_for_role(role)
            if items:
                ranked = sorted(items, key=lambda i: (repo.usage_counts.get(i.uid, 0), random.random()))
                choice = random.choice(ranked[:max(1, min(4, len(ranked)))])
                idx = combo.findData(choice.uid)
                if idx >= 0:
                    combo.setCurrentIndex(idx)

    def save_outfit(self):
        uids = [c.currentData() for c in self.combos.values() if c.currentData()]
        if not uids:
            QMessageBox.warning(self, "Nothing to save", "Select at least one item.")
            return

        default_name = self.editing_outfit_name or f"Outfit {self.main_window.repository.next_outfit_id()}"
        name, ok = QInputDialog.getText(self, "Save outfit" if self.editing_outfit_id is None else "Edit outfit", "Outfit name:", text=default_name)
        if not ok:
            return
        try:
            if self.editing_outfit_id is None:
                self.main_window.repository.add_outfit(name, uids)
                message = "Outfit saved."
            else:
                self.main_window.repository.update_outfit(self.editing_outfit_id, name, uids)
                message = "Outfit updated."
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return

        self.editing_outfit_id = None
        self.editing_outfit_name = ""
        self.main_window.show_saved_outfits()
        self.main_window.notify(message)

    def edit_outfit(self, outfit):
        self.editing_outfit_id = outfit["id"]
        self.editing_outfit_name = outfit.get("name", "Outfit")
        self.main_window.show_builder()
        self.refresh()
        self.clear()
        for uid in outfit.get("uids", []):
            item, _ = self.main_window.repository.find_item_by_uid(uid)
            if item:
                self.set_item(item)


class SavedOutfitsPage(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.build_ui()

    def build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 8, 14, 12)
        root.setSpacing(12)

        header = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(2)
        title = QLabel("Saved Outfits")
        title.setObjectName("PageTitle")
        subtitle = QLabel("Your saved outfits in one place.")
        subtitle.setObjectName("Subtitle")
        heading.addWidget(title)
        heading.addWidget(subtitle)
        header.addLayout(heading)
        header.addStretch()
        root.addLayout(header)

        self.list_layout = QVBoxLayout()
        self.list_layout.setSpacing(10)
        self.list_layout.setContentsMargins(0, 2, 0, 0)
        container = QWidget()
        container.setLayout(self.list_layout)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(container)
        root.addWidget(scroll, 1)

    def refresh(self):
        clear_layout(self.list_layout)
        outfits = sorted(self.main_window.repository.saved_outfits, key=lambda o: o.get("id", 0), reverse=True)
        if not outfits:
            self.list_layout.addWidget(
                empty_state("No saved outfits", "Save an outfit from Outfit Builder to see it here.")
            )
            self.list_layout.addStretch(1)
            return

        for outfit in outfits:
            self.list_layout.addWidget(self._build_card(outfit))
        self.list_layout.addStretch(1)

    def _build_card(self, outfit):
        card = QFrame()
        card.setObjectName("OutfitCard")
        card.setMinimumHeight(126)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        root = QVBoxLayout(card)
        root.setContentsMargins(16, 13, 16, 13)
        root.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(8)
        title = QLabel(outfit.get("name", "Outfit"))
        title.setObjectName("FeatureTitle")
        title.setWordWrap(True)
        top.addWidget(title, 1)

        fav = make_button("★" if outfit.get("favorite") else "☆", "IconButton")
        fav.setToolTip("Remove from favorites" if outfit.get("favorite") else "Add to favorites")
        fav.clicked.connect(lambda _, oid=outfit["id"]: self.main_window.toggle_outfit_favorite(oid))
        top.addWidget(fav, 0, Qt.AlignTop)
        root.addLayout(top)

        names = []
        for uid in outfit.get("uids", []):
            item, _ = self.main_window.repository.find_item_by_uid(uid)
            if item:
                names.append(item.label())
        pieces = QLabel("  •  ".join(names) if names else "No clothing items")
        pieces.setObjectName("SectionMuted")
        pieces.setWordWrap(True)
        root.addWidget(pieces)

        meta = QLabel(f"Worn {int(outfit.get('use_count', 0))} times  ·  Saved {outfit.get('created', '')}")
        meta.setObjectName("SmallMuted")
        root.addWidget(meta)

        actions = QHBoxLayout()
        actions.setSpacing(7)
        edit = make_button("Edit", "Secondary")
        edit.clicked.connect(lambda _, o=outfit: self.main_window.builder_page.edit_outfit(o))
        unsave = make_button("Unsave", "Secondary")
        unsave.setToolTip("Remove this outfit from Saved Outfits")
        unsave.clicked.connect(lambda _, oid=outfit["id"]: self._unsave(oid))
        worn = make_button("Mark as worn", "Primary")
        worn.clicked.connect(lambda _, oid=outfit["id"]: self.main_window.mark_outfit_used(oid))
        delete = make_button("Delete", "Delete")
        delete.clicked.connect(lambda _, oid=outfit["id"]: self.main_window.delete_outfit(oid))
        actions.addWidget(edit)
        actions.addWidget(unsave)
        actions.addWidget(worn)
        actions.addWidget(delete)
        actions.addStretch(1)
        root.addLayout(actions)
        return card

    def _unsave(self, outfit_id):
        answer = QMessageBox.question(
            self,
            "Unsave outfit",
            "Remove this outfit from Saved Outfits?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self.main_window.repository.delete_outfit(outfit_id)
        except Exception as e:
            QMessageBox.critical(self, "Unsave failed", str(e))
            return
        self.main_window.refresh_visible_page()
        self.main_window.notify("Outfit removed from Saved Outfits.")


class TodayPage(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.current_uids = []
        self.build_ui()

    def build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 12, 12)
        root.setSpacing(14)

        header = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(2)
        title = QLabel("What should I wear today?")
        title.setObjectName("PageTitle")
        subtitle = styled_label("Generate a balanced outfit from your available wardrobe.", "SectionMuted")
        heading.addWidget(title)
        heading.addWidget(subtitle)
        header.addLayout(heading)
        header.addStretch()
        root.addLayout(header)

        controls = QFrame()
        controls.setObjectName("FormCard")
        controls_layout = QGridLayout(controls)
        controls_layout.setContentsMargins(16, 14, 16, 14)
        controls_layout.setHorizontalSpacing(12)
        controls_layout.setVerticalSpacing(8)

        self.weather = QComboBox()
        self.occasion = QComboBox()
        self.style = QComboBox()
        self.color = QComboBox()
        for combo in (self.weather, self.occasion, self.style, self.color):
            combo.setObjectName("Input")
            combo.setMinimumHeight(38)

        self.weather.addItems(["Any weather", "Hot", "Mild", "Cool", "Cold", "Rainy"])
        self.occasion.addItems(["Any occasion", "Work", "Casual", "Dinner", "Formal"])
        self.style.addItems(["Any style", "Minimal", "Smart casual", "Classic", "Relaxed"])
        self.color.addItems(["Any color", "Dark", "Light", "Neutral", "Warm"])

        filters = [
            ("Weather", self.weather),
            ("Occasion", self.occasion),
            ("Style", self.style),
            ("Color", self.color),
        ]
        for column, (label_text, combo) in enumerate(filters):
            box = QVBoxLayout()
            box.setSpacing(5)
            label = QLabel(label_text)
            label.setObjectName("FormLabel")
            box.addWidget(label)
            box.addWidget(combo)
            controls_layout.addLayout(box, 0, column)
            controls_layout.setColumnStretch(column, 1)

        generate = make_button("Generate outfit", "Primary")
        generate.setMinimumHeight(38)
        generate.clicked.connect(self.generate)
        controls_layout.addWidget(generate, 1, 0, 1, 4, Qt.AlignRight)
        root.addWidget(controls)

        self.hero = QFrame()
        self.hero.setObjectName("TodayHero")
        self.hero_layout = QVBoxLayout(self.hero)
        self.hero_layout.setContentsMargins(18, 16, 18, 16)
        self.hero_layout.setSpacing(10)
        root.addWidget(self.hero, 1)

        actions = QHBoxLayout()
        actions.setContentsMargins(2, 0, 2, 0)
        actions.setSpacing(8)
        self.save_btn = make_button("Save suggestion", "Secondary")
        self.next_btn = make_button("Try another", "Secondary")
        self.use_btn = make_button("Mark as worn", "Primary")
        self.save_btn.clicked.connect(self.save_current)
        self.next_btn.clicked.connect(self.generate)
        self.use_btn.clicked.connect(self.use_current)
        actions.addWidget(self.save_btn)
        actions.addWidget(self.next_btn)
        actions.addStretch()
        actions.addWidget(self.use_btn)
        root.addLayout(actions)

    def refresh(self):
        self.update_repository_options()
        self.update_from_repository()

    def update_repository_options(self):
        pass

    def update_from_repository(self):
        if not self.current_uids:
            self.render_empty()
            return
        valid = [uid for uid in self.current_uids if uid in self.main_window.repository._uid_map]
        if not valid:
            self.current_uids = []
            self.render_empty()
            return
        self.current_uids = valid
        self.render_current()

    def render_empty(self):
        clear_layout(self.hero_layout)
        self.hero_layout.setAlignment(Qt.AlignCenter)
        empty = QVBoxLayout()
        empty.setSpacing(8)
        title = QLabel("No outfit generated yet")
        title.setObjectName("TodayTitle")
        title.setAlignment(Qt.AlignCenter)
        message = styled_label("Choose your preferences above, then generate an outfit.", "SectionMuted")
        message.setAlignment(Qt.AlignCenter)
        empty.addWidget(title)
        empty.addWidget(message)
        self.hero_layout.addLayout(empty)
        self.save_btn.setEnabled(False)
        self.use_btn.setEnabled(False)

    def score(self, item, role):
        repo = self.main_window.repository
        score = 0.0
        color = normalize_color(item.color)
        uses = repo.usage_counts.get(item.uid, 0)
        score += max(-1.5, -uses * 0.06)
        if self.occasion.currentText() not in {"Any occasion"} and self.occasion.currentText().casefold() in item.occasion.casefold():
            score += 2.5
        if self.style.currentText() not in {"Any style"} and self.style.currentText().casefold() in item.style.casefold():
            score += 2.5
        if self.weather.currentText() in {"Hot", "Mild", "Cool", "Cold", "Rainy"}:
            weather = self.weather.currentText()
            if weather == "Hot" and "Summer" in item.season:
                score += 2.2
            elif weather in {"Cool", "Rainy"} and any(s in item.season for s in ("Autumn", "Spring")):
                score += 1.8
            elif weather == "Cold" and "Winter" in item.season:
                score += 2.5
        if item.availability != "Available":
            score -= 10
        if self.color.currentText() == "Dark" and color in {"black", "dark brown", "burgundy", "charcoal melange", "khaki"}:
            score += 2
        if self.color.currentText() == "Light" and color in {"white", "light blue", "beige", "cream", "grey"}:
            score += 2
        if self.color.currentText() == "Neutral" and color in {"black", "white", "grey", "beige", "brown", "charcoal melange"}:
            score += 2
        if self.color.currentText() == "Warm" and color in {"brown", "dark brown", "burgundy", "beige", "khaki"}:
            score += 2
        if role in {"upper", "pants"} and self.occasion.currentText() == "Formal":
            if "polo" not in item.name.casefold() and "triko" not in item.name.casefold():
                score += 1
        return score

    def choose(self, role, required=True):
        items = self.main_window.repository.items_for_role(role)
        if not items:
            return None
        ranked = sorted(items, key=lambda i: self.score(i, role) + random.random() * 0.4, reverse=True)
        return ranked[0] if required else (ranked[0] if random.random() > 0.3 else None)

    def _role_card(self, role, item):
        card = QFrame()
        card.setObjectName("TodayRoleCard")
        card.setMinimumHeight(118)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(5)

        role_label = QLabel({"upper": "Tops", "pants": "Pants", "shoes": "Shoes", "belt": "Belts"}.get(role, ROLE_LABELS.get(role, role)))
        role_label.setObjectName("TodayRoleTitle")

        if item is None:
            item_label = QLabel("No suitable item")
            meta = QLabel("Try another suggestion")
        else:
            item_label = QLabel(item.label())
            uses = self.main_window.repository.usage_counts.get(item.uid, 0)
            meta = QLabel(f"{item.color} · {uses} wears")

        item_label.setObjectName("TodayRoleItem")
        item_label.setWordWrap(True)
        item_label.setMinimumHeight(38)
        item_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        meta.setObjectName("TodayRoleMeta")
        meta.setWordWrap(True)
        meta.setMinimumHeight(16)

        layout.addWidget(role_label)
        layout.addWidget(item_label, 1)
        layout.addWidget(meta)
        return card

    def _render_selection(self, selected, title_text="Today's suggestion"):
        clear_layout(self.hero_layout)
        self.hero_layout.setAlignment(Qt.AlignTop)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = styled_label(title_text, "TodayTitle")
        subtitle = styled_label(
            f"{self.occasion.currentText()} · {self.style.currentText()} · {self.weather.currentText()}",
            "SectionMuted",
        )
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch()
        context = styled_label(f"Color: {self.color.currentText()}", "SectionMuted")
        header.addWidget(context, 0, Qt.AlignTop)
        self.hero_layout.addLayout(header)

        selected_map = {role: item for role, item in selected}
        core_roles = [(role, selected_map.get(role)) for role in ("upper", "pants", "shoes", "belt")]
        optional_roles = [(r, item) for r, item in selected if r not in {"upper", "pants", "shoes", "belt"}]

        core_grid = QGridLayout()
        core_grid.setHorizontalSpacing(10)
        core_grid.setVerticalSpacing(10)
        for column in range(4):
            core_grid.setColumnStretch(column, 1)
        for column, (role, item) in enumerate(core_roles):
            core_grid.addWidget(self._role_card(role, item), 0, column)
        self.hero_layout.addLayout(core_grid)

        if optional_roles:
            extras = QHBoxLayout()
            extras.setSpacing(10)
            extras.setContentsMargins(0, 0, 0, 0)
            for role, item in optional_roles:
                extras.addWidget(self._role_card(role, item), 1)
            self.hero_layout.addLayout(extras)

        self.hero_layout.addStretch(1)

    def generate(self):
        selected = []
        for role in ("upper", "pants", "shoes", "belt"):
            item = self.choose(role, True)
            if item:
                selected.append((role, item))
        weather = self.weather.currentText()
        style = self.style.currentText()
        if weather in {"Cool", "Cold", "Rainy"}:
            item = self.choose("layer", False)
            if item:
                selected.append(("layer", item))
        if weather in {"Cold", "Rainy"}:
            item = self.choose("outer", False)
            if item:
                selected.append(("outer", item))
        if style in {"Classic", "Formal"}:
            item = self.choose("glasses", False)
            if item:
                selected.append(("glasses", item))
        accessory = self.choose("accessories", False)
        if accessory:
            selected.append(("accessories", accessory))
        self.current_uids = [item.uid for _, item in selected]
        if not selected:
            self.render_empty()
            return
        self._render_selection(selected, "Today's suggestion")
        self.save_btn.setEnabled(True)
        self.use_btn.setEnabled(True)

    def render_current(self):
        selected = []
        for uid in self.current_uids:
            item, cat = self.main_window.repository.find_item_by_uid(uid)
            if item:
                selected.append((category_role(cat.name), item))
        if not selected:
            self.render_empty()
            return
        self._render_selection(selected, "Today's suggestion")
        self.save_btn.setEnabled(True)
        self.use_btn.setEnabled(True)

    def save_current(self):
        if not self.current_uids:
            return
        name, ok = QInputDialog.getText(self, "Save suggestion", "Outfit name:", text=f"Daily Outfit {self.main_window.repository.next_outfit_id()}")
        if not ok:
            return
        try:
            self.main_window.repository.add_outfit(name, self.current_uids)
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return
        self.main_window.refresh_visible_page()
        self.main_window.notify("Suggestion saved.")

    def use_current(self):
        if not self.current_uids:
            return
        try:
            count = self.main_window.repository.mark_items_used(self.current_uids, "Today's suggestion")
        except Exception as e:
            QMessageBox.critical(self, "Could not record usage", str(e))
            return
        self.main_window.refresh_visible_page()
        self.main_window.notify(f"Marked {count} item(s) as worn.")


class StatsPage(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.build_ui()

    def build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 12, 12)
        root.setSpacing(16)

        header = QHBoxLayout()
        heading_box = QVBoxLayout()
        heading_box.setSpacing(3)
        title = QLabel("Wear Statistics")
        title.setObjectName("PageTitle")
        self.period_label = styled_label("Current period", "SectionMuted")
        heading_box.addWidget(title)
        heading_box.addWidget(self.period_label)
        header.addLayout(heading_box)
        header.addStretch()
        history = make_button("Wear history")
        history.clicked.connect(self.main_window.show_history_dialog)
        header.addWidget(history)
        root.addLayout(header)

        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.cards = [
            StatCard(0, "Total wears", compact=True),
            StatCard(0, "Pieces used", compact=True),
            StatCard(0, "Favorites", compact=True),
            StatCard(0, "Saved outfits", compact=True),
        ]
        for card in self.cards:
            cards.addWidget(card, 1)
        root.addLayout(cards)

        cols = QHBoxLayout()
        cols.setSpacing(14)
        self.usage_box = QVBoxLayout()
        self.usage_box.setSpacing(10)
        self.category_box = QVBoxLayout()
        self.category_box.setSpacing(10)

        left = QFrame()
        left.setObjectName("FormCard")
        left.setMinimumHeight(360)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(20, 18, 20, 18)
        ll.setSpacing(12)
        ll.addWidget(styled_label("Most worn pieces", "SectionTitle"))
        ll.addWidget(styled_label("Your most frequently used items this period.", "SectionMuted"))
        ll.addLayout(self.usage_box)
        ll.addStretch()

        right = QFrame()
        right.setObjectName("FormCard")
        right.setMinimumHeight(360)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(20, 18, 20, 18)
        rl.setSpacing(12)
        rl.addWidget(styled_label("Category distribution", "SectionTitle"))
        rl.addWidget(styled_label("How your wardrobe is distributed across categories.", "SectionMuted"))
        rl.addLayout(self.category_box)
        rl.addStretch()

        cols.addWidget(left, 1)
        cols.addWidget(right, 1)
        root.addLayout(cols, 1)

    def refresh(self):
        repo = self.main_window.repository
        pieces_used = sum(1 for item in repo.all_items() if repo.usage_counts.get(item.uid, 0) > 0)
        values = (repo.total_uses, pieces_used, len(repo.favorites), len(repo.saved_outfits))
        for card, value in zip(self.cards, values):
            labels = card.findChildren(QLabel)
            if labels:
                labels[0].setText(str(value))

        reset_text = repo.last_stats_reset or "Not reset yet"
        self.period_label.setText(f"Current period: {repo.stats_month}  ·  Last reset: {reset_text}")

        clear_layout(self.usage_box)
        ordered = sorted(
            repo.all_items(),
            key=lambda item: (-repo.usage_counts.get(item.uid, 0), item.name.casefold()),
        )[:8]
        max_count = max([repo.usage_counts.get(item.uid, 0) for item in ordered] or [1])
        if not ordered:
            self.usage_box.addWidget(styled_label("No usage data yet.", "SectionMuted"))
        for rank, item in enumerate(ordered, start=1):
            count = repo.usage_counts.get(item.uid, 0)
            _, cat = repo.find_item_by_uid(item.uid)
            row = QHBoxLayout()
            row.setSpacing(10)
            row.setContentsMargins(0, 2, 0, 2)

            rank_label = QLabel(f"{rank:02d}")
            rank_label.setObjectName("SmallMuted")
            rank_label.setFixedWidth(24)
            rank_label.setAlignment(Qt.AlignCenter)

            info = QVBoxLayout()
            info.setSpacing(2)
            name_label = QLabel(item.name)
            name_label.setObjectName("SmallMuted")
            name_label.setStyleSheet("font-size: 13px; font-weight: 700;")
            name_label.setWordWrap(True)
            meta_text = display_category_name(cat.name) if cat else "Unknown category"
            if item.color:
                meta_text += f" · {item.color}"
            meta_label = QLabel(meta_text)
            meta_label.setObjectName("SectionMuted")
            meta_label.setStyleSheet("font-size: 10px;")
            meta_label.setWordWrap(True)
            info.addWidget(name_label)
            info.addWidget(meta_label)

            track = QFrame()
            track.setObjectName("UsageTrack")
            track.setMinimumWidth(80)
            tl = QHBoxLayout(track)
            tl.setContentsMargins(0, 0, 0, 0)
            fill = QFrame()
            fill.setObjectName("UsageFill")
            fill_width = max(8, int(120 * count / max_count)) if max_count else 8
            fill.setFixedWidth(fill_width)
            tl.addWidget(fill)
            tl.addStretch()

            value = QLabel(f"{count} wears")
            value.setObjectName("SmallMuted")
            value.setMinimumWidth(58)
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

            row.addWidget(rank_label)
            row.addLayout(info, 2)
            row.addWidget(track, 1)
            row.addWidget(value)
            self.usage_box.addLayout(row)

        clear_layout(self.category_box)
        max_cat = max([category.count for category in repo.categories] or [1])
        for category in repo.categories:
            row = QHBoxLayout()
            row.setSpacing(10)
            label = QLabel(display_category_name(category.name))
            label.setMinimumWidth(155)
            label.setObjectName("SmallMuted")
            track = QFrame()
            track.setObjectName("UsageTrack")
            tl = QHBoxLayout(track)
            tl.setContentsMargins(0, 0, 0, 0)
            fill = QFrame()
            fill.setObjectName("UsageFill")
            fill_width = max(1, int(100 * category.count / max_cat)) if max_cat else 1
            fill.setMinimumWidth(fill_width)
            tl.addWidget(fill)
            tl.addStretch()
            value = QLabel(str(category.count))
            value.setMinimumWidth(30)
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row.addWidget(label)
            row.addWidget(track, 1)
            row.addWidget(value)
            self.category_box.addLayout(row)


class UsageHistoryDialog(QDialog):
    def __init__(self, main_window):
        super().__init__(main_window); self.setWindowTitle("Wear History"); self.resize(620,500); box=QVBoxLayout(self); title=QLabel("Recent wear history"); title.setObjectName("TodayTitle"); box.addWidget(title); scroll=QScrollArea(); scroll.setWidgetResizable(True); body=QWidget(); lay=QVBoxLayout(body); rows=list(reversed(main_window.repository.usage_history[-100:]));
        if not rows: lay.addWidget(styled_label("No history yet.", "SectionMuted"))
        for row in rows:
            item,_=main_window.repository.find_item_by_uid(row["uid"])
            if item:
                line=QHBoxLayout(); line.addWidget(QLabel(item.label())); line.addWidget(styled_label(row["source"], "SmallMuted"),1); line.addWidget(styled_label(row["timestamp"], "SmallMuted")); lay.addLayout(line)
        lay.addStretch(); scroll.setWidget(body); box.addWidget(scroll); close_btn=make_button("Close","Primary"); close_btn.clicked.connect(self.accept); box.addWidget(close_btn,0,Qt.AlignRight)


class ClothingDetailDialog(QDialog):
    def __init__(self, main_window, item):
        super().__init__(main_window); self.main_window=main_window; self.item=item; self.setWindowTitle(item.name); self.resize(500,420); self.build_ui()
    def build_ui(self):
        self.setWindowTitle(self.item.name)
        self.resize(560, 560)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(10)

        title = QLabel(self.item.name)
        title.setObjectName("PageTitle")
        root.addWidget(title)
        root.addWidget(styled_label(f"Color: {self.item.color or '—'}", "ItemColor"))
        root.addWidget(styled_label(self.item.description or "No description.", "SectionMuted"))

        used = self.main_window.repository.usage_counts.get(self.item.uid, 0)
        root.addWidget(styled_label(f"Worn {used} times", "SectionMuted"))

        meta_card = QFrame()
        meta_card.setObjectName("FormCard")
        meta = QGridLayout(meta_card)
        meta.setContentsMargins(14, 14, 14, 14)
        meta.setHorizontalSpacing(16)
        meta.setVerticalSpacing(10)
        metadata_rows = [
            ("Season", self.item.season),
            ("Occasion", self.item.occasion),
            ("Style", self.item.style),
            ("Formality", f"{self.item.formality}/5"),
            ("Fit", self.item.fit),
            ("Availability", self.item.availability),
        ]
        for idx, (label, value) in enumerate(metadata_rows):
            row = (idx // 2) * 2
            col = idx % 2
            meta.addWidget(styled_label(label, "FormLabel"), row, col)
            value_label = styled_label(value or "—", "SectionMuted")
            value_label.setWordWrap(True)
            meta.addWidget(value_label, row + 1, col)
        meta.setColumnStretch(0, 1)
        meta.setColumnStretch(1, 1)
        root.addWidget(meta_card)

        actions = QHBoxLayout()
        fav = make_button("Remove favorite" if self.item.uid in self.main_window.repository.favorites else "Add favorite")
        fav.clicked.connect(lambda: self.toggle_favorite(fav))
        builder = make_button("Add to builder")
        builder.clicked.connect(self.add_to_builder)
        use = make_button("Mark as worn", "Primary")
        use.clicked.connect(self.use_item)
        edit = make_button("Edit")
        edit.clicked.connect(self.edit_item)
        availability = make_button("Change availability")
        availability.clicked.connect(self.change_availability)
        actions.addWidget(fav)
        actions.addWidget(builder)
        actions.addWidget(use)
        actions.addWidget(edit)
        actions.addWidget(availability)
        root.addLayout(actions)

        close = make_button("Close")
        close.clicked.connect(self.accept)
        root.addWidget(close, 0, Qt.AlignRight)
    def toggle_favorite(self, button):
        self.main_window.toggle_favorite(self.item.uid); button.setText("Remove favorite" if self.item.uid in self.main_window.repository.favorites else "Add favorite")
    def use_item(self): self.main_window.mark_item_used(self.item.uid); self.accept()
    def edit_item(self): self.accept(); self.main_window.edit_item(self.item)
    def add_to_builder(self): self.accept(); self.main_window.show_builder_with_item(self.item)
    def change_availability(self):
        current = self.main_window.repository.find_item_by_uid(self.item.uid)[0]
        if not current: return
        value, ok = QInputDialog.getItem(self, "Availability", "Item availability:", AVAILABILITY_OPTIONS, AVAILABILITY_OPTIONS.index(current.availability) if current.availability in AVAILABILITY_OPTIONS else 0, False)
        if not ok: return
        try:
            self.main_window.repository.set_item_availability(self.item.uid, value)
            self.item.availability = value
            self.accept()
            self.main_window.refresh_visible_page()
            self.main_window.notify("Availability updated.")
        except Exception as e:
            QMessageBox.critical(self, "Action failed", str(e))


class ClothingFormPage(QWidget):
    def __init__(self, main_window):
        super().__init__(); self.main_window=main_window; self.editing=None; self.build_ui()
    def build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 4, 8, 8)
        title = QLabel("Add Clothing")
        title.setObjectName("PageTitle")
        self.title = title
        root.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 10, 12)
        content_layout.setSpacing(12)

        basic_card = QFrame()
        basic_card.setObjectName("FormCard")
        basic = QVBoxLayout(basic_card)
        basic.setContentsMargins(20, 18, 20, 18)
        basic.setSpacing(8)
        basic.addWidget(styled_label("Basic information", "SectionTitle"))

        self.category = QComboBox()
        self.category.setObjectName("Input")
        self.category.setEditable(True)
        self.category.setInsertPolicy(QComboBox.NoInsert)
        self.category.setPlaceholderText("Select or type a category...")
        if self.category.lineEdit():
            self.category.lineEdit().setPlaceholderText("Select or type a category...")
            self.category.lineEdit().setClearButtonEnabled(True)
        self.category.setMinimumHeight(44)
        self.name = QLineEdit()
        self.name.setObjectName("Input")
        self.name.setPlaceholderText("e.g. Oxford Shirt")
        self.color = QLineEdit()
        self.color.setObjectName("Input")
        self.color.setPlaceholderText("e.g. White")
        self.description = QTextEdit()
        self.description.setObjectName("Input")
        self.description.setPlaceholderText("Material, details, fit notes, or anything useful...")
        self.description.setFixedHeight(88)

        basic_grid = QGridLayout()
        basic_grid.setHorizontalSpacing(14)
        basic_grid.setVerticalSpacing(8)
        basic_grid.addWidget(styled_label("Category", "FormLabel"), 0, 0)
        basic_grid.addWidget(self.category, 1, 0)
        basic_grid.addWidget(styled_label("Name", "FormLabel"), 0, 1)
        basic_grid.addWidget(self.name, 1, 1)
        basic_grid.addWidget(styled_label("Color", "FormLabel"), 2, 0)
        basic_grid.addWidget(self.color, 3, 0)
        basic_grid.addWidget(styled_label("Description", "FormLabel"), 2, 1)
        basic_grid.addWidget(self.description, 3, 1)
        basic_grid.setColumnStretch(0, 1)
        basic_grid.setColumnStretch(1, 1)
        basic.addLayout(basic_grid)
        content_layout.addWidget(basic_card)

        meta_card = QFrame()
        meta_card.setObjectName("FormCard")
        meta = QVBoxLayout(meta_card)
        meta.setContentsMargins(20, 18, 20, 18)
        meta.setSpacing(8)
        meta.addWidget(styled_label("Style & recommendation metadata", "SectionTitle"))
        meta.addWidget(styled_label("These fields help filtering and future AI outfit recommendations.", "SectionMuted"))

        self.season = QLineEdit()
        self.season.setObjectName("Input")
        self.season.setPlaceholderText("Spring, Summer, Autumn, Winter")
        self.occasion = QLineEdit()
        self.occasion.setObjectName("Input")
        self.occasion.setPlaceholderText("Work, Casual, Dinner, Formal, Weekend, Travel")
        self.style = QLineEdit()
        self.style.setObjectName("Input")
        self.style.setPlaceholderText("Classic, Smart Casual, Minimal, Relaxed, Preppy, Elegant")
        self.formality = QComboBox()
        self.formality.setObjectName("Input")
        self.formality.addItems(["1 - Very casual", "2", "3 - Smart casual", "4", "5 - Formal"])
        self.fit = QComboBox()
        self.fit.setObjectName("Input")
        self.fit.addItems(FIT_OPTIONS)
        self.availability = QComboBox()
        self.availability.setObjectName("Input")
        self.availability.addItems(AVAILABILITY_OPTIONS)

        meta_grid = QGridLayout()
        meta_grid.setHorizontalSpacing(14)
        meta_grid.setVerticalSpacing(8)
        fields = [
            ("Season(s)", self.season, 0, 0),
            ("Occasion(s)", self.occasion, 0, 1),
            ("Style(s)", self.style, 2, 0),
            ("Formality", self.formality, 2, 1),
            ("Fit", self.fit, 4, 0),
            ("Availability", self.availability, 4, 1),
        ]
        for label, widget, row, col in fields:
            meta_grid.addWidget(styled_label(label, "FormLabel"), row, col)
            meta_grid.addWidget(widget, row + 1, col)
        meta_grid.setColumnStretch(0, 1)
        meta_grid.setColumnStretch(1, 1)
        meta.addLayout(meta_grid)
        content_layout.addWidget(meta_card)

        actions_card = QFrame()
        actions_card.setObjectName("FormCard")
        actions = QHBoxLayout(actions_card)
        actions.setContentsMargins(20, 14, 20, 14)
        cancel = make_button("Cancel")
        save = make_button("Save", "Primary")
        cancel.clicked.connect(self.main_window.go_back_from_form)
        save.clicked.connect(self.save)
        actions.addWidget(cancel)
        actions.addStretch()
        actions.addWidget(save)
        content_layout.addWidget(actions_card)
        content_layout.addStretch()

        scroll.setWidget(content)
        root.addWidget(scroll, 1)
    def prepare(self, editing=None):
        self.editing=editing; self.category.blockSignals(True); self.category.clear();
        for c in self.main_window.repository.categories: self.category.addItem(display_category_name(c.name), c.name)
        self.category.blockSignals(False)
        if editing:
            self.title.setText("Edit Clothing"); self.name.setText(editing.name); self.color.setText(editing.color); self.description.setPlainText(editing.description); self.season.setText(editing.season); self.occasion.setText(editing.occasion); self.style.setText(editing.style); self.formality.setCurrentIndex(max(0, min(4, editing.formality - 1))); self.fit.setCurrentText(editing.fit if editing.fit in FIT_OPTIONS else "Unknown"); self.availability.setCurrentText(editing.availability if editing.availability in AVAILABILITY_OPTIONS else "Available"); _,c=self.main_window.repository.find_item_by_uid(editing.uid);
            if c:
                idx = self.category.findData(c.name)
                self.category.setCurrentIndex(idx if idx >= 0 else 0)
        else:
            self.title.setText("Add Clothing"); self.name.clear(); self.color.clear(); self.description.clear(); self.category.setCurrentText(""); self.season.clear(); self.occasion.clear(); self.style.clear(); self.formality.setCurrentIndex(2); self.fit.setCurrentText("Unknown"); self.availability.setCurrentText("Available")
    def save(self):
        category_text=self.category.currentText().strip(); category=self.main_window.repository.resolve_category_name(category_text); name=self.name.text().strip(); color=self.color.text().strip(); desc=self.description.toPlainText().strip().replace("\n"," ")
        if not category:
            QMessageBox.warning(self,"Missing information","Category name cannot be empty.")
            return
        if not name: QMessageBox.warning(self,"Missing information","Clothing name cannot be empty."); return
        metadata={
            "season": self.season.text().strip(),
            "occasion": self.occasion.text().strip(),
            "style": self.style.text().strip(),
            "formality": self.formality.currentIndex()+1,
            "fit": self.fit.currentText(),
            "availability": self.availability.currentText(),
        }
        defaults=infer_metadata(category,name,color,desc)
        for key in ("season","occasion","style"):
            if not metadata[key]: metadata[key]=defaults[key]
        if metadata["fit"] == "Unknown" and defaults["fit"] != "Unknown": metadata["fit"]=defaults["fit"]
        try:
            if self.editing: self.main_window.repository.update(self.editing.number,category,name,color,desc,metadata); msg="Clothing updated."
            else: self.main_window.repository.add(category,name,color,desc,metadata); msg="Clothing added."
        except Exception as e: QMessageBox.critical(self,"Action failed",str(e)); return
        self.main_window.notify(msg); self.main_window.show_home()


class SettingsPage(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.build_ui()

    def _card(self, title, subtitle=None):
        card = QFrame()
        card.setObjectName("FormCard")
        box = QVBoxLayout(card)
        box.setContentsMargins(20, 18, 20, 18)
        box.setSpacing(12)
        box.addWidget(styled_label(title, "SectionTitle"))
        if subtitle:
            hint = styled_label(subtitle, "SectionMuted")
            hint.setWordWrap(True)
            box.addWidget(hint)
        return card, box

    def _add_setting_row(self, layout, label_text, control):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)
        label = styled_label(label_text, "FormLabel")
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        row.addWidget(label, 1)
        row.addWidget(control, 0, Qt.AlignRight | Qt.AlignVCenter)
        layout.addLayout(row)
        return row

    def build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 4, 8, 8)
        root.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Settings")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch()
        root.addLayout(header)

        intro = styled_label("Manage appearance, behavior, data safety, statistics, and updates.", "SectionMuted")
        intro.setWordWrap(True)
        root.addWidget(intro)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(2, 2, 8, 18)
        body_layout.setSpacing(14)

        # Appearance
        appearance, a = self._card("Appearance", "Choose how WARDROBE looks on this computer.")
        self.dark = QCheckBox("Dark mode")
        self.dark.setChecked(self.main_window.dark_mode)
        self.dark.toggled.connect(self.main_window.set_dark_mode)
        self.dark.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._add_setting_row(a, "Theme", self.dark)
        body_layout.addWidget(appearance)

        # Behavior
        behavior, b = self._card("Behavior", "Control confirmations and monthly statistics behavior.")
        self.confirm_delete = QCheckBox("Ask before deleting clothing")
        self.confirm_delete.setChecked(self.main_window.confirm_delete)
        self.confirm_delete.toggled.connect(self.main_window.set_confirm_delete)
        self.auto_monthly = QCheckBox("Automatically reset wear statistics at the start of each month")
        self.auto_monthly.setChecked(self.main_window.auto_monthly_reset)
        self.auto_monthly.toggled.connect(self.main_window.set_auto_monthly_reset)
        self._add_setting_row(b, "Delete confirmation", self.confirm_delete)
        self._add_setting_row(b, "Monthly reset", self.auto_monthly)
        body_layout.addWidget(behavior)

        # Categories
        categories_card, cg = self._card("Categories", "Manage your wardrobe categories. Removing a category also removes its clothing items and dependent outfit entries after confirmation.")
        self.category_manager_layout = QVBoxLayout()
        self.category_manager_layout.setSpacing(7)
        cg.addLayout(self.category_manager_layout)
        body_layout.addWidget(categories_card)

        # Wear statistics
        statistics, st = self._card("Wear statistics", "Statistics can be reset without deleting clothing, favorites, or saved outfits.")
        self.stats_status = styled_label("", "SectionMuted")
        self.stats_status.setWordWrap(True)
        st.addWidget(self.stats_status)
        stats_buttons = QHBoxLayout()
        reset_stats = make_button("Reset statistics", "Delete")
        reset_stats.clicked.connect(self.reset_statistics)
        stats_buttons.addWidget(reset_stats)
        stats_buttons.addStretch()
        st.addLayout(stats_buttons)
        body_layout.addWidget(statistics)

        # Data safety
        data, d = self._card("Data safety", "Your wardrobe is stored locally in SQLite. Backups are verified before they can be restored.")
        storage = styled_label(f"Database: {DB_FILE}", "SectionMuted")
        storage.setWordWrap(True)
        d.addWidget(storage)
        self.backup_status = styled_label("", "SectionMuted")
        self.backup_status.setWordWrap(True)
        d.addWidget(self.backup_status)
        d.addWidget(styled_label("Automatic full backups run at most once every 24 hours. The latest 10 verified backups are kept.", "SectionMuted"))
        d.addWidget(styled_label("The original TXT file is kept only as a legacy import source.", "SectionMuted"))
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        backup = make_button("Backup now", "Primary")
        backup.clicked.connect(self.backup_now)
        restore = make_button("Restore backup")
        restore.clicked.connect(self.restore_latest)
        open_folder = make_button("Open backup folder")
        open_folder.clicked.connect(self.open_folder)
        buttons.addWidget(backup)
        buttons.addWidget(restore)
        buttons.addWidget(open_folder)
        buttons.addStretch()
        d.addLayout(buttons)
        body_layout.addWidget(data)

        # Updates
        updates, up = self._card("Updates", "Check GitHub Releases for a newer WARDROBE version.")
        up.addWidget(styled_label(f"Current version: {APP_VERSION}", "FormLabel"))
        self.update_status = styled_label("Check manually for a newer WARDROBE release.", "SectionMuted")
        self.update_status.setWordWrap(True)
        up.addWidget(self.update_status)
        update_buttons = QHBoxLayout()
        check_updates = make_button("Check for updates", "Primary")
        check_updates.clicked.connect(self.check_updates)
        update_buttons.addWidget(check_updates)
        update_buttons.addStretch()
        up.addLayout(update_buttons)
        body_layout.addWidget(updates)

        # About
        about, ab = self._card("About")
        about_text = styled_label(
            f"WARDROBE · Desktop Wardrobe Manager\nVersion {APP_VERSION}\nSQLite storage · English interface · Local data only",
            "SectionMuted",
        )
        about_text.setWordWrap(True)
        ab.addWidget(about_text)
        body_layout.addWidget(about)

        body_layout.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

    def check_updates(self):
        self.main_window.check_for_updates()

    def backup_now(self):
        try:
            path = self.main_window.repository.backup_now("manual")
            self.refresh()
            self.main_window.notify(f"Backup created: {path.name}")
        except Exception as e:
            QMessageBox.critical(self, "Backup failed", str(e))

    def restore_latest(self):
        backups = self.main_window.repository.available_backups()
        if not backups:
            QMessageBox.information(self, "No backups", "No verified database backups were found.")
            return
        labels = [f"{p.name}  ·  {datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d %H:%M}" for p in backups]
        selected, ok = QInputDialog.getItem(self, "Restore backup", "Select a verified backup:", labels, 0, False)
        if not ok:
            return
        path = backups[labels.index(selected)]
        answer = QMessageBox.question(
            self,
            "Restore backup",
            f"Restore {path.name}? Your current data will be kept in a separate pre-restore backup.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self.main_window.repository.restore_backup(path)
            self.main_window.refresh_visible_page()
            self.refresh()
            self.main_window.notify(f"Backup restored: {path.name}")
        except Exception as e:
            QMessageBox.critical(self, "Restore failed", str(e))

    def reset_statistics(self):
        answer = QMessageBox.question(
            self,
            "Reset statistics",
            "Reset all wear counts, wear history, and outfit usage counts? Your clothing, favorites, and saved outfits will stay.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self.main_window.repository.reset_statistics("Reset statistics")
            self.main_window.refresh_visible_page()
            self.refresh()
            self.main_window.notify("Wear statistics reset.")
        except Exception as e:
            QMessageBox.critical(self, "Reset failed", str(e))

    def refresh(self):
        repo = self.main_window.repository
        last = repo.last_stats_reset or "Never"
        self.stats_status.setText(
            f"Last reset: {last}. Monthly reset is {'on' if self.main_window.auto_monthly_reset else 'off'}. "
            "Previous months are archived as SQLite snapshots in the backups folder."
        )
        latest = repo.latest_backup()
        latest_text = latest.name if latest else "No backup yet"
        self.backup_status.setText(
            f"Backups: {repo.backup_count()} managed copies. Latest verified backup: {latest_text}"
        )
        self.dark.blockSignals(True)
        self.dark.setChecked(self.main_window.dark_mode)
        self.dark.blockSignals(False)
        self.confirm_delete.blockSignals(True)
        self.confirm_delete.setChecked(self.main_window.confirm_delete)
        self.confirm_delete.blockSignals(False)
        self.auto_monthly.blockSignals(True)
        self.auto_monthly.setChecked(self.main_window.auto_monthly_reset)
        self.auto_monthly.blockSignals(False)
        self.refresh_categories()

    def refresh_categories(self):
        clear_layout(self.category_manager_layout)
        categories = sorted(self.main_window.repository.categories, key=lambda c: display_category_name(c.name).casefold())
        if not categories:
            self.category_manager_layout.addWidget(styled_label("No categories yet.", "SectionMuted"))
            return
        for category in categories:
            row = QHBoxLayout()
            row.setSpacing(10)
            label = QLabel(display_category_name(category.name))
            label.setObjectName("FeatureTitle")
            count = styled_label(f"{category.count} item" + ("" if category.count == 1 else "s"), "SectionMuted")
            info = QVBoxLayout()
            info.setSpacing(1)
            info.addWidget(label)
            info.addWidget(count)
            row.addLayout(info, 1)
            delete = make_button("Delete", "Delete")
            delete.setToolTip("Delete this category and its clothing items")
            delete.clicked.connect(lambda _, name=category.name: self.delete_category(name))
            row.addWidget(delete, 0, Qt.AlignVCenter)
            self.category_manager_layout.addLayout(row)

    def delete_category(self, category_name):
        category = self.main_window.repository.get_category(category_name)
        if category is None:
            self.refresh_categories()
            return
        shown = display_category_name(category.name)
        count = category.count
        details = (
            f"Delete {shown}?\n\n"
            f"This will permanently remove {count} clothing item{'' if count == 1 else 's'}, "
            "related wear history, favorites, and any saved-outfit entries that depend on them."
        )
        answer = QMessageBox.question(self, "Delete category", details, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        try:
            deleted_name, removed_count = self.main_window.repository.delete_category(category.name)
        except Exception as e:
            QMessageBox.critical(self, "Delete category failed", str(e))
            return
        self.main_window.refresh_visible_page()
        self.refresh()
        self.main_window.notify(f"Deleted {display_category_name(deleted_name)} and {removed_count} item" + ("." if removed_count == 1 else "s."))

    def open_folder(self):
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(BACKUP_DIR)))


def empty_state(title, text):
    frame=QFrame(); frame.setObjectName("FormCard"); box=QVBoxLayout(frame); box.setContentsMargins(24,24,24,24); box.addWidget(styled_label(title, "EmptyStateTitle")); box.addWidget(styled_label(text, "SectionMuted")); return frame


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("WARDROBE")
        self._fit_initial_window_to_screen()
        if APP_ICON_FILE.exists(): self.setWindowIcon(QIcon(str(APP_ICON_FILE)))
        self.repository=WardrobeRepository(DB_FILE, LEGACY_DATA_FILE); self.repository.on_internal_save=self._on_internal_save; self.settings=QSettings("WARDROBE","Wardrobe"); self.dark_mode=self.settings.value("dark_mode",False,type=bool); self.confirm_delete=self.settings.value("confirm_delete",True,type=bool); self.auto_monthly_reset=self.settings.value("auto_monthly_reset",True,type=bool); self.current_category=None; self.previous_page=None; self._loading_external=False; self._ignore_watcher_until=0.0; self._refreshing_visible_page=False; self.build_ui(); self.apply_theme(); self.setup_shortcuts(); self.month_check_timer=QTimer(self); self.month_check_timer.setInterval(60 * 60 * 1000); self.month_check_timer.timeout.connect(self.check_monthly_statistics); self.month_check_timer.start(); self.check_monthly_statistics(); self.show_home()

    def _fit_initial_window_to_screen(self):
        """Choose a usable initial size for the current Windows display/DPI."""
        screen = QApplication.primaryScreen()
        if screen is None:
            self.setMinimumSize(900, 600)
            self.resize(1180, 760)
            return

        available = screen.availableGeometry()
        width = max(760, int(available.width() * 0.92))
        height = max(520, int(available.height() * 0.92))
        width = min(1280, width)
        height = min(860, height)

        min_width = min(1000, width)
        min_height = min(680, height)
        self.setMinimumSize(min_width, min_height)
        self.resize(max(min_width, width), max(min_height, height))

        # Center the window on the usable desktop area instead of leaving it
        # partially outside the screen after Windows DPI scaling changes.
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(frame.topLeft())

    def build_ui(self):
        central=QWidget(); outer=QHBoxLayout(central); outer.setContentsMargins(0,0,0,0); outer.setSpacing(0)
        sidebar=QFrame(); sidebar.setObjectName("Sidebar"); sidebar.setFixedWidth(250); side=QVBoxLayout(sidebar); side.setContentsMargins(18,24,18,18); side.setSpacing(7)
        br=QHBoxLayout(); br.addWidget(styled_label("WARDROBE", "Brand")); br.addStretch(); side.addLayout(br); side.addSpacing(22); side.addWidget(styled_label("MENU", "NavSection"))
        self.home_button=self.nav_button("Home", "home"); self.items_button=self.nav_button("All Clothing", "clothing"); self.unused_button=self.nav_button("Unused", "unused"); self.builder_button=self.nav_button("Outfit Builder", "builder"); self.outfits_button=self.nav_button("Saved Outfits", "saved"); self.today_button=self.nav_button("What should I wear?", "today"); self.stats_button=self.nav_button("Wear Statistics", "stats"); self.settings_button=self.nav_button("Settings", "settings"); self.add_button=self.nav_button("Add Clothing", "add")
        for button, callback in ((self.home_button,self.show_home),(self.items_button,self.show_all_items),(self.unused_button,self.show_unused),(self.builder_button,self.show_builder),(self.outfits_button,self.show_saved_outfits),(self.today_button,self.show_today),(self.stats_button,self.show_stats),(self.add_button,self.show_add)):
            button.clicked.connect(callback); side.addWidget(button)
        side.addStretch(); self.settings_button.clicked.connect(self.show_settings); side.addWidget(self.settings_button); outer.addWidget(sidebar)
        content=QWidget(); cl=QVBoxLayout(content); cl.setContentsMargins(28,22,30,25); cl.setSpacing(14); top=QHBoxLayout(); self.breadcrumb=styled_label("Home", "Subtitle"); top.addWidget(self.breadcrumb); top.addStretch(); self.undo_button=make_button("Undo","Secondary"); self.undo_button.clicked.connect(self.undo_last); self.undo_button.setEnabled(False); top.addWidget(self.undo_button); self.global_search=QLineEdit(); self.global_search.setObjectName("GlobalSearch"); self.global_search.setPlaceholderText("Search wardrobe... (Ctrl+K)"); self.global_search.setFixedWidth(330); self.global_search.textChanged.connect(self.global_search_changed); top.addWidget(self.global_search); cl.addLayout(top)
        self.stack=QStackedWidget(); cl.addWidget(self.stack,1); outer.addWidget(content,1); self.setCentralWidget(central); self.statusBar().hide()
        self.home_page=HomePage(self); self.items_page=AllItemsPage(self); self.unused_page=UnusedPage(self); self.category_page=CategoryPage(self); self.builder_page=OutfitBuilderPage(self); self.outfits_page=SavedOutfitsPage(self); self.today_page=TodayPage(self); self.stats_page=StatsPage(self); self.form_page=ClothingFormPage(self); self.settings_page=SettingsPage(self)
        for page in (self.home_page,self.items_page,self.unused_page,self.category_page,self.builder_page,self.outfits_page,self.today_page,self.stats_page,self.form_page,self.settings_page): self.stack.addWidget(page)
    @staticmethod
    def nav_button(text, icon_name=None):
        button=QPushButton(text); button.setObjectName("NavButton"); button.setCursor(Qt.PointingHandCursor)
        button.setProperty("icon_name", icon_name or "")
        button.setIconSize(QSize(20, 20))
        return button

    def _tinted_nav_icon(self, icon_name, color):
        if not icon_name:
            return QIcon()
        icon_path = ICON_DIR / f"{icon_name}.png"
        if not icon_path.exists():
            return QIcon()
        source = QPixmap(str(icon_path))
        if source.isNull():
            return QIcon()
        pixmap = source.scaled(QSize(48, 48), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        painter = QPainter(pixmap)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), QColor(color))
        painter.end()
        return QIcon(pixmap)

    def _refresh_nav_icons(self):
        normal_color = DARK_COLORS["nav_text"] if self.dark_mode else LIGHT_COLORS["nav_text"]
        active_color = DARK_COLORS["accent_dark"] if self.dark_mode else LIGHT_COLORS["accent_dark"]
        buttons = (self.home_button,self.items_button,self.unused_button,self.builder_button,self.outfits_button,self.today_button,self.stats_button,self.settings_button,self.add_button)
        for button in buttons:
            icon_name = button.property("icon_name") or ""
            color = active_color if button.objectName() == "NavButtonActive" else normal_color
            button.setIcon(self._tinted_nav_icon(icon_name, color))
            button.setIconSize(QSize(20, 20))

    def apply_theme(self):
        QApplication.instance().setStyleSheet(make_stylesheet(DARK_COLORS if self.dark_mode else LIGHT_COLORS))
        if hasattr(self, "home_button"):
            self._refresh_nav_icons()
    def set_dark_mode(self, enabled):
        self.dark_mode=bool(enabled); self.settings.setValue("dark_mode",self.dark_mode); self.apply_theme(); self.settings_page.dark.blockSignals(True); self.settings_page.dark.setChecked(self.dark_mode); self.settings_page.dark.blockSignals(False)
    def set_confirm_delete(self, enabled): self.confirm_delete=bool(enabled); self.settings.setValue("confirm_delete",self.confirm_delete)
    def set_auto_monthly_reset(self, enabled):
        self.auto_monthly_reset=bool(enabled); self.settings.setValue("auto_monthly_reset",self.auto_monthly_reset); self.settings_page.refresh()
        if self.auto_monthly_reset: self.check_monthly_statistics()
    def check_monthly_statistics(self):
        try:
            if self.repository.ensure_monthly_statistics(self.auto_monthly_reset):
                self.refresh_visible_page(); self.notify("A new month started. Wear statistics were reset automatically.")
        except Exception as e:
            self.notify(f"Monthly statistics check failed: {e}")
    def setup_shortcuts(self):
        for seq, cb in (("Ctrl+N",self.show_add),("Ctrl+K",self.focus_global_search),("Ctrl+F",self.focus_current_search),("Escape",self.clear_searches)):
            QShortcut(QKeySequence(seq),self).activated.connect(cb)
    def focus_global_search(self): self.global_search.setFocus(); self.global_search.selectAll()
    def focus_current_search(self):
        if not self.global_search.isVisible():
            return
        self.global_search.setFocus(); self.global_search.selectAll()
    def clear_searches(self):
        self.global_search.clear()
    def _on_internal_save(self):
        # Repository save callback: update Undo state without rebuilding the current page.
        if hasattr(self, "undo_button"):
            self.undo_button.setEnabled(self.repository._last_snapshot is not None)

    def check_for_updates(self, silent=False):
        if not UPDATE_REPOSITORY or "/" not in UPDATE_REPOSITORY:
            message = "Update checking is not configured yet. Set github_repository in update_config.json to your GitHub repository (owner/repository) before publishing releases."
            self.settings_page.update_status.setText(message)
            if not silent:
                QMessageBox.information(self, "Updates", message)
            return False
        url = f"https://api.github.com/repos/{UPDATE_REPOSITORY}/releases/latest"
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": f"WARDROBE/{APP_VERSION}",
                "Accept": "application/vnd.github+json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=6) as response:
                payload = json.load(response)
            tag = payload.get("tag_name") or payload.get("name") or ""
            release_url = payload.get("html_url") or f"https://github.com/{UPDATE_REPOSITORY}/releases"
            if parse_version(tag) > parse_version(APP_VERSION):
                notes = format_release_notes(payload.get("body", ""))
                self.settings_page.update_status.setText(f"Update available: {tag}")
                box = QMessageBox(self)
                box.setWindowTitle("Update available")
                box.setIcon(QMessageBox.Information)
                box.setText(f"WARDROBE {tag} is available.")
                box.setInformativeText(f"You are using {APP_VERSION}.\\n\\nRelease notes:\\n{notes}")
                installer_asset = None
                checksum_asset = None
                for asset in payload.get("assets", []) or []:
                    name = str(asset.get("name", ""))
                    lowered = name.lower()
                    if lowered.endswith(".exe") and "wardrobe-setup" in lowered:
                        installer_asset = asset
                    elif lowered.endswith(".sha256") and "wardrobe-setup" in lowered:
                        checksum_asset = asset
                download_btn = None
                if installer_asset and installer_asset.get("browser_download_url"):
                    download_btn = box.addButton("Download & install", QMessageBox.AcceptRole)
                open_btn = box.addButton("Open release page", QMessageBox.ActionRole)
                box.addButton("Later", QMessageBox.RejectRole)
                box.exec()
                if download_btn is not None and box.clickedButton() is download_btn:
                    self._download_and_install_update(installer_asset, checksum_asset, tag)
                elif box.clickedButton() is open_btn:
                    QDesktopServices.openUrl(QUrl(release_url))
                return True
            self.settings_page.update_status.setText(f"You're up to date. Current version: {APP_VERSION}")
            if not silent:
                QMessageBox.information(self, "Updates", f"You're up to date. WARDROBE {APP_VERSION} is the latest configured release.")
            return False
        except urllib.error.HTTPError as e:
            message = f"Update check failed (HTTP {e.code}). Please try again later."
        except urllib.error.URLError:
            message = "Update check failed. Please check your internet connection."
        except Exception as e:
            message = f"Update check failed: {e}"
        self.settings_page.update_status.setText(message)
        if not silent:
            QMessageBox.warning(self, "Update check failed", message)
        return False

    def _download_and_install_update(self, asset, checksum_asset, tag):
        """Download the signed/released installer to a temp folder and launch it after WARDROBE exits."""
        installer_url = str(asset.get("browser_download_url") or "").strip()
        installer_name = Path(str(asset.get("name") or "WARDROBE-Setup.exe")).name
        if not installer_url or not installer_name.lower().endswith(".exe"):
            QMessageBox.warning(self, "Update", "The release does not contain a valid Windows installer.")
            return
        try:
            backup_path = self.repository.backup_now("pre_update")
            self.settings_page.update_status.setText(
                f"Preparing update {tag}. A safety backup was created: {backup_path.name}"
            )
            update_dir = Path(tempfile.gettempdir()) / "WARDROBE-update"
            update_dir.mkdir(parents=True, exist_ok=True)
            target = update_dir / installer_name
            request = urllib.request.Request(
                installer_url,
                headers={"User-Agent": f"WARDROBE/{APP_VERSION}", "Accept": "application/octet-stream"},
            )
            with urllib.request.urlopen(request, timeout=30) as response, target.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
            if not target.exists() or target.stat().st_size < 1_000_000:
                raise RuntimeError("The downloaded installer appears incomplete.")

            if checksum_asset and checksum_asset.get("browser_download_url"):
                checksum_request = urllib.request.Request(
                    str(checksum_asset["browser_download_url"]),
                    headers={"User-Agent": f"WARDROBE/{APP_VERSION}", "Accept": "text/plain"},
                )
                with urllib.request.urlopen(checksum_request, timeout=15) as response:
                    expected_text = response.read().decode("utf-8", errors="replace")
                expected = re.search(r"\b[a-fA-F0-9]{64}\b", expected_text)
                if expected:
                    digest = hashlib.sha256(target.read_bytes()).hexdigest()
                    if digest.lower() != expected.group(0).lower():
                        target.unlink(missing_ok=True)
                        raise RuntimeError("The downloaded installer failed SHA-256 verification.")

            updater_bat = update_dir / "apply_update.bat"
            pid = os.getpid()
            updater_bat.write_text(
                '@echo off\n'
                'set "TARGET_PID=%~1"\n'
                'set "INSTALLER=%~2"\n'
                ':wait\n'
                'tasklist /FI "PID eq %TARGET_PID%" | find /I "%TARGET_PID%" >nul\n'
                'if not errorlevel 1 (timeout /t 1 /nobreak >nul & goto wait)\n'
                'start "" "%INSTALLER%"\n'
                'del /f /q "%~f0" >nul 2>&1\n',
                encoding="utf-8",
            )
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(
                ["cmd.exe", "/c", str(updater_bat), str(pid), str(target)],
                close_fds=True,
                creationflags=creationflags,
            )
            self.notify(f"WARDROBE {tag} downloaded. Closing to install the update…")
            QTimer.singleShot(300, QApplication.instance().quit)
        except Exception as exc:
            self.settings_page.update_status.setText(f"Update failed: {exc}")
            QMessageBox.critical(self, "Update failed", str(exc))

    def notify(self,msg): self.statusBar().showMessage(msg,3500)
    def closeEvent(self, event):
        try:
            self.repository.close()
        finally:
            super().closeEvent(event)

    def set_active(self,active):
        for button in (self.home_button,self.items_button,self.unused_button,self.builder_button,self.outfits_button,self.today_button,self.stats_button,self.settings_button,self.add_button):
            button.setObjectName("NavButtonActive" if button is active else "NavButton"); button.style().unpolish(button); button.style().polish(button); button.update()
        self._refresh_nav_icons()
    def setup_watcher(self):
        # SQLite is the authoritative local data source; no filesystem watcher is needed.
        return None
    def file_mtime(self):
        try: return self.repository.db_path.stat().st_mtime_ns
        except OSError: return 0
    def watch_file(self): return None
    def _on_file_changed(self, _): return None
    def reload_external(self): return None
    def refresh_visible_page(self):
        if getattr(self, "_refreshing_visible_page", False):
            return
        self._refreshing_visible_page = True
        try:
            page=self.stack.currentWidget()
            if page is self.home_page: self.home_page.refresh()
            elif page is self.items_page: self.items_page.refresh()
            elif page is self.unused_page: self.unused_page.refresh()
            elif page is self.category_page: self.category_page.render_current()
            elif page is self.builder_page: self.builder_page.refresh()
            elif page is self.outfits_page: self.outfits_page.refresh()
            elif page is self.today_page: self.today_page.update_from_repository()
            elif page is self.stats_page: self.stats_page.refresh()
            elif page is self.settings_page: self.settings_page.refresh()
            self.undo_button.setEnabled(self.repository._last_snapshot is not None)
        finally:
            self._refreshing_visible_page = False
    def set_search_context(self, enabled, placeholder="Search wardrobe... (Ctrl+K)"):
        # Search is intentionally available only on pages where it is useful:
        # All Clothing, Unused Clothing, and individual Category pages.
        self.global_search.blockSignals(True)
        self.global_search.clear()
        self.global_search.setPlaceholderText(placeholder)
        self.global_search.setVisible(enabled)
        self.global_search.blockSignals(False)

    def set_global_search_visible(self, visible):
        # Backward-compatible helper for older call sites.
        self.set_search_context(visible)

    def show_home(self):
        self.set_search_context(False)
        self.stack.setCurrentWidget(self.home_page); self.breadcrumb.setText("Home"); self.set_active(self.home_button); self.home_page.refresh()
    def show_all_items(self):
        self.set_search_context(True, "Search wardrobe... (Ctrl+K)")
        self.previous_page=self.stack.currentWidget(); self.stack.setCurrentWidget(self.items_page); self.breadcrumb.setText("All Clothing"); self.set_active(self.items_button); self.items_page.refresh()
    def show_unused(self):
        self.set_search_context(True, "Search unused clothing... (Ctrl+K)")
        self.previous_page=self.stack.currentWidget(); self.stack.setCurrentWidget(self.unused_page); self.breadcrumb.setText("Unused Clothing"); self.set_active(self.unused_button); self.unused_page.refresh()
    def open_category(self,category):
        self.current_category = category.name
        self.stack.setCurrentWidget(self.category_page)
        self.breadcrumb.setText(f"Home / {display_category_name(category.name)}")
        self.set_active(None)
        self.set_search_context(True, f"Search {display_category_name(category.name).lower()}...")
        self.category_page.show_category(category)
    def show_builder(self):
        self.set_search_context(False)
        self.previous_page=self.stack.currentWidget(); self.stack.setCurrentWidget(self.builder_page); self.breadcrumb.setText("Outfit Builder"); self.set_active(self.builder_button); self.builder_page.refresh()
    def show_builder_with_item(self,item): self.show_builder(); self.builder_page.set_item(item)
    def show_saved_outfits(self):
        self.set_search_context(False)
        self.stack.setCurrentWidget(self.outfits_page); self.breadcrumb.setText("Saved Outfits"); self.set_active(self.outfits_button); self.outfits_page.refresh()
    def show_today(self):
        self.set_search_context(False)
        self.stack.setCurrentWidget(self.today_page); self.breadcrumb.setText("What should I wear today?"); self.set_active(self.today_button); self.today_page.generate()
    def show_stats(self):
        self.set_search_context(False)
        self.stack.setCurrentWidget(self.stats_page); self.breadcrumb.setText("Wear Statistics"); self.set_active(self.stats_button); self.stats_page.refresh()
    def show_settings(self):
        self.set_search_context(False)
        self.stack.setCurrentWidget(self.settings_page); self.breadcrumb.setText("Settings"); self.set_active(self.settings_button); self.settings_page.refresh()
    def show_add(self):
        self.set_search_context(False)
        self.previous_page=self.stack.currentWidget(); self.form_page.prepare(None); self.stack.setCurrentWidget(self.form_page); self.breadcrumb.setText("Add Clothing"); self.set_active(self.add_button)
    def go_back_from_form(self):
        if self.previous_page in (self.home_page,self.items_page,self.unused_page,self.builder_page,self.outfits_page,self.today_page,self.stats_page,self.settings_page,self.category_page):
            page = self.previous_page
            self.stack.setCurrentWidget(page)
            if page is self.items_page:
                self.set_search_context(True, "Search wardrobe... (Ctrl+K)")
            elif page is self.unused_page:
                self.set_search_context(True, "Search unused clothing... (Ctrl+K)")
            elif page is self.category_page:
                label = display_category_name(self.current_category).lower() if self.current_category else "category"
                self.set_search_context(True, f"Search {label}...")
            else:
                self.set_search_context(False)
            self.refresh_visible_page()
        else: self.show_home()
    def edit_item(self,item):
        self.set_search_context(False)
        self.previous_page=self.stack.currentWidget(); self.form_page.prepare(item); self.stack.setCurrentWidget(self.form_page); self.breadcrumb.setText(f"Edit / {item.name}"); self.set_active(self.add_button)
    def show_item_detail(self,item): ClothingDetailDialog(self,item).exec()
    def toggle_favorite(self,uid):
        try: value=self.repository.toggle_favorite(uid)
        except Exception as e: QMessageBox.critical(self,"Action failed",str(e)); return False
        self.refresh_visible_page(); self.notify("Added to favorites." if value else "Removed from favorites."); return value
    def mark_item_used(self,uid):
        current_page = self.stack.currentWidget()
        try:
            self.repository.mark_item_used(uid)
            self.refresh_visible_page()
        except Exception as e:
            QMessageBox.critical(self,"Action failed",str(e))
            return
        if self.stack.currentWidget() is not current_page:
            self.stack.setCurrentWidget(current_page)
        self.notify("Wear recorded.")
    def toggle_outfit_favorite(self,oid):
        try: value=self.repository.toggle_outfit_favorite(oid)
        except Exception as e: QMessageBox.critical(self,"Action failed",str(e)); return
        self.refresh_visible_page(); self.notify("Outfit added to favorites." if value else "Outfit removed from favorites.")
    def mark_outfit_used(self,oid):
        try: self.repository.mark_outfit_used(oid)
        except Exception as e: QMessageBox.critical(self,"Action failed",str(e)); return
        self.refresh_visible_page(); self.notify("Outfit wear recorded.")
    def delete_outfit(self,oid):
        answer=QMessageBox.question(self,"Delete outfit","Delete this saved outfit?",QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        if answer!=QMessageBox.Yes:return
        try:self.repository.delete_outfit(oid)
        except Exception as e:QMessageBox.critical(self,"Delete failed",str(e));return
        self.refresh_visible_page(); self.notify("Outfit deleted. Use Undo to restore it.")
    def delete_item(self,item):
        if self.confirm_delete:
            answer=QMessageBox.question(self,"Delete clothing",f'Delete “{item.name}”?\n\nThe change will be saved to the local database.',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
            if answer!=QMessageBox.Yes:return
        try:self.repository.delete(item.number)
        except Exception as e:QMessageBox.critical(self,"Delete failed",str(e));return
        self.refresh_visible_page(); self.notify("Clothing deleted. Use Undo to restore it.")
    def undo_last(self):
        try: action=self.repository.undo()
        except Exception as e: self.notify(str(e)); return
        self.refresh_visible_page(); self.undo_button.setEnabled(False); self.notify(f"Undid: {action}.")
    def show_history_dialog(self): UsageHistoryDialog(self).exec()
    def global_search_changed(self,text):
        page = self.stack.currentWidget()
        if page is self.category_page:
            self.category_page.render_current()
        elif page is self.items_page:
            self.items_page.refresh_results()
        elif page is self.unused_page:
            self.unused_page.refresh()


def main():
    prepare_user_data()
    sys.excepthook = log_exception
    app=QApplication(sys.argv)
    if APP_ICON_FILE.exists(): app.setWindowIcon(QIcon(str(APP_ICON_FILE)))
    app.setStyle("Fusion")
    app.setApplicationName("WARDROBE")
    app.setOrganizationName("WARDROBE")
    app.setApplicationVersion(APP_VERSION)
    try:
        window=MainWindow()
    except Exception as exc:
        QMessageBox.critical(None, "WARDROBE could not start", f"The application could not be started.\n\n{exc}\n\nA diagnostic log was written to the logs folder.")
        return 1
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
