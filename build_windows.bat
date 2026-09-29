@echo off
setlocal
cd /d "%~dp0"

echo ======================================
echo WARDROBE - Windows build
echo ======================================
echo.

echo [1/4] Installing/updating build dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo.
echo [2/4] Cleaning previous build...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

if not exist "WARDROBE.spec" (
    echo ERROR: WARDROBE.spec not found in:
    echo %CD%
    goto :error
)

echo.
echo [3/4] Building WARDROBE (onedir)...
python -m PyInstaller --clean --noconfirm "WARDROBE.spec"
if errorlevel 1 goto :error

if not exist "dist\WARDROBE\WARDROBE.exe" (
    echo ERROR: Build finished but dist\WARDROBE\WARDROBE.exe was not created.
    goto :error
)

echo.
echo [4/4] Build completed successfully!
echo.
echo Output:
echo %CD%\dist\WARDROBE\WARDROBE.exe
echo.
echo ======================================
echo WARDROBE build successful.
echo ======================================
pause
exit /b 0

:error
echo.
echo ======================================
echo WARDROBE build FAILED.
echo ======================================
pause
exit /b 1
