"""Генерация экземпляров задач из шаблонов: параметры → подстановка → самопроверка → дистракторы."""
import random
from dataclasses import dataclass

import sympy as sp

from core.checker import Cmp, numeric_compare
from core.latex import integral_latex
from core.parser import parse_trusted
from core.templates import DEFAULT_DOMAIN, Template

MAX_TRIES = 100
N_DISTRACTORS = 3


class GenerationError(RuntimeError):
    """Не удалось сгенерировать экземпляр: constraints невыполнимы или шаблон не прошёл самопроверку."""


@dataclass(frozen=True)
class CheckpointInstance:
    type: str
    prompt: str
    hint: str | None
    var: str
    domain: tuple[float, float]
    options: tuple[str, ...] = ()
    correct: tuple[str, ...] = ()
    expected: str | None = None        # с подставленными параметрами
    reference: str | None = None
    distractors: tuple[str, ...] = ()  # ровно N_DISTRACTORS для expr / transformed_integrand
    skippable: bool = False


@dataclass(frozen=True)
class Instance:
    template_id: str
    params: dict[str, int]
    integrand: str
    statement_latex: str
    checkpoints: tuple[CheckpointInstance, ...]


# --- вычисление формул шаблона ------------------------------------------------

def _ev(src: str, params: dict[str, int], var: str | None = None, real: bool = False) -> sp.Expr:
    """Формула шаблона с подставленными числами. Функции sympy (cancel, expand) вычисляются уже на числах."""
    loc = {k: sp.Integer(v) for k, v in params.items()}
    if var:
        loc[var] = sp.Symbol(var, real=True) if real else sp.Symbol(var)
    return sp.sympify(src, locals=loc)


def _domain(dom: tuple, params: dict[str, int]) -> tuple[float, float]:
    lo, hi = (float(e) if isinstance(e, float) else float(_ev(e, params)) for e in dom)
    return lo, hi


def _main_domain(tpl: Template, params) -> tuple[float, float]:
    """Domain по переменной шаблона: из точки antiderivative, иначе по умолчанию."""
    for cp in tpl.checkpoints:
        if cp.type == "antiderivative":
            return _domain(cp.domain, params)
    return DEFAULT_DOMAIN


# --- параметры --------------------------------------------------------------

def _constraints_ok(tpl: Template, params: dict[str, int]) -> bool:
    for c in tpl.constraints:
        try:
            if not bool(_ev(c, params)):
                return False
        except TypeError:
            raise GenerationError(f"{tpl.id}: constraint «{c}» не вычисляется в bool при {params}") from None
    return True


def sample_params(tpl: Template, rng: random.Random) -> dict[str, int]:
    """Случайные параметры, удовлетворяющие constraints. После MAX_TRIES → GenerationError."""
    for _ in range(MAX_TRIES):
        params = {name: int(rng.choice(spec.candidates())) for name, spec in tpl.params.items()}
        if _constraints_ok(tpl, params):
            return params
    raise GenerationError(f"{tpl.id}: constraints не выполнились за {MAX_TRIES} попыток")


# --- самопроверка -----------------------------------------------------------

def _expect_equal(a, b, var, domain, tpl, params, what):
    res = numeric_compare(a, b, var, domain)
    if res is not Cmp.EQUAL:
        raise GenerationError(f"{tpl.id} {params}: {what} — {res.value}")


def self_check(tpl: Template, params: dict[str, int]) -> None:
    """reference' == integrand; h(g(x))·g'(x) == integrand; expected == same_as (integrand или формула).
    Нарушение → GenerationError с описанием."""
    x = tpl.var
    xs = sp.Symbol(x, real=True)
    f = _ev(tpl.integrand, params, x, real=True)
    dom = _main_domain(tpl, params)
    if not dom[0] < dom[1]:
        raise GenerationError(f"{tpl.id} {params}: пустой domain {dom}")
    for i, cp in enumerate(tpl.checkpoints):
        if cp.type == "antiderivative":
            F = _ev(cp.reference, params, x, real=True)
            _expect_equal(sp.diff(F, xs), f, x, _domain(cp.domain, params), tpl, params,
                          f"точка {i}: reference' ≠ integrand")
        elif cp.type == "transformed_integrand":
            g = _ev(cp.substitution, params, x, real=True)
            h = _ev(cp.expected, params, cp.var, real=True)
            back = h.subs(sp.Symbol(cp.var, real=True), g) * sp.diff(g, xs)
            _expect_equal(back, f, x, dom, tpl, params, f"точка {i}: h(g(x))·g'(x) ≠ integrand")
        elif cp.type == "expr" and cp.same_as:
            v = cp.var or x
            e = _ev(cp.expected, params, v, real=True)
            target = f if cp.same_as == "integrand" else _ev(cp.same_as, params, v, real=True)
            _expect_equal(e, target, v, dom, tpl, params, f"точка {i}: expected ≠ same_as")


# --- дистракторы ------------------------------------------------------------

def make_distractors(expected: str, var: str, domain: tuple[float, float],
                     manual: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Сначала ручные, потом −e, 2e, e/2; эквивалентные эталону или друг другу отбрасываются.
    Возвращает N_DISTRACTORS штук или GenerationError."""
    e = parse_trusted(expected, var)
    candidates = [parse_trusted(m, var) for m in manual] + [-e, 2 * e, e / 2]
    chosen: list[sp.Expr] = []
    for c in candidates:
        if numeric_compare(c, e, var, domain) in (Cmp.EQUAL, Cmp.INSUFFICIENT):
            continue
        if any(numeric_compare(c, d, var, domain) is Cmp.EQUAL for d in chosen):
            continue
        chosen.append(c)
        if len(chosen) == N_DISTRACTORS:
            return tuple(str(d) for d in chosen)
    raise GenerationError(f"не набралось {N_DISTRACTORS} различимых дистракторов для «{expected}»")


# --- сборка -----------------------------------------------------------------

def build(tpl: Template, params: dict[str, int]) -> Instance:
    """Детерминированная сборка экземпляра по параметрам (для восстановления из БД)."""
    x = tpl.var
    f = _ev(tpl.integrand, params, x)
    cps = []
    for cp in tpl.checkpoints:
        var = cp.var or x
        dom = _domain(cp.domain, params)
        expected = str(_ev(cp.expected, params, var)) if cp.expected else None
        distractors = ()
        if cp.type in ("expr", "transformed_integrand"):
            manual = tuple(str(_ev(d, params, var)) for d in cp.distractors)
            try:
                distractors = make_distractors(expected, var, dom, manual)
            except GenerationError as exc:
                raise GenerationError(f"{tpl.id} {params}: {exc}") from None
        cps.append(CheckpointInstance(
            type=cp.type, prompt=cp.prompt, hint=cp.hint, var=var, domain=dom,
            options=cp.options, correct=cp.correct, expected=expected,
            reference=str(_ev(cp.reference, params, x)) if cp.reference else None,
            distractors=distractors, skippable=cp.skippable,
        ))
    return Instance(
        template_id=tpl.id, params=dict(params), integrand=str(f),
        statement_latex=tpl.statement or integral_latex(f, x),
        checkpoints=tuple(cps),
    )


def generate(tpl: Template, seed: int) -> Instance:
    """sample_params → self_check → build."""
    params = sample_params(tpl, random.Random(seed))
    self_check(tpl, params)
    return build(tpl, params)
