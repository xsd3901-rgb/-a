@echo off
setlocal
chcp 65001 >nul
title A-Quant 量化终端

cd /d "%~dp0"

echo ============================================================
echo              A-Quant 沪深A股量化终端
echo ============================================================
echo.
echo 项目目录：%CD%
echo.

set "PYEXE="
set "PYARGS="

if exist "C:\Program Files\Python312\python.exe" (
    set "PYEXE=C:\Program Files\Python312\python.exe"
)

if not defined PYEXE (
    for /f "delims=" %%P in ('where python 2^>nul') do (
        if not defined PYEXE set "PYEXE=%%P"
    )
)

if not defined PYEXE (
    for /f "delims=" %%P in ('where py 2^>nul') do (
        if not defined PYEXE (
            set "PYEXE=%%P"
            set "PYARGS=-3"
        )
    )
)

if not defined PYEXE (
    echo [错误] 没有找到 Python。
    echo 请先安装 Python 3.11 或 3.12。
    echo.
    pause
    exit /b 1
)

echo [1/4] 检查 Python...
"%PYEXE%" %PYARGS% -c "import sys; print('Python', sys.version.split()[0])"
if errorlevel 1 (
    echo [错误] Python 无法正常运行。
    echo.
    pause
    exit /b 1
)

echo.
echo [2/4] 检查核心依赖...
"%PYEXE%" %PYARGS% -c "import flask,pandas,numpy,duckdb,pyarrow,psutil" >nul 2>nul
if errorlevel 1 (
    echo 检测到依赖不完整，正在安装 requirements.txt...
    if not exist "requirements.txt" (
        echo [错误] 当前目录没有 requirements.txt。
        echo.
        pause
        exit /b 1
    )
    "%PYEXE%" %PYARGS% -m pip install -r requirements.txt
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
echo 浏览器将在几秒后自动打开：
echo http://127.0.0.1:5000
echo.
echo 提示：关闭这个窗口即可停止量化终端。
echo ============================================================
echo.

start "" cmd /c "timeout /t 3 /nobreak >nul & start "" "http://127.0.0.1:5000""

"%PYEXE%" %PYARGS% "%~dp0webapp.py"

echo.
echo A-Quant 已停止。
pause
