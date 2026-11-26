"""Пределы: математика цепочки, самопроверка, шаблоны limit → точки, проверка значения."""
import copy

import pytest
import sympy as sp

from bot.render import PNG_MAGIC, render_latex
from core.checker import OK, PARSE_ERROR, SIGN, WRONG, check_value
from core.generator import GenerationError, build, check_example, generate, self_check
from core.limits import (MAX_DIFFS, NO_TYPE, TYPES, LimitError, lhopital_chain, plain_value, ratio_type, verify_limit)
from core.templates import TemplateError, parse_template
from tests.lim_book import BOOK

x = sp.Symbol("x", real=True)
S = lambda s: sp.sympify(s, locals={"x": x})


# --- прямая подстановка -------------------------------------------------------------

@pytest.mark.parametrize("f, to, side, want", [
    ("cos(x)/2", "0", "both", "1/2"), ("exp(-x)", "oo", "-", "0"), ("log(x)", "0", "+", "-oo"),
    ("1/x", "0", "+", "oo"), ("x**2*exp(x)", "oo", "-", "oo"), ("3", "0", "both", "3"),
])
def test_plain_value(f, to, side, want):
    assert plain_value(S(f), x, S(to), side) == S(want)


@pytest.mark.parametrize("f, to, side", [
    ("sin(x)/x", "0", "both"), ("x*log(x)", "0", "+"), ("1/sin(x)**2 - 1/sinh(x)**2", "0", "both"),   # № 1334 после f′
    ("log(sin(x)/x)", "0", "both"), ("(1 + x)**(1/x)", "0", "both"), ("x**x", "0", "+"), ("x - exp(x)", "oo", "-"),
])
def test_hidden_indeterminacy_is_not_plain(f, to, side):
    """sympy такой предел находит сам, но студент при подстановке видит неопределённость."""
    assert plain_value(S(f), x, S(to), side) is None


@pytest.mark.parametrize("n, d, to, side, want", [
    ("sin(3*x)", "sin(2*x)", "0", "both", "0/0"), ("log(x)", "1/x", "0", "+", "∞/∞"),
    ("cos(x)", "x + 1", "0", "both", NO_TYPE), ("log(sin(x)/x)", "x**2", "0", "both", None),
])
def test_ratio_type(n, d, to, side, want):
    assert ratio_type(S(n), S(d), x, S(to), side) == want


# --- цепочка --------------------------------------------------------------------------

@pytest.mark.parametrize("n, d, to, side, diffs, value", [
    ("sin(3*x)", "sin(2*x)", "0", "both", 1, "3/2"),
    ("cosh(x) - cos(x)", "x**2", "0", "both", 2, "1"),
    ("tan(x) - x", "x - sin(x)", "0", "both", 2, "2"),          # через sin/cos короче, чем «в лоб» (3)
    ("3**x - 3**sin(x)", "x**3", "0", "both", 3, "log(3)/6"),
    ("log(x)", "1/x", "0", "+", 1, "0"),                       # без сокращения зацикливается
    ("x**3", "exp(2*x)", "oo", "-", 3, "0"),
    ("exp(x) - 1 - x", "x*(exp(x) - 1)", "0", "both", 2, "1/2"),
])
def test_chain(n, d, to, side, diffs, value):
    links = lhopital_chain(S(n), S(d), x, S(to), side)
    assert [k.kind for k in links[:-1]] == [k.kind for k in links[:-1] if k.kind in TYPES[:2]]
    assert len(links) == diffs + 1 and links[-1].kind == NO_TYPE
    assert plain_value(links[-1].num / links[-1].den, x, S(to), side) == S(value)


@pytest.mark.parametrize("n, d, to, side", [
    ("x", "1/log(x)", "0", "+"),                                # тупиковое приведение x·ln x
    ("x", "sqrt(1 + x**2)", "oo", "-"),                         # Лопиталь ходит по кругу
    ("exp(-1/x**2)", "x**100", "0", "+"),                       # № 1338: нужна замена
])
def test_chain_does_not_converge(n, d, to, side):
    assert lhopital_chain(S(n), S(d), x, S(to), side) is None


def test_chain_every_link_has_same_limit():
    links = lhopital_chain(S("tan(x) - x"), S("x - sin(x)"), x, S("0"), "both")
    assert {sp.limit(k.num / k.den, x, 0) for k in links} == {2}


# --- самопроверка значения -----------------------------------------------------------------

@pytest.mark.parametrize("num, f, to, side, ans", BOOK, ids=[b[0] for b in BOOK])
def test_book_answers_confirmed(num, f, to, side, ans):
    """Все 58 номеров: ответ книги подтверждается и sympy, и численно (1331, 1345 сходятся медленно; 1370 sympy врёт)."""
    v = S(ans)
    assert sp.simplify(verify_limit(S(f), x, S(to), side, value=v) - v) == 0


SOME = [b for b in BOOK if b[0] in ("1318", "1331", "1345", "1354", "1363", "1370")]


@pytest.mark.parametrize("num, f, to, side, ans", SOME, ids=[b[0] for b in SOME])
def test_wrong_answer_is_caught(num, f, to, side, ans):
    with pytest.raises(LimitError):
        verify_limit(S(f), x, S(to), side, value=S(ans) + sp.Rational(1, 7))


def test_sympy_mistake_is_caught_without_author_value():
    num, f, to, side, _ = next(b for b in BOOK if b[0] == "1370")
    with pytest.raises(LimitError):
        verify_limit(S(f), x, S(to), side)                        # sympy говорит oo, численно 3 — задачу не выдаём


@pytest.mark.parametrize("f", ["1/x", "Abs(x)/x", "exp(1/x)", "atan(1/x)"])
def test_different_one_sided_limits_rejected(f):
    with pytest.raises(LimitError):
        verify_limit(S(f), x, S("0"), "both")


def test_no_limit_rejected():
    with pytest.raises(LimitError):
        verify_limit(S("sin(1/x)"), x, S("0"), "both")


def test_infinite_limit_ok():
    assert verify_limit(S("1/x**2"), x, S("0"), "both") == sp.oo


# --- шаблоны: первые шесть видов ------------------------------------------------------------------

def raw(tid, number, limit, params, answer, **kind):
    p = {k: v for k, (v, _) in params.items()}
    return {"id": tid, "topic": "limits.lhopital", "source": f"Демидович, № {number}", "difficulty": "A", "var": "x",
            "params": p, "limit": limit, "blitz_method": "Лопиталь",
            "examples": [{"number": number, "params": {k: e for k, (_, e) in params.items()}, "answer": answer}], **kind}


I = lambda lo, hi, ex: ({"int": [lo, hi]}, ex)
ZERO, PLUS = {"to": "0", "side": "both"}, {"to": "0", "side": "+"}
RAW = {
    "1318": raw("lim_sin", 1318, ZERO, {"a": I(1, 5, 3), "b": I(1, 5, 2)}, "3/2", ratio=["sin(a*x)", "sin(b*x)"],
                constraints=["a != b"]),
    "1319": raw("lim_ch", 1319, ZERO, {"a": I(1, 4, 1)}, "1", ratio=["cosh(a*x) - cos(a*x)", "x**2"]),
    "1341": raw("lim_xln", 1341, PLUS, {"p": I(1, 4, 1)}, "0", product=["x**p", "log(x)"], reduce=["log(x)", "x**(-p)"],
                reduce_other=[["x**p", "1/log(x)"]]),
    "1342": raw("lim_xx", 1342, PLUS, {"a": I(1, 4, 1)}, "1", power=["x", "a*x"], reduce=["log(x)", "1/(a*x)"]),
    "1346": raw("lim_1inf", 1346, {"to": "1", "side": "both"}, {"a": I(1, 4, 1)}, "exp(-1)", power=["x", "a/(1 - x)"],
                reduce=["a*log(x)", "1 - x"]),
    "1354": raw("lim_diff", 1354, ZERO, {"a": I(1, 4, 1)}, "1/2", diff=["1/x", "a/(exp(a*x) - 1)"],
                reduce=["exp(a*x) - 1 - a*x", "x*(exp(a*x) - 1)"]),
}
TPL = {k: parse_template(copy.deepcopy(v)) for k, v in RAW.items()}


def inst(num, **params):
    t = TPL[num]
    return build(t, params or t.examples[0].params)


@pytest.mark.parametrize("num, roles", [
    ("1318", "type lhopital type value"),
    ("1319", "type lhopital type lhopital type value"),
    ("1341", "type reduce type lhopital type value"),
    ("1342", "type reduce type lhopital type value"),
    ("1346", "type reduce type lhopital type value"),
    ("1354", "type reduce type lhopital type lhopital type value"),
])
def test_steps_are_built(num, roles):
    i = inst(num)
    assert [c.role for c in i.checkpoints] == roles.split() and i.steps


@pytest.mark.parametrize("num, kinds", [
    ("1318", ["0/0", NO_TYPE]), ("1319", ["0/0", "0/0", NO_TYPE]), ("1341", ["0·∞", "∞/∞", NO_TYPE]),
    ("1342", ["0⁰", "∞/∞", NO_TYPE]), ("1346", ["1^∞", "0/0", NO_TYPE]), ("1354", ["∞−∞", "0/0", "0/0", NO_TYPE]),
])
def test_types_along_the_chain(num, kinds):
    cps = [c for c in inst(num).checkpoints if c.role == "type"]
    assert [c.correct for c in cps] == [(k,) for k in kinds]
    for c in cps:
        assert c.options == (*TYPES, NO_TYPE) and c.hint         # все восемь, в одном порядке


@pytest.mark.parametrize("num", sorted(RAW))
def test_book_example(num):
    check_example(TPL[num], TPL[num].examples[0])


def test_wrong_book_answer_in_example_is_caught():
    r = copy.deepcopy(RAW["1318"])
    r["examples"][0]["answer"] = "2/3"
    t = parse_template(r)
    with pytest.raises(GenerationError):
        check_example(t, t.examples[0])


def test_wrong_reduction_is_caught():
    r = copy.deepcopy(RAW["1354"])
    r["reduce"] = ["exp(a*x) - 1 + a*x", "x*(exp(a*x) - 1)"]      # опечатка автора: другое выражение
    with pytest.raises(GenerationError):
        self_check(parse_template(r), {"a": 1})


def test_power_reduction_is_ln():
    """Степень: приведение — это ln y, и значение возвращаем через e."""
    i = inst("1346", a=2)
    v = i.checkpoints[-1]
    assert sp.sympify(v.expected) == sp.exp(-2)
    assert v.traps and any(sp.sympify(k) == -2 for k in v.traps)   # забыл вернуться от ln y


@pytest.mark.parametrize("num", sorted(RAW))
def test_fifty_seeds(num):
    seen = set()
    for seed in range(50):
        i = generate(TPL[num], seed)
        seen.add(tuple(sorted(i.params.items())))
        assert i.checkpoints[-1].role == "value"
        assert i.statement_latex.startswith(r"\lim") and render_latex(i.statement_latex).startswith(PNG_MAGIC)
    assert len(seen) > 1


@pytest.mark.parametrize("num", sorted(RAW))
def test_lhopital_options(num):
    """f′/g′: верный вариант равен по смыслу, три неверных различимы, среди них — производная частного."""
    for c in inst(num).checkpoints:
        if c.role in ("lhopital", "reduce"):
            assert len(c.distractors) == 3 and len({*c.distractors, c.expected}) == 4


@pytest.mark.parametrize("num", sorted(RAW))
def test_options_are_shown_as_fractions(num):
    """sympy сворачивает (ln x)/(1/x) в x·ln x — студент должен видеть дробь; подписи всех вариантов различны."""
    for c in inst(num).checkpoints:
        if c.role in ("lhopital", "reduce"):
            labels = [c.labels[o] for o in (c.expected, *c.distractors)]
            assert len(set(labels)) == 4
            for lab in labels:
                assert render_latex(lab).startswith(PNG_MAGIC)


def test_reduction_label_keeps_fraction():
    c = next(c for c in inst("1341").checkpoints if c.role == "reduce")
    main, dead = c.labels[c.expected], c.labels[c.dead[0]]
    assert main.startswith(r"\frac{\ln") and dead.startswith(r"\frac{x}") and r"\ln" in dead


def test_wrong_options_really_differ():
    """Неверные варианты (кроме тупиковых и длинных) не равны верному по значению."""
    for num in RAW:
        for c in inst(num).checkpoints:
            if c.role in ("lhopital", "reduce"):
                for o in set(c.distractors) - {*c.dead, *c.alternatives}:
                    d = S(o) - S(c.expected)
                    assert any(abs(sp.N(d.subs(x, p))) > 1e-9 for p in (0.11, 0.37, 0.53)), (num, o)


def test_card_width_estimate():
    """Оценка ширины без рендера: ±30 % от настоящей на формулах разной длины."""
    import io

    from PIL import Image

    from bot.render import render_options
    from core.limits import card_px
    for num in RAW:
        for c in inst(num).checkpoints:
            for lab in (c.labels or {}).values():
                real = Image.open(io.BytesIO(render_options([lab]))).width
                assert 0.7 < card_px(lab) / real < 1.45, (lab, card_px(lab), real)


def test_quotient_rule_is_among_distractors():
    c = next(c for c in inst("1318").checkpoints if c.role == "lhopital")
    n, d = S("sin(3*x)"), S("sin(2*x)")
    quot = sp.diff(n / d, x)
    assert any(sp.simplify(S(o) - quot) == 0 for o in c.distractors)


def test_dead_end_reduction_is_marked():
    c = next(c for c in inst("1341").checkpoints if c.role == "reduce")
    assert len(c.dead) == 1 and c.alternatives == ()              # x/(1/ln x) верно алгебраически, но не сойдётся
    assert c.dead[0] in c.distractors


def test_longer_reduction_is_alternative():
    r = copy.deepcopy(RAW["1341"])
    r.update(id="lim_xctg", product=["x", "cot(a*x)"], reduce=["x", "tan(a*x)"], reduce_other=[["cot(a*x)", "1/x"]],
             params={"a": {"int": [1, 4]}}, examples=[{"number": 1323, "params": {"a": 1}, "answer": "1"}], limit=ZERO)
    c = next(c for c in build(parse_template(r), {"a": 2}).checkpoints if c.role == "reduce")
    assert len(c.alternatives) == 1 and c.dead == ()


def test_too_many_diffs_is_direct_only():
    r = raw("lim_1333", 1333, ZERO, {"a": I(1, 3, 1)}, "1/6", ratio=["cos(sin(a*x)) - cos(a*x)", "x**4"])
    i = build(parse_template(r), {"a": 1})
    assert MAX_DIFFS == 3 and not i.steps and [c.role for c in i.checkpoints] == ["value"]


def test_wide_card_is_direct_only():
    r = raw("lim_1335", 1335, ZERO, {"a": I(1, 3, 1)}, "1",
            ratio=["a*x - log(sin(a*x) + sqrt(1 + sin(a*x)**2))", "sinh(a*x) - sin(a*x)"])
    assert not build(parse_template(r), {"a": 1}).steps


def test_author_can_turn_steps_off_and_give_value():
    r = raw("lim_1370", 1370, {"to": "oo", "side": "-"}, {"a": I(1, 5, 3)}, "3",
            diff=["(x + a)**(1 + 1/x)", "x**(1 + 1/(x + a))"], steps=False, value="a")
    i = build(parse_template(r), {"a": 3})
    assert not i.steps and sp.sympify(i.checkpoints[-1].expected) == 3


def test_not_converging_chain_needs_explicit_steps_off():
    r = raw("lim_loop", 1, {"to": "oo", "side": "-"}, {"a": I(1, 5, 1)}, "1", ratio=["a*x", "sqrt(a**2*x**2 + 1)"])
    with pytest.raises(GenerationError):
        self_check(parse_template(r), {"a": 1})                    # либо опечатка, либо автор пишет steps: false


@pytest.mark.parametrize("patch", [
    {"limit": None}, {"limit": {"to": "0"}}, {"limit": {"to": "0", "side": "left"}}, {"ratio": ["x"]},
    {"product": ["x", "x"]}, {"ratio": None}, {"reduce": ["x", "x"]}, {"integrand": "x"}, {"ratio": ["q*x", "x"]},
])
def test_bad_format_rejected(patch):
    r = copy.deepcopy(RAW["1318"])
    for k, v in patch.items():
        if v is None:
            r.pop(k)
        else:
            r[k] = v
    with pytest.raises(TemplateError):
        parse_template(r)


def test_integral_templates_untouched():
    from tests.test_templates import VALID
    t = parse_template(VALID)
    i = build(t, {"a": 2, "s": 1, "k": 1})
    assert t.limit is None and i.steps and all(c.role is None for c in i.checkpoints)


# --- проверка значения -------------------------------------------------------------------------------

@pytest.mark.parametrize("user, want", [
    ("1/2", "1/2"), ("0.5", "1/2"), ("0,5", "1/2"), ("2/4", "1/2"), ("oo", "oo"), ("+oo", "oo"), ("∞", "oo"), ("-∞", "-oo"),
    ("e^(-1/6)", "exp(-1/6)"), ("1/e", "exp(-1)"), ("sqrt(e)", "exp(1/2)"), ("27(ln 3 - 1)", "27*(log(3) - 1)"),
    ("ln 27 - ln 9", "log(3)"), ("0", "0"), ("e^(2/pi)", "exp(2/pi)"),
])
def test_value_accepted(user, want):
    assert check_value(user, want).status == OK


@pytest.mark.parametrize("user, want, status", [
    ("1", "1/2", WRONG), ("-1/2", "1/2", SIGN), ("1/4", "1/2", WRONG), ("oo", "1", WRONG), ("1", "oo", WRONG),
    ("-oo", "oo", SIGN), ("oo", "-oo", SIGN), ("0", "oo", WRONG), ("2/3", "3/2", WRONG),
])
def test_value_rejected(user, want, status):
    assert check_value(user, want).status == status


@pytest.mark.parametrize("user, want", [("0.33", "1/3"), ("0.3679", "exp(-1)"), ("1.65", "exp(1/2)"), ("0,333", "1/3")])
def test_approximation_asks_for_exact(user, want):
    v = check_value(user, want)
    assert v.status == "approx" and "точн" in v.note.lower()       # попытка не тратится


@pytest.mark.parametrize("user", ["x", "2x + 1", "", "abc", "1/0", "oo - oo", "1 + C"])
def test_value_parse_error(user):
    assert check_value(user, "1/2").status == PARSE_ERROR


def test_traps_give_diagnosis():
    traps = {"-2": "Это предел ln y. Вернись: y = e^…", "3": "Неопределённости уже нет, а Лопиталь применён ещё раз."}
    v = check_value("-2", "exp(-2)", traps=traps)
    assert v.status == WRONG and "ln y" in v.note
    assert check_value("exp(-2)", "exp(-2)", traps=traps).status == OK
    assert check_value("5", "exp(-2)", traps=traps).note is None


def test_over_lhopital_trap_is_built():
    """После одного f′/g′ неопределённости нет; лишний Лопиталь даёт другое число — его и узнаём."""
    r = raw("lim_trap", 1, ZERO, {"a": I(1, 4, 1)}, "1", ratio=["exp(a*x) - 1", "a*x + x**2"])
    v = build(parse_template(r), {"a": 2}).checkpoints[-1]         # f′/g′ = 2e^(2x)/(2 + 2x) → 1; ещё раз: 4/2 = 2
    assert any(sp.sympify(k) == 2 and "ещё раз" in n for k, n in v.traps.items())


def test_trap_equal_to_answer_is_dropped():
    for num in RAW:
        v = inst(num).checkpoints[-1]
        assert all(sp.simplify(sp.sympify(k) - sp.sympify(v.expected)) != 0 for k in (v.traps or {}))


# --- найдено чтением карточек глазами -------------------------------------------------------------------

def test_wrong_options_are_simplified_too():
    """Верный вариант сокращён — неверные тоже, иначе он выдаёт себя видом."""
    for num in ("1341", "1342", "1346"):
        c = next(c for c in inst(num).checkpoints if c.role == "lhopital")
        for o in c.distractors:
            assert r"\frac{\frac{" not in c.labels[o] and r"{\frac{1}{x}}" not in c.labels[o], c.labels[o]


def test_statement_keeps_author_order():
    s = inst("1354").statement_latex.replace(" ", "")
    assert s.index(r"\frac{1}{x}") < s.index(r"e^{x}-1") and "-\\frac{1}{e^{x}-1}" in s


def test_minus_is_in_front_of_fraction():
    c = next(c for c in inst("1346").checkpoints if c.role == "lhopital")
    assert c.labels[c.expected].replace(" ", "").startswith(r"-\frac{1}")


def test_heavy_template_is_fast():
    import time
    r = raw("lim_heavy", 1335, ZERO, {"a": I(1, 3, 1)}, "1",
            ratio=["a*x - log(sin(a*x) + sqrt(1 + sin(a*x)**2))", "sinh(a*x) - sin(a*x)"])
    t0 = time.monotonic()
    generate(parse_template(r), 1)                                # самопроверка и сборка: цепочка считается один раз
    assert time.monotonic() - t0 < 4                              # сервер медленнее в 3–4 раза, таймаут генератора 20 с
