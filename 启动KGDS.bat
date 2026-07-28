@echo off
chcp 65001 >nul
title KGDS 知识图谱诊断系统

echo ==========================================
echo   KGDS 知识图谱诊断系统 - 一键启动
echo ==========================================
echo.

cd /d D:\kgds

echo ^>^>^> 检查端口 8081
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8081 ^| findstr LISTENING 2^>nul') do (
    echo    发现旧进程 PID: %%a，正在停止...
    taskkill /PID %%a /F >nul 2>&1
    timeout /t 1 /nobreak >nul
)
echo    端口已就绪

echo ^>^>^> 启动服务器
start /b python server.py > server.log 2>&1
timeout /t 3 /nobreak >nul

curl -s http://localhost:8081/ >nul 2>&1
if %errorlevel% equ 0 (
    echo    启动成功
) else (
    echo    启动失败，查看 server.log
    pause
    exit /b 1
)

echo.
echo ==========================================
echo   用户端:  http://localhost:8081/app.html
echo   管理端:  http://localhost:8081/admin.html
echo ==========================================
echo.
start http://localhost:8081/app.html

echo 按任意键停止服务器...
pause >nul

for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8081 ^| findstr LISTENING 2^>nul') do (
    taskkill /PID %%a /F >nul 2>&1
)
echo 服务器已停止
