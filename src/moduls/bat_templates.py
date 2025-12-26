# -*- coding: utf-8 -*-

BAT_TEMPLATES = {'Простой Hello World': {'filename': 'Hello World.bat',
                         'description': 'Простой пример, который выводит строку Hello World и ждёт нажатия клавиши.',
                         'content': '@echo off\necho Hello, World!\npause\n'},
 'Hello World с заголовком окна': {'filename': 'Hello World &Title.bat',
                                   'description': 'Аналог Hello World, но дополнительно задаёт заголовок консольного '
                                                  'окна.',
                                   'content': '@echo off\ntitle Hello World\necho Hello, World!\npause\n'},
 'Вывод даты и времени': {'filename': 'Date & time.bat',
                          'description': 'Показывает текущие системные дату и время с помощью переменных %DATE% и '
                                         '%TIME%.',
                          'content': '@echo off\necho Текущая дата: %DATE%\necho Текущее время: %TIME%\npause\n'},
 'Завершение программы по имени процесса': {'filename': 'Close a program.bat',
                                            'description': 'Запрашивает у пользователя имя процесса (например, '
                                                           'notepad.exe) и завершает его через taskkill.',
                                            'content': '@echo off\n'
                                                       'set /p PROC=Введите имя процесса для завершения (например, '
                                                       'notepad.exe): \n'
                                                       'taskkill /IM "%PROC%" /F\n'
                                                       'pause\n'},
 'Простой логин (демо-авторизация)': {'filename': 'Login 1.bat',
                                      'description': 'Пример простейшей проверки логина и пароля на стороне '
                                                     'BAT-скрипта (значения зашиты внутри файла).',
                                      'content': '@echo off\n'
                                                 'setlocal enabledelayedexpansion\n'
                                                 'set "USER=admin"\n'
                                                 'set "PASS=1234"\n'
                                                 'set /p U=Введите логин: \n'
                                                 'set /p P=Введите пароль: \n'
                                                 'if /I "%U%"=="%USER%" if "%P%"=="%PASS%" (\n'
                                                 '  echo Добро пожаловать!\n'
                                                 ') else (\n'
                                                 '  echo Неверный логин или пароль.\n'
                                                 ')\n'
                                                 'pause\n'
                                                 'endlocal\n'},
 'Изменение размеров консольного окна': {'filename': 'Mode cols & lines.bat',
                                         'description': 'Меняет размеры консольного окна с помощью команды mode con '
                                                        '(количество столбцов и строк).',
                                         'content': '@echo off\n'
                                                    'echo Текущий размер консоли будет изменён на 100 столбцов и 30 '
                                                    'строк.\n'
                                                    'mode con cols=100 lines=30\n'
                                                    'pause\n'},
 'Прогресс-бар в консоли': {'filename': 'Progress bar 1.bat',
                            'description': 'Показывает простой текстовый прогресс-бар в консоли в цикле FOR.',
                            'content': '@echo off\n'
                                       'setlocal enabledelayedexpansion\n'
                                       'set "bar="\n'
                                       'for /L %%i in (1,1,50) do (\n'
                                       '  set "bar=!bar!#"\n'
                                       '  cls\n'
                                       '  echo Загрузка: !bar! %%i%%\n'
                                       '  timeout /t 1 >nul\n'
                                       ')\n'
                                       'echo Готово!\n'
                                       'pause\n'
                                       'endlocal\n'},
 'Запуск программы по пути': {'filename': 'Run a program.bat',
                              'description': 'Запрашивает путь к исполняемому файлу и, если он существует, запускает '
                                             'его командой start.',
                              'content': '@echo off\n'
                                         'set /p APP=Введите полный путь к исполняемому файлу: \n'
                                         'if exist "%APP%" (\n'
                                         '  start "" "%APP%"\n'
                                         ') else (\n'
                                         '  echo Файл не найден.\n'
                                         ')\n'
                                         'pause\n'},
 'Выключение компьютера через таймер': {'filename': 'Shutdown.bat',
                                        'description': 'Планирует выключение компьютера через 60 секунд с помощью '
                                                       'команды shutdown /s /t 60.',
                                        'content': '@echo off\n'
                                                   'echo Компьютер будет выключен через 60 секунд.\n'
                                                   'shutdown /s /t 60\n'
                                                   'pause\n'},
 'Отмена запланированного выключения': {'filename': 'Shutdown cancle.bat',
                                        'description': 'Отменяет запланированное выключение или перезагрузку командой '
                                                       'shutdown /a.',
                                        'content': '@echo off\n'
                                                   'echo Отмена запланированного выключения...\n'
                                                   'shutdown /a\n'
                                                   'pause\n'},
 'Проверка диска (chkdsk)': {'filename': 'Check disk.bat',
                             'description': 'Запускает утилиту chkdsk для проверки выбранного диска. Требует запуска '
                                            'от имени администратора.',
                             'content': '::Check disk\n'
                                        '::Run as Administrator\n'
                                        '@echo OFF\n'
                                        'title Check disk\n'
                                        'color 0A\n'
                                        'chkdsk\n'
                                        'pause >nul\n'},
 'Отчёт по групповой политике (gpresult)': {'filename': 'Gpresult.bat',
                                            'description': 'Получает подробный отчёт по применённым групповым '
                                                           'политикам с помощью команды gpresult. Желательно запускать '
                                                           'от имени администратора.',
                                            'content': '::Gpresult\n'
                                                       '::Run as Administrator\n'
                                                       '@echo OFF\n'
                                                       'title Gpresult\n'
                                                       'color 0A\n'
                                                       'gpresult /z\n'
                                                       'pause >nul\n'},
 'Системная информация (systeminfo)': {'filename': 'System info.bat',
                                       'description': 'Выводит подробную системную информацию (версия ОС, параметры '
                                                      'железа и т.п.) командой systeminfo.',
                                       'content': '::System info\n'
                                                  '@echo OFF\n'
                                                  'title System info\n'
                                                  'color 0A\n'
                                                  'systeminfo\n'
                                                  'pause >nul\n'},
 'Список процессов (tasklist)': {'filename': 'Task list.bat',
                                 'description': 'Показывает список запущенных процессов с помощью команды tasklist.',
                                 'content': '::System info\n'
                                            '@echo OFF\n'
                                            'title System info\n'
                                            'color 0A\n'
                                            'tasklist\n'
                                            'pause >nul\n'},
 'Проверка интернет-подключения (ping)': {'filename': 'Check connection.bat',
                                          'description': 'Проверяет доступность интернета, отправляя один ping на '
                                                         'www.google.com. При успехе пишет, что соединение есть.',
                                          'content': '::Check connection\n'
                                                     '@echo OFF\n'
                                                     'color 0A\n'
                                                     'title Check connection\n'
                                                     'cls\n'
                                                     'echo please wait ...\n'
                                                     '\n'
                                                     'ping -n 1 www.google.com >nul\n'
                                                     'if not errorlevel 1 goto :noerror\n'
                                                     'if errorlevel 1 goto :error\n'
                                                     '\n'
                                                     ':noerror\n'
                                                     'echo Connection successful !\n'
                                                     'pause >nul\n'
                                                     '\n'
                                                     ':error\n'
                                                     'echo No connection :(\n'
                                                     'pause >nul\n'
                                                     '\n'},
 'Проверка порта (netstat / findstr)': {'filename': 'Check port.bat',
                                        'description': 'Показывает список подключений и процессов для проверки '
                                                       'конкретного порта с помощью netstat и findstr.',
                                        'content': '::Check port\n'
                                                   '@echo OFF\n'
                                                   'title Check port\n'
                                                   'color 0A\n'
                                                   'cls\n'
                                                   'echo please wait ...\n'
                                                   'netstat -ano\n'
                                                   'tasklist|findstr "9999"\n'
                                                   'pause >nul'},
 'FTP-сессия (ftp)': {'filename': 'FTP load.bat',
                      'description': 'Запускает встроенный FTP-клиент Windows (ftp). Внутри можно вводить FTP-команды '
                                     'вручную.',
                      'content': '::FTP load\n'
                                 '::Run as Administrator\n'
                                 '::Type HELP for command\n'
                                 '@echo OFF\n'
                                 'title FTP load\n'
                                 'color 0A\n'
                                 'ftp\n'
                                 'exit'},
 'Сетевые настройки (ipconfig /all)': {'filename': 'IP config.bat',
                                       'description': 'Выводит подробную информацию о сетевых интерфейсах и '
                                                      'IP-настройках командой ipconfig /all.',
                                       'content': '::IP config\n'
                                                  '@echo OFF\n'
                                                  'title IP config\n'
                                                  'color 0A\n'
                                                  'ipconfig /all\n'
                                                  'pause > nul'},
 'Диагностика сети (netstat)': {'filename': 'Netstat.bat',
                                'description': 'Запускает набор команд netstat для просмотра соединений, статистики, '
                                               'маршрутов и т.п. Полезно для диагностики сети.',
                                'content': '::Netstat\n'
                                           '::Run as Administrator\n'
                                           '@echo OFF\n'
                                           'title Netstat\n'
                                           'color 0A\n'
                                           'echo please wait ...\n'
                                           '\n'
                                           'netstat -a                         \n'
                                           'netstat -e                           \n'
                                           'netstat -n                           \n'
                                           'netstat -o                           \n'
                                           'netstat -p                           \n'
                                           'netstat -s\n'
                                           'netstat -r\n'
                                           'pause > nul'}}
