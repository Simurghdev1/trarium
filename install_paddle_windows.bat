@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python launcher 'py' was not found.
  echo For PaddleOCR on Windows, use a supported Python environment such as Python 3.12.
  pause
  exit /b 1
)
py -3.12 -m venv .venv-paddle
if errorlevel 1 (
  echo Python 3.12 is not installed. Install Python 3.12, then run this file again.
  pause
  exit /b 1
)
call .venv-paddle\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install paddlepaddle paddleocr
if errorlevel 1 (
  echo PaddleOCR installation failed. Check the Python/PaddlePaddle compatibility shown above.
  pause
  exit /b 1
)
python -c "import paddle,paddleocr; print('PaddlePaddle:', paddle.__version__); print('PaddleOCR:', paddleocr.__version__)"
echo.
echo PaddleOCR environment is ready.
pause
