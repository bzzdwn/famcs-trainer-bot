"""Темы и уровни для меню /train."""
import random

from core.templates import Template

# раздел — первая часть темы («limits.lhopital» → limits); порядок — как в меню
INTEGRALS, LIMITS = "indef_integral", "limits"
SECTION_NAMES = {INTEGRALS: "Интегралы", LIMITS: "Пределы"}

TOPIC_NAMES = {
    "indef_integral.basic": "Простейшие",
    "indef_integral.subst": "Подстановки",
    "indef_integral.parts": "По частям",
    "indef_integral.quadratic": "Квадратный трёхчлен",
    "indef_integral.rational": "Рациональные",
    "indef_integral.irrational": "Иррациональные",
    "indef_integral.trig": "Тригонометрические",
    "indef_integral.mixed": "Разные",     # Демидович § 6: метод темой не подсказан
    "limits.lhopital": "Правило Лопиталя",
}


MIX = "mix"            # «Вразнобой» без раздела — старые кнопки в чатах: интегралы; новые — «mix.<раздел>»
ANY_LEVEL = "any"      # любой уровень
MIX_TITLE = "🎲 Вразнобой"


def section_of(topic: str) -> str:
    """Раздел темы или «Вразнобой»."""
    if topic == MIX:
        return INTEGRALS
    return topic.removeprefix(f"{MIX}.").split(".")[0]


def is_mix(topic: str) -> bool:
    return topic == MIX or topic.startswith(f"{MIX}.")


def mix_key(section: str) -> str:
    return f"{MIX}.{section}"


def sections(bank: dict[str, Template]) -> list[str]:
    """Разделы, в которых есть шаблоны, в порядке меню."""
    have = {section_of(t.topic) for t in bank.values()}
    return [s for s in SECTION_NAMES if s in have]


def _in(section: str | None, t: Template) -> bool:
    return section is None or section_of(t.topic) == section


def mix_levels(bank: dict[str, Template], section: str | None = None) -> list[str]:
    """Уровни для «Вразнобой»: все уровни раздела (отсортированы) + ANY_LEVEL в конце."""
    return sorted({t.difficulty for t in bank.values() if _in(section, t)}) + [ANY_LEVEL]


def method_options(bank: dict[str, Template], section: str | None = None) -> tuple[str, ...]:
    """Все методы (blitz_method) раздела, без section — всего банка; без повторов, в фиксированном порядке:
    по группам тем как в TOPIC_NAMES (Демидович), внутри группы — по id шаблона."""
    order = {t: i for i, t in enumerate(TOPIC_NAMES)}
    group: dict[str, tuple[int, str]] = {}
    for t in bank.values():
        if t.blitz_method and _in(section, t):
            key = (order.get(t.topic, len(order)), t.id)
            group[t.blitz_method] = min(group.get(t.blitz_method, key), key)
    return tuple(sorted(group, key=group.get))


def build_method_step(bank: dict[str, Template], tpl: Template):
    """Шаг «Каким методом будешь решать?» для «Вразнобой» → bot.flow.MethodStep."""
    from bot.flow import MethodStep
    return MethodStep(options=method_options(bank, section_of(tpl.topic)), correct=tpl.blitz_method,
                      alternatives=tpl.method_alternatives,
                      hint=tpl.checkpoints[0].hint if tpl.checkpoints else None)   # у предела точки строит генератор


def available(bank: dict[str, Template], section: str | None = None) -> dict[str, list[str]]:
    """{topic: [уровни, по которым есть шаблоны]} в порядке TOPIC_NAMES, уровни отсортированы.
    section — только темы раздела."""
    levels: dict[str, set[str]] = {}
    for t in bank.values():
        if not _in(section, t):
            continue
        levels.setdefault(t.topic, set()).add(t.difficulty)
    order = [t for t in TOPIC_NAMES if t in levels] + sorted(set(levels) - set(TOPIC_NAMES))
    return {t: sorted(levels[t]) for t in order}


def pick_template(bank: dict[str, Template], topic: str, level: str, rng: random.Random,
                  exclude: str | None = None, solved: dict[str, str] | None = None) -> Template | None:
    """Случайный шаблон темы и уровня; None, если таких нет.
    «Вразнобой» — любая тема своего раздела, level = ANY_LEVEL — любой уровень.
    exclude — id прошлого шаблона: не повторяем подряд, если есть из чего выбрать.
    solved — {id: когда решён сам (ISO)}: сначала нерешённые; решено всё — самый давний (темы идут по кругу)."""
    def fits(t: Template) -> bool:
        return section_of(t.topic) == section_of(topic) if is_mix(topic) else t.topic == topic

    cands = sorted((t for t in bank.values() if fits(t) and level in (ANY_LEVEL, t.difficulty)), key=lambda t: t.id)
    fresh = [t for t in cands if t.id != exclude]
    pool = fresh or cands                      # единственный шаблон — повтор лучше, чем «задач нет»
    if not pool:
        return None
    solved = solved or {}
    unsolved = [t for t in pool if t.id not in solved]
    if unsolved:
        return rng.choice(unsolved)
    oldest = min(solved[t.id] for t in pool)
    return rng.choice([t for t in pool if solved[t.id] == oldest])


def topic_title(topic: str) -> str:
    """Русское название; для неизвестной темы — сам ключ."""
    return MIX_TITLE if is_mix(topic) else TOPIC_NAMES.get(topic, topic)
