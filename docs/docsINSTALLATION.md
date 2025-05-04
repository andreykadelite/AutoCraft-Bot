инструкция для компиляции
если не хотите компилировать, то запустите bot-ok.py

устанавливаем с оф. сайта
 https:/www.python.org/downloads/release/python-3117/
Python 3.11.7
устанавливаем пакеты
ppython -m pip install aiogram==2.25.1 psutil==5.9.5 speedtest-cli pyautogui PyQt5 requests gTTS pyttsx3 Pillow

устанавливаем компилятор
pip install nuitka

войдите в папку с проектом, путь у вас свой, а это мой путь проекта

cd "E:\vscod\tgbot\v4"

после того как вошли в папку с проектом введите команду для компиляции
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
    --include-data-file="E:\vscod\tgbot\v4\ffmpeg-7.1\bin\ffmpeg.exe=ffmpeg.exe" ^
    "bot-ok.py"
внимание!
путь к внешней библеотеки у вас свой, вот пример     --include-data-file="E:\vscod\tgbot\v4\ffmpeg-7.1\bin\ffmpeg.exe=ffmpeg.exe" ^
библеотека в корне проекта ffmpeg-7.1

или запустите
компиляция программы без консольного вывода.bat
путь к внешней библеотеки тоже прописать свой, 

 они должны быть в папке с проектом где исходники
начальный файл для компиляции bot-ok.py, остальные файлы он сам подхватит
после компиляции у вас появиться bot-ok.exe
рядом с приложением обязательно должен быть Python.zip, он используется для работы консоли Python и плагинов.
Python.zip лежит в папке с проектом.)”#   A u t o C r a f t - B o t 
 
 