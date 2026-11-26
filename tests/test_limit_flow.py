"""Задача на предел в сессии: шаги, попытки, диагнозы, подсказки."""
import time

import pytest

from bot.flow import MAX_ATTEMPTS, NOTE_METHOD_ALT, TrainSession
from bot.render import PNG_MAGIC, render_latex
from core.checker import OK, TIMEOUT, WRONG, check_expr, check_value, check_with_timeout, shutdown_pool
from core.generator import build
from core.hints import answer_hints
from core.limits import NO_TYPE, TYPES
from core.templates import parse_template
from tests.test_limits import RAW, TPL, ZERO, I, inst, raw


def check(kind, user, expected, var, domain, **kw):
    if kind == "value":
        return check_value(user, expected, var, domain, **kw)
    return check_expr(user, expected, var, domain, symbolic=False)


def session(num="1354", i=None, **kw):
    i = i or inst(num)
    return i, TrainSession(i, seed=1, check=check, **kw)


def cp(s, i):
    return i.checkpoints[s.current().idx]


def right(s, i):
    p, c = s.current(), cp(s, i)
    want = c.correct[0] if c.type == "choice" else c.labels[c.expected]
    return p.options.index(want)


def to_value(s, i):
    while s.current().kind == "buttons":
        assert s.answer_button(right(s, i)).verdict.status == OK


def option(s, i, src):
    return s.current().options.index(cp(s, i).labels[src])


# --- прохождение -----------------------------------------------------------------------

@pytest.mark.parametrize("num", sorted(RAW))
def test_full_run(num):
    i, s = session(num)
    to_value(s, i)
    assert s.current().kind == "input" and s.current().cp_type == "value"
    out = s.answer_text(i.checkpoints[-1].expected)
    assert out.verdict.status == OK and s.finished
    sm = s.summary()
    assert (sm.total, sm.first_try, sm.status) == (len(i.checkpoints), len(i.checkpoints), "solved")


def test_type_options_keep_order():
    i, s = session("1318")
    assert s.current().options == (*TYPES, NO_TYPE)                # не перемешиваем: список один на все задачи


def test_fraction_options_are_labels_and_shuffled():
    i, s = session("1341")
    s.answer_button(right(s, i))
    p = s.current()
    assert p.formulas and set(p.options) == set(cp(s, i).labels.values())
    orders = {tuple(TrainSession(i, seed=k, check=check)._buttons[1][0]) for k in range(8)}
    assert len(orders) > 1


def test_current_fraction_is_shown_when_asking_type_again():
    i, s = session("1354")
    assert s.current().context is None                             # условие и так на экране
    s.answer_button(right(s, i))
    assert s.current().cp_type == "expr"
    red = cp(s, i).labels[cp(s, i).expected]
    s.answer_button(right(s, i))
    assert cp(s, i).role == "type" and s.current().context == red  # «какой тип теперь?» — у этой дроби
    s.answer_button(right(s, i))
    assert s.current().context == red                              # и правило Лопиталя применяем к ней же


def test_value_step_shows_last_fraction():
    i, s = session("1318")
    to_value(s, i)
    lh = [c for c in i.checkpoints if c.role == "lhopital"][-1]
    assert s.current().context == lh.labels[lh.expected]


# --- приведение -----------------------------------------------------------------------------

def reduce_step(num="1341", i=None):
    i, s = session(num, i)
    s.answer_button(right(s, i))
    return i, s


def test_dead_end_is_wrong_with_explanation():
    i, s = reduce_step()
    out = s.answer_button(option(s, i, cp(s, i).dead[0]))
    assert out.verdict.status == WRONG and "неопределённость" in out.verdict.note and out.spent


def test_longer_way_is_accepted_with_note():
    r = dict(RAW["1341"], id="lim_xctg", product=["x", "cot(a*x)"], reduce=["x", "tan(a*x)"], limit=ZERO,
             reduce_other=[["cot(a*x)", "1/x"]], params={"a": {"int": [1, 4]}},
             examples=[{"number": 1, "params": {"a": 1}, "answer": "1"}])
    i, s = reduce_step(i=build(parse_template(r), {"a": 2}))
    alt = cp(s, i).alternatives[0]
    out = s.answer_button(option(s, i, alt))
    assert out.verdict.status == OK and out.verdict.note == NOTE_METHOD_ALT


def test_hint_does_not_hide_longer_way():
    r = dict(RAW["1341"], id="lim_xctg", product=["x", "cot(a*x)"], reduce=["x", "tan(a*x)"], limit=ZERO,
             reduce_other=[["cot(a*x)", "1/x"]], params={"a": {"int": [1, 4]}},
             examples=[{"number": 1, "params": {"a": 1}, "answer": "1"}])
    i, s = reduce_step(i=build(parse_template(r), {"a": 2}))
    alt = option(s, i, cp(s, i).alternatives[0])
    while s.take_hint():
        pass
    assert alt not in s.current().hidden


# --- значение ---------------------------------------------------------------------------------

def test_steps_value_has_three_attempts():
    i, s = session("1318")
    to_value(s, i)
    for k in range(MAX_ATTEMPTS):
        out = s.answer_text("7")
    assert out.forced_reveal and s.finished


def test_direct_value_has_two_attempts():
    i, s = session("1318", direct=True)
    assert not s.answer_text("7").forced_reveal
    out = s.answer_text("8")
    assert out.forced_reveal and s.finished and "frac{3}{2}" in out.answer
    assert "C" not in out.answer


def test_integrals_still_have_three_in_direct():
    from tests.test_flow import session as int_session
    _, s = int_session(direct=True)
    assert [s.answer_text("x + C").forced_reveal for _ in range(3)] == [False, False, True]


def test_approximation_costs_nothing():
    i = build(TPL["1346"], {"a": 1})
    _, s = session(i=i, direct=True)
    out = s.answer_text("0.3679")
    assert out.verdict.status == "approx" and not out.spent and out.verdict.note
    assert s.answer_text("1/e").verdict.status == OK


def test_traps_reach_student():
    i = build(TPL["1346"], {"a": 2})
    _, s = session(i=i, direct=True)
    assert "ln y" in s.answer_text("-2").verdict.note


@pytest.mark.parametrize("raw_in, ok", [("∞", True), ("-∞", True), ("1/2", True), ("e^(-1/6)", True), ("x+1", False), ("1/2 + C", False)])
def test_preview_of_value(raw_in, ok):
    _, s = session("1318", direct=True)
    p = s.preview(raw_in)
    assert p.ok == ok and (not ok or "C" not in p.latex)
    if ok:
        assert render_latex(p.latex).startswith(PNG_MAGIC)


def test_switch_from_direct_to_steps():
    i, s = session("1354", direct=True)
    s.answer_text("7")
    s.switch_to_steps()
    to_value(s, i)
    assert not s.answer_text("7").forced_reveal                    # одна попытка уже была, в шагах их три
    assert s.answer_text("8").forced_reveal


# --- без шагов ------------------------------------------------------------------------------------

def no_steps():
    return build(parse_template(raw("lim_1333", 1333, ZERO, {"a": I(1, 3, 1)}, "1/6",
                                    ratio=["cos(sin(a*x)) - cos(a*x)", "x**4"])), {"a": 1})


def test_no_steps_is_direct_with_two_attempts():
    _, s = session(i=no_steps(), direct=True)
    s.answer_text("7")
    with pytest.raises(ValueError):
        s.switch_to_steps()
    assert s.answer_text("8").forced_reveal


# --- уровень C: ввод ---------------------------------------------------------------------------------

def test_level_c_types_derivative_but_not_reduction():
    """Приведение вводом не спрашиваем: исходное выражение ему равно и засчиталось бы."""
    i, s = session("1354", free_input=True)
    kinds = {}
    while cp(s, i).role != "value":
        c = cp(s, i)
        kinds[c.role] = s.current().kind
        out = s.answer_button(right(s, i)) if s.current().kind == "buttons" else s.answer_text(c.expected)
        assert out.verdict.status == OK
    assert kinds == {"type": "buttons", "reduce": "buttons", "lhopital": "input"}


# --- подсказки ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("num", sorted(RAW))
@pytest.mark.parametrize("direct", [False, True])
def test_hints_exist_and_do_not_leak(num, direct):
    i = inst(num)
    t0 = time.monotonic()
    hs = answer_hints(i, direct)
    assert time.monotonic() - t0 < 0.2                             # всё посчитано при генерации
    assert hs and [h.level for h in hs] == list(range(1, len(hs) + 1)) and hs[-1].last
    from core.latex import ru_latex
    ans = ru_latex(i.checkpoints[-1].expected, "x")
    for h in hs:
        assert h.latex != ans
        if h.latex:
            assert render_latex(h.latex).startswith(PNG_MAGIC)


def test_direct_hints_go_type_reduction_last_fraction():
    hs = answer_hints(inst("1354"), direct=True)
    assert "∞−∞" in hs[0].text
    red = next(c for c in inst("1354").checkpoints if c.role == "reduce")
    assert hs[1].latex == red.labels[red.expected]
    lh = [c for c in inst("1354").checkpoints if c.role == "lhopital"][-1]
    assert hs[-1].latex == lh.labels[lh.expected]


def test_power_hint_reminds_about_exponent():
    assert any("ln y" in h.text for h in answer_hints(inst("1346"), direct=False))


def test_no_steps_still_has_hint():
    hs = answer_hints(no_steps(), direct=True)
    assert hs and "0/0" in hs[0].text


def test_steps_have_template_hints():
    i, s = session("1354")
    while s.current().kind == "buttons":
        h = s.take_hint()
        assert h and h.level == 1
        s.answer_button(right(s, i))


# --- проверка в отдельном процессе --------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _pool():
    yield
    shutdown_pool()


def test_value_in_pool():
    assert check_with_timeout("value", "1/2", "1/2").status == OK
    v = check_with_timeout("value", "-2", "exp(-2)", traps={"-2": "это ln y"})
    assert (v.status, v.note) == (WRONG, "это ln y")


def test_huge_number_does_not_hang():
    t0 = time.monotonic()
    assert check_with_timeout("value", "9^9^9^9", "1/2").status in (TIMEOUT, WRONG, "parse_error")
    assert time.monotonic() - t0 < 8
