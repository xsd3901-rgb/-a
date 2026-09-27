@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title A-Quant Full Market Acceptance
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
if not defined PYEXE (
  echo ERROR: Python not found.
  pause
  exit /b 1
)

echo Starting full-market local preparation and acceptance...
"%PYEXE%" %PYARGS% "%~dp0main.py" prepare-local --bootstrap-limit 0 --validation-limit 200 --horizon 10
echo.
if errorlevel 1 (
  echo FAILED. Check reports and the error above.
) else (
  echo COMPLETE.
)
pause
