"""/preview <id>: несколько экземпляров шаблона «глазами студента» — для ревью новых шаблонов."""
import random
from dataclasses import dataclass

from core.generator import generate
from core.latex import ru_latex
from core.templates import Template

MAX_N = 10
CTYPE_RU = {"choice": "выбор", "expr": "выражение", "transformed_integrand": "интеграл после замены",
            "antiderivative": "ответ", "value": "значение предела"}


@dataclass(frozen=True)
class PreviewStep:
    title: str                       # «Шаг 1 · выбор»
    prompt: str
    correct: str                     # подпись (choice) или LaTeX
    distractors: tuple[str, ...]     # LaTeX дистракторов (для expr / transformed_integrand)
    is_formula: bool


@dataclass(frozen=True)
class PreviewItem:
    params: dict
    statement_latex: str
    steps: tuple[PreviewStep, ...]


def preview_items(tpl: Template, n: int = 5, seed: int = 0) -> list[PreviewItem]:
    """n (≤ MAX_N) экземпляров: условие и все шаги с верным ответом и дистракторами."""
    rng = random.Random(seed)
    items = []
    for _ in range(min(n, MAX_N)):
        inst = generate(tpl, rng.randrange(2**32))
        steps = []
        for i, cp in enumerate(inst.checkpoints):
            title = f"Шаг {i + 1} · {CTYPE_RU.get(cp.type, cp.type)}"
            if cp.type == "choice":
                steps.append(PreviewStep(title, cp.prompt, " / ".join(cp.correct), (), False))
            elif cp.type == "antiderivative":
                steps.append(PreviewStep(title, cp.prompt, ru_latex(cp.reference, cp.var) + " + C", (), True))
            else:
                tex = lambda s: (cp.labels or {}).get(s) or ru_latex(s, cp.var)
                steps.append(PreviewStep(title, cp.prompt, tex(cp.expected), tuple(map(tex, cp.distractors)), True))
        items.append(PreviewItem(dict(inst.params), inst.statement_latex, tuple(steps)))
    return items


def preview_lines(item: PreviewItem) -> list[str]:
    """Строки для render_lines: обычный текст + формулы в $…$ (matplotlib mathtext)."""
    lines = ["Параметры: " + ", ".join(f"{k} = {v}" for k, v in item.params.items()),
             f"Условие:  ${item.statement_latex}$", ""]
    for st in item.steps:
        lines.append(f"{st.title}: {st.prompt}")
        lines.append(f"    ✅ ${st.correct}$" if st.is_formula else f"    ✅ {st.correct}")
        lines.extend(f"    ❌ ${d}$" for d in st.distractors)
    return lines
