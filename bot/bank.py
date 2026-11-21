"""Банк шаблонов в памяти бота и его горячая перезагрузка (/reload)."""
from pathlib import Path

from core.generator import GenerationError, generate
from core.templates import Template, TemplateError, load_templates

SMOKE_SEEDS = 5     # столько экземпляров каждого шаблона генерируем перед заменой банка


def reload_bank(bank: dict[str, Template], path: str | Path) -> tuple[bool, str]:
    """Перечитать YAML и, если всё валидно и каждый шаблон генерируется, заменить содержимое bank на месте.
    (False, сообщение об ошибке) — bank не тронут; (True, сводка) — заменён."""
    try:
        new = load_templates(path)
        for t in new.values():
            for seed in range(SMOKE_SEEDS):
                generate(t, seed)
    except (TemplateError, GenerationError) as exc:
        return False, str(exc)
    added, removed = sorted(set(new) - set(bank)), sorted(set(bank) - set(new))
    bank.clear()
    bank.update(new)
    parts = [f"Шаблонов: {len(bank)}"]
    if added:
        parts.append("добавлены: " + ", ".join(added))
    if removed:
        parts.append("убраны: " + ", ".join(removed))
    return True, "; ".join(parts)
