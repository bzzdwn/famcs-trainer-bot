from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from bot.db import DB
from bot.stats import local_date, streak, student_stats
from core.generator import generate
from core.templates import load_templates

BANK = load_templates(Path(__file__).resolve().parent.parent / "data" / "templates")
TODAY = date(2026, 11, 25)


# --- серия дней --------------------------------------------------------------

def d(k):
    return TODAY - timedelta(days=k)


@pytest.mark.parametrize("days, want", [
    (set(), 0),
    ({d(0)}, 1),
    ({d(0), d(1), d(2)}, 3),
    ({d(1), d(2)}, 2),              # сегодня ещё не заходил — серия жива
    ({d(2), d(3)}, 0),              # вчера пропустил — серии нет
    ({d(0), d(2), d(3)}, 1),        # разрыв
    ({d(0), d(1), d(5), d(6)}, 2),
])
def test_streak(days, want):
    assert streak(days, TODAY) == want


@pytest.mark.parametrize("iso, want", [
    ("2026-11-20T23:30:00+03:00", date(2026, 11, 20)),
    ("2026-11-20T21:30:00+00:00", date(2026, 11, 21)),   # 00:30 по Минску
    ("2026-11-20T20:59:00+00:00", date(2026, 11, 20)),   # 23:59 по Минску
])
def test_local_date(iso, want):
    assert local_date(iso) == want


# --- /me на заготовленных данных --------------------------------------------

@pytest.fixture
async def db():
    d_ = await DB.open(":memory:")
    yield d_
    await d_.close()


async def task(db, student, tid, mode, status, attempts, when: date = TODAY):
    """Сессия с попытками [(checkpoint_idx, verdict), ...]; время попыток — полдень дня when по Минску."""
    tpl = BANK[tid]
    iid = await db.save_instance(generate(tpl, 0), tpl.topic, tpl.difficulty)
    sid = await db.start_session(student, iid, mode)
    for k, (cp, verdict) in enumerate(attempts):
        await db.log_attempt(sid, checkpoint_idx=cp, checkpoint_type="x", raw_input="", verdict=verdict,
                             input_method="text", hint_used=False, attempt_no=k + 1, latency_ms=0)
    ts = datetime(when.year, when.month, when.day, 12, 0).isoformat() + "+03:00"
    await db.conn.execute("UPDATE attempts SET created_at=? WHERE session_id=?", (ts, sid))
    await db.conn.commit()
    if status:
        await db.finish_session(sid, status)
    return sid


async def test_student_stats(db):
    me = await db.add_student("Иванов Иван", 1)
    other = await db.add_student("Петров Пётр", 2)
    # trig, решена: cp0 с 1-й; cp1 со 2-й; cp2 — опечатка (не в счёт), затем верно → с 1-й
    await task(db, me, "int_trig_universal_01", "train", "solved",
               [(0, "ok"), (1, "wrong"), (1, "ok"), (2, "parse_error"), (2, "ok")])
    # parts, «сразу ответ»: финал с первой попытки
    await task(db, me, "int_parts_poly_exp_01", "train_direct", "solved", [(1, "ok")], when=d(1))
    # parts: на cp0 показал ответ сразу
    await task(db, me, "int_parts_poly_exp_01", "train", "revealed", [(0, "revealed")], when=d(1))
    # брошенная и прерванная рестартом — не в статистике (но активность в день засчитана)
    await task(db, me, "int_trig_odd_sin_01", "train", "dropped", [(0, "wrong")], when=d(2))
    await task(db, me, "int_trig_odd_sin_01", "train", None, [(0, "wrong")], when=d(3))
    # блиц — не в % точек
    await task(db, me, "int_trig_odd_sin_01", "blitz", "revealed", [(0, "wrong")], when=d(5))
    # чужие данные
    await task(db, other, "int_trig_universal_01", "train", "solved", [(0, "ok"), (1, "ok"), (2, "ok")])

    r1 = await db.start_blitz_run(me)
    await db.finish_blitz_run(r1, 7, 10)
    r2 = await db.start_blitz_run(me)
    await db.finish_blitz_run(r2, 9, 10)
    r5 = await db.start_blitz_run(me, time_limit=30)                 # тот же счёт, но с бо́льшим временем
    await db.finish_blitz_run(r5, 9, 10)
    r6 = await db.start_blitz_run(me, time_limit=5)                  # меньше время, но и счёт ниже
    await db.finish_blitz_run(r6, 8, 10)
    await db.start_blitz_run(me)                     # недоигранный — не в счёт
    r4 = await db.start_blitz_run(other)
    await db.finish_blitz_run(r4, 10, 10)

    st = await student_stats(db, me, today=TODAY)
    assert st.topics == {"indef_integral.trig": (1, 1), "indef_integral.parts": (1, 2)}
    assert (st.points, st.first_try) == (5, 3)         # trig: 3 точки, 2 с 1-й; direct: 1 из 1; revealed: 0 из 1
    assert st.first_try_pct == pytest.approx(60.0)
    assert st.best_blitz == (9, 10, 10)                             # лучший счёт; при равенстве — меньшее время
    assert st.streak == 4                               # d0..d3 подряд, d5 отдельно


async def test_empty_student(db):
    me = await db.add_student("Иванов Иван", 1)
    st = await student_stats(db, me, today=TODAY)
    assert st.topics == {} and (st.points, st.first_try) == (0, 0)
    assert st.first_try_pct is None and st.best_blitz is None and st.streak == 0
