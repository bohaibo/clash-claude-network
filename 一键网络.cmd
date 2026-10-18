@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Claude 网络环境

:menu
echo.
echo ============================================
echo    Claude 网络环境配置 / 恢复
echo ============================================
echo    1. 配置（apply）
echo    2. 恢复（restore）
echo    0. 退出
echo.
set "choice="
set /p "choice=请选择 [1/2/0]: "
if "%choice%"=="1" goto apply
if "%choice%"=="2" goto restore
if "%choice%"=="0" exit /b 0
echo 无效选择：%choice%
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
