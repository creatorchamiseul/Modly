@echo off
setlocal
cd /d "%~dp0"

rem ============================================================
rem  Modly local launcher - keeps everything inside this folder
rem  Runtime data  : <this folder>\data
rem  Download cache: <this folder>\.cache
rem ============================================================

set "MODLY_USER_DATA_DIR=%~dp0data"
set "HF_HOME=%~dp0.cache\huggingface"
set "HUGGINGFACE_HUB_CACHE=%~dp0.cache\huggingface\hub"
set "PIP_CACHE_DIR=%~dp0.cache\pip"
set "U2NET_HOME=%~dp0.cache\rembg"
set "TRITON_CACHE_DIR=%~dp0.cache\triton"

if not exist "%MODLY_USER_DATA_DIR%" mkdir "%MODLY_USER_DATA_DIR%"
if not exist "%MODLY_USER_DATA_DIR%\logs" mkdir "%MODLY_USER_DATA_DIR%\logs"

where node >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js not found. Install it from https://nodejs.org
    pause
    exit /b 1
)

if not exist "node_modules\" (
    echo [1/3] Installing JS dependencies...
    call npm install
    if errorlevel 1 ( echo [ERROR] npm install failed. & pause & exit /b 1 )
)

if not exist "resources\python-embed\python.exe" (
    echo [2/3] Downloading bundled Python runtime...
    node scripts\download-python-embed.js
    if errorlevel 1 ( echo [ERROR] Python runtime download failed. & pause & exit /b 1 )
)

if not exist "out\main\index.js" (
    echo [3/3] Building Modly...
    call npm run build
    if errorlevel 1 ( echo [ERROR] Build failed. & pause & exit /b 1 )
)

if not exist "node_modules\electron\dist\electron.exe" (
    echo [INFO] Electron binary missing - attempting repair...
    call node "node_modules\electron\install.js"
)
if not exist "node_modules\electron\dist\electron.exe" (
    echo [ERROR] Electron repair failed. Check your network connection, then run:
    echo     node node_modules\electron\install.js
    pause
    exit /b 1
)

echo Launching Modly...
start "" "%~dp0node_modules\electron\dist\electron.exe" "%~dp0."
endlocal