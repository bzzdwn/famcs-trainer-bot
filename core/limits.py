"""Пределы: тип неопределённости, цепочка Лопиталя, самопроверка значения."""
import re
from dataclasses import dataclass
from functools import lru_cache

import mpmath as mp
import sympy as sp

from core.latex import ru_latex

TYPES = ("0/0", "∞/∞", "0·∞", "∞−∞", "1^∞", "0⁰", "∞⁰")
NO_TYPE = "нет неопределённости"
MAX_DIFFS = 3                 # «По шагам» — не больше трёх дифференцирований
MAX_CARD_PX = 1500            # и ни одна карточка вариантов не шире
MAX_OPS = 120                 # громоздкие выражения не упрощаем: sympy может думать слишком долго
TRIG_OPS = 40                 # тригонометрию — только у совсем коротких
INF = (sp.oo, -sp.oo)


class LimitError(ValueError):
    """Предел не подтверждён или цепочка не строится."""


@dataclass(frozen=True)
class Link:
    num: sp.Expr              # отношение на этом звене
    den: sp.Expr
    kind: str                 # тип неопределённости звена или NO_TYPE


# --- подстановка -------------------------------------------------------------------

def _lim(e: sp.Expr, var: sp.Symbol, to: sp.Expr, side: str) -> sp.Expr | None:
    """Предел по sympy; None — не взялся или не существует."""
    try:
        if to in INF:
            r = sp.limit(e, var, to)
        elif side == "both":
            r, left = sp.limit(e, var, to, "+"), sp.limit(e, var, to, "-")
            if r != left and sp.simplify(r - left) != 0:
                return None
        else:
            r = sp.limit(e, var, to, side)
    except (NotImplementedError, ValueError, TypeError, AttributeError, RecursionError, sp.PoleError):
        return None
    ok = r in INF or (r.is_number and r.is_finite is not False and not r.has(sp.nan, sp.zoo, sp.AccumBounds, sp.Limit))
    return r if ok and r.is_extended_real is not False else None


def plain_value(expr: sp.Expr, var: sp.Symbol, to: sp.Expr, side: str) -> sp.Expr | None:
    """Значение прямой подстановкой (±oo — по арифметике бесконечностей); None — внутри неопределённость."""
    if not expr.has(var):
        return expr
    if expr == var:
        return to
    parts = [plain_value(a, var, to, side) for a in expr.args]
    if any(p is None for p in parts):
        return None
    inf = [p for p in parts if p in INF]
    if expr.is_Add and sp.oo in inf and -sp.oo in inf:
        return None
    if expr.is_Mul and inf and any(p == 0 for p in parts):
        return None
    if expr.is_Pow:
        b, p = parts
        if (p == 0 and (b == 0 or b in INF)) or (b == 1 and p in INF):
            return None
    return _lim(expr, var, to, side)


def ratio_type(num: sp.Expr, den: sp.Expr, var: sp.Symbol, to: sp.Expr, side: str) -> str | None:
    """"0/0", "∞/∞", NO_TYPE; None — своя неопределённость внутри числителя или знаменателя."""
    n, d = (_signed(e, var, to, side) for e in (num, den))
    if n is None or d is None:
        return None
    if n == 0 and d == 0:
        return "0/0"
    if n in INF and d in INF:
        return "∞/∞"
    return NO_TYPE


def _signed(e: sp.Expr, var, to, side):
    """plain_value; у двустороннего предела бесконечность разных знаков — всё равно бесконечность."""
    if side != "both" or to in INF:
        return plain_value(e, var, to, side)
    r, left = plain_value(e, var, to, "+"), plain_value(e, var, to, "-")
    if r is None or left is None:
        return None
    return r if r == left or (r in INF and left in INF) or sp.simplify(r - left) == 0 else None


# --- цепочка --------------------------------------------------------------------------

def _tidy(num: sp.Expr, den: sp.Expr) -> list[tuple[sp.Expr, sp.Expr]]:
    """f′/g′ как есть и сокращённая (без тригонометрии и с ней)."""
    out = [(num, den)]
    ops = num.count_ops() + den.count_ops()
    preps = [sp.powsimp] if ops <= MAX_OPS else []
    if ops <= TRIG_OPS:
        preps.append(lambda e: sp.trigsimp(sp.together(e)))
    for prep in preps:
        try:
            c = sp.fraction(sp.cancel(sp.together(prep(num / den))))
        except (sp.PolynomialError, NotImplementedError, ValueError, TypeError):
            continue
        if c not in out:
            out.append(c)
    return out


def _size(num, den) -> int:
    return len(ru_latex(num)) + len(ru_latex(den))


@lru_cache(maxsize=256)
def lhopital_chain(num, den, var, to, side, max_diffs: int = 5) -> tuple[Link, ...] | None:
    """Кратчайшая цепочка: на каждом шаге f′/g′ как есть или сокращённая. Последнее звено — NO_TYPE. None — не сходится."""
    front: list[tuple[sp.Expr, sp.Expr, tuple[Link, ...]]] = [(num, den, ())]
    for k in range(max_diffs + 1):
        done, nxt, seen = [], [], set()
        for n, d, path in front:
            kind = ratio_type(n, d, var, to, side)
            if kind is None:
                continue
            here = (*path, Link(n, d, kind))
            if kind == NO_TYPE:
                done.append(here)
            elif k < max_diffs:
                for c in _tidy(sp.diff(n, var), sp.diff(d, var)):
                    if c not in seen and c[1] != 0:
                        seen.add(c)
                        nxt.append((*c, here))
        if done:
            return min(done, key=lambda p: (max(_size(q.num, q.den) for q in p), str(p)))
        front = sorted(nxt, key=lambda c: _size(c[0], c[1]))[:6]
    return None


# --- ширина карточки -------------------------------------------------------------------

def _glyphs(latex: str) -> int:
    """Сколько знаков видно в строке LaTeX (без вложенных дробей — их считаем по более длинной части)."""
    m = re.search(r"\\frac\{", latex)
    if m:
        parts, i = [], m.end() - 1
        for _ in range(2):
            depth, j = 0, i
            while True:
                depth += {"{": 1, "}": -1}.get(latex[j], 0)
                if depth == 0:
                    break
                j += 1
            parts.append(latex[i + 1:j])
            i = j + 1
        return _glyphs(latex[:m.start()]) + max(map(_glyphs, parts)) + _glyphs(latex[i:])
    names = re.sub(r"\\(sin|cos|ln|exp|arcsin|arccos|sqrt|lim|left|right|mathrm|quad)", lambda k: {
        "left": "", "right": "", "mathrm": "", "quad": ""}.get(k.group(1), k.group(1)), latex)
    return len(re.sub(r"\\[a-zA-Z]+|\\.|[{}^_\s]", "", names))


def card_px(latex: str) -> int:
    """Оценка ширины карточки варианта в px без рендера (±20 %, калибровка — tests/test_limits.py):
    matplotlib в процессе-генераторе стоил бы серверу лишних 50 МБ памяти."""
    return round(220 + 20.5 * _glyphs(latex))


# --- самопроверка --------------------------------------------------------------------------

def _num_at(f, to, side, k: int):
    mp.mp.dps = 12 * k + 60
    if to in INF:
        pt = mp.mpf(10) ** k * (1 if to == sp.oo else -1)
    else:
        h = mp.mpf(10) ** (-k)
        pt = mp.mpf(sp.N(to, mp.mp.dps + 5)) + (h if side == "+" else -h)
    return mp.re(f(pt))


def _confirms(expr, var, to, side, value) -> bool:
    """Численно: значения при h = 10^−20, 10^−40, 10^−80 с экстраполяцией по 1/k (логарифмы сходятся медленно)."""
    f, dps = sp.lambdify(var, expr, "mpmath"), mp.mp.dps
    try:
        v = [_num_at(f, to, side, k) for k in (20, 40, 80)]
        if value in INF:
            sign = 1 if value == sp.oo else -1
            return all(a * sign > 0 for a in v) and abs(v[2]) > abs(v[1]) > abs(v[0]) and abs(v[2]) > 50
        target = mp.mpf(sp.N(value, 40))
        r1 = [2 * v[1] - v[0], 2 * v[2] - v[1]]
        best = min(abs(v[2] - target), abs(r1[1] - target), abs((4 * r1[1] - r1[0]) / 3 - target))
        return best <= mp.mpf("1e-3") * max(1, abs(target))
    except (ValueError, ZeroDivisionError, OverflowError, TypeError, mp.NoConvergence):
        return False
    finally:
        mp.mp.dps = dps


def verify_limit(expr: sp.Expr, var: sp.Symbol, to: sp.Expr, side: str, value: sp.Expr | None = None) -> sp.Expr:
    """Значение предела, подтверждённое sympy и численно (value — ответ автора, если sympy не справляется).
    side: "+", "-", "both". Расхождение или разные односторонние пределы → LimitError."""
    if value is None:
        value = _lim(expr, var, to, side)
        if value is None:
            raise LimitError(f"предел {expr} при {var} → {to} ({side}) не существует или не берётся")
    sides = ("+", "-") if side == "both" and to not in INF else (side,)
    for s in sides:
        if not _confirms(expr, var, to, s, value):
            raise LimitError(f"предел {expr} при {var} → {to} ({s}): {value} численно не подтверждается")
    return value
