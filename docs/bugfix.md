# Исправления

- Ограничена команда `hrp` — теперь её могут вызывать только авторизованные пользователи.
- Приведён к единому виду процесс авторизации по PIN-коду:
  - Отправляется сообщение "Вы авторизовались",
  - Выводятся накопленные логи (если есть),
  - Показывается статус дебага.

Поведение авторизации стало стабильным и предсказуемым.
