import asyncio
import os
import logging
import sqlite3

from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ConversationHandler, ContextTypes, filters
)
from delivery import init_delivery_db, delivery_admin_callback, delivery_reference_message

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
# HTTP client logs include full Telegram API URLs; suppress them to avoid
# exposing the bot token embedded in those URLs in deployment logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()
DB_PATH = os.getenv("DB_PATH", "bear_mode.db").strip()
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

SETS = {
    "1": {
        "name": "Энергия",
        "items": "Гречка с грибами, куриная грудка, салат «Овощной», яйцо отварное, морс, багет"
    },
    "2": {
        "name": "Атлантика",
        "items": "Рис с овощами, филе лосося, салат «Свежесть», омлет, морс, багет"
    },
    "3": {
        "name": "Комфорт",
        "items": "Картофельное пюре, домашние котлеты (2 шт.), салат «Витаминный», омлет, морс, багет"
    },
    "4": {
        "name": "Характер",
        "items": "Картофельное пюре, куриное бедро, салат «Свежесть», яйцо отварное, морс, багет"
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



def backup_database():
    """Create a consistent SQLite backup and retain the latest 14 copies."""
    source = Path(DB_PATH)
    if not source.is_file():
        raise FileNotFoundError(f"Database file not found: {source}")

    backup_dir = Path(os.getenv("DB_BACKUP_DIR", str(source.parent / "backups")))
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(VN_TZ).strftime("%Y%m%d_%H%M%S")
    destination = backup_dir / f"bear_mode_{stamp}.db"
    temporary = backup_dir / f".bear_mode_{stamp}.tmp"

    try:
        with sqlite3.connect(str(source), timeout=30) as src:
            with sqlite3.connect(str(temporary), timeout=30) as dst:
                src.backup(dst)
                check = dst.execute("PRAGMA integrity_check").fetchone()[0]
                if check != "ok":
                    raise RuntimeError(f"SQLite backup integrity check failed: {check}")
        os.replace(temporary, destination)

        backups = sorted(
            backup_dir.glob("bear_mode_*.db"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for old_backup in backups[14:]:
            old_backup.unlink(missing_ok=True)

        log.info("SQLite backup created and verified: %s (%s bytes)", destination.name, destination.stat().st_size)
        return str(destination)
    finally:
        temporary.unlink(missing_ok=True)



def verify_latest_backup_restore():
    """Restore the newest backup to a temporary DB and compare core order data."""
    backup_dir = Path(os.getenv("DB_BACKUP_DIR", str(Path(DB_PATH).parent / "backups")))
    backups = sorted(
        backup_dir.glob("bear_mode_*.db"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not backups:
        raise FileNotFoundError("No automatic backup files found")

    source_backup = backups[0]
    live_db = Path(DB_PATH)
    test_restore = backup_dir / ".restore_verification.tmp"

    try:
        with sqlite3.connect(str(source_backup), timeout=30) as src:
            with sqlite3.connect(str(test_restore), timeout=30) as dst:
                src.backup(dst)
                integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise RuntimeError(f"Restored copy integrity check failed: {integrity}")
                restored_tables = {
                    row[0] for row in dst.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                if "orders" not in restored_tables:
                    raise RuntimeError("Restored copy does not contain the orders table")
                restored_count, restored_max_id = dst.execute(
                    "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM orders"
                ).fetchone()

        with sqlite3.connect(str(live_db), timeout=30) as live:
            live_count, live_max_id = live.execute(
                "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM orders"
            ).fetchone()

        if (restored_count, restored_max_id) != (live_count, live_max_id):
            raise RuntimeError(
                "Backup differs from live DB: "
                f"backup orders={restored_count}, max_id={restored_max_id}; "
                f"live orders={live_count}, max_id={live_max_id}"
            )
        return source_backup.name, restored_count, restored_max_id
    finally:
        test_restore.unlink(missing_ok=True)


async def backupcheck_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update):
        await update.effective_message.reply_text("⛔ Эта команда доступна только администратору.")
        return
    try:
        filename, order_count, max_order_id = await asyncio.to_thread(
            verify_latest_backup_restore
        )
        await update.effective_message.reply_text(
            "✅ Проверка восстановления пройдена.\n"
            f"Файл: {filename}\n"
            f"Заказов в восстановленной копии: {order_count}\n"
            f"Последний номер заказа: {max_order_id}\n"
            "Рабочая база не изменялась."
        )
    except Exception:
        log.exception("Backup restore verification failed")
        await update.effective_message.reply_text(
            "❌ Проверка резервной копии не пройдена. Рабочая база не изменялась. "
            "Проверьте журналы Railway."
        )


async def automatic_backup_loop():
    """Create one verified backup at startup and then every 24 hours."""
    while True:
        try:
            await asyncio.to_thread(backup_database)
        except Exception:
            log.exception("Automatic SQLite backup failed")
        await asyncio.sleep(24 * 60 * 60)


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
    chat_id = update.effective_chat.id
    update_id = update.update_id

    # Serialize /start handling per chat. This prevents two near-simultaneous
    # updates from leaving two welcome/menu blocks in the conversation.
    locks = context.application.bot_data.setdefault("start_locks", {})
    lock = locks.setdefault(chat_id, asyncio.Lock())

    async with lock:
        # Every distinct /start update starts a fresh cycle immediately.
        # The per-chat lock serializes resets; only the same update is ignored.
        if context.chat_data.get("last_start_update_id") == update_id:
            log.info("Ignoring duplicate delivery of the same /start update: chat_id=%s update_id=%s", chat_id, update_id)
            return ConversationHandler.END

        context.chat_data["last_start_update_id"] = update_id
        log.info("Received /start: chat_id=%s user_id=%s update_id=%s",
                 chat_id, update.effective_user.id if update.effective_user else None, update_id)
        context.user_data.clear()

        # Keep the previous conversation visible. /start appends a fresh
        # welcome/menu cycle below it, just like a new conversation start.

        welcome = (
            "🐻 *BEAR MODE — еда с характером!*\n\n"
            "Сытные домашние сеты и отдельные блюда в Нячанге 🇻🇳\n"
            "Выбирай готовый сет или собери свой заказ!\n\n"
            "🔥 Готовые сеты — 170.000 ₫"
        )
        sent_ids = []
        msg = await update.message.reply_text(
            welcome, parse_mode="Markdown"
        )
        sent_ids.append(msg.message_id)
        msg = await update.message.reply_text(
            "🐼 Выбирай, что будем делать:\n\n🐾 Выберите действие:",
            parse_mode="Markdown",
            reply_markup=main_keyboard()
        )
        sent_ids.append(msg.message_id)
        context.chat_data["start_message_ids"] = sent_ids
        # Do not delete previous menu/catalog messages on /start.
        
        # Do not reset Telegram's menu button on every /start.
        # It is persistent and does not need another network round trip.

    return ConversationHandler.END

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Ваш Telegram ID: `{update.effective_chat.id}`\n"
        "Скопируйте это число и добавьте его в переменную ADMIN_CHAT_ID в настройках запуска бота.",
        parse_mode="Markdown"
    )

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _menu_callback_locked(update, context, generation)

async def _menu_callback_locked(update: Update, context: ContextTypes.DEFAULT_TYPE, generation: int):
    q = update.callback_query
    # Acknowledge without blocking the visible menu update.
    context.application.create_task(q.answer(), update=update)
    data = q.data

    if data == "home":
        context.user_data.clear()
        await q.edit_message_text(
            "🐼 Выбирай, что будем делать:",
            parse_mode="Markdown", reply_markup=main_keyboard()
        )

    elif data in ("catalog", "delivery", "contact"):
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
    context.application.create_task(q.answer(), update=update)
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
    context.application.create_task(q.answer(), update=update)
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

  


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Оформление остановлено. Чтобы начать заново, нажмите /start.")
    return ConversationHandler.END


async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm that Telegram polling and command handlers are alive."""
    await update.effective_message.reply_text("🐻 BEAR MODE работает. Бот получает команды.")


async def telegram_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    error = context.error
    if error is not None:
        log.error(
            "Unhandled Telegram update error",
            exc_info=(type(error), error, error.__traceback__)
        )
    else:
        log.error("Telegram update failed without an exception object")


def serialize_chat_navigation(handler):
    """Run every menu transition and /start reset sequentially per chat."""
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        if chat is None:
            return await handler(update, context)
        locks = context.application.bot_data.setdefault("menu_navigation_locks", {})
        lock = locks.setdefault(chat.id, asyncio.Lock())
        async with lock:
            return await handler(update, context)
    wrapped.__name__ = handler.__name__
    return wrapped


# These handlers must share the same lock as menu_callback. Otherwise a fast
# tap can race a reminder send/delete and leave duplicate shop messages.
start = serialize_chat_navigation(start)
menu_callback = serialize_chat_navigation(menu_callback)
choose_set = serialize_chat_navigation(choose_set)
quantity = serialize_chat_navigation(quantity)


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Set BOT_TOKEN in your environment variables.")
    if not ADMIN_CHAT_ID or not ADMIN_CHAT_ID.isdigit() or int(ADMIN_CHAT_ID) <= 0:
        raise RuntimeError("Set ADMIN_CHAT_ID to the numeric Telegram user/chat ID of the store administrator.")

    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
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
    init_delivery_db()  # Additive migration; existing orders are preserved.
    backup_task = asyncio.create_task(automatic_backup_loop())
    # Keep /start independent from ConversationHandler state so it always resets
    # the chat and shows the main menu, even after an interrupted conversation.
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("ping", ping_command))
    app.add_handler(CommandHandler("backupcheck", backupcheck_command))
    app.add_handler(CommandHandler("admin", admin_command))
    # Handle order details and delivery controls before the general admin callback.
    app.add_handler(CallbackQueryHandler(delivery_admin_callback, pattern=r"^adm:(order|delivery|set|reference):"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern=r"^adm:"))
    app.add_handler(MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND, delivery_reference_message), group=-1)
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
    app.add_error_handler(telegram_error_handler)

    print("BEAR MODE: starting Telegram bot...", flush=True)
    await app.initialize()
    # Ensure Telegram is not holding updates for a webhook while this bot uses polling.
    await app.bot.delete_webhook(drop_pending_updates=False)
    print("BEAR MODE: webhook cleared; pending updates preserved.", flush=True)
    await app.start()
    await app.updater.start_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
        timeout=30,
        bootstrap_retries=5,
        poll_interval=1.0,
    )
    print("BEAR MODE: Telegram polling started.", flush=True)

    print("BEAR MODE: startup complete; Telegram button ordering is active.", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        backup_task.cancel()
        await asyncio.gather(backup_task, return_exceptions=True)
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())