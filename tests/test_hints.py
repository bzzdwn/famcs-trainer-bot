"""Лестница подсказок: что делать дальше → результат следующего шага → первообразная по t."""
import random
import time
from pathlib import Path

import pytest
import sympy as sp

from bot.render import PNG_MAGIC, render_latex
from core.checker import Cmp, check_antiderivative, numeric_compare
from core.generator import build, generate, sample_params
from core.hints import Hint, answer_hints, antiderivative_in, partial_form, partial_fractions
from core.parser import parse_trusted
from core.templates import load_templates

BANK = load_templates(Path(__file__).resolve().parent.parent / "data" / "templates")
IDS = sorted(BANK)
t = sp.Symbol("t")


def final(inst):
    return next(c for c in inst.checkpoints if c.type == "antiderivative")


def flat(latex):
    return latex.replace(" ", "").replace("\\left", "").replace("\\right", "")


# --- разложение на простые дроби ---------------------------------------------------

@pytest.mark.parametrize("expr, parts", [
    ("6/(t*(t**3 + t**2 + t + 1))", [r"\frac{A}{t}", r"\frac{B}{t+1}", r"\frac{Ct+D}{t^{2}+1}"]),   # случай со скриншота
    ("2*(t**2 + 1)/(t*(t + 1)**2)", [r"\frac{A}{t}", r"\frac{B}{t+1}", r"\frac{C}{(t+1)^{2}}"]),      # кратный корень
    ("1/((t + 1)*(t + 3))", [r"\frac{A}{t+1}", r"\frac{B}{t+3}"]),
])
def test_partial_form(expr, parts):
    f = flat(partial_form(expr, "t"))
    for p in parts:
        assert p in f, f
    assert f.count(r"\frac") == len(parts)


def test_partial_form_improper_mentions_polynomial():
    f = partial_form("6*t**3/(t + 1)", "t")
    assert "многочлен" in f and r"\frac{A}{t+1}" in flat(f)


@pytest.mark.parametrize("expr", ["t**2 + 1", "2/(t**2 + 9)", "exp(t)", "sqrt(t)/(t + 1)", "log(t + 1)", "1/t**2"])
def test_nothing_to_decompose(expr):
    assert partial_form(expr, "t") is None and partial_fractions(expr, "t") is None


def test_partial_fractions_screenshot_case():
    r = parse_trusted(partial_fractions("6/(t*(t**3 + t**2 + t + 1))", "t"), "t")
    want = 6/t - 3/(t + 1) - 3*(t + 1)/(t**2 + 1)
    assert sp.simplify(r - want) == 0 and len(sp.Add.make_args(r)) == 3


# --- первообразная по переменной замены ----------------------------------------------

@pytest.mark.parametrize("expr", ["6/(t*(t**3 + t**2 + t + 1))", "exp(3*t)", "-t*cos(t)", "t**(-3/2)", "2*sin(t)**2", "log(t + 1)"])
def test_antiderivative_in(expr):
    F = parse_trusted(antiderivative_in(expr, "t"), "t")
    assert check_antiderivative(str(F) + " + C", expr, "t", (0.2, 3.0), symbolic=False).status == "ok"


def test_antiderivative_logs_have_modulus():
    assert "Abs" in antiderivative_in("1/(t - 1)", "t")


def test_antiderivative_not_elementary_is_none():
    assert antiderivative_in("exp(t**2)", "t") is None and antiderivative_in("exp(t)/t", "t") is None


# --- лестница целиком -------------------------------------------------------------------

def test_screenshot_ladder():
    inst = build(BANK["int_exp_lcm_01"], {"m": 6})
    hs = answer_hints(inst)
    assert [h.level for h in hs] == [1, 2, 3] and [h.last for h in hs] == [False, False, True]
    assert "прост" in hs[0].text.lower() and r"\frac{A}{t}" in flat(hs[0].latex)     # что делать
    assert "A" not in hs[1].latex                                                    # готовое разложение — с числами
    assert r"\ln" in hs[2].latex and "верн" in hs[2].text.lower()                    # осталось вернуться к x


def test_direct_ladder_starts_with_method_and_shows_substituted_integral():
    inst = build(BANK["int_exp_lcm_01"], {"m": 6})
    hs = answer_hints(inst, direct=True)
    assert hs[0].text == inst.checkpoints[0].hint                                    # про метод, как сейчас
    assert len(hs) == len(answer_hints(inst)) + 2                                    # + метод + интеграл после замены
    assert "t" in hs[1].latex


@pytest.mark.parametrize("tid", IDS)
def test_every_template_has_ladder(tid):
    """У всех шаблонов на финале есть хотя бы одна подсказка; уровни по порядку; формулы рисуются; быстро."""
    seen = set()
    for seed in range(8):
        params = sample_params(BANK[tid], random.Random(seed))
        key = tuple(sorted(params.items()))
        if key in seen:
            continue
        seen.add(key)
        inst = build(BANK[tid], params)
        for direct in (False, True):
            t0 = time.monotonic()
            hs = answer_hints(inst, direct)
            assert time.monotonic() - t0 < 1.5, (tid, params)
            assert hs and all(isinstance(h, Hint) for h in hs), (tid, params)
            assert [h.level for h in hs] == list(range(1, len(hs) + 1))
            assert [h.last for h in hs] == [False] * (len(hs) - 1) + [True]
            for h in hs:
                assert h.text.strip()
                if h.latex:
                    assert render_latex(h.latex).startswith(PNG_MAGIC)


@pytest.mark.parametrize("tid", IDS)
def test_hints_do_not_give_the_answer(tid):
    """Ни один уровень не содержит готовый ответ в x — его показывает только «Показать ответ»."""
    from core.latex import ru_latex
    inst = generate(BANK[tid], 0)
    cp = final(inst)
    answer = flat(ru_latex(cp.reference, cp.var))
    for direct in (False, True):
        for h in answer_hints(inst, direct):
            assert h.latex is None or answer not in flat(h.latex), (tid, h)


def test_no_substitution_shows_one_term_only():
    inst = build(BANK["mix_parts_atan_01"], {"a": 2, "b": 3})                        # expr-шаблон, в ответе 3 слагаемых
    hs = answer_hints(inst)
    assert len(hs) == 2
    assert "arctg" in hs[0].text and "ln" in hs[0].text and hs[0].latex is None      # из чего состоит ответ
    F = parse_trusted(final(inst).reference, "x")
    assert len(sp.Add.make_args(F)) >= 2 and hs[1].latex                             # одно слагаемое


def test_single_term_answer_skips_term_level():
    inst = build(BANK["mix_exp_trig_01"], {"a": 1})                                  # ответ eˣ·tg(x/2): слагаемое = весь ответ
    assert len(answer_hints(inst)) == 1


def test_manual_hint_has_priority():
    tid = next(i for i in IDS if any(c.type == "antiderivative" and c.hint for c in BANK[i].checkpoints))
    inst = generate(BANK[tid], 0)
    assert answer_hints(inst)[0].text == final(inst).hint


def test_screenshot_like_templates_have_all_three_levels():
    """11 шаблонов из замера: есть замена и есть что раскладывать."""
    for tid in ("int_exp_lcm_01", "int_exp_lcm_02", "int_root_lcm_01", "int_trig_root_sin_01", "mix_binom_01",
                "mix_trig_root_01"):
        assert len(answer_hints(generate(BANK[tid], 0))) == 3, tid


@pytest.mark.parametrize("tid", IDS)
def test_decomposition_is_correct(tid):
    for seed in range(5):
        inst = build(BANK[tid], sample_params(BANK[tid], random.Random(seed)))
        for c in inst.checkpoints:
            if c.type == "transformed_integrand" and (r := partial_fractions(c.expected, c.var)):
                assert numeric_compare(parse_trusted(r, c.var), parse_trusted(c.expected, c.var), c.var, (0.21, 2.9)) is Cmp.EQUAL


# --- смысл подсказок (найдено чтением всех уровней банка глазами) ---------------------

def first(tid, params):
    return answer_hints(build(BANK[tid], params))[0].text


def test_components_ignore_constants():
    s = first("int_trig_aux_angle_01", {"a": 1, "b": 2})                 # arctg 2 — число, а не функция от x
    assert "arctg" not in s and "ln" in s
    s = first("mix_exp_diff_01", {"c": 2})                               # sh 2, ch 2, cth 2 — числа
    assert not any(n in s for n in ("sh", "ch", "cth")) and "ln" in s


def test_single_trig_function_is_table_integral():
    assert "табличн" in first("int_trig_subst_01", {"a": 2}).lower()


def test_root_over_polynomial_is_not_complete_square():
    s = first("mix_hyp_root_01", {"c": 1})
    assert "полный квадрат" not in s and "домнож" in s.lower()


def test_atan_over_fraction_says_split_first():
    s = first("mix_exp_atan_01", {"m": 2})
    assert "1/t²" in s and "по частям" in s


def test_power_of_trig_times_t_says_reduce_first():
    s = first("mix_parts_asin_02", {"c": 1}).lower()
    assert "пониз" in s and "по частям" in s


def test_antiderivative_is_simplified():
    F = parse_trusted(antiderivative_in("t*cos(t)**2", "t"), "t")
    assert not (F.has(sp.sin(t)**2) and F.has(sp.cos(t)**2))


def test_product_of_powers_says_expand():
    assert "скобки" in first("int_trig_odd_sin_01", {"a": 1, "n": 4})
