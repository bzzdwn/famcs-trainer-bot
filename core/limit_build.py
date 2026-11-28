"""Задача на предел из шаблона: самопроверка и точки, построенные по цепочке Лопиталя."""
import sympy as sp

from core.checker import Cmp, numeric_compare
from core.generator import (
    N_DISTRACTORS, CheckpointInstance, GenerationError, Instance, _ev, _fill,
)
from core.latex import limit_latex, polish, ru_latex
from core.limits import (
    INF, MAX_CARD_PX, MAX_DIFFS, MAX_OPS, TYPE_HINTS, _signed, NO_TYPE, TYPES, LimitError, Link, _lim, card_px, lhopital_chain, plain_value,
    verify_limit,
)
from core.parser import parse_trusted
from core.templates import Example, Template

DEFAULT_DOMAIN = (-3.0, 3.0)
TYPE_LOOK = "Подставь предельную точку в каждую часть по отдельности и посмотри, к чему они стремятся."
NOTE_DEAD = "Верно, но после правила Лопиталя станет сложнее: неопределённость не исчезнет."
PART_NAMES = {"ratio": ("Числитель", "знаменатель"), "product": ("Первый множитель", "второй"),
              "diff": ("Уменьшаемое", "вычитаемое"), "power": ("Основание", "показатель")}
TRAP_LN = "Это предел ln y. Вернись к y: возьми e в этой степени."
TRAP_OVER = "Неопределённости уже нет, а правило Лопиталя применено ещё раз."
LH_HINT = "Производная числителя, делённая на производную знаменателя (а не производная дроби), затем сократи."


class _Task:
    """Числа подставлены: функция, точка, части, приведение."""

    def __init__(self, tpl: Template, params: dict[str, int]):
        L, x = tpl.limit, tpl.var
        self.tpl, self.params, self.spec, self.x = tpl, params, L, sp.Symbol(x, real=True)
        ev = lambda src: _ev(src, params, x, real=True)
        self.u, self.v, self.to, self.side = ev(L.u), ev(L.v), _ev(L.to, params), L.side
        self.expr = {"ratio": lambda: self.u / self.v, "product": lambda: self.u * self.v,
                     "diff": lambda: self.u - self.v, "power": lambda: self.u ** self.v}[L.kind]()
        self.reduced = tuple(map(ev, L.reduce)) if L.reduce else None
        self.other = [tuple(map(ev, o)) for o in L.reduce_other]
        self.value = _ev(L.value, params) if L.value else None

    def fail(self, what: str):
        raise GenerationError(f"{self.tpl.id} {self.params}: {what}")

    @property
    def domain(self) -> tuple[float, float]:
        """Где сравнивать варианты: рядом с точкой, с той стороны, откуда предел."""
        if self.to == sp.oo:
            return 3.0, 8.0
        if self.to == -sp.oo:
            return -8.0, -3.0
        a = float(self.to)
        return (a - 0.6, a - 0.05) if self.side == "-" else (a + 0.05, a + 0.6)

    def target(self) -> sp.Expr:
        """Чему равна приведённая дробь: сама функция, а для степени — ln y."""
        return self.v * sp.log(self.u) if self.spec.kind == "power" else self.expr

    def start_kind(self) -> str:
        """Тип исходной неопределённости по пределам частей."""
        side = "+" if self.side == "both" else self.side
        a, b = (plain_value(e, self.x, self.to, side) for e in (self.u, self.v))
        if a is None or b is None:
            self.fail("внутри частей своя неопределённость: перепиши их")
        inf = [p in INF for p in (a, b)]
        kinds = {
            "product": "0·∞" if (a == 0 and inf[1]) or (b == 0 and inf[0]) else None,
            "diff": "∞−∞" if all(inf) and a == b else None,
            "power": "1^∞" if a == 1 and inf[1] else "0⁰" if a == 0 and b == 0 else "∞⁰" if inf[0] and b == 0 else None,
        }
        return kinds[self.spec.kind] or self.fail(f"нет неопределённости вида {self.spec.kind}: части → {a}, {b}")

    def ratio(self) -> tuple[sp.Expr, sp.Expr]:
        return (self.u, self.v) if self.spec.kind == "ratio" else self.reduced

    def chain(self, num, den) -> tuple[Link, ...] | None:
        links = lhopital_chain(num, den, self.x, self.to, self.side)
        return links if links and len(links) > 1 else None


def self_check_limit(tpl: Template, params: dict[str, int]) -> None:
    """Предел подтверждён sympy и численно; приведения равны функции (для степени — ln y); цепочка сходится."""
    t = _Task(tpl, params)
    try:
        verify_limit(t.expr, t.x, t.to, t.side, t.value)
    except LimitError as e:
        t.fail(str(e))
    for num, den in ([t.reduced] if t.reduced else []) + t.other:
        if numeric_compare(num / den, t.target(), tpl.var, t.domain) is not Cmp.EQUAL:
            t.fail(f"приведение {num} / {den} не равно исходному")
    if t.spec.steps and t.chain(*t.ratio()) is None:
        t.fail("цепочка Лопиталя не сходится: проверь запись или поставь steps: false")


def check_limit_example(tpl: Template, ex: Example) -> None:
    t = _Task(tpl, ex.params)
    book = sp.sympify(ex.answer)
    value = verify_limit(t.expr, t.x, t.to, t.side, t.value)
    same = value == book if value in INF or book in INF else abs(sp.N(value - book, 30)) < sp.Float("1e-15")
    if not same:
        raise GenerationError(f"{tpl.id} № {ex.number}: ответ книги {book}, а предел {value}")


# --- точки -------------------------------------------------------------------------------

def _plain(e: sp.Expr, var: str) -> str:
    """Строка sympy с обычным символом (как у остальных точек)."""
    return str(e.xreplace({s: sp.Symbol(var) for s in e.free_symbols if s.name == var}))


def _fmt(v: sp.Expr) -> str:
    """Предел части словами: 0, 3/2, −∞, e^(−1)."""
    if v in INF:
        return "+∞" if v == sp.oo else "−∞"
    text = sp.sstr(sp.nsimplify(v) if v.is_number else v)
    for a, b in (("**", "^"), ("exp(", "e^("), ("log(", "ln("), ("sqrt(", "√("), ("-", "−"), ("*", "·")):
        text = text.replace(a, b)
    return text


def _facts(t: _Task, a: sp.Expr, b: sp.Expr, kind: str) -> str:
    """«Числитель → 0, знаменатель → +∞.» — куда стремятся части (двусторонняя ∞ разных знаков — просто ∞)."""
    first, second = PART_NAMES[kind]
    vals = []
    for e in (a, b):
        if t.side == "both" and t.to not in INF:
            r, left = (plain_value(e, t.x, t.to, s) for s in ("+", "-"))
            if r in INF and left in INF and r != left:
                vals.append("∞")                       # справа и слева бесконечности разных знаков
                continue
        v = _signed(e, t.x, t.to, t.side)
        vals.append(_fmt(v) if v is not None else "∞" if e.has(t.x) else _fmt(e))
    return f"{first} → {vals[0]}, {second} → {vals[1]}."


def _type_point(t: _Task, kind: str, first: bool, parts: tuple[sp.Expr, sp.Expr], how: str) -> CheckpointInstance:
    prompt = "Какой тип неопределённости?" if first else "Какой тип неопределённости теперь?"
    facts = _facts(t, *parts, how)
    notes = {}
    for other in (*TYPES, NO_TYPE):
        if other == kind:
            continue
        if other == NO_TYPE:
            notes[other] = f"Подставь отдельно в каждую часть: {facts[0].lower()}{facts[1:]} Это неопределённость."
        elif kind == NO_TYPE:
            notes[other] = f"Неопределённости нет: {facts[0].lower()}{facts[1:]}"
        else:
            notes[other] = f"Не тот тип: {facts[0].lower()}{facts[1:]}"
    return CheckpointInstance("choice", prompt, TYPE_LOOK, t.tpl.var, t.domain, options=(*TYPES, NO_TYPE),
                              correct=(kind,), role="type", notes=notes, extra_hints=((facts, None),))


Pair = tuple[sp.Expr, sp.Expr]


def _src(p: Pair, var: str) -> str:
    """Дробь строкой, без сворачивания sympy."""
    return f"({_plain(p[0], var)})/({_plain(p[1], var)})"


def _label(p: Pair, var: str) -> str:
    num, den = p
    sign = ""
    if den != 1 and not num.is_Add and num.could_extract_minus_sign():
        sign, num = "- ", -num
    n, d = (ru_latex(parse_trusted(_plain(e, var), var), var) for e in (num, den))
    return n if d == "1" else rf"{sign}\frac{{{n}}}{{{d}}}"


def _neat(p: Pair) -> Pair:
    """Сокращённая дробь: неверные варианты не должны отличаться от верного неряшливым видом."""
    if p[1] == 0 or p[0].count_ops() + p[1].count_ops() > MAX_OPS:
        return p
    try:
        return sp.fraction(sp.cancel(sp.together(p[0] / p[1])))
    except (sp.PolynomialError, NotImplementedError, ValueError, TypeError):
        return p


AUTO_NOTES = ("Проверь знак.", "Потерян множитель (например, от производной сложной функции).",
              "Потерян множитель (например, от производной сложной функции).", "Дробь перевёрнута.")


def _pair_point(t: _Task, prompt: str, hint: str, role: str, right: Pair, same: dict[str, list[Pair]],
                wrong: list[tuple[Pair, str]], extra_hints: tuple = ()) -> CheckpointInstance:
    """Выбор из четырёх дробей. same — равные верной (dead / alternatives): показываем, но они не «неверные по значению»."""
    var, (n, d) = t.tpl.var, right
    shown = [*same.get("dead", []), *same.get("alternatives", [])][:N_DISTRACTORS]
    why = {p: NOTE_DEAD for p in same.get("dead", [])}
    value = n / d
    used = [value]
    auto = list(zip([(-n, d), (2 * n, d), (n, 2 * d), (d, n)], AUTO_NOTES))
    for p, note in [*wrong, *auto]:
        if len(shown) == N_DISTRACTORS:
            break
        if p[1] == 0 or any(e.has(sp.nan, sp.zoo, sp.oo) for e in p):
            continue
        q = p[0] / p[1]
        if any(numeric_compare(q, u, var, t.domain) in (Cmp.EQUAL, Cmp.INSUFFICIENT) for u in used):
            continue
        if _label(p, var) in {_label(x, var) for x in [right, *shown]}:
            continue
        used.append(q)
        shown.append(p)
        why[p] = note
    if len(shown) < N_DISTRACTORS:
        t.fail(f"{role}: не набралось {N_DISTRACTORS} различимых вариантов")
    names = {k: tuple(_src(p, var) for p in same.get(k, []) if p in shown) for k in ("dead", "alternatives")}
    return CheckpointInstance("expr", prompt, hint, var, t.domain, expected=_src(right, var),
                              distractors=tuple(_src(p, var) for p in shown), role=role,
                              labels={_src(p, var): _label(p, var) for p in [right, *shown]},
                              notes={_src(p, var): why[p] for p in shown if p in why}, extra_hints=extra_hints, **names)


def _reduce_point(t: _Task) -> CheckpointInstance:
    num, den = t.reduced
    same: dict[str, list[Pair]] = {"dead": [], "alternatives": []}
    for p in t.other:
        same["alternatives" if t.chain(*p) else "dead"].append(p)
    power = t.spec.kind == "power"
    prompt = "Приведи к дроби ln y" if power else "Приведи к 0/0 или ∞/∞"
    hint = {"power": "y = u^v, ln y = v·ln u; затем один из множителей опусти в знаменатель.",
            "product": "u·v = u/(1/v) = v/(1/u): выбери дробь, которая после дифференцирования упростится.",
            "diff": "Приведи к общему знаменателю."}[t.spec.kind]
    first = {"power": ((t.u, 1 / t.v), "Потерян логарифм: ln y = v·ln u."),
             "product": ((t.u, t.v), "Это частное u/v, а не произведение: множитель переносят в знаменатель как 1/(…)."),
             "diff": ((t.u, t.v), "Это частное, а не разность: приведи к общему знаменателю.")}[t.spec.kind]
    wrong = [first, ((den, num), "Дробь перевёрнута: она равна 1/(исходное выражение), а не ему.")]
    return _pair_point(t, prompt, hint, "reduce", (num, den), same, wrong)


def _lhopital_point(t: _Task, prev: Link, cur: Link) -> CheckpointInstance:
    f, g, x = prev.num, prev.den, t.x
    df, dg = sp.diff(f, x), sp.diff(g, x)
    quotient = sp.fraction(sp.together(sp.diff(f / g, x)))
    wrong = [(quotient, "Это производная всей дроби (f/g)′. По правилу Лопиталя числитель и знаменатель "
                        "дифференцируют по отдельности: f′/g′."),
             ((df, g), "Продифференцирован только числитель — знаменатель тоже нужно."),
             ((f, dg), "Продифференцирован только знаменатель — числитель тоже нужно.")]
    var = t.tpl.var
    hints = (("Производная числителя: f′ =", ru_latex(parse_trusted(_plain(df, var), var), var)),
             ("Производная знаменателя: g′ =", ru_latex(parse_trusted(_plain(dg, var), var), var)))
    return _pair_point(t, "Примени правило Лопиталя и упрости", LH_HINT, "lhopital", (cur.num, cur.den), {},
                       [(_neat(p), note) for p, note in wrong if p[1] != 0], hints)


def _traps(t: _Task, value: sp.Expr, links: tuple[Link, ...] | None) -> dict[str, str]:
    """Типичные неверные значения: не вернулся от ln y; Лопиталь без неопределённости."""
    found: list[tuple[sp.Expr, str]] = []
    power = t.spec.kind == "power"
    if power and value not in INF and value != 0:
        found.append((sp.log(value), TRAP_LN))
    if links:
        d = sp.diff(links[-1].den, t.x)
        over = _lim(sp.diff(links[-1].num, t.x) / d, t.x, t.to, t.side) if d != 0 else None
        if over is not None and over not in INF:
            found.append((over, TRAP_OVER))
            if power:
                found.append((sp.exp(over), TRAP_OVER))
    out: dict[str, str] = {}
    for v, note in found:
        v = sp.simplify(v)
        if value in INF or abs(sp.N(v - value, 30)) > sp.Float("1e-15"):
            out.setdefault(str(v), note)
    return out


def _hints(t: _Task, links: tuple[Link, ...] | None, steps: bool) -> tuple[tuple, tuple]:
    """Лестницы финала. Готовое число не показываем: последняя дробь — только если в ней есть переменная."""
    power, var = t.spec.kind == "power", t.tpl.var
    if power or t.spec.kind != "ratio":
        kind = t.start_kind()
    else:
        kind = links[0].kind if links else None
    first = (f"Это неопределённость {kind}. {TYPE_HINTS[kind]}" if kind else
             "Сначала определи тип неопределённости: подставь предельную точку в каждую часть.", None)
    back = [("Найденное число — предел ln y. Вернись к y: возьми e в этой степени.", None)] if power else []
    last = []
    if steps and links[-1].num.has(t.x) | links[-1].den.has(t.x):
        last = [("Подставь предельную точку в последнюю дробь:", _label((links[-1].num, links[-1].den), var))]
    direct = [first]
    if steps and t.reduced:
        direct.append(("Приведи к дроби ln y:" if power else "Приведи так:", _label(t.reduced, var)))
    if links:
        n = len(links) - 1
        times = "один раз" if n == 1 else f"{n} раза" if n < 5 else f"{n} раз"
        direct.append((f"Правило Лопиталя понадобится {times}; после каждого проверяй, осталась ли неопределённость.", None))
    own = last + back or [("Неопределённости больше нет: подставь предельную точку.", None)]
    return tuple(own), (*direct, *back, *[("После правила Лопиталя и упрощения:", lab) for _, lab in last])


def _statement(t: _Task) -> str:
    """Условие в порядке автора: sympy переставил бы слагаемые разности и свернул бы дробь."""
    var = t.tpl.var
    u, v = (ru_latex(parse_trusted(_plain(e, var), var), var) for e in (t.u, t.v))
    body = {"ratio": lambda: rf"\frac{{{u}}}{{{v}}}", "diff": lambda: rf"\left({u} - {v}\right)"}.get(t.spec.kind)
    return limit_latex(t.expr, var, t.to, t.side, body() if body else None)


def build_limit(tpl: Template, params: dict[str, int]) -> Instance:
    t = _Task(tpl, params)
    try:
        value = verify_limit(t.expr, t.x, t.to, t.side, t.value)
    except LimitError as e:
        t.fail(str(e))
    links = t.chain(*t.ratio()) if t.spec.steps else None
    cps: list[CheckpointInstance] = []
    if links and len(links) - 1 <= MAX_DIFFS:
        try:
            if tpl.limit.kind != "ratio":
                cps += [_type_point(t, t.start_kind(), True, (t.u, t.v), tpl.limit.kind), _reduce_point(t)]
            cps.append(_type_point(t, links[0].kind, not cps, (links[0].num, links[0].den), "ratio"))
            for prev, cur in zip(links, links[1:]):
                cps += [_lhopital_point(t, prev, cur), _type_point(t, cur.kind, False, (cur.num, cur.den), "ratio")]
        except GenerationError as exc:
            t.fail(str(exc))
        if any(card_px(label) > MAX_CARD_PX for c in cps for label in (c.labels or {}).values()):
            cps = []
    steps = bool(cps)
    cps.append(CheckpointInstance("value", "Чему равен предел?", None, tpl.var, DEFAULT_DOMAIN, expected=str(value),
                                  traps=_traps(t, value, links), role="value"))
    latex = polish(_fill(tpl.statement, params) if tpl.statement else _statement(t))
    return Instance(template_id=tpl.id, params=dict(params), integrand=_plain(t.expr, tpl.var), statement_latex=latex,
                    checkpoints=tuple(cps), steps=steps, limit_hints=_hints(t, links, steps))
