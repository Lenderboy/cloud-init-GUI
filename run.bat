@echo off
setlocal EnableDelayedExpansion

:: run.bat — Launch cloud-init GUI via uv (Windows)
:: Double-click this file or run it from a terminal.

cd /d "%~dp0"

echo.
echo   cloud-init GUI -- Ubuntu Server OVA Configurator
echo   --------------------------------------------------
echo.

:: ---------------------------------------------------------------------------
:: 1. Ensure uv is available, installing it if necessary
:: ---------------------------------------------------------------------------
where uv >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo uv not found -- downloading installer via PowerShell...
    powershell -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    if %ERRORLEVEL% neq 0 (
        echo.
        echo ERROR: uv installation failed.
        echo Make sure you are connected to the internet and try again.
        pause
        exit /b 1
    )

    :: Add uv to PATH for this session
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"

    where uv >nul 2>&1
    if !ERRORLEVEL! neq 0 (
        echo.
        echo ERROR: uv was installed but is still not in PATH.
        echo Close this window, open a new terminal, and run run.bat again.
        pause
        exit /b 1
    )
)

for /f "tokens=*" %%v in ('uv --version') do echo uv found: %%v

:: ---------------------------------------------------------------------------
:: 2. Ensure a pyproject.toml exists (uv project bootstrap)
:: ---------------------------------------------------------------------------
if not exist pyproject.toml (
    echo Initialising uv project...
    uv init --name cloud-init-gui --no-readme --python ">=3.10"
    if exist main.py  del main.py
    if exist hello.py del hello.py
)

:: ---------------------------------------------------------------------------
:: 3. Install Python dependencies
::    tkinter is bundled with uv-managed Python on Windows by default.
:: ---------------------------------------------------------------------------
echo Syncing dependencies...
uv add "PyYAML>=6.0" "pycdlib>=1.14.0" >nul 2>&1
uv add tk >nul 2>&1 || echo    (tk wheel unavailable -- using stdlib tkinter)

:: Verify tkinter works
uv run python -c "import tkinter" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo.
    echo ERROR: tkinter is not available in the uv-managed Python.
    echo uv-managed Python for Windows normally includes tkinter.
    echo Try: uv python install 3.12
    echo Then re-run this script.
    pause
    exit /b 1
)

:: ---------------------------------------------------------------------------
:: 4. Launch the application
:: ---------------------------------------------------------------------------
echo Launching cloud-init GUI...
echo.
uv run app.py

:: Keep the window open if the app exits with an error
if %ERRORLEVEL% neq 0 (
    echo.
    echo The app exited with an error (code %ERRORLEVEL%).
    pause
)
