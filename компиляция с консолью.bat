@echo off
echo === Компиляция начинается ===
echo.

set SCRIPT=bot-ok.py

python -m nuitka --standalone --onefile ^
--plugin-enable=pyqt5 ^
--include-qt-plugins=sensible ^
--include-package=aiogram ^
--include-package=psutil ^
--include-package=speedtest ^
--include-package=pyautogui "%SCRIPT%"

if errorlevel 1 (
    echo ❌ Ошибка компиляции! Проверь выше.
) else (
    echo ✅ Компиляция прошла успешно.
    
    REM Проверим, существует ли exe
    if exist bot_ok.exe (
        echo 🔄 Запуск скомпилированной программы:
        bot_ok.exe
    ) else (
        echo ⚠️ Не найден bot_ok.exe. Возможно, Nuitka дало другое имя или файл не создался.
    )
)

echo.
pause
