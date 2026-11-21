"""Ежедневный бэкап БД админу в Telegram. Запуск: venv/bin/python -m bot.backup (cron, 03:00).

Копия — через sqlite3 backup API: консистентна, даже пока бот пишет в базу.
"""
import asyncio
import gzip
import os
import sqlite3
import tempfile
from datetime import datetime

from aiogram import Bot
from aiogram.types import BufferedInputFile

from bot.config import load_settings
from bot.db import TZ


def make_backup(db_path: str) -> bytes:
    """Консистентная копия SQLite → gzip."""
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        src, dst = sqlite3.connect(db_path), sqlite3.connect(tmp)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        with open(tmp, "rb") as f:
            return gzip.compress(f.read())
    finally:
        os.unlink(tmp)


def backup_name(now: datetime) -> str:
    return f"bot_{now:%Y-%m-%d_%H%M}.db.gz"


async def send_backup() -> None:
    s = load_settings()
    data = make_backup(s.db_path)
    now = datetime.now(TZ)
    bot = Bot(s.bot_token)
    try:
        await bot.send_document(s.admin_tg_id, BufferedInputFile(data, backup_name(now)),
                                caption=f"Бэкап БД, {now:%d.%m.%Y %H:%M} ({len(data) // 1024 + 1} КБ). "
                                        "Распаковать: gunzip")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(send_backup())
