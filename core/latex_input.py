"""LaTeX от OCR (Markdown с $…$) или редактора MathLive → строка в синтаксисе ввода бота (её потом разбирает core.parser)."""
import re

# \команда → имя в синтаксисе ввода (русская нотация, как учат на лекциях)
COMMANDS = {
    "sin": "sin", "cos": "cos", "tan": "tg", "tg": "tg", "cot": "ctg", "ctg": "ctg",
    "arcsin": "arcsin", "arccos": "arccos", "arctan": "arctg", "arctg": "arctg",
    "arccot": "arcctg", "arcctg": "arcctg", "ln": "ln", "log": "ln", "exp": "exp",
    "sinh": "sh", "cosh": "ch", "tanh": "th", "coth": "cth", "sh": "sh", "ch": "ch", "th": "th", "cth": "cth",
    "pi": "pi", "infty": "oo", "cdot": "*", "times": "*", "exponentialE": "e",
}
SPACING = {",", ";", ":", "!", " ", "quad", "qquad", "left.", "right."}

_MATH_RE = re.compile(r"\$\$(.+?)\$\$|\$(.+?)\$|\\\((.+?)\\\)|\\\[(.+?)\\\]", re.S)
_CYR_RE = re.compile(r"[А-Яа-яЁё]")
# слова, набранные буквами: имена функций целиком, остальное — отдельные буквы («xe» в LaTeX — это x·e)
_WORDS = sorted({w for w in COMMANDS if len(w) > 1 and w.islower() and w not in ("cdot", "times", "infty")}
                | {"const", "sqrt"}, key=len, reverse=True)
_LETTERS_RE = re.compile("|".join(_WORDS) + "|[A-Za-z]")
_RUN_RE = re.compile(r"[A-Za-z]+")
_CMD_RE = re.compile(r"\\([A-Za-z]+|.)")


class Unreadable(ValueError):
    """Формулы нет, их несколько или запись не переводится."""


class EmptyCell(Unreadable):
    """Незаполненная клетка: \\placeholder{} редактора, пустой числитель, степень или корень."""


def _extract(markdown: str) -> str:
    s = markdown.strip()
    if not s or "UNREADABLE" in s.upper():
        raise Unreadable("пусто")
    found = [next(g for g in m.groups() if g is not None) for m in _MATH_RE.finditer(s)]
    if not found:
        if "$" in s:
            raise Unreadable("незакрытый $")
        return s
    if len(found) != 1:
        raise Unreadable("несколько формул")
    return found[0]


def _group(s: str, i: int) -> tuple[str, int]:
    """Аргумент после i: {…} (с вложенностью) или один символ / одна \\команда."""
    while i < len(s) and s[i] == " ":
        i += 1
    if i >= len(s):
        raise Unreadable("нет аргумента")
    if s[i] == "{":
        depth = 0
        for j in range(i, len(s)):
            depth += {"{": 1, "}": -1}.get(s[j], 0)
            if depth == 0:
                return s[i + 1:j], j + 1
        raise Unreadable("непарные скобки")
    m = _CMD_RE.match(s, i)
    if m:
        return m.group(0), m.end()
    return s[i], i + 1


def _closed(s: str) -> bool:
    """Вся строка — одна скобочная группа «(…)»."""
    if not (s.startswith("(") and s.endswith(")")):
        return False
    depth = 0
    for j, ch in enumerate(s):
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if depth == 0:
            return j == len(s) - 1
    return False


def _paren(s: str) -> str:
    """Скобки, если их ещё нет (лишние съедают лимит длины ввода)."""
    s = s.strip()
    return s if _closed(s) else f"({s})"


def _atom(s: str) -> str:
    """Как _paren, но число или одна буква — без скобок. Только там, где рядом стоит / или скобка."""
    s = s.strip()
    return s if s.isdigit() or (len(s) == 1 and s.isalpha()) else _paren(s)


def _part(s: str) -> str:
    """Числитель или знаменатель: как _atom, но и вызов «имя(…)» целиком — без скобок."""
    s = s.strip()
    m = re.match(r"[a-z]+\s*(?=\()", s)
    return s if m and _closed(s[m.end():]) else _atom(s)


def _arg(s: str) -> str:
    """Перевод обязательного аргумента; пустой → EmptyCell."""
    r = _conv(s)
    if not r.strip():
        raise EmptyCell("пустая клетка")
    return r


def _word(s: str) -> str:
    """Имя функции: 'tg' или '\\mathrm{tg}' (MathLive оборачивает русские имена дважды)."""
    m = re.fullmatch(r"\\(?:mathrm|text|operatorname)\{(\w+)\}", s.strip())
    return m.group(1) if m else s.strip()


def _conv(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        ch = s[i]
        if ch == "\\":
            m = _CMD_RE.match(s, i)
            name = m.group(1)
            i = m.end()
            if name in ("left", "right", "mleft", "mright"):
                if s.startswith(".", i):          # \left. — невидимая скобка
                    i += 1
                continue
            if name in ("frac", "dfrac", "tfrac"):
                a, i = _group(s, i)
                b, i = _group(s, i)
                out.append(f"({_part(_arg(a))}/{_part(_arg(b))})")
            elif name == "sqrt":
                while i < len(s) and s[i] == " ":     # Яндекс пишет «\sqrt [3] {x}»
                    i += 1
                if s.startswith("[", i):
                    j = s.index("]", i)
                    n, i = s[i + 1:j], j + 1
                    a, i = _group(s, i)
                    out.append(f"({_atom(_arg(a))}^(1/{_atom(_arg(n))}))")
                else:
                    a, i = _group(s, i)
                    out.append(f" sqrt{_paren(_arg(a))}")
            elif name in ("operatorname", "mathrm", "text"):
                word, i = _group(s, i)
                word = _word(word)
                if word not in COMMANDS:
                    raise Unreadable(f"неизвестная функция {word}")
                out.append(f" {COMMANDS[word]} ")
            elif name in COMMANDS:
                out.append(f" {COMMANDS[name]} ")
            elif name in SPACING or name in ",;:! ":
                out.append(" ")
            elif name in ("{", "}"):
                out.append({"{": "(", "}": ")"}[name])
            elif name in ("|", "lvert", "rvert", "vert"):
                out.append("|")
            elif name == "placeholder":
                raise EmptyCell("пустая клетка")
            else:
                raise Unreadable(f"неизвестная команда \\{name}")
        elif ch == "^":
            a, i = _group(s, i + 1)
            out.append(f"^{_paren(_arg(a))}")
        elif ch == "{":
            a, i = _group(s, i)
            bars = out and out[-1].strip() == "|" and re.match(r"\s*\\(?:m?right|rvert)", s[i:])
            out.append(_conv(a) if bars else _paren(_conv(a)))        # |{…}|: скобки внутри модуля не нужны
        elif ch == "}":
            raise Unreadable("непарные скобки")
        elif m := _RUN_RE.match(s, i):
            out.append(" ".join(_LETTERS_RE.findall(m.group(0))))
            i = m.end()
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def latex_to_input(markdown: str) -> str:
    """'$\\frac{1}{2}\\ln\\left|x\\right| + C$' → '((1)/(2)) ln |x| + C'."""
    latex = _extract(markdown)
    if _CYR_RE.search(latex):
        raise Unreadable("текст вместо формулы")
    text = re.sub(r"\s+", " ", _conv(latex)).strip()
    text = re.sub(r"(?<=[(^*/+\-]) | (?=[()^*/+\-])", "", text)          # пробелы у скобок и знаков: короче и читаемее
    if not text:
        raise Unreadable("пусто")
    return text
