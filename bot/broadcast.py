"""/broadcast: рассылка сообщения преподавателя всем студентам."""
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from bot.db import Student

MAX_LEN = 4096          # лимит Telegram на текст сообщения
DELAY = 0.05            # пауза между сообщениями — в пределах лимитов Telegram

SendFn = Callable[[int, str], Awaitable[object]]   # (tg_id, text)


class BroadcastError(ValueError):
    """Текст нельзя разослать. str(e) — сообщение преподавателю."""


@dataclass(frozen=True)
class Report:
    delivered: int
    failed: list[str] = field(default_factory=list)   # имена тех, кому не дошло


def validate_text(text: str) -> str:
    """Обрезает пробелы по краям; пустой или длиннее MAX_LEN → BroadcastError."""
    text = (text or "").strip()
    if not text:
        raise BroadcastError("Пустое сообщение. Формат: /broadcast текст (можно в несколько строк)")
    if len(text) > MAX_LEN:
        raise BroadcastError(f"Слишком длинно: {len(text)} символов, Telegram пропускает до {MAX_LEN}")
    return text


def recipients(students: list[Student], admin_tg_id: int) -> list[Student]:
    """Все студенты, кроме самого преподавателя."""
    return [s for s in students if s.tg_id != admin_tg_id]


async def broadcast(students: list[Student], text: str, send: SendFn, delay: float = DELAY) -> Report:
    """Отправить text каждому; ошибка одному не обрывает рассылку остальным."""
    delivered, failed = 0, []
    for i, st in enumerate(students):
        if i and delay:
            await asyncio.sleep(delay)
        try:
            await send(st.tg_id, text)
            delivered += 1
        except Exception as exc:                     # заблокировал бота, удалил аккаунт, сеть
            logging.warning("broadcast: не доставлено %s: %s", st.full_name, exc)
            failed.append(st.full_name)
    return Report(delivered, failed)
