from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import load_workbook

from bot.db import DB
from bot.export import SHEETS, build_xlsx, collect
from bot.topics import TOPIC_NAMES, method_options
from core.generator import generate
from core.templates import load_templates

BANK = load_templates(Path(__file__).resolve().parent.parent / "data" / "templates")
TODAY = date(2026, 11, 25)
UNI, TAN, PARTS = "int_trig_universal_01", "int_trig_tan_01", "int_parts_poly_exp_01"
TRIG, PARTS_T = "indef_integral.trig", "indef_integral.parts"


def d(k):
    return TODAY - timedelta(days=k)


async def task(db, student, tid, mode, status, attempts, when=TODAY):
    """attempts: [(checkpoint_idx, verdict, raw_input, hint_used), …]"""
    tpl = BANK[tid]
    iid = await db.save_instance(generate(tpl, 0), tpl.topic, tpl.difficulty)
    sid = await db.start_session(student, iid, mode)
    for k, (cp, verdict, raw, hint) in enumerate(attempts):
        ctype = "blitz" if mode == "blitz" else ("method" if cp == -1 else "x")
        await db.log_attempt(sid, checkpoint_idx=cp, checkpoint_type=ctype, raw_input=raw, verdict=verdict,
                             input_method="button", hint_used=hint, attempt_no=k + 1, latency_ms=0)
    ts = datetime(when.year, when.month, when.day, 12, 0).isoformat() + "+03:00"
    await db.conn.execute("UPDATE attempts SET created_at=? WHERE session_id=?", (ts, sid))
    await db.conn.commit()
    if status:
        await db.finish_session(sid, status)


def a(cp, verdict, raw="", hint=False):
    return (cp, verdict, raw, hint)


@pytest.fixture
async def data():
    db = await DB.open(":memory:")
    ivan = await db.add_student("Иванов Иван", 1)
    petr = await db.add_student("Петров Пётр", 2)
    await db.add_student("Сидоров Сидор", 3)                         # ни разу не заходил
    # Иванов
    await task(db, ivan, UNI, "train", "solved",
               [a(0, "ok"), a(1, "wrong", "1/(t^2+9)"), a(1, "ok"), a(2, "parse_error", "atan("), a(2, "ok")])
    await task(db, ivan, UNI, "train", "revealed",
               [a(0, "wrong", "t = cos x", hint=True), a(0, "wrong", "t = cos x"), a(0, "wrong", " t = cos x "),
                a(1, "revealed"), a(2, "no_const", "F"), a(2, "ok")], when=d(1))
    await task(db, ivan, PARTS, "train", "dropped", [a(0, "ok")], when=d(2))
    await task(db, ivan, UNI, "blitz", "solved", [a(0, "ok", "универсальная замена")], when=d(3))
    await task(db, ivan, PARTS, "blitz", "revealed", [a(0, "wrong", "универсальная замена")], when=d(3))
    await task(db, ivan, UNI, "blitz", "revealed", [a(0, "skip", "")], when=d(3))
    await task(db, ivan, TAN, "train", "solved",                     # «Вразнобой»: сначала метод
               [a(-1, "wrong", "по частям"), a(-1, "ok", "замена t = tg x"), a(0, "ok"), a(1, "ok"), a(2, "ok")])
    for score in (7, 9):
        r = await db.start_blitz_run(ivan)
        await db.finish_blitz_run(r, score, 10)
    await db.start_blitz_run(ivan)                                  # недоигранный
    # Петров
    await task(db, petr, UNI, "train", "solved", [a(0, "ok"), a(1, "ok"), a(2, "wrong", "x + C"), a(2, "ok")])
    try:
        yield await collect(db, BANK, today=TODAY)
    finally:
        await db.close()                                             # иначе поток aiosqlite вешает pytest


# --- Студенты ----------------------------------------------------------------

async def test_students(data):
    assert data.topics == list(TOPIC_NAMES)
    ivan, petr, sid = data.students                                 # по алфавиту, все трое
    assert (ivan.name, petr.name, sid.name) == ("Иванов Иван", "Петров Пётр", "Сидоров Сидор")
    assert (ivan.days_active, ivan.streak, ivan.solved, ivan.finished) == (4, 4, 2, 3)
    assert ivan.first_try == pytest.approx(0.5)                     # 5 из 10 шагов, брошенная — не в счёт
    assert ivan.topic_first_try[TRIG] == pytest.approx(0.5)
    assert ivan.topic_first_try[PARTS_T] is None                    # только брошенная задача
    assert (ivan.best_blitz, ivan.blitz_runs, ivan.last_active) == ("9/10", 2, TODAY)
    assert (petr.days_active, petr.streak, petr.solved, petr.finished) == (1, 1, 1, 1)
    assert petr.first_try == pytest.approx(2 / 3) and petr.best_blitz is None
    assert (sid.days_active, sid.streak, sid.solved, sid.finished, sid.first_try,
            sid.best_blitz, sid.blitz_runs, sid.last_active) == (0, 0, 0, 0, None, None, 0, None)


# --- Шаблоны -----------------------------------------------------------------

async def test_checkpoints(data):
    rows = {(r.template_id, r.step): r for r in data.checkpoints}
    assert [(r.template_id, r.step) for r in data.checkpoints] == [
        (PARTS, 1), (TAN, 1), (TAN, 2), (TAN, 3), (UNI, 1), (UNI, 2), (UNI, 3)]   # без шага «метод», без блица
    u1, u2, u3 = rows[(UNI, 1)], rows[(UNI, 2)], rows[(UNI, 3)]
    assert (u1.topic, u1.difficulty) == (TRIG, "B")
    assert (u1.attempts, u1.parse_errors) == (5, 0)
    assert u1.error_rate == pytest.approx(3 / 5)
    assert u1.hint_rate == pytest.approx(1 / 3) and u1.revealed_rate == pytest.approx(1 / 3)  # 3 ошибки → показан
    assert u1.top_wrong == [("t = cos x", 3)]                        # пробелы по краям не дробят ввод
    assert u2.attempts == 3 and u2.error_rate == pytest.approx(1 / 3)
    assert u2.revealed_rate == pytest.approx(1 / 3)                  # кнопка «Показать ответ»
    assert (u3.attempts, u3.parse_errors) == (5, 1) and u3.error_rate == pytest.approx(2 / 5)
    assert u3.top_wrong == [("F", 1), ("x + C", 1)]                  # равные — по алфавиту
    assert u3.revealed_rate == 0
    p1 = rows[(PARTS, 1)]
    assert (p1.attempts, p1.error_rate) == (1, 0)                    # брошенная задача учтена


# --- Матрицы -----------------------------------------------------------------

async def test_blitz_matrix(data):
    m = data.blitz
    assert m.methods == list(method_options(BANK))
    assert m.counts == {("универсальная замена", "универсальная замена"): 1,
                        ("универсальная замена", None): 1,
                        ("по частям", "универсальная замена"): 1}
    assert m.accuracy("универсальная замена") == pytest.approx(0.5)
    assert m.accuracy("по частям") == 0
    assert m.accuracy("выделение полного квадрата") is None


async def test_mix_matrix(data):
    m = data.mix
    assert m.counts == {("замена t = tg x", "по частям"): 1, ("замена t = tg x", "замена t = tg x"): 1}
    assert m.accuracy("замена t = tg x") == pytest.approx(0.5)


# --- XLSX --------------------------------------------------------------------

def header(ws):
    return [c.value for c in ws[1]]


async def test_build_xlsx(data):
    wb = load_workbook(BytesIO(build_xlsx(data)))
    assert wb.sheetnames == SHEETS

    st = wb["Студенты"]
    h = header(st)
    assert h[0] == "ФИО" and "Дней активности" in h and "Тригонометрические" in h and "Лучший блиц" in h
    assert [st.cell(r, 1).value for r in range(2, 5)] == ["Иванов Иван", "Петров Пётр", "Сидоров Сидор"]
    pct = st.cell(2, h.index("% с 1-й попытки") + 1)
    assert pct.value == pytest.approx(0.5) and "%" in pct.number_format
    assert st.cell(4, h.index("% с 1-й попытки") + 1).value is None   # нет данных — пустая ячейка, не 0
    assert st.freeze_panes == "B2"

    tp = wb["Шаблоны"]
    assert header(tp)[:5] == ["Шаблон", "Тема", "Уровень", "Шаг №", "Тип"]
    assert tp.max_row == 1 + len(data.checkpoints)
    assert tp.cell(2, 2).value == "По частям"                         # тема — по-русски

    bl = wb["Блиц"]
    h = header(bl)
    assert "не успел" in h and h[-1] == "% верных"
    rows = {bl.cell(r, 1).value: r for r in range(2, bl.max_row + 1)}
    assert bl.cell(rows["универсальная замена"], h.index("универсальная замена") + 1).value == 1
    assert bl.cell(rows["универсальная замена"], h.index("не успел") + 1).value == 1

    assert "не успел" not in header(wb["Вразнобой"])                  # там таймера нет


async def test_build_xlsx_empty():
    db = await DB.open(":memory:")
    try:
        data = await collect(db, BANK, today=TODAY)
    finally:
        await db.close()
    wb = load_workbook(BytesIO(build_xlsx(data)))
    assert wb.sheetnames == SHEETS
    assert wb["Студенты"].max_row == 1 and wb["Шаблоны"].max_row == 1
