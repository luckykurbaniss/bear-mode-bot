# BEAR MODE Telegram bot

## Архитектура

- Магазин — статический сайт GitHub Pages: https://luckykurbaniss.github.io/bear-mode-bot/
- Telegram-бот — отдельный постоянно работающий Python worker.
- Текущий рабочий путь заказа из магазина: Telegram Web App `sendData` → обработчик `web_app_data` в `bot.py` → проверка состава и цен на сервере → SQLite → сообщение администратору.
- База заказов хранится по пути из `DB_PATH`. В Railway используется постоянный том, смонтированный в `/data`, и `DB_PATH=/data/bear_mode.db`.
- Статусы GrabExpress и COD ведутся вручную в панели администратора. Официальной автоматической интеграции GrabExpress API в этом проекте нет.

## Настройка

Требуются Python 3.10+ и зависимости из `requirements.txt`.

Обязательные переменные окружения:

- `BOT_TOKEN` — токен от @BotFather. Не помещайте его в репозиторий и не отправляйте в сообщения.
- `ADMIN_CHAT_ID` — числовой Telegram ID администратора. Без него бот не должен запускаться.

Дополнительные переменные:

- `WEB_APP_URL` — адрес магазина; по умолчанию используется GitHub Pages выше.
- `DB_PATH` — путь к SQLite; по умолчанию `bear_mode.db`. На сервере обязательно используйте постоянный том.

Команда запуска: `python bot.py`.

## Проверка

1. Откройте бота и нажмите Start.
2. Проверьте команду `/ping`.
3. Откройте магазин только кнопкой «Открыть магазин» в чате с ботом.
4. Создайте тестовый заказ и проверьте, что он появился у администратора.
5. Откройте карточку заказа, проверьте статусы, Grab ID и COD.
6. После теста проверьте, что заказ сохранён после перезапуска сервиса.

## Важные замечания

- Магазин передаёт заказ через Telegram `sendData`. Это не HTTP-запрос к HOKAS.
- В `bot.py` сохранены вспомогательные HTTP API-функции с проверкой Telegram `initData`, но текущий запуск их не регистрирует и не использует. Не считайте их работающим endpoint.
- Telegram `sendData` доступен при запуске Mini App из подходящей кнопки Web App в чате с ботом; обычное открытие сайта в браузере не является рабочим способом отправки заказа.
- Сервер пересчитывает итог по собственному каталогу, а не доверяет цене, переданной браузером.
- Изменения каталога нужно синхронно вносить в данные сайта и серверный список цен в `bot.py`.
- Перед изменениями базы данных делайте отдельную резервную копию SQLite-файла.


## Customer support automation

- Each order card has a "Ответить клиенту" action for the administrator.
- The administrator selects a reply category and a saved template, previews it, can replace the text by replying to the preview, and sends it to the customer only after pressing the send button.
- Customer FAQs are answered automatically for known topics: menu/prices, delivery policy, payment methods, greeting and general food-composition requests.
- Requests about order changes, cancellations, allergies, complaints and other unknown topics are forwarded to the administrator with a direct reply action.
- Unknown questions are not guessed; the bot acknowledges receipt and escalates them to the administrator.
- The current automatic FAQ matcher is deterministic and does not require an external AI API key. It must not be described as a generative AI assistant.
- The support feature does not change order statuses or the existing automatic order-status notifications.
