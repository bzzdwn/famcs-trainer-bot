"""/train: выбор темы и уровня, выбор хода (по шагам / сразу ответ), решение, выход из задачи.

Вся логика попыток — в bot.flow.TrainSession; здесь только Telegram и запись в БД.
"""
import asyncio
import logging
import random
import secrets
import time
from dataclasses import dataclass
from html import escape
from io import BytesIO

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)

from bot.config import Settings
from bot.db import DB
from bot.flow import HIDE_TEXT, NOT_SPENT, Outcome, TrainSession
from bot.formula import FormulaError, formula_keyboard, parse_webapp_data
from bot.genpool import GEN_FAILED, GenError, GenPool
from bot.ocr import OcrError, YandexOcr
from bot.photo import OCR_DAILY_LIMIT, photo_attempt_fields, pick_photo_size
from bot.render import render_options, render_or_text
from bot.topics import (ANY_LEVEL, SECTION_NAMES, available, build_method_step, is_mix, mix_key, mix_levels,
                        pick_template, section_of, sections, topic_title)
from bot.waiting import waiting
from core.checker import check_with_timeout
from core.generator import Instance
from core.limits import TYPES
from core.latex_input import Unreadable, latex_to_input
from core.templates import Template

router = Router()

DIAG = {"wrong": "Неверно.", "sign": "Проверь знак.", "factor": "Потерян множитель.", "no_const": "Забыл +C."}


@dataclass
class Active:
    session: TrainSession
    session_id: int
    topic: str
    level: str
    shown_at: float = 0.0
    pending: str | None = None     # ввод, ожидающий подтверждения превью
    pending_method: str = "text"   # откуда pending: text | formula (фото — по pending_ocr)
    pending_ocr: str | None = None # что распознал OCR (пока не проверено) — для ocr_raw / ocr_edited
    pending_ocr_id: int | None = None  # запись ocr_requests — свяжем с попыткой


@dataclass
class Offered:
    """Условие показано, ход решения ещё не выбран (сессии в БД пока нет)."""
    inst: Instance
    instance_id: int
    topic: str
    level: str                     # как выбрали в меню (может быть ANY_LEVEL)
    difficulty: str                # уровень самого шаблона


# tg chat → состояние; в памяти, теряется при рестарте (сессии в БД закрывает abandon_unfinished)
ACTIVE: dict[int, Active] = {}
OFFERED: dict[int, Offered] = {}
LAST_TPL: dict[int, str] = {}      # chat → id прошлого шаблона: не повторяем подряд


def bot_check(kind, user, expected, var, domain, **extra):
    return check_with_timeout(kind, user, expected, var, domain, **extra)


# --- клавиатуры ------------------------------------------------------------

def btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


EXIT_ROW = [btn("✖ Выйти из задачи", "drop")]
EDITOR_ROW = [btn("✏️ Редактор формул", "editor")]     # необязательный способ ввода: текст и фото остаются


def kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


HINT_ROW = [btn("💡 Подсказка", "hint")]               # есть с начала любого шага


def answer_rows(p) -> list[list[InlineKeyboardButton]]:
    """Кнопки вариантов; убранные подсказкой пропускаем, номера остальных прежние."""
    shown = [i for i in range(len(p.options)) if i not in p.hidden]
    if p.options[:len(TYPES)] == TYPES:                # типы неопределённости: 4 + 3 + «нет неопределённости»
        short = [btn(p.options[i], f"b:{i}") for i in shown if i < len(TYPES)]
        return [short[k:k + 4] for k in range(0, len(short), 4)] + [
            [btn(p.options[i], f"b:{i}")] for i in shown if i >= len(TYPES)]
    if p.formulas:
        return [[btn(str(i + 1), f"b:{i}") for i in shown]]
    return [[btn(p.options[i], f"b:{i}")] for i in shown]


def help_rows(a: Active) -> list[list[InlineKeyboardButton]]:
    rows = [[btn("💡 Подсказка", "hint"), btn("Показать ответ", "reveal")]]
    if a.session.direct and a.session.inst.steps:
        rows.append([btn("Решать по шагам", "steps")])
    return rows


# --- отправка ----------------------------------------------------------------

async def send_formula(message: Message, latex: str, caption: str = "", markup: InlineKeyboardMarkup | None = None):
    img = await asyncio.to_thread(render_or_text, latex, latex)
    if isinstance(img, bytes):
        await message.answer_photo(BufferedInputFile(img, "f.png"), caption=caption or None, reply_markup=markup)
    else:
        await message.answer(f"{caption}\n<code>{escape(img)}</code>", reply_markup=markup)


async def show_prompt(message: Message, a: Active, db: DB, after_wrong: bool = False):
    p = a.session.current()
    a.shown_at = time.monotonic()
    a.pending = a.pending_ocr = a.pending_ocr_id = None
    a.pending_method = "text"
    if p.kind == "done":
        await finish(message, a, db)
        return
    extra = help_rows(a) if after_wrong else [HINT_ROW]
    if p.kind == "buttons":
        markup = kb(*answer_rows(p), *extra, EXIT_ROW)
        if p.formulas:
            card = await asyncio.to_thread(render_options, list(p.options), p.context)
            await message.answer_photo(BufferedInputFile(card, "options.png"), caption=escape(p.text),
                                       reply_markup=markup)
        elif p.context:                                # «какой тип теперь?» — у этой дроби
            await send_formula(message, p.context, caption=escape(p.text), markup=markup)
        else:
            await message.answer(escape(p.text), reply_markup=markup)
    else:
        text = "Введи ещё раз или возьми подсказку." if after_wrong else \
            escape(p.text) + "\n<i>Введи ответ сообщением, в редакторе или пришли фото формулы.</i>"
        markup = kb(EDITOR_ROW, *extra, EXIT_ROW)
        if p.context and not after_wrong:
            await send_formula(message, p.context, caption=text, markup=markup)
        else:
            await message.answer(text, reply_markup=markup)


async def finish(message: Message, a: Active, db: DB):
    sm = a.session.summary()
    await db.finish_session(a.session_id, sm.status)
    ACTIVE.pop(message.chat.id, None)
    lines = [f"<b>Задача разобрана.</b> С первой попытки: {sm.first_try} из {sm.total}."]
    if sm.revealed:
        lines.append(f"Ответ показан: {sm.revealed}.")
    await message.answer("\n".join(lines), reply_markup=kb(
        [btn("Следующая задача", f"next:{a.topic}:{a.level}")], [btn("Другая тема", f"s:{section_of(a.topic)}")]))


async def log(db: DB, a: Active, out: Outcome, **extra) -> int | None:
    if out.log:
        return await db.log_attempt(a.session_id, latency_ms=int((time.monotonic() - a.shown_at) * 1000),
                                    **{**out.log, **extra})
    return None


async def react(message: Message, a: Active, db: DB, out: Outcome):
    """Показать результат попытки и следующий шаг."""
    status, note = out.verdict.status, out.verdict.note
    if status == "ok":
        await message.answer("✅ Верно!" + (f"\n<i>{escape(note)}</i>" if note else ""))
        await show_prompt(message, a, db)
        return
    if status in NOT_SPENT:
        await message.answer(f"⚠️ {escape(note or 'Не смог проверить')}. Попытка не засчитана, введи ещё раз.")
        return
    if out.moved_on and not out.forced_reveal:          # уровень C, «свой путь»
        await message.answer("Не совпало с эталоном — идём дальше своим путём.")
        await show_prompt(message, a, db)
        return
    text = "❌ " + escape(note or DIAG.get(status, "Неверно."))
    if out.forced_reveal:
        await message.answer(text + "\nПопытки кончились, вот ответ:")
        await send_answer(message, out.answer, out.log.get("checkpoint_type") in ("choice", "method"))
        await show_prompt(message, a, db)
    else:
        await message.answer(text)
        await show_prompt(message, a, db, after_wrong=True)


async def send_answer(message: Message, answer: str, is_label: bool = False):
    """Ответ точки: подпись варианта (choice) или формула."""
    if is_label:
        await message.answer(f"Ответ: <b>{escape(answer)}</b>")
    else:
        await send_formula(message, answer, caption="Ответ")


# --- меню ----------------------------------------------------------------------

async def require_student(message: Message, db: DB, tg_id: int):
    st = await db.get_student_by_tg(tg_id)
    if not st:
        await message.answer("Сначала зарегистрируйся: /start")
    return st


def topics_kb(bank: dict[str, Template], section: str) -> InlineKeyboardMarkup:
    """Темы раздела; «Вразнобой» — если тем больше одной; «Разделы» — если разделов больше одного."""
    topics = available(bank, section)
    rows = [[btn(topic_title(t), f"t:{t}")] for t in topics]
    if len(topics) > 1:
        rows.append([btn(topic_title(mix_key(section)), f"t:{mix_key(section)}")])
    if len(sections(bank)) > 1:
        rows.append([btn("◀ Разделы", "menu")])
    return kb(*rows)


async def main_menu(message: Message, bank: dict[str, Template]):
    """Раздел → тема → уровень; единственный раздел не спрашиваем."""
    secs = sections(bank)
    if len(secs) == 1:
        await message.answer("Выбери тему:", reply_markup=topics_kb(bank, secs[0]))
    else:
        await message.answer("Выбери раздел:", reply_markup=kb(*[[btn(SECTION_NAMES[s], f"s:{s}")] for s in secs]))


@router.message(Command("train"))
async def cmd_train(message: Message, db: DB, bank: dict[str, Template]):
    if await require_student(message, db, message.from_user.id):
        await main_menu(message, bank)


@router.callback_query(F.data == "menu")
async def cb_menu(cb: CallbackQuery, bank: dict[str, Template]):
    await cb.answer()
    await main_menu(cb.message, bank)


@router.callback_query(F.data.startswith("s:"))
async def cb_section(cb: CallbackQuery, bank: dict[str, Template]):
    section = cb.data[2:]
    if section not in sections(bank):
        await cb.answer("В этом разделе пока нет задач", show_alert=True)
        return
    await cb.answer()
    await cb.message.answer(f"{SECTION_NAMES[section]}. Выбери тему:", reply_markup=topics_kb(bank, section))


@router.callback_query(F.data.startswith("t:"))
async def cb_topic(cb: CallbackQuery, bank: dict[str, Template]):
    topic = cb.data[2:]
    await cb.answer()
    levels = mix_levels(bank, section_of(topic)) if is_mix(topic) else available(bank).get(topic, [])
    # 4 кнопки «Уровень X» в ряд Telegram обрезает до «Ур…ь X» — слово «уровень» уже в тексте
    row = [btn("Любой" if lv == ANY_LEVEL else lv, f"l:{topic}:{lv}") for lv in levels]
    await cb.message.edit_text(f"{escape(topic_title(topic))}: выбери уровень сложности (A — проще, C — сложнее)",
                               reply_markup=kb(row))


async def close_current(chat_id: int, db: DB) -> bool:
    """Закрыть текущую задачу как dropped. True — было что закрывать."""
    a = ACTIVE.pop(chat_id, None)
    if a:
        a.session.drop()
        await db.finish_session(a.session_id, "dropped")
    return bool(a) | bool(OFFERED.pop(chat_id, None))


@router.callback_query(F.data.startswith("l:") | F.data.startswith("next:"))
async def cb_level(cb: CallbackQuery, db: DB, bank: dict[str, Template], gen: GenPool):
    _, topic, level = cb.data.split(":")
    await cb.answer()
    st = await require_student(cb.message, db, cb.from_user.id)
    if not st:
        return
    chat = cb.message.chat.id
    tpl = pick_template(bank, topic, level, random.Random(secrets.randbits(64)), exclude=LAST_TPL.get(chat),
                        solved=await db.solved_templates(st.id))
    if tpl is None:
        await cb.message.answer("По этой теме и уровню пока нет задач.")
        return
    async with waiting(cb.message, "Готовлю задачу…"):
        try:
            inst = await gen.instance(tpl, secrets.randbits(32))
        except GenError:                              # прошлую задачу не закрываем: новой нет
            await cb.message.answer(GEN_FAILED, reply_markup=kb([btn("Ещё раз", cb.data)]))
            return
        await close_current(chat, db)
        from bot.handlers.blitz import cancel_blitz   # ленивый импорт: train ↔ blitz
        cancel_blitz(chat)
        iid = await db.save_instance(inst, tpl.topic, tpl.difficulty)
        OFFERED[chat] = Offered(inst, iid, topic, level, tpl.difficulty)
        LAST_TPL[chat] = tpl.id
        # во «Вразнобой» настоящую тему не показываем — она подсказывает метод
        await send_formula(cb.message, inst.statement_latex,
                           caption=f"{escape(topic_title(topic))}, уровень {tpl.difficulty}. Как будешь решать?",
                           markup=kb([btn("По шагам", "path:steps")] * inst.steps + [btn("Сразу ответ", "path:direct")], EXIT_ROW))


@router.callback_query(F.data.startswith("path:"))
async def cb_path(cb: CallbackQuery, db: DB, bank: dict[str, Template]):
    off = OFFERED.pop(cb.message.chat.id, None)
    if off is None:
        await cb.answer("Эта задача уже закрыта. /train — новая", show_alert=True)
        return
    st = await require_student(cb.message, db, cb.from_user.id)
    if not st:
        return
    await cb.answer()
    await cb.message.edit_reply_markup(reply_markup=None)
    tpl = bank.get(off.inst.template_id)
    # во «Вразнобой» шаги шаблона выдают метод — сначала студент выбирает его сам
    method_step = build_method_step(bank, tpl) if is_mix(off.topic) and tpl and tpl.blitz_method else None
    session = TrainSession(off.inst, seed=off.instance_id, check=bot_check, free_input=off.difficulty == "C",
                           direct=cb.data == "path:direct", method_step=method_step)
    sid = await db.start_session(st.id, off.instance_id, session.mode)
    a = Active(session, sid, off.topic, off.level)
    ACTIVE[cb.message.chat.id] = a
    await show_prompt(cb.message, a, db)


# --- выход из задачи ----------------------------------------------------------

async def after_drop(message: Message, chat_id: int, db: DB):
    a, off = ACTIVE.get(chat_id), OFFERED.get(chat_id)
    where = a or off
    if not await close_current(chat_id, db):
        await message.answer("Сейчас нет задачи. /train — выбрать.")
        return
    await message.answer("Задача закрыта.", reply_markup=kb(
        [btn("Другая задача этой темы", f"next:{where.topic}:{where.level}")], [btn("Меню тем", f"s:{section_of(where.topic)}")]))


@router.callback_query(F.data == "drop")
async def cb_drop(cb: CallbackQuery, db: DB):
    await cb.answer()
    await cb.message.edit_reply_markup(reply_markup=None)
    await after_drop(cb.message, cb.message.chat.id, db)


@router.message(Command("stop"))
async def cmd_stop(message: Message, db: DB):
    await after_drop(message, message.chat.id, db)


# --- ответы ----------------------------------------------------------------

async def active_or_stale(cb: CallbackQuery) -> Active | None:
    a = ACTIVE.get(cb.message.chat.id)
    if a is None:
        await cb.answer("Эта задача уже закрыта. /train — новая", show_alert=True)
    return a


@router.callback_query(F.data.startswith("b:"))
async def cb_button(cb: CallbackQuery, db: DB):
    a = await active_or_stale(cb)
    if not a:
        return
    try:
        out = a.session.answer_button(int(cb.data[2:]))
    except (ValueError, RuntimeError):
        await cb.answer("Эта кнопка уже неактуальна", show_alert=True)
        return
    await cb.answer()
    await cb.message.edit_reply_markup(reply_markup=None)
    await log(db, a, out)
    await react(cb.message, a, db, out)


async def ask_confirm(message: Message, a: Active, raw: str, method: str) -> bool:
    """Превью «Ты имел в виду это?» для текста и редактора. False — запись не разобрана (попытка не тратится)."""
    prev = a.session.preview(raw)
    if not prev.ok:
        await message.answer(f"⚠️ {escape(prev.error)}")
        return False
    a.pending, a.pending_method = raw, method
    await message.bot.send_chat_action(message.chat.id, "upload_photo")   # превью рисуется доли секунды
    await send_formula(message, prev.latex, caption="Ты имел в виду это?",
                       markup=kb([btn("Да, проверь", "confirm"), btn("Исправить", "fix")]))
    return True


def input_hint(a: Active | None) -> str | None:
    """Почему сейчас нельзя ввести ответ; None — можно."""
    if a is not None and a.session.current().kind == "input":
        return None
    return "Выбери вариант кнопкой выше." if a else "Не понял. /train — тренировка, /help — синтаксис."


@router.message(F.text & ~F.text.startswith("/"))
async def on_text(message: Message):
    a = ACTIVE.get(message.chat.id)
    if hint := input_hint(a):
        await message.answer(hint)
        return
    await ask_confirm(message, a, message.text, "text")


@router.callback_query(F.data == "editor")
async def cb_editor(cb: CallbackQuery, settings: Settings):
    a = await active_or_stale(cb)
    if not a:
        return
    if a.session.current().kind != "input":
        await cb.answer("Сейчас выбери вариант кнопкой", show_alert=True)
        return
    await cb.answer()
    await cb.message.answer("Открой редактор кнопкой под полем ввода.",
                            reply_markup=formula_keyboard(settings.formula_url))


@router.message(F.web_app_data)
async def on_formula(message: Message):
    a = ACTIVE.get(message.chat.id)
    if hint := input_hint(a):
        await message.answer(hint)
        return
    a.pending = a.pending_ocr = a.pending_ocr_id = None       # прошлое непроверенное больше не в силе
    data = message.web_app_data.data
    try:
        raw = parse_webapp_data(data)
    except FormulaError as exc:
        logging.warning("редактор формул: %s | data=%r", exc, data[:500])
        await message.answer(f"⚠️ {escape(str(exc))}")
        return
    if not await ask_confirm(message, a, raw, "formula"):
        logging.warning("редактор формул: не разобрано | data=%r raw=%r", data[:500], raw[:300])


@router.callback_query(F.data == "confirm")
async def cb_confirm(cb: CallbackQuery, db: DB):
    a = await active_or_stale(cb)
    if not a or a.pending is None or a.session.current().kind != "input":
        await cb.answer()
        return
    raw, a.pending = a.pending, None
    extra = photo_attempt_fields(a.pending_ocr, raw) if a.pending_ocr is not None else {}
    ocr_id = a.pending_ocr_id
    await cb.answer()
    await cb.message.edit_reply_markup(reply_markup=None)
    async with waiting(cb.message, "Проверяю ответ…"):
        out = await asyncio.to_thread(a.session.answer_text, raw, extra.get("input_method", a.pending_method))
    attempt_id = await log(db, a, out, **extra)
    if ocr_id is not None and attempt_id is not None:
        await db.link_ocr_attempt(ocr_id, attempt_id)
    await react(cb.message, a, db, out)


@router.callback_query(F.data == "fix")
async def cb_fix(cb: CallbackQuery):
    a = await active_or_stale(cb)
    if not a:
        return
    await cb.answer()
    await cb.message.edit_reply_markup(reply_markup=None)
    raw, a.pending = a.pending or "", None          # pending_ocr не сбрасываем: правка распознанного
    await cb.message.answer(f"Скопируй, поправь и отправь заново:\n<code>{escape(raw)}</code>")


@router.message(F.photo)
async def on_photo(message: Message, db: DB, ocr: YandexOcr | None):
    a = ACTIVE.get(message.chat.id)
    if a is None or a.session.current().kind != "input":
        await message.answer("Фото принимаю только на шаге, где нужно ввести ответ.")
        return
    if ocr is None:
        await message.answer("Фото-ввод пока выключен — введи ответ текстом.")
        return
    student = await db.get_student_by_tg(message.from_user.id)
    if student is None:
        return
    if await db.count_ocr_today(student.id) >= OCR_DAILY_LIMIT:
        await message.answer(f"Лимит {OCR_DAILY_LIMIT} фото в день исчерпан — введи ответ текстом.")
        return
    size = pick_photo_size(message.photo)
    buf = BytesIO()
    markdown = None
    try:
        async with waiting(message, "Распознаю фото… (до 15 секунд)"):
            await message.bot.download(size.file_id, destination=buf)    # в память, на диск не пишем
            markdown = await ocr.recognize_markdown(buf.getvalue())
        text = latex_to_input(markdown)
    except (Unreadable, OcrError) as exc:
        status = "unreadable" if isinstance(exc, Unreadable) else "error"
        logging.warning("OCR %s: %s | markdown=%r", status, exc, markdown)
        await db.log_ocr(student.id, size.file_id, None, status, markdown=markdown)
        await message.answer("Не разобрал. Пересними крупнее (только итоговую формулу) или введи текстом.")
        return
    a.pending_ocr_id = await db.log_ocr(student.id, size.file_id, text, "ok", markdown=markdown)
    a.pending_ocr = text
    prev = a.session.preview(text)
    if not prev.ok:
        await message.answer(f"Распознал так:\n<code>{escape(text)}</code>\n⚠️ {escape(prev.error)}\n"
                             "Скопируй, поправь и отправь текстом.")
        return
    a.pending = text
    await send_formula(message, prev.latex, caption="Ты написал это?",
                       markup=kb([btn("Да, проверь", "confirm"), btn("Исправить", "fix")]))


@router.callback_query(F.data == "hint")
async def cb_hint(cb: CallbackQuery, gen: GenPool):
    a = await active_or_stale(cb)
    if not a:
        return
    await cb.answer()
    reveal = kb([btn("Показать ответ", "reveal")])
    if a.session.needs_answer_hints:                              # sympy — только в процессе-генераторе
        direct = a.session.direct
        try:
            a.session.set_answer_hints(direct, await gen.hints(a.session.inst, direct))
        except GenError:
            await cb.message.answer("Не получилось подготовить подсказку. Нажми ещё раз.", reply_markup=kb(HINT_ROW))
            return
    hint = a.session.take_hint()
    if hint is None:
        await cb.message.answer("Подсказок больше нет.", reply_markup=reveal)
        return
    more = reveal if hint.last else kb([btn("💡 Ещё подсказка", "hint")])
    text = f"💡 {escape(hint.text)}"
    if hint.text == HIDE_TEXT:
        p = a.session.current()
        await cb.message.answer(text + " Остались:", reply_markup=kb(*answer_rows(p), *reveal.inline_keyboard, EXIT_ROW))
    elif hint.latex:
        await send_formula(cb.message, hint.latex, caption=text, markup=more)
    else:
        await cb.message.answer(text, reply_markup=more)


@router.callback_query(F.data == "reveal")
async def cb_reveal(cb: CallbackQuery, db: DB):
    a = await active_or_stale(cb)
    if not a or a.session.finished:
        return
    await cb.answer()
    await cb.message.edit_reply_markup(reply_markup=None)
    is_label = a.session.current().cp_type in ("choice", "method")
    out = a.session.reveal()
    await log(db, a, out)
    await send_answer(cb.message, out.answer, is_label)
    await show_prompt(cb.message, a, db)


@router.callback_query(F.data == "steps")
async def cb_steps(cb: CallbackQuery, db: DB):
    a = await active_or_stale(cb)
    if not a:
        return
    try:
        a.session.switch_to_steps()
    except (ValueError, RuntimeError):
        await cb.answer("Уже не актуально", show_alert=True)
        return
    await cb.answer()
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer("Хорошо, давай по шагам.")
    await show_prompt(cb.message, a, db)
