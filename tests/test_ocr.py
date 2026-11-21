import asyncio
import base64
from io import BytesIO

import pytest
from PIL import Image

from bot.ocr import MAX_PIXELS, MODEL, URL, OcrError, YandexOcr, prepare_image
from bot.photo import OCR_DAILY_LIMIT, photo_attempt_fields, pick_photo_size
from core.latex_input import Unreadable
from core.parser import parse_answer, split_constant


def jpeg(w, h):
    buf = BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, format="JPEG")
    return buf.getvalue()


class FakePost:
    def __init__(self, status=200, response=None, delay=0.0):
        self.status, self.response, self.delay, self.calls = status, response or {}, delay, []

    async def __call__(self, url, headers, body, timeout):
        self.calls.append((url, headers, body, timeout))
        await asyncio.sleep(self.delay)
        return self.status, self.response


def md_response(md, wrapped=True):
    ann = {"textAnnotation": {"fullText": "", "markdown": md, "blocks": []}, "page": "0"}
    return {"result": ann} if wrapped else ann


# --- подготовка картинки -----------------------------------------------------

def test_prepare_downscales():
    out = Image.open(BytesIO(prepare_image(jpeg(3000, 2000))))
    assert out.format == "JPEG"
    assert out.width * out.height <= MAX_PIXELS
    assert abs(out.width / out.height - 1.5) < 0.01


def test_prepare_keeps_small():
    out = Image.open(BytesIO(prepare_image(jpeg(800, 600))))
    assert (out.width, out.height) == (800, 600)


def test_prepare_png_rgba():
    buf = BytesIO()
    Image.new("RGBA", (500, 500), (0, 0, 0, 0)).save(buf, format="PNG")
    assert Image.open(BytesIO(prepare_image(buf.getvalue()))).format == "JPEG"


# --- запрос ------------------------------------------------------------------

async def test_request_shape():
    post = FakePost(response=md_response("$x + C$"))
    ocr = YandexOcr("KEY", "FOLDER", post=post)
    await ocr.recognize(jpeg(3000, 2000))
    (url, headers, body, timeout), = post.calls
    assert url == URL
    assert headers["Authorization"] == "Api-Key KEY"
    assert headers["x-folder-id"] == "FOLDER"
    assert headers["x-data-logging-enabled"] == "false"          # фото студентов не логируем у Яндекса
    assert set(body) == {"mimeType", "languageCodes", "model", "content"}   # ни условия, ни эталона
    assert body["model"] == MODEL
    img = Image.open(BytesIO(base64.b64decode(body["content"])))
    assert img.width * img.height <= MAX_PIXELS                  # уменьшили до отправки
    assert timeout == 15.0


# --- ответ -------------------------------------------------------------------

@pytest.mark.parametrize("wrapped", [True, False])
async def test_recognize_ok(wrapped):
    ocr = YandexOcr("K", "F", post=FakePost(response=md_response("$\\frac{x^{2}}{2} + C$", wrapped)))
    text = await ocr.recognize(jpeg(100, 100))
    body, has_c = split_constant(text)
    assert has_c and str(parse_answer(body)) == "x**2/2"


@pytest.mark.parametrize("md", ["", "UNREADABLE", "$x$ и $y$"])
async def test_recognize_unreadable(md):
    ocr = YandexOcr("K", "F", post=FakePost(response=md_response(md)))
    with pytest.raises(Unreadable):
        await ocr.recognize(jpeg(100, 100))


@pytest.mark.parametrize("status, response", [(500, {}), (401, {"message": "bad key"}), (200, {"weird": 1})])
async def test_recognize_api_error(status, response):
    ocr = YandexOcr("K", "F", post=FakePost(status=status, response=response))
    with pytest.raises(OcrError):
        await ocr.recognize(jpeg(100, 100))


async def test_recognize_timeout():
    ocr = YandexOcr("K", "F", post=FakePost(response=md_response("$x$"), delay=1.0), timeout=0.1)
    with pytest.raises(OcrError):
        await ocr.recognize(jpeg(100, 100))


async def test_recognize_bad_image():
    ocr = YandexOcr("K", "F", post=FakePost(response=md_response("$x$")))
    with pytest.raises(Unreadable):
        await ocr.recognize(b"not an image")


# --- фото из Telegram и поля попытки ----------------------------------------

class Size:
    def __init__(self, w, h):
        self.width, self.height, self.file_id = w, h, f"{w}x{h}"


def test_pick_photo_size():
    sizes = [Size(90, 60), Size(320, 213), Size(800, 533), Size(1280, 853), Size(2560, 1706)]
    assert pick_photo_size(sizes).file_id == "800x533"            # крупнейший ≤ 1 Мп (1280×853 ≈ 1.09 Мп — уже нет)
    assert pick_photo_size([Size(2560, 1706), Size(1600, 1200)]).file_id == "1600x1200"   # все крупные → мельчайший


def test_pick_photo_size_boundary():
    assert pick_photo_size([Size(1000, 1000), Size(500, 500)]).file_id == "1000x1000"


@pytest.mark.parametrize("raw, final, edited", [
    ("x + C", "x + C", False),
    ("x + C", " x+C ", False),          # только пробелы
    ("x + c", "x + C", True),
    ("sin(x) + C", "-sin(x) + C", True),
])
def test_photo_attempt_fields(raw, final, edited):
    assert photo_attempt_fields(raw, final) == {"input_method": "photo", "ocr_raw": raw, "ocr_edited": edited}


def test_daily_limit_value():
    assert OCR_DAILY_LIMIT == 30


# --- языки и квота 1 запрос/с ------------------------------------------------

async def test_language_codes():
    post = FakePost(response=md_response("$x$"))
    await YandexOcr("K", "F", post=post).recognize(jpeg(100, 100))
    assert post.calls[0][2]["languageCodes"] == ["ru", "en"]      # "*" math-markdown отвергает (HTTP 400)


class StampPost(FakePost):
    async def __call__(self, url, headers, body, timeout):
        self.calls.append(asyncio.get_running_loop().time())
        return 200, md_response("$x$")


async def test_requests_are_spaced():
    post = StampPost()
    ocr = YandexOcr("K", "F", post=post, min_interval=0.2)
    await asyncio.gather(*(ocr.recognize(jpeg(50, 50)) for _ in range(3)))   # три студента одновременно
    gaps = [b - a for a, b in zip(post.calls, post.calls[1:])]
    assert len(post.calls) == 3 and all(g >= 0.19 for g in gaps)


class RateLimitedOnce(FakePost):
    async def __call__(self, url, headers, body, timeout):
        self.calls.append(1)
        if len(self.calls) == 1:
            return 429, {"error": {"message": "rate quota limit exceed"}}
        return 200, md_response("$x + C$")


async def test_retry_on_429():
    post = RateLimitedOnce()
    text = await YandexOcr("K", "F", post=post, min_interval=0.05).recognize(jpeg(50, 50))
    assert len(post.calls) == 2 and "C" in text


async def test_429_twice_is_error():
    post = FakePost(status=429, response={"error": {"message": "rate"}})
    with pytest.raises(OcrError):
        await YandexOcr("K", "F", post=post, min_interval=0.05).recognize(jpeg(50, 50))
    assert len(post.calls) == 2                                    # один повтор, не бесконечно
