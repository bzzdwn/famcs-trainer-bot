"""Этап 4: блиц по разделам («назови тип» для пределов), /me и /export по разделам."""
import datetime as dt
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import load_workbook

from bot.blitz import N_OPTIONS, BlitzRun
from bot.db import DB
from bot.export import build_xlsx, collect
from bot.flow import TrainSession
from bot.genpool import BlitzStocks
from bot.handlers import blitz as blitz_h
from bot.stats import student_stats
from bot.topics import INTEGRALS, LIMITS, section_of
from core.generator import build, generate
from core.limits import NO_TYPE, TYPES
from core.templates import load_templates
from tests.test_limit_flow import check, right
from tests.test_train_formula import STUDENT, w  # noqa: F401

BANK = load_templates(Path(__file__).resolve().parent.parent / "data" / "templates")
LIM = {k: t for k, t in BANK.items() if t.limit}


# --- серия ---------------------------------------------------------------------------------

def test_limits_series_is_name_the_type():
    for seed in range(5):
        run = BlitzRun(BANK, seed=seed, section=LIMITS)
        for t in run.tasks:
            assert BANK[t.template_id].limit
            assert t.correct in TYPES and len(set(t.options)) == N_OPTIONS and t.correct in t.options
            assert set(t.options) <= set(TYPES) | {NO_TYPE} and t.hint


def test_blitz_type_is_the_real_start_type():
    """blitz_method шаблона пределов — тип исходной неопределённости, тот же, что на первом шаге."""
    for tid, tpl in LIM.items():
        i = generate(tpl, 0)
        first = next(c for c in i.checkpoints if c.role == "type")
        assert tpl.blitz_method == first.correct[0], tid


def test_integral_series_unchanged():
    ints = {k: t for k, t in BANK.items() if not t.limit}
    assert BlitzRun(BANK, seed=3).tasks == BlitzRun(ints, seed=3, section=INTEGRALS).tasks


async def test_generator_builds_limit_series():
    from bot.genpool import GenPool
    g = GenPool(timeout=20)
    try:
        tasks = await g.blitz_tasks(BANK, 5, LIMITS)
        assert tasks == BlitzRun(BANK, seed=5, section=LIMITS).tasks
    finally:
        g.close()


def test_stocks_per_section_and_reset_all():
    class S:
        resets = 0

        def reset(self):
            self.resets += 1
    st = BlitzStocks({INTEGRALS: S(), LIMITS: S()})
    st.reset()
    assert st[INTEGRALS].resets == st[LIMITS].resets == 1


# --- диалог ----------------------------------------------------------------------------------

class OneSeries:
    def __init__(self, section):
        self.section = section

    async def take(self):
        return BlitzRun(BANK, seed=1, section=self.section).tasks

    def reset(self):
        pass


@pytest.fixture
async def ready(w, monkeypatch):                                                      # noqa: F811
    async def no_wait(_):
        return None
    monkeypatch.setattr(blitz_h.asyncio, "sleep", no_wait)
    w.bank = BANK
    w.stock = BlitzStocks({INTEGRALS: OneSeries(INTEGRALS), LIMITS: OneSeries(LIMITS)})
    yield w
    blitz_h.cancel_blitz(STUDENT)


def series_section():
    st = blitz_h.BLITZ.get(STUDENT)
    return {section_of(BANK[t.template_id].topic) for t in st.run.tasks}


async def test_blitz_asks_section(ready):
    await ready.feed(text="/blitz")
    assert [b for b in ready.bot.buttons() if b.startswith("bs:")] == [f"bs:{INTEGRALS}", f"bs:{LIMITS}"]


async def test_section_then_time_then_series(ready):
    await ready.feed(cb=f"bs:{LIMITS}")
    assert f"bt:10:{LIMITS}" in ready.bot.buttons() and f"bt:custom:{LIMITS}" in ready.bot.buttons()
    assert "тип" in ready.bot.texts().lower()                                  # «назови тип», а не «выбери метод»
    await ready.feed(cb=f"bt:10:{LIMITS}")
    assert series_section() == {LIMITS}


async def test_custom_time_keeps_section(ready):
    await ready.feed(cb=f"bt:custom:{LIMITS}")
    await ready.feed(text="25")
    assert series_section() == {LIMITS} and blitz_h.BLITZ[STUDENT].run.time_limit == 25


@pytest.mark.parametrize("cb", ["bt:10", "bt:custom"])
async def test_old_time_buttons_mean_integrals(ready, cb):
    await ready.feed(cb=cb)
    if cb == "bt:custom":
        await ready.feed(text="15")
    assert series_section() == {INTEGRALS}


async def test_old_blitz_button_asks_section(ready):
    await ready.feed(cb="blitz")
    assert f"bs:{LIMITS}" in ready.bot.buttons()


async def test_one_section_skips_question(ready):
    ready.bank = {k: t for k, t in BANK.items() if not t.limit}
    await ready.feed(text="/blitz")
    assert f"bt:10:{INTEGRALS}" in ready.bot.buttons() and not [b for b in ready.bot.buttons() if b.startswith("bs:")]


async def test_finish_buttons_keep_section(ready):
    await ready.feed(cb=f"bt:10:{LIMITS}")
    st = blitz_h.BLITZ[STUDENT]
    while not st.run.finished:
        st.run.answer(0, 1.0)
    ready.bot.sent.clear()
    await blitz_h.finish(STUDENT, ready.db)
    assert {f"bt:10:{LIMITS}", f"bs:{LIMITS}"} <= set(ready.bot.buttons())


async def test_run_records_section(ready):
    await ready.feed(cb=f"bt:10:{LIMITS}")
    async with ready.db.conn.execute("SELECT section FROM blitz_runs") as cur:
        assert [r[0] for r in await cur.fetchall()] == [LIMITS]


# --- статистика ---------------------------------------------------------------------------------

@pytest.fixture
async def db():
    d = await DB.open(":memory:")
    yield d
    await d.close()


async def run(db, sid, score, section=None, t=10):
    rid = await db.start_blitz_run(sid, time_limit=t, **({"section": section} if section else {}))
    await db.finish_blitz_run(rid, score, 10)


async def test_old_runs_are_integrals(db):
    sid = await db.add_student("Иванов Иван", 1)
    await run(db, sid, 7)
    await db.conn.execute("UPDATE blitz_runs SET section = NULL")             # серии до этапа 4
    await run(db, sid, 5, LIMITS)
    st = await student_stats(db, sid)
    assert st.best_blitz == (7, 10, 10) and st.best_blitz_by_section == {INTEGRALS: (7, 10, 10), LIMITS: (5, 10, 10)}


async def test_me_shows_both(w):                                               # noqa: F811
    st = await w.db.get_student_by_tg(STUDENT)
    await run(w.db, st.id, 7)
    await run(w.db, st.id, 9, LIMITS)
    await w.feed(text="/me")
    text = w.bot.texts()
    assert "7/10" in text and "9/10" in text and "Интегралы" in text and "Пределы" in text


async def test_migration_adds_columns(tmp_path):
    import aiosqlite
    path = str(tmp_path / "old.db")
    async with aiosqlite.connect(path) as c:                                    # база до этапа 4
        await c.executescript("CREATE TABLE blitz_runs (id INTEGER PRIMARY KEY, student_id INT, started_at TEXT,"
                              " finished_at TEXT, score INT, total INT, time_limit INT);"
                              "CREATE TABLE attempts (id INTEGER PRIMARY KEY, session_id INT, checkpoint_idx INT,"
                              " checkpoint_type TEXT, raw_input TEXT, verdict TEXT, input_method TEXT, ocr_raw TEXT,"
                              " ocr_edited INT, hint_used INT, attempt_no INT, latency_ms INT, created_at TEXT);")
    d = await DB.open(path)
    for table, col in (("blitz_runs", "section"), ("attempts", "expected")):
        async with d.conn.execute(f"PRAGMA table_info({table})") as cur:
            assert col in {r[1] for r in await cur.fetchall()}
    await d.close()


# --- лог шагов предела ------------------------------------------------------------------------------

def test_limit_steps_logged_with_role_and_expected():
    i = build(BANK["lim_diff_exp_01"], {"a": 1})
    s = TrainSession(i, seed=1, check=check)
    wrong = s.current().options.index("0/0")
    out = s.answer_button(wrong)
    assert out.log["checkpoint_type"] == "type" and out.log["expected"] == "∞−∞" and out.log["raw_input"] == "0/0"
    s.answer_button(right(s, i))
    assert s.answer_button(right(s, i)).log["checkpoint_type"] == "reduce"


def test_integral_log_unchanged():
    from tests.test_flow import session
    inst, s = session()
    out = s.answer_button(0)
    assert out.log["checkpoint_type"] == "choice" and "expected" not in out.log


# --- /export --------------------------------------------------------------------------------------

async def test_export_sheets_and_types_matrix(db):
    sid = await db.add_student("Иванов Иван", 1)
    i = generate(BANK["lim_diff_exp_01"], 0)
    iid = await db.save_instance(i, "limits.lhopital", "B")
    ses = await db.start_session(sid, iid, "train")
    for raw, verdict in (("0/0", "wrong"), ("∞−∞", "ok")):
        await db.log_attempt(ses, checkpoint_idx=0, checkpoint_type="type", raw_input=raw, verdict=verdict,
                             input_method="button", hint_used=False, attempt_no=1, latency_ms=0, expected="∞−∞")
    await db.finish_session(ses, "solved")
    await run(db, sid, 8, LIMITS)
    wb = load_workbook(BytesIO(build_xlsx(await collect(db, BANK, dt.date(2026, 11, 28)))))
    assert {"Блиц — интегралы", "Блиц — пределы", "Типы неопределённостей"} <= set(wb.sheetnames)   # «:» Excel запрещает
    ws = wb["Типы неопределённостей"]
    rows = list(ws.iter_rows(values_only=True))
    header = rows[0]
    row = next(r for r in rows if r[0] == "∞−∞")
    assert row[header.index("0/0")] == 1 and row[header.index("∞−∞")] == 1
    students = list(wb["Студенты"].iter_rows(values_only=True))
    assert "Лучший блиц: пределы" in students[0]
