@echo off
setlocal EnableExtensions

if /I "%~1"=="RUN" goto RUN
start "A-Quant Installer" cmd /k ""%~f0" RUN"
exit /b

:RUN
chcp 65001 >nul
title A-Quant Installer
cd /d "%~dp0"

echo ============================================================
echo A-Quant one-click installer
echo Target: %CD%
echo ============================================================
echo.

set "TMPROOT=%TEMP%\aquant_install_fixed"
set "ZIPFILE=%TMPROOT%\project.zip"
set "EXTRACT=%TMPROOT%\extract"
set "SRC="

if exist "%TMPROOT%" rd /s /q "%TMPROOT%"
mkdir "%TMPROOT%"
if errorlevel 1 goto FAIL_MKDIR
mkdir "%EXTRACT%"
if errorlevel 1 goto FAIL_MKDIR

echo [1/4] Downloading project from GitHub...
where curl.exe >nul 2>nul
if errorlevel 1 goto DOWNLOAD_PS

curl.exe -L --fail --retry 2 -o "%ZIPFILE%" "https://github.com/xsd3901-rgb/-a/archive/refs/heads/main.zip"
if errorlevel 1 goto FAIL_DOWNLOAD
goto EXTRACT_ZIP

:DOWNLOAD_PS
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/xsd3901-rgb/-a/archive/refs/heads/main.zip' -OutFile '%ZIPFILE%'"
if errorlevel 1 goto FAIL_DOWNLOAD

:EXTRACT_ZIP
echo [2/4] Extracting...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; Expand-Archive -LiteralPath '%ZIPFILE%' -DestinationPath '%EXTRACT%' -Force"
if errorlevel 1 goto FAIL_EXTRACT

for /d %%D in ("%EXTRACT%\*") do set "SRC=%%~fD"
if not defined SRC goto FAIL_SOURCE

echo [3/4] Copying project files...
robocopy "%SRC%" "%CD%" /E /R:1 /W:1 /XF "安装量化项目.bat" "安装量化项目_修复版.bat" >nul
set "RC=%ERRORLEVEL%"
if %RC% GEQ 8 goto FAIL_COPY

if exist "%SRC%\启动量化.bat" copy /Y "%SRC%\启动量化.bat" "%CD%\启动量化.bat" >nul

echo [4/4] Checking files...
if not exist "%CD%\webapp.py" goto FAIL_CHECK
if not exist "%CD%\main.py" goto FAIL_CHECK
if not exist "%CD%\requirements.txt" goto FAIL_CHECK
if not exist "%CD%\aquant" goto FAIL_CHECK

echo.
echo ============================================================
echo SUCCESS
echo Full A-Quant project is now installed in:
echo %CD%
echo ============================================================
echo.
echo You should now see webapp.py, main.py, requirements.txt and aquant.
echo.
echo Next: double-click "启动量化.bat"
echo.
rd /s /q "%TMPROOT%" >nul 2>nul
pause
exit /b 0

:FAIL_MKDIR
echo.
echo [ERROR] Could not create temporary folder.
goto FAIL_END

:FAIL_DOWNLOAD
echo.
echo [ERROR] Download failed.
echo Your proxy may block curl/PowerShell access to GitHub.
echo Test this URL in your browser:
echo https://github.com/xsd3901-rgb/-a
goto FAIL_END

:FAIL_EXTRACT
echo.
echo [ERROR] ZIP extraction failed.
goto FAIL_END

:FAIL_SOURCE
echo.
echo [ERROR] Extracted project folder was not found.
goto FAIL_END

:FAIL_COPY
echo.
echo [ERROR] Project file copy failed. Robocopy code: %RC%
goto FAIL_END

:FAIL_CHECK
echo.
echo [ERROR] Installation finished but core files are missing.
goto FAIL_END

:FAIL_END
echo.
echo This window will stay open.
echo Please send a screenshot of the last error lines to ChatGPT.
echo.
pause
exit /b 1
