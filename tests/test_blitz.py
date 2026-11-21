import copy
from pathlib import Path

import pytest

from bot.blitz import BLITZ_SIZE, N_OPTIONS, OK, SKIP, TIME_LIMIT, WRONG, BlitzRun
from core.templates import load_templates, parse_template
from tests.test_templates import VALID

BANK = load_templates(Path(__file__).resolve().parent.parent / "data" / "templates")


def correct_idx(task):
    return task.options.index(task.correct)


def wrong_idx(task):
    return next(i for i, o in enumerate(task.options) if o != task.correct)


def toy_bank(methods):
    """Банк из игрушечных шаблонов с заданными blitz_method."""
    bank = {}
    for k, m in enumerate(methods):
        raw = copy.deepcopy(VALID)
        raw["id"], raw["blitz_method"] = f"toy_{k}", m
        bank[raw["id"]] = parse_template(raw)
    return bank


# --- состав серии ------------------------------------------------------------

def test_series_shape():
    run = BlitzRun(BANK, seed=1)
    assert len(run.tasks) == BLITZ_SIZE
    for t in run.tasks:
        tpl = BANK[t.template_id]
        assert t.correct == tpl.blitz_method
        assert t.hint == tpl.checkpoints[0].hint
        assert len(t.options) == N_OPTIONS == len(set(t.options))
        assert t.options.count(t.correct) == 1
        assert t.instance.template_id == t.template_id


def test_no_repeats_when_bank_is_big():
    run = BlitzRun(BANK, seed=2)
    ids = [t.template_id for t in run.tasks]
    assert len(set(ids)) == len(ids)


def test_small_bank_repeats():
    run = BlitzRun(toy_bank(["м1", "м2", "м3", "м4"]), seed=0)
    assert len(run.tasks) == BLITZ_SIZE


def test_too_few_methods():
    with pytest.raises(ValueError):
        BlitzRun(toy_bank(["м1", "м2", "м3", "м3"]), seed=0)   # 3 различных метода < 4 кнопок


def test_deterministic():
    a, b = BlitzRun(BANK, seed=5), BlitzRun(BANK, seed=5)
    assert [(t.template_id, t.options, t.instance) for t in a.tasks] == \
           [(t.template_id, t.options, t.instance) for t in b.tasks]
    assert len({tuple(t.template_id for t in BlitzRun(BANK, seed=s).tasks) for s in range(10)}) > 1


def test_correct_position_varies():
    positions = {correct_idx(t) for s in range(5) for t in BlitzRun(BANK, seed=s).tasks}
    assert len(positions) > 1


# --- ответы ------------------------------------------------------------------

def test_answers():
    run = BlitzRun(BANK, seed=3)
    a = run.answer(correct_idx(run.current()), elapsed=2.0)
    assert (a.idx, a.verdict, a.elapsed) == (0, OK, 2.0) and a.chosen == run.tasks[0].correct
    a = run.answer(wrong_idx(run.current()), elapsed=3.0)
    assert (a.idx, a.verdict) == (1, WRONG) and a.chosen != run.tasks[1].correct
    a = run.answer(None, elapsed=TIME_LIMIT)               # таймер
    assert (a.idx, a.verdict, a.chosen) == (2, SKIP, None)


def test_late_correct_is_skip():
    run = BlitzRun(BANK, seed=3)
    a = run.answer(correct_idx(run.current()), elapsed=TIME_LIMIT + 0.5)
    assert a.verdict == SKIP and a.chosen is None


def test_exact_limit_counts():
    run = BlitzRun(BANK, seed=3)
    assert run.answer(correct_idx(run.current()), elapsed=TIME_LIMIT).verdict == OK


def test_invalid_index():
    run = BlitzRun(BANK, seed=3)
    with pytest.raises(ValueError):
        run.answer(N_OPTIONS, elapsed=1.0)
    assert run.current() is run.tasks[0]                    # не сдвинулись


def test_finish():
    run = BlitzRun(BANK, seed=4)
    for _ in range(BLITZ_SIZE):
        run.answer(correct_idx(run.current()), elapsed=1.0)
    assert run.finished and run.current() is None
    with pytest.raises(RuntimeError):
        run.answer(0, elapsed=1.0)


# --- итог --------------------------------------------------------------------

def test_summary():
    run = BlitzRun(BANK, seed=6)
    plan = [OK, WRONG, SKIP, OK, OK, WRONG, OK, OK, SKIP, OK]
    for v in plan:
        t = run.current()
        if v == OK:
            run.answer(correct_idx(t), 1.0)
        elif v == WRONG:
            run.answer(wrong_idx(t), 1.0)
        else:
            run.answer(None, TIME_LIMIT)
    sm = run.summary()
    assert (sm.score, sm.total) == (plan.count(OK), BLITZ_SIZE)
    assert [m.idx for m in sm.mistakes] == [i for i, v in enumerate(plan) if v != OK]
    for m in sm.mistakes:
        t = run.tasks[m.idx]
        assert m.correct == t.correct and m.hint == t.hint
        assert m.statement_latex == t.instance.statement_latex
        assert (m.chosen is None) == (plan[m.idx] == SKIP)


def test_summary_before_finish():
    run = BlitzRun(BANK, seed=6)
    run.answer(correct_idx(run.current()), 1.0)
    sm = run.summary()                                      # промежуточный счёт — только по отвеченным
    assert (sm.score, sm.total, sm.mistakes) == (1, BLITZ_SIZE, ())


# --- отсчёт в конце задачи --------------------------------------------------

from bot.blitz import COUNTDOWN, countdown_marks


def test_countdown_marks():
    assert countdown_marks() == [(7.0, "3…"), (8.0, "2…"), (9.0, "1…")]
    assert countdown_marks(5.0, 2) == [(3.0, "2…"), (4.0, "1…")]
    assert len(countdown_marks()) == COUNTDOWN


# --- своё время на карточку ---------------------------------------------------

from bot.blitz import TIME_CHOICES, InvalidTime, parse_time


@pytest.mark.parametrize("text, want", [("15", 15), (" 20 ", 20), ("25с", 25), ("25 с", 25), ("60", 60), ("5", 5)])
def test_parse_time_ok(text, want):
    assert parse_time(text) == want


@pytest.mark.parametrize("text", ["7", "0", "65", "abc", "", "  ", "-10", "12.5"])
def test_parse_time_bad(text):
    with pytest.raises(InvalidTime) as e:
        parse_time(text)
    assert str(e.value)


def test_time_choices():
    assert TIME_CHOICES == (5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60)


def test_custom_time_limit():
    run = BlitzRun(BANK, seed=3, time_limit=25)
    assert run.time_limit == 25 and BlitzRun(BANK, seed=3).time_limit == TIME_LIMIT
    assert run.answer(correct_idx(run.current()), elapsed=25.0).verdict == OK       # ровно лимит — засчитано
    assert run.answer(correct_idx(run.current()), elapsed=25.5).verdict == SKIP     # позже — не успел


def test_custom_time_same_series():
    a, b = BlitzRun(BANK, seed=4), BlitzRun(BANK, seed=4, time_limit=40)
    assert [t.template_id for t in a.tasks] == [t.template_id for t in b.tasks]     # время не меняет задачи


def test_invalid_time_limit():
    with pytest.raises(ValueError):
        BlitzRun(BANK, seed=0, time_limit=7)


def test_countdown_for_custom_time():
    assert countdown_marks(25) == [(22.0, "3…"), (23.0, "2…"), (24.0, "1…")]
    assert countdown_marks(5) == [(2.0, "3…"), (3.0, "2…"), (4.0, "1…")]
