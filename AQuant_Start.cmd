@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title A-Quant

set "NO_PROXY=127.0.0.1,localhost"
set "no_proxy=127.0.0.1,localhost"
set "PYEXE="
set "PYARGS="

if defined AQUANT_PYTHON if exist "%AQUANT_PYTHON%" set "PYEXE=%AQUANT_PYTHON%"
if not defined PYEXE if exist "%~dp0.venv\Scripts\python.exe" set "PYEXE=%~dp0.venv\Scripts\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PYEXE if exist "C:\Program Files\Python312\python.exe" set "PYEXE=C:\Program Files\Python312\python.exe"
if not defined PYEXE if exist "C:\Program Files\Python311\python.exe" set "PYEXE=C:\Program Files\Python311\python.exe"
if not defined PYEXE for /f "delims=" %%P in ('where python 2^>nul') do if not defined PYEXE set "PYEXE=%%P"
if not defined PYEXE for /f "delims=" %%P in ('where py 2^>nul') do if not defined PYEXE (
  set "PYEXE=%%P"
  set "PYARGS=-3"
)

if not defined PYEXE goto NOPYTHON
if not exist "%~dp0webapp.py" goto MISSING

echo Starting A-Quant...
start "A-Quant Server" /D "%~dp0" cmd /k ""%PYEXE%" %PYARGS% "%~dp0webapp.py""

for /L %%I in (1,1,40) do (
  "%PYEXE%" %PYARGS% -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000', timeout=1).read(16)" >nul 2>nul
  if not errorlevel 1 goto READY
  timeout /t 1 /nobreak >nul
)

echo ERROR: web server did not become ready within 40 seconds.
echo Check the A-Quant Server window.
pause
exit /b 1

:READY
echo A-Quant is ready: http://127.0.0.1:5000
start "" "http://127.0.0.1:5000"
exit /b 0

:NOPYTHON
echo ERROR: Python 3.11/3.12 not found.
pause
exit /b 1

:MISSING
echo ERROR: webapp.py not found in this folder.
pause
exit /b 1
