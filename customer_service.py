import logging
import os
import sqlite3

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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

🚚 Доставка оплачивается отдельно.
💵 Оплата: наличными или переводом."""

SET_DETAILS = {
    "энергия": "Сет «Энергия»: гречка с грибами, куриная грудка, овощной салат, яйцо отварное, морс и багет.",
    "атлантика": "Сет «Атлантика»: рис с овощами, филе лосося, салат «Свежесть», омлет, морс и багет.",
    "комфорт": "Сет «Комфорт»: картофельное пюре, домашние котлеты (2 шт.), витаминный салат, омлет, морс и багет.",
    "характер": "Сет «Характер»: картофельное пюре, куриное бедро, салат «Свежесть», яйцо отварное, морс и багет.",
}

TEMPLATES = {
    "welcome_confirm": ("Приветствие и подтверждение", "Добрый день! 🐻\n\n🐼 Спасибо за заказ в BEAR MODE"),
    "prices": ("Отправить меню и цены", MENU_TEXT),
    "delivery": ("Условия доставки", "Доставка оплачивается отдельно. Напишите, пожалуйста, адрес доставки — мы уточним стоимость и возможность доставки в ваш район 😊"),
    "payment": ("Способы оплаты", "Оплатить заказ можно наличными или переводом. Какой способ вам удобнее? 😊"),
    "ingredients": ("Состав блюда", "С удовольствием подскажем! Напишите, пожалуйста, какое именно блюдо вас интересует, и мы уточним его состав."),
    "allergy": ("Аллергия / продукты", "Спасибо, что предупредили! Напишите, пожалуйста, какие продукты вам нельзя употреблять. Мы проверим состав и уточним, можем ли учесть ваши пожелания."),
    "add": ("Добавить блюдо", "Конечно! Напишите, пожалуйста, какое блюдо и в каком количестве хотите добавить. Мы проверим возможность и пересчитаем сумму."),
    "change": ("Изменить заказ", "Без проблем! Напишите, пожалуйста, что именно хотите изменить. Мы проверим возможность изменений и сообщим новую стоимость."),
    "remove": ("Убрать блюдо", "Уточните, пожалуйста, какое блюдо необходимо убрать. Мы проверим, можно ли изменить заказ на этом этапе, и пересчитаем сумму."),
    "total": ("Итоговая сумма", "Стоимость вашего заказа составляет {total}. Доставка оплачивается отдельно."),
    "cancel": ("Отмена заказа", "Подскажите, пожалуйста, причину отмены. Мы проверим текущий статус заказа и возможность отмены."),
    "delay": ("Задержка доставки", "Приносим извинения за ожидание! 🙏 Мы уточним ситуацию и сообщим вам актуальную информацию как можно скорее."),
    "complaint": ("Жалоба / проблема", "Нам очень жаль, что возникла такая ситуация. Расскажите, пожалуйста, подробнее — мы обязательно разберёмся и постараемся найти решение."),
    "thanks": ("Поблагодарить", "Спасибо за добрые слова! ❤️ Нам очень приятно. Будем рады приготовить для вас снова!"),
    "review": ("Попросить отзыв", "Будем очень благодарны, если вы поделитесь впечатлениями о нашем заказе! Ваше мнение помогает нам становиться лучше ❤️"),
    "closing": ("Завершить общение", "Спасибо, что выбираете BEAR MODE! ❤️ Желаем вам прекрасного дня! 🐼"),
    "received": ("Передать вопрос администратору", "Спасибо за сообщение! Мы уточним информацию и вернёмся к вам с ответом 😊"),
}

CATEGORIES = {
    "start": ("👋 Приветствие и подтверждение", ["welcome_confirm"]),
    "menu": ("🍱 Меню и цены", ["prices", "total"]),
    "delivery": ("🚚 Доставка", ["delivery", "delay"]),
    "payment": ("💵 Оплата", ["payment"]),
    "food": ("🥗 Блюда и состав", ["ingredients", "allergy"]),
    "order": ("🧾 Изменение заказа", ["add", "change", "remove", "cancel", "total"]),
    "problems": ("🛠 Проблемы и жалобы", ["delay", "complaint", "received"]),
    "feedback": ("❤️ Отзывы и благодарность", ["thanks", "review", "closing"]),
    "other": ("💬 Другие ответы", ["received", "closing"]),
}


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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


def _target_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"support:cat:{key}")]
        for key, (label, _) in CATEGORIES.items()
    ] + [[InlineKeyboardButton("✖️ Закрыть", callback_data="support:close")]])


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
    if order and template_id == "welcome_confirm":
        total = f"{int(order['total']):,}".replace(",", ".") + " ₫"
        items = (order["items_text"] or "Состав заказа не указан").strip()
        address = (order["address"] or "Адрес не указан").strip()
        text = (
            "Добрый день! 🐻\n\n🐼 Спасибо за заказ в BEAR MODE\n\n"
            f"📍 Адрес: {address}\n"
            f"🍱 Заказ: {items}\n"
            f"💰 Итого к оплате: {total} + … ₫ "
            "(стоимость доставки рассчитывается отдельно в зависимости от вашего адреса).\n\n"
            "Если всё верно, пожалуйста, подтвердите заказ — и мы сразу возьмём его в работу. "
            "Следите за статусом в боте ❤️"
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
            "text": draft,
        }
        preview = await q.message.reply_text(
            "✏️ Черновик сообщения клиенту:\n\n"
            + draft
            + "\n\nЧтобы изменить текст, ответьте на это сообщение своим вариантом. "
              "Затем нажмите «Отправить клиенту».",
            reply_markup=_preview_keyboard(),
            disable_web_page_preview=True,
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
        if not draft:
            await q.answer("Черновик не найден.", show_alert=True)
            return
        try:
            await context.bot.send_message(
                chat_id=draft["chat_id"],
                text=draft["text"],
                disable_web_page_preview=True,
            )
        except Exception:
            log.exception("Could not send support reply to customer")
            await q.answer("Не удалось отправить. Проверьте, доступен ли чат клиента.", show_alert=True)
            return
        order_id = draft.get("order_id") or 0
        log.info("Admin sent support reply: order_id=%s customer_chat_id=%s", order_id, draft["chat_id"])
        context.user_data.pop("support_draft", None)
        context.user_data.pop("support_preview_message_id", None)
        context.user_data.pop("support_edit_prompt_message_id", None)
        context.user_data.pop("support_edit_mode", None)
        await q.edit_message_text("✅ Сообщение отправлено клиенту.")
        await q.answer("Отправлено")
        return

    if action in {"cancel", "close"}:
        context.user_data.pop("support_draft", None)
        context.user_data.pop("support_preview_message_id", None)
        context.user_data.pop("support_edit_prompt_message_id", None)
        context.user_data.pop("support_edit_mode", None)
        if action == "close":
            context.user_data.pop("support_target", None)
        await q.edit_message_text("Действие отменено.")
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
    if not (is_reply_to_draft or is_reply_to_edit_prompt or is_direct_edit):
        return

    new_text = message.text.strip()
    if not new_text:
        await message.reply_text("Текст ответа не должен быть пустым.")
        return
    if len(new_text) > 4000:
        await message.reply_text("Сообщение слишком длинное. Максимум 4000 символов.")
        return

    draft["text"] = new_text
    try:
        await context.bot.edit_message_text(
            chat_id=message.chat_id,
            message_id=preview_id,
            text="✏️ Обновлённый черновик сообщения клиенту:\n\n" + new_text
                 + "\n\nНажмите «Отправить клиенту» или измените текст ответом на это сообщение.",
            reply_markup=_preview_keyboard(),
            disable_web_page_preview=True,
        )
        context.user_data["support_edit_mode"] = False
        context.user_data.pop("support_edit_prompt_message_id", None)
        # Не отправляем отдельное подтверждение: обновлённый текст уже виден в черновике.
    except Exception:
        log.exception("Could not update support draft preview")
        await message.reply_text("Не удалось обновить предпросмотр. Черновик не отправлен. Попробуйте ещё раз.")


def _faq_answer(text):
    value = text.lower().replace("ё", "е")
    if any(word in value for word in ("достав", "курьер", "привез")):
        if any(word in value for word in ("время", "когда", "во сколько", "долго")):
            return "Время доставки согласуем при подтверждении заказа. Напишите, пожалуйста, удобное для вас время 😊"
        return "Доставка оплачивается отдельно. Напишите, пожалуйста, ваш адрес — мы уточним стоимость и возможность доставки в ваш район. 🚚"
    if any(word in value for word in ("цен", "стоим", "сколько", "прайс", "меню")):
        return MENU_TEXT
    if any(word in value for word in ("оплат", "налич", "перевод", "перевести")):
        return "Оплатить заказ можно наличными или переводом. Доставка оплачивается отдельно. 😊"
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
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not message.text or not user or not chat or chat.type != "private":
        return
    if _admin(update) or message.text.startswith("/"):
        return
    # Do not intercept text while the customer is completing the legacy bot order flow.
    if any(key in context.user_data for key in ("set_id", "quantity", "name", "phone", "address", "comment")):
        return

    answer = _faq_answer(message.text)
    if answer:
        await message.reply_text(answer)
        return

    order = _latest_order(chat.id)
    order_label = f"Заказ №{order['id']}" if order else "Заказ не найден"
    username = f"@{user.username}" if user.username else f"Telegram ID {user.id}"
    alert = (
        "💬 ВОПРОС КЛИЕНТА BEAR MODE\n\n"
        f"👤 {user.full_name}\n"
        f"🔗 {username}\n"
        f"🧾 {order_label}\n\n"
        f"Сообщение:\n{message.text[:3000]}"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "💬 Ответить клиенту",
            callback_data=f"support:target:{chat.id}:{order['id'] if order else 0}",
        )
    ]])
    try:
        await context.bot.send_message(chat_id=int(ADMIN_CHAT_ID), text=alert, reply_markup=keyboard)
        await message.reply_text(
            "Спасибо за сообщение! ❤️ Передали ваш вопрос администратору BEAR MODE. "
            "Мы вернёмся к вам с ответом."
        )
    except Exception:
        log.exception("Could not escalate customer message to admin")
        await message.reply_text(
            "Извините, сейчас не удалось передать сообщение. Пожалуйста, попробуйте ещё раз немного позже."
        )
