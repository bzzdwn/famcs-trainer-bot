"""/me — личная статистика."""
from html import escape

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.db import DB
from bot.stats import student_stats
from bot.topics import TOPIC_NAMES, topic_title

router = Router()


def days_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "день"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "дня"
    return "дней"


@router.message(Command("me"))
async def cmd_me(message: Message, db: DB):
    student = await db.get_student_by_tg(message.from_user.id)
    if not student:
        await message.answer("Сначала зарегистрируйся: /start")
        return
    st = await student_stats(db, student.id)
    pct = f"{st.first_try_pct:.0f}% ({st.first_try} из {st.points} шагов)" if st.points else "—"
    blitz = (f"{st.best_blitz[0]}/{st.best_blitz[1]} · {st.best_blitz[2]} с на карточку" if st.best_blitz
             else "ещё не было — /blitz")
    lines = [
        f"<b>{escape(student.full_name)}</b>",
        f"🔥 Серия: {st.streak} {days_word(st.streak)} подряд",
        f"🎯 С первой попытки: {pct}",
        f"⚡ Лучший блиц: {blitz}",
    ]
    if st.topics:
        lines.append("\n<b>Задачи по темам</b> (решено сам / всего):")
        order = [t for t in TOPIC_NAMES if t in st.topics] + sorted(set(st.topics) - set(TOPIC_NAMES))
        for t in order:
            solved, total = st.topics[t]
            lines.append(f"• {escape(topic_title(t))}: {solved} / {total}")
    else:
        lines.append("\nЗадач пока нет — /train")
    await message.answer("\n".join(lines))
