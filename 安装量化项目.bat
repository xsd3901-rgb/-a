@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title A-Quant Installer

set "ZIP=%TEMP%\aquant_main.zip"
set "OUT=%TEMP%\aquant_main_extract"

if exist "%ZIP%" del /q "%ZIP%" >nul 2>nul
if exist "%OUT%" rd /s /q "%OUT%" >nul 2>nul
mkdir "%OUT%" >nul 2>nul

echo [1/4] Downloading latest A-Quant project...
where curl.exe >nul 2>nul
if errorlevel 1 goto POWERSHELL

curl.exe -L --fail --retry 2 --connect-timeout 20 -o "%ZIP%" "https://github.com/xsd3901-rgb/-a/archive/refs/heads/main.zip"
if exist "%ZIP%" goto EXTRACT

:POWERSHELL
echo curl failed or unavailable. Trying PowerShell...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/xsd3901-rgb/-a/archive/refs/heads/main.zip' -OutFile '%ZIP%'"
if not exist "%ZIP%" goto FAIL

:EXTRACT
echo [2/4] Extracting...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; Expand-Archive -LiteralPath '%ZIP%' -DestinationPath '%OUT%' -Force"
if errorlevel 1 goto FAIL

set "SRC="
for /d %%D in ("%OUT%\*") do if not defined SRC set "SRC=%%~fD"
if not defined SRC goto FAIL

echo [3/4] Copying project files...
robocopy "%SRC%" "%CD%" /E /R:1 /W:1 /XD ".git" "data_store" "reports" "data_cache" /XF "安装量化项目.bat" >nul
set "RC=%ERRORLEVEL%"
if %RC% GEQ 8 goto FAIL

echo [4/4] Checking project...
if not exist "%CD%\main.py" goto FAIL
if not exist "%CD%\webapp.py" goto FAIL
if not exist "%CD%\requirements.txt" goto FAIL
if not exist "%CD%\aquant" goto FAIL
if not exist "%CD%\AQuant_FirstRun.cmd" goto FAIL
if not exist "%CD%\AQuant_Start.cmd" goto FAIL
if not exist "%CD%\AQuant_FullMarket_Acceptance.cmd" goto FAIL

echo SUCCESS
echo Project installed/updated in:
echo %CD%
echo.
call "%CD%\AQuant_FirstRun.cmd"
exit /b 0

:FAIL
echo.
echo INSTALL FAILED
echo Please keep this window open and send the last error lines.
pause
exit /b 1
