import logging
import os
import sqlite3

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

log = logging.getLogger(__name__)
DB_PATH = os.getenv("DB_PATH", "bear_mode.db").strip()
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "5500196056").strip()

DELIVERY_ACTIONS = {"grab_requested", "picked_up", "payment_received", "delivered"}


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


def _set_value(order_id, column, value):
    if column not in {"grab_status", "payment_status", "status"}:
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
        row = [
            InlineKeyboardButton(label, callback_data=f"adm:set:{order_id}:{status}")
            for label, status in status_buttons[start:start + 2]
            if status != order["status"]
        ]
        if row:
            rows.append(row)
    rows.extend([
        [InlineKeyboardButton("🧾 Указать Grab ID", callback_data=f"adm:reference:{order_id}")],
        [InlineKeyboardButton("🚚 Grab вызван", callback_data=f"adm:delivery:{order_id}:grab_requested")],
        [InlineKeyboardButton("🛵 Курьер забрал", callback_data=f"adm:delivery:{order_id}:picked_up")],
        [InlineKeyboardButton("💵 Деньги получены", callback_data=f"adm:delivery:{order_id}:payment_received")],
        [InlineKeyboardButton("🎉 Доставлен", callback_data=f"adm:delivery:{order_id}:delivered")],
        [InlineKeyboardButton("⬅️ К списку", callback_data="adm:list:all:0")],
    ])
    return InlineKeyboardMarkup(rows)


async def delivery_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles order details and manual delivery actions before the general admin handler."""
    q = update.callback_query
    if not _admin_only(update):
        await q.answer("Нет доступа.", show_alert=True)
        return
    parts = q.data.split(":")
    if len(parts) < 3 or parts[1] not in {"order", "delivery", "set", "reference"}:
        return

    if parts[1] == "reference":
        order_id = int(parts[2])
        if not _get_order(order_id):
            await q.answer("Заказ не найден.", show_alert=True)
            return
        prompt = await q.message.reply_text(
            f"Отправьте Grab ID для заказа №{order_id} ответом на это сообщение.\n"
            "Если передумали, просто не отправляйте ответ."
        )
        # Keep the pending request per chat so callback and reply updates share it.
        context.chat_data["pending_grab_reference"] = {
            "order_id": order_id,
            "prompt_id": prompt.message_id,
        }
        log.info(
            "Grab ID prompt created: order_id=%s chat_id=%s prompt_message_id=%s",
            order_id, q.message.chat_id, prompt.message_id,
        )
        await q.answer("Жду Grab ID")
        return

    if parts[1] == "order":
        order = _get_order(int(parts[2]))
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
    if parts[1] == "delivery" and action not in DELIVERY_ACTIONS:
        await q.answer("Неизвестное действие.", show_alert=True)
        return
    order = _get_order(order_id)
    if not order:
        await q.answer("Заказ не найден.", show_alert=True)
        return

    notice = None
    if parts[1] == "set":
        status_messages = {
            "new": "Мы получили ваш заказ и скоро свяжемся с вами для подтверждения.",
            "confirmed": "Ваш заказ подтверждён! Спасибо.",
            "cooking": "Ваш заказ уже готовится на кухне.",
            "courier": "Ваш заказ передан курьеру и скоро будет у вас.",
            "delivered": "Ваш заказ отмечен как доставленный. Приятного аппетита!",
            "cancelled": "К сожалению, ваш заказ отменён. Если это ошибка, напишите нам @lucky_ranger.",
        }
        if action not in status_messages:
            await q.answer("Неизвестный статус.", show_alert=True)
            return
        _set_value(order_id, "status", action)
        notice = status_messages[action]
    elif action == "grab_requested":
        _set_value(order_id, "grab_status", "requested")
    elif action == "picked_up":
        _set_value(order_id, "grab_status", "picked_up")
        _set_value(order_id, "status", "courier")
        notice = "🛵 Ваш заказ передан курьеру GrabExpress и скоро будет у вас."
    elif action == "payment_received":
        _set_value(order_id, "payment_status", "received")
    elif action == "delivered":
        _set_value(order_id, "grab_status", "delivered")
        _set_value(order_id, "status", "delivered")
        notice = "🎉 Ваш заказ доставлен! Спасибо, что выбрали BEAR MODE."

    if notice and order["customer_chat_id"]:
        try:
            await context.bot.send_message(chat_id=order["customer_chat_id"], text=notice)
        except Exception:
            log.exception("Could not notify customer about delivery update for order %s", order_id)

    updated = _get_order(order_id)
    await q.edit_message_text(_order_text(updated), reply_markup=_order_keyboard(updated))
    await q.answer("Обновлено")


async def delivery_reference_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save a Grab booking/reference ID sent as a reply to the admin prompt."""
    message = update.effective_message
    if not _admin_only(update) or not message:
        log.warning("Grab ID reply ignored: unauthorized user or missing message")
        return
    pending = context.chat_data.get("pending_grab_reference")
    if not pending:
        log.info("Text reply received without a pending Grab ID request: chat_id=%s", message.chat_id)
        return
    reply = message.reply_to_message
    if not reply or reply.message_id != pending["prompt_id"]:
        log.info(
            "Grab ID reply did not match prompt: chat_id=%s expected_prompt_id=%s actual_reply_id=%s",
            message.chat_id, pending["prompt_id"], reply.message_id if reply else None,
        )
        await message.reply_text("Пожалуйста, отправьте Grab ID именно ответом на последнее сообщение с запросом.")
        return
    pending_order_id = pending["order_id"]
    log.info(
        "Grab ID reply matched: order_id=%s chat_id=%s prompt_message_id=%s",
        pending_order_id, message.chat_id, reply.message_id,
    )
    reference = (message.text or "").strip()
    if not reference or len(reference) > 120 or any(ord(ch) < 32 for ch in reference):
        await update.effective_message.reply_text("Некорректный Grab ID. Отправьте значение длиной до 120 символов.")
        return
    order = _get_order(pending_order_id)
    if not order:
        context.chat_data.pop("pending_grab_reference", None)
        await message.reply_text("Заказ не найден. Откройте его заново в панели.")
        return
    with db_connect() as conn:
        cursor = conn.execute("UPDATE orders SET grab_reference = ? WHERE id = ?", (reference, pending_order_id))
        if cursor.rowcount != 1:
            log.error("Grab ID save affected %s rows for order_id=%s", cursor.rowcount, pending_order_id)
            await message.reply_text("Не удалось сохранить Grab ID. Проверьте заказ и попробуйте ещё раз.")
            return
    context.chat_data.pop("pending_grab_reference", None)
    updated = _get_order(pending_order_id)
    log.info("Grab ID saved successfully: order_id=%s", pending_order_id)
    await message.reply_text("✅ Grab ID сохранён.\n\n" + _order_text(updated))
