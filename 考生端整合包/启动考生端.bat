@echo off
chcp 65001 >nul
title 考试监控客户端
cd /d "%~dp0"
echo ==============================================
echo    考试监控客户端（考生端）启动器
echo ==============================================
echo.
:ASKID
set /p SID=请输入考生学号（如 20260101）: 
if "%SID%"=="" ( echo 学号不能为空 & goto ASKID )
set /p IP=请输入监考端 IP（直接回车 = 本机 127.0.0.1）: 
if "%IP%"=="" set IP=127.0.0.1
echo %IP% | findstr /b /c:"ws" >nul
if %errorlevel%==0 ( set URL=%IP% & goto RUN )
echo %IP% | findstr /c:":" >nul
if %errorlevel%==0 ( set URL=ws://%IP% & goto RUN )
set URL=ws://%IP%:8765
:RUN
echo.
echo 学号=%SID%  监考端=%URL%
client.exe --student-id %SID% --server %URL%
echo.
pause
