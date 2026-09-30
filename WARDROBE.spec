# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

ROOT = Path(SPECPATH)

block_cipher = None

# Explicitly bundle every runtime resource used by WARDROBE. The folder is
# copied as-is so the packaged EXE can load PNG sidebar icons from RESOURCE_DIR.
datas = [
    (str(ROOT / 'update_config.json'), '.'),
    (str(ROOT / 'version.json'), '.'),
    (str(ROOT / 'wardrobe.ico'), '.'),
    (str(ROOT / 'icons'), 'icons'),
]

a = Analysis(
    [str(ROOT / 'main.py')],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='WARDROBE',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(ROOT / 'wardrobe.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='WARDROBE',
)
