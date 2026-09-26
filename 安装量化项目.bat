@echo off
setlocal EnableExtensions EnableDelayedExpansion

if /I "%~1"=="RUN" goto RUN
start "A-Quant Installer" cmd /k ""%~f0" RUN"
exit /b

:RUN
chcp 65001 >nul
title A-Quant 一键安装
cd /d "%~dp0"

echo ============================================================
echo              A-Quant 沪深A股量化终端
echo                    一键安装向导
echo ============================================================
echo.
echo 目标目录：%CD%
echo.
echo 说明：
echo - 下载源码不会触发 GitHub Actions；
echo - 保留你电脑现有代理设置；
echo - 不删除已有 data_store / reports；
echo - 安装完成后自动进入“首次运行向导”。
echo.

if exist "%~dp0main.py" if exist "%~dp0webapp.py" if exist "%~dp0requirements.txt" if exist "%~dp0aquant" if exist "%~dp0首次运行.bat" goto PROJECT_READY

set "TMPROOT=%TEMP%\aquant_install_%RANDOM%%RANDOM%"
set "ZIPFILE=%TMPROOT%\project.zip"
set "EXTRACT=%TMPROOT%\extract"
set "SRC="

if exist "%TMPROOT%" rd /s /q "%TMPROOT%" >nul 2>nul
mkdir "%TMPROOT%" >nul 2>nul
if errorlevel 1 goto FAIL_TEMP
mkdir "%EXTRACT%" >nul 2>nul
if errorlevel 1 goto FAIL_TEMP

echo [1/4] 正在下载最新项目...
where curl.exe >nul 2>nul
if errorlevel 1 goto DOWNLOAD_PS

curl.exe -L --fail --retry 2 --connect-timeout 20 -o "%ZIPFILE%" "https://github.com/xsd3901-rgb/-a/archive/refs/heads/main.zip"
if errorlevel 1 goto DOWNLOAD_PS
goto EXTRACT_ZIP

:DOWNLOAD_PS
echo curl 下载不可用，改用 PowerShell...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/xsd3901-rgb/-a/archive/refs/heads/main.zip' -OutFile '%ZIPFILE%'"
if errorlevel 1 goto FAIL_DOWNLOAD

:EXTRACT_ZIP
echo.
echo [2/4] 正在解压项目...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; Expand-Archive -LiteralPath '%ZIPFILE%' -DestinationPath '%EXTRACT%' -Force"
if errorlevel 1 goto FAIL_EXTRACT

for /d %%D in ("%EXTRACT%\*") do (
    if not defined SRC set "SRC=%%~fD"
)
if not defined SRC goto FAIL_SOURCE

echo.
echo [3/4] 正在复制项目到当前文件夹...
robocopy "%SRC%" "%CD%" /E /R:1 /W:1 /XF "安装量化项目.bat" /XD ".git" "data_store" "reports" "data_cache" >nul
set "RC=%ERRORLEVEL%"
if %RC% GEQ 8 goto FAIL_COPY

echo.
echo [4/4] 正在检查完整性...
if not exist "%~dp0main.py" goto FAIL_CHECK
if not exist "%~dp0webapp.py" goto FAIL_CHECK
if not exist "%~dp0requirements.txt" goto FAIL_CHECK
if not exist "%~dp0aquant" goto FAIL_CHECK
if not exist "%~dp0首次运行.bat" goto FAIL_CHECK

rd /s /q "%TMPROOT%" >nul 2>nul

echo.
echo [成功] 完整 A-Quant 项目已经安装到：
echo %CD%
echo.
goto START_FIRST_RUN

:PROJECT_READY
echo 已检测到完整项目，跳过下载。
echo.

:START_FIRST_RUN
if not exist "%~dp0首次运行.bat" (
    echo [错误] 找不到 首次运行.bat。
    echo 请重新运行安装向导获取完整项目。
    pause
    exit /b 1
)

echo 正在打开首次运行向导...
call "%~dp0首次运行.bat"
echo.
echo 安装向导可以关闭。
pause
exit /b 0

:FAIL_TEMP
echo.
echo [错误] 无法创建临时目录。
goto FAIL_END

:FAIL_DOWNLOAD
echo.
echo [错误] 无法从 GitHub 下载项目。
echo 请确认代理已经连接，并确认浏览器能打开：
echo https://github.com/xsd3901-rgb/-a
goto FAIL_END

:FAIL_EXTRACT
echo.
echo [错误] 项目 ZIP 解压失败。
goto FAIL_END

:FAIL_SOURCE
echo.
echo [错误] 解压后没有找到项目目录。
goto FAIL_END

:FAIL_COPY
echo.
echo [错误] 项目复制失败，Robocopy 返回码：%RC%
goto FAIL_END

:FAIL_CHECK
echo.
echo [错误] 下载完成，但核心项目文件不完整。
goto FAIL_END

:FAIL_END
if exist "%TMPROOT%" rd /s /q "%TMPROOT%" >nul 2>nul
echo.
echo 窗口会保留。请把最后几行错误截图发给 ChatGPT。
pause
exit /b 1
