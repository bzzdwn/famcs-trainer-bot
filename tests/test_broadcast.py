import pytest

from bot.broadcast import MAX_LEN, BroadcastError, broadcast, recipients, validate_text
from bot.db import Student

ADMIN = 100
S = [Student(1, "Иванов Иван", 11, "2026-11-20T12:00:00+03:00"),
     Student(2, "Петров Пётр", 22, "2026-11-20T12:00:00+03:00"),
     Student(3, "Бовт Тимофей", ADMIN, "2026-11-20T12:00:00+03:00")]   # преподаватель тоже зарегистрирован


class FakeSend:
    def __init__(self, fail_for=()):
        self.sent, self.fail_for = [], set(fail_for)

    async def __call__(self, tg_id, text):
        if tg_id in self.fail_for:
            raise RuntimeError("Forbidden: bot was blocked by the user")
        self.sent.append((tg_id, text))


def test_validate_text():
    assert validate_text("  К среде пройдите блиц  \n") == "К среде пройдите блиц"
    assert validate_text("строка 1\nстрока 2") == "строка 1\nстрока 2"         # многострочный — ок
    for bad in ("", "   \n ", "x" * (MAX_LEN + 1)):
        with pytest.raises(BroadcastError) as e:
            validate_text(bad)
        assert str(e.value)
    assert len(validate_text("x" * MAX_LEN)) == MAX_LEN                        # ровно лимит — можно


def test_recipients_exclude_admin():
    assert [s.tg_id for s in recipients(S, ADMIN)] == [11, 22]


async def test_broadcast_all():
    send = FakeSend()
    text = "Блиц <по> Лопиталю *к среде*"                                      # спецсимволы — как есть
    rep = await broadcast(recipients(S, ADMIN), text, send, delay=0)
    assert send.sent == [(11, text), (22, text)]
    assert (rep.delivered, rep.failed) == (2, [])


async def test_broadcast_failure_does_not_stop():
    send = FakeSend(fail_for={11})
    rep = await broadcast(recipients(S, ADMIN), "текст", send, delay=0)
    assert send.sent == [(22, "текст")]                                        # второму дошло
    assert (rep.delivered, rep.failed) == (1, ["Иванов Иван"])


async def test_broadcast_empty_list():
    rep = await broadcast([], "текст", FakeSend(), delay=0)
    assert (rep.delivered, rep.failed) == (0, [])
