"""Пределы в боте: меню, шаги, кнопки типов, ввод ∞, /preview, редактор, генератор."""
import json
from pathlib import Path

import pytest

from bot.formula import parse_webapp_data
from bot.genpool import GenPool
from bot.handlers import train
from bot.preview import preview_items, preview_lines
from bot.render import PNG_MAGIC, render_lines
from core.generator import generate
from core.parser import parse_answer
from tests.test_limit_flow import no_steps
from tests.test_limits import RAW, TPL, inst
from tests.test_train_formula import BANK, STUDENT, show, w  # noqa: F401

LIM = {t.id: t for t in TPL.values()}
PAGE = Path(__file__).resolve().parent.parent / "docs" / "formula" / "index.html"


async def task(w, num="1354", direct=False, i=None):                        # noqa: F811
    """Активная задача на предел — через настоящую сессию бота."""
    from bot.flow import TrainSession
    from tests.test_limit_flow import check
    i = i or inst(num)
    st = await w.db.get_student_by_tg(STUDENT)
    iid = await w.db.save_instance(i, "limits.lhopital", "A")
    s = TrainSession(i, seed=1, check=check, direct=direct)
    a = train.Active(s, await w.db.start_session(st.id, iid, s.mode), "limits.lhopital", "A")
    train.ACTIVE[STUDENT] = a
    w.bank = {**BANK, **LIM}
    return a


async def press_right(w, a):                                               # noqa: F811
    from tests.test_limit_flow import right
    await w.feed(cb=f"b:{right(a.session, a.session.inst)}")


async def test_task_from_menu(w):                                          # noqa: F811
    w.bank = {**BANK, **LIM}
    await w.feed(cb="l:limits.lhopital:A")
    assert w.bank[train.OFFERED[STUDENT].inst.template_id].limit is not None
    assert {"path:steps", "path:direct"} <= set(w.bot.buttons())


async def test_no_steps_hides_button(w, monkeypatch):                      # noqa: F811
    w.bank = {**BANK, **LIM}

    async def only_direct(tpl, seed):
        return no_steps()
    monkeypatch.setattr(w.gen, "instance", only_direct)
    await w.feed(cb="l:limits.lhopital:A")
    assert "path:direct" in w.bot.buttons() and "path:steps" not in w.bot.buttons()


async def test_no_steps_after_error_does_not_offer_steps(w):               # noqa: F811
    a = await task(w, direct=True, i=no_steps())
    await show(w, a, after_wrong=True)
    assert "steps" not in w.bot.buttons()


async def test_type_buttons(w):                                            # noqa: F811
    a = await task(w, "1318")
    await show(w, a)
    assert [b for b in w.bot.buttons() if b.startswith("b:")] == [f"b:{k}" for k in range(8)]
    rows = w.bot.sent[-1].reply_markup.inline_keyboard
    assert max(len(r) for r in rows) <= 4 and len(rows) <= 6               # восемь типов компактно, а не столбиком
    assert "hint" in w.bot.buttons() and "drop" in w.bot.buttons()


async def test_next_type_question_comes_with_picture(w):                   # noqa: F811
    a = await task(w, "1354")
    await press_right(w, a)
    await press_right(w, a)
    photos = [m for m in w.bot.sent if type(m).__name__ == "SendPhoto"]
    assert photos and "тип" in (photos[-1].caption or "").lower()
    assert len([b for b in w.bot.buttons() if b.startswith("b:")]) == 8


async def test_walk_to_answer(w):                                          # noqa: F811
    a = await task(w, "1354")
    while a.session.current().kind == "buttons":
        await press_right(w, a)
    assert "editor" in w.bot.buttons()
    await w.feed(text="1/2")
    assert "Ты имел в виду это?" in w.bot.texts()
    await w.feed(cb="confirm")
    assert "Верно" in w.bot.texts()
    assert {x[1] for x in await w.attempts()} == {"ok"}


async def test_infinity_by_text(w):                                        # noqa: F811
    await task(w, direct=True)
    await w.feed(text="∞")
    assert "Ты имел в виду это?" in w.bot.texts()


async def test_infinity_by_editor(w):                                      # noqa: F811
    await task(w, direct=True)
    await w.feed(web=r"-\infty")
    assert "Ты имел в виду это?" in w.bot.texts()


async def test_approximation_message_keeps_attempts(w):                    # noqa: F811
    await task(w, "1346", direct=True)
    for _ in range(3):
        await w.feed(text="0.3679")
        await w.feed(cb="confirm")
        assert "точное" in w.bot.texts().lower()
    await w.feed(text="1/e")
    await w.feed(cb="confirm")
    assert "Верно" in w.bot.texts()


async def test_two_attempts_then_answer(w):                                # noqa: F811
    await task(w, "1318", direct=True)
    for k, want in ((7, "❌"), (8, "вот ответ")):
        await w.feed(text=str(k))
        await w.feed(cb="confirm")
        assert want in w.bot.texts()
    assert STUDENT not in train.ACTIVE


async def test_dead_end_explained(w):                                      # noqa: F811
    from tests.test_limit_flow import cp, option
    a = await task(w, "1341")
    await press_right(w, a)
    s, i = a.session, a.session.inst
    await w.feed(cb=f"b:{option(s, i, cp(s, i).dead[0])}")
    assert "неопределённость" in w.bot.texts()


async def test_hints_to_the_end(w):                                        # noqa: F811
    await task(w, "1354", direct=True)
    for _ in range(6):
        await w.feed(cb="hint")
    assert "reveal" in w.bot.buttons()


@pytest.mark.parametrize("latex, want", [(r"\infty", "oo"), (r"-\infty", "-oo"), (r"+\infty", "oo"), (r"e^{-\frac16}", "exp(-1/6)")])
def test_editor_value(latex, want):
    import sympy as sp
    assert parse_answer(parse_webapp_data(json.dumps({"v": 1, "latex": latex}))) == sp.sympify(want)


def test_page_has_infinity_key():
    assert r"\\infty" in PAGE.read_text(encoding="utf-8")


@pytest.mark.parametrize("num", sorted(RAW))
def test_preview_command(num):
    it = preview_items(TPL[num], 1, seed=0)[0]
    lines = preview_lines(it)
    assert render_lines(lines).startswith(PNG_MAGIC)
    assert any("✅" in l for l in lines) and not any("+ C" in l for l in lines)


async def test_generator_process_makes_same_limit():
    g = GenPool(timeout=20)
    try:
        for num in ("1318", "1354"):
            assert await g.instance(TPL[num], 3) == generate(TPL[num], 3)
    finally:
        g.close()
