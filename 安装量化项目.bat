@echo off
setlocal EnableExtensions
chcp 65001 >nul
title A-Quant 一键安装

cd /d "%~dp0"

echo ============================================================
echo              A-Quant 沪深A股量化终端
echo                 一键安装到当前文件夹
echo ============================================================
echo.
echo 安装目录：%CD%
echo.

if exist "%~dp0webapp.py" (
    echo 已检测到完整项目文件。
    echo.
    if exist "%~dp0启动量化.bat" (
        echo 正在启动量化终端...
        call "%~dp0启动量化.bat"
        exit /b %errorlevel%
    )
    echo [错误] 找到了项目，但没有找到 启动量化.bat
    pause
    exit /b 1
)

echo 当前文件夹只有启动器或项目文件不完整。
echo 正在从 GitHub 下载完整项目...
echo 你的系统代理设置会保持不变。
echo.

set "TMPROOT=%TEMP%\aquant_install_%RANDOM%%RANDOM%"
set "ZIPFILE=%TMPROOT%\project.zip"
set "EXTRACT=%TMPROOT%\extract"

mkdir "%TMPROOT%" >nul 2>nul
mkdir "%EXTRACT%" >nul 2>nul

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop';" ^
  "$ProgressPreference='SilentlyContinue';" ^
  "$zip='%ZIPFILE%';" ^
  "$extract='%EXTRACT%';" ^
  "$dest='%CD%';" ^
  "Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/xsd3901-rgb/-a/archive/refs/heads/main.zip' -OutFile $zip;" ^
  "Expand-Archive -LiteralPath $zip -DestinationPath $extract -Force;" ^
  "$src=Get-ChildItem -LiteralPath $extract -Directory | Select-Object -First 1;" ^
  "if(-not $src){throw '没有找到解压后的项目目录'};" ^
  "Copy-Item -Path (Join-Path $src.FullName '*') -Destination $dest -Recurse -Force;"

if errorlevel 1 (
    echo.
    echo [错误] 项目下载或安装失败。
    echo.
    echo 请确认浏览器能正常打开 GitHub，并检查代理是否允许 PowerShell 联网。
    echo GitHub 项目：
    echo https://github.com/xsd3901-rgb/-a
    echo.
    rd /s /q "%TMPROOT%" >nul 2>nul
    pause
    exit /b 1
)

rd /s /q "%TMPROOT%" >nul 2>nul

echo.
echo 正在检查项目文件...
if not exist "%~dp0webapp.py" (
    echo [错误] 安装完成后仍没有找到 webapp.py。
    echo 请把这个窗口截图发给 ChatGPT。
    pause
    exit /b 1
)

if not exist "%~dp0requirements.txt" (
    echo [错误] 安装完成后仍没有找到 requirements.txt。
    echo 请把这个窗口截图发给 ChatGPT。
    pause
    exit /b 1
)

echo.
echo [成功] 完整量化项目已经安装到：
echo %CD%
echo.
echo 现在应该能看到：
echo   webapp.py
echo   main.py
echo   requirements.txt
echo   aquant
echo   启动量化.bat
echo.
echo 正在启动量化终端...
echo.

if exist "%~dp0启动量化.bat" (
    call "%~dp0启动量化.bat"
) else (
    echo [错误] 没有找到 启动量化.bat
    pause
    exit /b 1
)
