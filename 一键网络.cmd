@echo off
chcp 65001 >nul
color 0F
setlocal
cd /d "%~dp0"
title Claude 网络环境

:menu
echo.
echo ============================================
echo    Claude 网络环境配置 / 恢复
echo ============================================
echo    执行时会逐条显示「第几步 / 共几步」
echo    1. 配置（完整，会强制关闭浏览器）
echo    2. 配置（不改浏览器设置）
echo    3. 恢复（可选快照）
echo    4. 保存当前快照
echo    5. 补充配置（扫描新增规则和网卡）
echo    0. 退出
echo.
choice /C 123450 /N /M "请选择 [1/2/3/4/5/0]: "
if errorlevel 255 goto menu
if errorlevel 6 exit /b 0
if errorlevel 5 goto supplement
if errorlevel 4 goto snapshot
if errorlevel 3 goto restore
if errorlevel 2 goto apply_nobrowser
if errorlevel 1 goto apply
goto menu

:apply
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run.ps1" -Action apply
set "code=%errorlevel%"
if not "%code%"=="0" pause
exit /b %code%

:apply_nobrowser
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run.ps1" -Action apply -SkipBrowser
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
