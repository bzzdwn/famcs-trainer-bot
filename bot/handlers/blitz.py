"""/blitz: 10 задач «выбери метод» с таймером 10 с. Логика серии — bot.blitz.BlitzRun."""
import asyncio
import secrets
import time
from dataclasses import dataclass, field
from html import escape

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.blitz import OK, SKIP, TIME_LIMIT, BlitzAnswer, BlitzRun, InvalidTime, countdown_marks, parse_time
from bot.db import DB
from bot.render import render_or_text
from core.templates import Template

router = Router()


@dataclass
class BlitzState:
    run: BlitzRun
    run_id: int
    student_id: int
    images: list                       # PNG (bytes) или текст-fallback для каждого условия
    bank: dict[str, Template]          # для topic/difficulty экземпляров в БД
    message: Message                   # куда слать задачи
    sent_at: float = 0.0
    task_msg: Message | None = None
    timer: asyncio.Task | None = field(default=None, repr=False)
    countdown: list = field(default_factory=list)   # «3…», «2…», «1…» текущей задачи — удалим по её окончании


BLITZ: dict[int, BlitzState] = {}      # chat_id → серия


def cancel_blitz(chat_id: int) -> bool:
    """Оборвать серию (недоигранная в «лучший блиц» не идёт). True — была серия."""
    st = BLITZ.pop(chat_id, None)
    if st and st.timer:
        st.timer.cancel()
    if st:
        asyncio.create_task(clear_countdown(st))
    was_waiting = chat_id in WAIT_TIME          # передумал вводить время — /stop или /train снимают ожидание
    WAIT_TIME.discard(chat_id)
    return st is not None or was_waiting


async def clear_countdown(st: BlitzState):
    msgs, st.countdown = st.countdown, []
    for m in msgs:
        try:
            await m.delete()
        except Exception:
            pass


def _idx(st: BlitzState) -> int:
    return st.run.tasks.index(st.run.current())


def _kb(st: BlitzState, idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=o, callback_data=f"z:{idx}:{i}")] for i, o in enumerate(st.run.tasks[idx].options)])


async def send_task(chat_id: int, db: DB):
    st = BLITZ.get(chat_id)
    if st is None:
        return
    if st.run.finished:
        await finish(chat_id, db)
        return
    idx = _idx(st)
    caption = f"{idx + 1}/{len(st.run.tasks)} · {int(st.run.time_limit)} с · какой метод?"
    img = st.images[idx]
    if isinstance(img, bytes):
        st.task_msg = await st.message.answer_photo(BufferedInputFile(img, "b.png"), caption=caption,
                                                    reply_markup=_kb(st, idx))
    else:
        st.task_msg = await st.message.answer(f"{caption}\n<code>{escape(img)}</code>", reply_markup=_kb(st, idx))
    st.sent_at = time.monotonic()
    st.timer = asyncio.create_task(_timeout(chat_id, idx, db))


def _same_task(chat_id: int, idx: int) -> BlitzState | None:
    st = BLITZ.get(chat_id)
    return st if st and not st.run.finished and _idx(st) == idx else None


async def _timeout(chat_id: int, idx: int, db: DB):
    """Отсчёт «3… 2… 1…» в последние секунды, затем — время вышло. Отменяется при ответе."""
    elapsed = 0.0
    limit = BLITZ[chat_id].run.time_limit if chat_id in BLITZ else TIME_LIMIT
    for at, text in countdown_marks(limit):
        await asyncio.sleep(at - elapsed)
        elapsed = at
        st = _same_task(chat_id, idx)
        if st is None:
            return
        st.countdown.append(await st.message.answer(text))
    await asyncio.sleep(limit - elapsed)
    st = _same_task(chat_id, idx)
    if st is None:
        return
    await clear_countdown(st)
    ans = st.run.answer(None, st.run.time_limit)
    try:
        await st.task_msg.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await st.message.answer(f"⏰ Время! Это: <b>{escape(st.run.tasks[idx].correct)}</b>")
    await record(st, ans, db)
    await send_task(chat_id, db)


async def record(st: BlitzState, ans: BlitzAnswer, db: DB):
    task = st.run.tasks[ans.idx]
    tpl = st.bank.get(task.template_id)
    iid = await db.save_instance(task.instance, tpl.topic if tpl else "", tpl.difficulty if tpl else "")
    sid = await db.start_session(st.student_id, iid, "blitz")
    await db.log_attempt(sid, checkpoint_idx=0, checkpoint_type="blitz", raw_input=ans.chosen or "",
                         verdict=ans.verdict, input_method="button", hint_used=False, attempt_no=1,
                         latency_ms=int(ans.elapsed * 1000))
    await db.finish_session(sid, "solved" if ans.verdict == OK else "revealed")


async def finish(chat_id: int, db: DB):
    st = BLITZ.pop(chat_id, None)
    if st is None:
        return
    sm = st.run.summary()
    await db.finish_blitz_run(st.run_id, sm.score, sm.total)
    lines = [f"<b>Блиц окончен: {sm.score}/{sm.total}</b>"]
    if sm.mistakes:
        lines.append("\nРазбор:")
        for m in sm.mistakes:
            you = "время вышло" if m.chosen is None else f"ты: {escape(m.chosen)}"
            line = f"{m.idx + 1}) <b>{escape(m.correct)}</b> ({you})"
            if m.hint:
                line += f"\n   Признак: {escape(m.hint)}"
            lines.append(line)
    else:
        lines.append("Без ошибок! 🔥")
    t = int(st.run.time_limit)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"Ещё блиц · {t} с", callback_data=f"bt:{t}"),
        InlineKeyboardButton(text="Другое время", callback_data="blitz")]])
    await st.message.answer("\n".join(lines), reply_markup=kb)


WAIT_TIME: set[int] = set()            # чаты, где ждём число секунд сообщением


async def ask_time(message: Message, tg_id: int, db: DB):
    """Шаг перед блицем: стандартные 10 с или своё время."""
    if not await db.get_student_by_tg(tg_id):
        await message.answer("Сначала зарегистрируйся: /start")
        return
    WAIT_TIME.discard(message.chat.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"⏱ {int(TIME_LIMIT)} с", callback_data=f"bt:{int(TIME_LIMIT)}"),
        InlineKeyboardButton(text="Указать вручную", callback_data="bt:custom")]])
    await message.answer("⚡ Блиц: 10 задач на выбор метода. Сколько времени на карточку?", reply_markup=kb)


async def start_blitz(message: Message, tg_id: int, db: DB, bank: dict[str, Template], time_limit: int):
    student = await db.get_student_by_tg(tg_id)
    if not student:
        await message.answer("Сначала зарегистрируйся: /start")
        return
    WAIT_TIME.discard(message.chat.id)
    from bot.handlers.train import close_current      # ленивый импорт: train ↔ blitz
    await close_current(message.chat.id, db)
    cancel_blitz(message.chat.id)
    await message.answer(f"⚡ Блиц: 10 задач, {time_limit} секунд на каждую. Выбери метод решения.\n"
                         "Готовлю задачи…")
    run = await asyncio.to_thread(BlitzRun, bank, secrets.randbits(32), time_limit=time_limit)
    images = await asyncio.to_thread(
        lambda: [render_or_text(t.instance.statement_latex, t.instance.statement_latex) for t in run.tasks])
    run_id = await db.start_blitz_run(student.id, time_limit=time_limit)
    BLITZ[message.chat.id] = BlitzState(run, run_id, student.id, images, dict(bank), message)
    for n in ("3", "2", "1"):
        await message.answer(n)
        await asyncio.sleep(1)
        if message.chat.id not in BLITZ:          # /stop во время отсчёта
            return
    await send_task(message.chat.id, db)


@router.message(Command("blitz"))
async def cmd_blitz(message: Message, db: DB):
    await ask_time(message, message.from_user.id, db)


@router.callback_query(F.data == "blitz")
async def cb_blitz(cb: CallbackQuery, db: DB):
    await cb.answer()
    await ask_time(cb.message, cb.from_user.id, db)


@router.callback_query(F.data.startswith("bt:"))
async def cb_time(cb: CallbackQuery, db: DB, bank: dict[str, Template]):
    await cb.answer()
    await cb.message.edit_reply_markup(reply_markup=None)
    choice = cb.data[3:]
    if choice == "custom":
        WAIT_TIME.add(cb.message.chat.id)
        await cb.message.answer("Напиши, сколько секунд на карточку: от 5 до 60, кратное 5 (например, 25).")
        return
    await start_blitz(cb.message, cb.from_user.id, db, bank, time_limit=int(choice))


@router.message(F.text & ~F.text.startswith("/"), F.chat.id.func(lambda cid: cid in WAIT_TIME))
async def on_time_text(message: Message, db: DB, bank: dict[str, Template]):
    try:
        seconds = parse_time(message.text)
    except InvalidTime as e:
        await message.answer(escape(str(e)))
        return
    await start_blitz(message, message.from_user.id, db, bank, time_limit=seconds)


@router.callback_query(F.data.startswith("z:"))
async def cb_answer(cb: CallbackQuery, db: DB):
    st = BLITZ.get(cb.message.chat.id)
    _, idx, i = cb.data.split(":")
    if st is None or st.run.finished or _idx(st) != int(idx):
        await cb.answer("Уже неактуально")
        return
    elapsed = time.monotonic() - st.sent_at         # фиксируем до любых await
    if st.timer:
        st.timer.cancel()
    ans = st.run.answer(int(i), elapsed)
    await clear_countdown(st)
    task = st.run.tasks[ans.idx]
    await cb.answer()
    await cb.message.edit_reply_markup(reply_markup=None)
    if ans.verdict == OK:
        await cb.message.answer("✅ Верно!")
    elif ans.verdict == SKIP:
        await cb.message.answer(f"⏰ Не успел. Это: <b>{escape(task.correct)}</b>")
    else:
        await cb.message.answer(f"❌ Неверно. Это: <b>{escape(task.correct)}</b>")
    await record(st, ans, db)
    await send_task(cb.message.chat.id, db)


@router.message(Command("stop"))
async def cmd_stop_blitz(message: Message):
    if not cancel_blitz(message.chat.id):
        raise SkipHandler()                            # нет блица — пусть /stop обработает тренировка
    await message.answer("Блиц прерван. /blitz — заново, /train — тренировка.")
