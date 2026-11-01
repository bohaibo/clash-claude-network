@echo off
chcp 65001 >nul
color 0F
setlocal
set PYTHONNOUSERSITE=1
cd /d "%~dp0"
title Claude 网络环境

rem The GUI is the normal entry point. `--menu` keeps a console fallback for
rem troubleshooting or machines where the isolated browser cannot be opened.
if /I not "%~1"=="--menu" goto gui

:menu
echo.
echo 正在检查项目内环境（不会修改系统 PATH）...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap.ps1"
if errorlevel 1 (
  pause
  exit /b 1
)
echo.
echo ============================================
echo    Claude 网络环境（命令行备用入口）
echo ============================================
echo    执行时会逐条显示「第几步 / 共几步」
echo    1. 配置（完整）
echo    2. 恢复（可选快照）
echo    3. 保存当前快照
echo    4. 补充配置（扫描新增规则和网卡）
echo    5. 恢复最早状态并重新配置（独立网络看护）
echo    0. 退出
echo.
echo    正常使用请关闭本窗口，直接运行「一键网络.cmd」打开图形界面。
echo.
choice /C 123450 /N /M "请选择 [1/2/3/4/5/0]: "
if errorlevel 6 exit /b 0
if errorlevel 5 goto reconfigure
if errorlevel 4 goto supplement
if errorlevel 3 goto snapshot
if errorlevel 2 goto restore
if errorlevel 1 goto apply
goto menu

:apply
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run.ps1" -Action apply
set "code=%errorlevel%"
if not "%code%"=="0" pause
exit /b %code%

:restore
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run.ps1" -Action restore
set "code=%errorlevel%"
if not "%code%"=="0" pause
exit /b %code%

:snapshot
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run.ps1" -Action snapshot
set "code=%errorlevel%"
if not "%code%"=="0" pause
exit /b %code%

:supplement
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run.ps1" -Action supplement
set "code=%errorlevel%"
if not "%code%"=="0" pause
exit /b %code%

:reconfigure
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run.ps1" -Action reconfigure
set "code=%errorlevel%"
if not "%code%"=="0" pause
exit /b %code%

:gui
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\gui.ps1" -NoPause
set "code=%errorlevel%"
if not "%code%"=="0" pause
exit /b %code%
