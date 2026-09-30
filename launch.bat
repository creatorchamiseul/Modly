@echo off
setlocal
cd /d "%~dp0"

rem Keep all runtime data and download caches inside this folder (same as start-modly.bat)
set "MODLY_USER_DATA_DIR=%~dp0data"
set "HF_HOME=%~dp0.cache\huggingface"
set "HUGGINGFACE_HUB_CACHE=%~dp0.cache\huggingface\hub"
set "PIP_CACHE_DIR=%~dp0.cache\pip"
set "U2NET_HOME=%~dp0.cache\rembg"
set "TRITON_CACHE_DIR=%~dp0.cache\triton"

if not exist "%MODLY_USER_DATA_DIR%" mkdir "%MODLY_USER_DATA_DIR%"
if not exist "%MODLY_USER_DATA_DIR%\logs" mkdir "%MODLY_USER_DATA_DIR%\logs"

echo  Modly - Production Launcher
echo ================================
echo.

:: Check Node.js
where node >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js is not installed or not in PATH.
    echo         Download it from https://nodejs.org
    pause
    exit /b 1
)

:: Install dependencies if node_modules is missing
if not exist "node_modules\" (
    echo [1/3] Installing dependencies...
    call npm install
    if errorlevel 1 (
        echo [ERROR] npm install failed.
        pause
        exit /b 1
    )
    echo.
)

:: Download the bundled Python runtime if missing (needed by the first-run setup)
if not exist "resources\python-embed\python.exe" (
    echo [2/3] Downloading bundled Python runtime...
    node scripts\download-python-embed.js
    if errorlevel 1 (
        echo [ERROR] Python runtime download failed.
        pause
        exit /b 1
    )
    echo.
)

:: Build if out/ is missing
if not exist "out\" (
    echo [3/3] Building the app...
    call npm run build
    if errorlevel 1 (
        echo [ERROR] Build failed.
        pause
        exit /b 1
    )
    echo.
)

:: Repair electron binary if missing (downloads it via its install script)
if not exist "node_modules\electron\dist\electron.exe" (
    echo [INFO] Electron binary missing - attempting repair...
    call node "node_modules\electron\install.js"
)
if not exist "node_modules\electron\dist\electron.exe" (
    echo [ERROR] Electron repair failed. Check your network connection.
    pause
    exit /b 1
)

:: Launch (direct Electron start - fast, and the console window closes right away)
echo Launching Modly...
start "" "%~dp0node_modules\electron\dist\electron.exe" "%~dp0."

endlocal