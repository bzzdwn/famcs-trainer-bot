"""Темы и уровни для меню /train."""
import random

from core.templates import Template

TOPIC_NAMES = {
    "indef_integral.basic": "Простейшие",
    "indef_integral.subst": "Подстановки",
    "indef_integral.parts": "По частям",
    "indef_integral.quadratic": "Квадратный трёхчлен",
    "indef_integral.rational": "Рациональные",
    "indef_integral.irrational": "Иррациональные",
    "indef_integral.trig": "Тригонометрические",
}


MIX = "mix"            # «Вразнобой»: все темы
ANY_LEVEL = "any"      # любой уровень
MIX_TITLE = "🎲 Вразнобой"


def mix_levels(bank: dict[str, Template]) -> list[str]:
    """Уровни для «Вразнобой»: все уровни банка (отсортированы) + ANY_LEVEL в конце."""
    return sorted({t.difficulty for t in bank.values()}) + [ANY_LEVEL]


def method_options(bank: dict[str, Template]) -> tuple[str, ...]:
    """Все методы банка (blitz_method) без повторов, в фиксированном порядке:
    по группам тем как в TOPIC_NAMES (Демидович), внутри группы — по id шаблона."""
    order = {t: i for i, t in enumerate(TOPIC_NAMES)}
    group: dict[str, tuple[int, str]] = {}
    for t in bank.values():
        if t.blitz_method:
            key = (order.get(t.topic, len(order)), t.id)
            group[t.blitz_method] = min(group.get(t.blitz_method, key), key)
    return tuple(sorted(group, key=group.get))


def build_method_step(bank: dict[str, Template], tpl: Template):
    """Шаг «Каким методом будешь решать?» для «Вразнобой» → bot.flow.MethodStep."""
    from bot.flow import MethodStep
    return MethodStep(options=method_options(bank), correct=tpl.blitz_method,
                      alternatives=tpl.method_alternatives, hint=tpl.checkpoints[0].hint)


def available(bank: dict[str, Template]) -> dict[str, list[str]]:
    """{topic: [уровни, по которым есть шаблоны]} в порядке TOPIC_NAMES, уровни отсортированы."""
    levels: dict[str, set[str]] = {}
    for t in bank.values():
        levels.setdefault(t.topic, set()).add(t.difficulty)
    order = [t for t in TOPIC_NAMES if t in levels] + sorted(set(levels) - set(TOPIC_NAMES))
    return {t: sorted(levels[t]) for t in order}


def pick_template(bank: dict[str, Template], topic: str, level: str, rng: random.Random,
                  exclude: str | None = None) -> Template | None:
    """Случайный шаблон темы и уровня; None, если таких нет.
    topic = MIX — любая тема, level = ANY_LEVEL — любой уровень.
    exclude — id прошлого шаблона: не повторяем подряд, если есть из чего выбрать."""
    cands = sorted((t for t in bank.values()
                    if (topic == MIX or t.topic == topic) and (level == ANY_LEVEL or t.difficulty == level)),
                   key=lambda t: t.id)
    fresh = [t for t in cands if t.id != exclude]
    pool = fresh or cands                      # единственный шаблон — повтор лучше, чем «задач нет»
    return rng.choice(pool) if pool else None


def topic_title(topic: str) -> str:
    """Русское название; для неизвестной темы — сам ключ."""
    return MIX_TITLE if topic == MIX else TOPIC_NAMES.get(topic, topic)
