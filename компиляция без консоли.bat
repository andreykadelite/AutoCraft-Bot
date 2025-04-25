@echo off
echo Компиляция началась...
python -m nuitka --standalone --onefile --windows-console-mode=disable --plugin-enable=pyqt5 --include-qt-plugins=sensible --include-package=aiogram --include-package=psutil --include-package=speedtest --include-package=pyautogui "bot-ok.py"
pause
