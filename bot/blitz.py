"""Блиц: 10 задач, 4 кнопки с методами, 10 секунд на ответ. Чистая логика, без Telegram."""
import random
from dataclasses import dataclass

from core.generator import Instance, generate
from core.templates import Template

BLITZ_SIZE = 10
TIME_LIMIT = 10.0        # секунд; ответ позже — пропуск
N_OPTIONS = 4
OK, WRONG, SKIP = "ok", "wrong", "skip"


COUNTDOWN = 3            # последние секунды задачи: «3…», «2…», «1…»


def countdown_marks(limit: float = TIME_LIMIT, n: int = COUNTDOWN) -> list[tuple[float, str]]:
    """[(секунда от показа задачи, текст), …] для отсчёта в конце: при 10 с — (7, '3…'), (8, '2…'), (9, '1…')."""
    return [(float(limit - k), f"{k}…") for k in range(n, 0, -1)]


@dataclass(frozen=True)
class BlitzTask:
    template_id: str
    instance: Instance
    correct: str                  # верный метод (blitz_method шаблона)
    options: tuple[str, ...]      # N_OPTIONS различных методов, верный ровно один
    hint: str | None              # признак метода — hint первой точки шаблона


@dataclass(frozen=True)
class BlitzAnswer:
    idx: int
    chosen: str | None            # None — время вышло
    verdict: str                  # ok | wrong | skip
    elapsed: float


@dataclass(frozen=True)
class Mistake:
    idx: int
    statement_latex: str
    correct: str
    chosen: str | None
    hint: str | None


@dataclass(frozen=True)
class BlitzSummary:
    score: int
    total: int
    mistakes: tuple[Mistake, ...]


class BlitzRun:
    def __init__(self, bank: dict[str, Template], seed: int, size: int = BLITZ_SIZE):
        """Серия из size задач по шаблонам с blitz_method; без повторов, пока шаблонов хватает.
        Различных методов < N_OPTIONS → ValueError."""
        eligible = sorted((t for t in bank.values() if t.blitz_method), key=lambda t: t.id)
        methods = sorted({t.blitz_method for t in eligible})
        if len(methods) < N_OPTIONS:
            raise ValueError(f"для блица нужно ≥ {N_OPTIONS} различных методов, есть {len(methods)}")
        rng = random.Random(seed)
        order: list[Template] = []
        while len(order) < size:
            pool = eligible[:]
            rng.shuffle(pool)
            order.extend(pool)
        tasks = []
        for tpl in order[:size]:
            inst = generate(tpl, rng.randrange(2**32))
            opts = rng.sample([m for m in methods if m != tpl.blitz_method], N_OPTIONS - 1) + [tpl.blitz_method]
            rng.shuffle(opts)
            tasks.append(BlitzTask(tpl.id, inst, tpl.blitz_method, tuple(opts), tpl.checkpoints[0].hint))
        self._tasks = tuple(tasks)
        self._answers: list[BlitzAnswer] = []

    @property
    def tasks(self) -> tuple[BlitzTask, ...]:
        return self._tasks

    @property
    def finished(self) -> bool:
        return len(self._answers) >= len(self._tasks)

    def current(self) -> BlitzTask | None:
        return None if self.finished else self._tasks[len(self._answers)]

    def answer(self, i: int | None, elapsed: float) -> BlitzAnswer:
        """i — индекс кнопки (0-based) или None (таймер). elapsed > TIME_LIMIT → skip."""
        task = self.current()
        if task is None:
            raise RuntimeError("блиц уже закончен")
        if i is not None and not 0 <= i < len(task.options):
            raise ValueError(f"нет кнопки {i}")
        idx = len(self._answers)
        if i is None or elapsed > TIME_LIMIT:
            ans = BlitzAnswer(idx, None, SKIP, elapsed)
        else:
            chosen = task.options[i]
            ans = BlitzAnswer(idx, chosen, OK if chosen == task.correct else WRONG, elapsed)
        self._answers.append(ans)
        return ans

    def summary(self) -> BlitzSummary:
        mistakes = tuple(
            Mistake(a.idx, self._tasks[a.idx].instance.statement_latex, self._tasks[a.idx].correct,
                    a.chosen, self._tasks[a.idx].hint)
            for a in self._answers if a.verdict != OK)
        score = sum(a.verdict == OK for a in self._answers)
        return BlitzSummary(score, len(self._tasks), mistakes)
