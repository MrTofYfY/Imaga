import os
import asyncio
import logging
import aiosqlite
from datetime import datetime, timedelta
from dotenv import load_dotenv
from typing import Callable, Dict, Any, Awaitable

from aiogram import Bot, Dispatcher, Router, F, BaseMiddleware
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BotCommand
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMINS = [a.strip().lower() for a in os.getenv("ADMINS", "mellfreezy").split(",")]
SERVER_IP = os.getenv("SERVER_IP", "5.35.126.109:7486")
FORUM_URL = os.getenv("FORUM_URL", "https://gameforum.hgweb.ru")

DB_PATH = "dmarena.db"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

scheduler = AsyncIOScheduler()

_chat_id_cache = {}


# ======================== Middleware ========================

class CacheChatIdMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event,
        data: Dict[str, Any]
    ) -> Any:
        if isinstance(event, Message):
            if event.from_user and event.from_user.username:
                _chat_id_cache[event.from_user.username.lower()] = event.chat.id
        elif isinstance(event, CallbackQuery):
            if event.from_user and event.from_user.username and event.message:
                _chat_id_cache[event.from_user.username.lower()] = event.message.chat.id
        return await handler(event, data)


router.message.middleware(CacheChatIdMiddleware())
router.callback_query.middleware(CacheChatIdMiddleware())


# ======================== FSM States ========================

class ReportStates(StatesGroup):
    waiting_for_problem = State()


class ReplyStates(StatesGroup):
    waiting_for_reply = State()


class AddHelperStates(StatesGroup):
    waiting_for_username = State()


# ======================== Database ========================

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                message TEXT NOT NULL,
                status TEXT DEFAULT 'open',
                reply TEXT,
                replied_by TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                replied_at TIMESTAMP,
                notify_msg_ids TEXT DEFAULT ''
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS helpers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                added_by TEXT
            )
        """)
        for admin in ADMINS:
            try:
                await db.execute(
                    "INSERT OR IGNORE INTO helpers (username, added_by) VALUES (?, ?)",
                    (admin.lower(), "system")
                )
            except Exception:
                pass
        await db.commit()


async def is_staff(username: str) -> bool:
    if not username:
        return False
    uname = username.lower()
    if uname in ADMINS:
        return True
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id FROM helpers WHERE username = ?", (uname,)
        )
        row = await cursor.fetchone()
        return row is not None


async def is_admin(username: str) -> bool:
    if not username:
        return False
    return username.lower() in ADMINS


# ======================== Keyboards ========================

def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛡 Поддержка", callback_data="support")],
        [InlineKeyboardButton(text="🎮 Подключиться", callback_data="connect")],
        [InlineKeyboardButton(text="🌐 Форум", url=FORUM_URL)],
    ])


def support_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Создать обращение", callback_data="create_report")],
        [InlineKeyboardButton(text="📋 Мои обращения", callback_data="my_reports")],
        [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_to_menu")],
    ])


def staff_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📬 Открытые репорты", callback_data="staff_open_reports")],
        [InlineKeyboardButton(text="✅ Отвеченные репорты", callback_data="staff_answered_reports")],
        [InlineKeyboardButton(text="👥 Управление помощниками", callback_data="manage_helpers")],
        [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_to_menu")],
    ])


def report_action_keyboard(report_id: int, status: str) -> InlineKeyboardMarkup:
    buttons = []
    if status == "open":
        buttons.append([InlineKeyboardButton(
            text="💬 Ответить", callback_data=f"reply_report_{report_id}"
        )])
    elif status == "answered":
        buttons.append([InlineKeyboardButton(
            text="✏️ Изменить ответ", callback_data=f"reply_report_{report_id}"
        )])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="staff_open_reports")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ======================== Welcome ========================

WELCOME_TEXT = """
<b>🏆 Добро пожаловать в DMArena!</b>

━━━━━━━━━━━━━━━━━━━━━━

<b>⚡ Уникальный DM сервер SA:MP</b>

Мы создали сервер, который переосмысляет PvP в SA:MP.
Здесь каждый бой — это вызов, а каждая победа — заслуженна.

🎯 <b>Что тебя ждёт:</b>

   🔸 <b>Уникальный мод</b> — авторская система боёв,
        которую ты не найдёшь на других серверах

   ⚔️ <b>Дуэли</b> — вызывай любого игрока на
        честный поединок 1 на 1

   🏟 <b>Арена</b> — сражайся против всех и докажи,
        что ты лучший боец на сервере

   🎓 <b>Тренировочный мод</b> — прокачивай скилл,
        оттачивай прицел и стань мастером DM

━━━━━━━━━━━━━━━━━━━━━━

<b>🔥 Присоединяйся и покажи на что ты способен!</b>

<i>Выбери действие в меню ниже:</i>
"""


# ======================== Notify Staff ========================

async def notify_staff(report_id, user_id, username, first_name, problem_text):
    notify_text = (
        f"<b>📬 Новое обращение #{report_id}</b>\n\n"
        f"👤 <b>От:</b> {first_name} (@{username})\n"
        f"🆔 <b>User ID:</b> <code>{user_id}</code>\n\n"
        f"💬 <b>Сообщение:</b>\n<i>{problem_text}</i>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Ответить", callback_data=f"reply_report_{report_id}")],
    ])

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT username FROM helpers")
        helpers = await cursor.fetchall()

    all_staff = set(ADMINS)
    for h in helpers:
        all_staff.add(h[0].lower())

    sent_msg_ids = []

    for staff_uname in all_staff:
        chat_id = _chat_id_cache.get(staff_uname)
        if chat_id:
            try:
                msg = await bot.send_message(chat_id, notify_text, reply_markup=kb)
                sent_msg_ids.append(f"{chat_id}:{msg.message_id}")
            except Exception as e:
                logger.error(f"Failed to notify @{staff_uname}: {e}")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE reports SET notify_msg_ids = ? WHERE id = ?",
            (",".join(sent_msg_ids), report_id)
        )
        await db.commit()


# ======================== Handlers ========================

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(WELCOME_TEXT, reply_markup=main_menu_keyboard())


@router.message(Command("panel"))
async def cmd_panel(message: Message, state: FSMContext):
    if not await is_staff(message.from_user.username):
        await message.answer("❌ У вас нет доступа к панели.")
        return
    await state.clear()

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM reports WHERE status = 'open'")
        open_count = (await cursor.fetchone())[0]
        cursor = await db.execute("SELECT COUNT(*) FROM reports WHERE status = 'answered'")
        answered_count = (await cursor.fetchone())[0]

    await message.answer(
        f"<b>🔧 Панель поддержки DMArena</b>\n\n"
        f"📬 Открытых обращений: <b>{open_count}</b>\n"
        f"✅ Отвеченных: <b>{answered_count}</b>\n\n"
        f"<i>Выберите действие:</i>",
        reply_markup=staff_panel_keyboard()
    )


@router.callback_query(F.data == "back_to_menu")
async def cb_back_to_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(WELCOME_TEXT, reply_markup=main_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "connect")
async def cb_connect(callback: CallbackQuery):
    ip, port = SERVER_IP.split(":")
    connect_text = (
        f"<b>🎮 Подключение к серверу DMArena</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📡 <b>IP:</b> <code>{SERVER_IP}</code>\n\n"
        f"Нажмите кнопку ниже для автоматического\n"
        f"подключения через SA:MP клиент.\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )
    connect_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="▶️ Подключиться к серверу",
            url=f"https://server.sa-mp.com/{ip}:{port}"
        )],
        [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_to_menu")]
    ])
    await callback.message.edit_text(connect_text, reply_markup=connect_kb)
    await callback.answer()


@router.callback_query(F.data == "support")
async def cb_support(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "<b>🛡 Поддержка DMArena</b>\n\n"
        "Если у вас возникла проблема или вопрос,\n"
        "создайте обращение и мы ответим в ближайшее время.\n\n"
        "<i>Выберите действие:</i>",
        reply_markup=support_menu_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "create_report")
async def cb_create_report(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "<b>📝 Создание обращения</b>\n\n"
        "Опишите вашу проблему или вопрос в <b>одном сообщении</b>.\n"
        "Постарайтесь описать ситуацию максимально подробно.\n\n"
        "<i>Отправьте сообщение ниже ⬇️</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="support")]
        ])
    )
    await state.set_state(ReportStates.waiting_for_problem)
    await callback.answer()


@router.message(ReportStates.waiting_for_problem)
async def process_report(message: Message, state: FSMContext):
    problem_text = message.text
    if not problem_text:
        await message.answer("❌ Отправьте текстовое сообщение.")
        return

    user_id = message.from_user.id
    username = message.from_user.username or "нет_юзернейма"
    first_name = message.from_user.first_name or "Аноним"

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO reports (user_id, username, first_name, message) VALUES (?, ?, ?, ?)",
            (user_id, username, first_name, problem_text)
        )
        report_id = cursor.lastrowid
        await db.commit()

    await state.clear()

    await message.answer(
        f"<b>✅ Обращение #{report_id} создано!</b>\n\n"
        f"📝 <b>Ваш вопрос:</b>\n<i>{problem_text}</i>\n\n"
        "⏳ Ожидайте ответа от администрации.\n"
        "Ответ придёт вам в личные сообщения.",
        reply_markup=main_menu_keyboard()
    )

    await notify_staff(report_id, user_id, username, first_name, problem_text)


@router.callback_query(F.data == "my_reports")
async def cb_my_reports(callback: CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, message, status, reply FROM reports WHERE user_id = ? ORDER BY id DESC LIMIT 10",
            (user_id,)
        )
        reports = await cursor.fetchall()

    if not reports:
        await callback.message.edit_text(
            "<b>📋 Мои обращения</b>\n\nУ вас пока нет обращений.",
            reply_markup=support_menu_keyboard()
        )
        await callback.answer()
        return

    text = "<b>📋 Мои обращения</b>\n\n"
    for r in reports:
        rid, msg, status, reply = r
        status_icon = "🟡" if status == "open" else "✅"
        text += f"{status_icon} <b>#{rid}</b> — {msg[:50]}{'...' if len(msg) > 50 else ''}\n"
        if reply:
            text += f"   ↳ <i>Ответ: {reply[:60]}{'...' if len(reply) > 60 else ''}</i>\n"
        text += "\n"

    await callback.message.edit_text(text, reply_markup=support_menu_keyboard())
    await callback.answer()
  # ======================== Staff Handlers ========================

@router.callback_query(F.data == "staff_open_reports")
async def cb_staff_open_reports(callback: CallbackQuery):
    if not await is_staff(callback.from_user.username):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, user_id, username, first_name, message "
            "FROM reports WHERE status = 'open' ORDER BY id DESC"
        )
        reports = await cursor.fetchall()

    if not reports:
        await callback.message.edit_text(
            "<b>📬 Открытые обращения</b>\n\nНет открытых обращений! 🎉",
            reply_markup=staff_panel_keyboard()
        )
        await callback.answer()
        return

    buttons = []
    for r in reports:
        rid, uid, uname, fname, msg = r
        preview = msg[:40] + "..." if len(msg) > 40 else msg
        buttons.append([InlineKeyboardButton(
            text=f"🟡 #{rid} | {fname} — {preview}",
            callback_data=f"view_report_{rid}"
        )])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_panel")])

    await callback.message.edit_text(
        "<b>📬 Открытые обращения</b>\n\nНажмите на обращение для просмотра:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()


@router.callback_query(F.data == "staff_answered_reports")
async def cb_staff_answered(callback: CallbackQuery):
    if not await is_staff(callback.from_user.username):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, username, first_name, message, reply, replied_by "
            "FROM reports WHERE status = 'answered' ORDER BY replied_at DESC"
        )
        reports = await cursor.fetchall()

    if not reports:
        await callback.message.edit_text(
            "<b>✅ Отвеченные обращения</b>\n\nНет отвеченных обращений.",
            reply_markup=staff_panel_keyboard()
        )
        await callback.answer()
        return

    buttons = []
    for r in reports:
        rid, uname, fname, msg, reply, replied_by = r
        preview = msg[:30] + "..." if len(msg) > 30 else msg
        buttons.append([InlineKeyboardButton(
            text=f"✅ #{rid} | {fname} — {preview}",
            callback_data=f"view_report_{rid}"
        )])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_panel")])

    await callback.message.edit_text(
        "<b>✅ Отвеченные обращения</b>\n\nНажмите для просмотра (можно изменить ответ):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()


@router.callback_query(F.data == "back_to_panel")
async def cb_back_to_panel(callback: CallbackQuery, state: FSMContext):
    if not await is_staff(callback.from_user.username):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    await state.clear()

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM reports WHERE status = 'open'")
        open_count = (await cursor.fetchone())[0]
        cursor = await db.execute("SELECT COUNT(*) FROM reports WHERE status = 'answered'")
        answered_count = (await cursor.fetchone())[0]

    await callback.message.edit_text(
        f"<b>🔧 Панель поддержки DMArena</b>\n\n"
        f"📬 Открытых: <b>{open_count}</b>\n"
        f"✅ Отвеченных: <b>{answered_count}</b>",
        reply_markup=staff_panel_keyboard()
    )
    await callback.answer()


# ======================== View Report ========================

@router.callback_query(F.data.startswith("view_report_"))
async def cb_view_report(callback: CallbackQuery):
    if not await is_staff(callback.from_user.username):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    report_id = int(callback.data.split("_")[2])

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, user_id, username, first_name, message, status, reply, "
            "replied_by, created_at, replied_at FROM reports WHERE id = ?",
            (report_id,)
        )
        report = await cursor.fetchone()

    if not report:
        await callback.answer("Обращение не найдено", show_alert=True)
        return

    rid, uid, uname, fname, msg, status, reply, replied_by, created, replied_at = report
    status_text = "🟡 Открыт" if status == "open" else "✅ Отвечен"

    text = (
        f"<b>📄 Обращение #{rid}</b>\n\n"
        f"📊 <b>Статус:</b> {status_text}\n"
        f"👤 <b>От:</b> {fname} (@{uname})\n"
        f"🆔 <b>User ID:</b> <code>{uid}</code>\n"
        f"📅 <b>Создано:</b> {created}\n\n"
        f"💬 <b>Сообщение:</b>\n<i>{msg}</i>\n"
    )

    if reply:
        text += (
            f"\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"✅ <b>Ответ от</b> @{replied_by}:\n"
            f"<i>{reply}</i>\n"
            f"📅 <b>Отвечено:</b> {replied_at}"
        )

    await callback.message.edit_text(text, reply_markup=report_action_keyboard(rid, status))
    await callback.answer()


# ======================== Reply to Report ========================

@router.callback_query(F.data.startswith("reply_report_"))
async def cb_reply_report(callback: CallbackQuery, state: FSMContext):
    if not await is_staff(callback.from_user.username):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    report_id = int(callback.data.split("_")[2])
    await state.set_state(ReplyStates.waiting_for_reply)
    await state.update_data(report_id=report_id)

    await callback.message.edit_text(
        f"<b>💬 Ответ на обращение #{report_id}</b>\n\n"
        "Напишите ваш ответ пользователю в <b>одном сообщении</b>.\n\n"
        "<i>Отправьте ответ ниже ⬇️</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="back_to_panel")]
        ])
    )
    await callback.answer()


@router.message(ReplyStates.waiting_for_reply)
async def process_reply(message: Message, state: FSMContext):
    if not await is_staff(message.from_user.username):
        return

    reply_text = message.text
    if not reply_text:
        await message.answer("❌ Отправьте текстовое сообщение.")
        return

    data = await state.get_data()
    report_id = data.get("report_id")
    replied_by = message.from_user.username or "unknown"

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT user_id, username, first_name, message, notify_msg_ids "
            "FROM reports WHERE id = ?",
            (report_id,)
        )
        report = await cursor.fetchone()

        if not report:
            await message.answer("❌ Обращение не найдено.")
            await state.clear()
            return

        user_id, uname, fname, original_msg, notify_msg_ids = report

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await db.execute(
            "UPDATE reports SET status = 'answered', reply = ?, "
            "replied_by = ?, replied_at = ? WHERE id = ?",
            (reply_text, replied_by, now, report_id)
        )
        await db.commit()

    await state.clear()

    # Уведомляем пользователя
    try:
        user_notify_text = (
            f"<b>✅ Ответ на ваше обращение #{report_id}</b>\n\n"
            f"📝 <b>Ваш вопрос:</b>\n<i>{original_msg}</i>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💬 <b>Ответ от поддержки:</b>\n<i>{reply_text}</i>\n\n"
            f"<i>Спасибо за обращение! Если проблема не решена,\n"
            f"создайте новое обращение.</i>"
        )
        await bot.send_message(
            user_id, user_notify_text, reply_markup=main_menu_keyboard()
        )
    except Exception as e:
        logger.error(f"Не удалось уведомить пользователя {user_id}: {e}")

    # Обновляем уведомления у персонала
    if notify_msg_ids:
        for item in notify_msg_ids.split(","):
            if ":" in item:
                try:
                    chat_id, msg_id = item.split(":")
                    updated_text = (
                        f"<b>✅ Обращение #{report_id} — ОТВЕЧЕНО</b>\n\n"
                        f"👤 <b>От:</b> {fname} (@{uname})\n\n"
                        f"💬 <b>Вопрос:</b>\n<i>{original_msg}</i>\n\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"✅ <b>Ответ от</b> @{replied_by}:\n<i>{reply_text}</i>"
                    )
                    await bot.edit_message_text(
                        updated_text,
                        chat_id=int(chat_id),
                        message_id=int(msg_id),
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(
                                text="✏️ Изменить ответ",
                                callback_data=f"reply_report_{report_id}"
                            )]
                        ])
                    )
                except Exception as e:
                    logger.error(f"Failed to update notification: {e}")

    await message.answer(
        f"<b>✅ Ответ на обращение #{report_id} отправлен!</b>\n\n"
        f"Пользователь {fname} (@{uname}) уведомлён.",
        reply_markup=staff_panel_keyboard()
    )


# ======================== Manage Helpers ========================

@router.callback_query(F.data == "manage_helpers")
async def cb_manage_helpers(callback: CallbackQuery):
    if not await is_admin(callback.from_user.username):
        await callback.answer(
            "❌ Только администраторы могут управлять помощниками",
            show_alert=True
        )
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT username, added_by FROM helpers")
        helpers = await cursor.fetchall()

    text = "<b>👥 Управление помощниками</b>\n\n"

    if helpers:
        for h in helpers:
            uname, added_by = h
            is_adm = "👑" if uname in ADMINS else "🛡"
            text += f"{is_adm} @{uname}"
            if uname not in ADMINS:
                text += f" (добавил: @{added_by})"
            else:
                text += " (Администратор)"
            text += "\n"
    else:
        text += "Нет помощников.\n"

    buttons = [
        [InlineKeyboardButton(text="➕ Добавить помощника", callback_data="add_helper")],
    ]
    for h in helpers:
        if h[0] not in ADMINS:
            buttons.append([InlineKeyboardButton(
                text=f"❌ Удалить @{h[0]}",
                callback_data=f"remove_helper_{h[0]}"
            )])
    buttons.append([
        InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_panel")
    ])

    await callback.message.edit_text(
        text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()


@router.callback_query(F.data == "add_helper")
async def cb_add_helper(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.username):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    await state.set_state(AddHelperStates.waiting_for_username)
    await callback.message.edit_text(
        "<b>➕ Добавление помощника</b>\n\n"
        "Введите <b>username</b> нового помощника (без @).\n\n"
        "<i>Отправьте username ниже ⬇️</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="manage_helpers")]
        ])
    )
    await callback.answer()


@router.message(AddHelperStates.waiting_for_username)
async def process_add_helper(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.username):
        return

    username = message.text.strip().replace("@", "").lower()
    if not username:
        await message.answer("❌ Введите корректный username.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO helpers (username, added_by) VALUES (?, ?)",
                (username, message.from_user.username)
            )
            await db.commit()
            await state.clear()
            await message.answer(
                f"<b>✅ Помощник @{username} добавлен!</b>",
                reply_markup=staff_panel_keyboard()
            )
        except aiosqlite.IntegrityError:
            await message.answer(
                f"❌ @{username} уже является помощником.",
                reply_markup=staff_panel_keyboard()
            )
            await state.clear()


@router.callback_query(F.data.startswith("remove_helper_"))
async def cb_remove_helper(callback: CallbackQuery):
    if not await is_admin(callback.from_user.username):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    username = callback.data.replace("remove_helper_", "")

    if username in ADMINS:
        await callback.answer("❌ Нельзя удалить администратора", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM helpers WHERE username = ?", (username,))
        await db.commit()

    await callback.answer(f"✅ @{username} удалён из помощников", show_alert=True)
    await cb_manage_helpers(callback)


# ======================== Cleanup ========================

async def cleanup_old_reports():
    """Удаляет отвеченные репорты старше 1 дня"""
    threshold = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, notify_msg_ids FROM reports "
            "WHERE status = 'answered' AND replied_at <= ?",
            (threshold,)
        )
        old_reports = await cursor.fetchall()

        for report in old_reports:
            rid, notify_msg_ids = report
            if notify_msg_ids:
                for item in notify_msg_ids.split(","):
                    if ":" in item:
                        try:
                            chat_id, msg_id = item.split(":")
                            await bot.delete_message(int(chat_id), int(msg_id))
                        except Exception:
                            pass
            logger.info(f"Cleanup: removing answered report #{rid}")

        await db.execute(
            "DELETE FROM reports WHERE status = 'answered' AND replied_at <= ?",
            (threshold,)
        )
        await db.commit()


# ======================== Main ========================

async def on_startup():
    await init_db()
    await bot.set_my_commands([
        BotCommand(command="start", description="🏠 Главное меню"),
        BotCommand(command="panel", description="🔧 Панель поддержки (для персонала)"),
    ])
    scheduler.add_job(cleanup_old_reports, "interval", hours=1)
    scheduler.start()
    logger.info("Bot started!")
    logger.info(f"Admins: {ADMINS}")
    logger.info(f"Server: {SERVER_IP}")


async def main():
    await on_startup()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
