"""Шаблоны задач: загрузка YAML и валидация формата."""
import re
from dataclasses import dataclass, field
from pathlib import Path

import sympy as sp
import yaml

from core.parser import ALIASES, CONSTS, FUNCS

CHECKPOINT_TYPES = {"choice", "expr", "transformed_integrand", "antiderivative"}
DIFFICULTIES = {"A", "B", "C"}
DEFAULT_DOMAIN = (-3.0, 3.0)

TOP_KEYS = {"id", "topic", "source", "difficulty", "var", "params", "constraints",
            "integrand", "statement", "checkpoints", "blitz_method", "method_alternatives", "examples",
            "limit", "ratio", "product", "diff", "power", "reduce", "reduce_other", "steps", "value"}
TOP_REQUIRED = {"id", "topic", "difficulty", "var", "integrand", "checkpoints"}
LIMIT_KINDS = ("ratio", "product", "diff", "power")
LIMIT_KEYS = {"limit", *LIMIT_KINDS, "reduce", "reduce_other", "steps", "value"}
LIMIT_REQUIRED = {"id", "topic", "difficulty", "var", "limit"}
CP_KEYS = {"type", "prompt", "hint", "var", "options", "correct", "expected", "substitution",
           "same_as", "distractors", "skippable", "reference", "domain"}
CP_REQUIRED = {
    "choice": {"options", "correct"},
    "expr": {"expected"},
    "transformed_integrand": {"expected", "var", "substitution"},
    "antiderivative": {"reference"},
}
CONSTRAINT_NAMES = {"gcd", "Abs", "Mod", "and", "or", "not"}

_NAME_RE = re.compile(r"^[a-z][a-z0-9]*$")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_RESERVED = set(FUNCS) | set(CONSTS) | set(ALIASES) | {"cancel", "expand", "together", "I"}


class TemplateError(ValueError):
    """Ошибка в шаблоне. Сообщение содержит файл и id шаблона."""


@dataclass(frozen=True)
class ParamSpec:
    name: str
    kind: str                         # int | choice
    lo: int | None = None             # для int: [lo, hi] включительно
    hi: int | None = None
    exclude: tuple = ()
    values: tuple = ()                # для choice

    def candidates(self) -> list[int]:
        if self.kind == "choice":
            return list(self.values)
        return [v for v in range(self.lo, self.hi + 1) if v not in self.exclude]


@dataclass(frozen=True)
class Checkpoint:
    type: str                          # choice | expr | transformed_integrand | antiderivative
    prompt: str = ""
    hint: str | None = None
    var: str | None = None             # переменная точки (t для transformed_integrand); None → var шаблона
    options: tuple[str, ...] = ()      # choice
    correct: tuple[str, ...] = ()      # choice
    expected: str | None = None        # expr, transformed_integrand
    substitution: str | None = None    # transformed_integrand: t = g(x), для самопроверки
    same_as: str | None = None         # expr: "integrand" или формула — expected обязан с ней совпасть
    distractors: tuple[str, ...] = ()  # ручные дистракторы
    skippable: bool = False
    reference: str | None = None       # antiderivative
    domain: tuple = DEFAULT_DOMAIN     # числа или строки-выражения от параметров


@dataclass(frozen=True)
class Example:
    """Номер из задачника: при этих параметрах шаблон даёт ровно его; answer — ответ из книги (синтаксис sympy)."""
    number: str
    params: dict
    answer: str


@dataclass(frozen=True)
class LimitSpec:
    """Предел: kind и две части (u/v, u·v, u − v, u^v); точки задачи строит генератор (core.limit_build)."""
    to: str                            # точка: формула от параметров, "oo", "-oo"
    side: str                          # "+", "-", "both"
    kind: str                          # ratio | product | diff | power
    u: str
    v: str
    reduce: tuple[str, str] | None = None            # приведение автора (num, den); для power — дробь для ln y
    reduce_other: tuple[tuple[str, str], ...] = ()   # другие верные приведения: длиннее или тупиковые
    steps: bool = True                 # False — только «Сразу ответ»
    value: str | None = None           # ответ автора, если sympy не справляется


@dataclass(frozen=True)
class Template:
    id: str
    topic: str
    source: str
    difficulty: str
    var: str
    params: dict[str, ParamSpec]
    integrand: str
    checkpoints: tuple[Checkpoint, ...]
    constraints: tuple[str, ...] = ()
    statement: str | None = None       # LaTeX с {{параметрами}}; None → sympy.latex(Integral(integrand))
    blitz_method: str | None = None
    method_alternatives: tuple[str, ...] = ()   # тоже рабочие методы: «Сработает, но можно короче»
    examples: tuple[Example, ...] = ()           # сверка с задачником (tests/test_bank.py)
    file: str = field(default="", compare=False)
    limit: LimitSpec | None = None               # не None — задача на предел: integrand и checkpoints пустые


# --- валидация --------------------------------------------------------------

def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _parse_param(name: str, raw, fail) -> ParamSpec:
    if not _NAME_RE.match(name) or name in _RESERVED:
        fail(f"недопустимое имя параметра «{name}»")
    if not isinstance(raw, dict):
        fail(f"параметр {name}: ожидался словарь")
    keys = set(raw)
    if keys == {"choice"}:
        vals = raw["choice"]
        if not isinstance(vals, list) or not vals or not all(_is_int(v) for v in vals):
            fail(f"параметр {name}: choice — непустой список целых")
        return ParamSpec(name, "choice", values=tuple(vals))
    if "int" in keys and keys <= {"int", "exclude"}:
        rng, exclude = raw["int"], raw.get("exclude", [])
        if not (isinstance(rng, list) and len(rng) == 2 and all(_is_int(v) for v in rng)):
            fail(f"параметр {name}: int — [lo, hi] из целых")
        if rng[0] > rng[1]:
            fail(f"параметр {name}: lo > hi")
        if not isinstance(exclude, list) or not all(_is_int(v) for v in exclude):
            fail(f"параметр {name}: exclude — список целых")
        spec = ParamSpec(name, "int", lo=rng[0], hi=rng[1], exclude=tuple(exclude))
        if not spec.candidates():
            fail(f"параметр {name}: пустой диапазон")
        return spec
    fail(f"параметр {name}: неизвестный тип {sorted(keys)} (нужно int или choice)")


def _check_formula(src, allowed: set[str], what: str, fail) -> None:
    if not isinstance(src, str) or not src.strip():
        fail(f"{what}: ожидалась строка-формула")
    try:
        e = sp.sympify(src, locals={n: sp.Symbol(n) for n in allowed})
    except Exception as exc:
        fail(f"{what}: не разбирается «{src}» ({type(exc).__name__})")
    extra = {s.name for s in getattr(e, "free_symbols", set())} - allowed
    if extra:
        fail(f"{what}: неизвестные имена {sorted(extra)} в «{src}»")


def _parse_checkpoint(i: int, raw, tpl_var: str, params: set[str], fail) -> Checkpoint:
    where = f"точка {i}"
    if not isinstance(raw, dict):
        fail(f"{where}: ожидался словарь")
    ctype = raw.get("type")
    if ctype not in CHECKPOINT_TYPES:
        fail(f"{where}: неизвестный тип «{ctype}»")
    where = f"точка {i} ({ctype})"
    unknown = set(raw) - CP_KEYS
    if unknown:
        fail(f"{where}: неизвестные ключи {sorted(unknown)}")
    missing = CP_REQUIRED[ctype] - set(raw)
    if missing:
        fail(f"{where}: нет полей {sorted(missing)}")

    var = raw.get("var")
    if var is not None and (not isinstance(var, str) or not _NAME_RE.match(var) or var in params):
        fail(f"{where}: недопустимая переменная «{var}»")
    cp_var = var or tpl_var
    formula_names = params | {cp_var}

    options = tuple(raw.get("options", ()))
    correct = tuple(raw.get("correct", ()))
    if ctype == "choice":
        if len(options) < 2 or not correct:
            fail(f"{where}: нужны ≥ 2 options и хотя бы один correct")
        if not set(correct) <= set(options):
            fail(f"{where}: correct {sorted(set(correct) - set(options))} нет в options")

    for key in ("expected", "reference"):
        if key in raw:
            _check_formula(raw[key], formula_names, f"{where}.{key}", fail)
    if "substitution" in raw:
        _check_formula(raw["substitution"], params | {tpl_var}, f"{where}.substitution", fail)
    distractors = tuple(raw.get("distractors", ()))
    for d in distractors:
        _check_formula(d, formula_names, f"{where}.distractors", fail)

    same_as = raw.get("same_as")
    if same_as is not None:
        if ctype != "expr":
            fail(f"{where}: same_as допустим только у expr")
        if same_as != "integrand":
            _check_formula(same_as, formula_names, f"{where}.same_as", fail)

    domain = raw.get("domain", DEFAULT_DOMAIN)
    if not isinstance(domain, (list, tuple)) or len(domain) != 2:
        fail(f"{where}: domain — два конца [lo, hi]")
    dom = []
    for end in domain:
        if isinstance(end, (int, float)) and not isinstance(end, bool):
            dom.append(float(end))
        else:
            _check_formula(end, params, f"{where}.domain", fail)
            dom.append(end)

    return Checkpoint(
        type=ctype, prompt=str(raw.get("prompt", "")), hint=raw.get("hint"), var=var,
        options=options, correct=correct, expected=raw.get("expected"),
        substitution=raw.get("substitution"), same_as=same_as, distractors=distractors,
        skippable=bool(raw.get("skippable", False)), reference=raw.get("reference"),
        domain=tuple(dom),
    )


def _parse_limit(raw: dict, var: str, params: set[str], fail) -> LimitSpec:
    lim = raw["limit"]
    if not isinstance(lim, dict) or set(lim) != {"to", "side"}:
        fail("limit: нужны ровно to и side")
    if lim["side"] not in ("+", "-", "both"):
        fail("limit.side — «+», «-» или both")
    _check_formula(lim["to"], params, "limit.to", fail)
    kinds = [k for k in LIMIT_KINDS if k in raw]
    if len(kinds) != 1:
        fail(f"предел: нужно ровно одно из {list(LIMIT_KINDS)}")
    kind = kinds[0]

    def pair(value, what: str) -> tuple[str, str]:
        if not isinstance(value, list) or len(value) != 2:
            fail(f"{what}: ожидались две формулы")
        for f in value:
            _check_formula(f, params | {var}, what, fail)
        return value[0], value[1]

    u, v = pair(raw[kind], kind)
    steps = raw.get("steps", True)
    if not isinstance(steps, bool):
        fail("steps — true или false")
    if kind == "ratio" and ("reduce" in raw or "reduce_other" in raw):
        fail("reduce для отношения не нужен")
    if kind != "ratio" and steps and "reduce" not in raw:
        fail(f"{kind}: нужно reduce (приведение к дроби) или steps: false")
    other = raw.get("reduce_other", [])
    if not isinstance(other, list):
        fail("reduce_other: ожидался список пар")
    value = raw.get("value")
    if value is not None:
        _check_formula(value, params, "value", fail)
    return LimitSpec(to=lim["to"], side=lim["side"], kind=kind, u=u, v=v,
                     reduce=pair(raw["reduce"], "reduce") if "reduce" in raw else None,
                     reduce_other=tuple(pair(o, "reduce_other") for o in other), steps=steps, value=value)


def parse_template(raw: dict, source: str = "<dict>") -> Template:
    """dict из YAML → Template. Любая ошибка формата → TemplateError."""
    tid = raw.get("id", "?") if isinstance(raw, dict) else "?"

    def fail(msg: str):
        raise TemplateError(f"{source}: шаблон {tid}: {msg}")

    if not isinstance(raw, dict):
        fail("ожидался словарь")
    unknown = set(raw) - TOP_KEYS
    if unknown:
        fail(f"неизвестные ключи {sorted(unknown)}")
    is_limit = "limit" in raw
    missing = (LIMIT_REQUIRED if is_limit else TOP_REQUIRED) - set(raw)
    if missing:
        fail(f"нет полей {sorted(missing)}")
    mixed = set(raw) & ({"integrand", "checkpoints"} if is_limit else LIMIT_KEYS)
    if mixed:
        fail(f"поля {sorted(mixed)} не для этого вида задачи")
    if raw["difficulty"] not in DIFFICULTIES:
        fail(f"difficulty должен быть из {sorted(DIFFICULTIES)}")
    var = raw["var"]
    if not isinstance(var, str) or not _NAME_RE.match(var):
        fail(f"недопустимая переменная «{var}»")

    raw_params = raw.get("params") or {}
    if not isinstance(raw_params, dict):
        fail("params: ожидался словарь")
    if var in raw_params:
        fail(f"параметр совпадает с переменной «{var}»")
    params = {name: _parse_param(name, spec, fail) for name, spec in raw_params.items()}
    names = set(params)

    limit = _parse_limit(raw, var, names, fail) if is_limit else None
    if not is_limit:
        _check_formula(raw["integrand"], names | {var}, "integrand", fail)

    constraints = tuple(raw.get("constraints") or ())
    for c in constraints:
        if not isinstance(c, str):
            fail("constraints: ожидались строки")
        extra = set(_IDENT_RE.findall(c)) - names - CONSTRAINT_NAMES
        if extra:
            fail(f"constraints: неизвестные имена {sorted(extra)} в «{c}»")

    cps_raw = [] if is_limit else raw["checkpoints"]
    if not isinstance(cps_raw, list) or not (cps_raw or is_limit):
        fail("checkpoints: нужен непустой список")
    cps = tuple(_parse_checkpoint(i, cp, var, names, fail) for i, cp in enumerate(cps_raw))

    statement = raw.get("statement")
    if statement is not None:
        unknown = set(re.findall(r"\{\{(\w+)\}\}", str(statement))) - names
        if unknown:
            fail(f"statement: неизвестные параметры {sorted(unknown)}")

    alts = raw.get("method_alternatives", [])
    if not isinstance(alts, list) or not all(isinstance(a, str) and a.strip() for a in alts):
        fail("method_alternatives: ожидался список названий методов")
    if alts and not raw.get("blitz_method"):
        fail("method_alternatives без blitz_method")
    if raw.get("blitz_method") in alts:
        fail("method_alternatives дублирует blitz_method")

    examples = []
    raw_ex = raw.get("examples", [])
    if not isinstance(raw_ex, list):
        fail("examples: ожидался список")
    for k, ex in enumerate(raw_ex):
        where = f"examples[{k}]"
        if not isinstance(ex, dict) or set(ex) != {"number", "params", "answer"}:
            fail(f"{where}: нужны ровно ключи number, params, answer")
        p = ex["params"]
        if not isinstance(p, dict) or set(p) != names:
            fail(f"{where}: params должны задавать ровно {sorted(names)}")
        if not all(_is_int(v) for v in p.values()):
            fail(f"{where}: значения параметров — целые числа")
        _check_formula(ex["answer"], {var}, f"{where}.answer", fail)
        examples.append(Example(str(ex["number"]), dict(p), ex["answer"]))

    return Template(
        id=str(raw["id"]), topic=str(raw["topic"]), source=str(raw.get("source", "")),
        difficulty=raw["difficulty"], var=var, params=params, integrand=raw.get("integrand", ""),
        checkpoints=cps, constraints=constraints, statement=statement,
        blitz_method=raw.get("blitz_method"), method_alternatives=tuple(alts), examples=tuple(examples),
        file=source, limit=limit,
    )


def load_templates(path: str | Path) -> dict[str, Template]:
    """Файл .yaml (список шаблонов) или папка с *.yaml → {id: Template}."""
    path = Path(path)
    files = sorted(path.glob("*.yaml")) if path.is_dir() else [path]
    bank: dict[str, Template] = {}
    for f in files:
        try:
            items = yaml.safe_load(f.read_text(encoding="utf-8")) or []
        except yaml.YAMLError as exc:
            raise TemplateError(f"{f}: невалидный YAML: {exc}") from None
        if not isinstance(items, list):
            raise TemplateError(f"{f}: ожидался список шаблонов")
        for raw in items:
            t = parse_template(raw, source=str(f))
            if t.id in bank:
                raise TemplateError(f"{f}: дубль id {t.id} (уже есть в {bank[t.id].file})")
            bank[t.id] = t
    methods = {t.blitz_method for t in bank.values() if t.blitz_method}
    for t in bank.values():
        unknown = [a for a in t.method_alternatives if a not in methods]
        if unknown:
            raise TemplateError(f"{t.file}: шаблон {t.id}: method_alternatives {unknown} — "
                                f"нет среди blitz_method банка")
    return bank
