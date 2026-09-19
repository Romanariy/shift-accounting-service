"""Private, bounded image storage. No user-controlled filesystem paths."""
import hashlib
import io
import warnings
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from PIL import Image, ImageOps, UnidentifiedImageError


def media_path(relative):
    root = Path(settings.OFFER_MEDIA_ROOT).resolve()
    path = (root / relative).resolve()
    if not relative or not path.is_relative_to(root) or path == root:
        raise ValidationError("Некорректный путь изображения.")
    return path


def store_image(content):
    if not content or len(content) > settings.OFFER_UPLOAD_BYTES:
        raise ValidationError("Изображение должно быть не больше 10 МБ.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if image.format not in ("JPEG", "PNG", "WEBP") or image.width * image.height > 20_000_000:
                    raise ValidationError("Нужен JPG, PNG или WebP размером до 20 мегапикселей.")
                image = ImageOps.exif_transpose(image).convert("RGB")
                if min(image.size) < 200:
                    raise ValidationError("Скриншот слишком маленький для распознавания.")
                clean = io.BytesIO()
                image.save(clean, format="PNG")
                data = clean.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValidationError("Не удалось прочитать изображение.")
    name = f"images/{uuid4().hex}.png"
    path = media_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
    return name, hashlib.sha256(data).hexdigest()


def remove_image(relative):
    if relative:
        media_path(relative).unlink(missing_ok=True)


def crop_header(relative, bottom):
    with Image.open(media_path(relative)) as image:
        if not 0 < bottom < image.height * .55:
            raise ValidationError("Заголовок с залами не найден. Повторите скриншот.")
        clean = io.BytesIO()
        image.crop((0, 0, image.width, int(bottom))).save(clean, format="PNG")
    return store_image(clean.getvalue())[0]
