import copy
import random

import pytest
import sympy as sp

from core.checker import Cmp, numeric_compare
from core.generator import (
    MAX_TRIES, N_DISTRACTORS, GenerationError,
    build, generate, make_distractors, sample_params, self_check,
)
from core.parser import parse_trusted
from core.templates import parse_template
from tests.test_templates import VALID

TOY = parse_template(VALID)


def toy(**patch):
    """TOY с заменой полей верхнего уровня или точек: toy(reference='...')."""
    raw = copy.deepcopy(VALID)
    cps = raw["checkpoints"]
    if "reference" in patch:
        cps[2]["reference"] = patch.pop("reference")
    if "substitution" in patch:
        cps[1]["substitution"] = patch.pop("substitution")
    if "expected" in patch:
        cps[1]["expected"] = patch.pop("expected")
    raw.update(patch)
    return parse_template(raw)


# --- параметры -------------------------------------------------------------

def test_sample_respects_ranges_and_constraints():
    rng = random.Random(0)
    for _ in range(50):
        p = sample_params(TOY, rng)
        assert set(p) == {"a", "s", "k"}
        assert 2 <= p["a"] <= 5          # constraint a > 1
        assert p["s"] in (1, -1)
        assert -3 <= p["k"] <= 3 and p["k"] != 0
        assert all(isinstance(v, int) for v in p.values())


def test_sample_covers_range():
    rng = random.Random(0)
    seen = {sample_params(TOY, rng)["a"] for _ in range(200)}
    assert seen == {2, 3, 4, 5}


def test_impossible_constraints():
    with pytest.raises(GenerationError):
        sample_params(toy(constraints=["a > 10"]), random.Random(0))


def test_generate_deterministic():
    assert generate(TOY, seed=7) == generate(TOY, seed=7)
    seeds = {tuple(sorted(generate(TOY, seed=s).params.items())) for s in range(30)}
    assert len(seeds) > 1


def test_build_rebuilds_from_params():
    inst = generate(TOY, seed=3)
    assert build(TOY, inst.params) == inst


# --- подстановка -----------------------------------------------------------

def test_substitution():
    inst = build(TOY, {"a": 3, "s": -1, "k": 1})
    x = sp.Symbol("x")
    assert sp.simplify(parse_trusted(inst.integrand) - (-3 * sp.cos(3 * x))) == 0
    cp = inst.checkpoints
    assert cp[0].type == "choice" and cp[0].correct == ("A",) and cp[0].var == "x"
    assert cp[1].var == "t" and parse_trusted(cp[1].expected, "t") == -1
    assert sp.simplify(parse_trusted(cp[2].reference) + sp.sin(3 * x)) == 0
    assert cp[2].domain == (-3.0, 3.0)   # ["-a", "a"] при a = 3
    assert all(isinstance(v, float) for v in cp[2].domain)


def test_statement_latex():
    inst = build(TOY, {"a": 3, "s": 1, "k": 1})
    assert inst.statement_latex.startswith("\\int")
    assert "\\cos" in inst.statement_latex and "dx" in inst.statement_latex
    assert "a" not in inst.statement_latex.replace("\\int", "")  # параметр подставлен


def test_statement_override():
    inst = build(toy(statement="\\int s \\, dx"), {"a": 3, "s": 1, "k": 1})
    assert inst.statement_latex == "\\int s \\, dx"


# --- самопроверка ----------------------------------------------------------

def test_self_check_ok():
    self_check(TOY, {"a": 2, "s": 1, "k": 1})


@pytest.mark.parametrize("patch", [
    {"reference": "s*sin(a*x)/a"},      # опечатка: лишний /a
    {"reference": "-s*sin(a*x)"},       # опечатка: знак
    {"substitution": "cos(a*x)"},       # не та замена
    {"expected": "2*s"},                # не тот интеграл по t
])
def test_self_check_catches_typos(patch):
    with pytest.raises(GenerationError):
        self_check(toy(**patch), {"a": 2, "s": 1, "k": 1})


def test_self_check_same_as():
    raw = copy.deepcopy(VALID)
    raw["checkpoints"].insert(1, {"type": "expr", "prompt": "?", "expected": "s*a*cos(a*x)", "same_as": "integrand"})
    self_check(parse_template(raw), {"a": 2, "s": 1, "k": 1})
    raw["checkpoints"][1]["expected"] = "s*cos(a*x)"
    with pytest.raises(GenerationError):
        self_check(parse_template(raw), {"a": 2, "s": 1, "k": 1})


def test_generate_runs_self_check():
    with pytest.raises(GenerationError):
        generate(toy(reference="s*sin(a*x)/a"), seed=0)


# --- дистракторы -----------------------------------------------------------

def _all_distinct(expected, ds, var="t", domain=(-3.0, 3.0)):
    exprs = [parse_trusted(e, var) for e in (expected, *ds)]
    for i in range(len(exprs)):
        for j in range(i + 1, len(exprs)):
            if numeric_compare(exprs[i], exprs[j], var, domain) == Cmp.EQUAL:
                return False
    return True


def test_distractors_auto():
    ds = make_distractors("2/(8 + 2*t**2)", "t", (-3.0, 3.0))
    assert len(ds) == N_DISTRACTORS
    assert _all_distinct("2/(8 + 2*t**2)", ds)


def test_distractors_manual_first():
    ds = make_distractors("2/(8 + 2*t**2)", "t", (-3.0, 3.0), manual=("2/(2 + 8*t**2)",))
    assert parse_trusted(ds[0], "t") == parse_trusted("2/(2 + 8*t**2)", "t")


def test_distractors_dedup():
    # ручной дистрактор эквивалентен эталону, второй — дубль автоматического −e
    ds = make_distractors("1/(4 + t**2)", "t", (-3.0, 3.0),
                          manual=("2/(8 + 2*t**2)", "-1/(t**2 + 4)"))
    assert len(ds) == N_DISTRACTORS
    assert _all_distinct("1/(4 + t**2)", ds)


def test_distractors_not_enough():
    with pytest.raises(GenerationError):
        make_distractors("0", "t", (-3.0, 3.0))   # −0 = 2·0 = 0/2 = 0


def test_instance_has_distractors():
    inst = generate(TOY, seed=1)
    assert len(inst.checkpoints[1].distractors) == N_DISTRACTORS
    assert inst.checkpoints[0].distractors == ()     # у choice свои options
    assert inst.checkpoints[2].distractors == ()     # финальный ответ — ввод


def test_max_tries_is_sane():
    assert 10 <= MAX_TRIES <= 10_000


def test_self_check_same_as_formula():
    raw = copy.deepcopy(VALID)
    raw["checkpoints"].insert(1, {"type": "expr", "prompt": "?", "expected": "(x + a)**2",
                                  "same_as": "x**2 + 2*a*x + a**2"})
    self_check(parse_template(raw), {"a": 2, "s": 1, "k": 1})
    raw["checkpoints"][1]["same_as"] = "x**2 + a*x + a**2"          # опечатка в шаблоне
    with pytest.raises(GenerationError):
        self_check(parse_template(raw), {"a": 2, "s": 1, "k": 1})
