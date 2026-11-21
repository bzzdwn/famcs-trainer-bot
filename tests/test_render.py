from pathlib import Path

import pytest
import sympy as sp

from bot.render import PNG_MAGIC, RenderError, render_latex, render_options, render_or_text
from core.generator import generate
from core.latex import integral_latex, ru_latex
from core.parser import parse_answer
from core.templates import load_templates

BANK = load_templates(Path(__file__).resolve().parent.parent / "data" / "templates")
x = sp.Symbol("x")


# --- русская нотация -------------------------------------------------------

@pytest.mark.parametrize("expr, must, must_not", [
    (sp.atan(x), "arctg", ["atan", "arctan"]),
    (sp.tan(x), "tg", ["tan"]),
    (sp.cot(x), "ctg", ["cot"]),
    (sp.acot(x), "arcctg", ["acot", "arccot"]),
    (sp.log(x), "ln", ["log"]),
    (sp.asin(x), "arcsin", ["asin"]),
    (sp.log(sp.Abs(x + 1)), "ln", ["log"]),
])
def test_ru_notation(expr, must, must_not):
    s = ru_latex(expr)
    assert must in s
    for bad in must_not:
        assert bad not in s


def test_ru_latex_from_string():
    assert "arctg" in ru_latex("2*atan(tan(x/2)/3)/3")


def test_integral_latex():
    s = integral_latex("1/(5 + 4*cos(x))")
    assert s.startswith("\\int") and s.rstrip().endswith("dx")


def test_statement_uses_ru_notation():
    for t in BANK.values():
        if t.statement:                       # условие задано вручную ({{параметры}}) — проверяется отдельно
            continue
        inst = generate(t, 0)
        assert inst.statement_latex == integral_latex(inst.integrand, t.var)


# --- рендер ----------------------------------------------------------------

def test_render_png():
    png = render_latex(ru_latex(sp.atan(x) / 2))
    assert png.startswith(PNG_MAGIC) and len(png) > 200


def test_render_error():
    with pytest.raises(RenderError):
        render_latex("\\frac{")


def test_render_or_text_fallback():
    assert render_or_text("\\frac{", "atan(x)/2") == "atan(x)/2"
    assert isinstance(render_or_text(ru_latex(sp.sin(x)), "sin(x)"), bytes)


def test_render_options():
    png = render_options([ru_latex(e) for e in (x, x**2, sp.sin(x), sp.atan(x))])
    assert png.startswith(PNG_MAGIC)


def test_render_student_preview():
    e = parse_answer("1/2 arctg(tg(x/2)/2)")
    assert render_latex(ru_latex(e) + " + C").startswith(PNG_MAGIC)


@pytest.mark.parametrize("tid", sorted(BANK))
def test_bank_renders(tid):
    """Всё, что бот покажет картинкой, рендерится без fallback."""
    for seed in range(5):
        inst = generate(BANK[tid], seed)
        assert render_latex(inst.statement_latex).startswith(PNG_MAGIC), f"{tid} seed={seed} условие"
        for cp in inst.checkpoints:
            for src in filter(None, (cp.expected, cp.reference, *cp.distractors)):
                latex = ru_latex(src, cp.var)
                assert render_latex(latex).startswith(PNG_MAGIC), f"{tid} seed={seed}: {src}"
