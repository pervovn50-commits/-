import os
import json
import logging
from datetime import datetime, timezone, time as dtime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── НАСТРОЙКИ ────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВСТАВЬТЕ_ТОКЕН")
REPORT_CHAT_ID = os.getenv("REPORT_CHAT_ID", "ВСТАВЬТЕ_ID_ЧАТА")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # Ваш личный Telegram ID

USERS_FILE = "users.json"  # Хранилище пользователей

# ─── СОСТОЯНИЯ ────────────────────────────────────────────────────────────────
(
    MAIN_MENU,
    DOING_OPEN, OPEN_PHOTO,
    DOING_CLOSE, CLOSE_PHOTO_SHIPMENT, CLOSE_PHOTO_RECEPTION, CLOSE_PHOTO_PVZ,
    CLOSE_SUPPLIES, CLOSE_SUPPLIES_COMMENT,
    DOING_CLEAN, CLEAN_PHOTOS,
    DOING_INVENTORY, INVENTORY_INPUT, INVENTORY_PHOTO,
    WAITING_ANY_PHOTO,
    ENTERING_NAME,
) = range(16)

# ─── ХРАНИЛИЩЕ ────────────────────────────────────────────────────────────────
sessions = {}  # { user_id: {...} }
active_breaks = {}  # { user_id: {"started_at": datetime, "job_name": str} }


def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_users(users):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def get_user(user_id):
    users = load_users()
    return users.get(str(user_id))


def is_approved(user_id):
    u = get_user(user_id)
    return u and u.get("status") == "approved"


def is_admin(user_id):
    return user_id == ADMIN_ID


def esc(text):
    """Экранирует спецсимволы Markdown: _ * ` ["""
    for ch in ["_", "*", "`", "["]:
        text = str(text).replace(ch, f"\\{ch}")
    return text


# ─── ВСПОМОГАТЕЛЬНЫЕ ──────────────────────────────────────────────────────────
def get_session(user_id):
    return sessions.get(user_id, {})


def set_session(user_id, data):
    sessions[user_id] = data


def now_str():
    return datetime.now().strftime("%d.%m.%Y %H:%M")


def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌅 Открытие ПВЗ", callback_data="menu_open")],
        [InlineKeyboardButton("🌙 Закрытие ПВЗ", callback_data="menu_close")],
        [InlineKeyboardButton("🧹 Уборка и чистота", callback_data="menu_clean")],
        [InlineKeyboardButton("📦 Инвентаризация", callback_data="menu_inventory")],
        [InlineKeyboardButton("☕ Перерыв", callback_data="menu_break")],
    ])


# ─── /start — РЕГИСТРАЦИЯ ─────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = str(user.id)
    users = load_users()

    # Если уже одобрен — сразу главное меню
    if uid in users and users[uid]["status"] == "approved":
        city = users[uid].get("city", "—")
        real_name = users[uid].get("name", user.first_name)
        await update.message.reply_text(
            f"Привет, {esc(real_name)}! 👋\n🏙 Город: {city}\n\nВыбери чек-лист:",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU

    # Если уже ожидает одобрения
    if uid in users and users[uid]["status"] == "pending":
        await update.message.reply_text(
            "⏳ Твоя заявка уже отправлена, ожидай одобрения от руководителя."
        )
        return ConversationHandler.END

    # Новый пользователь — спрашиваем имя
    await update.message.reply_text(
        "👋 Привет! Добро пожаловать в бот ПВЗ.\n\n"
        "Для регистрации напиши своё настоящее имя и фамилию:\n"
        "(например: Мария Иванова)"
    )
    return ENTERING_NAME


async def receive_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получаем настоящее имя сотрудника и отправляем заявку"""
    user = update.effective_user
    uid = str(user.id)
    real_name = update.message.text.strip()

    # Минимальная валидация
    if len(real_name) < 2 or len(real_name) > 60:
        await update.message.reply_text(
            "⚠️ Имя должно быть от 2 до 60 символов. Попробуй ещё раз:"
        )
        return ENTERING_NAME

    users = load_users()
    users[uid] = {
        "status": "pending",
        "name": real_name,
        "username": user.username or "",
        "city": None,
        "registered_at": now_str(),
    }
    save_users(users)

    await update.message.reply_text(
        f"✅ Отлично, {esc(real_name)}!\n\n"
        "Заявка на доступ отправлена руководителю.\n"
        "Как только тебя одобрят — получишь уведомление.\n\n"
        "⏳ Ожидай..."
    )

    # Уведомляем администратора
    uname_str = f"@{user.username}" if user.username else "без username"
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Одобрить (Гатчина)", callback_data=f"approve_{uid}_Гатчина"),
            InlineKeyboardButton("✅ Одобрить (Всеволожск)", callback_data=f"approve_{uid}_Всеволожск"),
        ],
        [InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{uid}")],
    ])

    await ctx.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"🔔 Новая заявка на доступ\n\n"
             f"👤 Имя: {real_name}\n"
             f"📱 Telegram: {uname_str}\n"
             f"🆔 ID: {user.id}\n"
             f"📅 {now_str()}\n\n"
             f"Выбери город и одобри или отклони:",
        reply_markup=keyboard,
    )
    return ConversationHandler.END


# ─── ОБРАБОТКА ЗАЯВОК АДМИНИСТРАТОРОМ ────────────────────────────────────────
async def handle_admin_action(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    data = query.data
    users = load_users()

    if data.startswith("approve_"):
        parts = data.split("_", 2)
        uid = parts[1]
        city = parts[2]
        if uid in users:
            users[uid]["status"] = "approved"
            users[uid]["city"] = city
            save_users(users)
            name = users[uid]["name"]
            await query.edit_message_text(
                f"✅ Сотрудник *{esc(name)}* одобрен\n🏙 Город: *{city}*",
                parse_mode="Markdown"
            )
            await ctx.bot.send_message(
                chat_id=int(uid),
                text=f"✅ Доступ одобрен!\n\n"
                     f"🏙 Твой город: *{city}*\n\n"
                     f"Напиши /start чтобы начать работу.",
                parse_mode="Markdown"
            )

    elif data.startswith("reject_"):
        uid = data.replace("reject_", "")
        if uid in users:
            name = users[uid]["name"]
            users[uid]["status"] = "rejected"
            save_users(users)
            await query.edit_message_text(f"❌ Сотрудник *{esc(name)}* отклонён.", parse_mode="Markdown")
            await ctx.bot.send_message(
                chat_id=int(uid),
                text="❌ К сожалению, в доступе отказано. Обратитесь к руководителю."
            )

    elif data.startswith("remove_"):
        uid = data.replace("remove_", "")
        if uid in users:
            name = users[uid]["name"]
            del users[uid]
            save_users(users)
            await query.edit_message_text(f"🗑 Сотрудник *{esc(name)}* удалён.", parse_mode="Markdown")
            try:
                await ctx.bot.send_message(
                    chat_id=int(uid),
                    text="⛔ Ваш доступ к боту был отозван руководителем."
                )
            except Exception:
                pass


# ─── /staff — список сотрудников (только для админа) ─────────────────────────
async def staff_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    users = load_users()
    approved = {uid: u for uid, u in users.items() if u["status"] == "approved"}
    pending = {uid: u for uid, u in users.items() if u["status"] == "pending"}

    text = "👥 *Сотрудники с доступом:*\n\n"
    if approved:
        for uid, u in approved.items():
            uname = f"@{esc(u['username'])}" if u.get("username") else "—"
            text += f"• {esc(u['name'])} ({uname}) — {u.get('city', '—')}\n"
    else:
        text += "_Нет одобренных сотрудников_\n"

    keyboard = []
    for uid, u in approved.items():
        keyboard.append([InlineKeyboardButton(
            f"🗑 Удалить {u['name']} ({u.get('city','—')})",
            callback_data=f"remove_{uid}"
        )])

    if pending:
        text += f"\n⏳ *Ожидают одобрения: {len(pending)}*\n"
        for uid, u in pending.items():
            uname = f"@{esc(u['username'])}" if u.get("username") else "—"
            text += f"• {esc(u['name'])} ({uname})\n"
            keyboard.append([
                InlineKeyboardButton(f"✅ {u['name']} (Гатчина)", callback_data=f"approve_{uid}_Гатчина"),
                InlineKeyboardButton(f"✅ (Всеволожск)", callback_data=f"approve_{uid}_Всеволожск"),
            ])
            keyboard.append([InlineKeyboardButton(f"❌ Отклонить {u['name']}", callback_data=f"reject_{uid}")])

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
        parse_mode="Markdown"
    )


# ─── ГЛАВНОЕ МЕНЮ ─────────────────────────────────────────────────────────────
async def main_menu_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if not is_approved(user_id):
        await query.message.reply_text("⛔ У вас нет доступа.")
        return ConversationHandler.END

    user_info = get_user(user_id)
    city = user_info.get("city", "—")
    name = user_info.get("name", query.from_user.first_name)
    username = user_info.get("username") or query.from_user.first_name

    base_session = {
        "name": name,
        "username": username,
        "city": city,
        "started_at": now_str(),
        "photos": {},
    }

    action = query.data

    if action == "menu_open":
        set_session(user_id, {**base_session, "type": "open"})
        await query.edit_message_text(
            f"🌅 ОТКРЫТИЕ ПВЗ\n🏙 {city}\n\n"
            "Нажми кнопку, чтобы зафиксировать открытие смены.\n"
            "Затем прикрепи обязательное фото.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Открываю смену!", callback_data="open_confirm")],
                [InlineKeyboardButton("« Назад в меню", callback_data="back_to_main_menu")],
            ])
        )
        return DOING_OPEN

    elif action == "menu_close":
        set_session(user_id, {**base_session, "type": "close",
                               "all_shipped": None,
                               "photos": {"shipment": None, "reception": None, "pvz": None}})
        await query.edit_message_text(
            f"🌙 ЗАКРЫТИЕ ПВЗ\n🏙 {city}\n\n"
            "Все посылки отгружены?",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Да, все отгружены", callback_data="shipped_yes"),
                    InlineKeyboardButton("❌ Нет", callback_data="shipped_no"),
                ],
                [InlineKeyboardButton("« Назад в меню", callback_data="close_back_to_menu")],
            ])
        )
        return DOING_CLOSE

    elif action == "menu_clean":
        set_session(user_id, {**base_session, "type": "clean",
                               "items": {}, "photos": []})
        return await show_clean_checklist(update, ctx, user_id, edit=True)

    elif action == "menu_inventory":
        set_session(user_id, {**base_session, "type": "inventory",
                               "found": None, "not_found": None, "photo": None})
        await query.edit_message_text(
            f"📦 ИНВЕНТАРИЗАЦИЯ\n🏙 {city}\n\n"
            "Введи количество найденных посылок (цифрой):",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("« Назад в меню", callback_data="back_to_main_menu")]
            ])
        )
        return INVENTORY_INPUT

    elif action == "back_to_main_menu":
        sessions.pop(user_id, None)
        await query.edit_message_text(
            f"Привет, {esc(name)}! 👋\n🏙 Город: {city}\n\nВыбери чек-лист:",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU


# ══════════════════════════════════════════════════════════════════════════════
# 🌅 ОТКРЫТИЕ ПВЗ
# ══════════════════════════════════════════════════════════════════════════════

async def open_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    session = get_session(user_id)
    session["confirmed"] = True
    set_session(user_id, session)

    await query.edit_message_text(
        "✅ Отлично! Теперь прикрепи фото — обязательно.\n\n"
        "📸 Отправь фото прямо сейчас:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("« Назад", callback_data="open_back")]
        ])
    )
    return OPEN_PHOTO


async def open_receive_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)

    if update.callback_query:
        await update.callback_query.answer()
        if update.callback_query.data == "open_back":
            await update.callback_query.edit_message_text(
                "🌅 ОТКРЫТИЕ ПВЗ\n\n"
                "Нажми кнопку, чтобы зафиксировать открытие смены.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Открываю смену!", callback_data="open_confirm")],
                    [InlineKeyboardButton("« Назад в меню", callback_data="back_to_main_menu")],
                ])
            )
            return DOING_OPEN
        return OPEN_PHOTO

    if not update.message.photo:
        await update.message.reply_text(
            "📸 Нужно отправить фото!",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("« Назад", callback_data="open_back")
            ]])
        )
        return OPEN_PHOTO

    session["open_photo"] = update.message.photo[-1].file_id
    set_session(user_id, session)

    await update.message.reply_text(
        "✅ Фото получено! Отправляю отчёт...",
    )
    await send_open_report(update, ctx, user_id)
    return ConversationHandler.END


async def send_open_report(update, ctx, user_id):
    session = get_session(user_id)
    text = (
        f"{'─' * 30}\n"
        f"🌅 ОТКРЫТИЕ ПВЗ\n"
        f"{'─' * 30}\n"
        f"👤 {session['name']} (@{session['username']})\n"
        f"🏙 Город: {session['city']}\n"
        f"🕐 Время открытия: {session['started_at']}\n"
        f"{'─' * 30}\n"
        f"✅ Смена открыта\n"
        f"📸 Фото прилагается"
    )
    await ctx.bot.send_message(chat_id=REPORT_CHAT_ID, text=text)
    if session.get("open_photo"):
        await ctx.bot.send_photo(chat_id=REPORT_CHAT_ID, photo=session["open_photo"])

    await update.message.reply_text(
        "✅ Отчёт об открытии отправлен! Хорошей смены! 💪\n\n/start — вернуться в меню"
    )
    sessions.pop(user_id, None)


# ══════════════════════════════════════════════════════════════════════════════
# 🌙 ЗАКРЫТИЕ ПВЗ
# ══════════════════════════════════════════════════════════════════════════════

async def close_shipped(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    session = get_session(user_id)

    if query.data == "close_back_to_menu":
        sessions.pop(user_id, None)
        await query.edit_message_text(
            "Закрытие отменено. Выбери чек-лист:",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU

    session["all_shipped"] = (query.data == "shipped_yes")
    set_session(user_id, session)

    await query.edit_message_text(
        "📸 Шаг 1 из 3 — отправь фото страницы ОТГРУЗКИ:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("« Назад", callback_data="close_back_to_shipped")]
        ])
    )
    return CLOSE_PHOTO_SHIPMENT


async def close_photo_shipment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)

    # Кнопка "Назад" через callback
    if update.callback_query:
        await update.callback_query.answer()
        if update.callback_query.data == "close_back_to_shipped":
            await update.callback_query.edit_message_text(
                "📦 Все посылки отгружены?",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("✅ Да, все отгружены", callback_data="shipped_yes"),
                        InlineKeyboardButton("❌ Нет", callback_data="shipped_no"),
                    ],
                    [InlineKeyboardButton("« Назад в меню", callback_data="close_back_to_menu")],
                ])
            )
            return DOING_CLOSE
        return CLOSE_PHOTO_SHIPMENT

    if not update.message.photo:
        await update.message.reply_text(
            "📸 Нужно отправить фото страницы отгрузки!\n\nИли нажми «Назад»:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("« Назад", callback_data="close_back_to_shipped")
            ]])
        )
        return CLOSE_PHOTO_SHIPMENT
    session["photos"]["shipment"] = update.message.photo[-1].file_id
    set_session(user_id, session)
    await update.message.reply_text(
        "✅ Принято!\n\n📸 Шаг 2 из 3 — отправь фото страницы ПРИЁМКИ:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("« Назад", callback_data="close_back_to_shipment")
        ]])
    )
    return CLOSE_PHOTO_RECEPTION


async def close_photo_reception(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)

    if update.callback_query:
        await update.callback_query.answer()
        if update.callback_query.data == "close_back_to_shipment":
            session["photos"]["shipment"] = None
            set_session(user_id, session)
            await update.callback_query.edit_message_text(
                "📸 Шаг 1 из 3 — отправь фото страницы ОТГРУЗКИ:",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("« Назад", callback_data="close_back_to_shipped")
                ]])
            )
            return CLOSE_PHOTO_SHIPMENT
        return CLOSE_PHOTO_RECEPTION

    if not update.message.photo:
        await update.message.reply_text(
            "📸 Нужно отправить фото страницы приёмки!\n\nИли нажми «Назад»:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("« Назад", callback_data="close_back_to_shipment")
            ]])
        )
        return CLOSE_PHOTO_RECEPTION
    session["photos"]["reception"] = update.message.photo[-1].file_id
    set_session(user_id, session)
    await update.message.reply_text(
        "✅ Принято!\n\n📸 Шаг 3 из 3 — отправь фото ПВЗ в конце смены:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("« Назад", callback_data="close_back_to_reception")
        ]])
    )
    return CLOSE_PHOTO_PVZ


async def close_photo_pvz(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)

    if update.callback_query:
        await update.callback_query.answer()
        if update.callback_query.data == "close_back_to_reception":
            session["photos"]["reception"] = None
            set_session(user_id, session)
            await update.callback_query.edit_message_text(
                "📸 Шаг 2 из 3 — отправь фото страницы ПРИЁМКИ:",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("« Назад", callback_data="close_back_to_shipment")
                ]])
            )
            return CLOSE_PHOTO_RECEPTION
        return CLOSE_PHOTO_PVZ

    if not update.message.photo:
        await update.message.reply_text(
            "📸 Нужно отправить фото ПВЗ!\n\nИли нажми «Назад»:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("« Назад", callback_data="close_back_to_reception")
            ]])
        )
        return CLOSE_PHOTO_PVZ
    session["photos"]["pvz"] = update.message.photo[-1].file_id
    set_session(user_id, session)

    # Спрашиваем про расходники
    await update.message.reply_text(
        "🗂 Все расходники есть?\n(скотч, пакеты, чековая лента и т.д.)",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Да, всё есть", callback_data="supplies_yes"),
                InlineKeyboardButton("❌ Нет, не хватает", callback_data="supplies_no"),
            ],
            [InlineKeyboardButton("« Назад", callback_data="close_back_to_pvz")],
        ])
    )
    return CLOSE_SUPPLIES


async def close_supplies(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    session = get_session(user_id)

    if query.data == "close_back_to_pvz":
        session["photos"]["pvz"] = None
        set_session(user_id, session)
        await query.edit_message_text(
            "📸 Шаг 3 из 3 — отправь фото ПВЗ в конце смены:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("« Назад", callback_data="close_back_to_reception")
            ]])
        )
        return CLOSE_PHOTO_PVZ

    if query.data == "supplies_yes":
        session["supplies_ok"] = True
        session["supplies_comment"] = None
        set_session(user_id, session)
        await query.edit_message_text("✅ Отлично! Отправляю отчёт...")
        await send_close_report(query.message, ctx, user_id)
        return ConversationHandler.END

    if query.data == "supplies_no":
        session["supplies_ok"] = False
        set_session(user_id, session)
        await query.edit_message_text(
            "📝 Напиши что именно не хватает:\n(например: скотч, пакеты, чековая лента)",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("« Назад", callback_data="supplies_back")
            ]])
        )
        return CLOSE_SUPPLIES_COMMENT


async def close_supplies_comment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)

    # Кнопка назад
    if update.callback_query:
        await update.callback_query.answer()
        if update.callback_query.data == "supplies_back":
            await update.callback_query.edit_message_text(
                "🗂 Все расходники есть?",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("✅ Да, всё есть", callback_data="supplies_yes"),
                        InlineKeyboardButton("❌ Нет, не хватает", callback_data="supplies_no"),
                    ],
                    [InlineKeyboardButton("« Назад", callback_data="close_back_to_pvz")],
                ])
            )
            return CLOSE_SUPPLIES
        return CLOSE_SUPPLIES_COMMENT

    if not update.message.text:
        await update.message.reply_text("✏️ Напиши текстом что не хватает:")
        return CLOSE_SUPPLIES_COMMENT

    session["supplies_comment"] = update.message.text.strip()
    set_session(user_id, session)
    await update.message.reply_text("✅ Записал! Отправляю отчёт...")
    await send_close_report(update.message, ctx, user_id)
    return ConversationHandler.END


async def send_close_report(message, ctx, user_id):
    session = get_session(user_id)
    shipped = "✅ Да" if session.get("all_shipped") else "❌ Нет"
    supplies_ok = session.get("supplies_ok", True)
    supplies_comment = session.get("supplies_comment")

    supplies_line = "✅ Все расходники есть" if supplies_ok else f"❌ Не хватает расходников: {supplies_comment}"

    text = (
        f"{'─' * 30}\n"
        f"🌙 ЗАКРЫТИЕ ПВЗ\n"
        f"{'─' * 30}\n"
        f"👤 {session['name']} (@{session['username']})\n"
        f"🏙 Город: {session['city']}\n"
        f"🕐 Время: {session['started_at']}\n"
        f"📤 Отправлено: {now_str()}\n"
        f"{'─' * 30}\n"
        f"📦 Все посылки отгружены: {shipped}\n"
        f"🗂 Расходники: {supplies_line}\n"
        f"{'─' * 30}\n"
        f"📸 Фото отгрузки: ✅\n"
        f"📸 Фото приёмки: ✅\n"
        f"📸 Фото ПВЗ: ✅"
    )

    # Акцент если не хватает расходников
    if not supplies_ok:
        text += f"\n\n⚠️ ВНИМАНИЕ! Сотрудник сообщил о нехватке расходников!\nНужно: {supplies_comment}"

    await ctx.bot.send_message(chat_id=REPORT_CHAT_ID, text=text)

    from telegram import InputMediaPhoto
    photos = session["photos"]
    media = []
    if photos.get("shipment"):
        media.append(InputMediaPhoto(media=photos["shipment"], caption="📄 Страница отгрузки"))
    if photos.get("reception"):
        media.append(InputMediaPhoto(media=photos["reception"], caption="📄 Страница приёмки"))
    if photos.get("pvz"):
        media.append(InputMediaPhoto(media=photos["pvz"], caption="🏪 ПВЗ в конце смены"))
    if media:
        await ctx.bot.send_media_group(chat_id=REPORT_CHAT_ID, media=media)

    await message.reply_text(
        "✅ Отчёт о закрытии отправлен! До завтра! 👋\n\n/start — вернуться в меню"
    )
    sessions.pop(user_id, None)


# ══════════════════════════════════════════════════════════════════════════════
# 🧹 УБОРКА И ЧИСТОТА
# ══════════════════════════════════════════════════════════════════════════════

CLEAN_ITEMS = [
    "Пол подметён / вымыт",
    "Стойка/стол протёрты",
    "Зеркала/стёкла без пятен",
]


async def show_clean_checklist(update, ctx, user_id, edit=False):
    session = get_session(user_id)
    done = session.get("items", {})
    photos = session.get("photos", [])
    completed = sum(1 for v in done.values() if v is True)
    failed = sum(1 for v in done.values() if v is False)

    keyboard = []
    for i, item in enumerate(CLEAN_ITEMS):
        status = "✅" if done.get(i) is True else ("❌" if done.get(i) is False else "⬜")
        keyboard.append([InlineKeyboardButton(f"{status} {item}", callback_data=f"clean_item_{i}")])

    keyboard.append([InlineKeyboardButton(f"📸 Добавить фото ({len(photos)}/3)", callback_data="clean_add_photo")])

    all_marked = len(done) == len(CLEAN_ITEMS)
    has_enough_photos = len(photos) >= 3
    if all_marked and has_enough_photos:
        keyboard.append([InlineKeyboardButton("📤 Отправить отчёт", callback_data="clean_send")])
    elif all_marked and not has_enough_photos:
        keyboard.append([InlineKeyboardButton(f"⚠️ Нужно минимум 3 фото (есть {len(photos)})", callback_data="noop")])

    keyboard.append([InlineKeyboardButton("« Назад в меню", callback_data="back_to_main_menu")])

    text = (
        f"🧹 УБОРКА И ЧИСТОТА\n"
        f"🏙 {session['city']}\n\n"
        f"✅ {completed} | ❌ {failed} | ⬜ {len(CLEAN_ITEMS) - len(done)}\n"
        f"📸 Фото: {len(photos)} (нужно минимум 3)\n\n"
        "Отметь каждый пункт:"
    )

    msg = update.callback_query.message if update.callback_query else update.message
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return DOING_CLEAN


async def clean_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    session = get_session(user_id)

    if query.data == "noop":
        return DOING_CLEAN

    if query.data == "clean_add_photo":
        await query.message.reply_text(
            "📸 Отправь фото (нужно минимум 3).\n"
            "Когда закончишь — напиши /done_photo"
        )
        session["waiting_photo_for"] = "clean"
        set_session(user_id, session)
        return CLEAN_PHOTOS

    if query.data == "clean_send":
        photos = session.get("photos", [])
        if len(photos) < 3:
            await query.answer("⚠️ Нужно минимум 3 фото!", show_alert=True)
            return DOING_CLEAN
        await send_clean_report(query, ctx, user_id)
        return ConversationHandler.END

    if query.data.startswith("clean_item_"):
        idx = int(query.data.replace("clean_item_", ""))
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Выполнено", callback_data=f"clean_mark_ok_{idx}"),
                InlineKeyboardButton("❌ Не выполнено", callback_data=f"clean_mark_fail_{idx}"),
            ],
            [InlineKeyboardButton("« Назад", callback_data="clean_back")],
        ])
        await query.edit_message_text(
            f"{CLEAN_ITEMS[idx]}\n\nКак отметить?",
            reply_markup=kb
        )
        return DOING_CLEAN

    if query.data.startswith("clean_mark_"):
        parts = query.data.split("_")
        ok = parts[2] == "ok"
        idx = int(parts[3])
        session["items"][idx] = ok
        set_session(user_id, session)
        return await show_clean_checklist(update, ctx, user_id, edit=True)

    if query.data == "clean_back":
        return await show_clean_checklist(update, ctx, user_id, edit=True)

    return DOING_CLEAN


async def clean_receive_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)
    if update.message.photo:
        session.setdefault("photos", []).append(update.message.photo[-1].file_id)
        set_session(user_id, session)
        await update.message.reply_text(
            f"📸 Фото {len(session['photos'])} добавлено!\n"
            "Отправь ещё или напиши /done_photo"
        )
    return CLEAN_PHOTOS


async def clean_done_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)
    photos = session.get("photos", [])
    if len(photos) < 3:
        await update.message.reply_text(f"⚠️ Нужно минимум 3 фото! Сейчас: {len(photos)}. Отправь ещё.")
        return CLEAN_PHOTOS
    return await show_clean_checklist(update, ctx, user_id, edit=False)


async def send_clean_report(query_or_update, ctx, user_id):
    session = get_session(user_id)
    done = session.get("items", {})
    photos = session.get("photos", [])
    completed = sum(1 for v in done.values() if v is True)
    failed = sum(1 for v in done.values() if v is False)

    lines = [
        f"{'─' * 30}",
        f"🧹 УБОРКА И ЧИСТОТА",
        f"{'─' * 30}",
        f"👤 {session['name']} (@{session['username']})",
        f"🏙 Город: {session['city']}",
        f"🕐 {session['started_at']}",
        f"📤 Отправлено: {now_str()}",
        f"{'─' * 30}",
        f"📋 Результат: {completed}/{len(CLEAN_ITEMS)}",
        "",
    ]
    for i, item in enumerate(CLEAN_ITEMS):
        status = "✅" if done.get(i) is True else ("❌" if done.get(i) is False else "⬜")
        lines.append(f"{status} {item}")
    lines += [f"{'─' * 30}", f"📸 Фото: {len(photos)} шт."]
    if failed > 0:
        lines.append(f"\n⚠️ {failed} пункт(а) не выполнено!")

    await ctx.bot.send_message(chat_id=REPORT_CHAT_ID, text="\n".join(lines))

    if photos:
        from telegram import InputMediaPhoto
        media = [InputMediaPhoto(media=fid) for fid in photos[:10]]
        await ctx.bot.send_media_group(chat_id=REPORT_CHAT_ID, media=media)

    msg = query_or_update.message if hasattr(query_or_update, 'message') else query_or_update
    await msg.reply_text("✅ Отчёт об уборке отправлен!\n\n/start — вернуться в меню")
    sessions.pop(user_id, None)


# ══════════════════════════════════════════════════════════════════════════════
# 📦 ИНВЕНТАРИЗАЦИЯ
# ══════════════════════════════════════════════════════════════════════════════

async def inventory_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)
    text = update.message.text.strip()

    if session.get("found") is None:
        if not text.isdigit():
            await update.message.reply_text(
                "⚠️ Введи число! Сколько посылок найдено?",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("« Назад в меню", callback_data="back_to_main_menu")
                ]])
            )
            return INVENTORY_INPUT
        session["found"] = int(text)
        set_session(user_id, session)
        await update.message.reply_text(
            "✅ Принято! Теперь введи количество НЕ найденных посылок:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("« Назад", callback_data="inventory_back_found")
            ]])
        )
        return INVENTORY_INPUT

    if session.get("not_found") is None:
        if not text.isdigit():
            await update.message.reply_text(
                "⚠️ Введи число! Сколько посылок не найдено?",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("« Назад", callback_data="inventory_back_found")
                ]])
            )
            return INVENTORY_INPUT
        session["not_found"] = int(text)
        set_session(user_id, session)
        await update.message.reply_text(
            f"✅ Записал!\n\n"
            f"📦 Найдено: {session['found']}\n"
            f"❌ Не найдено: {session['not_found']}\n\n"
            f"📸 Теперь прикрепи обязательное фото инвентаризации:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("« Назад", callback_data="inventory_back_notfound")
            ]])
        )
        return INVENTORY_PHOTO

    return INVENTORY_INPUT


async def inventory_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    session = get_session(user_id)

    if query.data == "inventory_back_found":
        session["found"] = None
        set_session(user_id, session)
        await query.edit_message_text(
            "📦 ИНВЕНТАРИЗАЦИЯ\n\nВведи количество найденных посылок (цифрой):",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("« Назад в меню", callback_data="back_to_main_menu")
            ]])
        )
        return INVENTORY_INPUT

    if query.data == "inventory_back_notfound":
        session["not_found"] = None
        set_session(user_id, session)
        await query.edit_message_text(
            "Введи количество НЕ найденных посылок:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("« Назад", callback_data="inventory_back_found")
            ]])
        )
        return INVENTORY_INPUT


async def inventory_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)

    if update.callback_query:
        return await inventory_back(update, ctx)

    if not update.message.photo:
        await update.message.reply_text(
            "📸 Нужно отправить фото для инвентаризации!",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("« Назад", callback_data="inventory_back_notfound")
            ]])
        )
        return INVENTORY_PHOTO
    session["photo"] = update.message.photo[-1].file_id
    set_session(user_id, session)
    await update.message.reply_text("✅ Фото получено! Отправляю отчёт...")
    await send_inventory_report(update, ctx, user_id)
    return ConversationHandler.END


async def send_inventory_report(update, ctx, user_id):
    session = get_session(user_id)
    found = session.get("found", 0)
    not_found = session.get("not_found", 0)
    total = found + not_found

    text = (
        f"{'─' * 30}\n"
        f"📦 ИНВЕНТАРИЗАЦИЯ\n"
        f"{'─' * 30}\n"
        f"👤 {session['name']} (@{session['username']})\n"
        f"🏙 Город: {session['city']}\n"
        f"🕐 {session['started_at']}\n"
        f"📤 Отправлено: {now_str()}\n"
        f"{'─' * 30}\n"
        f"📦 Всего проверено: {total}\n"
        f"✅ Найдено: {found}\n"
        f"❌ Не найдено: {not_found}\n"
        f"{'─' * 30}\n"
        f"📸 Фото прилагается"
    )
    if not_found > 0:
        text += f"\n\n⚠️ Внимание: {not_found} посылок не найдено!"

    await ctx.bot.send_message(chat_id=REPORT_CHAT_ID, text=text)
    if session.get("photo"):
        await ctx.bot.send_photo(chat_id=REPORT_CHAT_ID, photo=session["photo"])

    await update.message.reply_text(
        f"✅ Отчёт инвентаризации отправлен!\n\n"
        f"📦 Найдено: {found} | ❌ Не найдено: {not_found}\n\n"
        "/start — вернуться в меню"
    )
    sessions.pop(user_id, None)


# ─── ЗАЩИТА: БЛОКИРОВКА НЕАВТОРИЗОВАННЫХ ──────────────────────────────────────
async def block_unauthorized(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_admin(user_id) or is_approved(user_id):
        return  # разрешаем
    await update.message.reply_text(
        "⛔ У вас нет доступа к этому боту.\n"
        "Напишите /start чтобы отправить заявку."
    )


# ─── ТЕСТ НАПОМИНАНИЯ (только для админа) ────────────────────────────────────
async def test_reminder(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text("🔔 Отправляю тестовое напоминание всем сотрудникам...")
    await send_inventory_reminder(ctx)
    await update.message.reply_text("✅ Готово! Все сотрудники получили напоминание.")


# ─── ОТМЕНА ───────────────────────────────────────────────────────────────────
async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sessions.pop(update.effective_user.id, None)
    await update.message.reply_text("Отменено. /start — вернуться в меню.")
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
# ☕ ПЕРЕРЫВ
# ══════════════════════════════════════════════════════════════════════════════

async def break_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Сотрудник нажал кнопку Перерыв"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if not is_approved(user_id):
        await query.message.reply_text("⛔ У вас нет доступа.")
        return MAIN_MENU

    # Если перерыв уже активен
    if user_id in active_breaks:
        started = active_breaks[user_id]["started_at"]
        diff = int((datetime.now(timezone.utc) - started).total_seconds() / 60)
        await query.answer(f"⏳ Перерыв уже идёт {diff} мин.", show_alert=True)
        return MAIN_MENU

    user_info = get_user(user_id)
    name = user_info.get("name", "Сотрудник")
    username = user_info.get("username", "")
    city = user_info.get("city", "—")
    started_at = datetime.now(timezone.utc)
    started_str = (started_at + __import__('datetime').timedelta(hours=3)).strftime("%H:%M")

    active_breaks[user_id] = {
        "started_at": started_at,
        "name": name,
        "username": username,
        "city": city,
        "started_str": started_str,
    }

    # Планируем напоминание через 15 минут
    job = ctx.job_queue.run_once(
        break_timeout,
        when=15 * 60,
        data={"user_id": user_id},
        name=f"break_{user_id}",
    )
    active_breaks[user_id]["job_name"] = f"break_{user_id}"

    # Сообщение сотруднику
    await query.edit_message_text(
        f"☕ Перерыв начат в {started_str}\n\n"
        "Максимальное время: 15 минут.\n"
        "Нажми кнопку когда вернёшься:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Перерыв завершён, я вернулся!", callback_data="break_end")]
        ])
    )

    # Уведомление в групповой чат
    uname_str = f"@{username}" if username else ""
    report_text = (
        f"{'─' * 30}\n"
        f"☕ ПЕРЕРЫВ НАЧАТ\n"
        f"{'─' * 30}\n"
        f"👤 {name} {uname_str}\n"
        f"🏙 Город: {city}\n"
        f"🕐 Время начала: {started_str}\n"
        f"⏳ Максимум до: {(started_at + __import__('datetime').timedelta(hours=3, minutes=15)).strftime('%H:%M')}\n"
        f"{'─' * 30}"
    )
    await ctx.bot.send_message(chat_id=REPORT_CHAT_ID, text=report_text)
    return MAIN_MENU


async def break_end(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Сотрудник завершил перерыв"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if user_id not in active_breaks:
        await query.edit_message_text("ℹ️ Активного перерыва не найдено.\n\n/start — в меню")
        return MAIN_MENU

    break_data = active_breaks.pop(user_id)
    started_at = break_data["started_at"]
    now_utc = datetime.now(timezone.utc)
    duration = int((now_utc - started_at).total_seconds() / 60)
    seconds_extra = int((now_utc - started_at).total_seconds() % 60)
    ended_str = (now_utc + __import__('datetime').timedelta(hours=3)).strftime("%H:%M")
    name = break_data["name"]
    username = break_data.get("username", "")
    city = break_data["city"]
    started_str = break_data["started_str"]

    # Отменяем таймер если ещё не сработал
    current_jobs = ctx.job_queue.get_jobs_by_name(f"break_{user_id}")
    for job in current_jobs:
        job.schedule_removal()

    overdue = duration > 15
    duration_str = f"{duration} мин {seconds_extra} сек"

    # Сообщение сотруднику
    if overdue:
        employee_msg = (
            f"⚠️ Перерыв завершён с превышением!\n\n"
            f"🕐 Начало: {started_str}\n"
            f"🕑 Конец: {ended_str}\n"
            f"⏱ Длительность: {duration_str}\n"
            f"❌ Превышение: {duration - 15} мин {seconds_extra} сек\n\n"
            f"/start — вернуться в меню"
        )
    else:
        employee_msg = (
            f"✅ Перерыв завершён!\n\n"
            f"🕐 Начало: {started_str}\n"
            f"🕑 Конец: {ended_str}\n"
            f"⏱ Длительность: {duration_str}\n\n"
            f"/start — вернуться в меню"
        )
    await query.edit_message_text(employee_msg)

    # Отчёт в групповой чат
    uname_str = f"@{username}" if username else ""
    if overdue:
        report_text = (
            f"{'━' * 30}\n"
            f"⚠️ ВНИМАНИЕ! ПЕРЕРЫВ ЗАВЕРШЁН С ПРЕВЫШЕНИЕМ!\n"
            f"{'━' * 30}\n"
            f"👤 {name} {uname_str}\n"
            f"🏙 Город: {city}\n"
            f"🕐 Начало: {started_str}\n"
            f"🕑 Конец: {ended_str}\n"
            f"⏱ Длительность: {duration_str}\n"
            f"❌ Превышение нормы: {duration - 15} мин {seconds_extra} сек\n"
            f"{'━' * 30}\n"
            f"⚠️ Сотрудник задержался на перерыве!"
        )
    else:
        report_text = (
            f"{'─' * 30}\n"
            f"☕ ПЕРЕРЫВ ЗАВЕРШЁН\n"
            f"{'─' * 30}\n"
            f"👤 {name} {uname_str}\n"
            f"🏙 Город: {city}\n"
            f"🕐 Начало: {started_str}\n"
            f"🕑 Конец: {ended_str}\n"
            f"⏱ Длительность: {duration_str} ✅\n"
            f"{'─' * 30}"
        )
    await ctx.bot.send_message(chat_id=REPORT_CHAT_ID, text=report_text)
    return MAIN_MENU


async def break_timeout(ctx: ContextTypes.DEFAULT_TYPE):
    """Срабатывает через 15 минут если перерыв не завершён"""
    user_id = ctx.job.data["user_id"]

    if user_id not in active_breaks:
        return  # уже завершён вручную

    break_data = active_breaks[user_id]
    name = break_data["name"]
    username = break_data.get("username", "")
    city = break_data["city"]
    started_str = break_data["started_str"]
    uname_str = f"@{username}" if username else ""

    # Уведомление сотруднику
    try:
        await ctx.bot.send_message(
            chat_id=user_id,
            text=(
                f"🚨 ПЕРЕРЫВ УЖЕ 15 МИНУТ!\n\n"
                f"Ты начал перерыв в {started_str}.\n"
                f"Уже прошло 15 минут — пора возвращаться!\n\n"
                f"Нажми кнопку когда вернёшься:"
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Я вернулся!", callback_data="break_end")]
            ])
        )
    except Exception as e:
        logger.warning(f"Не удалось уведомить сотрудника {user_id}: {e}")

    # Уведомление в групповой чат — с особым акцентом
    report_text = (
        f"{'━' * 30}\n"
        f"🚨 ПЕРЕРЫВ НЕ ЗАВЕРШЁН!\n"
        f"{'━' * 30}\n"
        f"👤 {name} {uname_str}\n"
        f"🏙 Город: {city}\n"
        f"🕐 Начало: {started_str}\n"
        f"⏱ Прошло: 15 минут\n"
        f"{'━' * 30}\n"
        f"Сотрудник не вернулся с перерыва!\n"
        f"Требуется внимание руководителя!\n"
        f"{'━' * 30}"
    )
    try:
        await ctx.bot.send_message(chat_id=REPORT_CHAT_ID, text=report_text)
    except Exception as e:
        logger.warning(f"Не удалось отправить уведомление в чат: {e}")


# ─── НАПОМИНАНИЕ ПО ПН И ЧТ РАЗ В ДВЕ НЕДЕЛИ В 12:00 МСК ───────────────────
async def send_inventory_reminder(ctx: ContextTypes.DEFAULT_TYPE):
    now_msk = datetime.now(timezone.utc).astimezone(
        timezone(datetime.now(timezone.utc).astimezone().utcoffset())
    )
    # Используем московское время через смещение UTC+3
    from datetime import timedelta
    now_msk = datetime.now(timezone.utc) + timedelta(hours=3)

    weekday = now_msk.weekday()   # 0=пн, 3=чт
    week_number = now_msk.isocalendar()[1]

    # Только понедельник (0) или четверг (3), и только чётные недели
    if weekday not in (0, 3):
        return
    if week_number % 2 != 0:
        return

    users = load_users()
    today = now_msk.strftime("%d.%m.%Y")
    day_name = "понедельник" if weekday == 0 else "четверг"
    count = 0

    for uid, u in users.items():
        if u.get("status") == "approved":
            try:
                await ctx.bot.send_message(
                    chat_id=int(uid),
                    text=f"⏰ *Напоминание об инвентаризации!*\n\n"
                         f"🏙 {u.get('city', '—')} | 📅 {today} ({day_name})\n\n"
                         f"Пора провести инвентаризацию посылок.\n"
                         f"Нажми /start и выбери 📦 Инвентаризация",
                    parse_mode="Markdown"
                )
                count += 1
            except Exception as e:
                logger.warning(f"Не удалось отправить напоминание {uid}: {e}")
    logger.info(f"Напоминания отправлены: {count} сотрудников")


# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Напоминание каждый день в 09:00 UTC (= 12:00 МСК)
    # Фильтрация по пн/чт и чётным неделям — внутри функции
    job_queue = app.job_queue
    job_queue.run_daily(
        send_inventory_reminder,
        time=dtime(9, 0, tzinfo=timezone.utc),
        days=(0, 1, 2, 3, 4, 5, 6),
    )

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(main_menu_callback, pattern="^menu_"),
        ],
        states={
            ENTERING_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_name),
            ],
            MAIN_MENU: [
                CallbackQueryHandler(main_menu_callback, pattern="^(menu_|back_to_main_menu)"),
                CallbackQueryHandler(break_start, pattern="^menu_break$"),
                CallbackQueryHandler(break_end, pattern="^break_end$"),
            ],
            DOING_OPEN: [
                CallbackQueryHandler(open_confirm, pattern="^open_confirm$"),
                CallbackQueryHandler(main_menu_callback, pattern="^back_to_main_menu$"),
            ],
            OPEN_PHOTO: [
                MessageHandler(filters.PHOTO, open_receive_photo),
                CallbackQueryHandler(open_receive_photo, pattern="^open_back$"),
            ],
            DOING_CLOSE: [
                CallbackQueryHandler(close_shipped, pattern="^(shipped_|close_back_to_menu)"),
            ],
            CLOSE_PHOTO_SHIPMENT: [
                MessageHandler(filters.PHOTO, close_photo_shipment),
                CallbackQueryHandler(close_photo_shipment, pattern="^close_back_to_shipped$"),
            ],
            CLOSE_PHOTO_RECEPTION: [
                MessageHandler(filters.PHOTO, close_photo_reception),
                CallbackQueryHandler(close_photo_reception, pattern="^close_back_to_shipment$"),
            ],
            CLOSE_PHOTO_PVZ: [
                MessageHandler(filters.PHOTO, close_photo_pvz),
                CallbackQueryHandler(close_photo_pvz, pattern="^close_back_to_reception$"),
            ],
            CLOSE_SUPPLIES: [
                CallbackQueryHandler(close_supplies, pattern="^(supplies_|close_back_to_pvz)"),
            ],
            CLOSE_SUPPLIES_COMMENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, close_supplies_comment),
                CallbackQueryHandler(close_supplies_comment, pattern="^supplies_back$"),
            ],
            DOING_CLEAN: [
                CallbackQueryHandler(clean_callback, pattern="^clean_"),
                CallbackQueryHandler(main_menu_callback, pattern="^back_to_main_menu$"),
            ],
            CLEAN_PHOTOS: [
                MessageHandler(filters.PHOTO, clean_receive_photo),
                CommandHandler("done_photo", clean_done_photo),
            ],
            INVENTORY_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, inventory_input),
                CallbackQueryHandler(inventory_back, pattern="^inventory_back_found$"),
                CallbackQueryHandler(main_menu_callback, pattern="^back_to_main_menu$"),
            ],
            INVENTORY_PHOTO: [
                MessageHandler(filters.PHOTO, inventory_photo),
                CallbackQueryHandler(inventory_photo, pattern="^inventory_back_notfound$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start),
        ],
        allow_reentry=True,
    )

    app.add_handler(conv)

    # Перерыв — глобальные хендлеры (работают вне состояний ConversationHandler)
    # Нужны чтобы кнопка "Я вернулся" из сообщения-таймера тоже работала
    app.add_handler(CallbackQueryHandler(break_start, pattern="^menu_break$"))
    app.add_handler(CallbackQueryHandler(break_end, pattern="^break_end$"))

    # Команды только для админа
    app.add_handler(CommandHandler("staff", staff_list))
    app.add_handler(CommandHandler("test_reminder", test_reminder))

    # Обработка кнопок одобрения/удаления сотрудников
    app.add_handler(CallbackQueryHandler(
        handle_admin_action,
        pattern="^(approve_|reject_|remove_)"
    ))

    logger.info("Бот запущен ✅")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
