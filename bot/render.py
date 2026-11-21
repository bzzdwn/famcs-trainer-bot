"""Рендер формул в PNG через matplotlib mathtext (без pyplot — безопасно из потоков)."""
from io import BytesIO

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from PIL import Image

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
DPI = 200
PAD = 24          # px вокруг карточки вариантов
GAP = 28          # px между вариантами


class RenderError(RuntimeError):
    """mathtext не справился с формулой."""


def render_latex(latex: str, fontsize: int = 22) -> bytes:
    """Одна формула → PNG. Ошибка mathtext → RenderError."""
    fig = Figure(figsize=(0.01, 0.01), dpi=DPI)
    FigureCanvasAgg(fig)
    fig.text(0, 0, f"${latex}$", fontsize=fontsize, math_fontfamily="cm")
    buf = BytesIO()
    try:
        fig.savefig(buf, format="png", dpi=DPI, bbox_inches="tight", pad_inches=0.15, facecolor="white")
    except Exception as exc:
        raise RenderError(f"mathtext: {exc}") from None
    return buf.getvalue()


def render_or_text(latex: str, plain: str) -> bytes | str:
    """PNG, а если не вышло — plain-текст для отправки сообщением."""
    try:
        return render_latex(latex)
    except RenderError:
        return plain


def render_options(latexes: list[str]) -> bytes:
    """Карточка вариантов: «1) … 2) … 3) … 4) …» одной картинкой."""
    imgs = [Image.open(BytesIO(render_latex(rf"{i})\quad {l}"))).convert("RGB")
            for i, l in enumerate(latexes, 1)]
    w = max(im.width for im in imgs) + 2 * PAD
    h = sum(im.height for im in imgs) + GAP * (len(imgs) - 1) + 2 * PAD
    card = Image.new("RGB", (w, h), "white")
    y = PAD
    for im in imgs:
        card.paste(im, (PAD, y))
        y += im.height + GAP
    buf = BytesIO()
    card.save(buf, format="PNG")
    return buf.getvalue()
