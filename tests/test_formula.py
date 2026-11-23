"""Ввод формулы из Mini App: LaTeX редактора MathLive → ввод бота, клавиатура, страница."""
import json
import re
from pathlib import Path

import pytest
import sympy as sp

from bot.config import Settings
from bot.formula import MAX_DATA, FormulaError, formula_keyboard, parse_webapp_data
from core.checker import OK, check_antiderivative
from core.generator import generate
from core.latex import ru_latex
from core.parser import MAX_LEN, parse_answer, split_constant
from core.templates import load_templates

ROOT = Path(__file__).resolve().parent.parent
BANK = load_templates(ROOT / "data" / "templates")
PAGE = ROOT / "docs" / "formula"
x = sp.Symbol("x")


def data(latex, **over):
    return json.dumps({"v": 1, "latex": latex, **over})


def parsed(latex):
    body, has_c = split_constant(parse_webapp_data(data(latex)))
    return parse_answer(body), has_c


# --- строки, снятые с настоящего MathLive 0.111.4 (набор в headless Chrome) ------

@pytest.mark.parametrize("latex, expected", [
    (r"\frac{x^2}{2}", x**2 / 2),
    (r"\frac12x", x / 2),                                              # короткая дробь без скобок
    (r"\frac{\left(x+1\right)}{\left(x-1\right)}", (x + 1) / (x - 1)),
    (r"x^{\left(\frac13\right)}", x ** sp.Rational(1, 3)),
    (r"e^{\left(-\frac{x}{2}\right)}", sp.exp(-x / 2)),
    (r"e^{\left(2x\right)}", sp.exp(2 * x)),
    (r"2x\left(x+1\right)", 2 * x * (x + 1)),
    (r"sin\left(x\right)", sp.sin(x)),                                 # имя функции буквами
    (r"ln\left|x\right|", sp.log(sp.Abs(x))),
    (r"2arctg\left(\frac{x}{2}\right)", 2 * sp.atan(x / 2)),
    (r"\operatorname{\mathrm{tg}}\left(x\right)", sp.tan(x)),          # русские имена MathLive оборачивает дважды
    (r"\operatorname{\mathrm{arctg}}\left(x\right)", sp.atan(x)),
    (r"\operatorname{\mathrm{sh}}\left(x\right)", sp.sinh(x)),
    (r"\operatorname{\mathrm{tg}}^2\left(x\right)", sp.tan(x) ** 2),
    (r"\sqrt[3]{x^2}", (x ** 2) ** sp.Rational(1, 3)),                  # не x^(2/3): при x < 0 это разное
    (r"\sqrt{x+1}", sp.sqrt(x + 1)),
])
def test_mathlive_conversion(latex, expected):
    expr, _ = parsed(latex)
    assert sp.simplify(expr - expected) == 0, parse_webapp_data(data(latex))


# --- буквы рядом в LaTeX — произведение (и набранные буквами имена функций) --------

@pytest.mark.parametrize("latex, expected", [
    (r"xe^{x}", x * sp.exp(x)),
    (r"x\sqrt2", x * sp.sqrt(2)),
    (r"2x\sqrt{x+1}", 2 * x * sp.sqrt(x + 1)),
    (r"x\sin\left(x\right)", x * sp.sin(x)),
    (r"xsin\left(x\right)", x * sp.sin(x)),
    (r"x^22", 2 * x**2),
    (r"sinx", sp.sin(x)),
    (r"xlnx", x * sp.log(x)),
    (r"arctgx", sp.atan(x)),
    (r"arcctgx", sp.acot(x)),
    (r"e^{x}sinx", sp.exp(x) * sp.sin(x)),
    (r"shx+chx", sp.sinh(x) + sp.cosh(x)),
])
def test_adjacent_letters(latex, expected):
    expr, _ = parsed(latex)
    assert sp.simplify(expr - expected) == 0, parse_webapp_data(data(latex))


def test_word_const_survives():
    assert parsed(r"x+const")[1] is True


# --- команды, которыми MathLive может заменить набранное (по его документации) ---

@pytest.mark.parametrize("latex, expected", [
    (r"\exponentialE^{x}", sp.exp(x)),
    (r"\mleft(x+1\mright)^2", (x + 1) ** 2),
    (r"\ln\lvert x\rvert", sp.log(sp.Abs(x))),
    (r"2\times x", 2 * x),
])
def test_mathlive_aliases(latex, expected):
    expr, _ = parsed(latex)
    assert sp.simplify(expr - expected) == 0


def test_constant_kept():
    assert parsed(r"\frac12ln\left|x\right|+C")[1] is True
    assert parsed(r"\frac12ln\left|x\right|")[1] is False              # «забыл + C» ловится как раньше


@pytest.mark.parametrize("latex", [r"\frac{x}{\placeholder{}}", r"\sqrt{\placeholder{}}", r"\frac{}{2}", r"x^{}"])
def test_empty_cell_is_clear_error(latex):
    with pytest.raises(FormulaError, match="[Пп]уст"):                 # «есть пустая клетка», а не общая ошибка
        parse_webapp_data(data(latex))


@pytest.mark.parametrize("bad", [
    "", "не json", "[]", "{}", '{"v": 1}', '{"v": 2, "latex": "x"}', '{"v": 1, "latex": 5}',
    '{"v": 1, "latex": ""}', '{"v": 1, "latex": "   "}', data(r"\int x"), data(r"\begin{matrix}x\end{matrix}"),
    data("привет"), data(r"\href{http://a}{x}"),
])
def test_bad_data_rejected(bad):
    with pytest.raises(FormulaError) as e:
        parse_webapp_data(bad)
    assert str(e.value)                                                # текст для студента есть


def test_too_long_rejected():
    with pytest.raises(FormulaError):
        parse_webapp_data(data("x+" * MAX_DATA))


# --- весь банк: эталон, набранный в редакторе, проходит проверку ------------------

@pytest.mark.parametrize("tid", sorted(BANK))
def test_every_reference_can_be_entered(tid):
    inst = generate(BANK[tid], 0)
    cp = next(c for c in inst.checkpoints if c.type == "antiderivative")
    latex = re.sub(r"\\mathrm\{(\w+)\}", r"\\operatorname{\\mathrm{\1}}", ru_latex(cp.reference, cp.var)) + "+C"
    raw = parse_webapp_data(data(latex))
    assert len(raw) <= MAX_LEN, f"{len(raw)} > {MAX_LEN}: {raw}"
    assert check_antiderivative(raw, inst.integrand, domain=cp.domain, symbolic=False).status == OK, raw


# --- клавиатура ----------------------------------------------------------------

def test_keyboard_opens_web_app():
    kb = formula_keyboard("https://example.org/formula/")
    [[b]] = kb.keyboard
    assert b.web_app.url == "https://example.org/formula/"
    assert kb.resize_keyboard


def test_keyboard_needs_https():
    with pytest.raises(ValueError):
        formula_keyboard("http://example.org/")


def test_default_page_url_is_https():
    url = Settings.__dataclass_fields__["formula_url"].default
    assert formula_keyboard(url)


# --- страница -------------------------------------------------------------------

def page():
    return (PAGE / "index.html").read_text(encoding="utf-8")


def test_page_assets_are_local():
    html = page()
    external = [u for u in re.findall(r"""(?:src|href)=["']([^"']+)""", html) if u.startswith(("http", "//"))]
    assert external == ["https://telegram.org/js/telegram-web-app.js"]  # остальное лежит рядом, без CDN
    for u in re.findall(r"""(?:src|href)=["']([^"'#]+)""", html):
        if not u.startswith("http"):
            assert (PAGE / u).is_file(), u
    assert (PAGE / "fonts").is_dir() and any((PAGE / "fonts").glob("*.woff2"))


def test_page_ships_mathlive_license():
    text = (PAGE / "LICENSE-mathlive.txt").read_text(encoding="utf-8")
    assert "Arno Gourdol" in text and "Permission is hereby granted, free of charge" in text   # текст MIT


def test_page_protocol_and_settings():
    html = page()
    assert "sendData(" in html and "JSON.stringify" in html
    assert re.search(r"v:\s*1", html) and "latex" in html
    assert re.search(r"smartSuperscript\s*=\s*false", html)            # иначе x^10 превращается в x¹·0
    assert (ROOT / "docs" / ".nojekyll").exists()                      # Pages отдаёт файлы как есть


def test_page_can_restore_last_formula():
    """«Исправить»: прошлую формулу можно вернуть. Хранится только на устройстве, ошибка хранилища не ломает страницу."""
    html = page()
    assert 'id="restore"' in html and "Вернуть прошлую" in html
    assert "localStorage.setItem" in html and "localStorage.getItem" in html
    for m in re.finditer(r"localStorage\.", html):
        assert "try" in html[max(0, m.start() - 120):m.start()]
