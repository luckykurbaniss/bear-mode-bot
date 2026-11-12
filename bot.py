import asyncio
import os
import logging
import json
import sqlite3
from datetime import datetime
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo,
    KeyboardButton, ReplyKeyboardMarkup
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
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()
WEB_APP_URL = os.getenv("WEB_APP_URL", "https://luckykurbaniss.github.io/bear-mode-bot/").strip()
DB_PATH = os.getenv("DB_PATH", "bear_mode.db").strip()

SETS = {
    "1": {
        "name": "Сет 1",
        "items": "Гречка с грибами, куриная грудка, салат «Овощной», яйцо отварное, мини-банан, морс, багет"
    },
    "2": {
        "name": "Сет 2",
        "items": "Рис с овощами, филе лосося, салат «Свежесть», омлет, мини-банан, морс, багет"
    },
    "3": {
        "name": "Сет 3",
        "items": "Картофельное пюре, домашняя котлета, салат «Витаминный», омлет, мини-банан, морс, багет"
    },
    "4": {
        "name": "Сет 4",
        "items": "Картофельное пюре, куриное бедро, салат «Свежесть», яйцо отварное, мини-банан, морс, багет"
    },
}
PRICE = 170_000

ORDER_STATUSES = {
    "new": ("🆕 Новый", "Мы получили ваш заказ и скоро свяжемся с вами для подтверждения."),
    "confirmed": ("✅ Подтверждён", "Ваш заказ BEAR MODE подтверждён! Спасибо."),
    "cooking": ("👨‍🍳 Готовится", "Ваш заказ BEAR MODE уже готовится на кухне."),
    "courier": ("🛵 Передан курьеру", "Ваш заказ BEAR MODE передан курьеру и скоро будет у вас."),
    "delivered": ("🎉 Доставлен", "Ваш заказ BEAR MODE отмечен как доставленный. Приятного аппетита!"),
    "cancelled": ("❌ Отменён", "К сожалению, ваш заказ BEAR MODE отменён. Если это ошибка, напишите нам @lucky_ranger."),
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
            (datetime.now().astimezone().isoformat(timespec="seconds"), customer_chat_id,
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
    today = datetime.now().astimezone().date().isoformat()
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
        f"🐻 *ЗАКАЗ BEAR MODE №{order['id']}*\\n\\n"
        f"📌 Статус: {label}\\n"
        f"🕒 Создан: {order['created_at']}\\n"
        f"👤 Имя: {order['customer_name']}\\n"
        f"📱 Телефон: {order['phone']}\\n"
        f"📍 Адрес: {order['address']}\\n"
        f"📅 Доставка: {order['delivery_date'] or 'не указана'} {order['delivery_time'] or ''}\\n"
        f"🍱 Заказ: {order['items_text']}\\n"
        f"💰 Сумма: {order['total']:,} ₫\\n"
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
        text = ("📊 *СТАТИСТИКА BEAR MODE*\\n\\n"
                f"🧾 Всего заказов: *{total}*\\n"
                f"🆕 Новых: *{new_count}*\\n"
                f"📅 Заказов сегодня: *{today_count}*\\n"
                f"💵 Сумма заказов сегодня: *{today_revenue:,} ₫*\\n"
                f"💰 Выручка по подтверждённым: *{revenue:,} ₫*\\n\\n"
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
        await q.edit_message_text(title if orders else title + "\\n\\nЗаказов пока нет.",
                                  parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
        await q.answer()
    elif parts[1] == "order":
        order = get_order(int(parts[2]))
        if not order:
            await q.answer("Заказ не найден.", show_alert=True)
            return
        await q.edit_message_text(order_card_text(order), parse_mode="Markdown",
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
        await q.edit_message_text(order_card_text(order), parse_mode="Markdown",
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
    text = (
        "🐻 *BEAR MODE — еда с характером!*\n\n"
        "Сытные домашние сеты в Нячанге 🇻🇳\n"
        "Выбирай свой вариант, оформляй заказ — приготовим и доставим!\n\n"
        "🔥 4 сета на выбор • 170.000 ₫ за сет"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_keyboard())
    await update.message.reply_text(
        "Или открой полный каталог BEAR MODE и оформи заказ здесь:",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("🌐 Открыть меню и заказать", web_app=WebAppInfo(url=WEB_APP_URL))]],
            resize_keyboard=True
        )
    )
    return ConversationHandler.END

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Ваш Telegram ID: `{update.effective_chat.id}`\n"
        "Скопируйте это число и добавьте его в переменную ADMIN_CHAT_ID в настройках запуска бота.",
        parse_mode="Markdown"
    )

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    if data == "home":
        await q.edit_message_text(
            "🐻 *BEAR MODE*\nВыбирай, что будем делать:",
            parse_mode="Markdown", reply_markup=main_keyboard()
        )
    elif data == "catalog":
        await q.edit_message_text("🍱 *Выбери свой сет:*", parse_mode="Markdown", reply_markup=catalog_keyboard())
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
    elif data == "contact":
        await q.edit_message_text(
            "☎️ Чтобы задать вопрос по заказу, напиши владельцу канала: @lucky_ranger",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ В главное меню", callback_data="home")]])
        )
    return ConversationHandler.END

async def choose_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
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
        "🧾 *Проверьте заказ BEAR MODE*\n\n"
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
        admin_text = f"🧾 Номер заказа: №{order_id}\\n\\n" + admin_text
        await context.bot.send_message(
            chat_id=int(ADMIN_CHAT_ID),
            text=admin_text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📂 Открыть заказ в панели", callback_data=f"adm:order:{order_id}")]])
        )

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
    """Receive an order submitted by the BEAR MODE website via Telegram Web App."""
    message = update.effective_message
    if not message or not message.web_app_data:
        return

    try:
        order = json.loads(message.web_app_data.data)
        if order.get("type") != "bear_mode_order":
            raise ValueError("Unknown order type")

        customer_name = str(order.get("customer_name", "")).strip()
        phone = str(order.get("phone", "")).strip()
        address = str(order.get("address", "")).strip()
        delivery_date = str(order.get("delivery_date", "")).strip()
        delivery_time = str(order.get("delivery_time", "")).strip()
        comment = str(order.get("comment", "")).strip()
        items = order.get("items", [])

        if not all([customer_name, phone, address, delivery_date, delivery_time]):
            raise ValueError("Required customer or delivery fields are missing")
        if not isinstance(items, list) or not items:
            raise ValueError("The order has no items")

        lines = []
        total = 0
        for entry in items:
            set_id = str(entry.get("id", ""))
            if set_id not in SETS:
                raise ValueError("Unknown set")
            quantity = int(entry.get("quantity", 0))
            if quantity < 1 or quantity > 100:
                raise ValueError("Invalid quantity")
            line_total = quantity * PRICE
            total += line_total
            lines.append(f"🍱 {SETS[set_id]['name']} × {quantity} — {line_total:,} ₫".replace(",", "."))

        if not ADMIN_CHAT_ID:
            await message.reply_text(
                "Заказ заполнен, но уведомления владельцу ещё не настроены. "
                "Пожалуйста, напишите @lucky_ranger."
            )
            log.error("Web App order received but ADMIN_CHAT_ID is not configured")
            return

        user = update.effective_user
        admin_text = (
            "🐻 НОВЫЙ ЗАКАЗ С САЙТА BEAR MODE!\n\n"
            + "\n".join(lines)
            + f"\n\n💰 Итого за сеты: {total:,} ₫".replace(",", ".")
            + f"\n\n👤 Имя: {customer_name}"
            + f"\n📱 Телефон: {phone}"
            + f"\n📍 Адрес: {address}"
            + f"\n📅 Дата доставки: {delivery_date}"
            + f"\n🕒 Время доставки: {delivery_time}"
            + f"\n💬 Комментарий: {comment or 'Нет'}"
        )
        if user:
            admin_text += f"\n\nTelegram клиента: {('@' + user.username) if user.username else user.id}"

        items_text = ", ".join(
            f"{SETS[str(entry['id'])]['name']} × {int(entry['quantity'])}"
            for entry in items
        )
        order_id = save_order(
            user.id if user else None, customer_name, phone, address,
            delivery_date, delivery_time, comment, items_text, total
        )
        admin_text = f"🧾 Номер заказа: №{order_id}\\n\\n" + admin_text
        await context.bot.send_message(
            chat_id=int(ADMIN_CHAT_ID),
            text=admin_text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📂 Открыть заказ в панели", callback_data=f"adm:order:{order_id}")]])
        )
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
            CallbackQueryHandler(choose_set, pattern=r"^set_[1-4]$")
        ],
        states={
            QUANTITY: [
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
                CallbackQueryHandler(choose_set, pattern=r"^set_[1-4]$")
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        per_message=False,
    )

    app.add_handler(CommandHandler("start", start))
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

    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    print("BEAR MODE bot is running!")

    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
