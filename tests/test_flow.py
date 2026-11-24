import copy
from pathlib import Path

import pytest

from bot.flow import MAX_ATTEMPTS, TrainSession
from core.checker import TIMEOUT, Verdict, check_antiderivative, check_expr
from core.generator import generate
from core.latex import ru_latex
from core.templates import load_templates, parse_template
from tests.test_templates import VALID

BANK = load_templates(Path(__file__).resolve().parent.parent / "data" / "templates")
UNIVERSAL = "int_trig_universal_01"


def direct_check(kind, user, expected, var, domain):
    """Без пула процессов: в тестах таймауты не нужны."""
    if kind == "antiderivative":
        return check_antiderivative(user, expected, var, domain, symbolic=False)
    return check_expr(user, expected, var, domain, symbolic=False)


def session(tid=UNIVERSAL, seed=0, shuffle_seed=1, **kw):
    inst = generate(BANK[tid], seed)
    return inst, TrainSession(inst, seed=shuffle_seed, check=kw.pop("check", direct_check), **kw)


def correct_button(s, inst):
    """Индекс правильной кнопки на текущей точке."""
    p = s.current()
    cp = inst.checkpoints[p.idx]
    if cp.type == "choice":
        return next(i for i, o in enumerate(p.options) if o in cp.correct)
    return p.options.index(ru_latex(cp.expected, cp.var))


def wrong_button(s, inst):
    return next(i for i in range(len(s.current().options)) if i != correct_button(s, inst))


def reference_answer(s, inst):
    return inst.checkpoints[s.current().idx].reference + " + C"


def solve_all(s, inst):
    while not s.finished:
        p = s.current()
        if p.kind == "buttons":
            out = s.answer_button(correct_button(s, inst))
        else:
            cp = inst.checkpoints[p.idx]
            out = s.answer_text(cp.reference + " + C" if cp.type == "antiderivative" else cp.expected)
        assert out.verdict.status == "ok", (inst.template_id, p, out)


# --- прохождение целиком ---------------------------------------------------

@pytest.mark.parametrize("tid", sorted(BANK))
def test_full_run_with_references(tid):
    for seed in range(5):
        inst, s = session(tid, seed)
        solve_all(s, inst)
        sm = s.summary()
        assert sm.total == len(inst.checkpoints) and sm.first_try == sm.total
        assert sm.revealed == 0 and sm.status == "solved"
        assert s.current().kind == "done"


def test_prompts_universal():
    inst, s = session()
    p = s.current()
    assert (p.kind, p.idx, p.cp_type, p.formulas) == ("buttons", 0, "choice", False)
    assert sorted(p.options) == sorted(inst.checkpoints[0].options)
    s.answer_button(correct_button(s, inst))
    p = s.current()
    assert (p.kind, p.cp_type, p.formulas) == ("buttons", "transformed_integrand", True)
    assert len(p.options) == 4
    assert p.options.count(ru_latex(inst.checkpoints[1].expected, "t")) == 1
    s.answer_button(correct_button(s, inst))
    p = s.current()
    assert (p.kind, p.cp_type) == ("input", "antiderivative")


# --- попытки ---------------------------------------------------------------

def test_correct_button():
    inst, s = session()
    out = s.answer_button(correct_button(s, inst))
    assert out.verdict.status == "ok" and out.spent and out.attempt_no == 1 and out.moved_on


def test_wrong_button_spends_attempt():
    inst, s = session()
    out = s.answer_button(wrong_button(s, inst))
    assert out.verdict.status == "wrong" and out.spent and out.attempt_no == 1 and not out.moved_on
    out = s.answer_button(correct_button(s, inst))
    assert out.attempt_no == 2 and out.moved_on
    assert s.summary().first_try == 0


def test_three_wrong_forced_reveal():
    inst, s = session()
    for k in range(1, MAX_ATTEMPTS + 1):
        out = s.answer_button(wrong_button(s, inst))
        assert out.attempt_no == k
    assert out.forced_reveal and out.moved_on and out.answer
    assert s.current().idx == 1
    sm = s.summary()
    assert sm.revealed == 1 and sm.status == "revealed"


@pytest.mark.parametrize("raw", ["2+", "atan(", "sinx + C"])
def test_parse_error_not_spent(raw):
    inst, s = session("int_parts_poly_exp_01")
    s.answer_button(correct_button(s, inst))
    out = s.answer_text(raw)
    assert out.verdict.status == "parse_error" and not out.spent and not out.moved_on
    out = s.answer_text(reference_answer(s, inst))
    assert out.verdict.status == "ok" and out.attempt_no == 1
    assert s.summary().first_try == 2


def test_timeout_not_spent():
    inst, s = session("int_parts_poly_exp_01", check=lambda *a: Verdict(TIMEOUT, "упрости"))
    s.answer_button(correct_button(s, inst))
    for _ in range(MAX_ATTEMPTS + 2):
        out = s.answer_text("x + C")
        assert out.verdict.status == TIMEOUT and not out.spent and not out.moved_on


def test_no_const_spent():
    inst, s = session("int_parts_poly_exp_01")
    s.answer_button(correct_button(s, inst))
    out = s.answer_text(inst.checkpoints[1].reference)
    assert out.verdict.status == "no_const" and out.spent and out.attempt_no == 1


@pytest.mark.parametrize("user, status", [("-({ref}) + C", "sign"), ("2*({ref}) + C", "factor"), ("x + C", "wrong")])
def test_mistakes_spent(user, status):
    inst, s = session("int_parts_poly_exp_01")
    s.answer_button(correct_button(s, inst))
    out = s.answer_text(user.format(ref=inst.checkpoints[1].reference))
    assert out.verdict.status == status and out.spent


# --- превью ----------------------------------------------------------------

def test_preview():
    inst, s = session("int_parts_poly_exp_01")
    s.answer_button(correct_button(s, inst))
    ok = s.preview("(x-1)e^x + C")
    assert ok.ok and "C" in ok.latex and "e^{x}" in ok.latex
    no_c = s.preview("(x-1)e^x")
    assert no_c.ok and "C" not in no_c.latex     # превью честно показывает, что C нет
    bad = s.preview("sinx")
    assert not bad.ok and bad.error


# --- подсказки и «показать ответ» -----------------------------------------

def test_hint():
    inst, s = session()
    assert s.take_hint().text == inst.checkpoints[0].hint
    out = s.answer_button(correct_button(s, inst))
    assert out.log["hint_used"] == 1
    out = s.answer_button(correct_button(s, inst))
    assert out.log["hint_used"] == 0         # подсказка не «перетекает» на следующую точку
    assert s.summary().hints == 1


def test_final_answer_has_hint():
    inst, s = session("int_parts_poly_exp_01")
    assert s.summary().hints == 0
    s.answer_button(correct_button(s, inst))
    assert s.take_hint().level == 1              # у финального ответа теперь своя лестница (core.hints)
    assert s.summary().hints == 1


def test_reveal():
    inst, s = session()
    out = s.reveal()
    assert out.moved_on and out.answer == "t = tg(x/2)" and s.current().idx == 1
    out = s.reveal()
    assert out.answer == ru_latex(inst.checkpoints[1].expected, "t")
    assert s.summary().revealed == 2


# --- перемешивание ---------------------------------------------------------

def test_shuffle_deterministic():
    _, a = session(shuffle_seed=5)
    _, b = session(shuffle_seed=5)
    assert a.current().options == b.current().options
    orders = {session(shuffle_seed=k)[1].current().options for k in range(20)}
    assert len(orders) > 1


def test_multiple_correct():
    raw = copy.deepcopy(VALID)
    raw["checkpoints"][0].update(options=["A", "B", "C"], correct=["A", "B"])
    inst = generate(parse_template(raw), 0)
    for label in ("A", "B"):
        s = TrainSession(inst, seed=0, check=direct_check)
        out = s.answer_button(s.current().options.index(label))
        assert out.verdict.status == "ok"


# --- лог -------------------------------------------------------------------

def test_log_fields():
    inst, s = session("int_parts_poly_exp_01")
    out = s.answer_button(correct_button(s, inst))
    assert out.log == {"checkpoint_idx": 0, "checkpoint_type": "choice", "raw_input": "u = px + q",
                       "verdict": "ok", "input_method": "button", "hint_used": False, "attempt_no": 1}
    out = s.answer_text("x + C", input_method="text")
    assert out.log["raw_input"] == "x + C" and out.log["input_method"] == "text"
    assert out.log["checkpoint_type"] == "antiderivative"


# --- уровень C: свободный ввод ----------------------------------------------

def test_free_input_skippable():
    inst, s = session(free_input=True)
    s.answer_button(correct_button(s, inst))
    p = s.current()
    assert p.kind == "input" and p.cp_type == "transformed_integrand"
    out = s.answer_text("t^5")                     # «свой путь»: не сошлось, но skippable
    assert out.moved_on and not out.spent and not out.forced_reveal
    sm = s.summary()
    assert sm.skipped == 1 and sm.revealed == 0


def test_free_input_correct():
    inst, s = session(free_input=True)
    s.answer_button(correct_button(s, inst))
    out = s.answer_text(inst.checkpoints[1].expected)
    assert out.verdict.status == "ok" and out.moved_on


# --- защита от неверных вызовов -------------------------------------------

def test_wrong_kind():
    inst, s = session()
    with pytest.raises(ValueError):
        s.answer_text("x")                         # сейчас кнопки
    with pytest.raises(ValueError):
        s.answer_button(10)                        # нет такой кнопки


def test_finished():
    inst, s = session()
    solve_all(s, inst)
    with pytest.raises(RuntimeError):
        s.answer_button(0)


# --- выход из задачи --------------------------------------------------------

def test_drop():
    inst, s = session()
    s.answer_button(correct_button(s, inst))
    assert not s.dropped
    s.drop()
    assert s.dropped and s.finished and s.current().kind == "done"
    with pytest.raises(RuntimeError):
        s.answer_button(0)
    with pytest.raises(RuntimeError):
        s.answer_text("x + C")
    with pytest.raises(RuntimeError):
        s.reveal()
    assert s.take_hint() is None


def test_drop_before_any_answer():
    _, s = session()
    s.drop()                                   # «открыл по ошибке»
    assert s.dropped and s.finished


def test_drop_direct():
    _, s = session(direct=True)
    s.drop()
    assert s.dropped and s.finished


# --- «Сразу ответ» ----------------------------------------------------------

def last_idx(inst):
    return len(inst.checkpoints) - 1


def test_mode():
    assert session()[1].mode == "train"
    assert session(direct=True)[1].mode == "train_direct"


def test_direct_first_prompt():
    inst, s = session(direct=True)
    p = s.current()
    assert (p.kind, p.cp_type, p.idx) == ("input", "antiderivative", last_idx(inst))
    assert s.direct


@pytest.mark.parametrize("tid", sorted(BANK))
def test_direct_solve(tid):
    inst, s = session(tid, direct=True)
    out = s.answer_text(inst.checkpoints[-1].reference + " + C")
    assert out.verdict.status == "ok" and out.attempt_no == 1 and out.moved_on
    assert out.log["checkpoint_idx"] == last_idx(inst)
    assert s.finished and not s.dropped
    sm = s.summary()
    assert (sm.total, sm.first_try, sm.revealed, sm.status) == (1, 1, 0, "solved")


def test_direct_hint_is_method_hint():
    inst, s = session(direct=True)
    assert s.take_hint().text == inst.checkpoints[0].hint
    out = s.answer_text("x + C")
    assert out.log["hint_used"] == 1
    assert s.summary().hints == 1


def test_direct_three_wrong_forced_reveal():
    inst, s = session(direct=True)
    for _ in range(MAX_ATTEMPTS):
        out = s.answer_text("x + C")
    assert out.forced_reveal and s.finished
    sm = s.summary()
    assert (sm.total, sm.revealed, sm.status) == (1, 1, "revealed")


def test_direct_switch_to_steps_keeps_final_attempts():
    inst, s = session(direct=True)
    out = s.answer_text("x + C")
    assert out.verdict.status == "wrong" and out.attempt_no == 1
    s.switch_to_steps()
    assert not s.direct and s.mode == "train_direct"     # в БД — как начинали
    p = s.current()
    assert (p.idx, p.cp_type) == (0, "choice")
    s.answer_button(correct_button(s, inst))
    s.answer_button(correct_button(s, inst))
    assert s.current().cp_type == "antiderivative"
    out = s.answer_text("x + C")
    assert out.attempt_no == 2 and not out.forced_reveal
    out = s.answer_text("x + C")
    assert out.attempt_no == 3 and out.forced_reveal     # всего 3 на финальный ответ
    sm = s.summary()
    assert (sm.total, sm.first_try, sm.revealed) == (3, 2, 1)


def test_direct_switch_then_solve():
    inst, s = session(direct=True)
    s.answer_text("x + C")
    s.switch_to_steps()
    solve_all(s, inst)
    sm = s.summary()
    assert (sm.total, sm.first_try, sm.status) == (3, 2, "solved")   # финал — не с первой попытки


def test_direct_switch_hint_resets():
    inst, s = session(direct=True)
    s.take_hint()
    s.switch_to_steps()
    out = s.answer_button(correct_button(s, inst))
    assert out.log["hint_used"] == 0             # подсказка была к финалу, не к выбору замены


def test_switch_errors():
    _, s = session()
    with pytest.raises(ValueError):
        s.switch_to_steps()                            # уже по шагам
    inst, s = session(direct=True)
    s.answer_text(inst.checkpoints[-1].reference + " + C")
    with pytest.raises(RuntimeError):
        s.switch_to_steps()                            # задача решена


def test_reveal_is_logged():
    inst, s = session()
    out = s.reveal()
    assert out.log == {"checkpoint_idx": 0, "checkpoint_type": "choice", "raw_input": "",
                       "verdict": "revealed", "input_method": "button", "hint_used": False, "attempt_no": 1}


# --- «Вразнобой»: шаг выбора метода -------------------------------------------

from bot.flow import METHOD_IDX, NOTE_METHOD_ALT
from bot.topics import build_method_step, method_options


def mix_session(tid=UNIVERSAL, seed=0, **kw):
    inst = generate(BANK[tid], seed)
    step = build_method_step(BANK, BANK[tid])
    return inst, TrainSession(inst, seed=1, check=direct_check, method_step=step, **kw)


def method_btn(s, method):
    return s.current().options.index(method)


def test_method_step_first():
    inst, s = mix_session()
    p = s.current()
    assert (p.kind, p.idx, p.cp_type, p.formulas) == ("buttons", METHOD_IDX, "method", False)
    assert p.options == method_options(BANK)                         # все методы, порядок фиксирован
    assert p.text == "Каким методом будешь решать?"


def test_method_correct():
    inst, s = mix_session()
    out = s.answer_button(method_btn(s, BANK[UNIVERSAL].blitz_method))
    assert out.verdict.status == "ok" and out.verdict.note is None and out.moved_on
    assert out.log == {"checkpoint_idx": METHOD_IDX, "checkpoint_type": "method",
                       "raw_input": "универсальная замена", "verdict": "ok", "input_method": "button",
                       "hint_used": False, "attempt_no": 1}
    assert (s.current().idx, s.current().cp_type) == (0, "choice")   # дальше — шаги шаблона без сдвига


def test_method_alternative():
    inst, s = mix_session("int_trig_tan_01")
    out = s.answer_button(method_btn(s, "универсальная замена"))
    assert out.verdict.status == "ok" and out.verdict.note == NOTE_METHOD_ALT and out.moved_on
    assert s.summary().first_try == 1                                # засчитано как верно


def test_method_wrong_three_times():
    inst, s = mix_session()
    wrong = method_btn(s, "по частям")
    for k in range(1, MAX_ATTEMPTS + 1):
        out = s.answer_button(wrong)
        assert out.verdict.status == "wrong" and out.attempt_no == k
    assert out.forced_reveal and out.answer == "универсальная замена"
    assert s.current().idx == 0


def test_method_hint_and_reveal():
    inst, s = mix_session()
    assert s.take_hint().text == inst.checkpoints[0].hint
    out = s.reveal()
    assert out.answer == "универсальная замена" and out.log["checkpoint_idx"] == METHOD_IDX
    assert s.current().idx == 0


def test_mix_full_solve():
    inst, s = mix_session()
    s.answer_button(method_btn(s, BANK[UNIVERSAL].blitz_method))
    solve_all(s, inst)
    sm = s.summary()
    assert (sm.total, sm.first_try, sm.status) == (len(inst.checkpoints) + 1, len(inst.checkpoints) + 1, "solved")


def test_mix_direct_skips_method_until_switch():
    inst, s = mix_session(direct=True)
    assert s.current().cp_type == "antiderivative"                   # «Сразу ответ» — без вопроса о методе
    s.answer_text("x + C")
    s.switch_to_steps()
    assert (s.current().idx, s.current().cp_type) == (METHOD_IDX, "method")


def test_mix_direct_solved_summary():
    inst, s = mix_session(direct=True)
    s.answer_text(inst.checkpoints[-1].reference + " + C")
    assert s.summary().total == 1                                    # метод не спрашивали — не считаем
