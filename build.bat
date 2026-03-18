@echo off
chcp 65001 > nul
echo ========================================
echo    Сборка Kontrollka в EXE
echo ========================================
echo.

call .venv\Scripts\activate

echo Запуск PyInstaller...

pyinstaller --onefile ^
  --add-data "templates;templates" ^
  --add-data "scripts;scripts" ^
  --add-data "device_settings.json;." ^
  --hidden-import flask ^
  --hidden-import netmiko ^
  --hidden-import logging ^
  --hidden-import threading ^
  --hidden-import datetime ^
  --hidden-import re ^
  --hidden-import time ^
  --hidden-import io ^
  --hidden-import json ^
  --hidden-import os ^
  --hidden-import ssl ^
  --hidden-import socket ^
  --hidden-import functools ^
  --hidden-import database ^
  --hidden-import shutil ^
  --hidden-import werkzeug.utils ^
  --name Kontrollka ^
  app.py

if %errorlevel% equ 0 (
    echo ========================================
    echo    ✅ Сборка успешно завершена!
    echo    📁 Файл: dist\Kontrollka.exe
    echo ========================================
) else (
    echo ========================================
    echo    ❌ ОШИБКА сборки!
    echo    Смотри вывод выше
    echo ========================================
)

pause