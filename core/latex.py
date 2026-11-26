"""LaTeX в русской нотации: tg, ctg, arctg, arcctg, ln, sh, ch, th, cth."""
import re

import sympy as sp

from core.parser import parse_trusted

# порядок важен: длинные имена раньше коротких; (?![a-z]) не трогает \tanh, \coth
_RU = [
    (re.compile(r"\\arctan(?![a-z])"), r"\\mathrm{arctg}"),
    (re.compile(r"\\operatorname\{arccot\}"), r"\\mathrm{arcctg}"),
    (re.compile(r"\\tan(?![a-z])"), r"\\mathrm{tg}"),
    (re.compile(r"\\cot(?![a-z])"), r"\\mathrm{ctg}"),
    (re.compile(r"\\sinh(?![a-z])"), r"\\mathrm{sh}"),
    (re.compile(r"\\cosh(?![a-z])"), r"\\mathrm{ch}"),
    (re.compile(r"\\tanh(?![a-z])"), r"\\mathrm{th}"),
    (re.compile(r"\\coth(?![a-z])"), r"\\mathrm{cth}"),
    (re.compile(r"\\operatorname\{([a-z]+)\}"), r"\\mathrm{\1}"),   # mathtext и \operatorname не дружат
    # mathtext съедает пробелы: «2∛x» читается как «2³√x», «48ln», «x³ln», «x arctg» слипаются
    (re.compile(r"([\w})])\s*(\\(?:sqrt\[|ln|log|sin|cos|sinh|cosh|tanh|coth|arcsin|arccos|mathrm|exp)(?![a-z]))"),
     r"\1\\,\2"),
    # знак интеграла в mathtext вплотную прилипает к подынтегральной функции
    (re.compile(r"\\int\s*"), r"\\int\\;\\;"),
]


def _ru(latex: str) -> str:
    for pat, repl in _RU:
        latex = pat.sub(repl, latex)
    return latex


def polish(latex: str) -> str:
    """Та же постобработка (русская нотация, отступы для mathtext) для LaTeX, написанного вручную."""
    return _ru(latex)


def _to_expr(e: sp.Expr | str, var: str) -> sp.Expr:
    return parse_trusted(e, var) if isinstance(e, str) else e


def ru_latex(expr: sp.Expr | str, var: str = "x") -> str:
    """sympy-выражение (или доверенная строка) → LaTeX, совместимый с matplotlib mathtext."""
    return _ru(sp.latex(_to_expr(expr, var), ln_notation=True, inv_trig_style="full"))


def limit_latex(expr: sp.Expr, var: str, to: sp.Expr, side: str, body: str | None = None) -> str:
    """lim f(x) в нотации Демидовича: x → +0, x → 1 − 0, x → +∞. body — готовый LaTeX функции вместо ru_latex(expr)."""
    point = {sp.oo: r"+\infty", -sp.oo: r"-\infty"}.get(to) or sp.latex(to)
    if side != "both" and to not in (sp.oo, -sp.oo):
        point = f"{side}0" if to == 0 else rf"{point} {side} 0"
    return rf"\lim_{{{var} \to {point}}}\;\; {body or ru_latex(expr, var)}"


def integral_latex(integrand: sp.Expr | str, var: str = "x") -> str:
    """∫ f(x) dx в русской нотации."""
    e = sp.Integral(_to_expr(integrand, var), sp.Symbol(var))
    return _ru(sp.latex(e, ln_notation=True, inv_trig_style="full"))
