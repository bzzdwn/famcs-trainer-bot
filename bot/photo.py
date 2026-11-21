"""Фото-ввод: выбор размера фото из Telegram, лимит, поля попытки для БД."""
OCR_DAILY_LIMIT = 30
TARGET_PIXELS = 1_000_000


def pick_photo_size(sizes: list):
    """Из PhotoSize Telegram (width, height): самый крупный ≤ TARGET_PIXELS, иначе самый мелкий."""
    fit = [s for s in sizes if s.width * s.height <= TARGET_PIXELS]
    if fit:
        return max(fit, key=lambda s: s.width * s.height)
    return min(sizes, key=lambda s: s.width * s.height)


def photo_attempt_fields(ocr_raw: str, final_text: str) -> dict:
    """input_method / ocr_raw / ocr_edited для attempts. Правка — если текст изменился не только пробелами."""
    squash = lambda t: "".join(t.split())
    return {"input_method": "photo", "ocr_raw": ocr_raw, "ocr_edited": squash(ocr_raw) != squash(final_text)}
