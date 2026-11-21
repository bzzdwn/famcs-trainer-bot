"""SQLite (aiosqlite): схема из ТЗ и запросы бота."""
import json
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import aiosqlite

from core.generator import Instance

SCHEMA = """
CREATE TABLE IF NOT EXISTS students  (id INTEGER PRIMARY KEY, full_name TEXT UNIQUE, tg_id INTEGER UNIQUE,
                                      registered_at TEXT);
CREATE TABLE IF NOT EXISTS instances (id INTEGER PRIMARY KEY, template_id TEXT, topic TEXT, difficulty TEXT,
                                      params_json TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS sessions  (id INTEGER PRIMARY KEY, student_id INT, instance_id INT, mode TEXT,
                                      started_at TEXT, finished_at TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS attempts  (id INTEGER PRIMARY KEY, session_id INT, checkpoint_idx INT,
                                      checkpoint_type TEXT, raw_input TEXT, verdict TEXT,
                                      input_method TEXT, ocr_raw TEXT, ocr_edited INT,
                                      hint_used INT, attempt_no INT, latency_ms INT, created_at TEXT);
CREATE TABLE IF NOT EXISTS ocr_requests (id INTEGER PRIMARY KEY, student_id INT, file_id TEXT,
                                         recognized TEXT, status TEXT, created_at TEXT,
                                         markdown TEXT, attempt_id INT);
CREATE TABLE IF NOT EXISTS blitz_runs (id INTEGER PRIMARY KEY, student_id INT, started_at TEXT, finished_at TEXT,
                                       score INT, total INT, time_limit INT);
"""


TZ = ZoneInfo("Europe/Minsk")


def _now() -> str:
    """Время по Минску с поясом: дни в статистике не зависят от часового пояса сервера."""
    return datetime.now(TZ).isoformat(timespec="seconds")


@dataclass(frozen=True)
class OcrRow:
    id: int
    created_at: str
    student: str | None
    file_id: str
    status: str                # ok | unreadable | error
    markdown: str | None       # сырой ответ Яндекса
    recognized: str | None     # после конвертера, в синтаксисе ввода
    final_input: str | None    # что ушло на проверку (после правки)
    edited: bool | None        # None — на проверку не отправляли
    verdict: str | None


class DuplicateStudent(ValueError):
    """ФИО или tg_id уже заняты."""


@dataclass(frozen=True)
class Student:
    id: int
    full_name: str
    tg_id: int
    registered_at: str


class DB:
    def __init__(self, conn: aiosqlite.Connection):
        self.conn = conn

    @classmethod
    async def open(cls, path: str) -> "DB":
        """Открыть (":memory:" для тестов) и создать схему."""
        conn = await aiosqlite.connect(path)
        await conn.executescript(SCHEMA)
        await cls._migrate(conn)
        await conn.commit()
        return cls(conn)

    # колонки, добавленные после первых запусков: CREATE TABLE IF NOT EXISTS их в старую базу не добавит
    MIGRATIONS = {"ocr_requests": {"markdown": "TEXT", "attempt_id": "INT"},
                  "blitz_runs": {"time_limit": "INT"}}          # старые серии: NULL = 10 с

    @classmethod
    async def _migrate(cls, conn: aiosqlite.Connection) -> None:
        for table, cols in cls.MIGRATIONS.items():
            async with conn.execute(f"PRAGMA table_info({table})") as cur:
                have = {r[1] for r in await cur.fetchall()}
            for col, typ in cols.items():
                if col not in have:
                    await conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")

    async def close(self) -> None:
        await self.conn.close()

    async def _exec(self, sql: str, *args) -> aiosqlite.Cursor:
        cur = await self.conn.execute(sql, args)
        await self.conn.commit()
        return cur

    # --- студенты
    async def add_student(self, full_name: str, tg_id: int) -> int:
        try:
            cur = await self._exec("INSERT INTO students (full_name, tg_id, registered_at) VALUES (?, ?, ?)",
                                   full_name, tg_id, _now())
        except aiosqlite.IntegrityError:
            raise DuplicateStudent(full_name) from None
        return cur.lastrowid

    async def get_student_by_tg(self, tg_id: int) -> Student | None:
        async with self.conn.execute("SELECT id, full_name, tg_id, registered_at FROM students WHERE tg_id=?",
                                     (tg_id,)) as cur:
            row = await cur.fetchone()
        return Student(*row) if row else None

    async def list_students(self) -> list[Student]:
        async with self.conn.execute("SELECT id, full_name, tg_id, registered_at FROM students") as cur:
            rows = await cur.fetchall()
        return sorted((Student(*r) for r in rows), key=lambda s: s.full_name.casefold())

    async def rename_student(self, student_id: int, new_name: str) -> bool:
        """False — нет такого id; занятое имя → DuplicateStudent."""
        try:
            cur = await self._exec("UPDATE students SET full_name=? WHERE id=?", new_name, student_id)
        except aiosqlite.IntegrityError:
            raise DuplicateStudent(new_name) from None
        return cur.rowcount > 0

    async def delete_student(self, student_id: int) -> bool:
        cur = await self._exec("DELETE FROM students WHERE id=?", student_id)
        return cur.rowcount > 0

    # --- задачи
    async def save_instance(self, inst: Instance, topic: str, difficulty: str) -> int:
        cur = await self._exec(
            "INSERT INTO instances (template_id, topic, difficulty, params_json, created_at) VALUES (?, ?, ?, ?, ?)",
            inst.template_id, topic, difficulty, json.dumps(inst.params, sort_keys=True), _now())
        return cur.lastrowid

    async def start_session(self, student_id: int, instance_id: int, mode: str) -> int:
        cur = await self._exec("INSERT INTO sessions (student_id, instance_id, mode, started_at) VALUES (?, ?, ?, ?)",
                               student_id, instance_id, mode, _now())
        return cur.lastrowid

    async def finish_session(self, session_id: int, status: str) -> None:
        await self._exec("UPDATE sessions SET status=?, finished_at=? WHERE id=?", status, _now(), session_id)

    async def log_attempt(self, session_id: int, *, checkpoint_idx: int, checkpoint_type: str,
                          raw_input: str, verdict: str, input_method: str, hint_used: bool,
                          attempt_no: int, latency_ms: int,
                          ocr_raw: str | None = None, ocr_edited: bool = False) -> int:
        cur = await self._exec(
            "INSERT INTO attempts (session_id, checkpoint_idx, checkpoint_type, raw_input, verdict, input_method, "
            "ocr_raw, ocr_edited, hint_used, attempt_no, latency_ms, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            session_id, checkpoint_idx, checkpoint_type, raw_input, verdict, input_method,
            ocr_raw, int(ocr_edited), int(hint_used), attempt_no, latency_ms, _now())
        return cur.lastrowid

    # --- блиц
    async def start_blitz_run(self, student_id: int, time_limit: int = 10) -> int:
        cur = await self._exec("INSERT INTO blitz_runs (student_id, started_at, time_limit) VALUES (?, ?, ?)",
                               student_id, _now(), time_limit)
        return cur.lastrowid

    async def finish_blitz_run(self, run_id: int, score: int, total: int) -> None:
        await self._exec("UPDATE blitz_runs SET finished_at=?, score=?, total=? WHERE id=?",
                         _now(), score, total, run_id)

    # --- фото
    async def log_ocr(self, student_id: int, file_id: str, recognized: str | None, status: str,
                      markdown: str | None = None) -> int:
        """status: ok | unreadable | error. Сами фото не храним — только file_id Telegram."""
        cur = await self._exec("INSERT INTO ocr_requests (student_id, file_id, recognized, status, created_at, markdown) "
                               "VALUES (?, ?, ?, ?, ?, ?)", student_id, file_id, recognized, status, _now(), markdown)
        return cur.lastrowid

    async def link_ocr_attempt(self, ocr_id: int, attempt_id: int) -> None:
        await self._exec("UPDATE ocr_requests SET attempt_id=? WHERE id=?", attempt_id, ocr_id)

    async def ocr_dump_rows(self, limit: int = 200) -> list["OcrRow"]:
        """Последние limit распознаваний (свежие первыми) с итоговой попыткой, если её отправили."""
        async with self.conn.execute(
                "SELECT o.id, o.created_at, s.full_name, o.file_id, o.status, o.markdown, o.recognized, "
                "a.raw_input, a.ocr_edited, a.verdict "
                "FROM ocr_requests o LEFT JOIN students s ON s.id = o.student_id "
                "LEFT JOIN attempts a ON a.id = o.attempt_id ORDER BY o.id DESC LIMIT ?", (limit,)) as cur:
            rows = await cur.fetchall()
        return [OcrRow(*r[:8], None if r[8] is None else bool(r[8]), r[9]) for r in rows]

    async def count_ocr_today(self, student_id: int, today=None) -> int:
        """Запросов к OCR за сегодня (по Минску) — для лимита."""
        today = today or datetime.now(TZ).date()
        async with self.conn.execute("SELECT created_at FROM ocr_requests WHERE student_id=?", (student_id,)) as cur:
            rows = await cur.fetchall()
        def day(ts: str):
            dt = datetime.fromisoformat(ts)
            return (dt if dt.tzinfo else dt.replace(tzinfo=TZ)).astimezone(TZ).date()
        return sum(day(ts) == today for (ts,) in rows)

    async def abandon_unfinished(self) -> int:
        """Незавершённые сессии → abandoned (при старте бота). Возвращает их число."""
        cur = await self._exec("UPDATE sessions SET status='abandoned', finished_at=? WHERE finished_at IS NULL",
                               _now())
        return cur.rowcount
