@echo off
echo 🚀 Компиляция началась...

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
    --include-data-file="E:\vscod\tgbot\v4\ffmpeg-7.1\bin\ffmpeg.exe=ffmpeg.exe" ^
    "bot-ok.py"

echo 🚀 Готово! Упаковано и отправлено в космос!
pause
