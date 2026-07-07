@echo off
setlocal
cd /d %~dp0
title GiziKos Server
where py >nul 2>nul
if %errorlevel%==0 (
  set PYTHON_CMD=py
) else (
  set PYTHON_CMD=python
)
if not exist .venv (
  echo [1/3] Membuat virtual environment...
  %PYTHON_CMD% -m venv .venv
)
call .venv\Scripts\activate
if not exist .venv\.dependencies_v3 (
  echo [2/3] Memasang dependency untuk pertama kali...
  python -m pip install -r requirements.txt
  if errorlevel 1 goto :error
  type nul > .venv\.dependencies_v3
) else (
  echo [2/3] Dependency sudah tersedia.
)
echo [3/3] Menjalankan GiziKos...
python run.py
if errorlevel 1 goto :error
goto :end
:error
echo.
echo GiziKos gagal dijalankan. Baca pesan error di atas.
pause
:end
endlocal
