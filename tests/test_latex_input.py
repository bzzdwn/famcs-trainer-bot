import pytest
import sympy as sp

from core.latex_input import Unreadable, latex_to_input
from core.parser import parse_answer, split_constant

x = sp.Symbol("x")


def parsed(markdown):
    body, has_c = split_constant(latex_to_input(markdown))
    return parse_answer(body), has_c


@pytest.mark.parametrize("md, expected", [
    ("$\\frac{x^{2}}{2}$", x**2 / 2),
    ("$\\dfrac{1}{x+1}$", 1 / (x + 1)),
    ("$\\frac{1}{\\sqrt{x}}$", 1 / sp.sqrt(x)),
    ("$\\sqrt{x+1}$", sp.sqrt(x + 1)),
    ("$\\sqrt[3]{x}$", x ** sp.Rational(1, 3)),
    ("$\\operatorname{arctg} x$", sp.atan(x)),
    ("$\\arctan(x)$", sp.atan(x)),
    ("$\\mathrm{tg}\\,x$", sp.tan(x)),
    ("$\\tan x$", sp.tan(x)),
    ("$\\ln\\left|x-1\\right|$", sp.log(sp.Abs(x - 1))),
    ("$\\ln|x|$", sp.log(sp.Abs(x))),
    ("$e^{2x}$", sp.exp(2 * x)),
    ("$2 \\cdot x$", 2 * x),
    ("$\\left(x+1\\right)^{2}$", (x + 1) ** 2),
    ("$2\\sin x\\cos x$", 2 * sp.sin(x) * sp.cos(x)),
    ("$-\\frac{1}{3}\\cos^{3}x$", -sp.cos(x) ** 3 / 3),
    ("$\\frac{1}{2}\\operatorname{arctg}\\frac{x}{2}$", sp.atan(x / 2) / 2),
    ("$\\pi x$", sp.pi * x),
    ("$x \\, e^{x}$", x * sp.exp(x)),
    ("$$x^{3}$$", x**3),
    ("\\(x^{3}\\)", x**3),
    ("x^{3}", x**3),                                 # без обёрток
    ("$\\frac{x^2}{2}$", x**2 / 2),                  # степень без фигурных скобок
])
def test_conversion(md, expected):
    expr, _ = parsed(md)
    assert sp.simplify(expr - expected) == 0, latex_to_input(md)


@pytest.mark.parametrize("md", ["$\\frac{x^{2}}{2} + C$", "$x + C$", "$\\ln|x| + c$"])
def test_constant_survives(md):
    _, has_c = parsed(md)
    assert has_c


@pytest.mark.parametrize("md", ["", "   ", "$$", "UNREADABLE", "Привет", "$x$ и $y$", "$x$\n\n$y$"])
def test_unreadable(md):
    with pytest.raises(Unreadable):
        latex_to_input(md)


def test_no_latex_left():
    out = latex_to_input("$\\frac{1}{2}\\ln\\left|\\frac{x-1}{x+1}\\right| + C$")
    assert "\\" not in out and "{" not in out and "$" not in out


# --- реальные ответы Yandex math-markdown (20.11.2026) ---------------------

@pytest.mark.parametrize("md, expected", [
    ("$$\\frac {x ^ {3}} {3} \\ln 2x - \\frac {x ^ {3}} {9} + C$$\n\n",
     x**3 * sp.log(2 * x) / 3 - x**3 / 9),
    ("## $$\\frac {2} {3} \\arctg (\\frac {\\operatorname {tg} (x / 2)} {3}) + C$$\n\n",
     2 * sp.atan(sp.tan(x / 2) / 3) / 3),
    ("## $$6 \\left (\\frac {\\sqrt {x}} {3} - \\sqrt [3] {x} + 4 \\sqrt [6] {x} - 8 \\ln (\\sqrt [6] {x} + 2) \\right) + C$$\n\n",
     6 * (sp.sqrt(x) / 3 - x ** sp.Rational(1, 3) + 4 * x ** sp.Rational(1, 6) - 8 * sp.log(x ** sp.Rational(1, 6) + 2))),
])
def test_real_yandex_output(md, expected):
    expr, has_c = parsed(md)
    assert has_c and sp.simplify(expr - expected) == 0, latex_to_input(md)
