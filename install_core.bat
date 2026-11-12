@echo off
setlocal
cd /d "%~dp0"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (echo Core installation failed.&pause&exit /b 1)
echo.
echo Trarium core installed. Run run_trarium.bat
pause
