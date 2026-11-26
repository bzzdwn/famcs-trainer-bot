"""Банк пределов: настоящие шаблоны data/templates/limits.yaml."""
import io
import time
from pathlib import Path

import pytest
import yaml
from PIL import Image

from bot.blitz import BlitzRun
from bot.render import PNG_MAGIC, render_latex, render_options
from bot.topics import INTEGRALS, LIMITS, TOPIC_NAMES, available, method_options, section_of, sections, topic_title
from core.checker import OK, check_value
from core.generator import check_example, generate
from core.hints import answer_hints
from core.limits import MAX_CARD_PX
from core.templates import load_templates

ROOT = Path(__file__).resolve().parent.parent
BANK = load_templates(ROOT / "data" / "templates")
LIM = {k: t for k, t in BANK.items() if t.limit}
IDS = sorted(LIM)
EXAMPLES = [(tid, ex) for tid in IDS for ex in LIM[tid].examples]
N = 12


def unique(tid):
    seen = {}
    for seed in range(N):
        i = generate(LIM[tid], seed)
        seen.setdefault(tuple(sorted(i.params.items())), i)
    return list(seen.values())


def test_first_six():
    nums = {ex.number for _, ex in EXAMPLES}
    assert {"1318", "1319", "1341", "1342", "1346", "1354"} <= nums
    assert all(t.topic == "limits.lhopital" for t in LIM.values())


def test_menu_sees_section():
    assert sections(BANK) == [INTEGRALS, LIMITS] and topic_title("limits.lhopital") == "Правило Лопиталя"
    assert list(available(BANK, LIMITS)) == ["limits.lhopital"]
    assert [t for t in TOPIC_NAMES if section_of(t) == LIMITS] == ["limits.lhopital"]


def test_integrals_untouched_by_limits():
    ints = {k: t for k, t in BANK.items() if not t.limit}
    assert method_options(BANK, INTEGRALS) == method_options(ints)
    assert BlitzRun(BANK, seed=1).tasks == BlitzRun(ints, seed=1).tasks


@pytest.mark.parametrize("tid", IDS)
def test_generates_and_renders(tid):
    insts = unique(tid)
    assert len(insts) > 1                                             # не одна и та же задача
    for i in insts:
        assert render_latex(i.statement_latex).startswith(PNG_MAGIC)
        assert check_value(i.checkpoints[-1].expected, i.checkpoints[-1].expected).status == OK
        for c in i.checkpoints:
            assert c.hint or c.role == "value"


@pytest.mark.parametrize("tid", IDS)
def test_cards_fit(tid):
    """Настоящая ширина карточек, а не оценка: то, что видит студент."""
    for i in unique(tid)[:3]:
        for c in i.checkpoints:
            if c.labels:
                png = render_options([c.labels[o] for o in (c.expected, *c.distractors)])
                assert Image.open(io.BytesIO(png)).width <= MAX_CARD_PX * 1.15, (tid, c.role, i.params)


@pytest.mark.parametrize("tid", IDS)
def test_step_count_is_constant(tid):
    """/export группирует по номеру шага: у шаблона число шагов не зависит от параметров."""
    assert len({tuple(c.role for c in i.checkpoints) for i in unique(tid)}) == 1


@pytest.mark.parametrize("tid", IDS)
def test_hints(tid):
    for i in unique(tid)[:3]:
        assert answer_hints(i, True) and answer_hints(i, False)


@pytest.mark.parametrize("tid", IDS)
def test_generation_time(tid):
    t0 = time.monotonic()
    generate(LIM[tid], 999)
    assert time.monotonic() - t0 < 4                                  # сервер в 3–4 раза медленнее, таймаут 20 с


@pytest.mark.parametrize("tid, ex", EXAMPLES, ids=[f"{t}-{e.number}" for t, e in EXAMPLES])
def test_book(tid, ex):
    check_example(LIM[tid], ex)


def test_coverage_file_has_limits():
    cov = yaml.safe_load((ROOT / "data" / "demidovich.yaml").read_text(encoding="utf-8"))
    for tid, ex in EXAMPLES:
        assert cov[float(ex.number)]["template"] == tid


def test_export_names_value_step():
    from bot.export import CTYPE_RU
    assert CTYPE_RU["value"] == "значение"
