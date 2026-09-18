"""
TeleSave-style bot — Instagram/TikTok/YouTube video yuklab beruvchi Telegram bot.

Talab qilinadigan kutubxonalar:
    pip install python-telegram-bot==21.* yt-dlp requests --break-system-packages

Shuningdek, tizimda ffmpeg o'rnatilgan bo'lishi kerak (katta videolarni
avtomatik siqish uchun):
    Ubuntu/Debian: sudo apt install ffmpeg
    macOS (brew):  brew install ffmpeg
    Windows:       ffmpeg.org saytidan yuklab, PATH'ga qo'shing

Ishga tushirish:
    1. @BotFather orqali bot yarating va tokenni oling.
    2. Pastdagi BOT_TOKEN o'zgaruvchisiga tokeningizni qo'ying.
    3. python telesave_bot.py

Eslatma: Instagram, TikTok va YouTube kontentini yuklab olish ularning
foydalanish shartlariga (Terms of Service) zid bo'lishi mumkin, va
mualliflik huquqi himoyalangan videolarni ruxsatsiz tarqatish
noqonuniy bo'lishi mumkin. Botni faqat o'zingizga tegishli kontent
yoki ochiq/ruxsat etilgan materiallar uchun ishlating.
"""

import logging
import os
import re
import subprocess
import tempfile
import uuid

import requests
import yt_dlp
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = "8883137791:AAHi29GDDzradtxbsjMghLolIGxatr69wH4"  # @BotFather dan olingan token

# audd.io saytida ro'yxatdan o'tib, bepul API tokenini oling:
# https://dashboard.audd.io/ — musiqani ovozidan aniqlash (Shazam-kabi) uchun kerak.
AUDD_API_TOKEN = "SIZNING_AUDD_TOKENINGIZ"

URL_PATTERN = re.compile(
    r"(https?://(?:www\.)?(?:instagram\.com|instagr\.am|tiktok\.com|vt\.tiktok\.com"
    r"|youtube\.com|youtu\.be|m\.youtube\.com)\S+)"
)

MAX_FILESIZE_MB = 50  # Telegram bot API orqali yuborish limiti
TARGET_FILESIZE_MB = 47  # Siqishda maqsad hajm (limitdan biroz kichik, xavfsizlik uchun)
MIN_VIDEO_BITRATE_KBPS = 200  # Video bitrate juda pasaymasligi uchun quyi chegara
AUDIO_BITRATE_KBPS = 96


def extract_audio(input_path: str, output_path: str) -> bool:
    """Videodan audio (MP3) ni ffmpeg yordamida ajratib oladi."""
    cmd = [
        "ffmpeg",
        "-y",
        "-i", input_path,
        "-vn",
        "-acodec", "libmp3lame",
        "-q:a", "2",
        output_path,
    ]

    try:
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
        )
    except FileNotFoundError:
        logger.error("ffmpeg topilmadi — tizimda o'rnatilganiga ishonch hosil qiling.")
        return False

    if result.returncode != 0:
        logger.error("ffmpeg audio xatoligi: %s", result.stderr.decode(errors="ignore"))
        return False

    return os.path.exists(output_path)


def compress_video(input_path: str, output_path: str, duration_sec: float) -> bool:
    """ffmpeg yordamida videoni TARGET_FILESIZE_MB hajmiga sig'diradi.

    Video va audio uchun umumiy bitrate hisoblanadi (davomiylikka qarab),
    so'ngra ffmpeg shu bitrate bilan qayta kodlaydi. Muvaffaqiyatli bo'lsa
    True, aks holda False qaytaradi.
    """
    if duration_sec <= 0:
        return False

    target_total_kbps = int((TARGET_FILESIZE_MB * 8 * 1024) / duration_sec)
    video_kbps = max(target_total_kbps - AUDIO_BITRATE_KBPS, MIN_VIDEO_BITRATE_KBPS)

    cmd = [
        "ffmpeg",
        "-y",
        "-i", input_path,
        "-c:v", "libx264",
        "-b:v", f"{video_kbps}k",
        "-maxrate", f"{video_kbps}k",
        "-bufsize", f"{video_kbps * 2}k",
        "-c:a", "aac",
        "-b:a", f"{AUDIO_BITRATE_KBPS}k",
        "-preset", "fast",
        output_path,
    ]

    try:
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
        )
    except FileNotFoundError:
        logger.error("ffmpeg topilmadi — tizimda o'rnatilganiga ishonch hosil qiling.")
        return False

    if result.returncode != 0:
        logger.error("ffmpeg xatoligi: %s", result.stderr.decode(errors="ignore"))
        return False

    return os.path.exists(output_path)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Salom! Menga quyidagilarni yuborishingiz mumkin:\n\n"
        "🔗 Instagram, TikTok yoki YouTube havolasi — videoni yuklab beraman\n"
        "🎵 Qo'shiq nomi (matn) — YouTube'dan qidirib, audio yuklab beraman\n"
        "🎬 Video/audio/ovozli xabar — undagi musiqani aniqlab, topib beraman"
    )


async def search_and_send_song(
    update: Update, context: ContextTypes.DEFAULT_TYPE, query: str
) -> None:
    if not query:
        await update.message.reply_text(
            "Iltimos, video havolasi yoki qo'shiq nomini yuboring."
        )
        return

    status_msg = await update.message.reply_text(f"🔎 \"{query}\" qidirilmoqda...")

    with tempfile.TemporaryDirectory() as tmp_dir:
        audio_opts = {
            "outtmpl": os.path.join(tmp_dir, "audio.%(ext)s"),
            "format": "bestaudio/best",
            "quiet": True,
            "noplaylist": True,
            "default_search": "ytsearch1",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }
        try:
            with yt_dlp.YoutubeDL(audio_opts) as ydl:
                info = ydl.extract_info(query, download=True)
                if "entries" in info:
                    info = info["entries"][0]
        except Exception:
            logger.exception("Qidirishda xatolik")
            await status_msg.edit_text("❌ Qo'shiq topilmadi.")
            return

        title = (info.get("title") or query)[:60]
        for fname in os.listdir(tmp_dir):
            if fname.endswith(".mp3"):
                await status_msg.edit_text("📤 Yuborilmoqda...")
                with open(os.path.join(tmp_dir, fname), "rb") as af:
                    await update.message.reply_audio(audio=af, title=title)
                await status_msg.delete()
                return

        await status_msg.edit_text("❌ Audio fayl topilmadi.")


async def recognize_music_from_media(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Foydalanuvchi yuborgan video/audio/ovozli xabardagi musiqani
    audd.io orqali aniqlaydi (Shazam kabi) va topilsa, uni yuklab beradi.
    """
    if AUDD_API_TOKEN == "SIZNING_AUDD_TOKENINGIZ":
        await update.message.reply_text(
            "⚠️ Musiqani aniqlash uchun AUDD_API_TOKEN sozlanmagan."
        )
        return

    media = (
        update.message.video
        or update.message.audio
        or update.message.voice
        or update.message.video_note
    )
    if not media:
        return

    status_msg = await update.message.reply_text("🎧 Musiqa aniqlanmoqda...")

    with tempfile.TemporaryDirectory() as tmp_dir:
        tg_file = await media.get_file()
        local_path = os.path.join(tmp_dir, "input_media")
        await tg_file.download_to_drive(local_path)

        # audd.io video fayllarni ham qabul qiladi, lekin ishonchli
        # bo'lishi uchun audio qismini ajratib yuboramiz.
        audio_path = os.path.join(tmp_dir, "sample.mp3")
        source_path = local_path
        if extract_audio(local_path, audio_path):
            source_path = audio_path

        try:
            with open(source_path, "rb") as f:
                response = requests.post(
                    "https://api.audd.io/",
                    data={"api_token": AUDD_API_TOKEN, "return": "apple_music,spotify"},
                    files={"file": f},
                    timeout=30,
                )
            result = response.json()
        except Exception:
            logger.exception("audd.io so'rovida xatolik")
            await status_msg.edit_text("❌ Aniqlashda xatolik yuz berdi.")
            return

        song = result.get("result")
        if result.get("status") != "success" or not song:
            await status_msg.edit_text("❌ Musiqa aniqlanmadi.")
            return

        artist = song.get("artist", "")
        title = song.get("title", "")
        full_title = f"{artist} - {title}".strip(" -")
        await status_msg.edit_text(f"🎵 Topildi: {full_title}\nYuklanmoqda...")

    await search_and_send_song(update, context, full_title)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    match = URL_PATTERN.search(text)

    if not match:
        # Havola emas — demak bu qo'shiq nomi, YouTube'dan qidirib topamiz.
        await search_and_send_song(update, context, text.strip())
        return

    url = match.group(1)
    status_msg = await update.message.reply_text("⏳ Havola tekshirilmoqda...")

    with tempfile.TemporaryDirectory() as tmp_dir:
        # 1-QADAM: faqat ma'lumot (metadata) olish — hali yuklab olmaymiz.
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "noplaylist": True}) as ydl:
                info = ydl.extract_info(url, download=False)
        except yt_dlp.utils.DownloadError as e:
            logger.error("Info error: %s", e)
            await status_msg.edit_text(
                "❌ Havolani ochib bo'lmadi. Video yopiq/xususiy yoki noto'g'ri "
                "havola bo'lishi mumkin."
            )
            return

        duration = info.get("duration") or 0
        title = (info.get("title") or "audio")[:60]

        # "Progressiv" format qidiramiz — video va audio bitta faylda,
        # shu sababli qayta kodlash/birlashtirishsiz to'g'ridan-to'g'ri
        # Telegram'ga havola sifatida berish mumkin (eng tezkor yo'l).
        direct_url = None
        for f in reversed(info.get("formats") or []):
            if (
                f.get("vcodec") not in (None, "none")
                and f.get("acodec") not in (None, "none")
                and f.get("ext") == "mp4"
                and (f.get("height") or 0) <= 720
                and f.get("url")
            ):
                direct_url = f["url"]
                break
        if not direct_url and info.get("url"):
            direct_url = info.get("url")

        # 2-QADAM: TEZKOR YO'L — havolani to'g'ridan-to'g'ri Telegram'ga beramiz,
        # Telegram serverlari o'zi yuklab oladi (bizning server orqali
        # yuklash + qayta yuborish bosqichlari kerak bo'lmaydi).
        if direct_url:
            try:
                await status_msg.edit_text("⚡ Video tezkor usulda yuborilmoqda...")

                # Havolani keyinroq (tugma bosilganda) audio ajratish uchun
                # xotirada saqlab qo'yamiz.
                song_id = uuid.uuid4().hex[:12]
                context.bot_data.setdefault("song_cache", {})[song_id] = {
                    "url": url,
                    "title": title,
                }
                keyboard = InlineKeyboardMarkup(
                    [[InlineKeyboardButton(
                        "📥 Qo'shiqni yuklab olish", callback_data=f"song:{song_id}"
                    )]]
                )

                await update.message.reply_video(
                    video=direct_url, caption=title, reply_markup=keyboard
                )
                await status_msg.delete()
                return
            except Exception as e:
                logger.warning("Tezkor yo'l ishlamadi, oddiy yuklashga o'tilmoqda: %s", e)

        # 3-QADAM: ZAXIRA YO'L — tezkor yo'l ishlamasa (havola bloklangan,
        # juda katta, yoki alohida video/audio oqimlari birlashtirilishi
        # kerak bo'lsa — masalan YouTube'da), to'liq yuklab, qayta yuboramiz.
        await status_msg.edit_text("⏳ Video yuklanmoqda...")
        outtmpl = os.path.join(tmp_dir, "%(id)s.%(ext)s")
        ydl_opts = {
            "outtmpl": outtmpl,
            "format": "bv*[height<=720][ext=mp4]+ba[ext=m4a]/best[height<=720][ext=mp4]/best",
            "quiet": True,
            "noplaylist": True,
            "max_filesize": MAX_FILESIZE_MB * 1024 * 1024 * 4,  # siqish uchun zaxira
            "concurrent_fragment_downloads": 8,
            "merge_output_format": "mp4",
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filepath = ydl.prepare_filename(info)
        except yt_dlp.utils.DownloadError as e:
            logger.error("Download error: %s", e)
            await status_msg.edit_text(
                "❌ Videoni yuklab bo'lmadi. Havola noto'g'ri, video "
                "yopiq/xususiy, yoki hajmi juda katta bo'lishi mumkin."
            )
            return
        except Exception as e:
            logger.exception("Unexpected error")
            await status_msg.edit_text("❌ Kutilmagan xatolik yuz berdi.")
            return

        if not os.path.exists(filepath):
            await status_msg.edit_text("❌ Fayl topilmadi.")
            return

        file_size_mb = os.path.getsize(filepath) / (1024 * 1024)

        if file_size_mb > MAX_FILESIZE_MB:
            duration = info.get("duration") or 0
            await status_msg.edit_text(
                f"⚙️ Video hajmi {file_size_mb:.1f} MB — Telegram limiti uchun "
                "siqilmoqda, biroz kuting..."
            )

            compressed_path = os.path.join(tmp_dir, "compressed.mp4")
            ok = compress_video(filepath, compressed_path, duration)

            if not ok:
                await status_msg.edit_text(
                    "❌ Video juda katta va siqib bo'lmadi. ffmpeg o'rnatilganiga "
                    "ishonch hosil qiling yoki qisqaroq video yuboring."
                )
                return

            compressed_size_mb = os.path.getsize(compressed_path) / (1024 * 1024)
            if compressed_size_mb > MAX_FILESIZE_MB:
                await status_msg.edit_text(
                    f"❌ Siqishdan keyin ham hajm {compressed_size_mb:.1f} MB, "
                    f"bu {MAX_FILESIZE_MB} MB limitidan katta. Video juda uzun."
                )
                return

            filepath = compressed_path

        song_id = uuid.uuid4().hex[:12]
        context.bot_data.setdefault("song_cache", {})[song_id] = {
            "url": url,
            "title": title,
        }
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(
                "📥 Qo'shiqni yuklab olish", callback_data=f"song:{song_id}"
            )]]
        )

        await status_msg.edit_text("📤 Yuborilmoqda...")
        with open(filepath, "rb") as video_file:
            await update.message.reply_video(video=video_file, reply_markup=keyboard)

        await status_msg.delete()


async def download_song_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("⏳ Qo'shiq tayyorlanmoqda...")

    song_id = query.data.split(":", 1)[1]
    song_cache = context.bot_data.get("song_cache", {})
    song = song_cache.get(song_id)

    if not song:
        await query.message.reply_text(
            "❌ Bu tugma muddati o'tgan. Iltimos, havolani qayta yuboring."
        )
        return

    with tempfile.TemporaryDirectory() as tmp_dir:
        audio_opts = {
            "outtmpl": os.path.join(tmp_dir, "audio.%(ext)s"),
            "format": "bestaudio/best",
            "quiet": True,
            "noplaylist": True,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }
        try:
            with yt_dlp.YoutubeDL(audio_opts) as ydl:
                ydl.download([song["url"]])
        except Exception:
            logger.exception("Qo'shiqni yuklab bo'lmadi")
            await query.message.reply_text("❌ Qo'shiqni yuklab bo'lmadi.")
            return

        for fname in os.listdir(tmp_dir):
            if fname.endswith(".mp3"):
                with open(os.path.join(tmp_dir, fname), "rb") as af:
                    await query.message.reply_audio(audio=af, title=song["title"])
                break
        else:
            await query.message.reply_text("❌ Audio fayl topilmadi.")

    # Bir martalik foydalanish — keshdan o'chiramiz.
    song_cache.pop(song_id, None)


def main() -> None:
    if BOT_TOKEN == "SIZNING_BOT_TOKENINGIZ":
        raise SystemExit(
            "Iltimos, BOT_TOKEN o'zgaruvchisiga @BotFather dan olingan "
            "haqiqiy tokeningizni kiriting."
        )

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(download_song_callback, pattern="^song:"))
    app.add_handler(
        MessageHandler(
            filters.VIDEO | filters.AUDIO | filters.VOICE | filters.VIDEO_NOTE,
            recognize_music_from_media,
        )
    )

    logger.info("Bot ishga tushdi...")
    app.run_polling()


if __name__ == "__main__":
    main()
      
