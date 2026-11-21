import csv
import io
import zipfile

import aiosqlite
import pytest

from bot.db import DB, OcrRow
from bot.ocrdump import COLUMNS, CSV_NAME, build_ocr_dump


@pytest.fixture
async def db():
    d = await DB.open(":memory:")
    yield d
    await d.close()


# --- миграция старой схемы ---------------------------------------------------

async def test_migration_adds_columns(tmp_path):
    path = str(tmp_path / "old.db")
    conn = await aiosqlite.connect(path)
    await conn.execute("CREATE TABLE ocr_requests (id INTEGER PRIMARY KEY, student_id INT, file_id TEXT, "
                       "recognized TEXT, status TEXT, created_at TEXT)")
    await conn.execute("INSERT INTO ocr_requests (student_id, file_id, recognized, status, created_at) "
                       "VALUES (1, 'f1', 'x + C', 'ok', '2026-11-20T12:00:00+03:00')")
    await conn.commit()
    await conn.close()

    d = await DB.open(path)
    async with d.conn.execute("PRAGMA table_info(ocr_requests)") as cur:
        cols = {r[1] for r in await cur.fetchall()}
    assert {"markdown", "attempt_id"} <= cols
    async with d.conn.execute("SELECT file_id, recognized, markdown FROM ocr_requests") as cur:
        assert await cur.fetchall() == [("f1", "x + C", None)]       # старые данные целы
    await d.close()
    d = await DB.open(path)                                           # повторное открытие не падает
    await d.close()


# --- запись и выборка --------------------------------------------------------

async def test_ocr_rows(db):
    sid = await db.add_student("Иванов Иван", 1)
    ok = await db.log_ocr(sid, "f_ok", "x + C", "ok", markdown="$x + C$")
    bad = await db.log_ocr(sid, "f_bad", None, "unreadable", markdown="## какой-то текст")
    err = await db.log_ocr(sid, "f_err", None, "error")
    att = await db.log_attempt(5, checkpoint_idx=2, checkpoint_type="antiderivative", raw_input="x + 1 + C",
                               verdict="wrong", input_method="photo", hint_used=False, attempt_no=1,
                               latency_ms=0, ocr_raw="x + C", ocr_edited=True)
    await db.link_ocr_attempt(ok, att)

    rows = await db.ocr_dump_rows()
    assert [r.id for r in rows] == [err, bad, ok]                    # свежие первыми
    r_ok = rows[2]
    assert (r_ok.student, r_ok.status, r_ok.markdown, r_ok.recognized) == ("Иванов Иван", "ok", "$x + C$", "x + C")
    assert (r_ok.final_input, r_ok.edited, r_ok.verdict) == ("x + 1 + C", True, "wrong")
    r_bad = rows[1]
    assert r_bad.markdown == "## какой-то текст" and r_bad.recognized is None
    assert (r_bad.final_input, r_bad.edited, r_bad.verdict) == (None, None, None)
    assert len(await db.ocr_dump_rows(limit=2)) == 2


# --- сборка архива -----------------------------------------------------------

def row(i, **kw):
    base = dict(id=i, created_at="2026-11-20T12:00:00+03:00", student="Иванов Иван", file_id=f"f{i}",
                status="ok", markdown="$x$", recognized="x", final_input="x + C", edited=True, verdict="ok")
    return OcrRow(**{**base, **kw})


def test_build_dump():
    rows = [row(1), row(2, status="unreadable", recognized=None, final_input=None, edited=None, verdict=None)]
    data = build_ocr_dump(rows, {1: b"\xff\xd8jpeg1", 2: None})
    z = zipfile.ZipFile(io.BytesIO(data))
    assert set(z.namelist()) == {CSV_NAME, "photos/1.jpg"}           # нескачанного фото нет в архиве
    assert z.read("photos/1.jpg") == b"\xff\xd8jpeg1"
    text = z.read(CSV_NAME).decode("utf-8-sig")                      # BOM — чтобы Excel открыл кириллицу
    table = list(csv.DictReader(io.StringIO(text)))
    assert list(table[0]) == COLUMNS
    assert table[0]["photo"] == "photos/1.jpg" and table[0]["student"] == "Иванов Иван"
    assert table[1]["photo"] == "не скачалось" and table[1]["edited"] == ""


def test_build_dump_empty():
    z = zipfile.ZipFile(io.BytesIO(build_ocr_dump([], {})))
    assert z.namelist() == [CSV_NAME]
