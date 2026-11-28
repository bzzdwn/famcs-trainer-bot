"""/me — личная статистика."""
from html import escape

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.db import DB
from bot.stats import student_stats
from bot.topics import INTEGRALS, SECTION_NAMES, TOPIC_NAMES, topic_title

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
    fmt = lambda b: f"{b[0]}/{b[1]} · {b[2]} с на карточку"
    blitz = fmt(st.best_blitz) if st.best_blitz else "ещё не было — /blitz"
    if len(st.best_blitz_by_section) > 1 or set(st.best_blitz_by_section) - {INTEGRALS}:
        best = st.best_blitz_by_section
        blitz = "; ".join(f"{SECTION_NAMES[s]} {fmt(best[s])}" for s in SECTION_NAMES if s in best)
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
