@echo off
setlocal
chcp 65001 >nul
title A-Quant 量化终端启动器

cd /d "%~dp0"

echo ============================================================
echo              A-Quant 沪深A股量化终端
echo ============================================================
echo.
echo 项目目录：%CD%
echo.

set "PYEXE="

if exist "C:\Program Files\Python312\python.exe" (
    set "PYEXE=C:\Program Files\Python312\python.exe"
)

if not defined PYEXE (
    where python >nul 2>nul
    if not errorlevel 1 set "PYEXE=python"
)

if not defined PYEXE (
    where py >nul 2>nul
    if not errorlevel 1 set "PYEXE=py -3"
)

if not defined PYEXE (
    echo [错误] 没有找到 Python。
    echo 请先安装 Python 3.11 或 3.12。
    echo.
    pause
    exit /b 1
)

echo [1/4] 检查 Python...
%PYEXE% -c "import sys; print('Python', sys.version.split()[0])"
if errorlevel 1 (
    echo [错误] Python 无法正常运行。
    echo.
    pause
    exit /b 1
)

echo.
echo [2/4] 检查核心依赖...
%PYEXE% -c "import flask,pandas,numpy,duckdb,pyarrow,psutil" >nul 2>nul
if errorlevel 1 (
    echo 检测到依赖不完整，正在安装 requirements.txt...
    if not exist "requirements.txt" (
        echo [错误] 当前目录没有 requirements.txt。
        echo.
        pause
        exit /b 1
    )
    %PYEXE% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [错误] 依赖安装失败，请检查网络后重试。
        pause
        exit /b 1
    )
)

echo.
echo [3/4] 准备本地目录...
if not exist "data_store" mkdir "data_store"
if not exist "reports" mkdir "reports"
if not exist "data_cache" mkdir "data_cache"

echo 数据库目录：%CD%\data_store
echo 报告目录：  %CD%\reports

echo.
echo [4/4] 启动量化终端...
echo 稍后将自动打开浏览器：http://127.0.0.1:5000
echo.

start "A-Quant Server" cmd /k ""%PYEXE%" "%~dp0webapp.py""

timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:5000"

echo 启动完成。
echo 如果浏览器没有自动打开，请手动访问：
echo http://127.0.0.1:5000
echo.
timeout /t 2 /nobreak >nul
exit /b 0
