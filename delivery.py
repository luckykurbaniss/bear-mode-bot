import asyncio
import logging
import os
import sqlite3
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes
from customer_service import delete_order_decision_notice, support_order_button

log = logging.getLogger(__name__)
DB_PATH = os.getenv("DB_PATH", "bear_mode.db").strip()
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()

DELIVERY_ACTIONS = {"grab_requested", "picked_up", "payment_received", "delivered"}
ORDER_STATUS_TRANSITIONS = {
    "new": {"confirmed", "cancelled"},
    "confirmed": {"cooking", "cancelled"},
    "cooking": {"ready", "cancelled"},
    "ready": {"cancelled"},
    "courier": set(),
    "delivered": set(),
    "cancelled": set(),
}
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def is_order_transition_allowed(current_status: str, next_status: str) -> bool:
    """Return whether an order status may move to the requested next status."""
    return next_status in ORDER_STATUS_TRANSITIONS.get(current_status, set())


def _manual_status_transition_allowed(order, next_status: str) -> bool:
    """Allow backward corrections, but only one forward lifecycle step at a time."""
    current = order["status"]
    if next_status == current:
        return False
    if next_status == "cancelled":
        return current in {"new", "confirmed", "cooking", "ready"}
    if current == "cancelled":
        return next_status == "new"
    ranks = {"new": 0, "confirmed": 1, "cooking": 2, "ready": 3, "courier": 4, "delivered": 5}
    if current not in ranks or next_status not in ranks:
        return False
    if ranks[next_status] < ranks[current]:
        return True
    if ranks[next_status] != ranks[current] + 1:
        return False
    if next_status == "courier":
        return (
            bool(order["grab_reference"])
            and order["grab_status"] == "picked_up"
            and order["payment_status"] == "received"
        )
    if next_status == "delivered":
        return bool(order["grab_tracking_url"]) and order["grab_status"] == "picked_up"
    return True

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
            "grab_tracking_url": "TEXT",
            "payment_status": "TEXT NOT NULL DEFAULT 'unpaid'",
            "payment_method": "TEXT NOT NULL DEFAULT 'cash'",
            "delivery_fee": "INTEGER NOT NULL DEFAULT 0",
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


def _catalog_copy_entities(text):
    """Make every catalog item name and price independently selectable/copyable."""
    entities = []
    for name, price in EDITABLE_CATALOG.items():
        amount = f"{price:,}".replace(",", ".") + " ₫"
        line = f"• {name} — {amount}"
        line_index = text.find(line)
        if line_index < 0:
            continue
        for value, relative_start in (
            (name, line_index + 2),
            (amount, line_index + len(f"• {name} — ")),
        ):
            offset = len(text[:relative_start].encode("utf-16-le")) // 2
            length = len(value.encode("utf-16-le")) // 2
            if length:
                entities.append(MessageEntity(type=MessageEntity.CODE, offset=offset, length=length))
    return entities


def _order_entities(order, prefix=""):
    """Make the name and phone independently selectable as code-formatted fields."""
    text = prefix + _order_text(order)
    entities = []
    for marker, value in (
        ("👤 Имя: ", str(order["customer_name"])),
        ("📱 Телефон: ", str(order["phone"])),
    ):
        marker_index = text.find(marker)
        if marker_index < 0:
            continue
        start = marker_index + len(marker)
        # Telegram entity offsets/lengths use UTF-16 code units.
        offset = len(text[:start].encode("utf-16-le")) // 2
        length = len(value.encode("utf-16-le")) // 2
        if length:
            entities.append(MessageEntity(type=MessageEntity.CODE, offset=offset, length=length))
    return entities


def _order_text(order):
    order_status = {
        "new": "✅ Новый", "confirmed": "✅ Подтверждён",
        "cooking": "✅ Готовится", "ready": "✅ Готов", "courier": "✅ Передан курьеру",
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
        f"🔗 Telegram: {'@' + order['customer_username'] if 'customer_username' in order.keys() and order['customer_username'] else ('ID ' + str(order['customer_chat_id']) if order['customer_chat_id'] else 'не указан')}\n"
        f"📍 Адрес: {order['address']}\n"
        f"📅 Дата доставки: {order['delivery_date'] or 'не указана'}\n"
        f"🍱 Заказ: {order['items_text']}\n"
        f"💰 Сумма заказа: {order['total']:,} ₫\n"
        f"🛵 Доставка: {(order['delivery_fee'] or 0):,} ₫\n"
        f"💳 Всего с доставкой: {int(order['total']) + int(order['delivery_fee'] or 0):,} ₫\n"
        f"💳 Способ оплаты: {payment_method_label}\n"
        f"💵 Статус оплаты: {payment_status_label}\n"
        f"💬 Комментарий: {order['comment'] or 'Нет'}\n\n"
        f"🚚 GrabExpress: {grab_status}\n"
        f"🧾 Grab ID: {order['grab_reference'] or 'не указан'}\n"
        f"🔗 Ссылка отслеживания: {order['grab_tracking_url'] or 'не добавлена'}"
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
    # Keep customer name and phone as plain text in the order card.
    # The admin can select the name directly from the message and copy it.
    rows = []
    # Keep cancellation visible at the top of a new order card.
    # Once cancelled, replace the action with a persistent status marker.
    if order["status"] == "cancelled":
        # A customer-cancelled order is terminal: show only the cancellation
        # marker and navigation. Do not leave confirmation or delivery actions.
        rows.append([
            InlineKeyboardButton(
                "❌ Отменён",
                callback_data=f"adm:order:{order_id}",
            )
        ])
        rows.append([
            InlineKeyboardButton("💬 Переписка", callback_data=f"support:history:{order_id}")
        ])
        rows.append([
            InlineKeyboardButton("⬅️ К списку", callback_data="adm:list:all:0")
        ])
        return InlineKeyboardMarkup(rows)
    elif order["status"] == "new":
        rows.append([
            InlineKeyboardButton(
                "❌ Отменить",
                callback_data=f"adm:edit:{order_id}:cancelled",
            )
        ])
    # Reflect the selected order status as completed stages in the main menu.
    # Cancelled orders visually reset all stage checkmarks without deleting data.
    show_progress = order["status"] != "cancelled"
    manual_status_override = bool(order["manual_status_override"]) if "manual_status_override" in order.keys() else False
    confirmed_stage_reached = show_progress and (
        order["status"] == "confirmed" if manual_status_override
        else order["status"] in {"confirmed", "cooking", "ready", "courier", "delivered"}
    )
    cooking_stage_reached = show_progress and (
        order["status"] == "cooking" if manual_status_override
        else order["status"] in {"cooking", "ready", "courier", "delivered"}
    )
    ready_stage_reached = show_progress and (
        order["status"] == "ready" if manual_status_override
        else order["status"] in {"ready", "courier", "delivered"}
    )
    courier_stage_reached = show_progress and order["status"] in {"courier", "delivered"}
    status_buttons = [
        ("✅ Подтверждён" if confirmed_stage_reached else "🤝 Подтвердить", "confirmed"),
        ("✅ Готовится" if cooking_stage_reached else "🍳 Готовится", "cooking"),
        ("✅ Готов" if ready_stage_reached else "🍱 Готов", "ready"),
    ]
    for start in range(0, len(status_buttons), 1):
        label, status = status_buttons[start]
        rows.append([
            InlineKeyboardButton(
                label,
                callback_data=f"adm:set:{order_id}:{status}",
            )
        ])
        if status == "confirmed":
            rows.append([
                InlineKeyboardButton(
                    ("✅ " if int(order["delivery_fee"] or 0) > 0 else "💰 ") + "Указать сумму доставки",
                    callback_data=f"adm:fee:{order_id}",
                )
            ])
    rows.extend([
        [InlineKeyboardButton(("✅ " if order["grab_reference"] else "🧾 ") + "Указать Grab ID", callback_data=f"adm:reference:{order_id}")],
        [InlineKeyboardButton(("✅ " if order["grab_status"] in {"requested", "picked_up", "delivered"} else "🚚 ") + "Grab вызван", callback_data=f"adm:delivery:{order_id}:grab_requested")],
        [InlineKeyboardButton(("✅ " if order["payment_status"] == "received" else "💵 ") + "Деньги получены", callback_data=f"adm:delivery:{order_id}:payment_received")],
        [InlineKeyboardButton(("✅ " if order["grab_status"] in {"picked_up", "delivered"} else "🛵 ") + "Курьер забрал", callback_data=f"adm:delivery:{order_id}:picked_up")],
        [InlineKeyboardButton(("✅ " if order["grab_tracking_url"] else "🔗 ") + "Ссылка отслеживания Grab", callback_data=f"adm:tracking:{order_id}")],
        [InlineKeyboardButton(("✅ " if show_progress and order["status"] == "delivered" else "🏡 ") + "Доставлен", callback_data=f"adm:delivery:{order_id}:delivered")],
        [InlineKeyboardButton("✏️ Изменить заказ", callback_data=f"adm:edit:{order_id}")],
        [InlineKeyboardButton("💬 Переписка", callback_data=f"support:history:{order_id}")],
        support_order_button(order_id),
        [InlineKeyboardButton("⬅️ К списку", callback_data="adm:list:all:0")],
    ])
    return InlineKeyboardMarkup(rows)


def _order_edit_keyboard(order_id):
    """Build the field selector shown after the administrator taps Edit order."""
    fields = [
        ("🔄 Изменить статус", "status"),
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


def _order_status_keyboard(order):
    """Mirror the main order menu sequence, with New first and Cancelled last."""
    order_id = order["id"]
    rows = []
    show_progress = order["status"] != "cancelled"
    manual_status_override = bool(order["manual_status_override"]) if "manual_status_override" in order.keys() else False
    confirmed_stage_reached = show_progress and (
        order["status"] == "confirmed" if manual_status_override
        else order["status"] in {"confirmed", "cooking", "ready", "courier", "delivered"}
    )
    cooking_stage_reached = show_progress and (
        order["status"] == "cooking" if manual_status_override
        else order["status"] in {"cooking", "ready", "courier", "delivered"}
    )
    ready_stage_reached = show_progress and (
        order["status"] == "ready" if manual_status_override
        else order["status"] in {"ready", "courier", "delivered"}
    )
    courier_stage_reached = show_progress and order["status"] in {"courier", "delivered"}

    # Keep the exact same operational sequence as the main order card.
    rows.append([
        InlineKeyboardButton(
            ("✅ " if order["status"] == "new" else "🆕 ") + "Новый",
            callback_data=f"adm:edit:{order_id}:status:new",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            ("✅ " if confirmed_stage_reached else "🤝 ") + "Подтверждён",
            callback_data=f"adm:edit:{order_id}:status:confirmed",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            ("✅ " if cooking_stage_reached else "🍳 ") + "Готовится",
            callback_data=f"adm:edit:{order_id}:status:cooking",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            ("✅ " if ready_stage_reached else "🍱 ") + "Готов",
            callback_data=f"adm:edit:{order_id}:status:ready",
        )
    ])

    operational_actions = [
        (
            ("✅ " if order["grab_reference"] else "🧾 ") + "Указать Grab ID",
            f"adm:reference:{order_id}",
        ),
        (
            ("✅ " if order["grab_status"] in {"requested", "picked_up", "delivered"} else "🚚 ") + "Grab вызван",
            f"adm:delivery:{order_id}:grab_requested",
        ),
        (
            ("✅ " if order["payment_status"] == "received" else "💵 ") + "Деньги получены",
            f"adm:delivery:{order_id}:payment_received",
        ),
        (
            ("✅ " if order["grab_status"] in {"picked_up", "delivered"} else "🛵 ") + "Курьер забрал",
            f"adm:delivery:{order_id}:picked_up",
        ),
        (
            ("✅ " if order["grab_tracking_url"] else "🔗 ") + "Ссылка отслеживания Grab",
            f"adm:tracking:{order_id}",
        ),
    ]
    for label, callback_data in operational_actions:
        rows.append([InlineKeyboardButton(label, callback_data=callback_data)])

    rows.append([
        InlineKeyboardButton(
            ("✅ " if show_progress and order["status"] == "delivered" else "🏡 ") + "Доставлен",
            callback_data=f"adm:edit:{order_id}:status:delivered",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            ("✅ " if order["status"] == "cancelled" else "❌ ") + "Отменён",
            callback_data=f"adm:edit:{order_id}:status:cancelled",
        )
    ])
    rows.append([
        InlineKeyboardButton("⬅️ Назад к изменению заказа", callback_data=f"adm:edit:{order_id}")
    ])
    return InlineKeyboardMarkup(rows)

async def delivery_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles order details and manual delivery actions before the general admin handler."""
    q = update.callback_query
    if not _admin_only(update):
        await q.answer("Нет доступа.", show_alert=True)
        return
    parts = q.data.split(":")
    if len(parts) < 3 or parts[1] not in {"order", "delivery", "set", "reference", "tracking", "edit", "fee"}:
        return

    if parts[1] == "fee":
        order_id = int(parts[2])
        order = _get_order(order_id)
        if not order:
            await q.answer("Заказ не найден.", show_alert=True)
            return
        prompt = await q.message.reply_text(
            f"💰 Укажите стоимость доставки для заказа №{order_id} в VND (только число, например 25000)."
        )
        context.chat_data["pending_order_edit"] = {
            "order_id": order_id,
            "field": "delivery_fee",
            "prompt_id": prompt.message_id,
            "order_card_chat_id": q.message.chat_id,
            "order_card_message_id": q.message.message_id,
        }
        await q.answer("Жду сумму доставки")
        return

    if parts[1] == "edit":
        context.chat_data.pop("pending_grab_reference", None)
        order_id = int(parts[2])
        order = _get_order(order_id)
        if not order:
            await q.answer("Заказ не найден.", show_alert=True)
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
        if field == "status":
            if len(parts) == 4:
                await q.edit_message_text(
                    "🔄 *ИЗМЕНЕНИЕ СТАТУСА ЗАКАЗА №" + str(order_id) + "*\n\nВыберите новый статус:",
                    parse_mode="Markdown",
                    reply_markup=_order_status_keyboard(order),
                )
                await q.answer()
                return
            if len(parts) != 5:
                await q.answer("Некорректный статус.", show_alert=True)
                return
            new_status = parts[4]
            valid_statuses = {"new", "confirmed", "cooking", "ready", "courier", "delivered", "cancelled"}
            if new_status not in valid_statuses:
                await q.answer("Неизвестный статус.", show_alert=True)
                return
            if new_status == order["status"]:
                await q.answer("Этот статус уже установлен.", show_alert=True)
                return
            if not _manual_status_transition_allowed(order, new_status):
                await q.answer("Нельзя пропустить этап. Сначала выполните предыдущие действия или вернитесь на предыдущий статус.", show_alert=True)
                return
            _set_value(order_id, "status", new_status)
            # Manual status selection displays only that selected stage in the main menu.
            with db_connect() as conn:
                conn.execute("UPDATE orders SET manual_status_override = 1 WHERE id = ?", (order_id,))
            # Manual status corrections are silent: update admin state only.
            updated = _get_order(order_id)
            await q.edit_message_text(_order_text(updated), entities=_order_entities(updated), reply_markup=_order_keyboard(updated))
            await q.answer("Статус заказа изменён")
            return
        if order["status"] in {"delivered", "cancelled"}:
            await q.answer("Данные доставленного или отменённого заказа менять нельзя. Изменить можно только статус.", show_alert=True)
            return
        if field == "cancelled":
            if order["status"] not in {"new", "confirmed", "cooking", "ready"}:
                await q.answer("Заказ на этом этапе уже нельзя отменить из панели.", show_alert=True)
                return
            _set_value(order_id, "status", "cancelled")
            customer_chat_id = order["customer_chat_id"] if "customer_chat_id" in order.keys() else None
            if customer_chat_id:
                try:
                    await context.bot.send_message(
                        chat_id=customer_chat_id,
                        text="🐻 Ваш заказ отменён. До новых встреч и вкусных моментов вместе с BEAR MODE! 🐼",
                    )
                    log.info("Cancellation notification sent: order_id=%s customer_chat_id=%s", order_id, customer_chat_id)
                except Exception:
                    log.exception("Could not notify customer about cancelled order %s", order_id)
            order = _get_order(order_id)
            await q.edit_message_text(
                _order_text(order),
                entities=_order_entities(order),
                reply_markup=_order_keyboard(order),
            )
            await q.answer("Заказ отменён")
            return
        prompts = {
            "customer_name": ("имя клиента", "Введите новое имя (2–80 символов)."),
            "phone": ("телефон", "Введите новый телефон (7–30 символов)."),
            "address": ("адрес доставки", "Введите новый адрес (5–1000 символов)."),
            "delivery_date": ("дату доставки", "Введите дату в формате ГГГГ-ММ-ДД. Например: 2026-11-20."),
            "comment": ("комментарий", "Введите новый комментарий (до 500 символов). Чтобы очистить, отправьте —."),
            "items": ("состав и сумму", "Отправьте каждую позицию с новой строки в формате Название × количество.\n\nДоступные позиции:\n" + "\n".join(f"• {name} — {price:,} ₫".replace(",", ".") for name, price in EDITABLE_CATALOG.items())),
        }
        if field not in prompts:
            await q.answer("Неизвестное поле.", show_alert=True)
            return
        label, prompt_text = prompts[field]
        full_prompt_text = (
            f"✏️ Изменяем {label} заказа №{order_id}.\n\n{prompt_text}\n\n"
            "Можно ответить на это сообщение или просто отправить новое сообщение в чат."
        )
        prompt_entities = _catalog_copy_entities(full_prompt_text) if field == "items" else None
        prompt = await q.message.reply_text(
            full_prompt_text,
            entities=prompt_entities,
        )
        context.chat_data["pending_order_edit"] = {
            "order_id": order_id,
            "field": field,
            "prompt_id": prompt.message_id,
            "order_card_chat_id": q.message.chat_id,
            "order_card_message_id": q.message.message_id,
        }
        await q.answer("Жду новые данные")
        return

    if parts[1] == "tracking":
        order_id = int(parts[2])
        order = _get_order(order_id)
        if not order:
            await q.answer("Заказ не найден.", show_alert=True)
            return
        if order["grab_tracking_url"]:
            await q.answer("Ссылка отслеживания уже добавлена. Повторно изменить её нельзя.", show_alert=True)
            return
        if order["grab_status"] != "picked_up" or order["status"] != "courier":
            await q.answer("Сначала отметьте «Курьер забрал». После этого добавьте ссылку отслеживания.", show_alert=True)
            return
        # A new tracking-link request supersedes any stale Grab ID request.
        context.chat_data.pop("pending_grab_reference", None)
        prompt = await q.message.reply_text(
            f"🔗 Отправьте ссылку отслеживания GrabExpress для заказа №{order_id}."
        )
        context.chat_data["pending_grab_tracking"] = {
            "order_id": order_id,
            "prompt_id": prompt.message_id,
            "order_card_message_id": q.message.message_id,
        }
        await q.answer("Жду ссылку Grab")
        return

    if parts[1] == "reference":
        order_id = int(parts[2])
        order = _get_order(order_id)
        if not order:
            await q.answer("Заказ не найден.", show_alert=True)
            return
        if order["grab_reference"]:
            await q.answer("Grab ID уже указан. Повторно изменить его нельзя.", show_alert=True)
            return
        if order["status"] != "ready":
            await q.answer("Сначала отметьте «Готовится», затем «Готов».", show_alert=True)
            return

        # Ignore rapid repeated taps: one active Grab ID prompt per order/chat.
        # Without this guard, a second prompt replaces the first prompt ID and
        # a valid reply to the first visible message is rejected as outdated.
        pending = context.chat_data.get("pending_grab_reference")
        if pending and pending.get("order_id") == order_id:
            await q.answer("Запрос Grab ID уже открыт. Ответьте на последнее сообщение с запросом.")
            return

        # A new Grab ID request supersedes any stale tracking-link request.
        context.chat_data.pop("pending_grab_tracking", None)
        prompt = await q.message.reply_text(
            f"Отправьте Grab ID для заказа №{order_id} ответом на это сообщение."
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
        await q.message.edit_text(_order_text(order), entities=_order_entities(order), reply_markup=_order_keyboard(order))
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
    # Enforce a one-way order lifecycle, including callbacks from stale cards.
    if parts[1] == "set":
        if action == order["status"]:
            await q.answer("Этот статус уже установлен.")
            return
        if not is_order_transition_allowed(order["status"], action):
            await q.answer("Этот переход статуса недоступен. Откройте актуальную карточку заказа.", show_alert=True)
            return
    if parts[1] == "delivery":
        current_grab = order["grab_status"] or "not_requested"
        if order["status"] == "cancelled" or (
            order["status"] == "delivered" and action != "payment_received"
        ):
            await q.answer("Для отменённого заказа это действие недоступно.", show_alert=True)
            return
        already_applied = (
            (action == "grab_requested" and current_grab == "requested")
            or (action == "picked_up" and current_grab == "picked_up" and order["status"] == "courier")
            or (action == "payment_received" and order["payment_status"] == "received")
            or (action == "delivered" and current_grab == "delivered" and order["status"] == "delivered")
        )
        if already_applied:
            await q.answer("Это действие уже выполнено.")
            return
        if action == "grab_requested":
            if not order["grab_reference"]:
                await q.answer("Сначала укажите Grab ID.", show_alert=True)
                return
            if current_grab != "not_requested":
                await q.answer("Grab уже был вызван или курьер забрал заказ.", show_alert=True)
                return
            if order["status"] != "ready":
                await q.answer("Сначала отметьте статус «Готов».", show_alert=True)
                return
        if action == "payment_received" and current_grab != "requested":
            await q.answer("Сначала вызовите Grab.", show_alert=True)
            return
        if action == "picked_up":
            if order["payment_status"] != "received":
                await q.answer("Сначала отметьте «Деньги получены».", show_alert=True)
                return
            if current_grab != "requested":
                await q.answer("Сначала отметьте, что Grab вызван.", show_alert=True)
                return
            if order["status"] != "ready":
                await q.answer("Сначала отметьте статус «Готов».", show_alert=True)
                return
        if action == "delivered" and not (
            current_grab == "picked_up" or order["status"] == "courier"
        ):
            await q.answer("Сначала отметьте, что курьер забрал заказ.", show_alert=True)
            return
        if action == "delivered" and not order["grab_tracking_url"]:
            await q.answer("Сначала добавьте ссылку отслеживания Grab.", show_alert=True)
            return

    if parts[1] == "set":
        status_messages = {
            "new": "Мы получили ваш заказ и скоро свяжемся с вами для подтверждения.",
            "confirmed": "🐻 Ура! Ваш заказ подтверждён! Мишки приступают к работе! ❤️",
            "cooking": "🍳 Ваш заказ уже готовится! На медвежьей кухне всё кипит! 🐼",
            "ready": "🍱 Ваш заказ готов! Мишки всё проверили и аккуратно упаковали! 🐾",
            "courier": "🛵 Ваш заказ уже у курьера! Медвежьи вкусняшки едут к вам! 🍯",
            "delivered": "🏡 Ваш заказ доставлен! Мишки желают вам приятного аппетита! 🥰",
            "cancelled": "🐻 Ваш заказ отменён. До новых встреч и вкусных моментов вместе с BEAR MODE! 🐼",
        }
        if action not in status_messages:
            await q.answer("Неизвестный статус.", show_alert=True)
            return
        _set_value(order_id, "status", action)
        with db_connect() as conn:
            conn.execute("UPDATE orders SET manual_status_override = 0 WHERE id = ?", (order_id,))
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
        with db_connect() as conn:
            conn.execute("UPDATE orders SET manual_status_override = 0 WHERE id = ?", (order_id,))
        notice = "🛵 Ваш заказ уже у курьера! Медвежьи вкусняшки едут к вам! 🍯"
    elif action == "payment_received":
        _set_value(order_id, "payment_status", "received")
    elif action == "delivered":
        _set_value(order_id, "grab_status", "delivered")
        _set_value(order_id, "status", "delivered")
        with db_connect() as conn:
            conn.execute("UPDATE orders SET manual_status_override = 0 WHERE id = ?", (order_id,))
        notice = "🏡 Ваш заказ доставлен! Мишки желают вам приятного аппетита! 🥰"

    notification_error = False
    if notice:
        customer_chat_id = order["customer_chat_id"] if "customer_chat_id" in order.keys() else None
        if not customer_chat_id:
            notification_error = True
            log.error("Cannot send order status notification: customer_chat_id is missing (order_id=%s, status=%s)", order_id, action)
        else:
            try:
                await context.bot.send_message(chat_id=customer_chat_id, text=notice)
                log.info("Order status notification sent: order_id=%s status=%s customer_chat_id=%s", order_id, action, customer_chat_id)
            except Exception:
                notification_error = True
                log.exception("Could not notify customer about order status: order_id=%s status=%s customer_chat_id=%s", order_id, action, customer_chat_id)

    if action == "delivered":
        # The temporary admin message confirming the customer's initial decision
        # is no longer needed once the order has reached its final delivered stage.
        await delete_order_decision_notice(context.bot, order_id)

    updated = _get_order(order_id)
    # Keep exactly one live order card: replace its text and keyboard in place.
    await q.message.edit_text(_order_text(updated), entities=_order_entities(updated), reply_markup=_order_keyboard(updated))
    await q.answer(
        "Статус обновлён, но уведомление клиенту не отправлено. Проверьте Telegram клиента и журнал Railway."
        if notification_error else "Статус обновлён"
    )


async def delivery_reference_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save a Grab ID sent directly or as a reply to the active admin prompt."""
    message = update.effective_message
    if not _admin_only(update) or not message:
        log.warning("Grab ID reply ignored: unauthorized user or missing message")
        return
    tracking_pending = context.chat_data.get("pending_grab_tracking")
    if tracking_pending:
        reply = message.reply_to_message
        if reply and reply.message_id != tracking_pending["prompt_id"]:
            await message.reply_text("Ответьте на запрос ссылки Grab или отправьте ссылку обычным сообщением.")
            return
        raw_tracking_message = (message.text or "").strip()
        # Accept either a bare URL or a sentence containing the Grab tracking URL.
        # Store and send only the extracted URL, never the surrounding instruction text.
        matches = re.findall(r"https?://[^\s<>]+", raw_tracking_message, flags=re.IGNORECASE)
        if len(matches) != 1:
            await message.reply_text(
                "Не удалось найти одну ссылку Grab. Отправьте ссылку отдельно или вместе с текстом, например:\n"
                "Отследи свою доставку! Посмотри маршрут и расчётное время прибытия в https://express.grab.com/..."
            )
            return
        tracking_url = matches[0].rstrip(".,!?;:)]}»”’")
        parsed = urlsplit(tracking_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"express.grab.com", "www.express.grab.com"}
            or not parsed.path
            or len(tracking_url) > 2000
        ):
            await message.reply_text(
                "Некорректная ссылка Grab. Проверьте, что она начинается с https://express.grab.com/."
            )
            return
        tracking_order_id = tracking_pending["order_id"]
        tracking_order = _get_order(tracking_order_id)
        if not tracking_order:
            context.chat_data.pop("pending_grab_tracking", None)
            await message.reply_text("Заказ не найден. Откройте его заново в панели.")
            return
        if tracking_order["grab_status"] != "picked_up" or tracking_order["status"] != "courier":
            context.chat_data.pop("pending_grab_tracking", None)
            await message.reply_text("Сначала отметьте «Курьер забрал». После этого можно добавить ссылку отслеживания.")
            return
        if tracking_order["grab_tracking_url"]:
            context.chat_data.pop("pending_grab_tracking", None)
            await message.reply_text("Ссылка отслеживания уже была добавлена. Повторное изменение запрещено.")
            return
        with db_connect() as conn:
            cursor = conn.execute(
                "UPDATE orders SET grab_tracking_url = ? WHERE id = ? AND (grab_tracking_url IS NULL OR grab_tracking_url = '')",
                (tracking_url, tracking_order_id),
            )
            if cursor.rowcount != 1:
                context.chat_data.pop("pending_grab_tracking", None)
                await message.reply_text("Ссылка уже добавлена или заказ изменился. Повторное изменение запрещено.")
                return
        context.chat_data.pop("pending_grab_tracking", None)
        updated = _get_order(tracking_order_id)
        # Once the URL is safely stored, run customer notification, order-card
        # refresh, and cleanup of both temporary admin messages concurrently.
        async def _send_customer_tracking():
            customer_chat_id = updated["customer_chat_id"]
            if not customer_chat_id:
                return
            try:
                await context.bot.send_message(
                    chat_id=customer_chat_id,
                    text="🛵 Отследить доставку вашего заказа можно по ссылке ниже.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Отследить курьера Grab", url=tracking_url)]]),
                )
            except Exception:
                log.exception("Could not send Grab tracking link to customer for order %s", tracking_order_id)

        async def _refresh_order_card():
            card_id = tracking_pending.get("order_card_message_id")
            if not card_id:
                return
            try:
                await context.bot.edit_message_text(
                    chat_id=message.chat_id,
                    message_id=card_id,
                    text=_order_text(updated),
                    entities=_order_entities(updated),
                    reply_markup=_order_keyboard(updated),
                )
            except BadRequest as exc:
                log.warning("Could not refresh order card after tracking URL save: %s", exc)

        async def _delete_temporary_message(temporary_message_id):
            if not temporary_message_id:
                return
            try:
                await context.bot.delete_message(
                    chat_id=message.chat_id,
                    message_id=temporary_message_id,
                )
            except BadRequest as exc:
                log.info(
                    "Temporary Grab tracking message could not be deleted: chat_id=%s message_id=%s error=%s",
                    message.chat_id, temporary_message_id, exc,
                )

        await asyncio.gather(
            _send_customer_tracking(),
            _refresh_order_card(),
            _delete_temporary_message(tracking_pending.get("prompt_id")),
            _delete_temporary_message(message.message_id),
        )
        return

    pending = context.chat_data.get("pending_grab_reference")
    if not pending:
        log.info("Text received without pending Grab ID or tracking URL: chat_id=%s", message.chat_id)
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
        pending_order_id, message.chat_id, reply.message_id if reply else None,
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
    if order["status"] != "ready":
        context.chat_data.pop("pending_grab_reference", None)
        await message.reply_text("Сначала отметьте «Готовится», затем «Готов», и после этого укажите Grab ID.")
        return
    if order["grab_reference"]:
        context.chat_data.pop("pending_grab_reference", None)
        await message.reply_text("Grab ID уже был указан. Повторное изменение запрещено.")
        return
    with db_connect() as conn:
        cursor = conn.execute(
            "UPDATE orders SET grab_reference = ? WHERE id = ? AND (grab_reference IS NULL OR grab_reference = '')",
            (reference, pending_order_id),
        )
        if cursor.rowcount != 1:
            log.error("Grab ID save affected %s rows for order_id=%s", cursor.rowcount, pending_order_id)
            await message.reply_text("Не удалось сохранить Grab ID. Проверьте заказ и попробуйте ещё раз.")
            return
    context.chat_data.pop("pending_grab_reference", None)
    updated = _get_order(pending_order_id)
    log.info("Grab ID saved successfully: order_id=%s", pending_order_id)
    # Refresh the persistent order card and delete both temporary messages
    # concurrently. Sequential Telegram API calls made the two messages linger.
    order_card_message_id = pending.get("order_card_message_id")
    prompt_id = pending.get("prompt_id")
    tasks = []
    task_kinds = []
    if order_card_message_id:
        tasks.append(context.bot.edit_message_text(
            chat_id=message.chat_id,
            message_id=order_card_message_id,
            text=_order_text(updated),
            entities=_order_entities(updated),
            reply_markup=_order_keyboard(updated),
        ))
        task_kinds.append(("card", order_card_message_id))
    else:
        tasks.append(context.bot.send_message(
            chat_id=message.chat_id,
            text=_order_text(updated),
            entities=_order_entities(updated),
            reply_markup=_order_keyboard(updated),
        ))
        task_kinds.append(("card", None))

    for temporary_message_id in (prompt_id, message.message_id):
        if temporary_message_id:
            tasks.append(context.bot.delete_message(
                chat_id=message.chat_id,
                message_id=temporary_message_id,
            ))
            task_kinds.append(("delete", temporary_message_id))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for (kind, message_id), result in zip(task_kinds, results):
        if isinstance(result, Exception):
            if kind == "card":
                log.warning("Could not refresh order card after Grab ID save: %s", result)
                if order_card_message_id:
                    try:
                        await context.bot.send_message(
                            chat_id=message.chat_id,
                            text=_order_text(updated),
                            entities=_order_entities(updated),
                            reply_markup=_order_keyboard(updated),
                        )
                    except Exception:
                        log.exception("Could not send refreshed order card after Grab ID save")
            else:
                log.info(
                    "Temporary Grab ID message could not be deleted: chat_id=%s message_id=%s error=%s",
                    message.chat_id, message_id, result,
                )


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
            if not 5 <= len(raw) <= 1000:
                raise ValueError("Адрес должен содержать от 5 до 1000 символов.")
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
        elif field == "delivery_fee":
            normalized = raw.replace(" ", "").replace(".", "").replace(",", "")
            if not normalized.isdigit() or not 0 <= int(normalized) <= 5_000_000:
                raise ValueError("Введите сумму от 0 до 5.000.000 VND, только цифрами.")
            updates[field] = int(normalized)
            customer_notice = (
                f"Стоимость доставки: {int(normalized):,} ₫\n"
                f"Итоговая сумма с доставкой: {int(order['total']) + int(normalized):,} ₫"
            ).replace(",", ".")
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
            if total != int(order["total"]):
                # A previously received payment can no longer be assumed valid
                # after the customer-facing amount changes.
                updates["payment_status"] = "unpaid"
                customer_notice = (
                    f"Новый состав: {items_text}\n"
                    f"Новая сумма к оплате: {total:,} ₫\n"
                    "Пожалуйста, проверьте сумму. Оплату необходимо сверить повторно."
                ).replace(",", ".")
            else:
                customer_notice = (
                    f"Новый состав: {items_text}\n"
                    f"Итого к оплате: {total:,} ₫"
                ).replace(",", ".")
        else:
            raise ValueError("Неизвестное поле.")
    except ValueError as exc:
        await message.reply_text(f"⚠️ {exc}\nПопробуйте ещё раз, ответив на исходное сообщение.")
        return

    allowed_columns = {"customer_name", "phone", "address", "delivery_date", "comment", "items_text", "total", "payment_status", "delivery_fee"}
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
    card_chat_id = pending.get("order_card_chat_id", message.chat_id)
    card_message_id = pending.get("order_card_message_id")
    if card_message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=card_chat_id,
                message_id=card_message_id,
                text=_order_text(updated),
                entities=_order_entities(updated),
                reply_markup=_order_keyboard(updated),
            )
        except BadRequest as exc:
            log.warning("Could not refresh original order card after edit: %s", exc)
            await message.reply_text(
                f"✅ Заказ №{order_id} обновлён.\n\n{_order_text(updated)}",
                entities=_order_entities(updated, f"✅ Заказ №{order_id} обновлён.\n\n"),
                reply_markup=_order_keyboard(updated),
            )
    else:
        await message.reply_text(
            f"✅ Заказ №{order_id} обновлён.\n\n{_order_text(updated)}",
            entities=_order_entities(updated, f"✅ Заказ №{order_id} обновлён.\n\n"),
            reply_markup=_order_keyboard(updated),
        )


