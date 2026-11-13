@echo off
setlocal
cd /d "%~dp0"
if exist ".venv-paddle\Scripts\python.exe" (
  echo Using Trarium PaddleOCR environment...
  call ".venv-paddle\Scripts\activate.bat"
)
python -m streamlit run streamlit_app.py
if errorlevel 1 pause
