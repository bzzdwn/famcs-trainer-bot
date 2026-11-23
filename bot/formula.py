"""Ввод формулы через Mini App (MathLive): данные из sendData → строка в синтаксисе ввода бота."""
import json
from html import escape

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, WebAppInfo

from core.latex import ru_latex
from core.latex_input import EmptyCell, Unreadable, latex_to_input
from core.parser import ParseError, parse_answer, split_constant

BUTTON = "✏️ Ввести формулу"
MAX_DATA = 4096                 # предел Telegram.WebApp.sendData, байт
VERSION = 1


class FormulaError(ValueError):
    """Данные не разобраны. str(e) — сообщение для студента."""


def parse_webapp_data(data: str) -> str:
    """'{"v": 1, "latex": "\\\\frac12x^2+C"}' → строка ввода. Плохие данные → FormulaError.
    Данные приходят от клиента, доверять им нельзя: дальше строку разбирает core.parser со своим белым списком."""
    if len(data.encode()) > MAX_DATA:
        raise FormulaError("Формула слишком длинная.")
    try:
        obj = json.loads(data)
    except ValueError:
        raise FormulaError("Не получилось прочитать формулу из редактора. Открой его ещё раз.") from None
    latex = obj.get("latex") if isinstance(obj, dict) and obj.get("v") == VERSION else None
    if not isinstance(latex, str):
        raise FormulaError("Не получилось прочитать формулу из редактора. Открой его ещё раз.")
    if not latex.strip():
        raise FormulaError("Пустая формула: набери ответ и отправь ещё раз.")
    try:
        return latex_to_input(latex)
    except EmptyCell:
        raise FormulaError("В формуле осталась пустая клетка — заполни её или удали.") from None
    except Unreadable as e:
        raise FormulaError(f"Не понял формулу: {e}.") from None


def formula_keyboard(url: str) -> ReplyKeyboardMarkup:
    """Кнопка под полем ввода, открывающая страницу (sendData работает только из неё)."""
    if not url.startswith("https://"):
        raise ValueError("Mini App открывается только по https")
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=BUTTON, web_app=WebAppInfo(url=url))]],
                               resize_keyboard=True)


def formula_report(data: str, var: str = "x") -> tuple[str, str | None]:
    """Прототип /formula: (HTML-текст «что пришло → как перевелось», LaTeX понятой формулы или None при ошибке)."""
    try:
        raw = parse_webapp_data(data)
    except FormulaError as e:
        return f"⚠️ {escape(str(e))}\nПришло: <code>{escape(data[:500])}</code>", None
    body, has_c = split_constant(raw)
    sent = f"LaTeX: <code>{escape(json.loads(data)['latex'])}</code>\nВвод бота ({len(raw)} симв.): <code>{escape(raw)}</code>"
    try:
        expr = parse_answer(body, var)
    except ParseError as e:
        return f"⚠️ {escape(str(e))}\n{sent}", None
    return sent, ru_latex(expr) + (" + C" if has_c else "")
