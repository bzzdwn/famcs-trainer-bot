"""Разделы меню: интегралы и пределы. Раздел → тема → уровень; «Вразнобой» и методы — внутри раздела.
Настоящих пределов в банке ещё нет: раздел собран из игрушечных шаблонов."""
import copy
import random

import pytest

from bot.blitz import BlitzRun
from bot.topics import (ANY_LEVEL, MIX, SECTION_NAMES, available, build_method_step, is_mix, method_options, mix_key,
                        mix_levels, pick_template, section_of, sections, topic_title)
from core.templates import parse_template
from tests.test_templates import VALID
from tests.test_train_formula import BANK, STUDENT, w  # noqa: F401  (фикстура w)
from bot.handlers import train

INT, LIM = "indef_integral", "limits"
INT_BANK = {k: t for k, t in BANK.items() if not t.limit}      # пределы из настоящего банка не берём: раздел собран из игрушек


def toy(tid, topic, level="A", method="тип 0/0"):
    raw = copy.deepcopy(VALID)
    raw.update(id=tid, topic=topic, difficulty=level, blitz_method=method)
    return parse_template(raw)


def both(n_topics=1):
    """Настоящие интегралы + игрушечные пределы."""
    lim = [toy("lim_a", "limits.lhopital", "A", "тип 0/0"), toy("lim_b", "limits.lhopital", "B", "тип ∞/∞")]
    if n_topics == 2:
        lim.append(toy("lim_c", "limits.taylor", "A", "Тейлор"))
    return {**INT_BANK, **{t.id: t for t in lim}}


# --- разделы -----------------------------------------------------------------------

def test_section_names():
    assert SECTION_NAMES == {INT: "Интегралы", LIM: "Пределы"}


@pytest.mark.parametrize("topic, sec", [
    ("indef_integral.trig", INT), ("limits.lhopital", LIM), (MIX, INT), ("mix.limits", LIM), ("mix.indef_integral", INT),
])
def test_section_of(topic, sec):
    assert section_of(topic) == sec                         # «mix» без раздела — старые кнопки: интегралы


def test_only_sections_with_templates():
    assert sections(INT_BANK) == [INT]
    assert sections(both()) == [INT, LIM]                   # порядок как в SECTION_NAMES


def test_available_by_section():
    b = both(2)
    assert set(available(b, LIM)) == {"limits.lhopital", "limits.taylor"}
    assert available(b, LIM)["limits.lhopital"] == ["A", "B"]
    assert all(t.startswith(INT) for t in available(b, INT))
    assert set(available(b)) == set(available(b, INT)) | set(available(b, LIM))   # без раздела — всё, как раньше


# --- «Вразнобой» внутри раздела -------------------------------------------------------

def test_mix_key_and_title():
    assert is_mix(MIX) and is_mix(mix_key(LIM)) and not is_mix("limits.lhopital")
    assert topic_title(mix_key(LIM)) == topic_title(MIX)


@pytest.mark.parametrize("key, sec", [(mix_key(INT), INT), (mix_key(LIM), LIM), (MIX, INT)])
def test_mix_stays_in_section(key, sec):
    rng = random.Random(0)
    got = {section_of(pick_template(both(2), key, ANY_LEVEL, rng).topic) for _ in range(150)}
    assert got == {sec}


def test_mix_levels_by_section():
    assert mix_levels(both(), LIM) == ["A", "B", ANY_LEVEL]
    assert mix_levels(both(), INT) == ["A", "B", "C", ANY_LEVEL] == mix_levels(INT_BANK)


# --- методы не смешиваются ---------------------------------------------------------------

def test_method_options_by_section():
    b = both()
    assert set(method_options(b, LIM)) == {"тип 0/0", "тип ∞/∞"}
    assert method_options(b, INT) == method_options(INT_BANK)   # порядок кнопок в интегралах не изменился


def test_method_step_offers_own_section_only():
    b = both()
    assert "тип 0/0" not in build_method_step(b, b["int_trig_tan_01"]).options
    assert set(build_method_step(b, b["lim_a"]).options) == {"тип 0/0", "тип ∞/∞"}


def test_blitz_is_integrals_only_for_now():
    """Свой блиц пределов — отдельный этап; до него пределы в серию и в кнопки не попадают."""
    for seed in range(4):
        for t in BlitzRun(both(), seed=seed).tasks:
            assert section_of(both()[t.template_id].topic) == INT
            assert not {"тип 0/0", "тип ∞/∞"} & set(t.options)


# --- меню -----------------------------------------------------------------------------------

async def test_train_asks_section_when_two(w):                              # noqa: F811
    w.bank = both()
    await w.feed(text="/train")
    assert [b for b in w.bot.buttons() if b.startswith("s:")] == [f"s:{INT}", f"s:{LIM}"]
    assert not [b for b in w.bot.buttons() if b.startswith("t:")]


async def test_train_skips_section_when_one(w):                             # noqa: F811
    """Пока раздел один, меню студента не меняется."""
    w.bank = INT_BANK
    await w.feed(text="/train")
    assert "t:indef_integral.basic" in w.bot.buttons() and not [b for b in w.bot.buttons() if b.startswith("s:")]


async def test_section_opens_its_topics(w):                                 # noqa: F811
    w.bank = both(2)
    await w.feed(cb=f"s:{LIM}")
    assert {"t:limits.lhopital", "t:limits.taylor", f"t:{mix_key(LIM)}", "menu"} <= set(w.bot.buttons())
    assert not [b for b in w.bot.buttons() if b.startswith("t:indef")]


async def test_mix_hidden_when_section_has_one_topic(w):                    # noqa: F811
    w.bank = both(1)
    await w.feed(cb=f"s:{LIM}")
    assert "t:limits.lhopital" in w.bot.buttons() and f"t:{mix_key(LIM)}" not in w.bot.buttons()


async def test_levels_of_limit_topic(w):                                    # noqa: F811
    w.bank = both()
    await w.feed(cb="t:limits.lhopital")
    assert [b for b in w.bot.buttons() if b.startswith("l:")] == ["l:limits.lhopital:A", "l:limits.lhopital:B"]


async def test_levels_of_section_mix(w):                                    # noqa: F811
    w.bank = both(2)
    await w.feed(cb=f"t:{mix_key(LIM)}")
    assert f"l:{mix_key(LIM)}:B" in w.bot.buttons() and f"l:{mix_key(LIM)}:C" not in w.bot.buttons()


async def test_section_mix_gives_task_with_method_step(w):                  # noqa: F811
    w.bank = both(2)
    await w.feed(cb=f"l:{mix_key(LIM)}:{ANY_LEVEL}")
    assert section_of(w.bank[train.OFFERED[STUDENT].inst.template_id].topic) == LIM
    await w.feed(cb="path:steps")
    a = train.ACTIVE[STUDENT]
    assert a.session.current().cp_type == "method"
    assert set(a.session.current().options) == set(method_options(w.bank, LIM))


async def test_other_topic_returns_to_same_section(w):                      # noqa: F811
    w.bank = both(2)
    a = await w.task()
    a.topic = "limits.lhopital"
    await w.feed(cb="drop")
    assert f"s:{LIM}" in w.bot.buttons()


async def test_unknown_section_does_not_break(w):                           # noqa: F811
    await w.feed(cb="s:geometry")
    assert not [b for b in w.bot.buttons() if b.startswith("t:")]


# --- старые кнопки в чатах студентов -----------------------------------------------------------

@pytest.mark.parametrize("cb", ["next:mix:C", "l:mix:B", "l:mix:any", "next:indef_integral.trig:B"])
async def test_old_buttons_still_give_tasks(w, cb):                         # noqa: F811
    w.bank = both()
    await w.feed(cb=cb)
    assert section_of(w.bank[train.OFFERED[STUDENT].inst.template_id].topic) == INT


async def test_old_topic_and_menu_buttons(w):                               # noqa: F811
    w.bank = both()
    await w.feed(cb="t:mix")
    assert "l:mix:any" in w.bot.buttons() or f"l:{mix_key(INT)}:any" in w.bot.buttons()
    await w.feed(cb="t:indef_integral.trig")
    assert "l:indef_integral.trig:B" in w.bot.buttons()
    await w.feed(cb="menu")
    assert f"s:{LIM}" in w.bot.buttons()


def test_callback_data_fits_telegram():
    for t in [*available(both(2)), mix_key(INT), mix_key(LIM)]:
        assert len(f"next:{t}:{ANY_LEVEL}".encode()) <= 64
