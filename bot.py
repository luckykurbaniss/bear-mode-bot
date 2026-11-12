import asyncio
import os
import logging
import json
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
        await context.bot.send_message(
            chat_id=int(ADMIN_CHAT_ID),
            text=admin_text
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

        await context.bot.send_message(chat_id=int(ADMIN_CHAT_ID), text=admin_text)
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
    app.add_handler(CommandHandler("myid", myid))
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
