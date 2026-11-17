"""Admin-only image upload helper for Instagram media hosting.

Images are stored in the Railway S3-compatible bucket. The bot replies with a
time-limited presigned HTTPS URL that Instagram can fetch when creating media.
"""
import asyncio
import logging
import os
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import boto3
from botocore.config import Config

log = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 15 * 1024 * 1024
URL_TTL_SECONDS = 7 * 24 * 60 * 60
ALLOWED_MIME_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


def _s3_client():
    required = (
        "MEDIA_S3_ENDPOINT",
        "MEDIA_S3_BUCKET",
        "MEDIA_S3_REGION",
        "MEDIA_S3_ACCESS_KEY_ID",
        "MEDIA_S3_SECRET_ACCESS_KEY",
    )
    missing = [name for name in required if not os.getenv(name, "").strip()]
    if missing:
        raise RuntimeError("Не настроены параметры медиахранилища: " + ", ".join(missing))

    return boto3.client(
        "s3",
        endpoint_url=os.environ["MEDIA_S3_ENDPOINT"],
        region_name=os.environ["MEDIA_S3_REGION"],
        aws_access_key_id=os.environ["MEDIA_S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["MEDIA_S3_SECRET_ACCESS_KEY"],
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": os.getenv("MEDIA_S3_ADDRESSING_STYLE", "virtual")},
        ),
    )


async def admin_media_upload(update, context):
    """Upload a private-chat photo from the configured admin to object storage."""
    message = update.effective_message
    user = update.effective_user
    admin_chat_id = os.getenv("ADMIN_CHAT_ID", "").strip()

    if not user or not admin_chat_id or str(user.id) != admin_chat_id:
        return

    if not message or not message.photo:
        return

    photo = message.photo[-1]
    if photo.file_size and photo.file_size > MAX_IMAGE_BYTES:
        await message.reply_text("⚠️ Изображение больше 15 МБ. Отправьте файл меньшего размера.")
        return

    try:
        telegram_file = await context.bot.get_file(photo.file_id)
        image_bytes = bytes(await telegram_file.download_as_bytearray())
        if not image_bytes or len(image_bytes) > MAX_IMAGE_BYTES:
            await message.reply_text("⚠️ Не удалось принять изображение: допустимый размер — до 15 МБ.")
            return

        # Telegram photo uploads are JPEG; inspect the actual signature instead
        # of trusting the optional file metadata.
        if image_bytes.startswith(b"\xff\xd8\xff"):
            content_type, extension = "image/jpeg", "jpg"
        elif image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            content_type, extension = "image/png", "png"
        elif image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
            content_type, extension = "image/webp", "webp"
        else:
            await message.reply_text("⚠️ Формат изображения не распознан. Отправьте JPG, PNG или WebP.")
            return

        now = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh"))
        object_key = f"instagram/{now:%Y/%m/%d}/{uuid.uuid4().hex}.{extension}"
        client = _s3_client()
        await asyncio.to_thread(
            client.put_object,
            Bucket=os.environ["MEDIA_S3_BUCKET"],
            Key=object_key,
            Body=image_bytes,
            ContentType=content_type,
            CacheControl="public, max-age=604800",
        )
        url = await asyncio.to_thread(
            client.generate_presigned_url,
            "get_object",
            Params={"Bucket": os.environ["MEDIA_S3_BUCKET"], "Key": object_key},
            ExpiresIn=URL_TTL_SECONDS,
        )
        await message.reply_text(
            "✅ Изображение сохранено в медиахранилище BEAR MODE.\n\n"
            "Временная HTTPS-ссылка для загрузки в Instagram (действует 7 дней):\n"
            f"{url}\n\n"
            "Скопируйте ссылку и пришлите её сюда в ChatGPT для публикации."
        )
        log.info("Admin media uploaded to object storage: %s", object_key)
    except Exception:
        log.exception("Admin media upload failed")
        await message.reply_text(
            "❌ Не удалось сохранить изображение. Проверьте настройки медиахранилища в Railway."
        )
