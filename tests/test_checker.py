import time

import pytest
import sympy as sp

from core.checker import (
    FACTOR, NO_CONST, OK, PARSE_ERROR, SIGN, TIMEOUT, WRONG,
    Cmp, check_antiderivative, check_choice, check_expr, check_with_timeout,
    numeric_compare, shutdown_pool,
)

x = sp.Symbol("x")


# --- choice ----------------------------------------------------------------

def test_choice():
    assert check_choice("t = tg(x/2)", ["t = tg(x/2)"]).status == OK
    assert check_choice("t = cos x", ["t = tg(x/2)"]).status == WRONG
    assert check_choice("Лопиталь", ["Тейлор", "Лопиталь"]).status == OK


# --- expr / transformed_integrand ----------------------------------------

@pytest.mark.parametrize("user, expected, var", [
    ("sin(x)^2 + cos(x)^2", "1", "x"),
    ("(x+1)^2", "x**2 + 2*x + 1", "x"),
    ("sin(2x)/2", "sin(x)*cos(x)", "x"),
    ("(1+t^2)^(-1)", "1/(1+t**2)", "t"),
    ("2/((5+3) + (5-3)t^2)", "1/(4 + t**2)", "t"),
    ("1/(x-1) - 1/(x+1)", "2/(x**2-1)", "x"),
])
@pytest.mark.parametrize("symbolic", [True, False])
def test_expr_equivalent(user, expected, var, symbolic):
    assert check_expr(user, expected, var=var, symbolic=symbolic).status == OK


@pytest.mark.parametrize("user, status", [
    ("-(x+1)^2", SIGN),
    ("2(x+1)^2", FACTOR),
    ("(x+1)^2/2", FACTOR),
    ("x^2 + 1", WRONG),
    ("(x+1)^2 + 1", WRONG),
    ("2+", PARSE_ERROR),
])
@pytest.mark.parametrize("symbolic", [True, False])
def test_expr_mistakes(user, status, symbolic):
    assert check_expr(user, "(x+1)**2", symbolic=symbolic).status == status


def test_expr_numeric_only_case():
    # simplify без assumptions real не сведёт к 0, численно — равны
    assert check_expr("log(x^2)", "2*log(Abs(x))").status == OK


def test_parse_error_has_note():
    v = check_expr("sinx", "sin(x)")
    assert v.status == PARSE_ERROR and v.note


# --- numeric_compare -------------------------------------------------------

def test_numeric_compare_basic():
    assert numeric_compare(sp.sin(x), sp.sin(x), "x", (-3, 3)) == Cmp.EQUAL
    assert numeric_compare(-sp.sin(x), sp.sin(x), "x", (-3, 3)) == Cmp.NEG
    assert numeric_compare(3 * sp.sin(x), sp.sin(x), "x", (-3, 3)) == Cmp.FACTOR
    assert numeric_compare(sp.cos(x), sp.sin(x), "x", (-3, 3)) == Cmp.DIFFERENT


def test_numeric_compare_drops_complex_points():
    # sqrt(x) комплексен при x<0; на (-1, 5) с SEED=42 положительных точек 9 из 12 — хватает
    assert numeric_compare(sp.sqrt(x), x ** sp.Rational(1, 2), "x", (-1, 5)) == Cmp.EQUAL
    # на (-3, 3) положительных точек только 5 — проверять не по чему
    assert numeric_compare(sp.sqrt(x), x ** sp.Rational(1, 2), "x", (-3, 3)) == Cmp.INSUFFICIENT
    assert numeric_compare(sp.log(x**2), 2 * sp.log(sp.Abs(x)), "x", (-3, 3)) == Cmp.EQUAL


def test_numeric_compare_insufficient():
    e = sp.sqrt(x - 10)  # комплексен на всём domain
    assert numeric_compare(e, e, "x", (-3, 3)) == Cmp.INSUFFICIENT


def test_insufficient_is_not_ok():
    assert check_expr("sqrt(x-10)", "sqrt(x - 10) + 1", symbolic=False).status != OK


def test_numeric_deterministic():
    r = [check_expr("log(x^2)", "2*log(Abs(x))", symbolic=False) for _ in range(3)]
    assert len(set(r)) == 1


# --- antiderivative --------------------------------------------------------

@pytest.mark.parametrize("user", ["atan(x) + C", "arctg x + 5 + C", "atan(x) - C", "-acot(x) + c", "pi/2 - arcctg x + const"])
@pytest.mark.parametrize("symbolic", [True, False])
def test_antiderivative_forms_and_constants(user, symbolic):
    v = check_antiderivative(user, "1/(1+x**2)", symbolic=symbolic)
    assert v.status == OK


@pytest.mark.parametrize("user, status", [
    ("-atan(x) + C", SIGN),
    ("2arctg x + C", FACTOR),
    ("atan(x)/2 + C", FACTOR),
    ("x + C", WRONG),
    ("1/(1+x^2) + C", WRONG),   # прислал подынтегральную функцию
    ("atan( + C", PARSE_ERROR),
    ("atan(x)", NO_CONST),
    ("arctg x + 5", NO_CONST),  # число — не константа интегрирования
])
@pytest.mark.parametrize("symbolic", [True, False])
def test_antiderivative_mistakes(user, status, symbolic):
    assert check_antiderivative(user, "1/(1+x**2)", symbolic=symbolic).status == status


def test_no_const_but_wrong_math():
    # без C и с ошибкой: вердикт по математике, про C — в note
    v = check_antiderivative("-atan(x)", "1/(1+x**2)")
    assert v.status == SIGN
    assert v.note and "C" in v.note


def test_expr_does_not_accept_c():
    # в промежуточных выражениях константы нет
    assert check_expr("(x+1)^2 + C", "(x+1)**2").status == PARSE_ERROR

def test_lost_modulus_ok_with_note():
    good = check_antiderivative("ln|x| + C", "1/x")
    lost = check_antiderivative("ln x + C", "1/x")
    assert good.status == OK and good.note is None
    assert lost.status == OK and lost.note  # замечание про модуль, без штрафа


def test_log_of_positive_arg_no_note():
    v = check_antiderivative("ln(x^2+1) + C", "2*x/(x**2+1)")
    assert v.status == OK and v.note is None


@pytest.mark.parametrize("user, integrand, status", [
    ("(x-1)e^x + C", "x*exp(x)", OK),
    ("(x+1)e^x + C", "x*exp(x)", WRONG),
    ("-cos x + cos^3 x/3 + C", "sin(x)**3", OK),
    ("1/2 arctg(tg(x/2)/2) + C", "1/(5 + 3*cos(x))", OK),        # шаблон 14, a=5, b=3
    ("x arctg x - 1/2 ln(1+x^2) + C", "atan(x)", OK),
    ("x arctg x + 1/2 ln(1+x^2) + C", "atan(x)", WRONG),
])
def test_antiderivative_typical(user, integrand, status):
    assert check_antiderivative(user, integrand).status == status


# --- таймауты (pebble) ----------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _pool():
    yield
    shutdown_pool()


def test_with_timeout_normal():
    t0 = time.monotonic()
    v = check_with_timeout("antiderivative", "arctg x + C", "1/(1+x**2)")
    assert v.status == OK
    assert time.monotonic() - t0 < 3.0


def test_symbolic_timeout_falls_back_to_numeric():
    # simplify думает ~2 с (> 1.5), численно — 0.01 с
    t0 = time.monotonic()
    v = check_with_timeout("expr", "(sin x + cos x)^12/(1+sin(2x))^6 + ch(x)^8 - sh(x)^8",
                           "1 + (cosh(x)**2+sinh(x)**2)*(cosh(x)**4+sinh(x)**4)")
    assert v.status == OK
    assert time.monotonic() - t0 < 3.0


def test_total_timeout_and_recovery():
    # обе ветки медленные (~4.5 с и ~1 с), таймауты урезаны до 0.3 с
    t0 = time.monotonic()
    v = check_with_timeout("expr", "(x+1)^1500", "expand((x+1)**1500)",
                           symbolic_timeout=0.3, numeric_timeout=0.3)
    assert v.status == TIMEOUT
    assert time.monotonic() - t0 < 3.5
    # зависший воркер убит, пул живой
    assert check_with_timeout("expr", "(x+1)^2", "x**2+2*x+1").status == OK


def test_with_timeout_transformed_integrand():
    assert check_with_timeout("transformed_integrand", "2/(8+2t^2)", "1/(4+t**2)", var="t").status == OK


def test_pool_uses_spawn():
    # fork многопоточного процесса (Linux по умолчанию) может повесить воркер — только spawn
    from core.checker import _get_pool
    wm = _get_pool()._pool_manager.worker_manager          # внутренности pebble 5.x
    ctx = next(v for v in vars(wm).values() if hasattr(v, "get_start_method"))
    assert ctx.get_start_method() == "spawn"


# --- регрессия: simplify(F' − f) = Piecewise(zoo при x=0, 0) → отношение 1 — это верно, а не «множитель» ---

@pytest.mark.parametrize("user, integrand", [
    ("2*ln|x| - 2/x + 5/(2*x^2) + C", "2/x + 2/x**2 - 5/x**3"),
    ("3 ln|x - 1| - 2 ln|x + 2| + C", "3/(x - 1) - 2/(x + 2)"),
])
def test_abs_log_sum_is_ok_symbolically(user, integrand):
    assert check_antiderivative(user, integrand, domain=(0.3, 3.0), symbolic=True).status == OK
