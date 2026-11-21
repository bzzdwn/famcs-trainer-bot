import json
from datetime import datetime
from pathlib import Path

import pytest

from bot.db import DB, DuplicateStudent
from core.generator import generate
from core.templates import load_templates

BANK = load_templates(Path(__file__).resolve().parent.parent / "data" / "templates")


@pytest.fixture
async def db():
    d = await DB.open(":memory:")
    yield d
    await d.close()


async def rows(db, sql, *args):
    async with db.conn.execute(sql, args) as cur:
        return await cur.fetchall()


async def test_schema(db):
    names = {r[0] for r in await rows(db, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"students", "instances", "sessions", "attempts"} <= names


async def test_open_twice_is_idempotent(tmp_path):
    path = str(tmp_path / "bot.db")
    d = await DB.open(path)
    await d.add_student("Иванов Иван", 1)
    await d.close()
    d = await DB.open(path)
    assert (await d.get_student_by_tg(1)).full_name == "Иванов Иван"
    await d.close()


# --- студенты --------------------------------------------------------------

async def test_add_and_get(db):
    sid = await db.add_student("Иванов Иван", 111)
    st = await db.get_student_by_tg(111)
    assert st.id == sid and st.full_name == "Иванов Иван" and st.tg_id == 111
    datetime.fromisoformat(st.registered_at)
    assert await db.get_student_by_tg(222) is None


async def test_duplicates(db):
    await db.add_student("Иванов Иван", 111)
    with pytest.raises(DuplicateStudent):
        await db.add_student("Иванов Иван", 222)
    with pytest.raises(DuplicateStudent):
        await db.add_student("Петров Пётр", 111)


async def test_list_sorted(db):
    await db.add_student("Сидоров Сидор", 3)
    await db.add_student("Иванов Иван", 1)
    assert [s.full_name for s in await db.list_students()] == ["Иванов Иван", "Сидоров Сидор"]


async def test_rename(db):
    a = await db.add_student("Иванов Иван", 1)
    await db.add_student("Петров Пётр", 2)
    assert await db.rename_student(a, "Иванов Иоанн") is True
    assert (await db.get_student_by_tg(1)).full_name == "Иванов Иоанн"
    with pytest.raises(DuplicateStudent):
        await db.rename_student(a, "Петров Пётр")
    assert await db.rename_student(999, "Кто Тут") is False


async def test_delete(db):
    a = await db.add_student("Иванов Иван", 1)
    assert await db.delete_student(a) is True
    assert await db.get_student_by_tg(1) is None
    assert await db.delete_student(a) is False


# --- задачи, сессии, попытки ----------------------------------------------

async def test_instance(db):
    inst = generate(BANK["int_trig_universal_01"], 0)
    iid = await db.save_instance(inst, "indef_integral.trig", "B")
    (tid, topic, diff, pj, created), = await rows(
        db, "SELECT template_id, topic, difficulty, params_json, created_at FROM instances WHERE id=?", iid)
    assert (tid, topic, diff) == ("int_trig_universal_01", "indef_integral.trig", "B")
    assert json.loads(pj) == inst.params
    datetime.fromisoformat(created)


async def test_session_lifecycle(db):
    sid = await db.add_student("Иванов Иван", 1)
    iid = await db.save_instance(generate(BANK["int_parts_poly_exp_01"], 0), "indef_integral.parts", "A")
    ses = await db.start_session(sid, iid, "train")
    (status, finished), = await rows(db, "SELECT status, finished_at FROM sessions WHERE id=?", ses)
    assert status is None and finished is None
    await db.finish_session(ses, "solved")
    (status, finished), = await rows(db, "SELECT status, finished_at FROM sessions WHERE id=?", ses)
    assert status == "solved" and finished


async def test_log_attempt(db):
    aid = await db.log_attempt(7, checkpoint_idx=2, checkpoint_type="antiderivative", raw_input="x + C",
                               verdict="wrong", input_method="text", hint_used=True, attempt_no=2, latency_ms=150)
    (r,) = await rows(db, "SELECT session_id, checkpoint_idx, checkpoint_type, raw_input, verdict, input_method, "
                          "ocr_raw, ocr_edited, hint_used, attempt_no, latency_ms, created_at "
                          "FROM attempts WHERE id=?", aid)
    assert r[:11] == (7, 2, "antiderivative", "x + C", "wrong", "text", None, 0, 1, 2, 150)
    datetime.fromisoformat(r[11])


async def test_abandon_unfinished(db):
    s1 = await db.start_session(1, 1, "train")
    s2 = await db.start_session(1, 2, "train")
    s3 = await db.start_session(1, 3, "train")
    await db.finish_session(s3, "solved")
    assert await db.abandon_unfinished() == 2
    st = dict(await rows(db, "SELECT id, status FROM sessions"))
    assert st == {s1: "abandoned", s2: "abandoned", s3: "solved"}
    assert await db.abandon_unfinished() == 0


async def test_dropped_and_direct_mode(db):
    ses = await db.start_session(1, 1, "train_direct")
    await db.finish_session(ses, "dropped")
    (mode, status), = await rows(db, "SELECT mode, status FROM sessions WHERE id=?", ses)
    assert (mode, status) == ("train_direct", "dropped")
    assert await db.abandon_unfinished() == 0          # dropped — завершённая сессия


async def test_blitz_runs(db):
    run = await db.start_blitz_run(5)
    (sid, started, finished, score), = await rows(
        db, "SELECT student_id, started_at, finished_at, score FROM blitz_runs WHERE id=?", run)
    assert (sid, finished, score) == (5, None, None) and started
    await db.finish_blitz_run(run, 8, 10)
    (finished, score, total), = await rows(db, "SELECT finished_at, score, total FROM blitz_runs WHERE id=?", run)
    assert finished and (score, total) == (8, 10)


async def test_timestamps_have_timezone(db):
    await db.add_student("Иванов Иван", 1)
    st = await db.get_student_by_tg(1)
    assert datetime.fromisoformat(st.registered_at).tzinfo is not None   # Минск, не «время сервера»


async def test_ocr_log_and_daily_count(db):
    from datetime import date
    a = await db.log_ocr(1, "file_a", "x + C", "ok")
    await db.log_ocr(1, "file_b", None, "unreadable")
    await db.log_ocr(2, "file_c", "x", "ok")                       # чужой
    old = await db.log_ocr(1, "file_d", "x", "ok")
    await db.conn.execute("UPDATE ocr_requests SET created_at='2026-11-01T12:00:00+03:00' WHERE id=?", (old,))
    await db.conn.commit()
    (fid, rec, status), = await rows(db, "SELECT file_id, recognized, status FROM ocr_requests WHERE id=?", a)
    assert (fid, rec, status) == ("file_a", "x + C", "ok")
    from bot.db import TZ
    today = datetime.now(TZ).date()
    assert await db.count_ocr_today(1, today) == 2                # unreadable тоже тратит лимит
    assert await db.count_ocr_today(2, today) == 1
    assert await db.count_ocr_today(1, date(2026, 11, 1)) == 1
