1. Общая структура плагина
• 
Папка плагина
Создайте отдельную папку в каталоге plugins. Имя папки должно точно соответствовать идентификатору плагина, например my_plugin.
• 
Файл метаданных (JSON)
В папке плагина создайте файл <plugin_id>.json, где <plugin_id> — имя папки и идентификатор.
Минимальное содержание файла:
json
Копировать
Редактировать
{
  "name": "<plugin_id>",
  "dependencies": [
    "<package1>",
    "<package2>",
    ...
  ]
}
Пример правильного net_scanner.json для плагина сканера сети:
json
Копировать
Редактировать
{
  "name": "net_scanner",
  "dependencies": [
    "python-nmap"
  ]
}
• 
Код плагина (.py)
Основной код располагается в одном или нескольких .py. Обязательный файл содержит функцию init_plugin(dp: Dispatcher).
 
2. Обязательные функции и точки входа
2.1. Инициализация
python
Копировать
Редактировать
def init_plugin(dp: Dispatcher):
    @dp.message_handler(lambda m: m.text == "Запустить мой плагин")
    async def start_handler(message: types.Message):
        await run_plugin(message)

    @dp.message_handler(lambda m: is_plugin_active(m) and m.text == "Закрыть плагин")
    async def close_handler(message: types.Message):
        await close_plugin(message)

    @dp.message_handler(lambda m: is_plugin_active(m))
    async def plugin_handler(message: types.Message):
        # основная логика по состоянию
        ...
2.2. Запуск плагина
python
Копировать
Редактировать
async def run_plugin(message: types.Message):
    user_id = message.from_user.id
    plugin_state[user_id] = {"active": True, "state": STATE_MAIN}
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Функция 1", "Функция 2", "Закрыть плагин")
    await message.answer("Плагин запущен. Выбирай действие:", reply_markup=kb)
2.3. Завершение плагина и возврат в главное меню
Важно: не вызывайте await keymenu(message), а берите готовую клавиатуру из keymenu.get_main_keyboard() и передавайте её в reply_markup.
python
Копировать
Редактировать
import keymenu

async def close_plugin(message: types.Message):
    """
    Завершает работу плагина:
      - Сбрасывает состояние
      - Выводит главное меню приложения через keymenu.get_main_keyboard()
    """
    user_id = message.from_user.id
    plugin_state.pop(user_id, None)

    # Получаем основную клавиатуру из keymenu
    kb = keymenu.get_main_keyboard()

    await message.answer(
        "Плагин завершён. Возвращаемся в главное меню.",
        reply_markup=kb
    )
 
2.4. Проверка активности
python
Копировать
Редактировать
def is_plugin_active(message: types.Message) -> bool:
    return plugin_state.get(message.from_user.id, {}).get("active", False)
 
3. Состояния и режимы работы
• 
Используйте глобальный plugin_state = { user_id: {"active": True, "state": STATE_MAIN} }.
• 
Типовые состояния:
• 
STATE_MAIN — главное меню плагина.
• 
STATE_INPUT (или своё) — ожидание ввода пользователя.
• 
Меняйте plugin_state[user_id]["state"] в зависимости от шагов.
 
4. Клавиатуры
• 
Меню плагина: types.ReplyKeyboardMarkup(resize_keyboard=True)
• 
Кнопки: основные функции + «Закрыть плагин».
• 
Возврат в меню приложения: получайте разметку через keymenu.get_main_keyboard() (см. п. 2.3), а не собирайте вручную.
 
5. Интеграция и изоляция
• 
Плагин работает в изолированном виртуальном окружении, созданном на основе <plugin_id>.json.
• 
Для сторонних пакетов — перечислите их в dependencies.
• 
Логируйте через свою утилиту write_plugin_log, если есть.
 
6. Асинхронность и длительные операции
• 
Все точки входа — async def.
• 
Для тяжёлых задач используйте asyncio.to_thread() или обёртки типа long_operation_wrapper.
 
7. Шаблон реализации (my_plugin.py)
python
Копировать
Редактировать
from aiogram import types, Dispatcher
import keymenu

plugin_state = {}
STATE_MAIN  = "MAIN"
STATE_INPUT = "INPUT"

def init_plugin(dp: Dispatcher):
    @dp.message_handler(lambda m: m.text == "Запустить мой плагин")
    async def start_handler(message: types.Message):
        await run_plugin(message)

    @dp.message_handler(lambda m: is_plugin_active(m) and m.text == "Закрыть плагин")
    async def close_handler(message: types.Message):
        await close_plugin(message)

    @dp.message_handler(lambda m: is_plugin_active(m))
    async def plugin_handler(message: types.Message):
        user_id = message.from_user.id
        state = plugin_state[user_id]["state"]
        if state == STATE_MAIN:
            if message.text == "Функция 1":
                await message.answer("Выполняется Функция 1...")
            else:
                await message.answer("Неверная команда.", reply_markup=main_keyboard())
        elif state == STATE_INPUT:
            # обработка ввода
            plugin_state[user_id]["state"] = STATE_MAIN
            await message.answer("Готово!", reply_markup=main_keyboard())

def is_plugin_active(message: types.Message) -> bool:
    return plugin_state.get(message.from_user.id, {}).get("active", False)

async def run_plugin(message: types.Message):
    user_id = message.from_user.id
    plugin_state[user_id] = {"active": True, "state": STATE_MAIN}
    await message.answer("Выберите функцию:", reply_markup=main_keyboard())

def main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Функция 1", "Функция 2", "Закрыть плагин")
    return kb

async def close_plugin(message: types.Message):
    user_id = message.from_user.id
    plugin_state.pop(user_id, None)
    kb = keymenu.get_main_keyboard()
    await message.answer("Плагин завершён. Возвращаемся в главное меню.", reply_markup=kb)
 