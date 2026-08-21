@echo off
chcp 65001 >nul
title 监考中心 - 屏幕监控系统
cd /d "%~dp0"
python server.py
pause
