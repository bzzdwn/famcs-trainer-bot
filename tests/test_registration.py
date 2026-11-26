import random
from pathlib import Path

import pytest

from bot.registration import InvalidName, check_group_code, normalize_name
from bot.topics import TOPIC_NAMES, available, pick_template, topic_title
from core.templates import load_templates

BANK = load_templates(Path(__file__).resolve().parent.parent / "data" / "templates")


# --- код группы ------------------------------------------------------------

@pytest.mark.parametrize("entered, ok", [
    ("matan2026", True), ("  MATAN2026 ", True), ("Matan2026\n", True),
    ("matan2025", False), ("", False), ("matan 2026", False),
])
def test_group_code(entered, ok):
    assert check_group_code(entered, "matan2026") is ok


# --- имя -------------------------------------------------------------------

@pytest.mark.parametrize("raw, name", [
    ("Иванов Иван", "Иванов Иван"),
    ("  иванов   иван ", "Иванов Иван"),
    ("ИВАНОВ ИВАН", "Иванов Иван"),
    ("петров-водкин кузьма", "Петров-Водкин Кузьма"),
    ("Ёлкин Пётр", "Ёлкин Пётр"),
])
def test_name_ok(raw, name):
    assert normalize_name(raw) == name


@pytest.mark.parametrize("raw", [
    "", "Иван", "Иванов Иван Иванович", "Ivanov Ivan", "Иванов 1ван",
    "И Иван", "Иванов " + "И" * 31, "Иванов -", "Иванов Иван!",
])
def test_name_bad(raw):
    with pytest.raises(InvalidName) as e:
        normalize_name(raw)
    assert str(e.value)


# --- темы и уровни ---------------------------------------------------------

def test_available():
    av = available(BANK)
    assert list(av) == [t for t in TOPIC_NAMES if t in av]          # порядок как в меню
    assert set(av) == set(TOPIC_NAMES)                               # у каждой темы меню есть шаблоны
    for topic, levels in av.items():                                 # уровни — ровно те, что есть в банке
        assert levels == sorted({t.difficulty for t in BANK.values() if t.topic == topic})
    assert all(levels for levels in av.values())


def test_pick_template():
    rng = random.Random(0)
    t = pick_template(BANK, "indef_integral.trig", "B", rng)
    assert (t.topic, t.difficulty) == ("indef_integral.trig", "B")
    assert pick_template(BANK, "indef_integral.parts", "C", rng) is None
    assert pick_template(BANK, "indef_integral.irrational", "C", rng).difficulty == "C"
    assert pick_template(BANK, "nope", "A", rng) is None


def test_topic_title():
    assert topic_title("indef_integral.trig") == "Тригонометрические"
    assert topic_title("unknown.topic") == "unknown.topic"


# --- «Вразнобой» -------------------------------------------------------------

from bot.topics import ANY_LEVEL, MIX, mix_levels


def test_mix_levels():
    assert mix_levels(BANK) == ["A", "B", "C", ANY_LEVEL]


def test_mix_pick_respects_level():
    rng = random.Random(0)
    picks = [pick_template(BANK, MIX, "B", rng) for _ in range(100)]
    assert all(t.difficulty == "B" for t in picks)
    assert len({t.topic for t in picks}) > 2                       # действительно из разных тем


def test_mix_any_level():
    rng = random.Random(0)
    picks = [pick_template(BANK, MIX, ANY_LEVEL, rng) for _ in range(200)]
    assert {t.difficulty for t in picks} == {"A", "B", "C"}


def test_no_immediate_repeat():
    rng = random.Random(0)
    prev = None
    for _ in range(50):
        t = pick_template(BANK, MIX, "A", rng, exclude=prev)
        assert t.id != prev
        prev = t.id


def test_exclude_ignored_when_single_candidate():
    one = {"int_binomial_01": BANK["int_binomial_01"]}             # банк с единственным C
    t = pick_template(one, MIX, "C", random.Random(0), exclude="int_binomial_01")
    assert t.id == "int_binomial_01"                               # другого C нет — повтор лучше, чем ничего


def test_mix_no_templates():
    assert pick_template(BANK, MIX, "D", random.Random(0)) is None


def test_exclude_in_topic_mode():
    rng = random.Random(0)
    ids = {pick_template(BANK, "indef_integral.trig", "B", rng, exclude="int_trig_tan_01").id for _ in range(50)}
    assert "int_trig_tan_01" not in ids
    assert ids <= {t.id for t in BANK.values() if t.topic == "indef_integral.trig" and t.difficulty == "B"}


# --- § 6 «Разные»: метод не подсказан темой -----------------------------------

MIXED = "indef_integral.mixed"


def test_mixed_topic_title():
    assert topic_title(MIXED) == "Разные"


def test_mixed_topic_last_in_menu():
    """«Разные» — последняя тема интегралов (за ними идёт раздел пределов)."""
    assert [t for t in TOPIC_NAMES if t.startswith("indef_integral.")][-1] == MIXED
    assert list(available(BANK, "indef_integral"))[-1] == MIXED


def test_mixed_topic_only_level_c():
    assert available(BANK)[MIXED] == ["C"]
    assert all(t.difficulty == "C" for t in BANK.values() if t.topic == MIXED)


# --- все методы для «Вразнобой» ---------------------------------------------

from bot.topics import build_method_step, method_options


def test_method_options():
    opts = method_options(BANK)
    assert len(opts) == len(set(opts)) == len({t.blitz_method for t in BANK.values()})
    assert opts == method_options(BANK)                              # фиксированный порядок
    order = list(TOPIC_NAMES)
    first_topic = {}
    for t in sorted(BANK.values(), key=lambda t: t.id):
        first_topic.setdefault(t.blitz_method, min(order.index(x.topic) for x in BANK.values()
                                                   if x.blitz_method == t.blitz_method))
    groups = [first_topic[m] for m in opts]
    assert groups == sorted(groups)                                  # сгруппированы как темы в меню


def test_build_method_step():
    tpl = BANK["int_trig_tan_01"]
    step = build_method_step(BANK, tpl)
    assert step.options == method_options(BANK, "indef_integral")   # методы своего раздела
    assert step.correct == tpl.blitz_method and step.alternatives == ("универсальная замена",)
    assert step.hint == tpl.checkpoints[0].hint
