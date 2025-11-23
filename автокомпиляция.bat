@echo off
setlocal
chcp 65001 >nul
title Сборка через Nuitka

echo 🚀 Компиляция началась...

REM === Папка проекта ===
set "SCRIPT_DIR=%~dp0"

REM (необязательно) подчистить старые выхлопы
if exist "%SCRIPT_DIR%build" rd /s /q "%SCRIPT_DIR%build"
if exist "%SCRIPT_DIR%dist"  rd /s /q "%SCRIPT_DIR%dist"
if exist "%SCRIPT_DIR%__pycache__" rd /s /q "%SCRIPT_DIR%__pycache__"

REM 🧹 Очистка старого кэша comtypes.gen (обёртки SAPI от текущей системы)
REM Оставляем только __init__.py, чтобы Nuitka не тащил старые модули в EXE
echo 🧹 Очистка кэша comtypes.gen...
python -c "import os,shutil,comtypes; gen_dir=os.path.join(os.path.dirname(comtypes.__file__),'gen'); print('Cleaning comtypes.gen at', gen_dir); [shutil.rmtree(os.path.join(gen_dir,n),ignore_errors=True) if os.path.isdir(os.path.join(gen_dir,n)) else (os.remove(os.path.join(gen_dir,n)) if n not in ('__init__.py','__pycache__') else None) for n in (os.listdir(gen_dir) if os.path.isdir(gen_dir) else [])]"

REM Убедись, что зависимости установлены:
REM pip install pywin32 aiogram aiohttp magic_filter psutil speedtest-cli pyautogui gTTS pyttsx3 edge-tts comtypes pycaw opencv-python numpy sounddevice soundfile py-cpuinfo WMI pydub Pillow mss selenium requests

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
    --include-package=win32com ^
    --include-module=win32com.client ^
    --include-module=win32com.shell.shell ^
    --include-module=pythoncom ^
    --include-module=pywintypes ^
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
    --include-data-file="%SCRIPT_DIR%nircmd-x64\NirCmd.chm=nircmd-x64\NirCmd.chm" ^
    --include-data-file="%SCRIPT_DIR%nircmd-x64\nircmd.exe=nircmd-x64\nircmd.exe" ^
    --include-data-file="%SCRIPT_DIR%nircmd-x64\nircmdc.exe=nircmd-x64\nircmdc.exe" ^
    "%SCRIPT_DIR%src\bot-ok.py"

echo ✅ Готово! Упаковано и отправлено в космос.
echo.
pause
