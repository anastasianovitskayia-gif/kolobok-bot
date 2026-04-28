import asyncio
import logging
import os
import shutil
import uuid
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    FSInputFile,
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery
)
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
TEMP_DIR = BASE_DIR / "temp"
TEMP_DIR.mkdir(exist_ok=True)
DB_PATH = BASE_DIR / "bot_stats.db"

MAX_VIDEO_NOTE_SIZE_MB = 20
MAX_VIDEO_NOTE_SIZE_BYTES = MAX_VIDEO_NOTE_SIZE_MB * 1024 * 1024

router = Router()

def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                first_seen TEXT,
                last_seen TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                event_type TEXT,
                created_at TEXT
            )
        """)


def track_user(message: Message) -> None:
    user = message.from_user
    if not user:
        return

    now = utc_now_for_mp4()

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO users (user_id, username, first_name, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_seen = excluded.last_seen
        """, (
            user.id,
            user.username,
            user.first_name,
            now,
            now,
        ))


def track_event(user_id: int, event_type: str) -> None:
    now = utc_now_for_mp4()

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO events (user_id, event_type, created_at) VALUES (?, ?, ?)",
            (user_id, event_type, now)
        )


def get_stats_text() -> str:
    with sqlite3.connect(DB_PATH) as conn:
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        total_events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

        videos = conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'video_to_circle'"
        ).fetchone()[0]

        refreshed = conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'refresh_circle'"
        ).fetchone()[0]

        errors = conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'error'"
        ).fetchone()[0]

    return (
        "📊 Статистика бота\n\n"
        f"👤 Пользователей: {total_users}\n"
        f"🎥 Видео → кружок: {videos}\n"
        f"🧼 Обновлено кружков: {refreshed}\n"
        f"⚠️ Ошибок: {errors}\n"
        f"📈 Всего действий: {total_events}"
    )

def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎥 Сделать кружок из видео",
                    callback_data="mode_video"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🧼 Обновить кружок",
                    callback_data="mode_refresh"
                )
            ],
            [
                InlineKeyboardButton(
                    text="ℹ️ Как это работает",
                    callback_data="mode_help"
                )
            ],
        ]
    )

def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Назад в меню",
                    callback_data="back_to_menu"
                )
            ]
        ]
    )

def utc_now_for_mp4() -> str:
    """MP4/QuickTime-friendly UTC timestamp."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_ffmpeg_installed() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("FFmpeg не найден. Установи FFmpeg и добавь его в PATH.")
    if shutil.which("ffprobe") is None:
        raise RuntimeError("FFprobe не найден. Обычно он ставится вместе с FFmpeg.")


async def run_process(command: list[str]) -> tuple[str, str]:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await process.communicate()

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")

    if process.returncode != 0:
        raise RuntimeError(stderr.strip() or "Команда завершилась с ошибкой.")

    return stdout, stderr


async def get_creation_time(path: Path) -> str:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format_tags=creation_time",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]

    try:
        stdout, _ = await run_process(command)
        return stdout.strip() or "не найдено"
    except Exception:
        return "не найдено"


async def refresh_video_note(input_path: Path, output_path: Path, creation_time: str) -> None:
    """
    Rebuilds Telegram video note as a new MP4 file:
    - removes old metadata
    - writes a fresh creation_time
    - keeps video_note-compatible square 512x512 H.264 MP4
    """
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-t",
        "59",
        "-vf",
        "scale=512:512,setsar=1,format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-movflags",
        "+faststart",
        "-map_metadata",
        "-1",
        "-metadata",
        f"creation_time={creation_time}",
        str(output_path),
    ]

    await run_process(command)

    # Also set the local file modification time to now before uploading.
    now_timestamp = datetime.now().timestamp()
    os.utime(output_path, (now_timestamp, now_timestamp))

@router.message(F.video)
async def video_handler(message: Message, bot: Bot) -> None:
    ensure_ffmpeg_installed()
    track_user(message)

    video = message.video

    if video.file_size and video.file_size > 50 * 1024 * 1024:
        await message.answer("Видео слишком большое. Пока лимит ~50MB.")
        return

    task_id = uuid.uuid4().hex
    input_path = TEMP_DIR / f"{task_id}_input.mp4"
    output_path = TEMP_DIR / f"{task_id}_output.mp4"

    status_message = await message.answer("🎥 Делаю кружок из видео...")

    try:
        await bot.download(file=video.file_id, destination=input_path)

        creation_time = utc_now_for_mp4()

        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-t",
            "59",
            "-vf",
            "crop=min(iw\\,ih):min(iw\\,ih),scale=512:512,setsar=1,format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-movflags",
            "+faststart",
            "-map_metadata",
            "-1",
            "-metadata",
            f"creation_time={creation_time}",
            str(output_path),
        ]

        await run_process(command)

        await bot.send_video_note(
            chat_id=message.chat.id,
            video_note=FSInputFile(output_path),
            duration=59,
            length=512,
        )

        track_event(message.from_user.id, "video_to_circle")

        await status_message.edit_text("✅ Готово! Отправил кружок")

    except Exception as error:
        logging.exception("Ошибка обработки видео")

        if message.from_user:
            track_event(message.from_user.id, "error")

        await status_message.edit_text(
            "Не получилось сделать кружок.\n\n"
            f"Ошибка: {str(error)[:1000]}"
        )

    finally:
        await safe_delete(input_path, output_path)
        
async def safe_delete(*paths: Path) -> None:
    for path in paths:
        try:
            if path.exists():
                path.unlink()
        except Exception:
            logging.exception("Не удалось удалить временный файл: %s", path)


@router.message(CommandStart())
async def start_handler(message: Message) -> None:
    await message.answer(
        "👋 Привет!\n\n"
        "Я работаю с Telegram-кружками 👇\n\n"
        "🎥 Делаю кружок из видео\n"
        "🧼 Обновляю готовые кружки\n\n"
        "Выбери действие:",
        reply_markup=main_keyboard()
    )

@router.callback_query(F.data == "mode_video")
async def mode_video_handler(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "🎥 Сделать кружок из видео\n\n"
        "Отправь мне обычное видео, и я превращу его в круглый Telegram-кружок.\n\n"
        "📌 Видео будет:\n"
        "• обрезано до квадрата\n"
        "• уменьшено до 512x512\n"
        "• ограничено до 59 секунд",
        reply_markup=back_keyboard()
    )


@router.callback_query(F.data == "mode_refresh")
async def mode_refresh_handler(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "🧼 Обновить готовый кружок\n\n"
        "Отправь мне Telegram-кружок, и я пересоберу его как новый файл.\n\n"
        "📌 Я обновлю метаданные и отправлю новый кружок обратно.",
        reply_markup=back_keyboard()
    )


@router.callback_query(F.data == "mode_help")
async def mode_help_handler(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "ℹ️ Как это работает\n\n"
        "Ты можешь отправить:\n\n"
        "🎥 обычное видео — я сделаю из него кружок\n"
        "🧼 готовый кружок — я обновлю его как новый\n\n"
        "Просто выбери действие и отправь файл.",
        reply_markup=back_keyboard()
    )

@router.callback_query(F.data == "back_to_menu")
async def back_to_menu_handler(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "👋 Привет!\n\n"
        "Я работаю с Telegram-кружками 👇\n\n"
        "🎥 Делаю кружок из видео\n"
        "🧼 Обновляю готовые кружки\n\n"
        "Выбери действие:",
        reply_markup=main_keyboard()
    )

@router.message(Command("help"))
async def help_handler(message: Message) -> None:
    await message.answer(
        "ℹ️ Как работает бот:\n\n"
        "Можно отправить:\n"
        "• Telegram-кружок — я пересоберу его как новый\n"
        "• обычное видео — я превращу его в кружок\n\n"
        "Видео обрезается до квадрата, уменьшается до 512x512 и ограничивается 59 секундами."
    )

@router.message(Command("privacy"))
async def privacy_handler(message: Message) -> None:
    await message.answer(
        "🔒 Приватность\n\n"
        "Бот не хранит ваши видео и кружки.\n\n"
        "Файлы используются только для обработки и удаляются сразу после отправки результата.\n\n"
        "В статистике сохраняются только технические данные: Telegram ID пользователя, username, имя и количество действий."
    )
    
@router.message(Command("stats"))
async def stats_handler(message: Message) -> None:
    admin_id = os.getenv("ADMIN_ID")

    if not admin_id or message.from_user.id != int(admin_id):
        await message.answer("⛔ Эта команда доступна только администратору.")
        return

    await message.answer(get_stats_text())

@router.message(F.video_note)
async def video_note_handler(message: Message, bot: Bot) -> None:
    ensure_ffmpeg_installed()
    track_user(message)

    video_note = message.video_note

    if video_note.file_size and video_note.file_size > MAX_VIDEO_NOTE_SIZE_BYTES:
        await message.answer(
            f"Файл слишком большой для MVP. Сейчас лимит: {MAX_VIDEO_NOTE_SIZE_MB} MB."
        )
        return

    task_id = uuid.uuid4().hex
    input_path = TEMP_DIR / f"{task_id}_input.mp4"
    output_path = TEMP_DIR / f"{task_id}_output.mp4"

    status_message = await message.answer("Принял кружок. Пересобираю файл...")

    try:
        await bot.download(file=video_note.file_id, destination=input_path)

        old_creation_time = await get_creation_time(input_path)
        expected_new_creation_time = utc_now_for_mp4()

        await refresh_video_note(
            input_path=input_path,
            output_path=output_path,
            creation_time=expected_new_creation_time,
        )

        actual_new_creation_time = await get_creation_time(output_path)

        await bot.send_video_note(
            chat_id=message.chat.id,
            video_note=FSInputFile(output_path),
            duration=min(video_note.duration or 59, 59),
            length=512,
        )

        track_event(message.from_user.id, "refresh_circle")

        await status_message.edit_text(
            "✅ Готово! Отправил обновлённый кружок."
        )

    except Exception as error:
        logging.exception("Ошибка обработки видео")

        if message.from_user:
            track_event(message.from_user.id, "error")

        await status_message.edit_text(
            "Не получилось сделать кружок.\n\n"
            f"Ошибка: {str(error)[:1000]}"
        )

    finally:
        await safe_delete(input_path, output_path)


@router.message()
async def fallback_handler(message: Message) -> None:
    await message.answer(
        "Отправь мне Telegram-кружок или обычное видео, и я обработаю его."
    )

async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    load_dotenv()
    init_db()

    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN не найден. Создай .env файл и добавь туда токен бота.")

    ensure_ffmpeg_installed()

    bot = Bot(token=token)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
