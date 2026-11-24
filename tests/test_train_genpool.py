"""Бот и генератор: задачи, подсказки и блиц идут через процесс; сбой виден студенту, бот жив."""
import pytest

from bot.blitz import BlitzRun
from bot.genpool import GenError
from bot.handlers import train
from tests.test_train_formula import BANK, STUDENT, w  # noqa: F401


class Broken:
    busy = False

    async def instance(self, tpl, seed):
        raise GenError("упал")

    async def hints(self, inst, direct):
        raise GenError("упал")

    async def blitz_tasks(self, bank, seed):
        raise GenError("упал")


async def test_train_takes_task_from_generator(w):                      # noqa: F811
    await w.feed(cb="next:mix:C")
    assert STUDENT in train.OFFERED and "path:steps" in w.bot.buttons()
    assert w.gen.calls == ["instance"]


async def test_generator_failure_in_train(w):                           # noqa: F811
    w.gen = Broken()
    await w.feed(cb="next:mix:C")
    assert "Не получилось" in w.bot.texts() and "next:mix:C" in w.bot.buttons()   # можно нажать ещё раз
    assert STUDENT not in train.OFFERED


async def test_failure_keeps_current_task(w):                           # noqa: F811
    a = await w.task()
    w.gen = Broken()
    await w.feed(cb="next:mix:C")
    assert train.ACTIVE.get(STUDENT) is a and not a.session.dropped      # прошлая задача не потеряна


async def test_final_hints_come_from_generator(w):                      # noqa: F811
    await w.task(tid="int_exp_lcm_01", params={"m": 6})
    await w.feed(cb="hint"); await w.feed(cb="hint")
    assert w.gen.calls == ["hints"]                                     # лестница считается один раз


async def test_button_step_hint_needs_no_generator(w):                  # noqa: F811
    await w.task(tid="int_exp_lcm_01", params={"m": 6}, direct=False)
    w.gen = Broken()
    await w.feed(cb="hint")
    assert "💡" in w.bot.texts()


async def test_generator_failure_in_hint(w):                            # noqa: F811
    a = await w.task(tid="int_exp_lcm_01", params={"m": 6})
    w.gen = Broken()
    await w.feed(cb="hint")
    assert "ещё раз" in w.bot.texts()
    out = a.session.reveal()
    assert out.log["hint_used"] == 0                                    # неудачная подсказка не засчитана


async def test_reload_resets_stock(w, tmp_path):                        # noqa: F811
    from tests.test_train_formula import ADMIN
    await w.feed(ADMIN, text="/reload")
    assert w.stock.resets == 1


# --- блиц ------------------------------------------------------------------------------

class OneSeries:
    def __init__(self, tasks):
        self.tasks = tasks

    async def take(self):
        if self.tasks is None:
            raise GenError("упал")
        return self.tasks


async def start(w, stock, monkeypatch):                                 # noqa: F811
    import datetime as dt

    from aiogram.types import Chat, Message

    from bot.handlers import blitz

    async def no_wait(_):
        return None
    monkeypatch.setattr(blitz.asyncio, "sleep", no_wait)                # отсчёт 3-2-1 и таймер карточки
    w.bot.sent.clear()
    m = Message(message_id=1, date=dt.datetime.now(), chat=Chat(id=STUDENT, type="private"), text="…").as_(w.bot)
    await blitz.start_blitz(m, STUDENT, w.db, dict(BANK), 25, stock)
    state = blitz.BLITZ.get(STUDENT)
    blitz.cancel_blitz(STUDENT)
    return state


async def test_blitz_uses_ready_series(w, monkeypatch):                 # noqa: F811
    tasks = BlitzRun(BANK, seed=3).tasks
    state = await start(w, OneSeries(tasks), monkeypatch)
    assert state.run.tasks == tasks and state.run.time_limit == 25
    assert any(type(m).__name__ == "SendPhoto" for m in w.bot.sent)     # первая карточка ушла


async def test_blitz_failure_keeps_current_task(w, monkeypatch):        # noqa: F811
    a = await w.task()
    assert await start(w, OneSeries(None), monkeypatch) is None
    assert "Не получилось" in w.bot.texts() and "blitz" in w.bot.buttons()
    assert train.ACTIVE.get(STUDENT) is a
