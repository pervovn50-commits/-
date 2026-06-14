import os
import logging
from datetime import datetime
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── НАСТРОЙКИ ────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВСТАВЬТЕ_ТОКЕН_СЮДА")
REPORT_CHAT_ID = os.getenv("REPORT_CHAT_ID", "ВСТАВЬТЕ_ID_ЧАТА_СЮДА")  # ID групповой беседы

# ─── ЧЕК-ЛИСТЫ ────────────────────────────────────────────────────────────────
CHECKLISTS = {
    "open": {
        "name": "🌅 Открытие ПВЗ",
        "emoji": "🌅",
        "items": [
            "Пришёл вовремя, ПВЗ открыт в срок",
            "Проверил освещение и оборудование",
            "Компьютер/терминал включён и работает",
            "Рабочее место убрано и готово",
            "Кассовый ящик проверен",
            "Расходные материалы в наличии (скотч, пакеты)",
            "Вывеска/режим работы на месте",
        ],
    },
    "close": {
        "name": "🌙 Закрытие ПВЗ",
        "emoji": "🌙",
        "items": [
            "Все посылки оприходованы в системе",
            "Касса закрыта и сверена",
            "Нерабочие заказы отложены/помечены",
            "Рабочее место убрано",
            "Оборудование выключено",
            "Дверь заперта, сигнализация включена",
            "Остатки пересчитаны и записаны",
        ],
    },
    "clean": {
        "name": "🧹 Уборка и чистота",
        "emoji": "🧹",
        "items": [
            "Пол подметён / вымыт",
            "Стойка/стол протёрты",
            "Полки для товара чистые",
            "Мусор вынесен",
            "Туалет убран (если есть)",
            "Входная зона чистая",
            "Зеркала/стёкла без пятен",
        ],
    },
    "stock": {
        "name": "📦 Остатки и товары",
        "emoji": "📦",
        "items": [
            "Пересчитан входящий товар",
            "Излишки/недостача зафиксированы",
            "Бракованные позиции помечены",
            "Полки заполнены и аккуратно расставлены",
            "Ценники актуальны",
            "Расходники пополнены или заявка отправлена",
        ],
    },
}

# ─── СОСТОЯНИЯ ────────────────────────────────────────────────────────────────
CHOOSING_LIST, DOING_CHECKLIST, WAITING_PHOTO, CONFIRM_SEND = range(4)

# ─── ХРАНИЛИЩЕ СЕССИЙ ─────────────────────────────────────────────────────────
# { user_id: { list_key, name, items_done: {idx: bool}, photos: [file_id], current_item } }
sessions = {}


def get_session(user_id):
    return sessions.get(user_id, {})


def set_session(user_id, data):
    sessions[user_id] = data


# ─── /start ───────────────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    keyboard = [
        [InlineKeyboardButton("🌅 Открытие ПВЗ", callback_data="list_open")],
        [InlineKeyboardButton("🌙 Закрытие ПВЗ", callback_data="list_close")],
        [InlineKeyboardButton("🧹 Уборка и чистота", callback_data="list_clean")],
        [InlineKeyboardButton("📦 Остатки и товары", callback_data="list_stock")],
    ]
    await update.message.reply_text(
        f"Привет, {user.first_name}! 👋\n\n"
        "Выбери чек-лист для заполнения:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CHOOSING_LIST


# ─── ВЫБОР ЧЕК-ЛИСТА ──────────────────────────────────────────────────────────
async def choose_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    list_key = query.data.replace("list_", "")
    user = query.from_user

    checklist = CHECKLISTS[list_key]
    set_session(user.id, {
        "list_key": list_key,
        "name": user.first_name,
        "username": user.username or user.first_name,
        "items_done": {},
        "photos": [],
        "current_item": None,
        "started_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
    })

    await query.edit_message_text(
        f"{checklist['emoji']} *{checklist['name']}*\n\n"
        "Пройдём по каждому пункту. Нажимай ✅ если выполнено, ❌ если нет.\n"
        "Фото можно прикрепить на любом шаге командой /photo\n\n"
        "Начинаем! 👇",
        parse_mode="Markdown"
    )
    await send_checklist(update, ctx, user.id, edit=False)
    return DOING_CHECKLIST


async def send_checklist(update, ctx, user_id, edit=False):
    session = get_session(user_id)
    list_key = session["list_key"]
    checklist = CHECKLISTS[list_key]
    items = checklist["items"]
    done = session["items_done"]

    keyboard = []
    for i, item in enumerate(items):
        status = "✅" if done.get(i) is True else ("❌" if done.get(i) is False else "⬜")
        keyboard.append([InlineKeyboardButton(
            f"{status} {item}", callback_data=f"item_{i}"
        )])

    keyboard.append([
        InlineKeyboardButton("📸 Прикрепить фото", callback_data="add_photo"),
    ])

    completed = sum(1 for v in done.values() if v is True)
    failed = sum(1 for v in done.values() if v is False)

    if len(done) == len(items):
        keyboard.append([
            InlineKeyboardButton("📤 Отправить отчёт", callback_data="send_report")
        ])

    photos_count = len(session.get("photos", []))
    text = (
        f"{checklist['emoji']} *{checklist['name']}*\n"
        f"👤 {session['name']} | 🕐 {session['started_at']}\n\n"
        f"✅ Выполнено: {completed} | ❌ Не выполнено: {failed} | ⬜ Осталось: {len(items) - len(done)}\n"
        f"📸 Фото: {photos_count}\n\n"
        "Отметь каждый пункт:"
    )

    msg = update.callback_query.message if update.callback_query else update.message
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
    else:
        await msg.reply_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )


# ─── ОТМЕТКА ПУНКТА ───────────────────────────────────────────────────────────
async def handle_item(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    session = get_session(user_id)

    if query.data == "add_photo":
        await query.message.reply_text(
            "📸 Отправь фото прямо сейчас (можно несколько).\n"
            "Когда закончишь — нажми /done_photo"
        )
        session["waiting_photo"] = True
        set_session(user_id, session)
        return WAITING_PHOTO

    if query.data == "send_report":
        await send_report(update, ctx, user_id)
        return ConversationHandler.END

    if query.data.startswith("item_"):
        idx = int(query.data.replace("item_", ""))
        checklist = CHECKLISTS[session["list_key"]]
        items = checklist["items"]

        # Показываем кнопки ✅/❌
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Выполнено", callback_data=f"mark_ok_{idx}"),
                InlineKeyboardButton("❌ Не выполнено", callback_data=f"mark_fail_{idx}"),
            ],
            [InlineKeyboardButton("« Назад к списку", callback_data="back_to_list")],
        ])
        await query.edit_message_text(
            f"Пункт {idx + 1} из {len(items)}:\n\n*{items[idx]}*\n\nКак отметить?",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    elif query.data.startswith("mark_ok_") or query.data.startswith("mark_fail_"):
        ok = query.data.startswith("mark_ok_")
        idx = int(query.data.split("_")[-1])
        session["items_done"][idx] = ok
        set_session(user_id, session)
        await send_checklist(update, ctx, user_id, edit=True)

    elif query.data == "back_to_list":
        await send_checklist(update, ctx, user_id, edit=True)

    return DOING_CHECKLIST


# ─── ПРИЁМ ФОТО ───────────────────────────────────────────────────────────────
async def receive_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)

    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        session.setdefault("photos", []).append(file_id)
        set_session(user_id, session)
        await update.message.reply_text(
            f"📸 Фото сохранено! Всего: {len(session['photos'])}\n"
            "Отправь ещё или напиши /done_photo чтобы вернуться к чек-листу."
        )
    return WAITING_PHOTO


async def done_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)
    session["waiting_photo"] = False
    set_session(user_id, session)

    checklist = CHECKLISTS[session["list_key"]]
    items = checklist["items"]
    done = session["items_done"]

    keyboard = []
    for i, item in enumerate(items):
        status = "✅" if done.get(i) is True else ("❌" if done.get(i) is False else "⬜")
        keyboard.append([InlineKeyboardButton(f"{status} {item}", callback_data=f"item_{i}")])
    keyboard.append([InlineKeyboardButton("📸 Прикрепить фото", callback_data="add_photo")])
    if len(done) == len(items):
        keyboard.append([InlineKeyboardButton("📤 Отправить отчёт", callback_data="send_report")])

    photos_count = len(session.get("photos", []))
    completed = sum(1 for v in done.values() if v is True)
    failed = sum(1 for v in done.values() if v is False)

    text = (
        f"{checklist['emoji']} *{checklist['name']}*\n"
        f"👤 {session['name']} | 🕐 {session['started_at']}\n\n"
        f"✅ Выполнено: {completed} | ❌ Не выполнено: {failed} | ⬜ Осталось: {len(items) - len(done)}\n"
        f"📸 Фото: {photos_count}\n\n"
        "Отметь каждый пункт:"
    )
    await update.message.reply_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )
    return DOING_CHECKLIST


# ─── ОТПРАВКА ОТЧЁТА ──────────────────────────────────────────────────────────
async def send_report(update, ctx, user_id):
    session = get_session(user_id)
    list_key = session["list_key"]
    checklist = CHECKLISTS[list_key]
    items = checklist["items"]
    done = session["items_done"]

    completed = sum(1 for v in done.values() if v is True)
    failed = sum(1 for v in done.values() if v is False)
    total = len(items)

    lines = [
        f"{'─' * 30}",
        f"{checklist['emoji']} *ОТЧЁТ: {checklist['name'].upper()}*",
        f"{'─' * 30}",
        f"👤 Сотрудник: {session['name']} (@{session['username']})",
        f"📅 Время: {session['started_at']}",
        f"📤 Отправлено: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        f"{'─' * 30}",
        f"📋 *РЕЗУЛЬТАТ: {completed}/{total}*",
        "",
    ]

    for i, item in enumerate(items):
        status = "✅" if done.get(i) is True else ("❌" if done.get(i) is False else "⬜ не проверено")
        lines.append(f"{status} {item}")

    lines += [
        "",
        f"{'─' * 30}",
        f"📸 Фото: {len(session.get('photos', []))} шт.",
        f"{'─' * 30}",
    ]

    if failed > 0:
        lines.append(f"\n⚠️ *Внимание: {failed} пункт(а) не выполнено!*")

    report_text = "\n".join(lines)

    # Отправляем текст
    await ctx.bot.send_message(
        chat_id=REPORT_CHAT_ID,
        text=report_text,
        parse_mode="Markdown"
    )

    # Отправляем фото
    photos = session.get("photos", [])
    if photos:
        from telegram import InputMediaPhoto
        media = [InputMediaPhoto(media=fid) for fid in photos[:10]]  # max 10
        await ctx.bot.send_media_group(chat_id=REPORT_CHAT_ID, media=media)

    # Подтверждение сотруднику
    msg = update.callback_query.message if update.callback_query else update.message
    await msg.reply_text(
        f"✅ Отчёт успешно отправлен!\n\n"
        f"📊 Итог: {completed}/{total} выполнено\n"
        f"📸 Фото: {len(photos)} шт.\n\n"
        "Хорошей смены! 💪\n\n"
        "Для нового чек-листа — /start"
    )

    # Очищаем сессию
    sessions.pop(user_id, None)


# ─── ОТМЕНА ───────────────────────────────────────────────────────────────────
async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sessions.pop(update.effective_user.id, None)
    await update.message.reply_text(
        "Чек-лист отменён. Для начала — /start"
    )
    return ConversationHandler.END


# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING_LIST: [CallbackQueryHandler(choose_list, pattern="^list_")],
            DOING_CHECKLIST: [
                CallbackQueryHandler(handle_item),
            ],
            WAITING_PHOTO: [
                MessageHandler(filters.PHOTO, receive_photo),
                CommandHandler("done_photo", done_photo),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    logger.info("Бот запущен ✅")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
