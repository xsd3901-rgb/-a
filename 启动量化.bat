@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
title A-Quant 沪深A股量化终端

cd /d "%~dp0"

echo ============================================================
echo              A-Quant 沪深A股量化终端
echo ============================================================
echo.
echo 项目目录：%CD%
echo.

REM ============================================================
REM 代理说明：
REM - 保留你电脑现有代理，不修改系统代理设置；
REM - 仅明确让 127.0.0.1 / localhost 不经过代理；
REM - 外部行情接口仍按系统/程序原有网络方式访问。
REM ============================================================
set "NO_PROXY=127.0.0.1,localhost"
set "no_proxy=127.0.0.1,localhost"

REM ---- 必需文件检查 ----
if not exist "%~dp0webapp.py" (
    echo [错误] 当前文件夹里没有 webapp.py
    echo.
    echo 请确认完整项目位于：
    echo C:\Users\Lenovo\Desktop\6688
    echo.
    echo 启动量化.bat 必须和 webapp.py、main.py、requirements.txt
    echo 放在同一个 6688 文件夹里。
    echo.
    pause
    exit /b 1
)

if not exist "%~dp0requirements.txt" (
    echo [错误] 当前文件夹里没有 requirements.txt
    echo 请先把完整项目放入 6688 文件夹。
    echo.
    pause
    exit /b 1
)

set "PYEXE="
set "PYARGS="

rem Python 寻址顺序：显式指定 -> 项目虚拟环境 -> 常见安装目录 -> PATH -> py launcher
if defined AQUANT_PYTHON if exist "%AQUANT_PYTHON%" set "PYEXE=%AQUANT_PYTHON%"
if not defined PYEXE if exist "%~dp0.venv\Scripts\python.exe" set "PYEXE=%~dp0.venv\Scripts\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PYEXE if exist "C:\Program Files\Python312\python.exe" set "PYEXE=C:\Program Files\Python312\python.exe"
if not defined PYEXE if exist "C:\Program Files\Python311\python.exe" set "PYEXE=C:\Program Files\Python311\python.exe"

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

echo [1/5] 检查 Python...
"%PYEXE%" %PYARGS% -c "import sys; print('Python', sys.version.split()[0])"
if errorlevel 1 (
    echo.
    echo [错误] Python 无法正常运行。
    pause
    exit /b 1
)

echo.
echo [2/5] 检查量化依赖...
"%PYEXE%" %PYARGS% -c "import flask,pandas,numpy,duckdb,pyarrow,psutil,requests,openpyxl,akshare,baostock" >nul 2>nul
if errorlevel 1 (
    echo 检测到依赖不完整，正在安装 requirements.txt...
    echo 这一步第一次运行可能需要几分钟。
    echo.
    "%PYEXE%" %PYARGS% -m pip install -r "%~dp0requirements.txt"
    if errorlevel 1 (
        echo.
        echo [错误] 依赖安装失败。
        echo 如果你的网络必须走代理，请先确认 pip 能通过当前代理联网。
        echo.
        pause
        exit /b 1
    )
)

echo.
echo [3/5] 检查程序是否能加载...
"%PYEXE%" %PYARGS% -c "import webapp; print('程序加载正常')"
if errorlevel 1 (
    echo.
    echo [错误] webapp.py 加载失败。
    echo 请把这个黑色窗口中的完整报错截图发给 ChatGPT。
    echo.
    pause
    exit /b 1
)

echo.
echo [4/5] 准备本地数据库目录...
if not exist "%~dp0data_store" mkdir "%~dp0data_store"
if not exist "%~dp0reports" mkdir "%~dp0reports"
if not exist "%~dp0data_cache" mkdir "%~dp0data_cache"

echo 数据库：%~dp0data_store
echo 报告：  %~dp0reports

echo.
echo [5/5] 启动量化终端...
echo.
echo 你的系统代理会保留。
echo 本地地址 127.0.0.1 已设置为不走代理。
echo.
echo 正在启动 Flask 服务...
echo.

start "A-Quant Server" /D "%~dp0" cmd /k ""%PYEXE%" %PYARGS% "%~dp0webapp.py""

echo 正在等待 http://127.0.0.1:5000 真正可访问...
set "READY=0"

for /L %%I in (1,1,30) do (
    "%PYEXE%" %PYARGS% -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000', timeout=1).read(32)" >nul 2>nul
    if not errorlevel 1 (
        set "READY=1"
        goto :READY
    )
    timeout /t 1 /nobreak >nul
)

:READY
if "%READY%"=="1" (
    echo.
    echo [成功] A-Quant 服务已经启动。
    echo 正在打开浏览器...
    start "" "http://127.0.0.1:5000"
    echo.
    echo 如果浏览器没有自动打开，请手动访问：
    echo http://127.0.0.1:5000
    echo.
    echo 启动器可以关闭；请保留 A-Quant Server 黑色窗口。
    timeout /t 3 /nobreak >nul
    exit /b 0
)

echo.
echo [错误] 等待 30 秒后，127.0.0.1:5000 仍未启动。
echo.
echo 这通常不是代理导致的，而是 webapp.py 启动时报错。
echo 请查看刚才弹出的 “A-Quant Server” 黑色窗口，
echo 把里面最后的完整报错截图发给 ChatGPT。
echo.
pause
exit /b 1
