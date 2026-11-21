"""LaTeX от OCR (Markdown с $…$) → строка в синтаксисе ввода бота (её потом разбирает core.parser)."""
import re

# \команда → имя в синтаксисе ввода (русская нотация, как учат на лекциях)
COMMANDS = {
    "sin": "sin", "cos": "cos", "tan": "tg", "tg": "tg", "cot": "ctg", "ctg": "ctg",
    "arcsin": "arcsin", "arccos": "arccos", "arctan": "arctg", "arctg": "arctg",
    "arccot": "arcctg", "arcctg": "arcctg", "ln": "ln", "log": "ln", "exp": "exp",
    "sinh": "sh", "cosh": "ch", "tanh": "th", "coth": "cth", "sh": "sh", "ch": "ch", "th": "th",
    "pi": "pi", "infty": "oo", "cdot": "*", "times": "*",
}
SPACING = {",", ";", ":", "!", " ", "quad", "qquad", "left.", "right."}

_MATH_RE = re.compile(r"\$\$(.+?)\$\$|\$(.+?)\$|\\\((.+?)\\\)|\\\[(.+?)\\\]", re.S)
_CYR_RE = re.compile(r"[А-Яа-яЁё]")
_CMD_RE = re.compile(r"\\([A-Za-z]+|.)")


class Unreadable(ValueError):
    """Формулы нет, их несколько или запись не переводится."""


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


def _conv(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        ch = s[i]
        if ch == "\\":
            m = _CMD_RE.match(s, i)
            name = m.group(1)
            i = m.end()
            if name in ("left", "right"):
                if s.startswith(".", i):          # \left. — невидимая скобка
                    i += 1
                continue
            if name in ("frac", "dfrac", "tfrac"):
                a, i = _group(s, i)
                b, i = _group(s, i)
                out.append(f"(({_conv(a)})/({_conv(b)}))")
            elif name == "sqrt":
                while i < len(s) and s[i] == " ":     # Яндекс пишет «\sqrt [3] {x}»
                    i += 1
                if s.startswith("[", i):
                    j = s.index("]", i)
                    n, i = s[i + 1:j], j + 1
                    a, i = _group(s, i)
                    out.append(f"(({_conv(a)})^(1/({_conv(n)})))")
                else:
                    a, i = _group(s, i)
                    out.append(f"sqrt({_conv(a)})")
            elif name in ("operatorname", "mathrm", "text"):
                word, i = _group(s, i)
                if word not in COMMANDS:
                    raise Unreadable(f"неизвестная функция {word}")
                out.append(f" {COMMANDS[word]} ")
            elif name in COMMANDS:
                out.append(f" {COMMANDS[name]} ")
            elif name in SPACING or name in ",;:! ":
                out.append(" ")
            elif name in ("{", "}"):
                out.append({"{": "(", "}": ")"}[name])
            elif name == "|":
                out.append("|")
            else:
                raise Unreadable(f"неизвестная команда \\{name}")
        elif ch == "^":
            a, i = _group(s, i + 1)
            out.append(f"^({_conv(a)})")
        elif ch == "{":
            a, i = _group(s, i)
            out.append(f"({_conv(a)})")
        elif ch == "}":
            raise Unreadable("непарные скобки")
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
    if not text:
        raise Unreadable("пусто")
    return text
