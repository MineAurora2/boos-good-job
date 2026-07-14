@echo off
cd /d "%~dp0"

set "VENV_DIR=%~dp0.venv"
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
    echo Virtual environment not found: %VENV_DIR%
    echo Please run: python -m venv .venv
    pause
    exit /b 1
)

echo Activating virtual environment...
call "%VENV_DIR%\Scripts\activate.bat"

echo Starting goodjob backend...
"%PYTHON_EXE%" main.py

echo.
echo Backend exited.
pause
