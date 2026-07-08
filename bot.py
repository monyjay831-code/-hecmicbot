import os
import logging
import tempfile
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from pydub import AudioSegment

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")

SUPPORTED_FORMATS = ["mp3", "wav", "ogg", "m4a", "flac"]


# ---------- Helpers ----------

def get_user_dir(user_id: int) -> Path:
    d = Path(tempfile.gettempdir()) / f"audioeditbot_{user_id}"
    d.mkdir(exist_ok=True)
    return d


def format_size(bytes_: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if bytes_ < 1024:
            return f"{bytes_:.1f}{unit}"
        bytes_ /= 1024
    return f"{bytes_:.1f}TB"


def main_menu_keyboard():
    buttons = [
        [InlineKeyboardButton("✂️ Trim", callback_data="menu_trim")],
        [InlineKeyboardButton("🔊 Volume", callback_data="menu_volume")],
        [InlineKeyboardButton("🔄 Convert format", callback_data="menu_convert")],
        [InlineKeyboardButton("📉 Compress", callback_data="menu_compress")],
        [InlineKeyboardButton("🔗 Merge with another file", callback_data="menu_merge")],
    ]
    return InlineKeyboardMarkup(buttons)


# ---------- Command handlers ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎧 *Audio Edit Bot*\n\n"
        "Send me an audio file, voice note, or music file and I'll show you "
        "editing options: trim, volume, format conversion, compression, and merging.\n\n"
        "Commands:\n"
        "/trim <start_sec> <end_sec> — trim last uploaded file\n"
        "/volume <factor> — e.g. /volume 1.5 for louder, 0.5 for quieter\n"
        "/convert <format> — mp3, wav, ogg, m4a, flac\n"
        "/compress — reduce file size (64kbps)\n"
        "/merge — merge the last two uploaded files\n"
        "/cancel — clear your uploaded files",
        parse_mode="Markdown",
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_dir = get_user_dir(update.effective_user.id)
    for f in user_dir.glob("*"):
        f.unlink()
    context.user_data.clear()
    await update.message.reply_text("🗑️ Cleared your uploaded files.")


# ---------- File upload handler ----------

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_dir = get_user_dir(user.id)

    file_obj = update.message.audio or update.message.voice or update.message.document
    if file_obj is None:
        return

    tg_file = await file_obj.get_file()

    history = context.user_data.get("history", [])
    ext = "ogg" if update.message.voice else (
        file_obj.file_name.split(".")[-1] if getattr(file_obj, "file_name", None) else "mp3"
    )
    dest = user_dir / f"upload_{len(history)}.{ext}"
    await tg_file.download_to_drive(custom_path=str(dest))

    history.append(str(dest))
    history = history[-2:]
    context.user_data["history"] = history
    context.user_data["current"] = str(dest)

    size = os.path.getsize(dest)
    await update.message.reply_text(
        f"✅ Received file ({format_size(size)}).\nWhat would you like to do?",
        reply_markup=main_menu_keyboard(),
    )


# ---------- Callback menu ----------

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data

    prompts = {
        "menu_trim": "Send: /trim <start_sec> <end_sec>\nExample: /trim 10 30",
        "menu_volume": "Send: /volume <factor>\nExample: /volume 1.5 (louder) or /volume 0.5 (quieter)",
        "menu_convert": "Send: /convert <format>\nSupported: mp3, wav, ogg, m4a, flac",
        "menu_compress": "Send /compress to reduce file size (64kbps mono).",
        "menu_merge": "Upload a second file, then send /merge to join them in upload order.",
    }
    await query.message.reply_text(prompts.get(action, "Choose an option from /start"))


# ---------- Editing commands ----------

async def trim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = context.user_data.get("current")
    if not current:
        await update.message.reply_text("Send me an audio file first.")
        return
    try:
        start_sec = float(context.args[0])
        end_sec = float(context.args[1])
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /trim <start_sec> <end_sec>")
        return

    audio = AudioSegment.from_file(current)
    trimmed = audio[start_sec * 1000: end_sec * 1000]
    out_path = Path(current).with_name("trimmed.mp3")
    trimmed.export(out_path, format="mp3")

    await update.message.reply_audio(audio=open(out_path, "rb"), caption="✂️ Trimmed!")


async def volume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = context.user_data.get("current")
    if not current:
        await update.message.reply_text("Send me an audio file first.")
        return
    try:
        factor = float(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /volume <factor>  e.g. /volume 1.5")
        return

    import math
    db_change = 20 * math.log10(factor) if factor > 0 else -60
    audio = AudioSegment.from_file(current)
    adjusted = audio.apply_gain(db_change)
    out_path = Path(current).with_name("volume.mp3")
    adjusted.export(out_path, format="mp3")

    await update.message.reply_audio(audio=open(out_path, "rb"), caption=f"🔊 Volume x{factor}")


async def convert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = context.user_data.get("current")
    if not current:
        await update.message.reply_text("Send me an audio file first.")
        return
    try:
        fmt = context.args[0].lower()
    except IndexError:
        await update.message.reply_text("Usage: /convert <format>  e.g. /convert wav")
        return
    if fmt not in SUPPORTED_FORMATS:
        await update.message.reply_text(f"Supported formats: {', '.join(SUPPORTED_FORMATS)}")
        return

    audio = AudioSegment.from_file(current)
    out_path = Path(current).with_name(f"converted.{fmt}")
    audio.export(out_path, format=fmt)

    await update.message.reply_document(document=open(out_path, "rb"), caption=f"🔄 Converted to {fmt}")


async def compress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = context.user_data.get("current")
    if not current:
        await update.message.reply_text("Send me an audio file first.")
        return

    audio = AudioSegment.from_file(current).set_channels(1)
    out_path = Path(current).with_name("compressed.mp3")
    audio.export(out_path, format="mp3", bitrate="64k")

    before = os.path.getsize(current)
    after = os.path.getsize(out_path)
    await update.message.reply_audio(
        audio=open(out_path, "rb"),
        caption=f"📉 Compressed: {format_size(before)} → {format_size(after)}",
    )


async def merge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history = context.user_data.get("history", [])
    if len(history) < 2:
        await update.message.reply_text("Upload two files first, then send /merge.")
        return

    a = AudioSegment.from_file(history[0])
    b = AudioSegment.from_file(history[1])
    merged = a + b
    out_path = Path(history[1]).with_name("merged.mp3")
    merged.export(out_path, format="mp3")

    await update.message.reply_audio(audio=open(out_path, "rb"), caption="🔗 Merged!")


# ---------- Main ----------

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is not set.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("trim", trim))
    app.add_handler(CommandHandler("volume", volume))
    app.add_handler(CommandHandler("convert", convert))
    app.add_handler(CommandHandler("compress", compress))
    app.add_handler(CommandHandler("merge", merge))

    app.add_handler(MessageHandler(
        filters.AUDIO | filters.VOICE | filters.Document.AUDIO, handle_audio
    ))
    app.add_handler(CallbackQueryHandler(menu_callback))

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
