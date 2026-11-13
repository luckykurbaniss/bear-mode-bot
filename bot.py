import asyncio
import os
import logging
import json
import sqlite3
import hashlib
import hmac
import time
from urllib.parse import parse_qsl
from aiohttp import web

from datetime import datetime, date
from zoneinfo import ZoneInfo
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, MenuButtonDefault
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ConversationHandler, ContextTypes, filters
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = "5500196056"  # BEAR MODE admin Telegram chat
WEB_APP_URL = os.getenv("WEB_APP_URL", "https://luckykurbaniss.github.io/bear-mode-bot/").strip()
API_PORT = int(os.getenv("PORT", "80"))  # Use the public port assigned by HOKAS; defaults to 80.
ALLOWED_WEB_ORIGIN = "https://luckykurbaniss.github.io"
DB_PATH = os.getenv("DB_PATH", "bear_mode.db").strip()
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

SETS = {
    "1": {
        "name": "Энергия",
        "items": "Гречка с грибами, куриная грудка, салат «Овощной», яйцо отварное, мини-банан, морс, багет"
    },
    "2": {
        "name": "Атлантика",
        "items": "Рис с овощами, филе лосося, салат «Свежесть», омлет, мини-банан, морс, багет"
    },
    "3": {
        "name": "Комфорт",
        "items": "Картофельное пюре, домашние котлеты (2 шт.), салат «Витаминный», омлет, мини-банан, морс, багет"
    },
    "4": {
        "name": "Характер",
        "items": "Картофельное пюре, куриное бедро, салат «Свежесть», яйцо отварное, мини-банан, морс, багет"
    },
}
PRICE = 170_000

ORDER_STATUSES = {
    "new": ("🆕 Новый", "Мы получили ваш заказ и скоро свяжемся с вами для подтверждения."),
    "confirmed": ("✅ Подтверждён", "Ваш заказ подтверждён! Спасибо."),
    "cooking": ("👨‍🍳 Готовится", "Ваш заказ уже готовится на кухне."),
    "courier": ("🛵 Передан курьеру", "Ваш заказ передан курьеру и скоро будет у вас."),
    "delivered": ("🎉 Доставлен", "Ваш заказ отмечен как доставленный. Приятного аппетита!"),
    "cancelled": ("❌ Отменён", "К сожалению, ваш заказ отменён. Если это ошибка, напишите нам @lucky_ranger."),
}


def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_connect() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            customer_chat_id INTEGER,
            customer_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            address TEXT NOT NULL,
            delivery_date TEXT,
            delivery_time TEXT,
            comment TEXT,
            items_text TEXT NOT NULL,
            total INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'new'
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at)")


def save_order(customer_chat_id, customer_name, phone, address, delivery_date, delivery_time, comment, items_text, total):
    with db_connect() as conn:
        cur = conn.execute(
            """INSERT INTO orders
            (created_at, customer_chat_id, customer_name, phone, address, delivery_date, delivery_time, comment, items_text, total, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')""",
            (datetime.now(VN_TZ).isoformat(timespec="seconds"), customer_chat_id,
             customer_name, phone, address, delivery_date, delivery_time, comment, items_text, total)
        )
        return cur.lastrowid


def get_order(order_id):
    with db_connect() as conn:
        return conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()


def get_orders(status="all", limit=8, offset=0):
    with db_connect() as conn:
        if status != "all":
            return conn.execute("SELECT * FROM orders WHERE status = ? ORDER BY id DESC LIMIT ? OFFSET ?",
                                (status, limit, offset)).fetchall()
        return conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT ? OFFSET ?",
                            (limit, offset)).fetchall()


def get_order_stats():
    today = datetime.now(VN_TZ).date().isoformat()
    with db_connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        today_count, today_revenue = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(total),0) FROM orders WHERE substr(created_at,1,10)=? AND status!='cancelled'",
            (today,)
        ).fetchone()
        revenue = conn.execute(
            "SELECT COALESCE(SUM(total),0) FROM orders WHERE status IN ('confirmed','cooking','courier','delivered')"
        ).fetchone()[0]
        new_count = conn.execute("SELECT COUNT(*) FROM orders WHERE status='new'").fetchone()[0]
    return total, today_count, today_revenue, revenue, new_count


def set_order_status(order_id, status):
    with db_connect() as conn:
        conn.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))


def delete_order(order_id):
    """Remove an order only when its notification could not be delivered."""
    with db_connect() as conn:
        conn.execute("DELETE FROM orders WHERE id=? AND status='new'", (order_id,))


def admin_only(update):
    return bool(ADMIN_CHAT_ID) and update.effective_user and str(update.effective_user.id) == ADMIN_CHAT_ID


def admin_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆕 Новые заказы", callback_data="adm:list:new:0"),
         InlineKeyboardButton("📋 Все заказы", callback_data="adm:list:all:0")],
        [InlineKeyboardButton("📊 Статистика и выручка", callback_data="adm:stats")]
    ])


def admin_order_keyboard(order_id, status):
    buttons = [InlineKeyboardButton(label, callback_data=f"adm:set:{order_id}:{key}")
               for key, (label, _) in ORDER_STATUSES.items() if key != status]
    rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("⬅️ К списку", callback_data="adm:list:all:0")])
    return InlineKeyboardMarkup(rows)


def order_card_text(order):
    label = ORDER_STATUSES.get(order["status"], (order["status"], ""))[0]
    return (
        f"🐻 ЗАКАЗ BEAR MODE №{order['id']}\n\n"
        f"📌 Статус: {label}\n"
        f"🕒 Создан: {order['created_at']}\n"
        f"👤 Имя: {order['customer_name']}\n"
        f"📱 Телефон: {order['phone']}\n"
        f"📍 Адрес: {order['address']}\n"
        f"📅 Дата доставки: {order['delivery_date'] or 'не указана'}\n"
        f"🍱 Заказ: {order['items_text']}\n"
        f"💰 Сумма: {order['total']:,} ₫\n"
        f"💬 Комментарий: {order['comment'] or 'Нет'}"
    ).replace(",", ".")


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update):
        await update.message.reply_text("⛔ Эта панель доступна только администратору.")
        return
    await update.message.reply_text("🐻 *BEAR MODE — панель управления*",
                                    parse_mode="Markdown", reply_markup=admin_menu_keyboard())


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not admin_only(update):
        await q.answer("Нет доступа.", show_alert=True)
        return
    parts = q.data.split(":")
    if parts[1] == "stats":
        total, today_count, today_revenue, revenue, new_count = get_order_stats()
        text = ("📊 *СТАТИСТИКА BEAR MODE*\n\n"
                f"🧾 Всего заказов: *{total}*\n"
                f"🆕 Новых: *{new_count}*\n"
                f"📅 Заказов сегодня: *{today_count}*\n"
                f"💵 Сумма заказов сегодня: *{today_revenue:,} ₫*\n"
                f"💰 Выручка по подтверждённым: *{revenue:,} ₫*\n\n"
                "_В выручку включены подтверждённые, готовящиеся, переданные курьеру и доставленные заказы._").replace(",", ".")
        await q.edit_message_text(text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ В меню", callback_data="adm:home")]]))
        await q.answer()
    elif parts[1] == "home":
        await q.edit_message_text("🐻 *BEAR MODE — панель управления*",
                                  parse_mode="Markdown", reply_markup=admin_menu_keyboard())
        await q.answer()
    elif parts[1] == "list":
        status, page = parts[2], int(parts[3])
        orders = get_orders(status, 8, page * 8)
        rows = []
        for order in orders:
            label = ORDER_STATUSES.get(order["status"], (order["status"],))[0]
            rows.append([InlineKeyboardButton(f"№{order['id']} · {order['customer_name'][:16]} · {label}",
                                               callback_data=f"adm:order:{order['id']}")])
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"adm:list:{status}:{page-1}"))
        if len(orders) == 8:
            nav.append(InlineKeyboardButton("Далее ➡️", callback_data=f"adm:list:{status}:{page+1}"))
        if nav:
            rows.append(nav)
        rows.append([InlineKeyboardButton("⬅️ В меню", callback_data="adm:home")])
        title = "🆕 *НОВЫЕ ЗАКАЗЫ*" if status == "new" else "📋 *ВСЕ ЗАКАЗЫ*"
        await q.edit_message_text(title if orders else title + "\n\nЗаказов пока нет.",
                                  parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
        await q.answer()
    elif parts[1] == "order":
        order = get_order(int(parts[2]))
        if not order:
            await q.answer("Заказ не найден.", show_alert=True)
            return
        await q.edit_message_text(order_card_text(order),
                                  reply_markup=admin_order_keyboard(order["id"], order["status"]))
        await q.answer()
    elif parts[1] == "set":
        order_id, status = int(parts[2]), parts[3]
        if status not in ORDER_STATUSES:
            await q.answer("Неизвестный статус.", show_alert=True)
            return
        order = get_order(order_id)
        if not order:
            await q.answer("Заказ не найден.", show_alert=True)
            return
        set_order_status(order_id, status)
        _, customer_message = ORDER_STATUSES[status]
        if order["customer_chat_id"]:
            try:
                await context.bot.send_message(chat_id=order["customer_chat_id"], text=customer_message)
            except Exception:
                log.exception("Could not notify customer about order status")
        order = get_order(order_id)
        await q.edit_message_text(order_card_text(order),
                                  reply_markup=admin_order_keyboard(order_id, status))
        await q.answer("Статус обновлён")


def order_status_keyboard(customer_chat_id):
    buttons = [
        InlineKeyboardButton("🆕 Новый", callback_data=f"bmst:new:{customer_chat_id}"),
        InlineKeyboardButton("✅ Подтверждён", callback_data=f"bmst:confirmed:{customer_chat_id}"),
        InlineKeyboardButton("👨‍🍳 Готовится", callback_data=f"bmst:cooking:{customer_chat_id}"),
        InlineKeyboardButton("🛵 Курьеру", callback_data=f"bmst:courier:{customer_chat_id}"),
        InlineKeyboardButton("🎉 Доставлен", callback_data=f"bmst:delivered:{customer_chat_id}"),
        InlineKeyboardButton("❌ Отменён", callback_data=f"bmst:cancelled:{customer_chat_id}"),
    ]
    return InlineKeyboardMarkup([buttons[:2], buttons[2:4], buttons[4:6]])


# Conversation states
SET, QUANTITY, NAME, PHONE, ADDRESS, COMMENT, CONFIRM = range(7)

def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🍱 Выбрать сет", callback_data="catalog")],
        [InlineKeyboardButton("📦 Как работает доставка", callback_data="delivery")],
        [InlineKeyboardButton("☎️ Связаться", callback_data="contact")],
    ])

def catalog_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Сет {i} — 170.000 ₫", callback_data=f"set_{i}")]
        for i in SETS
    ] + [[InlineKeyboardButton("⬅️ В главное меню", callback_data="home")]])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await remove_catalog_prompt(context)
    await remove_shop_hint(context)
    context.user_data.clear()
    welcome = (
        "🐻 *BEAR MODE — еда с характером!*\n\n"
        "Сытные домашние сеты и отдельные блюда в Нячанге 🇻🇳\n"
        "Выбирай готовый сет или собери свой заказ!\n\n"
        "🔥 Готовые сеты — 170.000 ₫"
    )
    # Reply-keyboard Web App launch supports Telegram.WebApp.sendData().
    # Remove the old persistent blue bottom menu button; keep the reply keyboard shop button.
    # Reset both the bot-wide and this chat's custom menu button.
    await context.bot.set_chat_menu_button(menu_button=MenuButtonDefault())
    await context.bot.set_chat_menu_button(
        chat_id=update.effective_chat.id,
        menu_button=MenuButtonDefault()
    )
    # Version the launch URL so Telegram opens the latest shop page instead of a cached older build.
    shop_url = WEB_APP_URL + ("&" if "?" in WEB_APP_URL else "?") + "v=20261113-3"
    shop_keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("🛒 Открыть магазин", web_app=WebAppInfo(url=shop_url))]],
        resize_keyboard=True,
        is_persistent=True,
    )
    # Attach the persistent shop keyboard to the existing welcome message.
    await update.message.reply_text(
        welcome,
        parse_mode="Markdown",
        reply_markup=shop_keyboard
    )
    await update.message.reply_text(
        "🐼 Выбирай, что будем делать:\n\n"
        "🐾 Магазин всегда под лапкой!\nОткрывайте меню в любое время — кнопка «Открыть магазин» внизу чата.",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )
    return ConversationHandler.END

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Ваш Telegram ID: `{update.effective_chat.id}`\n"
        "Скопируйте это число и добавьте его в переменную ADMIN_CHAT_ID в настройках запуска бота.",
        parse_mode="Markdown"
    )

async def remove_catalog_prompt(context):
    message_id = context.chat_data.pop("catalog_prompt_message_id", None)
    chat_id = context.chat_data.pop("catalog_prompt_chat_id", None)
    if message_id and chat_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            log.debug("Catalog prompt already removed or cannot be deleted")


async def set_shop_hint_visibility(context, visible, chat_id):
    message_id = context.chat_data.get("shop_hint_message_id")
    saved_chat_id = context.chat_data.get("shop_hint_chat_id")

    if not visible:
        if message_id and saved_chat_id:
            try:
                await context.bot.delete_message(
                    chat_id=saved_chat_id,
                    message_id=message_id
                )
            except Exception:
                log.debug("Shop hint already removed or cannot be deleted")
        context.chat_data.pop("shop_hint_message_id", None)
        context.chat_data.pop("shop_hint_chat_id", None)
        return

    # Restore only the temporary hint. The separate Web App keyboard message
    # remains untouched, so the bottom button stays available in every section.
    if message_id and saved_chat_id:
        return
    hint = await context.bot.send_message(
        chat_id=chat_id,
        text="🐾 Магазин всегда под лапкой!\nОткрывайте меню в любое время — кнопка «Открыть магазин» внизу чата."
    )
    context.chat_data["shop_hint_message_id"] = hint.message_id
    context.chat_data["shop_hint_chat_id"] = hint.chat_id


async def remove_shop_hint(context):
    message_id = context.chat_data.pop("shop_hint_message_id", None)
    chat_id = context.chat_data.pop("shop_hint_chat_id", None)
    if message_id and chat_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            log.debug("Shop hint already removed or cannot be deleted")

async def show_catalog_prompt(chat_id, context):
    # Keep exactly one prompt message per chat.
    await remove_catalog_prompt(context)
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text="Нажми кнопку «Магазин» внизу, чтобы открыть каталог и оформить заказ."
    )
    context.chat_data["catalog_prompt_message_id"] = msg.message_id
    context.chat_data["catalog_prompt_chat_id"] = chat_id




async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    # Acknowledge immediately so Telegram stops showing the loading spinner.
    await q.answer()
    data = q.data

    if data == "home":
        await remove_catalog_prompt(context)
        context.user_data.clear()
        await q.edit_message_text(
            "🐼 Выбирай, что будем делать:\n\n"
            "🐾 Магазин всегда под лапкой!\nОткрывайте меню в любое время — кнопка «Открыть магазин» внизу чата.",
            parse_mode="Markdown", reply_markup=main_keyboard()
        )

    elif data in ("catalog", "delivery", "contact"):
        # Show the requested section first; housekeeping runs in the background.
        context.application.create_task(remove_catalog_prompt(context))

        if data == "catalog":
            await q.edit_message_text(
                "🍱 *Выбери свой сет:*",
                parse_mode="Markdown",
                reply_markup=catalog_keyboard()
            )
        elif data == "delivery":
            await q.edit_message_text(
                "🚚 *Доставка BEAR MODE*\n\n"
                "Доставка по Нячангу. Стоимость и время доставки подтвердим после получения адреса.\n"
                "Нажми «Выбрать сет», чтобы оформить заказ.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🍱 Выбрать сет", callback_data="catalog")],
                    [InlineKeyboardButton("⬅️ В главное меню", callback_data="home")]
                ])
            )
        else:
            await q.edit_message_text(
                "☎️ Чтобы задать вопрос по заказу, напиши владельцу канала: @lucky_ranger",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⬅️ В главное меню", callback_data="home")
                ]])
            )

    return ConversationHandler.END

async def choose_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await remove_catalog_prompt(context)
    set_id = q.data.split("_", 1)[1]
    item = SETS[set_id]
    context.user_data.clear()
    context.user_data["set_id"] = set_id
    await q.edit_message_text(
        f"🍱 *{item['name']}*\n\n{item['items']}\n\nЦена: *170.000 ₫*\n\nСколько порций заказать?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(str(n), callback_data=f"qty_{n}") for n in range(1, 5)],
            [InlineKeyboardButton("⬅️ К сетам", callback_data="catalog")]
        ])
    )
    return QUANTITY

async def quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await remove_catalog_prompt(context)
    if q.data == "catalog":
        await q.edit_message_text("🍱 *Выбери свой сет:*", parse_mode="Markdown", reply_markup=catalog_keyboard())
        return SET
    qty = int(q.data.split("_")[1])
    context.user_data["quantity"] = qty
    context.user_data["total"] = qty * PRICE
    await q.edit_message_text(f"Отлично! {qty} порц. — {context.user_data['total']:,} ₫\n\nКак вас зовут?".replace(",", "."))
    return NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text.strip()
    await update.message.reply_text("📱 Напишите номер телефона для связи:")
    return PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["phone"] = update.message.text.strip()
    await update.message.reply_text("📍 Напишите полный адрес доставки в Нячанге (район, улица, дом/квартира):")
    return ADDRESS

async def get_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["address"] = update.message.text.strip()
    await update.message.reply_text("💬 Комментарий к заказу? Если нет — отправьте «Нет».")
    return COMMENT

async def get_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["comment"] = update.message.text.strip()
    sid = context.user_data["set_id"]
    item = SETS[sid]
    qty = context.user_data["quantity"]
    total = context.user_data["total"]
    summary = (
        "🧾 *Проверьте заказ*\n\n"
        f"🍱 {item['name']}\n"
        f"Количество: {qty}\n"
        f"Цена: 170.000 ₫ / порция\n"
        f"*Итого: {total:,} ₫*\n\n".replace(",", ".") +
        f"👤 Имя: {context.user_data['name']}\n"
        f"📱 Телефон: {context.user_data['phone']}\n"
        f"📍 Адрес: {context.user_data['address']}\n"
        f"💬 Комментарий: {context.user_data['comment']}"
    )
    context.user_data["summary"] = summary
    await update.message.reply_text(
        summary, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Подтвердить заказ", callback_data="confirm")],
            [InlineKeyboardButton("❌ Отменить", callback_data="cancel")]
        ])
    )
    return CONFIRM


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "cancel":
        await q.edit_message_text(
            "Заказ отменён. Если захотите заказать — нажмите /start."
        )
        return ConversationHandler.END

    if not ADMIN_CHAT_ID:
        await q.edit_message_text(
            "Заказ заполнен, но получатель заказов не настроен. "
            "Напишите @lucky_ranger."
        )
        return ConversationHandler.END

    user = update.effective_user

    admin_text = (
        "🐻 НОВЫЙ ЗАКАЗ BEAR MODE!\n\n"
        + context.user_data.get("summary", "").replace("*", "")
    )

    if user.username:
        admin_text += f"\n\nTelegram клиента: @{user.username}"
    else:
        admin_text += f"\n\nTelegram ID клиента: {user.id}"

    try:
        items_text = f"{SETS[context.user_data['set_id']]['name']} × {context.user_data['quantity']}"
        order_id = save_order(
            user.id, context.user_data["name"], context.user_data["phone"],
            context.user_data["address"], "", "", context.user_data["comment"],
            items_text, context.user_data["total"]
        )
        admin_text = f"🧾 Номер заказа: №{order_id}\n\n" + admin_text
        try:
            await context.bot.send_message(
                chat_id=int(ADMIN_CHAT_ID),
                text=admin_text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📂 Открыть заказ в панели", callback_data=f"adm:order:{order_id}")]])
            )
        except Exception:
            delete_order(order_id)
            raise

        await q.edit_message_text(
            "🎉 Заказ отправлен! Спасибо!\n\n"
            "Мы скоро свяжемся с вами для подтверждения доставки."
        )

    except Exception:
        log.exception("Failed to forward order")
        await q.edit_message_text(
            "Не удалось отправить заказ автоматически. "
            "Пожалуйста, напишите @lucky_ranger."
        )

    return ConversationHandler.END

  


async def receive_web_app_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Validate and forward set and individual-dish orders from the Mini App."""
    message = update.effective_message
    if not message or not message.web_app_data:
        return

    catalog_prices = {
        "set_1": ("Сет Энергия", PRICE), "set_2": ("Сет Атлантика", PRICE),
        "set_3": ("Сет Комфорт", PRICE), "set_4": ("Сет Характер", PRICE),
        "buckwheat": ("Гречка с грибами", 30000),
        "rice": ("Рис с овощами", 30000),
        "potato": ("Картофельное пюре", 30000),
        "chicken_breast": ("Куриная грудка", 60000),
        "salmon": ("Лосось", 80000),
        "cutlets": ("Домашние котлеты (2 шт.)", 65000),
        "chicken_thigh": ("Куриное бедро", 60000),
        "vegetable_salad": ("Овощной салат", 35000),
        "freshness_salad": ("Салат «Свежесть»", 35000),
        "vitamin_salad": ("Витаминный салат", 35000),
        "omelet": ("Омлет", 20000),
        "egg": ("Яйцо отварное", 15000),
        "banana": ("Мини-банан", 10000),
        "mors": ("Морс", 20000),
        "baguette": ("Багет", 15000),
    }
    try:
        order = json.loads(message.web_app_data.data)
        if order.get("type") != "bear_mode_order":
            raise ValueError("Unknown order type")
        customer_name = str(order.get("customer_name", "")).strip()
        phone = str(order.get("phone", "")).strip()
        address = str(order.get("address", "")).strip()
        delivery_date = str(order.get("delivery_date", "")).strip()
        comment = str(order.get("comment", "")).strip()
        items = order.get("items", [])
        if not all([customer_name, phone, address, delivery_date]):
            raise ValueError("Required customer or delivery fields are missing")
        if len(customer_name) > 80 or len(phone) > 30 or len(address) > 400 or len(comment) > 500:
            raise ValueError("One or more customer fields are too long")
        try:
            requested_date = date.fromisoformat(delivery_date)
        except ValueError as exc:
            raise ValueError("Invalid delivery date") from exc
        if requested_date < datetime.now(VN_TZ).date():
            raise ValueError("Delivery date cannot be in the past")
        if not isinstance(items, list) or not 1 <= len(items) <= len(catalog_prices):
            raise ValueError("The order must contain at least one valid item")

        lines, saved_items, total, seen_ids = [], [], 0, set()
        for entry in items:
            if not isinstance(entry, dict):
                raise ValueError("Invalid order item")
            item_id = str(entry.get("id", ""))
            if item_id not in catalog_prices or item_id in seen_ids:
                raise ValueError("Unknown or repeated catalog item")
            seen_ids.add(item_id)
            quantity = int(entry.get("quantity", 0))
            if quantity < 1 or quantity > 100:
                raise ValueError("Invalid quantity")
            item_name, unit_price = catalog_prices[item_id]
            line_total = quantity * unit_price
            total += line_total
            lines.append(f"• {item_name} × {quantity} — {line_total:,} ₫".replace(",", "."))
            saved_items.append(f"{item_name} × {quantity}")

        if not ADMIN_CHAT_ID:
            await message.reply_text(
                "Заказ заполнен, но уведомления владельцу ещё не настроены. "
                "Пожалуйста, напишите @lucky_ranger."
            )
            log.error("Web App order received but ADMIN_CHAT_ID is not configured")
            return

        user = update.effective_user
        admin_text = (
            "🐻 НОВЫЙ ЗАКАЗ САЙТА!\n\n"
            + "\n".join(lines)
            + f"\n\n💰 ИТОГО: {total:,} ₫".replace(",", ".")
            + f"\n\n👤 Имя: {customer_name}"
            + f"\n📱 Телефон: {phone}"
            + f"\n📍 Адрес: {address}"
            + f"\n📅 Дата доставки: {delivery_date}"
            + f"\n💬 Комментарий: {comment or 'Нет'}"
        )
        if user:
            admin_text += f"\n\nTelegram клиента: {('@' + user.username) if user.username else user.id}"

        order_id = save_order(
            user.id if user else None, customer_name, phone, address,
            delivery_date, "", comment, ", ".join(saved_items), total
        )
        admin_text = f"🧾 Номер заказа: №{order_id}\n\n" + admin_text
        try:
            await context.bot.send_message(
                chat_id=int(ADMIN_CHAT_ID),
                text=admin_text,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📂 Открыть заказ в панели", callback_data=f"adm:order:{order_id}")
                ]])
            )
        except Exception:
            delete_order(order_id)
            raise
        await message.reply_text(
            "🎉 Заказ отправлен! Спасибо!\n"
            "Мы скоро свяжемся с вами для подтверждения доставки."
        )
    except (ValueError, TypeError, json.JSONDecodeError):
        log.exception("Invalid order received from website")
        await message.reply_text(
            "Не удалось обработать заказ. Проверьте данные и попробуйте ещё раз "
            "или напишите @lucky_ranger."
        )
    except Exception:
        log.exception("Failed to forward website order")
        await message.reply_text(
            "Не удалось отправить заказ автоматически. Пожалуйста, напишите @lucky_ranger."
        )



def validate_telegram_init_data(init_data: str):
    """Validate Telegram Mini App initData and return its authenticated user."""
    if not init_data or len(init_data) > 10000:
        raise ValueError("Missing Telegram initData")
    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", "")
    if not received_hash:
        raise ValueError("Missing initData hash")
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated_hash, received_hash):
        raise ValueError("Invalid Telegram initData signature")
    auth_date = int(parsed.get("auth_date", "0"))
    if auth_date <= 0 or time.time() - auth_date > 86400 or auth_date - time.time() > 60:
        raise ValueError("Expired Telegram initData")
    user = json.loads(parsed.get("user", "{}"))
    if not user.get("id"):
        raise ValueError("Telegram user is missing")
    return user


async def health_api(request):
    return web.json_response({"ok": True, "service": "BEAR MODE order API", "port": API_PORT})


async def order_api(request):
    if request.method == "OPTIONS":
        return web.Response(status=204, headers={
            "Access-Control-Allow-Origin": ALLOWED_WEB_ORIGIN,
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, X-Telegram-Init-Data",
            "Vary": "Origin",
        })
    headers = {
        "Access-Control-Allow-Origin": ALLOWED_WEB_ORIGIN,
        "Vary": "Origin",
        "Content-Type": "application/json",
    }
    if request.headers.get("Origin") != ALLOWED_WEB_ORIGIN:
        return web.json_response({"ok": False, "error": "Origin not allowed"}, status=403, headers=headers)
    try:
        user = validate_telegram_init_data(request.headers.get("X-Telegram-Init-Data", ""))
        order = await request.json()
        if order.get("type") != "bear_mode_order":
            raise ValueError("Unknown order type")
        customer_name = str(order.get("customer_name", "")).strip()
        phone = str(order.get("phone", "")).strip()
        address = str(order.get("address", "")).strip()
        delivery_date = str(order.get("delivery_date", "")).strip()
        comment = str(order.get("comment", "")).strip()
        items = order.get("items", [])
        if not all([customer_name, phone, address, delivery_date]):
            raise ValueError("Заполните имя, телефон, адрес и дату доставки.")
        if len(customer_name) > 80 or len(phone) > 30 or len(address) > 400 or len(comment) > 500:
            raise ValueError("Слишком длинные данные.")
        requested_date = date.fromisoformat(delivery_date)
        if requested_date < datetime.now(VN_TZ).date():
            raise ValueError("Дата доставки не может быть в прошлом.")
        catalog_prices = {
            "set_1": ("Сет Энергия", PRICE), "set_2": ("Сет Атлантика", PRICE),
            "set_3": ("Сет Комфорт", PRICE), "set_4": ("Сет Характер", PRICE),
            "buckwheat": ("Гречка с грибами", 30000), "rice": ("Рис с овощами", 30000),
            "potato": ("Картофельное пюре", 30000), "chicken_breast": ("Куриная грудка", 60000),
            "salmon": ("Лосось", 80000), "cutlets": ("Домашние котлеты (2 шт.)", 65000),
            "chicken_thigh": ("Куриное бедро", 60000), "vegetable_salad": ("Овощной салат", 35000),
            "freshness_salad": ("Салат «Свежесть»", 35000), "vitamin_salad": ("Витаминный салат", 35000),
            "omelet": ("Омлет", 20000), "egg": ("Яйцо отварное", 15000),
            "banana": ("Мини-банан", 10000), "mors": ("Морс", 20000), "baguette": ("Багет", 15000),
        }
        if not isinstance(items, list) or not 1 <= len(items) <= len(catalog_prices):
            raise ValueError("Корзина пуста или содержит слишком много позиций.")
        lines, saved_items, total, seen = [], [], 0, set()
        for entry in items:
            item_id = str(entry.get("id", ""))
            quantity = int(entry.get("quantity", 0))
            if item_id not in catalog_prices or item_id in seen or not 1 <= quantity <= 100:
                raise ValueError("Некорректная позиция заказа.")
            seen.add(item_id)
            name, price = catalog_prices[item_id]
            amount = quantity * price
            total += amount
            lines.append(f"• {name} × {quantity} — {amount:,} ₫".replace(",", "."))
            saved_items.append(f"{name} × {quantity}")
        order_id = save_order(
            int(user["id"]), customer_name, phone, address, delivery_date,
            "", comment, ", ".join(saved_items), total
        )
        admin_text = (
            f"🧾 НОВЫЙ ЗАКАЗ №{order_id}\n\n" + "\n".join(lines)
            + f"\n\n💰 ИТОГО: {total:,} ₫".replace(",", ".")
            + f"\n\n👤 Имя: {customer_name}\n📱 Телефон: {phone}"
            + f"\n📍 Адрес: {address}\n📅 Дата доставки: {delivery_date}"
            + f"\n💬 Комментарий: {comment or 'Нет'}"
            + f"\n\nTelegram клиента: {('@' + user['username']) if user.get('username') else user['id']}"
        )
        try:
            await request.app["bot"].send_message(
                chat_id=int(ADMIN_CHAT_ID), text=admin_text,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📂 Открыть заказ в панели", callback_data=f"adm:order:{order_id}")
                ]])
            )
            await request.app["bot"].send_message(
                chat_id=int(user["id"]),
                text=f"🎉 Заказ №{order_id} принят! Спасибо. Скоро свяжемся для подтверждения доставки."
            )
        except Exception:
            delete_order(order_id)
            raise
        return web.json_response({"ok": True, "order_id": order_id}, headers=headers)
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400, headers=headers)
    except Exception:
        log.exception("Mini App order API failed")
        return web.json_response({"ok": False, "error": "Не удалось отправить заказ. Напишите @lucky_ranger."}, status=500, headers=headers)


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Оформление остановлено. Чтобы начать заново, нажмите /start.")
    return ConversationHandler.END


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Set BOT_TOKEN in your environment variables.")

    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(choose_set, pattern=r"^set_[1-4]$")
        ],
        states={
            QUANTITY: [
                CallbackQueryHandler(
                    menu_callback, pattern=r"^home$"
                ),
                CallbackQueryHandler(
                    quantity, pattern=r"^(qty_[1-4]|catalog)$"
                )
            ],
            NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)
            ],
            PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)
            ],
            ADDRESS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_address)
            ],
            COMMENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_comment)
            ],
            CONFIRM: [
                CallbackQueryHandler(confirm, pattern=r"^(confirm|cancel)$")
            ],
            SET: [
                CallbackQueryHandler(
                    menu_callback, pattern=r"^home$"
                ),
                CallbackQueryHandler(choose_set, pattern=r"^set_[1-4]$")
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        per_message=False,
        allow_reentry=True,
    )

    init_db()
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern=r"^adm:"))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, receive_web_app_order))
    app.add_handler(conv)
    app.add_handler(
        CallbackQueryHandler(
            menu_callback,
            pattern=r"^(catalog|delivery|contact|home)$"
        )
    )
    app.add_handler(
        CallbackQueryHandler(choose_set, pattern=r"^set_[1-4]$")
    )

    print("BEAR MODE: starting Telegram bot...", flush=True)
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    print("BEAR MODE: Telegram polling started.", flush=True)

    # Orders are delivered by Telegram Web App sendData; no HTTP order API is started.
    print("BEAR MODE: startup complete; Telegram bot is waiting for Web App orders.", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
