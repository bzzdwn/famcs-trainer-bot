"""Блиц: 10 задач, 4 кнопки с методами, 10 секунд на ответ. Чистая логика, без Telegram."""
import random
import re
from dataclasses import dataclass

from core.generator import Instance, generate
from bot.topics import INTEGRALS, LIMITS, section_of
from core.limits import TYPE_HINTS, TYPES
from core.templates import Template

BLITZ_SIZE = 10
TIME_LIMIT = 10.0        # секунд; ответ позже — пропуск
N_OPTIONS = 4
OK, WRONG, SKIP = "ok", "wrong", "skip"


COUNTDOWN = 3            # последние секунды задачи: «3…», «2…», «1…»
TIME_CHOICES = tuple(range(5, 61, 5))   # своё время на карточку: 5, 10, …, 60 с


class InvalidTime(ValueError):
    """str(e) — сообщение студенту."""


def parse_time(text: str) -> int:
    """«25», « 25 с», «25с» → 25. Не из TIME_CHOICES → InvalidTime."""
    m = re.fullmatch(r"\s*(\d+)\s*(?:с|сек|s)?\s*", text or "")
    if not m or int(m.group(1)) not in TIME_CHOICES:
        raise InvalidTime("Нужно число секунд от 5 до 60, кратное 5, например 25")
    return int(m.group(1))


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
    def __init__(self, bank: dict[str, Template], seed: int, size: int = BLITZ_SIZE,
                 time_limit: float = TIME_LIMIT, section: str = INTEGRALS):
        """section — раздел серии и всех кнопок: методы разных разделов не смешиваются.
        Серия из size задач по шаблонам с blitz_method; без повторов, пока шаблонов хватает.
        Различных методов < N_OPTIONS → ValueError."""
        eligible = sorted((t for t in bank.values() if t.blitz_method and section_of(t.topic) == section),
                          key=lambda t: t.id)
        # пределы: «назови тип» — неверные кнопки из всех типов, а не только встретившихся в банке
        methods = list(TYPES) if section == LIMITS else sorted({t.blitz_method for t in eligible})
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
            valid = {tpl.blitz_method, *tpl.method_alternatives}     # рабочие методы не годятся в неверные кнопки
            opts = rng.sample([m for m in methods if m not in valid], N_OPTIONS - 1) + [tpl.blitz_method]
            rng.shuffle(opts)
            hint = tpl.checkpoints[0].hint if tpl.checkpoints else TYPE_HINTS.get(tpl.blitz_method)
            tasks.append(BlitzTask(tpl.id, inst, tpl.blitz_method, tuple(opts), hint))
        self._setup(tuple(tasks), time_limit)

    def _setup(self, tasks: tuple[BlitzTask, ...], time_limit: float) -> None:
        if time_limit not in TIME_CHOICES:
            raise ValueError(f"время на карточку {time_limit} — не из {TIME_CHOICES}")
        self._tasks, self._time_limit = tasks, time_limit
        self._answers: list[BlitzAnswer] = []

    @classmethod
    def from_tasks(cls, tasks: tuple[BlitzTask, ...], time_limit: float = TIME_LIMIT) -> "BlitzRun":
        """Серия из готовых задач (запас): время на карточку от задач не зависит."""
        run = cls.__new__(cls)
        run._setup(tuple(tasks), time_limit)
        return run

    @property
    def time_limit(self) -> float:
        return self._time_limit

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
        if i is None or elapsed > self._time_limit:
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
