@echo off
setlocal EnableExtensions
chcp 65001 >nul
title A-Quant 全市场建库并验收
cd /d "%~dp0"

set "NO_PROXY=127.0.0.1,localhost"
set "no_proxy=127.0.0.1,localhost"

echo ============================================================
echo          A-Quant 全市场建库 + 系统验收
echo ============================================================
echo.
echo 该任务会处理沪深全市场，第一次运行可能耗时较长。
echo 中途不要关闭窗口。
echo.
pause

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
    echo [错误] 未找到 Python。
    pause
    exit /b 1
)

"%PYEXE%" %PYARGS% "%~dp0main.py" prepare-local --bootstrap-limit 0 --validation-limit 200 --horizon 10
if errorlevel 1 (
    echo.
    echo [错误] 首次准备未完成，请查看上方报错和 reports 文件夹。
    pause
    exit /b 1
)

echo.
echo ============================================================
echo 首次完整准备流程已结束。
echo 现在可以双击 启动量化.bat 打开网页终端。
echo ============================================================
pause
