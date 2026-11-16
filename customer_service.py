import logging
import os
import sqlite3

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Update
from telegram.ext import ContextTypes

log = logging.getLogger(__name__)
DB_PATH = os.getenv("DB_PATH", "bear_mode.db").strip()
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()

MENU_TEXT = """🐼 Актуальное меню BEAR MODE

🍱 ГОТОВЫЕ СЕТЫ
• Сет «Энергия» — 170.000 ₫
• Сет «Атлантика» — 170.000 ₫
• Сет «Комфорт» — 170.000 ₫
• Сет «Характер» — 170.000 ₫

🍽 ОСНОВНЫЕ БЛЮДА
• Гречка с грибами — 40.000 ₫
• Рис с овощами — 40.000 ₫
• Картофельное пюре — 40.000 ₫
• Куриная грудка — 70.000 ₫
• Лосось — 80.000 ₫
• Филе лосося — 80.000 ₫
• Домашние котлеты (2 шт.) — 75.000 ₫
• Куриное бедро — 70.000 ₫

🥗 САЛАТЫ
• Овощной салат — 50.000 ₫
• Салат «Свежесть» — 50.000 ₫
• Витаминный салат — 50.000 ₫

🥚 ДОПОЛНЕНИЯ
• Омлет — 15.000 ₫
• Яйцо отварное — 15.000 ₫
• Морс — 15.000 ₫
• Багет — 15.000 ₫


💵 Оплата: наличными или переводом."""

SET_DETAILS = {
    "энергия": "Сет «Энергия»: гречка с грибами, куриная грудка, овощной салат, яйцо отварное, морс и багет.",
    "атлантика": "Сет «Атлантика»: рис с овощами, филе лосося, салат «Свежесть», омлет, морс и багет.",
    "комфорт": "Сет «Комфорт»: картофельное пюре, домашние котлеты (2 шт.), витаминный салат, омлет, морс и багет.",
    "характер": "Сет «Характер»: картофельное пюре, куриное бедро, салат «Свежесть», яйцо отварное, морс и багет.",
}

TEMPLATES = {
    "welcome_confirm": ("Приветствие и подтверждение", "Добрый день! 🐻\n\n🐼 Спасибо за заказ в BEAR MODE"),
    "welcome_confirm_qr": ("Приветствие и подтверждение — QR", "Добрый день! 🐻\n\n🐼 Спасибо за заказ в BEAR MODE"),
    "prices": ("Отправить меню и цены", MENU_TEXT),
    "delivery": ("Условия доставки", "Напишите, пожалуйста, ваш адрес доставки — мы согласуем детали доставки 😊"),
    "payment": ("Способы оплаты", "Оплатить заказ можно наличными или переводом. Какой способ вам удобнее? 😊"),
    "ingredients": ("Состав блюда", "С удовольствием подскажем! Напишите, пожалуйста, какое именно блюдо вас интересует, и мы уточним его состав."),
    "allergy": ("Аллергия / продукты", "Спасибо, что предупредили! Напишите, пожалуйста, какие продукты вам нельзя употреблять. Мы проверим состав и уточним, можем ли учесть ваши пожелания."),
    "add": ("Добавить блюдо", "Конечно! Напишите, пожалуйста, какое блюдо и в каком количестве хотите добавить. Мы проверим возможность и пересчитаем сумму."),
    "change": ("Изменить заказ", "Без проблем! Напишите, пожалуйста, что именно хотите изменить. Мы проверим возможность изменений и сообщим новую стоимость."),
    "remove": ("Убрать блюдо", "Уточните, пожалуйста, какое блюдо необходимо убрать. Мы проверим, можно ли изменить заказ на этом этапе, и пересчитаем сумму."),
    "total": ("Итоговая сумма", "Стоимость вашего заказа составляет {total}."),
    "cancel": ("Отмена заказа", "Подскажите, пожалуйста, причину отмены. Мы проверим текущий статус заказа и возможность отмены."),
    "delay": ("Задержка доставки", "Приносим извинения за ожидание! 🙏 Мы уточним ситуацию и сообщим вам актуальную информацию как можно скорее."),
    "complaint": ("Жалоба / проблема", "Нам очень жаль, что возникла такая ситуация. Расскажите, пожалуйста, подробнее — мы обязательно разберёмся и постараемся найти решение."),
    "thanks": ("Поблагодарить", "Спасибо за добрые слова! ❤️ Нам очень приятно. Будем рады приготовить для вас снова!"),
    "review": ("Попросить отзыв", "Будем очень благодарны, если вы поделитесь впечатлениями о нашем заказе! Ваше мнение помогает нам становиться лучше ❤️"),
    "delivery_thanks_review": ("🐻🐼 Благодарность после доставки", "🐻🐼 Спасибо, что выбрали BEAR MODE! ❤️\n\nНадеемся, наш обед подарил вам немного домашнего тепла и вкусного настроения! 🥰\n\nБудем очень рады, если вы поделитесь впечатлениями и оставите отзыв. Для нас это очень ценно! 🫰\n\nДо новых вкусных встреч! Крепко обнимаем медвежьими лапками! 🐾\n\nС любовью, BEAR MODE"),
    "closing": ("Завершить общение", "Спасибо, что выбираете BEAR MODE! ❤️ Желаем вам прекрасного дня! 🐼"),
    "received": ("Передать вопрос администратору", "Спасибо за сообщение! Мы передали ваш вопрос администратору BEAR MODE. ❤️"),
}

CATEGORIES = {
    "start": ("👋 Приветствие и подтверждение", ["welcome_confirm", "welcome_confirm_qr"]),
    "menu": ("🍱 Меню и цены", ["prices", "total"]),
    "delivery": ("🚚 Доставка", ["delivery", "delay"]),
    "payment": ("💵 Оплата", ["payment"]),
    "food": ("🥗 Блюда и состав", ["ingredients", "allergy"]),
    "order": ("🧾 Изменение заказа", ["add", "change", "remove", "cancel", "total"]),
    "problems": ("🛠 Проблемы и жалобы", ["delay", "complaint", "received"]),
    "feedback": ("❤️ Отзывы и благодарность", ["delivery_thanks_review", "thanks", "review", "closing"]),
    "other": ("💬 Другие ответы", ["received", "closing"]),
}


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_order_decision_notices(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS order_decision_notices (
            order_id INTEGER PRIMARY KEY,
            admin_chat_id TEXT NOT NULL,
            message_id INTEGER NOT NULL
        )
        """
    )


async def delete_order_decision_notice(bot, order_id):
    """Delete the admin's temporary 'customer confirmed/cancelled' notice after delivery."""
    with _db() as conn:
        _ensure_order_decision_notices(conn)
        notice = conn.execute(
            "SELECT admin_chat_id, message_id FROM order_decision_notices WHERE order_id = ?",
            (int(order_id),),
        ).fetchone()
    if not notice:
        return False
    try:
        await bot.delete_message(
            chat_id=int(notice["admin_chat_id"]),
            message_id=int(notice["message_id"]),
        )
    except Exception:
        log.exception(
            "Could not delete customer decision notice: order_id=%s chat_id=%s message_id=%s",
            order_id, notice["admin_chat_id"], notice["message_id"],
        )
        return False
    with _db() as conn:
        conn.execute("DELETE FROM order_decision_notices WHERE order_id = ?", (int(order_id),))
    log.info("Deleted customer decision notice after delivery: order_id=%s", order_id)
    return True


def _admin(update: Update) -> bool:
    return bool(ADMIN_CHAT_ID) and update.effective_user is not None and str(update.effective_user.id) == ADMIN_CHAT_ID


def _order(order_id):
    with _db() as conn:
        return conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()


def _latest_order(chat_id):
    with _db() as conn:
        return conn.execute(
            "SELECT * FROM orders WHERE customer_chat_id = ? ORDER BY id DESC LIMIT 1",
            (chat_id,),
        ).fetchone()


def _ensure_order_conversation(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS order_conversation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            direction TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            telegram_message_id INTEGER
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_order_conversation_order ON order_conversation(order_id, id)"
    )


def _save_order_message(order_id, direction, body, telegram_message_id=None):
    if not order_id or not body:
        return
    with _db() as conn:
        _ensure_order_conversation(conn)
        conn.execute(
            "INSERT INTO order_conversation(order_id, direction, body, telegram_message_id) VALUES (?, ?, ?, ?)",
            (int(order_id), direction, str(body)[:4000], telegram_message_id),
        )


def _order_conversation_text(order_id):
    with _db() as conn:
        _ensure_order_conversation(conn)
        rows = conn.execute(
            "SELECT direction, body, created_at FROM order_conversation WHERE order_id = ? ORDER BY id DESC LIMIT 30",
            (int(order_id),),
        ).fetchall()
    if not rows:
        return f"💬 Переписка по заказу №{order_id} пока пуста."
    labels = {"customer": "👤 Клиент", "admin": "🐻 Администратор", "system": "🤖 BEAR MODE"}
    lines = [f"💬 ПЕРЕПИСКА ПО ЗАКАЗУ №{order_id}", ""]
    for row in reversed(rows):
        stamp = row["created_at"] or ""
        lines.append(f"{labels.get(row['direction'], 'Сообщение')} · {stamp}")
        lines.append(row["body"])
        lines.append("")
    result = "\n".join(lines).strip()
    return result[-3900:]


def _target_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"support:cat:{key}")]
        for key, (label, _) in CATEGORIES.items()
    ] + [[InlineKeyboardButton("✖️ Закрыть", callback_data="support:close")]])


def _customer_order_confirmation_keyboard(order_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Подтверждаю", callback_data=f"customer_order:confirmed:{order_id}")],
        [InlineKeyboardButton("❌ Отменить", callback_data=f"customer_order:cancelled:{order_id}")],
    ])


async def send_order_welcome(bot, order_id):
    """Send the matching ready-made welcome template with order decision buttons."""
    order = _order(order_id)
    if not order or not order["customer_chat_id"]:
        log.warning("Cannot send order welcome: order %s has no customer chat", order_id)
        return
    template_id = "welcome_confirm_qr" if order["payment_method"] == "qr" else "welcome_confirm"
    await bot.send_message(
        chat_id=int(order["customer_chat_id"]),
        text=_format_template(template_id, order),
        reply_markup=_customer_order_confirmation_keyboard(int(order_id)),
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


async def customer_order_confirmation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    if len(parts) != 3 or parts[1] not in {"confirmed", "cancelled"}:
        await q.answer("Некорректное действие.", show_alert=True)
        return
    order_id = int(parts[2])
    new_status = parts[1]
    with _db() as conn:
        order = conn.execute("SELECT id, customer_chat_id, status FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not order or int(order["customer_chat_id"] or 0) != int(update.effective_chat.id):
            await q.answer("Заказ не найден.", show_alert=True)
            return
        if order["status"] != "new":
            await q.answer("По этому заказу уже принято решение.", show_alert=True)
            return
        conn.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, order_id))
    if new_status == "confirmed":
        customer_text = "🐻 Ура! Ваш заказ подтверждён! Мишки приступают к работе! ❤️"
        admin_text = f"✅ Клиент подтвердил заказ BEAR MODE №{order_id}."
    else:
        customer_text = "🐻 Ваш заказ отменён. До новых встреч и вкусных моментов вместе с BEAR MODE! 🐼"
        admin_text = f"❌ Клиент отменил заказ BEAR MODE №{order_id}."
    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception:
        log.exception("Could not remove customer order confirmation buttons")
    await q.message.reply_text(customer_text)
    if ADMIN_CHAT_ID:
        try:
            admin_notice = await context.bot.send_message(chat_id=int(ADMIN_CHAT_ID), text=admin_text)
            with _db() as conn:
                _ensure_order_decision_notices(conn)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO order_decision_notices (order_id, admin_chat_id, message_id)
                    VALUES (?, ?, ?)
                    """,
                    (order_id, str(ADMIN_CHAT_ID), int(admin_notice.message_id)),
                )
        except Exception:
            log.exception("Could not notify admin about customer order decision")
    await q.answer("Готово")

def _preview_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📨 Отправить клиенту", callback_data="support:send")],
        [InlineKeyboardButton("✏️ Изменить текст", callback_data="support:edit"),
         InlineKeyboardButton("✖️ Отмена", callback_data="support:cancel")],
    ])


def support_order_button(order_id):
    return [InlineKeyboardButton("💬 Ответить клиенту", callback_data=f"support:order:{order_id}")]


def _format_template(template_id, order):
    text = TEMPLATES[template_id][1]
    if order and template_id in {"welcome_confirm", "welcome_confirm_qr"}:
        total = f"{int(order['total']):,}".replace(",", ".") + " ₫"
        items = (order["items_text"] or "Состав заказа не указан").strip()
        address = (order["address"] or "Адрес не указан").strip()
        payment_line = (
            "💳 Оплата: перевод по QR-коду Vietcombank"
            if template_id == "welcome_confirm_qr"
            else "💵 Оплата: при получении"
        )
        # Keep the exact delivery address and item list from the order.
        text = (
            "🐻 Топ-топ! Медвежья доставка на связи! 🐼\n\n"
            "Большой привет от BEAR MODE ❤️\n\n"
            "Ваш заказ успешно попал в наши лапки 🐾\n\n"
            "Осталось проверить детали, чтобы наши мишки ничего не перепутали.\n\n"
            "🧺 ВАША МЕДВЕЖЬЯ КОРЗИНКА:\n\n"
            f"📍 Адрес: {address}\n\n"
            f"🍱 {items}\n\n"
            f"🧾 Заказ №{order['id']}\n\n"
            f"💰 Итого: {total}\n\n"
            f"{payment_line}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🐾 Если всё верно — подтвердите заказ!\n\n"
            "А дальше дело за нами: приготовим с душой, аккуратно упакуем "
            "и передадим ваш заказ в дорогу.\n\n"
            "Спасибо, что заглянули в нашу медвежью берлогу! ❤️"
        )
    elif order and template_id == "total":
        total = f"{int(order['total']):,}".replace(",", ".") + " ₫"
        text = text.format(total=total)
    elif "{total}" in text:
        text = text.replace("{total}", "уточним после проверки заказа")
    return text

async def support_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not _admin(update):
        await q.answer("Нет доступа.", show_alert=True)
        return

    parts = q.data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "history":
        order_id = int(parts[2])
        order = _order(order_id)
        if not order:
            await q.answer("Заказ не найден.", show_alert=True)
            return
        await q.message.reply_text(
            _order_conversation_text(order_id),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ К заказу", callback_data=f"adm:order:{order_id}")
            ]]),
        )
        await q.answer()
        return

    if action in {"order", "target"}:
        if action == "order":
            order_id = int(parts[2])
            order = _order(order_id)
            if not order or not order["customer_chat_id"]:
                await q.answer("У заказа нет доступного чата клиента.", show_alert=True)
                return
            chat_id = int(order["customer_chat_id"])
        else:
            chat_id = int(parts[2])
            order_id = int(parts[3]) if len(parts) > 3 else 0
            order = _order(order_id) if order_id else _latest_order(chat_id)

        context.user_data["support_target"] = {
            "chat_id": chat_id,
            "order_id": order_id or (order["id"] if order else 0),
        }
        order_label = f"Заказ №{order['id']}" if order else "Без привязанного заказа"
        await q.message.reply_text(
            f"🐼 Ответ клиенту\n{order_label}\n\nВыберите тему сообщения:",
            reply_markup=_target_keyboard(),
        )
        await q.answer()
        return

    if action == "cat":
        category = parts[2]
        if category not in CATEGORIES or not context.user_data.get("support_target"):
            await q.answer("Сначала выберите клиента.", show_alert=True)
            return
        title, template_ids = CATEGORIES[category]
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(TEMPLATES[key][0], callback_data=f"support:tpl:{key}")]
             for key in template_ids]
            + [[InlineKeyboardButton("⬅️ К темам", callback_data="support:back")]]
        )
        await q.edit_message_text(f"🐼 {title}\n\nВыберите готовый ответ:", reply_markup=keyboard)
        await q.answer()
        return

    if action == "back":
        await q.edit_message_text("Выберите тему сообщения:", reply_markup=_target_keyboard())
        await q.answer()
        return

    if action == "tpl":
        template_id = parts[2]
        target = context.user_data.get("support_target")
        if template_id not in TEMPLATES or not target:
            await q.answer("Шаблон или клиент не найден.", show_alert=True)
            return
        order = _order(target["order_id"]) if target.get("order_id") else None
        draft = _format_template(template_id, order)
        context.user_data["support_draft"] = {
            "chat_id": target["chat_id"],
            "order_id": target.get("order_id", 0),
            "template_id": template_id,
            "text": draft,
        }
        preview = await q.message.reply_text(
            draft,
            reply_markup=_preview_keyboard(),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        context.user_data["support_preview_message_id"] = preview.message_id
        await q.answer()
        return

    if action == "edit":
        if not context.user_data.get("support_draft"):
            await q.answer("Сначала выберите шаблон.", show_alert=True)
            return
        prompt = await q.message.reply_text(
            "✏️ Отправьте новый текст ответом на это сообщение или ответом на сам черновик. "
            "Можно также отправить текст обычным сообщением в чат. "
            "После обновления нажмите «Отправить клиенту»."
        )
        context.user_data["support_edit_prompt_message_id"] = prompt.message_id
        context.user_data["support_edit_mode"] = True
        await q.answer("Жду исправленный текст")
        return

    if action == "send":
        draft = context.user_data.get("support_draft")
        if not draft or not draft.get("chat_id") or not draft.get("text", "").strip():
            await q.answer("Черновик пустой или не найден. Создайте его заново.", show_alert=True)
            return

        # Сначала отправляем клиенту именно актуальный текст черновика.
        try:
            sent = await context.bot.send_message(
                chat_id=int(draft["chat_id"]),
                text=draft["text"].strip(),
                reply_markup=(_customer_order_confirmation_keyboard(int(draft["order_id"])) if draft.get("order_id") and draft.get("template_id") in {"welcome_confirm", "welcome_confirm_qr"} else None),
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
        except Exception as exc:
            log.exception(
                "Could not send support reply: order_id=%s customer_chat_id=%s",
                draft.get("order_id"), draft.get("chat_id"),
            )
            await q.answer(f"Не отправлено: {str(exc)[:180]}", show_alert=True)
            return

        order_id = draft.get("order_id") or 0
        if order_id:
            _save_order_message(order_id, "admin", draft["text"].strip(), sent.message_id)
        log.info(
            "Admin support reply delivered: message_id=%s order_id=%s customer_chat_id=%s",
            sent.message_id, order_id, draft["chat_id"],
        )
        context.user_data.pop("support_draft", None)
        context.user_data.pop("support_preview_message_id", None)
        context.user_data.pop("support_edit_prompt_message_id", None)
        context.user_data.pop("support_edit_mode", None)

        # Не допускаем повторной отправки по старой кнопке.
        try:
            await q.edit_message_text("✅ Сообщение отправлено клиенту.")
        except Exception:
            log.exception("Customer reply sent, but could not update admin preview")
        await q.answer("Отправлено клиенту")
        return

    if action in {"cancel", "close"}:
        context.user_data.pop("support_draft", None)
        context.user_data.pop("support_preview_message_id", None)
        context.user_data.pop("support_edit_prompt_message_id", None)
        context.user_data.pop("support_edit_mode", None)
        if action == "close":
            context.user_data.pop("support_target", None)
        try:
            await q.message.delete()
        except Exception:
            log.exception("Could not delete cancelled customer-message draft")
        await q.answer()
        return


async def support_reply_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Update the active customer-message draft from a reply or direct admin text."""
    message = update.effective_message
    if not _admin(update) or not message or not message.text:
        return
    preview_id = context.user_data.get("support_preview_message_id")
    edit_prompt_id = context.user_data.get("support_edit_prompt_message_id")
    draft = context.user_data.get("support_draft")
    if not preview_id or not draft:
        return

    reply_id = message.reply_to_message.message_id if message.reply_to_message else None
    is_reply_to_draft = reply_id == preview_id
    is_reply_to_edit_prompt = bool(edit_prompt_id and reply_id == edit_prompt_id)
    is_direct_edit = bool(context.user_data.get("support_edit_mode") and reply_id is None)
    # Telegram's native "Edit message" produces edited_message updates, not new
    # text messages. Accept edits to the actual preview as the authoritative draft.
    is_native_edit_of_draft = message.message_id == preview_id
    if not (is_reply_to_draft or is_reply_to_edit_prompt or is_direct_edit or is_native_edit_of_draft):
        return

    new_text = message.text.strip()
    if not new_text:
        await message.reply_text("Текст ответа не должен быть пустым.")
        return
    if len(new_text) > 4000:
        await message.reply_text("Сообщение слишком длинное. Максимум 4000 символов.")
        return

    # Editing a draft must never send it to the customer. Keep the updated
    # text in admin context and refresh the preview with an explicit Send button.
    draft["text"] = new_text
    try:
        await context.bot.edit_message_text(
            chat_id=message.chat_id,
            message_id=preview_id,
            text=new_text,
            reply_markup=_preview_keyboard(),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except Exception:
        log.exception("Could not refresh edited customer-message draft preview")
        await message.reply_text("Не удалось обновить черновик. Клиенту сообщение не отправлено.")
        return

    context.user_data["support_draft"] = draft
    context.user_data.pop("support_edit_prompt_message_id", None)
    context.user_data.pop("support_edit_mode", None)
    await message.reply_text("✅ Черновик обновлён. Проверьте текст и нажмите «Отправить клиенту», когда будете готовы.")


def _faq_answer(text):
    value = text.lower().replace("ё", "е")
    if any(word in value for word in ("достав", "курьер", "привез")):
        if any(word in value for word in ("время", "когда", "во сколько", "долго")):
            return "Время доставки согласуем при подтверждении заказа. Напишите, пожалуйста, удобное для вас время 😊"
        return "Напишите, пожалуйста, ваш точный адрес доставки. 🚚"
    if any(word in value for word in ("цен", "стоим", "сколько", "прайс", "меню")):
        return MENU_TEXT
    if any(word in value for word in ("оплат", "налич", "перевод", "перевести")):
        return "Оплатить заказ можно наличными или переводом. 😊"
    if any(word in value for word in ("аллерг", "неперенос", "нельзя", "без ")):
        return None
    if any(word in value for word in ("измен", "добав", "убрать", "отмен", "замен", "исправ")):
        return None
    if any(word in value for word in ("состав", "ингредиент", "что входит", "из чего", "входит в")):
        for set_name, details in SET_DETAILS.items():
            if set_name in value:
                return details
        return "С удовольствием расскажем о составе! Напишите, пожалуйста, название блюда или сета, который вас интересует."
    if any(word in value for word in ("привет", "здравств", "добрый день", "доброе утро")):
        return "Добрый день! 🐼 Спасибо, что написали в BEAR MODE. Чем можем вам помочь?"
    return None


async def customer_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Forward every private customer message (text or media) to the admin."""
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat or chat.type != "private":
        return
    if _admin(update) or (message.text or "").startswith("/"):
        return

    body = (message.text or message.caption or "").strip()
    if not body:
        if message.photo:
            body = "[Фото]"
        elif message.video:
            body = "[Видео]"
        elif message.voice:
            body = "[Голосовое сообщение]"
        elif message.audio:
            body = "[Аудио]"
        elif message.document:
            body = f"[Файл: {message.document.file_name or 'без названия'}]"
        elif message.contact:
            body = "[Контакт]"
        elif message.location:
            body = "[Геопозиция]"
        elif message.sticker:
            body = "[Стикер]"
        else:
            body = "[Вложение или сообщение другого типа]"

    log.info(
        "Incoming customer message detected: chat_id=%s message_id=%s content_type=%s",
        chat.id, message.message_id, getattr(message, "effective_attachment", None).__class__.__name__
        if getattr(message, "effective_attachment", None) is not None else ("text" if message.text else "other"),
    )

    answer = _faq_answer(body) if message.text else None
    order = _latest_order(chat.id)
    if order:
        _save_order_message(order["id"], "customer", body, message.message_id)
    order_label = f"Заказ №{order['id']}" if order else "Заказ не найден"
    username = f"@{user.username}" if user.username else f"Telegram ID {user.id}"
    alert = (
        "💬 СООБЩЕНИЕ ОТ КЛИЕНТА BEAR MODE\n\n"
        f"👤 {user.full_name}\n"
        f"🔗 {username}\n"
        f"🧾 {order_label}\n\n"
        f"Сообщение:\n{body[:3000]}"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "💬 Ответить клиенту",
            callback_data=f"support:target:{chat.id}:{order['id'] if order else 0}",
        )
    ]])

    try:
        if not ADMIN_CHAT_ID:
            raise RuntimeError("ADMIN_CHAT_ID is empty")
        admin_chat_id = int(ADMIN_CHAT_ID)
        sent = await context.bot.send_message(
            chat_id=admin_chat_id,
            text=alert,
            reply_markup=keyboard,
        )
        # Preserve the original media/attachment in the admin chat as well.
        if not message.text and not message.contact and not message.location:
            await context.bot.copy_message(
                chat_id=admin_chat_id,
                from_chat_id=chat.id,
                message_id=message.message_id,
                reply_to_message_id=sent.message_id,
            )
        if order and answer:
            _save_order_message(order["id"], "system", answer)
        log.info(
            "Customer message forwarded successfully: customer_chat_id=%s message_id=%s admin_chat_id=%s order_id=%s",
            chat.id, message.message_id, admin_chat_id, order["id"] if order else None,
        )
    except Exception:
        log.exception(
            "FAILED forwarding customer message: customer_chat_id=%s message_id=%s admin_chat_id_configured=%s",
            chat.id, message.message_id, bool(ADMIN_CHAT_ID),
        )
        if answer:
            await message.reply_text(answer)
        else:
            await message.reply_text(
                "Извините, не удалось передать сообщение администратору. "
                "Пожалуйста, попробуйте ещё раз немного позже."
            )
        return

    if answer:
        await message.reply_text(answer)
        if order:
            _save_order_message(order["id"], "system", answer)
    else:
        acknowledgement = "Спасибо за сообщение! ❤️ Мы передали его администратору BEAR MODE."
        await message.reply_text(acknowledgement)
        if order:
            _save_order_message(order["id"], "system", acknowledgement)
