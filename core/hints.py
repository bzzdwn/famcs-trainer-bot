"""Лестница подсказок к финальному ответу: уровни собираются из эталона и промежуточных точек."""
from dataclasses import dataclass
from string import ascii_uppercase

import sympy as sp

from core.generator import Instance
from core.latex import ru_latex
from core.parser import parse_trusted


@dataclass(frozen=True)
class Hint:
    level: int                        # с 1; пишется в attempts.hint_used
    text: str
    latex: str | None = None          # формула к тексту (рисуется картинкой)
    last: bool = False                # дальше только «Показать ответ»


# --- простые дроби -------------------------------------------------------------

def _fraction(expr: str, var: str) -> tuple[sp.Symbol, sp.Expr, sp.Expr, sp.Expr] | None:
    """(переменная, выражение, числитель, знаменатель) сокращённой рациональной дроби; None — не дробь."""
    e = parse_trusted(expr, var)
    v = next((s for s in e.free_symbols if s.name == var), None)
    if v is None or not e.is_rational_function(v):
        return None
    n, d = sp.fraction(sp.cancel(sp.together(e)))
    return (v, e, n, d) if d.has(v) else None


def _apart(expr: str, var: str) -> sp.Expr | None:
    fr = _fraction(expr, var)
    if fr is None:
        return None
    v, e, n, d = fr
    try:
        a = sp.apart(n / d, v)
    except (sp.PolynomialError, NotImplementedError):
        return None
    terms = set(sp.Add.make_args(a))
    return a if len(terms) > 1 and terms != set(sp.Add.make_args(e)) else None


def partial_fractions(expr: str, var: str) -> str | None:
    """Готовое разложение (строка sympy) или None."""
    a = _apart(expr, var)
    return None if a is None else str(a)


def partial_form(expr: str, var: str) -> str | None:
    """LaTeX вида разложения с буквами: A/t + B/(t+1) + (Ct+D)/(t²+1). None — раскладывать нечего."""
    if _apart(expr, var) is None:
        return None
    v, _, n, d = _fraction(expr, var)
    letters = iter(ascii_uppercase)
    parts = [r"\text{многочлен}"] if sp.degree(n, v) >= sp.degree(d, v) else []
    factors = sp.factor_list(d, v)[1]
    for p, k in sorted(factors, key=lambda f: (sp.degree(f[0], v), f[1], sp.default_sort_key(f[0]))):
        deg = sp.degree(p, v)
        for i in range(1, k + 1):
            num = " + ".join(next(letters) + ("" if j == 0 else f" {sp.latex(v**j)}") for j in range(deg - 1, -1, -1))
            base = ru_latex(p)
            den = base if i == 1 else (f"{base}^{{{i}}}" if p == v else rf"\left({base}\right)^{{{i}}}")
            parts.append(rf"\frac{{{num}}}{{{den}}}")
    return " + ".join(parts)


# --- первообразная по переменной замены --------------------------------------------

def antiderivative_in(expr: str, var: str) -> str | None:
    """Первообразная по переменной замены (строка sympy, ln с модулем) или None, если не берётся.
    Корни из многочленов и обратные функции не пробуем: sympy на них может думать слишком долго."""
    e = parse_trusted(expr, var)
    v = next((s for s in e.free_symbols if s.name == var), None)
    if v is None or e.has(sp.atan, sp.asin, sp.acos, sp.acot):
        return None
    if any(not p.exp.is_integer and p.exp.is_number and p.base != v for p in e.atoms(sp.Pow)):
        return None
    if any(f.has(v) and not f.args[0].is_polynomial(v) for f in e.atoms(sp.Function)):
        return None
    x = sp.Symbol(var, real=True)
    try:
        F = sp.integrate(e.xreplace({v: x}), x)
    except (NotImplementedError, ValueError, sp.PolynomialError):
        return None
    allowed = (sp.log, sp.atan, sp.sin, sp.cos, sp.tan, sp.exp, sp.Abs, sp.asin, sp.atanh, sp.sinh, sp.cosh)
    if F.has(sp.Integral, sp.RootSum, sp.Piecewise, sp.I) or not all(isinstance(f, allowed) for f in F.atoms(sp.Function)):
        return None
    if F.has(sp.sin, sp.cos):
        F = sp.expand(sp.trigsimp(F))
    F = F.replace(lambda z: isinstance(z, sp.log) and not isinstance(z.args[0], sp.Abs),
                  lambda z: sp.log(sp.Abs(z.args[0])))
    return str(F)


# --- что делать дальше -----------------------------------------------------------------

def _next_action(expr: str, var: str) -> str:
    """Совет по виду функции после замены (раскладывать на дроби нечего)."""
    e = parse_trusted(expr, var)
    v = next((s for s in e.free_symbols if s.name == var), None)
    if v is None:
        return f"Осталось проинтегрировать константу по {var} и вернуться к x."
    fr = _fraction(expr, var)
    if fr:
        _, _, n, d = fr
        ratio = sp.cancel(n / sp.diff(d, v))
        if not ratio.has(v):
            return "Числитель — с точностью до множителя производная знаменателя: получится логарифм."
        if sp.degree(d, v) >= 4 and len(sp.factor_list(d, v)[1]) == 1:
            return "Разложи знаменатель на квадратные трёхчлены (коэффициенты иррациональные), затем на простые дроби."
        return "Одна простая дробь: табличный интеграл, если нужно — выдели полный квадрат в знаменателе."
    if e.is_polynomial(v) or all(a.as_independent(v)[1].as_base_exp()[0] == v or not a.has(v)
                                 for a in sp.Add.make_args(sp.expand(e))):
        return ("Остались степени: интегрируй почленно." if sp.expand(e) == e
                else "Раскрой скобки — останутся степени — и интегрируй почленно.")
    if e.has(sp.log, sp.atan, sp.acot, sp.asin, sp.acos):
        return "Дальше по частям: логарифм или обратную функцию бери за u."
    if e.has(sp.sin, sp.cos):
        with_power = any(a.has(v) and a.is_polynomial(v) for a in sp.Mul.make_args(e))
        powers = any(p.base.has(sp.sin, sp.cos) and p.exp != 1 for p in e.atoms(sp.Pow))
        if with_power:
            return ("Сначала понизь степень синуса или косинуса, затем по частям: степень переменной бери за u."
                    if powers else "Дальше по частям: степень переменной бери за u.")
        return ("Степени синуса и косинуса: понижай степень или внеси под дифференциал." if powers
                else "Табличный интеграл.")
    if any(not p.exp.is_integer and p.base.is_polynomial(v) and p.base != v for p in e.atoms(sp.Pow) if p.exp.is_number):
        return "Под корнем квадратный трёхчлен: выдели полный квадрат и сведи к табличному интегралу."
    return f"Осталось проинтегрировать по {var}: интеграл близок к табличному."


_NAMES = [(sp.log, "ln"), (sp.atan, "arctg"), (sp.acot, "arcctg"), (sp.asin, "arcsin"), (sp.acos, "arccos"),
          (sp.sin, "sin"), (sp.cos, "cos"), (sp.tan, "tg"), (sp.cot, "ctg"), (sp.sinh, "sh"), (sp.cosh, "ch"),
          (sp.tanh, "th"), (sp.coth, "cth")]


def _components(F: sp.Expr) -> str:
    has = lambda f: any(a.free_symbols for a in F.atoms(f))      # arctg 2 — число, а не часть вида ответа
    names = [n for f, n in _NAMES if has(f)]
    if has(sp.exp):
        names.append("экспонента")
    if any(p.exp.is_number and not p.exp.is_integer and p.base.free_symbols for p in F.atoms(sp.Pow)):
        names.append("корень")
    if F.has(sp.Abs) and not F.has(sp.log):
        names.append("модуль")
    return "В ответе есть: " + ", ".join(names) + "." if names else "В ответе только степени переменной."


# --- лестница -------------------------------------------------------------------------------

def answer_hints(inst: Instance, direct: bool = False) -> tuple[Hint, ...]:
    """Уровни для финальной точки. direct — «Сразу ответ»: студент не видел промежуточных шагов."""
    if inst.limit_hints is not None:
        ladder = inst.limit_hints[direct]
        return tuple(Hint(i, text, latex, i == len(ladder)) for i, (text, latex) in enumerate(ladder, 1))
    k = next(i for i, c in enumerate(inst.checkpoints) if c.type == "antiderivative")
    cp, before = inst.checkpoints[k], inst.checkpoints[:k]
    mid = next((c for c in reversed(before) if c.type in ("expr", "transformed_integrand")), None)
    sub = mid if mid and mid.type == "transformed_integrand" else None
    steps: list[tuple[str, str | None]] = []
    if direct:
        if before and before[0].hint:
            steps.append((before[0].hint, None))
        if sub:
            steps.append(("После замены интеграл становится таким:", rf"\int {ru_latex(sub.expected, sub.var)}\, d{sub.var}"))
        elif mid:
            steps.append((mid.prompt, ru_latex(mid.expected, mid.var)))
    if sub:
        form = partial_form(sub.expected, sub.var)
        if form:
            steps.append((cp.hint or "Рациональная функция: разложи её на простые дроби. Вид разложения:", form))
            steps.append(("Вот разложение — интегрируй почленно:",
                          ru_latex(partial_fractions(sub.expected, sub.var), sub.var)))
        else:
            steps.append((cp.hint or _next_action(sub.expected, sub.var), None))
        F = antiderivative_in(sub.expected, sub.var)
        if F:
            back = rf",\quad {sub.var} = {ru_latex(sub.substitution, cp.var)}" if sub.substitution else ""
            steps.append((f"Первообразная по {sub.var}. Осталось вернуться к {cp.var}:", ru_latex(F, sub.var) + back))
    else:
        F = parse_trusted(cp.reference, cp.var)
        steps.append((cp.hint or _components(F), None))
        terms = sp.Add.make_args(F)
        if len(terms) > 1:
            term = min(terms, key=lambda a: (len(str(a)), str(a)))
            steps.append(("Одно из слагаемых ответа:", ru_latex(term, cp.var)))
    return tuple(Hint(i, text, latex, i == len(steps)) for i, (text, latex) in enumerate(steps, 1))
