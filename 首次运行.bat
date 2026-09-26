@echo off
setlocal EnableExtensions EnableDelayedExpansion

if /I "%~1"=="RUN" goto RUN
start "A-Quant First Run" cmd /k ""%~f0" RUN"
exit /b

:RUN
chcp 65001 >nul
title A-Quant 首次运行
cd /d "%~dp0"

set "NO_PROXY=127.0.0.1,localhost"
set "no_proxy=127.0.0.1,localhost"

echo ============================================================
echo              A-Quant 沪深A股量化终端
echo                    首次运行向导
echo ============================================================
echo.
echo 项目目录：%CD%
echo.

if not exist "%~dp0main.py" goto MISSING_PROJECT
if not exist "%~dp0webapp.py" goto MISSING_PROJECT
if not exist "%~dp0requirements.txt" goto MISSING_PROJECT
if not exist "%~dp0aquant" goto MISSING_PROJECT

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
if not defined PYEXE goto NO_PYTHON

echo [1/5] Python...
"%PYEXE%" %PYARGS% -c "import sys; assert sys.version_info >= (3,11), '需要 Python 3.11+'; print(sys.version.split()[0])"
if errorlevel 1 goto PYTHON_BAD

echo.
echo [2/5] 核心依赖...
"%PYEXE%" %PYARGS% -c "import flask,pandas,numpy,duckdb,pyarrow,psutil,requests,openpyxl,akshare,baostock" >nul 2>nul
if errorlevel 1 (
    echo 首次运行需要安装依赖，正在执行 requirements.txt...
    "%PYEXE%" %PYARGS% -m pip install -r "%~dp0requirements.txt"
    if errorlevel 1 goto DEP_FAIL
) else (
    echo 依赖已齐全。
)

echo.
echo [3/5] 编译检查...
"%PYEXE%" %PYARGS% -m compileall -q "%~dp0"
if errorlevel 1 goto COMPILE_FAIL
echo 代码加载检查通过。

echo.
echo [4/5] 当前就绪状态...
"%PYEXE%" %PYARGS% "%~dp0main.py" readiness
if errorlevel 1 (
    echo.
    echo 就绪检查返回异常，但仍可启动网页查看详细状态。
)

echo.
echo [5/5] 启动网页终端...
start "A-Quant Server" /D "%~dp0" cmd /k ""%PYEXE%" %PYARGS% "%~dp0webapp.py""

echo 正在等待 http://127.0.0.1:5000 ...
set "READY=0"
for /L %%I in (1,1,40) do (
    "%PYEXE%" %PYARGS% -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000', timeout=1).read(16)" >nul 2>nul
    if not errorlevel 1 (
        set "READY=1"
        goto WEB_READY
    )
    timeout /t 1 /nobreak >nul
)

:WEB_READY
if "%READY%"=="1" (
    echo.
    echo [成功] 网页终端已经启动。
    echo 第一次使用请在网页中点击【首次完整准备】。
    echo 该步骤会建立本地数据库、审计数据并运行研究验收。
    echo.
    start "" "http://127.0.0.1:5000"
    echo 可以关闭本向导窗口；请保留 A-Quant Server 窗口。
    pause
    exit /b 0
)

echo.
echo [错误] 网页服务 40 秒内没有启动。
echo 请查看 A-Quant Server 窗口中的最后几行报错。
pause
exit /b 1

:MISSING_PROJECT
echo [错误] 当前文件夹不是完整 A-Quant 项目。
echo 必须同时存在 main.py、webapp.py、requirements.txt 和 aquant 文件夹。
echo.
echo 当前目录：%CD%
pause
exit /b 1

:NO_PYTHON
echo [错误] 没有找到 Python 3.11/3.12。
pause
exit /b 1

:PYTHON_BAD
echo [错误] Python 版本过低或无法运行。
pause
exit /b 1

:DEP_FAIL
echo [错误] 依赖安装失败。
echo 你使用代理时，请先确保 pip 能通过当前代理联网。
pause
exit /b 1

:COMPILE_FAIL
echo [错误] 项目代码编译检查失败。
pause
exit /b 1
