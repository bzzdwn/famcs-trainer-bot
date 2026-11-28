"""Этап 3: пояснение к каждому неверному варианту и лестница подсказок на шагах предела."""
import pytest
import sympy as sp

from bot.flow import HIDE_TEXT, TrainSession
from bot.render import PNG_MAGIC, render_latex
from core.checker import WRONG
from core.generator import generate
from core.limits import NO_TYPE, TYPES
from tests.test_limit_flow import check, cp, right
from tests.test_limits import RAW, TPL, inst
from tests.test_train_formula import STUDENT, w  # noqa: F401
from tests.test_limit_dialog import task

x = sp.Symbol("x", real=True)
STEP_ROLES = ("type", "reduce", "lhopital")


def all_steps():
    for num in sorted(RAW):
        for seed in range(4):
            i = generate(TPL[num], seed)
            for c in i.checkpoints:
                if c.role in STEP_ROLES:
                    yield num, i, c


def wrong(c):
    if c.role == "type":
        return [t for t in (*TYPES, NO_TYPE) if t not in c.correct]
    return [o for o in c.distractors if o not in c.alternatives]


# --- пояснения --------------------------------------------------------------------------

def test_every_wrong_option_is_explained():
    for num, i, c in all_steps():
        for o in wrong(c):
            assert c.notes and c.notes.get(o, "").strip(), (num, c.role, o)


def test_type_note_tells_real_limits():
    """Неверный тип: бот говорит, куда на самом деле стремятся части."""
    c = next(c for c in inst("1341").checkpoints if c.role == "type")      # x^p · ln x при x → +0: 0·∞
    n = c.notes["0/0"]
    assert "→ 0" in n and "−∞" in n and "0·∞" not in n                      # факты, а не название ответа
    last = [c for c in inst("1318").checkpoints if c.role == "type"][-1]   # после f′/g′: 3cos3x/(2cos2x) → 3/2
    assert "3/2" in last.notes["0/0"] or "→ 3" in last.notes["0/0"]


def test_no_type_chosen_wrongly():
    c = next(c for c in inst("1318").checkpoints if c.role == "type")
    assert "0" in c.notes[NO_TYPE] and "подстав" in c.notes[NO_TYPE].lower()


@pytest.mark.parametrize("origin, words", [("quotient", "(f/g)′"), ("num_only", "знаменатель"), ("den_only", "числитель")])
def test_lhopital_mistakes_named(origin, words):
    """Производная дроби целиком, продифференцирован только числитель или только знаменатель."""
    c = next(c for c in inst("1318").checkpoints if c.role == "lhopital")
    f, g = sp.sin(3 * x), sp.sin(2 * x)
    want = {"quotient": sp.diff(f / g, x), "num_only": sp.diff(f, x) / g, "den_only": f / sp.diff(g, x)}[origin]
    src = next(o for o in c.distractors if sp.simplify(sp.sympify(o, locals={"x": x}) - want) == 0)
    assert words in c.notes[src]


def test_inverted_reduction_named():
    c = next(c for c in inst("1354").checkpoints if c.role == "reduce")
    inv = next(o for o in c.distractors if sp.simplify(sp.sympify(o, locals={"x": x}) * sp.sympify(c.expected, locals={"x": x}) - 1) == 0)
    assert "перевёрн" in c.notes[inv]


def test_lost_logarithm_named():
    c = next(c for c in inst("1346").checkpoints if c.role == "reduce")
    assert any("ln" in c.notes[o] for o in wrong(c))


def test_dead_end_note_kept():
    c = next(c for c in inst("1341").checkpoints if c.role == "reduce")
    assert "неопределённость" in c.notes[c.dead[0]]


def test_notes_reach_student():
    i = inst("1341")
    s = TrainSession(i, seed=1, check=check)
    out = s.answer_button(s.current().options.index("0/0"))
    assert out.verdict.status == WRONG and "→ 0" in out.verdict.note


def test_integrals_have_no_notes():
    from tests.test_flow import BANK
    for t in list(BANK.values())[:20]:
        assert all(c.notes is None and not c.extra_hints for c in generate(t, 0).checkpoints)


# --- лестница -------------------------------------------------------------------------------

def ladder(i, role, k=0):
    s = TrainSession(i, seed=1, check=check)
    seen = 0
    while not (cp(s, i).role == role and seen == k):
        if cp(s, i).role == role:
            seen += 1
        s.answer_button(right(s, i))
    out = []
    while (h := s.take_hint()) is not None:
        out.append(h)
    return out, s


def test_type_ladder():
    hs, s = ladder(inst("1354"), "type", 1)                                # 0/0 после приведения
    assert [h.level for h in hs] == [1, 2, 3] and hs[-1].text == HIDE_TEXT and hs[-1].last
    assert "0/0" not in hs[0].text and "→" not in hs[0].text               # сначала — только «как узнать»
    assert "→ 0" in hs[1].text                                              # потом факты этой задачи
    assert len(s.current().hidden) >= 4


def test_lhopital_ladder_shows_derivatives():
    i = inst("1318")
    hs, s = ladder(i, "lhopital")
    assert [h.level for h in hs] == [1, 2, 3, 4] and hs[-1].text == HIDE_TEXT
    fp, gp = (h.latex for h in hs[1:3])
    assert "f" in hs[1].text and "g" in hs[2].text
    assert "\\cos" in fp and "3" in fp and "2" in gp                        # f′ = 3cos3x, g′ = 2cos2x
    for h in hs[1:3]:
        assert render_latex(h.latex).startswith(PNG_MAGIC)


def test_reduce_ladder():
    hs, _ = ladder(inst("1341"), "reduce")
    assert [h.level for h in hs] == [1, 2] and hs[-1].text == HIDE_TEXT


def test_hint_level_is_logged():
    i = inst("1318")
    s = TrainSession(i, seed=1, check=check)
    s.take_hint(); s.take_hint()
    assert s.answer_button(right(s, i)).log["hint_used"] == 2


# --- в боте -------------------------------------------------------------------------------------

async def test_wrong_type_explained_in_chat(w):                              # noqa: F811
    a = await task(w, "1341")
    await w.feed(cb=f"b:{a.session.current().options.index('0/0')}")
    assert "❌" in w.bot.texts() and "→ 0" in w.bot.texts()


async def test_derivative_hint_is_picture(w):                                # noqa: F811
    a = await task(w, "1318")
    await w.feed(cb=f"b:{right(a.session, a.session.inst)}")                # тип → шаг f′/g′
    for _ in range(2):
        await w.feed(cb="hint")
    assert any(type(m).__name__ == "SendPhoto" and "f′" in (m.caption or "") for m in w.bot.sent)


def test_two_sided_infinity_without_sign():
    """1/(1 − x) при x → 1: справа −∞, слева +∞ — в подсказке просто «∞»."""
    c = next(c for c in inst("1346").checkpoints if c.role == "type")
    facts = c.extra_hints[0][0]
    assert "показатель → ∞" in facts and "−∞" not in facts and "+∞" not in facts
