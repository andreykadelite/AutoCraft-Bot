AutoCraft Bot — это гибридная Telegram/десктоп-утилита на Python (aiogram + PyQt5), которая даёт:
• 
“Мониторинг сервера и сети (CPU/RAM/Disk, IP, скорость)”
• 
“Селфи-скриншоты рабочего стола и хранение последних 500 снимков”
• 
“Режим заметок и приём/отправку любых файлов (до 20 МБ)”
• 
“Управление плагинами с изоляцией в виртуальных окружениях venv”
• 
“Безопасную консоль Python прямо в чате”
• 
“GUI-оболочку с конфигом в config.ini (токен, PIN, список ID
устанавливаем с оф. сайта
 https:/www.python.org/downloads/release/python-3117/
Python 3.11.7
устанавливаем пакеты
python -m pip install aiogram==2.25.1 psutil==5.9.5 speedtest-cli pyautogui PyQt5 requests Pillow

если не хотите компилировать, то запустите bot-ok.py
устанавливаем компилятор
pip install nuitka

войдите в папку с проектом, путь у вас свой, а это мой путь проекта

cd "E:\vscod\tgbot\v4"

после того как вошли в папку с проектом введите одну из команд для компиляции
без консоли
python -m nuitka --standalone --onefile --windows-console-mode=disable --plugin-enable=pyqt5 --include-qt-plugins=sensible --include-package=aiogram --include-package=psutil --include-package=speedtest --include-package=pyautogui "E:\vscod\tgbot\v4\bot-ok.py"

или запустите
компиляция без консоли.bat
 или
компиляция с консолью.bat
они должны быть в папке с проектом где исходники
начальный файл для компиляции bot-ok.py, остальные файлы он сам подхватит
после компиляции у вас появиться bot-ok.exe
рядом с приложением обязательно должен быть Python.zip, он используется для работы консоли Python и плагинов.
Python.zip лежит в папке с проектом.)”#   A u t o C r a f t - B o t 
 
 