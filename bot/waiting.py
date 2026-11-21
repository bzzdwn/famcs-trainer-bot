"""«⏳ …» на время долгих операций: студент видит, что бот работает, а потом сообщение исчезает."""
from contextlib import asynccontextmanager

from aiogram.types import Message


@asynccontextmanager
async def waiting(message: Message, text: str):
    note = await message.answer(f"⏳ {text}")
    try:
        yield
    finally:
        try:
            await note.delete()
        except Exception:          # уже удалено / нет прав — не критично
            pass
