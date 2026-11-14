import logging
import os
import sqlite3

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

log = logging.getLogger(__name__)
DB_PATH = os.getenv("DB_PATH", "bear_mode.db").strip()
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "5500196056").strip()

DELIVERY_ACTIONS = {
    "grab_requested": ("🚚 Grab вызван", "requested"),
    "picked_up": ("🛵 Курьер забрал", "picked_up"),
    "payment_received": ("💵 Деньги получены", "payment_received"),
    "delivered": ("🎉 Доставлен", "delivered"),
}


def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_delivery_db():
    """Additive migration only: existing orders and IDs are preserved."""
    with db_connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(orders)")}
        additions = {
            "grab_status": "TEXT NOT NULL DEFAULT 'not_requested'",
            "grab_reference": "TEXT",
            "payment_status": "TEXT NOT NULL DEFAULT 'unpaid'",
        }
        for name, definition in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE orders ADD COLUMN {name} {definition}")


def _admin_only(update: Update) -> bool:
    return bool(ADMIN_CHAT_ID) and update.effective_user is not None and str(update.effective_user.id) == ADMIN_CHAT_ID


def _get_order(order_id):
    with db_connect() as conn:
        return conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()


def _set_delivery_value(order_id, column, value):
    allowed = {"grab_status", "payment_status", "status"}
    if column not in allowed:
        raise ValueError("Unsupported delivery field")
    with db_connect() as conn:
        conn.execute(f"UPDATE orders SET {column} = ? WHERE id = ?", (value, order_id))


def _order_text(order):
    order_status = {
        "new": "🆕 Новый", "confirmed": "✅ Подтверждён",
        "cooking": "👨‍🍳 Готовится", "courier": "🛵 Передан курьеру",
        "delivered": "🎉 Доставлен", "cancelled": "❌ Отменён",
    }.get(order["status"], order["status"])
    grab_status = {
        "not_requested": "Не вызван", "requested": "Вызван вручную",
        "picked_up": "Курьер забрал", "delivered": "Доставлен",
    }.get(order["grab_status"] or "not_requested", "Неизвестно")
    payment_status = "Получена" if order["payment_status"] == "received" else "Ожидается"
    return (
        f"🐻 ЗАКАЗ BEAR MODE №{order['id']}\n\n"
        f"📌 Статус заказа: {order_status}\n"
        f"🕒 Создан: {order['created_at']}\n"
        f"👤 Имя: {order['customer_name']}\n"
        f"📱 Телефон: {order['phone']}\n"
        f"📍 Адрес: {order['address']}\n"
        f"📅 Дата доставки: {order['delivery_date'] or 'не указана'}\n"
        f"🍱 Заказ: {order['items_text']}\n"
        f"💰 Сумма заказа / COD: {order['total']:,} ₫\n"
        f"💬 Комментарий: {order['comment'] or 'Нет'}\n\n"
        f"🚚 GrabExpress: {grab_status}\n"
        f"🧾 Grab ID: {order['grab_reference'] or 'не указан'}\n"
        f"💵 Наличные COD: {payment_status}"
    ).replace(",", ".")


def _order_keyboard(order):
    order_id = order["id"]
    rows = []
    status_buttons = [
        ("🆕 Новый", "new"), ("✅ Подтверждён", "confirmed"),
        ("👨‍🍳 Готовится", "cooking"), ("🛵 Курьеру", "courier"),
        ("🎉 Доставлен", "delivered"), ("❌ Отменён", "cancelled"),
    ]
    for start in range(0, len(status_buttons), 2):
        rows.append([
            InlineKeyboardButton(label, callback_data=f"adm:set:{order_id}:{status}")
            for label, status in status_buttons[start:start + 2]
            if status != order["status"]
        ])
    rows.append([InlineKeyboardButton("🚚 Grab вызван", callback_data=f"adm:delivery:{order_id}:grab_requested")])
    rows.append([InlineKeyboardButton("🛵 Курьер забрал", callback_data=f"adm:delivery:{order_id}:picked_up")])
    rows.append([InlineKeyboardButton("💵 Деньги получены", callback_data=f"adm:delivery:{order_id}:payment_received")])
    rows.append([InlineKeyboardButton("🎉 Доставлен", callback_data=f"adm:delivery:{order_id}:delivered")])
    rows.append([InlineKeyboardButton("⬅️ К списку", callback_data="adm:list:all:0")])
    return InlineKeyboardMarkup([row for row in rows if row])


async def delivery_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owns admin order-detail and delivery callbacks; other adm callbacks fall through."""
    q = update.callback_query
    if not _admin_only(update):
        await q.answer("Нет доступа.", show_alert=True)
        return

    parts = q.data.split(":")
    if len(parts) < 3 or parts[1] not in {"order", "delivery"}:
        return

    if parts[1] == "order":
        order_id = int(parts[2])
        order = _get_order(order_id)
        if not order:
            await q.answer("Заказ не найден.", show_alert=True)
            return
        await q.edit_message_text(_order_text(order), reply_markup=_order_keyboard(order))
        await q.answer()
        return

    if len(parts) != 4:
        await q.answer("Некорректное действие.", show_alert=True)
        return

    order_id, action = int(parts[2]), parts[3]
    order = _get_order(order_id)
    if not order:
        await q.answer("Заказ не найден.", show_alert=True)
        return
    if action not in DELIVERY_ACTIONS:
        await q.answer("Неизвестное действие.", show_alert=True)
        return

    if action == "grab_requested":
        _set_delivery_value(order_id, "grab_status", "requested")
        notice = None
    elif action == "picked_up":
        _set_delivery_value(order_id, "grab_status", "picked_up")
        _set_delivery_value(order_id, "status", "courier")
        notice = "🛵 Ваш заказ передан курьеру GrabExpress и скоро будет у вас."
    elif action == "payment_received":
        _set_delivery_value(order_id, "payment_status", "received")
        notice = None
    else:
        _set_delivery_value(order_id, "grab_status", "delivered")
        _set_delivery_value(order_id, "status", "delivered")
        notice = "🎉 Ваш заказ доставлен! Спасибо, что выбрали BEAR MODE."

    if notice and order["customer_chat_id"]:
        try:
            await context.bot.send_message(chat_id=order["customer_chat_id"], text=notice)
        except Exception:
            log.exception("Could not notify customer about delivery update for order %s", order_id)

    updated = _get_order(order_id)
    await q.edit_message_text(_order_text(updated), reply_markup=_order_keyboard(updated))
    await q.answer("Обновлено")
