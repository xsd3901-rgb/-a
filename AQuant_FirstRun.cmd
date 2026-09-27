@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title A-Quant First Run

set "NO_PROXY=127.0.0.1,localhost"
set "no_proxy=127.0.0.1,localhost"

if not exist "%~dp0main.py" goto MISSING
if not exist "%~dp0webapp.py" goto MISSING
if not exist "%~dp0requirements.txt" goto MISSING
if not exist "%~dp0aquant" goto MISSING

call :FIND_PYTHON
if not defined PYEXE goto NOPYTHON

echo [1/5] Python check...
"%PYEXE%" %PYARGS% -c "import sys; assert sys.version_info >= (3,11); print(sys.version.split()[0])"
if errorlevel 1 goto BADPYTHON

echo [2/5] Dependency check...
"%PYEXE%" %PYARGS% -c "import flask,pandas,numpy,duckdb,pyarrow,psutil,requests,openpyxl,akshare,baostock" >nul 2>nul
if errorlevel 1 (
  "%PYEXE%" %PYARGS% -m pip install -r "%~dp0requirements.txt"
  if errorlevel 1 goto DEPFAIL
)

echo [3/5] Compile check...
"%PYEXE%" %PYARGS% -m compileall -q "%~dp0"
if errorlevel 1 goto COMPILEFAIL

echo [4/5] Readiness check...
"%PYEXE%" %PYARGS% "%~dp0main.py" readiness

echo [5/5] Starting web terminal...
start "A-Quant Server" /D "%~dp0" cmd /k ""%PYEXE%" %PYARGS% "%~dp0webapp.py""

for /L %%I in (1,1,40) do (
  "%PYEXE%" %PYARGS% -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000', timeout=1).read(16)" >nul 2>nul
  if not errorlevel 1 goto WEBREADY
  timeout /t 1 /nobreak >nul
)

echo ERROR: web server did not become ready.
goto HOLD

:WEBREADY
echo SUCCESS: http://127.0.0.1:5000
start "" "http://127.0.0.1:5000"
goto HOLD

:FIND_PYTHON
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
exit /b 0

:MISSING
echo ERROR: incomplete A-Quant project.
goto HOLD
:NOPYTHON
echo ERROR: Python 3.11/3.12 not found.
goto HOLD
:BADPYTHON
echo ERROR: Python cannot run or version is too old.
goto HOLD
:DEPFAIL
echo ERROR: dependency installation failed.
goto HOLD
:COMPILEFAIL
echo ERROR: project compile check failed.
goto HOLD
:HOLD
echo.
pause
