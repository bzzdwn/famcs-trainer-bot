"""Проверка ответов: символьно, при неудаче — численно. Без LLM."""
import multiprocessing
import random
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from enum import Enum

import sympy as sp
from pebble import ProcessPool

from core.parser import ParseError, parse_answer, parse_trusted, split_constant

OK, WRONG, SIGN, FACTOR, NO_CONST, PARSE_ERROR, TIMEOUT = (
    "ok", "wrong", "sign", "factor", "no_const", "parse_error", "timeout")

N_POINTS = 12
MIN_VALID = 8
REL_TOL = 1e-8
IMAG_TOL = 1e-12
SEED = 42

NOTE_SIGN = "Проверь знак"
NOTE_FACTOR = "Потерян множитель"
NOTE_NO_CONST = "Забыл +C"
NOTE_ADD_CONST = "И не забудь +C"
NOTE_MODULUS = "Не забудь модуль под логарифмом: ln|u|"
NOTE_TIMEOUT = "Не смог проверить, упрости запись"


@dataclass(frozen=True)
class Verdict:
    status: str             # ok | wrong | sign | factor | no_const | parse_error | timeout
    note: str | None = None  # пояснение студенту (потерян модуль, ошибка парсинга и т.п.)


class Cmp(Enum):
    EQUAL = "equal"              # a == b
    NEG = "neg"                  # a == -b
    FACTOR = "factor"            # a == c*b, c ≠ ±1
    DIFFERENT = "different"
    INSUFFICIENT = "insufficient"  # < MIN_VALID вещественных точек


# --- численная часть ---------------------------------------------------------

def _points(domain: tuple[float, float]) -> list[float]:
    rng = random.Random(SEED)
    lo, hi = domain
    return [rng.uniform(lo, hi) for _ in range(N_POINTS)]


def _real_value(e: sp.Expr, subs: dict) -> float | None:
    """Значение e в точке, если оно конечно и вещественно; иначе None."""
    try:
        v = complex(e.evalf(30, subs=subs))
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None
    if v != v or abs(v.real) == float("inf"):  # nan / inf
        return None
    if abs(v.imag) > IMAG_TOL * max(1.0, abs(v.real)):
        return None
    return v.real


def _close(u: float, v: float) -> bool:
    return abs(u - v) <= REL_TOL * max(1.0, abs(u), abs(v))


def numeric_compare(a: sp.Expr, b: sp.Expr, var: str, domain: tuple[float, float]) -> Cmp:
    """Сравнение a и b в N_POINTS случайных точках domain (фиксированный SEED)."""
    syms = [s for s in (a.free_symbols | b.free_symbols) if s.name == var]
    pairs = []
    for p in _points(domain):
        subs = {s: p for s in syms}
        u, v = _real_value(a, subs), _real_value(b, subs)
        if u is not None and v is not None:
            pairs.append((u, v))
    if len(pairs) < MIN_VALID:
        return Cmp.INSUFFICIENT
    if all(_close(u, v) for u, v in pairs):
        return Cmp.EQUAL
    if all(_close(u, -v) for u, v in pairs):
        return Cmp.NEG
    ratios = [u / v for u, v in pairs if abs(v) > 1e-12]
    if len(ratios) >= MIN_VALID and abs(ratios[0]) > 1e-12 and all(_close(r, ratios[0]) for r in ratios):
        return Cmp.FACTOR
    return Cmp.DIFFERENT


def _symbolic_compare(a: sp.Expr, b: sp.Expr) -> Cmp | None:
    """Cmp, если simplify дал однозначный ответ; иначе None → численная ветка."""
    try:
        if sp.simplify(a - b) == 0:
            return Cmp.EQUAL
        if b != 0:
            r = sp.simplify(a / b)
            if r.is_number and r.is_real and r != 0:
                if r == 1:          # simplify(a − b) мог дать Piecewise(zoo в точке, 0) — но a/b ≡ 1: равны
                    return Cmp.EQUAL
                return Cmp.NEG if r == -1 else Cmp.FACTOR
    except Exception:
        pass
    return None


def _compare(a, b, var, domain, symbolic: bool) -> Cmp:
    if symbolic:
        res = _symbolic_compare(a, b)
        if res is not None:
            return res
    return numeric_compare(a, b, var, domain)


_CMP_TO_VERDICT = {
    Cmp.EQUAL: (OK, None),
    Cmp.NEG: (SIGN, NOTE_SIGN),
    Cmp.FACTOR: (FACTOR, NOTE_FACTOR),
    Cmp.DIFFERENT: (WRONG, None),
    Cmp.INSUFFICIENT: (WRONG, None),
}


def _to_real(e: sp.Expr, var: str) -> sp.Expr:
    """var → вещественный символ: так diff(Abs(x)) = sign(x), а не через re/im."""
    xr = sp.Symbol(var, real=True)
    return e.subs({s: xr for s in e.free_symbols if s.name == var})


# --- проверки по типам ------------------------------------------------------

def check_choice(selected: str, correct: list[str]) -> Verdict:
    return Verdict(OK if selected in correct else WRONG)


def check_expr(user: str, expected: str, var: str = "x",
               domain: tuple[float, float] = (-3.0, 3.0), *, symbolic: bool = True) -> Verdict:
    """expr и transformed_integrand: эквивалентность user и expected."""
    try:
        u = parse_answer(user, var)
    except ParseError as e:
        return Verdict(PARSE_ERROR, str(e))
    u, e = _to_real(u, var), _to_real(parse_trusted(expected, var), var)
    return Verdict(*_CMP_TO_VERDICT[_compare(u, e, var, domain, symbolic)])


def _lost_modulus(F: sp.Expr, var: str, domain) -> bool:
    """Есть ли log(u) без модуля, где u < 0 в какой-то точке domain."""
    syms = [s for s in F.free_symbols if s.name == var]
    for lg in F.atoms(sp.log):
        arg = lg.args[0]
        if isinstance(arg, sp.Abs):
            continue
        for p in _points(domain):
            v = _real_value(arg, {s: p for s in syms})
            if v is not None and v < 0:
                return True
    return False


def check_antiderivative(user: str, integrand: str, var: str = "x",
                         domain: tuple[float, float] = (-3.0, 3.0), *, symbolic: bool = True) -> Verdict:
    """F' == f. Любая константа и форма принимаются; ln u вместо ln|u| — ok с note.
    Константа интегрирования (+C) обязательна."""
    body, has_c = split_constant(user)
    try:
        F = parse_answer(body, var)
    except ParseError as e:
        return Verdict(PARSE_ERROR, str(e))
    F = _to_real(F, var)
    f = _to_real(parse_trusted(integrand, var), var)
    cmp = _compare(sp.diff(F, sp.Symbol(var, real=True)), f, var, domain, symbolic)

    if cmp is Cmp.EQUAL:
        if not has_c:
            return Verdict(NO_CONST, NOTE_NO_CONST)
        return Verdict(OK, NOTE_MODULUS if _lost_modulus(F, var, domain) else None)
    status, note = _CMP_TO_VERDICT[cmp]
    if not has_c:
        note = f"{note}. {NOTE_ADD_CONST}" if note else NOTE_ADD_CONST
    return Verdict(status, note)


_CHECKS = {
    "expr": check_expr,
    "transformed_integrand": check_expr,
    "antiderivative": check_antiderivative,
}


# --- изоляция и таймауты ----------------------------------------------------

_pool: ProcessPool | None = None


def _warmup() -> None:
    """Инициализатор воркера: импорт sympy и разбор до первой задачи, чтобы не жечь таймаут."""
    check_expr("x", "x", symbolic=False)


def _get_pool() -> ProcessPool:
    global _pool
    if _pool is None or not _pool.active:
        # spawn, а не fork (дефолт Linux): fork многопоточного процесса бота может повесить воркер
        _pool = ProcessPool(max_workers=2, initializer=_warmup, context=multiprocessing.get_context("spawn"))
    return _pool


def _run(fn, args, symbolic: bool, timeout: float) -> Verdict:
    fut = _get_pool().schedule(fn, args=args, kwargs={"symbolic": symbolic}, timeout=timeout)
    return fut.result()


def check_with_timeout(kind: str, user: str, expected: str, var: str = "x",
                       domain: tuple[float, float] = (-3.0, 3.0), *,
                       symbolic_timeout: float = 1.5, numeric_timeout: float = 1.5) -> Verdict:
    """kind: expr | transformed_integrand | antiderivative.
    Символьная ветка в процессе pebble; таймаут → численная ветка; её таймаут → TIMEOUT."""
    fn = _CHECKS[kind]
    args = (user, expected, var, tuple(domain))
    try:
        return _run(fn, args, True, symbolic_timeout)
    except FutureTimeout:
        pass
    except Exception:
        return Verdict(TIMEOUT, NOTE_TIMEOUT)
    try:
        return _run(fn, args, False, numeric_timeout)
    except Exception:
        return Verdict(TIMEOUT, NOTE_TIMEOUT)


def shutdown_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.stop()
        _pool.join()
        _pool = None
