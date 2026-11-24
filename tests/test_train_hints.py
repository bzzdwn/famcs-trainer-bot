"""Подсказки в диалоге: доступны с начала шага, каждое нажатие — следующий уровень."""
import pytest

from bot.flow import TrainSession
from core.generator import build
from tests.test_train_formula import BANK, GOOD, show, w  # noqa: F401  (фикстура w)


def opts(a):
    return a.session.current().options


# --- сессия -----------------------------------------------------------------------------

def session(tid="int_exp_lcm_01", params=None, **kw):
    inst = build(BANK[tid], params or {"m": 6})
    from tests.test_train_formula import quick_check
    return inst, TrainSession(inst, seed=1, check=quick_check, **kw)


def right(s, inst):
    from core.latex import ru_latex
    p, cp = s.current(), inst.checkpoints[s.current().idx]
    return next(i for i, o in enumerate(p.options) if o in (cp.correct or (ru_latex(cp.expected, cp.var),)))


def test_button_step_second_hint_hides_two_wrong():
    inst, s = session()
    h1, h2 = s.take_hint(), s.take_hint()
    assert (h1.level, h1.text) == (1, inst.checkpoints[0].hint) and h2.level == 2 and h2.last
    hidden = s.current().hidden
    assert len(hidden) == 2 and right(s, inst) not in hidden
    assert s.take_hint() is None                                        # дальше только «Показать ответ»


def test_formula_step_hides_too():
    inst, s = session()
    s.answer_button(right(s, inst))
    s.take_hint(); s.take_hint()
    assert len(s.current().hidden) == 2 and right(s, inst) not in s.current().hidden


def test_hidden_button_cannot_be_pressed():
    inst, s = session()
    s.take_hint(); s.take_hint()
    with pytest.raises(ValueError):
        s.answer_button(s.current().hidden[0])


def test_hidden_does_not_leak_to_next_step_and_is_stable():
    inst, s = session()
    s.take_hint(); s.take_hint()
    assert s.current().hidden == s.current().hidden
    s.answer_button(right(s, inst))
    assert s.current().hidden == ()


def test_all_correct_options_survive():
    inst, s = session("mix_sin_sub_01", {"p": 1})                       # два верных варианта из четырёх
    s.take_hint(); s.take_hint()
    left = [o for i, o in enumerate(s.current().options) if i not in s.current().hidden]
    assert set(inst.checkpoints[0].correct) <= set(left) and len(left) == 3


def test_level_goes_to_log_and_resets():
    inst, s = session()
    s.take_hint(); s.take_hint()
    out = s.answer_button(right(s, inst))
    assert out.log["hint_used"] == 2
    assert s.answer_button(right(s, inst)).log["hint_used"] == 0


def test_wrong_attempt_keeps_ladder_position():
    inst, s = session()
    s.take_hint()
    wrong = next(i for i in range(4) if i != right(s, inst))
    assert s.answer_button(wrong).log["hint_used"] == 1
    assert s.take_hint().level == 2                                     # не начинаем сначала


def test_final_step_ladder():
    inst, s = session()
    s.answer_button(right(s, inst)); s.answer_button(right(s, inst))
    levels = [s.take_hint() for _ in range(4)]
    assert [h and h.level for h in levels] == [1, 2, 3, None] and levels[2].last
    assert s.summary().hints == 1                                       # точка с подсказкой — одна


def test_switch_to_steps_restarts_ladder():
    inst, s = session(direct=True)
    s.take_hint(); s.take_hint()
    s.switch_to_steps()
    assert s.take_hint().text == inst.checkpoints[0].hint


def test_level_c_input_step_has_template_hint_only():
    inst, s = session("mix_exp_pow_01", {"a": 1}, free_input=True)
    s.answer_button(right(s, inst))
    assert s.current().kind == "input"
    h = s.take_hint()
    assert h.text == inst.checkpoints[1].hint and h.last and s.take_hint() is None


def test_method_step_hides_wrong_but_keeps_alternatives():
    from bot.topics import build_method_step
    inst = build(BANK["mix_irr_odd_01"], {"k": 1, "a": 1})
    tpl = BANK["mix_irr_odd_01"]
    from tests.test_train_formula import quick_check
    s = TrainSession(inst, seed=1, check=quick_check, method_step=build_method_step(BANK, tpl))
    n = len(s.current().options)
    s.take_hint()
    assert s.take_hint().level == 2
    left = {o for i, o in enumerate(s.current().options) if i not in s.current().hidden}
    assert {tpl.blitz_method, *tpl.method_alternatives} <= left and len(left) <= n // 2
    s.answer_button(s.current().options.index(tpl.blitz_method))
    assert s.current().hidden == () and s.take_hint().level == 1           # лестница следующего шага — сначала


# --- диалог --------------------------------------------------------------------------------

@pytest.mark.parametrize("direct", [False, True])
async def test_hint_button_from_the_start(w, direct):                   # noqa: F811
    a = await w.task(tid="int_exp_lcm_01", params={"m": 6}, direct=direct)
    await show(w, a)
    assert "hint" in w.bot.buttons() and "reveal" not in w.bot.buttons()   # «Показать ответ» — не сразу


async def test_hint_shows_text_then_formula(w):                         # noqa: F811
    a = await w.task(tid="int_exp_lcm_01", params={"m": 6})
    await w.feed(cb="hint")                                             # «Сразу ответ»: сначала метод
    assert a.session.inst.checkpoints[0].hint.split(":")[0] in w.bot.texts()
    await w.feed(cb="hint")
    assert any(type(m).__name__ == "SendPhoto" for m in w.bot.sent)     # интеграл после замены — картинкой
    assert await w.attempts() == []                                     # подсказка попытку не тратит


async def test_hint_offers_next_then_reveal(w):                         # noqa: F811
    await w.task(tid="int_exp_lcm_01", params={"m": 6})
    seen = []
    for _ in range(5):
        await w.feed(cb="hint")
        seen.append(("hint" in w.bot.buttons(), "reveal" in w.bot.buttons()))
    assert seen == [(True, False)] * 4 + [(False, True)]                # «Ещё подсказка», в конце «Показать ответ»


async def test_extra_press_after_last_level(w):                         # noqa: F811
    await w.task(tid="mix_exp_trig_01")
    for _ in range(8):
        await w.feed(cb="hint")
    assert "reveal" in w.bot.buttons() and "больше нет" in w.bot.texts().lower()


async def test_fifty_fifty_updates_buttons(w):                          # noqa: F811
    a = await w.task(tid="int_exp_lcm_01", params={"m": 6}, direct=False)
    await w.feed(cb="hint"); await w.feed(cb="hint")
    answers = [b for b in w.bot.buttons() if b.startswith("b:")]
    assert len(answers) == 2                                            # осталось два варианта
    hidden = a.session.current().hidden
    assert {int(b[2:]) for b in answers} == set(range(4)) - set(hidden)  # номера прежние


async def test_hidden_stay_hidden_after_wrong_answer(w):                # noqa: F811
    a = await w.task(tid="int_exp_lcm_01", params={"m": 6}, direct=False)
    await w.feed(cb="hint"); await w.feed(cb="hint")
    left = [i for i in range(4) if i not in a.session.current().hidden]
    wrong = next(i for i in left if opts(a)[i] not in a.session.inst.checkpoints[0].correct)
    await w.feed(cb=f"b:{wrong}")
    assert len([b for b in w.bot.buttons() if b.startswith("b:")]) == 2


async def test_level_saved_in_db(w):                                    # noqa: F811
    await w.task()
    await w.feed(cb="hint")
    await w.feed(web=GOOD); await w.feed(cb="confirm")
    async with w.db.conn.execute("SELECT hint_used FROM attempts") as cur:
        assert [r[0] for r in await cur.fetchall()] == [1]


async def test_hint_keeps_pending_formula(w):                           # noqa: F811
    a = await w.task()
    await w.feed(web=GOOD)
    await w.feed(cb="hint")
    assert a.pending                                                    # «Да, проверь» всё ещё работает


async def test_hint_without_task(w):                                    # noqa: F811
    await w.feed(cb="hint")
    assert not w.bot.buttons()
