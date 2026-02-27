@echo off
setlocal
cd /d "%~dp0"

echo [1/2] Locating PyInstaller...
where pyinstaller >nul 2>&1
if errorlevel 1 (
    echo PyInstaller not on PATH. Installing into venv...
    .venv\Scripts\pip.exe install "pyinstaller>=6.0" --quiet
    if errorlevel 1 ( echo ERROR: pip install failed. & pause & exit /b 1 )
    set PYINST=.venv\Scripts\pyinstaller.exe
) else (
    set PYINST=pyinstaller
)

echo [2/2] Building OdinM_py.exe ...
%PYINST% OdinM_py.spec
if errorlevel 1 (
    echo ERROR: PyInstaller build failed.
    pause & exit /b 1
)

echo.
echo Done. Standalone exe: dist\OdinM_py.exe
pause
