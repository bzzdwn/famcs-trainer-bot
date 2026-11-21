"""Регистрация: проверка кода группы и имени (чистая логика, без Telegram)."""
import re

MIN_PART, MAX_PART = 2, 30
_PART_RE = re.compile(r"^[А-ЯЁа-яё]+(?:-[А-ЯЁа-яё]+)?$")


class InvalidName(ValueError):
    """str(e) — сообщение студенту."""


def check_group_code(entered: str, expected: str) -> bool:
    """Без учёта регистра и пробелов по краям."""
    e = entered.strip()
    return bool(e) and e.casefold() == expected.strip().casefold()


def normalize_name(raw: str) -> str:
    """«  иванов   иван » → «Иванов Иван». Два слова кириллицей (дефис допустим). Иначе InvalidName."""
    parts = raw.split()
    if len(parts) != 2:
        raise InvalidName("Напиши фамилию и имя через пробел, например: Иванов Иван")
    for p in parts:
        if not _PART_RE.match(p):
            raise InvalidName("Только русские буквы (двойная фамилия — через дефис), например: Иванов Иван")
        if not MIN_PART <= len(p) <= MAX_PART:
            raise InvalidName(f"Каждое слово — от {MIN_PART} до {MAX_PART} букв")
    return " ".join("-".join(s.capitalize() for s in p.split("-")) for p in parts)
