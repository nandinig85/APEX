@echo off
REM APEX one-click install and launch (Windows). Double-click this file.
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.10+ is required. Install it from https://www.python.org/downloads/ and tick "Add Python to PATH".
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  python -m venv .venv || (echo Could not create the virtual environment. & pause & exit /b 1)
)
echo Installing dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt || (echo Dependency install failed. & pause & exit /b 1)
echo Starting APEX at http://localhost:8501 ...
".venv\Scripts\python.exe" -m streamlit run app.py
pause
