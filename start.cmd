@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python 3.10+ ne nayden v PATH. Ustanovite Python s https://www.python.org/downloads/
    pause
    exit /b 1
)
python "%~dp0cli\launcher.py" %*
if errorlevel 1 pause
