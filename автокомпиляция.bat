@echo off
echo 🚀 Компиляция началась...

REM Определяем папку скрипта
set "SCRIPT_DIR=%~dp0"

python -m nuitka ^
    --standalone ^
    --onefile ^
    --windows-console-mode=disable ^
    --plugin-enable=pyqt5 ^
    --include-qt-plugins=sensible ^
    --include-package=aiogram ^
    --include-package=psutil ^
    --include-package=speedtest ^
    --include-package=pyautogui ^
    --include-package=gtts ^
    --include-package=pyttsx3 ^
    --include-package=comtypes ^
    --include-package=pycaw ^
    --include-package=cv2 ^
    --include-package=numpy ^
    --include-package=sounddevice ^
    --include-package=soundfile ^
    --include-package=cpuinfo ^
    --include-package=wmi ^
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
    "%SCRIPT_DIR%src\bot-ok.py"

echo 🚀 Готово! Упаковано и отправлено в космос!
pause
