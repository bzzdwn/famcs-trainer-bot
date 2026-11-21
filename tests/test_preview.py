from pathlib import Path

from bot.preview import MAX_N, preview_items, preview_lines
from bot.render import PNG_MAGIC, render_lines
from core.templates import load_templates

BANK = load_templates(Path(__file__).resolve().parent.parent / "data" / "templates")


def test_preview_items_universal():
    tpl = BANK["int_trig_universal_01"]
    items = preview_items(tpl, n=5, seed=1)
    assert len(items) == 5 and len({tuple(sorted(i.params.items())) for i in items}) > 1
    it = items[0]
    assert it.statement_latex.startswith("\\int")
    choice, trans, ans = it.steps
    assert (choice.is_formula, choice.correct, choice.distractors) == (False, "t = tg(x/2)", ())
    assert trans.is_formula and len(trans.distractors) == 3 and trans.correct not in trans.distractors
    assert ans.is_formula and ans.correct.endswith("+ C") and ans.distractors == ()
    assert [s.title for s in it.steps] == ["Шаг 1 · выбор", "Шаг 2 · интеграл после замены", "Шаг 3 · ответ"]


def test_preview_deterministic_and_capped():
    tpl = BANK["int_parts_poly_exp_01"]
    assert preview_items(tpl, 3, seed=7) == preview_items(tpl, 3, seed=7)
    assert len(preview_items(tpl, 100, seed=0)) == MAX_N


def test_preview_lines_and_render_all_templates():
    for tid, tpl in BANK.items():
        for it in preview_items(tpl, 2, seed=0):
            lines = preview_lines(it)
            assert any("✅" in l for l in lines) and any(str(v) in lines[0] for v in it.params.values())
            assert render_lines(lines).startswith(PNG_MAGIC), tid       # кириллица + формулы рендерятся


def test_preview_lines_content():
    it = preview_items(BANK["int_trig_universal_01"], 1, seed=3)[0]
    lines = preview_lines(it)
    text = "\n".join(lines)
    assert text.count("❌") == 3                                          # три дистрактора шага по t
    trans = it.steps[1]
    assert f"${trans.correct}$" in text                                   # формулы — в $…$ для mathtext
    assert all(f"${d}$" in text for d in trans.distractors)
