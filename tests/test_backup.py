import gzip
import sqlite3
from datetime import datetime

from bot.backup import backup_name, make_backup


def test_backup_roundtrip(tmp_path):
    db = tmp_path / "bot.db"
    live = sqlite3.connect(db)                                   # бот держит базу открытой
    live.execute("CREATE TABLE students (id INTEGER PRIMARY KEY, full_name TEXT)")
    live.executemany("INSERT INTO students (full_name) VALUES (?)", [("Иванов Иван",), ("Петров Пётр",)])
    live.commit()
    data = make_backup(str(db))
    restored = tmp_path / "restored.db"
    restored.write_bytes(gzip.decompress(data))
    rows = sqlite3.connect(restored).execute("SELECT full_name FROM students ORDER BY id").fetchall()
    assert rows == [("Иванов Иван",), ("Петров Пётр",)]
    live.close()


def test_backup_uncommitted_not_included(tmp_path):
    db = tmp_path / "bot.db"
    live = sqlite3.connect(db)
    live.execute("CREATE TABLE t (x INT)")
    live.commit()
    live.execute("INSERT INTO t VALUES (1)")                     # транзакция не закоммичена
    restored = tmp_path / "r.db"
    restored.write_bytes(gzip.decompress(make_backup(str(db))))
    assert sqlite3.connect(restored).execute("SELECT COUNT(*) FROM t").fetchone() == (0,)
    live.close()


def test_backup_name():
    assert backup_name(datetime(2026, 11, 25, 3, 0)) == "bot_2026-11-25_0300.db.gz"
