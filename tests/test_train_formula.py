"""Редактор формул в задаче — необязательный способ ввода рядом с текстом и фото.
Прогон через настоящий Dispatcher aiogram; сеть заменена: бот только записывает, что отправил бы."""
import datetime as dt
import itertools
import json
import logging
from pathlib import Path

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import CallbackQuery, Chat, Message, ReplyKeyboardMarkup, Update, User, WebAppData

import bot.formula
from bot.blitz import BlitzRun
from bot.commands import commands_for
from bot.config import Settings
from bot.db import DB
from bot.flow import TrainSession
from bot.handlers import admin, start, train
from core.checker import check_antiderivative, check_expr
from core.generator import build, generate
from core.hints import answer_hints
from core.templates import load_templates

BANK = load_templates(Path(__file__).resolve().parent.parent / "data" / "templates")
ADMIN, STUDENT, STRANGER = 7, 8, 9
SETTINGS = Settings("1:A", ADMIN, "code", ":memory:", Path(__file__).resolve().parent.parent / "data" / "templates")
GOOD = r"e^{x}\operatorname{\mathrm{tg}}\left(\frac{x}{2}\right)+C"        # mix_exp_trig_01, a = 1
_ids = itertools.count(1)

DP = Dispatcher()                                        # router модуля можно включить только один раз
DP.include_routers(admin.make_router(ADMIN), train.router)


class FakeBot(Bot):
    def __init__(self):
        super().__init__("1:AAA", default=DefaultBotProperties(parse_mode="HTML"))
        self.sent = []

    async def __call__(self, method, request_timeout=None):
        self.sent.append(method)
        if type(method).__name__ in ("SendMessage", "SendPhoto"):
            return Message(message_id=next(_ids), date=dt.datetime.now(), text="…",
                           chat=Chat(id=method.chat_id, type="private")).as_(self)
        return True

    def texts(self):
        return "\n".join((getattr(m, "text", None) or getattr(m, "caption", None) or "") for m in self.sent)

    def buttons(self):
        """callback_data всех inline-кнопок отправленного."""
        return [b.callback_data for m in self.sent
                for row in getattr(getattr(m, "reply_markup", None), "inline_keyboard", None) or [] for b in row]

    def keyboards(self):
        return [m.reply_markup for m in self.sent if isinstance(getattr(m, "reply_markup", None), ReplyKeyboardMarkup)]


def quick_check(kind, user, expected, var, domain):
    f = check_antiderivative if kind == "antiderivative" else check_expr
    return f(user, expected, var, domain, symbolic=False)


class InlineGen:
    """Генератор без процесса: те же ответы, считает вызовы (процесс проверяет test_genpool)."""
    busy = False

    def __init__(self):
        self.calls = []

    async def instance(self, tpl, seed):
        self.calls.append("instance")
        return generate(tpl, seed)

    async def hints(self, inst, direct):
        self.calls.append("hints")
        return answer_hints(inst, direct)

    async def blitz_tasks(self, bank, seed):
        self.calls.append("blitz")
        return BlitzRun(bank, seed=seed).tasks


class FakeStock:
    resets = 0

    def reset(self):
        self.resets += 1


class World:
    def __init__(self, db, bot):
        self.db, self.bot = db, bot
        self.gen, self.stock, self.bank = InlineGen(), FakeStock(), BANK

    async def task(self, uid=STUDENT, tid="mix_exp_trig_01", params=None, direct=True):
        inst = build(BANK[tid], params or {"a": 1})
        st = await self.db.get_student_by_tg(uid)
        iid = await self.db.save_instance(inst, "t", "C")
        s = TrainSession(inst, seed=1, check=quick_check, free_input=True, direct=direct)
        a = train.Active(s, await self.db.start_session(st.id, iid, s.mode), "t", "C")
        train.ACTIVE[uid] = a
        return a

    async def feed(self, uid=STUDENT, *, web=None, **kw):
        """web — LaTeX из редактора; raw — сырые данные sendData; text; cb — нажатие inline-кнопки."""
        self.bot.sent.clear()
        user, chat = User(id=uid, is_bot=False, first_name="a"), Chat(id=uid, type="private")
        msg = lambda **k: Message(message_id=next(_ids), date=dt.datetime.now(), chat=chat, from_user=user, **k)
        if "cb" in kw:
            upd = Update(update_id=next(_ids), callback_query=CallbackQuery(
                id="1", from_user=user, chat_instance="1", data=kw["cb"], message=msg(text="…")))
        else:
            raw = json.dumps({"v": 1, "latex": web}) if web is not None else kw.get("raw")
            fields = {"web_app_data": WebAppData(data=raw, button_text="x")} if raw is not None else {"text": kw["text"]}
            upd = Update(update_id=next(_ids), message=msg(**fields))
        await DP.feed_update(self.bot, upd, db=self.db, bank=self.bank, settings=SETTINGS, ocr=None, gen=self.gen, stock=self.stock)

    async def attempts(self):
        async with self.db.conn.execute("SELECT raw_input, verdict, input_method, ocr_raw FROM attempts") as cur:
            return [tuple(r) for r in await cur.fetchall()]


@pytest.fixture
async def w():
    db = await DB.open(":memory:")
    await db.add_student("Иванов Иван", STUDENT)
    await db.add_student("Преподаватель", ADMIN)
    train.ACTIVE.clear(); train.OFFERED.clear()
    yield World(db, FakeBot())
    train.ACTIVE.clear()
    await db.close()


# --- редактор предлагается, но не навязывается -----------------------------------

async def show(w, a, after_wrong=False):
    w.bot.sent.clear()
    m = Message(message_id=1, date=dt.datetime.now(), chat=Chat(id=STUDENT, type="private"), text="…").as_(w.bot)
    await train.show_prompt(m, a, w.db, after_wrong)


@pytest.mark.parametrize("after_wrong", [False, True])
async def test_input_step_offers_editor(w, after_wrong):
    await show(w, await w.task(), after_wrong)
    assert "editor" in w.bot.buttons() and "drop" in w.bot.buttons()
    assert not w.bot.keyboards()                                    # сама клавиатура не появляется


async def test_input_prompt_still_mentions_text_and_photo(w):
    await show(w, await w.task())
    assert "сообщением" in w.bot.texts() and "фото" in w.bot.texts()


async def test_button_step_has_no_editor(w):
    await show(w, await w.task(direct=False))                       # первая точка — выбор
    assert "editor" not in w.bot.buttons()


async def test_editor_button_shows_keyboard_once_asked(w):
    await w.task()
    await w.feed(cb="editor")
    [k] = w.bot.keyboards()
    assert k.keyboard[0][0].web_app.url == SETTINGS.formula_url
    assert k.one_time_keyboard                                      # после использования сворачивается
    assert await w.attempts() == []                                 # попытка не тратится


async def test_editor_button_outside_input_step(w):
    await w.feed(cb="editor")                                       # задачи нет
    assert not w.bot.keyboards()
    await w.task(direct=False)                                      # шаг с кнопками
    await w.feed(cb="editor")
    assert not w.bot.keyboards()


# --- формула из редактора идёт тем же путём, что и текст --------------------------

async def test_formula_preview_then_confirm(w):
    a = await w.task()
    await w.feed(web=GOOD)
    assert "Ты имел в виду это?" in w.bot.texts() and {"confirm", "fix"} <= set(w.bot.buttons())
    assert await w.attempts() == [] and a.pending                   # пока только превью
    await w.feed(cb="confirm")
    assert "Верно" in w.bot.texts()
    [(raw, verdict, method, ocr)] = await w.attempts()
    assert (verdict, method, ocr) == ("ok", "formula", None) and "tg" in raw


async def test_forgotten_constant_reported(w):
    await w.task()
    await w.feed(web=GOOD.removesuffix("+C"))
    await w.feed(cb="confirm")
    assert "Забыл +C" in w.bot.texts()
    assert [x[1:3] for x in await w.attempts()] == [("no_const", "formula")]


async def test_fix_shows_text_to_copy(w):
    await w.task()
    await w.feed(web=GOOD)
    await w.feed(cb="fix")
    assert "<code>" in w.bot.texts() and "tg" in w.bot.texts()
    assert await w.attempts() == []


async def test_level_c_step_in_t(w):
    """Промежуточный шаг уровня C вводится по t — редактор годится и там."""
    a = await w.task(tid="mix_exp_pow_01", direct=False)
    opts = a.session.current().options
    a.session.answer_button(opts.index("t = x·ln x"))
    assert a.session.current().kind == "input"
    await w.feed(web=r"e^{t}")
    await w.feed(cb="confirm")
    assert [x[1:3] for x in await w.attempts()] == [("ok", "formula")]


# --- плохие данные и не тот момент -------------------------------------------------

@pytest.mark.parametrize("kw", [
    {"raw": "мусор"}, {"raw": "{}"}, {"raw": ""}, {"web": r"\frac{x}{\placeholder{}}"}, {"web": r"\int x"},
    {"web": "x" + "+x" * 3000}, {"web": "x+("}, {"web": "x+y"},
])
async def test_bad_formula_costs_nothing(w, kw):
    a = await w.task()
    await w.feed(**kw)
    assert "⚠️" in w.bot.texts()
    assert a.pending is None and await w.attempts() == []


async def test_bad_formula_clears_previous_pending(w):
    a = await w.task()
    await w.feed(web=GOOD)
    assert a.pending
    await w.feed(web=r"\int x")                                     # иначе «Да, проверь» проверит прошлую формулу
    assert a.pending is None


async def test_unreadable_latex_is_logged(w, caplog):
    await w.task()
    with caplog.at_level(logging.WARNING):
        await w.feed(web=r"\int x")
    assert r"\int x" in caplog.text                                 # чтобы доучивать конвертер


async def test_error_text_is_escaped(w):
    await w.task()
    await w.feed(web=r"\operatorname{<b>}x")
    assert "⚠️" in w.bot.texts() and "<b>" not in w.bot.texts()


async def test_formula_without_task(w):
    await w.feed(web=GOOD)
    assert "/train" in w.bot.texts() and await w.attempts() == []


async def test_formula_on_button_step(w):
    await w.task(direct=False)
    await w.feed(web=GOOD)
    assert "кнопкой" in w.bot.texts() and await w.attempts() == []


async def test_stranger_formula(w):
    await w.feed(STRANGER, web=GOOD)
    assert await w.attempts() == []


# --- способы ввода не путаются ------------------------------------------------------

async def test_text_input_unchanged(w):
    await w.task()
    await w.feed(text="e^x*tg(x/2) + C")
    await w.feed(cb="confirm")
    assert [x[1:3] for x in await w.attempts()] == [("ok", "text")]


async def test_photo_then_formula_is_formula(w):
    a = await w.task()
    a.pending = a.pending_ocr = "x + C"                             # фото распознано, но не проверено
    a.pending_ocr_id = 1
    await w.feed(web=GOOD)
    assert a.pending_ocr is None and a.pending_ocr_id is None
    await w.feed(cb="confirm")
    assert [x[2:] for x in await w.attempts()] == [("formula", None)]


async def test_formula_then_text_is_text(w):
    await w.task()
    await w.feed(web=GOOD)
    await w.feed(text="e^x*tg(x/2) + C")
    await w.feed(cb="confirm")
    assert [x[2] for x in await w.attempts()] == ["text"]


# --- прототип /formula убран ---------------------------------------------------------

async def test_admin_solves_with_editor(w):
    await w.task(uid=ADMIN)
    await w.feed(ADMIN, web=GOOD)
    assert "Ты имел в виду это?" in w.bot.texts() and "LaTeX:" not in w.bot.texts()
    await w.feed(ADMIN, cb="confirm")
    assert [x[1:3] for x in await w.attempts()] == [("ok", "formula")]


async def test_formula_command_removed(w):
    assert "formula" not in [c.command for c in commands_for(True, True)]
    assert not hasattr(bot.formula, "formula_report")
    await w.feed(ADMIN, text="/formula")
    assert not w.bot.keyboards()


def test_help_mentions_editor():
    assert "редактор" in start.CHEATSHEET.lower()
