@echo off
setlocal
chcp 65001 >nul
title Сборка через Nuitka

echo 🚀 Компиляция началась...

set "SCRIPT_DIR=%~dp0"
set "PYTHONPATH=%SCRIPT_DIR%src\moduls;%SCRIPT_DIR%src\gui_win;%SCRIPT_DIR%src\sys_core;%PYTHONPATH%"

if exist "%SCRIPT_DIR%build" rd /s /q "%SCRIPT_DIR%build"
if exist "%SCRIPT_DIR%dist"  rd /s /q "%SCRIPT_DIR%dist"
if exist "%SCRIPT_DIR%__pycache__" rd /s /q "%SCRIPT_DIR%__pycache__"

echo 🧹 Очистка кэша comtypes.gen...
python -c "import os,shutil,comtypes; gen_dir=os.path.join(os.path.dirname(comtypes.__file__),'gen'); print('Cleaning comtypes.gen at', gen_dir); [shutil.rmtree(os.path.join(gen_dir,n),ignore_errors=True) if os.path.isdir(os.path.join(gen_dir,n)) else (os.remove(os.path.join(gen_dir,n)) if n not in ('__init__.py','__pycache__') else None) for n in (os.listdir(gen_dir) if os.path.isdir(gen_dir) else [])]"

if not exist "%SCRIPT_DIR%src\moduls\web_dashboard\." (
  echo ❌ Папка не найдена: "%SCRIPT_DIR%src\moduls\web_dashboard"
  pause
  exit /b 1
)

if not exist "%SCRIPT_DIR%src\sys_core\." (
  echo ❌ Папка не найдена: "%SCRIPT_DIR%src\sys_core"
  pause
  exit /b 1
)

python -m nuitka ^
  --standalone ^
  --onefile ^
  --windows-console-mode=hide ^
  --plugin-enable=pyqt5 ^
  --include-qt-plugins=sensible ^
  --include-package=aiogram ^
  --include-package=aiohttp ^
  --include-package=magic_filter ^
  --include-package=psutil ^
  --include-package=speedtest ^
  --include-package=pyautogui ^
  --include-package=gtts ^
  --include-package=pyttsx3 ^
  --include-package=edge_tts ^
  --include-package=comtypes ^
  --include-package=pycaw ^
  --include-package=cv2 ^
  --include-package=numpy ^
  --include-package=sounddevice ^
  --include-package=soundfile ^
  --include-package=cpuinfo ^
  --include-package=wmi ^
  --include-package=pydub ^
  --include-package=PIL ^
  --include-package=mss ^
  --include-package=selenium ^
  --include-package=requests ^
  --include-package=flask ^
  --include-package=fastapi ^
  --include-package=uvicorn ^
  --include-package=qasync ^
  --include-package=websocket ^
  --include-package=win32com ^
  --include-module=win32com.client ^
  --include-module=win32com.shell.shell ^
  --include-module=pythoncom ^
  --include-module=pywintypes ^
  --include-package=flask_appbuilder ^
  --include-package=flask_migrate ^
  --include-package=flask_wtf ^
  --include-package=waitress ^
  --include-package=apscheduler ^
  --include-package=passlib ^
  --include-package=bcrypt ^
  --include-package=sqlalchemy ^
  --include-package=alembic ^
  --include-data-file="%SCRIPT_DIR%ffmpeg-7.1\bin\ffmpeg.exe=ffmpeg.exe" ^
  --include-data-file="%SCRIPT_DIR%ffmpeg-7.1\bin\swscale-8.dll=swscale-8.dll" ^
  --include-data-file="%SCRIPT_DIR%ffmpeg-7.1\bin\swresample-5.dll=swresample-5.dll" ^
  --include-data-file="%SCRIPT_DIR%ffmpeg-7.1\bin\postproc-58.dll=postproc-58.dll" ^
  --include-data-file="%SCRIPT_DIR%ffmpeg-7.1\bin\ffprobe.exe=ffprobe.exe" ^
  --include-data-file="%SCRIPT_DIR%ffmpeg-7.1\bin\ffplay.exe=ffplay.exe" ^
  --include-data-file="%SCRIPT_DIR%ffmpeg-7.1\bin\avutil-59.dll=avutil-59.dll" ^
  --include-data-file="%SCRIPT_DIR%ffmpeg-7.1\bin\avformat-61.dll=avformat-61.dll" ^
  --include-data-file="%SCRIPT_DIR%ffmpeg-7.1\bin\avfilter-10.dll=avfilter-10.dll" ^
  --include-data-file="%SCRIPT_DIR%ffmpeg-7.1\bin\avdevice-61.dll=avdevice-61.dll" ^
  --include-data-file="%SCRIPT_DIR%ffmpeg-7.1\bin\avcodec-61.dll=avcodec-61.dll" ^
  --include-data-file="%SCRIPT_DIR%serverapibot.zip=serverapibot.zip" ^
  --include-data-file="%SCRIPT_DIR%Python.zip=Python.zip" ^
  --include-data-file="%SCRIPT_DIR%LibreHardwareMonitor.NET.10.zip=LibreHardwareMonitor.NET.10.zip" ^
  --include-data-file="%SCRIPT_DIR%nircmd-x64.zip=nircmd-x64.zip" ^
  --include-package=moduls ^
  --include-data-dir="%SCRIPT_DIR%src\moduls\web_dashboard=moduls\web_dashboard" ^
  --include-package=network_module ^
  --include-package=gui_win ^
  --include-package=sys_core ^
  --include-data-dir="%SCRIPT_DIR%src\sys_core=sys_core" ^
  --include-plugin-directory="%SCRIPT_DIR%src\moduls" ^
  --include-plugin-directory="%SCRIPT_DIR%src\gui_win" ^
  --include-plugin-directory="%SCRIPT_DIR%src\sys_core" ^
  "%SCRIPT_DIR%src\bot-ok.py"

if errorlevel 1 (
  echo ❌ Сборка упала. Смотри ошибки выше.
  pause
  exit /b 1
)

echo ✅ Готово! Упаковано и отправлено в космос.
echo.
pause