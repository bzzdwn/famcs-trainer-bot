"""Распознавание фото итоговой формулы: Yandex Vision OCR, модель math-markdown.

Модели передаём только картинку — ни условия, ни эталона, чтобы она не «чинила» ошибки студента.
"""
import asyncio
import base64
import math
from io import BytesIO
from typing import Awaitable, Callable

from PIL import Image, UnidentifiedImageError

from core.latex_input import Unreadable, latex_to_input

URL = "https://ocr.api.cloud.yandex.net/ocr/v1/recognizeText"   # ai.api.cloud.yandex.net из РБ не отвечает
MODEL = "math-markdown"
TIMEOUT = 15.0
MAX_PIXELS = 1_000_000
LANGUAGES = ["ru", "en"]   # "*" (автоопределение) math-markdown отвергает с HTTP 400
MIN_INTERVAL = 1.05        # квота каталога — 1 запрос в секунду

# post(url, headers, json_body, timeout) -> (http_status, json_response); в тестах подменяется
PostFn = Callable[[str, dict, dict, float], Awaitable[tuple[int, dict]]]


class OcrError(RuntimeError):
    """Сеть, таймаут или ошибка API."""


def prepare_image(data: bytes, max_pixels: int = MAX_PIXELS) -> bytes:
    """Любая картинка → JPEG не больше max_pixels с сохранением пропорций."""
    try:
        img = Image.open(BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError):
        raise Unreadable("не картинка") from None
    img = img.convert("RGB")
    if img.width * img.height > max_pixels:
        k = math.sqrt(max_pixels / (img.width * img.height))
        img = img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))), Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


async def _aiohttp_post(url: str, headers: dict, body: dict, timeout: float) -> tuple[int, dict]:
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=body,
                                timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            try:
                data = await r.json(content_type=None)
            except Exception:
                data = {}
            return r.status, data if isinstance(data, dict) else {}


class YandexOcr:
    def __init__(self, api_key: str, folder_id: str, post: PostFn | None = None, timeout: float = TIMEOUT,
                 min_interval: float = MIN_INTERVAL):
        self._headers = {
            "Content-Type": "application/json",
            "Authorization": f"Api-Key {api_key}",
            "x-folder-id": folder_id,
            "x-data-logging-enabled": "false",       # не разрешаем Яндексу хранить фото студентов
        }
        self._post = post or _aiohttp_post
        self._timeout = timeout
        self._min_interval = min_interval
        self._lock = asyncio.Lock()          # общая очередь: запросы всех студентов по одному
        self._last = float("-inf")

    async def _send(self, body: dict) -> tuple[int, dict]:
        """Один запрос с соблюдением квоты; при 429 — один повтор после паузы."""
        loop = asyncio.get_running_loop()
        for attempt in range(2):
            async with self._lock:
                wait = self._last + self._min_interval - loop.time()
                if wait > 0:
                    await asyncio.sleep(wait)
                try:
                    status, resp = await asyncio.wait_for(
                        self._post(URL, self._headers, body, self._timeout), self._timeout)
                finally:
                    self._last = loop.time()
            if status != 429 or attempt == 1:
                return status, resp
        return status, resp

    async def recognize_markdown(self, image: bytes) -> str:
        """Сырой Markdown от модели (для отладки и логов)."""
        body = {
            "mimeType": "JPEG",
            "languageCodes": LANGUAGES,
            "model": MODEL,
            "content": base64.b64encode(prepare_image(image)).decode(),
        }
        try:
            status, resp = await self._send(body)
        except asyncio.TimeoutError:
            raise OcrError("таймаут") from None
        except Exception as exc:
            raise OcrError(f"сеть: {type(exc).__name__}") from None
        if status != 200:
            raise OcrError(f"HTTP {status}: {str(resp)[:300]}")
        ann = resp.get("result", resp).get("textAnnotation")
        if not isinstance(ann, dict):
            raise OcrError("неожиданный ответ API")
        return ann.get("markdown") or ""

    async def recognize(self, image: bytes) -> str:
        """Фото → строка в синтаксисе ввода. Unreadable — не разобрал; OcrError — сбой."""
        return latex_to_input(await self.recognize_markdown(image))
