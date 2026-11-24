"""Логика одной задачи в режиме «Тренировка» — без Telegram, чтобы тестировать pytest'ом.

Хендлеры только переводят нажатия/текст в вызовы TrainSession и показывают результат.
"""
import random
from dataclasses import dataclass, field
from typing import Callable

from core.checker import OK, WRONG, Verdict
from core.generator import Instance
from core.hints import Hint, answer_hints
from core.latex import ru_latex
from core.parser import ParseError, parse_answer, split_constant

MAX_ATTEMPTS = 3
NOT_SPENT = {"parse_error", "timeout"}   # опечатка в записи / не смогли проверить — попытка не тратится
REVEALED = "revealed"

# check(kind, user, expected, var, domain) -> Verdict; в боте — check_with_timeout, в тестах — прямой вызов
CheckFn = Callable[[str, str, str, str, tuple[float, float]], Verdict]


HIDE_TEXT = "Убрал часть неверных вариантов."
NOTE_METHOD_ALT = "Сработает, но можно короче"
METHOD_IDX = -1                    # номер шага «метод» в логе: номера шагов шаблона не сдвигаются


@dataclass(frozen=True)
class MethodStep:
    """Первый шаг во «Вразнобой»: выбрать метод из всех методов банка."""
    options: tuple[str, ...]
    correct: str
    alternatives: tuple[str, ...] = ()   # тоже засчитываются, с пометкой NOTE_METHOD_ALT
    hint: str | None = None
    prompt: str = "Каким методом будешь решать?"


@dataclass(frozen=True)
class Prompt:
    kind: str                      # buttons | input | done
    idx: int                       # номер контрольной точки
    cp_type: str
    text: str                      # вопрос точки
    options: tuple[str, ...] = ()  # buttons: подписи (choice) или LaTeX формул (expr / transformed_integrand)
    formulas: bool = False         # True → options — формулы, рисуем карточкой
    hidden: tuple[int, ...] = ()   # варианты, убранные подсказкой (номера остальных не меняются)


@dataclass(frozen=True)
class Outcome:
    verdict: Verdict
    spent: bool                    # потрачена ли попытка
    attempt_no: int                # номер попытки на этой точке (для лога)
    moved_on: bool                 # перешли к следующей точке
    forced_reveal: bool = False    # исчерпаны попытки → показали ответ
    answer: str | None = None      # правильный ответ (LaTeX или подпись), если показан
    log: dict = field(default_factory=dict)  # поля для attempts


@dataclass(frozen=True)
class Preview:
    ok: bool
    latex: str | None = None       # ok → что распознали (с «+ C», если было)
    error: str | None = None       # не ok → сообщение парсера


@dataclass(frozen=True)
class Summary:
    total: int                     # точек в задаче (без пропущенных «своим путём»)
    first_try: int                 # взято с первой попытки
    revealed: int                  # показан ответ
    hints: int                     # точек, где брали подсказку
    status: str                    # solved | revealed
    skipped: int = 0               # skippable-точки, пропущенные «своим путём» (уровень C)


class TrainSession:
    def __init__(self, instance: Instance, seed: int, check: CheckFn, free_input: bool = False,
                 direct: bool = False, method_step: MethodStep | None = None):
        """seed — для перемешивания вариантов (в боте = id экземпляра в БД).
        free_input — уровень C: промежуточные выражения вводом, skippable-точки «свой путь».
        direct — «Сразу ответ»: только первообразная, после ❌ можно switch_to_steps()."""
        self.inst = instance
        self._check = check
        self._free = free_input
        self._idx = 0
        self._attempts = 0
        self._seed = seed
        self._answer_hints: tuple[bool, tuple[Hint, ...]] | None = None   # (direct, уровни) — считаем один раз
        self._level = 0                        # сколько уровней подсказок взято на текущем шаге
        self._hidden: tuple[int, ...] = ()     # неверные варианты, убранные подсказкой
        self._hints: set[int] = set()
        self._results: list[str] = []          # first_try | later | revealed | skipped
        self._dropped = False
        self._start_direct = self._direct = direct
        finals = [i for i, cp in enumerate(instance.checkpoints) if cp.type == "antiderivative"]
        self._final = finals[-1] if finals else None
        if direct and self._final is None:
            raise ValueError("«Сразу ответ» возможен только в задаче с первообразной")
        self._final_attempts = 0               # попытки на финальной точке, потраченные в «Сразу ответ»
        if direct:
            self._idx = self._final
        self._method = method_step
        self._in_method = self._method_asked = method_step is not None and not direct
        # варианты кнопок: (подписи для показа, исходники для лога, индексы верных)
        self._buttons: dict[int, tuple[tuple[str, ...], tuple[str, ...], set[int]]] = {}
        for i, cp in enumerate(instance.checkpoints):
            rng = random.Random(f"{seed}:{i}")
            if cp.type == "choice":
                opts = list(cp.options)
                rng.shuffle(opts)
                self._buttons[i] = (tuple(opts), tuple(opts), {k for k, o in enumerate(opts) if o in cp.correct})
            elif cp.type in ("expr", "transformed_integrand") and not free_input:
                srcs = [cp.expected, *cp.distractors]
                rng.shuffle(srcs)
                self._buttons[i] = (tuple(ru_latex(s, cp.var) for s in srcs), tuple(srcs),
                                    {srcs.index(cp.expected)})

    # --- состояние --------------------------------------------------------

    @property
    def finished(self) -> bool:
        return self._dropped or self._idx >= len(self.inst.checkpoints)

    @property
    def _cp(self):
        return self.inst.checkpoints[self._idx]

    def current(self) -> Prompt:
        if self.finished:
            return Prompt("done", self._idx, "", "")
        if self._in_method:
            m = self._method
            return Prompt("buttons", METHOD_IDX, "method", m.prompt, m.options, hidden=self._hidden)
        cp = self._cp
        if self._idx in self._buttons:
            labels = self._buttons[self._idx][0]
            return Prompt("buttons", self._idx, cp.type, cp.prompt, labels, formulas=cp.type != "choice",
                          hidden=self._hidden)
        return Prompt("input", self._idx, cp.type, cp.prompt)

    def _ensure(self, kind: str) -> None:
        if self.finished:
            raise RuntimeError("задача уже решена")
        if self.current().kind != kind:
            raise ValueError(f"сейчас ожидается {self.current().kind}, а не {kind}")

    def _answer(self) -> str:
        if self._in_method:
            return self._method.correct
        cp = self._cp
        if cp.type == "choice":
            return cp.correct[0]
        if cp.type == "antiderivative":
            return ru_latex(cp.reference, cp.var) + " + C"
        return ru_latex(cp.expected, cp.var)

    def _advance(self, result: str) -> None:
        if self._in_method:                    # после метода — первая точка шаблона (idx уже 0)
            self._results.append(result)
            self._in_method = False
            self._attempts = self._final_attempts if self._idx == self._final else 0
            self._reset_hints()
            return
        self._results.append(result)
        self._idx = len(self.inst.checkpoints) if self._direct else self._idx + 1
        self._attempts = self._final_attempts if self._idx == self._final else 0
        self._reset_hints()

    def _reset_hints(self) -> None:
        self._level, self._hidden = 0, ()

    def _where(self) -> tuple[int, str]:
        """(номер для лога, тип) текущего шага."""
        return (METHOD_IDX, "method") if self._in_method else (self._idx, self._cp.type)

    def _register(self, verdict: Verdict, raw: str, method: str) -> Outcome:
        idx, ctype = self._where()
        spent = verdict.status not in NOT_SPENT
        attempt_no = self._attempts + 1
        log = {"checkpoint_idx": idx, "checkpoint_type": ctype, "raw_input": raw,
               "verdict": verdict.status, "input_method": method,
               "hint_used": self._level, "attempt_no": attempt_no}
        if verdict.status == OK:
            self._attempts += 1
            self._advance("first_try" if self._attempts == 1 else "later")
            return Outcome(verdict, True, attempt_no, True, log=log)
        if not spent:
            return Outcome(verdict, False, attempt_no, False, log=log)
        if not self._in_method and self._free and self._cp.skippable and ctype != "antiderivative":
            self._advance("skipped")                       # «свой путь» — дальше без штрафа
            return Outcome(verdict, False, attempt_no, True, log=log)
        self._attempts += 1
        if self._attempts >= MAX_ATTEMPTS:
            answer = self._answer()
            self._advance(REVEALED)
            return Outcome(verdict, True, attempt_no, True, forced_reveal=True, answer=answer, log=log)
        return Outcome(verdict, True, attempt_no, False, log=log)

    # --- действия студента -------------------------------------------------

    def answer_button(self, i: int) -> Outcome:
        """Нажата кнопка i (0-based) на текущей точке."""
        self._ensure("buttons")
        if i in self._hidden:
            raise ValueError(f"вариант {i} убран подсказкой")
        if self._in_method:
            m = self._method
            if not 0 <= i < len(m.options):
                raise ValueError(f"нет кнопки {i}")
            chosen = m.options[i]
            if chosen == m.correct:
                verdict = Verdict(OK)
            elif chosen in m.alternatives:
                verdict = Verdict(OK, NOTE_METHOD_ALT)
            else:
                verdict = Verdict(WRONG)
            return self._register(verdict, chosen, "button")
        labels, srcs, correct = self._buttons[self._idx]
        if not 0 <= i < len(labels):
            raise ValueError(f"нет кнопки {i}")
        return self._register(Verdict(OK if i in correct else WRONG), srcs[i], "button")

    def preview(self, raw: str) -> Preview:
        """Разбор ввода для «Ты имел в виду это?» — до проверки."""
        cp = self._cp
        body, has_c = split_constant(raw) if cp.type == "antiderivative" else (raw, False)
        try:
            e = parse_answer(body, cp.var)
        except ParseError as exc:
            return Preview(False, error=str(exc))
        return Preview(True, latex=ru_latex(e) + (" + C" if has_c else ""))

    def answer_text(self, raw: str, input_method: str = "text") -> Outcome:
        """Текстовый ответ на текущей точке (после подтверждения превью)."""
        self._ensure("input")
        cp = self._cp
        expected = self.inst.integrand if cp.type == "antiderivative" else cp.expected
        verdict = self._check(cp.type, raw, expected, cp.var, cp.domain)
        return self._register(verdict, raw, input_method)

    def take_hint(self) -> Hint | None:
        """Следующий уровень подсказки текущего шага; None — больше нет. Номер уровня идёт в лог попыток.
        Варианты кнопками: подсказка шаблона → убрать часть неверных. Финал (и «Сразу ответ») — core.hints."""
        if self.finished:
            return None
        ladder = self._ladder()
        if self._level >= len(ladder):
            return None
        hint = ladder[self._level]
        self._level += 1
        self._hints.add(self._where()[0])
        if hint.text == HIDE_TEXT:
            self._hidden = self._to_hide()
        return hint

    @property
    def direct(self) -> bool:
        return self._direct

    @property
    def needs_answer_hints(self) -> bool:
        """Для следующей подсказки нужна лестница финала, а её ещё нет: бот считает её в процессе-генераторе."""
        return not self.finished and self._on_final() and (
            self._answer_hints is None or self._answer_hints[0] != self._direct)

    def set_answer_hints(self, direct: bool, hints: tuple[Hint, ...]) -> None:
        self._answer_hints = (direct, hints)

    def _on_final(self) -> bool:
        return not self._in_method and (self._direct or self._cp.type == "antiderivative")

    def _ladder(self) -> tuple[Hint, ...]:
        if self._on_final():
            if self.needs_answer_hints:
                self.set_answer_hints(self._direct, answer_hints(self.inst, self._direct))
            return self._answer_hints[1]
        texts = [t for t in [self._method.hint if self._in_method else self._cp.hint] if t]
        if self.current().kind == "buttons" and self._to_hide():
            texts.append(HIDE_TEXT)
        return tuple(Hint(i, t, last=i == len(texts)) for i, t in enumerate(texts, 1))

    def _to_hide(self) -> tuple[int, ...]:
        """Какие неверные варианты убрать: остаётся треть (не меньше одного). Рабочие альтернативы не трогаем."""
        if self._in_method:
            m = self._method
            wrong = [i for i, o in enumerate(m.options) if o != m.correct and o not in m.alternatives]
        else:
            labels, _, correct = self._buttons[self._idx]
            wrong = [i for i in range(len(labels)) if i not in correct]
        keep = max(1, len(wrong) // 3)
        random.Random(f"{self._seed}:{self._where()[0]}:hide").shuffle(wrong)
        return tuple(sorted(wrong[keep:]))

    def reveal(self) -> Outcome:
        """«Показать ответ и идти дальше»."""
        if self.finished:
            raise RuntimeError("задача уже решена")
        (idx, ctype), answer, attempt_no = self._where(), self._answer(), self._attempts + 1
        log = {"checkpoint_idx": idx, "checkpoint_type": ctype, "raw_input": "",
               "verdict": REVEALED, "input_method": "button",
               "hint_used": self._level, "attempt_no": attempt_no}
        self._advance(REVEALED)
        return Outcome(Verdict(REVEALED), False, attempt_no, True, answer=answer, log=log)

    @property
    def mode(self) -> str:
        """train | train_direct — как задача начата (для sessions.mode)."""
        return "train_direct" if self._start_direct else "train"

    @property
    def direct(self) -> bool:
        """Сейчас идёт ход «Сразу ответ»."""
        return self._direct

    @property
    def dropped(self) -> bool:
        return self._dropped

    def drop(self) -> None:
        """Выход из задачи: дальше ответы не принимаются."""
        self._dropped = True

    def switch_to_steps(self) -> None:
        """Из «Сразу ответ» — на шаги с первой точки. Попытки финальной точки сохраняются."""
        if self.finished:
            raise RuntimeError("задача уже закрыта")
        if not self._direct:
            raise ValueError("уже идём по шагам")
        self._final_attempts = self._attempts
        self._direct = False
        self._idx = 0
        self._in_method = self._method_asked = self._method is not None
        self._attempts = self._final_attempts if self._final == 0 and not self._in_method else 0
        self._reset_hints()

    def summary(self) -> Summary:
        r = self._results
        revealed = r.count(REVEALED)
        return Summary(
            total=1 if self._direct else len(self.inst.checkpoints) - r.count("skipped") + int(self._method_asked),
            first_try=r.count("first_try"), revealed=revealed, hints=len(self._hints),
            status=REVEALED if revealed else "solved", skipped=r.count("skipped"),
        )
