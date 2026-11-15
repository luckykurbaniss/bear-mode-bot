import logging
import os
import sqlite3
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes
from customer_service import support_order_button

log = logging.getLogger(__name__)
DB_PATH = os.getenv("DB_PATH", "bear_mode.db").strip()
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()

DELIVERY_ACTIONS = {"grab_requested", "picked_up", "payment_received", "delivered"}
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

# Keep these values aligned with the live Mini App catalog in bot.py.
EDITABLE_CATALOG = {
    "Сет Энергия": 170000,
    "Сет Атлантика": 170000,
    "Сет Комфорт": 170000,
    "Сет Характер": 170000,
    "Гречка с грибами": 40000,
    "Рис с овощами": 40000,
    "Картофельное пюре": 40000,
    "Куриная грудка": 70000,
    "Лосось": 80000,
    "Филе лосося": 80000,
    "Домашние котлеты (2 шт.)": 75000,
    "Куриное бедро": 70000,
    "Овощной салат": 50000,
    "Салат «Свежесть»": 50000,
    "Витаминный салат": 50000,
    "Омлет": 15000,
    "Яйцо отварное": 15000,
    "Морс": 15000,
    "Багет": 15000,
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
            "payment_method": "TEXT NOT NULL DEFAULT 'cash'",
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
        "new": "✅ Новый", "confirmed": "✅ Подтверждён",
        "cooking": "✅ Готовится", "courier": "✅ Передан курьеру",
        "delivered": "✅ Доставлен", "cancelled": "✅ Отменён",
    }.get(order["status"], order["status"])
    grab_status = {
        "not_requested": "Не вызван", "requested": "Вызван вручную",
        "picked_up": "Курьер забрал", "delivered": "Доставлен",
    }.get(order["grab_status"] or "not_requested", "Неизвестно")
    payment_status = "Получена" if order["payment_status"] == "received" else "Ожидается"
    payment_method = order["payment_method"] if "payment_method" in order.keys() else "cash"
    payment_method_label = (
        "Перевод по QR-коду Vietcombank" if payment_method == "qr"
        else "Наличные при получении"
    )
    payment_status_label = (
        "Клиент отметил оплату — проверить поступление"
        if payment_method == "qr" and order["payment_status"] != "received"
        else ("Оплачено / получено" if order["payment_status"] == "received"
              else ("Ожидается при получении" if payment_method == "cash" else payment_status))
    )
    return (
        f"🐻 ЗАКАЗ BEAR MODE №{order['id']}\n\n"
        f"📌 Статус заказа: {order_status}\n"
        f"🕒 Создан: {order['created_at']}\n"
        f"👤 Имя: {order['customer_name']}\n"
        f"📱 Телефон: {order['phone']}\n"
        f"📍 Адрес: {order['address']}\n"
        f"📅 Дата доставки: {order['delivery_date'] or 'не указана'}\n"
        f"🍱 Заказ: {order['items_text']}\n"
        f"💰 Сумма заказа: {order['total']:,} ₫\n"
        f"💳 Способ оплаты: {payment_method_label}\n"
        f"💵 Статус оплаты: {payment_status_label}\n"
        f"💬 Комментарий: {order['comment'] or 'Нет'}\n\n"
        f"🚚 GrabExpress: {grab_status}\n"
        f"🧾 Grab ID: {order['grab_reference'] or 'не указан'}"
    ).replace(",", ".")


def _parse_edited_items(raw_text):
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if not lines or len(lines) > len(EDITABLE_CATALOG):
        raise ValueError("Укажите от 1 до 19 позиций, каждая с новой строки.")
    normalized = {name.casefold(): (name, price) for name, price in EDITABLE_CATALOG.items()}
    normalized["котлеты"] = ("Домашние котлеты (2 шт.)", 75000)
    result = []
    seen = set()
    total = 0
    for line in lines:
        match = re.fullmatch(r"(.+?)\s*[x×]\s*(\d+)", line, flags=re.IGNORECASE)
        if not match:
            raise ValueError("Формат строки: Название × количество.")
        requested_name = match.group(1).strip().casefold()
        quantity = int(match.group(2))
        if requested_name not in normalized:
            raise ValueError(f"Не нашёл позицию «{match.group(1).strip()}». Проверьте название.")
        name, unit_price = normalized[requested_name]
        if name in seen:
            raise ValueError(f"Позиция «{name}» указана дважды. Объедините количество в одной строке.")
        if not 1 <= quantity <= 100:
            raise ValueError("Количество каждой позиции должно быть от 1 до 100.")
        seen.add(name)
        result.append(f"{name} × {quantity}")
        total += unit_price * quantity
    return ", ".join(result), total


def _order_keyboard(order):
    order_id = order["id"]
    rows = []
    status_buttons = [
        ("Новый", "new"), ("Подтверждён", "confirmed"),
        ("Готовится", "cooking"), ("Отменён", "cancelled"),
    ]
    # Show only available next statuses. The current status is omitted,
    # so an old status can never keep a stale checkmark on the card.
    available_statuses = [
        (label, status) for label, status in status_buttons
        if status != order["status"]
    ]
    for start in range(0, len(available_statuses), 2):
        row = [
            InlineKeyboardButton(
                label,
                callback_data=f"adm:set:{order_id}:{status}",
            )
            for label, status in available_statuses[start:start + 2]
        ]
        rows.append(row)
    if order["status"] not in {"delivered", "cancelled"}:
        rows.append([InlineKeyboardButton("✏️ Изменить заказ", callback_data=f"adm:edit:{order_id}")])
    rows.extend([
        [InlineKeyboardButton(("✅ " if order["grab_reference"] else "🧾 ") + "Указать Grab ID", callback_data=f"adm:reference:{order_id}")],
        [InlineKeyboardButton(("✅ " if order["grab_status"] in {"requested", "picked_up", "delivered"} else "🚚 ") + "Grab вызван", callback_data=f"adm:delivery:{order_id}:grab_requested")],
        [InlineKeyboardButton(("✅ " if order["grab_status"] in {"picked_up", "delivered"} or order["status"] in {"courier", "delivered"} else "🛵 ") + "Курьер забрал", callback_data=f"adm:delivery:{order_id}:picked_up")],
        [InlineKeyboardButton(("✅ " if order["payment_status"] == "received" else "💵 ") + "Деньги получены", callback_data=f"adm:delivery:{order_id}:payment_received")],
        [InlineKeyboardButton(("✅ " if order["status"] == "delivered" else "🎉 ") + "Доставлен", callback_data=f"adm:delivery:{order_id}:delivered")],
        support_order_button(order_id),
        [InlineKeyboardButton("⬅️ К списку", callback_data="adm:list:all:0")],
    ])
    return InlineKeyboardMarkup(rows)


def _order_edit_keyboard(order_id):
    """Build the field selector shown after the administrator taps Edit order."""
    fields = [
        ("👤 Имя клиента", "customer_name"),
        ("📱 Телефон", "phone"),
        ("📍 Адрес доставки", "address"),
        ("📅 Дата доставки", "delivery_date"),
        ("🍱 Состав и сумма", "items"),
        ("💬 Комментарий", "comment"),
    ]
    rows = [
        [InlineKeyboardButton(label, callback_data=f"adm:edit:{order_id}:{field}")]
        for label, field in fields
    ]
    rows.append([
        InlineKeyboardButton("⬅️ Назад к заказу", callback_data=f"adm:order:{order_id}")
    ])
    return InlineKeyboardMarkup(rows)


async def delivery_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles order details and manual delivery actions before the general admin handler."""
    q = update.callback_query
    if not _admin_only(update):
        await q.answer("Нет доступа.", show_alert=True)
        return
    parts = q.data.split(":")
    if len(parts) < 3 or parts[1] not in {"order", "delivery", "set", "reference", "edit"}:
        return

    if parts[1] == "edit":
        context.chat_data.pop("pending_grab_reference", None)
        order_id = int(parts[2])
        order = _get_order(order_id)
        if not order:
            await q.answer("Заказ не найден.", show_alert=True)
            return
        if order["status"] in {"delivered", "cancelled"}:
            await q.answer("Доставленный или отменённый заказ нельзя редактировать.", show_alert=True)
            return
        if len(parts) == 3:
            await q.edit_message_text(
                "✏️ *ИЗМЕНЕНИЕ ЗАКАЗА №" + str(order_id) + "*\n\nВыберите, что нужно исправить:",
                parse_mode="Markdown",
                reply_markup=_order_edit_keyboard(order_id),
            )
            await q.answer()
            return
        field = parts[3]
        prompts = {
            "customer_name": ("имя клиента", "Введите новое имя (2–80 символов)."),
            "phone": ("телефон", "Введите новый телефон (7–30 символов)."),
            "address": ("адрес доставки", "Введите новый адрес (5–400 символов)."),
            "delivery_date": ("дату доставки", "Введите дату в формате ГГГГ-ММ-ДД. Например: 2026-11-20."),
            "comment": ("комментарий", "Введите новый комментарий (до 500 символов). Чтобы очистить, отправьте —."),
            "items": ("состав и сумму", "Отправьте каждую позицию с новой строки в формате Название × количество.\n\nДоступные позиции:\n" + "\n".join(f"• {name} — {price:,} ₫".replace(",", ".") for name, price in EDITABLE_CATALOG.items())),
        }
        if field not in prompts:
            await q.answer("Неизвестное поле.", show_alert=True)
            return
        label, prompt_text = prompts[field]
        prompt = await q.message.reply_text(
            f"✏️ Изменяем {label} заказа №{order_id}.\n\n{prompt_text}\n\n"
            "Можно ответить на это сообщение или просто отправить новое сообщение в чат."
        )
        context.chat_data["pending_order_edit"] = {
            "order_id": order_id,
            "field": field,
            "prompt_id": prompt.message_id,
        }
        await q.answer("Жду новые данные")
        return

    if parts[1] == "reference":
        order_id = int(parts[2])
        if not _get_order(order_id):
            await q.answer("Заказ не найден.", show_alert=True)
            return

        # Ignore rapid repeated taps: one active Grab ID prompt per order/chat.
        # Without this guard, a second prompt replaces the first prompt ID and
        # a valid reply to the first visible message is rejected as outdated.
        pending = context.chat_data.get("pending_grab_reference")
        if pending and pending.get("order_id") == order_id:
            await q.answer("Запрос Grab ID уже открыт. Ответьте на последнее сообщение с запросом.")
            return

        prompt = await q.message.reply_text(
            f"Отправьте Grab ID для заказа №{order_id} ответом на это сообщение.\n"
            "Если передумали, просто не отправляйте ответ."
        )
        # Keep the pending request per chat so callback and reply updates share it.
        context.chat_data["pending_grab_reference"] = {
            "order_id": order_id,
            "prompt_id": prompt.message_id,
            "order_card_message_id": q.message.message_id,
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
        await q.message.edit_text(_order_text(order), reply_markup=_order_keyboard(order))
        await q.answer("Заказ открыт")
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
    # Make status actions idempotent: repeated taps on an old Telegram card
    # must not send duplicate customer notifications.
    if parts[1] == "set" and order["status"] == action:
        await q.answer("Этот статус уже установлен.")
        return
    if parts[1] == "delivery":
        current_grab = order["grab_status"] or "not_requested"
        already_applied = (
            (action == "grab_requested" and current_grab == "requested")
            or (action == "picked_up" and current_grab == "picked_up" and order["status"] == "courier")
            or (action == "payment_received" and order["payment_status"] == "received")
            or (action == "delivered" and current_grab == "delivered" and order["status"] == "delivered")
        )
        if already_applied:
            await q.answer("Это действие уже выполнено.")
            return

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
        previous_status = order["grab_status"] or "not_requested"
        _set_value(order_id, "grab_status", "requested")
        log.info(
            "Grab status updated: order_id=%s previous=%s new=requested",
            order_id, previous_status,
        )
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
    # Keep exactly one live order card: replace its text and keyboard in place.
    await q.message.edit_text(_order_text(updated), reply_markup=_order_keyboard(updated))
    await q.answer("Карточка заказа обновлена")


async def delivery_reference_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save a Grab ID sent directly or as a reply to the active admin prompt."""
    message = update.effective_message
    if not _admin_only(update) or not message:
        log.warning("Grab ID reply ignored: unauthorized user or missing message")
        return
    pending = context.chat_data.get("pending_grab_reference")
    if not pending:
        log.info("Text reply received without a pending Grab ID request: chat_id=%s", message.chat_id)
        return
    reply = message.reply_to_message
    # Accept both ways: a direct text message while a Grab ID request is pending,
    # or a reply specifically to the active Grab ID prompt.
    if reply and reply.message_id != pending["prompt_id"]:
        log.info(
            "Grab ID reply targeted another message: chat_id=%s expected_prompt_id=%s actual_reply_id=%s",
            message.chat_id, pending["prompt_id"], reply.message_id,
        )
        await message.reply_text("Если отвечаете на сообщение, выберите последнее сообщение с запросом Grab ID. Можно также отправить ID обычным сообщением.")
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
    order_card_message_id = pending.get("order_card_message_id")
    if order_card_message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=message.chat_id,
                message_id=order_card_message_id,
                text=_order_text(updated),
                reply_markup=_order_keyboard(updated),
            )
        except BadRequest as exc:
            log.warning("Could not refresh order card after Grab ID save: %s", exc)
            await message.reply_text(_order_text(updated), reply_markup=_order_keyboard(updated))
    else:
        await message.reply_text(_order_text(updated), reply_markup=_order_keyboard(updated))


async def order_edit_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Validate and save one administrator-entered order edit."""
    message = update.effective_message
    if not _admin_only(update) or not message:
        return
    if message.reply_to_message and message.reply_to_message.message_id == context.user_data.get("support_preview_message_id"):
        return
    pending = context.chat_data.get("pending_order_edit")
    if not pending:
        return
    # A reply must target the active prompt. A regular text message is also
    # accepted while an edit is pending, so the admin is not forced to use Reply.
    if message.reply_to_message and message.reply_to_message.message_id != pending["prompt_id"]:
        return
    order_id = pending["order_id"]
    field = pending["field"]
    order = _get_order(order_id)
    if not order or order["status"] in {"delivered", "cancelled"}:
        context.chat_data.pop("pending_order_edit", None)
        await message.reply_text("Заказ уже недоступен для редактирования.")
        return

    raw = (message.text or "").strip()
    updates = {}
    try:
        if field == "customer_name":
            if not 2 <= len(raw) <= 80:
                raise ValueError("Имя должно содержать от 2 до 80 символов.")
            updates[field] = raw
            customer_notice = f"Имя: {raw}"
        elif field == "phone":
            if not 7 <= len(raw) <= 30:
                raise ValueError("Телефон должен содержать от 7 до 30 символов.")
            updates[field] = raw
            customer_notice = f"Телефон: {raw}"
        elif field == "address":
            if not 5 <= len(raw) <= 400:
                raise ValueError("Адрес должен содержать от 5 до 400 символов.")
            updates[field] = raw
            customer_notice = f"Адрес доставки: {raw}"
        elif field == "delivery_date":
            try:
                parsed_date = date.fromisoformat(raw)
            except ValueError as exc:
                raise ValueError("Неверная дата. Используйте формат ГГГГ-ММ-ДД.") from exc
            if parsed_date < datetime.now(VN_TZ).date():
                raise ValueError("Нельзя указать дату в прошлом.")
            updates[field] = raw
            customer_notice = f"Дата доставки: {raw}"
        elif field == "comment":
            if raw == "—":
                raw = ""
            if len(raw) > 500:
                raise ValueError("Комментарий должен быть не длиннее 500 символов.")
            updates[field] = raw
            customer_notice = f"Комментарий: {raw or 'удалён'}"
        elif field == "items":
            items_text, total = _parse_edited_items(raw)
            updates["items_text"] = items_text
            updates["total"] = total
            customer_notice = f"Новый состав: {items_text}\nНовая сумма: {total:,} ₫".replace(",", ".")
        else:
            raise ValueError("Неизвестное поле.")
    except ValueError as exc:
        await message.reply_text(f"⚠️ {exc}\nПопробуйте ещё раз, ответив на исходное сообщение.")
        return

    allowed_columns = {"customer_name", "phone", "address", "delivery_date", "comment", "items_text", "total"}
    if not updates or any(column not in allowed_columns for column in updates):
        await message.reply_text("Не удалось сохранить изменение.")
        return
    with db_connect() as conn:
        assignments = ", ".join(f"{column} = ?" for column in updates)
        conn.execute(
            f"UPDATE orders SET {assignments} WHERE id = ?",
            (*updates.values(), order_id),
        )
    context.chat_data.pop("pending_order_edit", None)
    if order["customer_chat_id"]:
        try:
            await context.bot.send_message(
                chat_id=order["customer_chat_id"],
                text=f"ℹ️ Заказ BEAR MODE №{order_id} обновлён.\n{customer_notice}",
            )
        except Exception:
            log.exception("Could not notify customer about edited order %s", order_id)
    updated = _get_order(order_id)
    await message.reply_text(
        f"✅ Заказ №{order_id} обновлён.\n\n{_order_text(updated)}",
        reply_markup=_order_keyboard(updated),
    )


