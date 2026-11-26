"""Безопасный парсинг студенческого ввода в sympy-выражение.

Конвейер: проверка длины → normalize (строковые замены, применение функций
без скобок, |..| → Abs) → белый список символов и имён → parse_expr
без auto_symbol и без builtins.
"""
import ast
import math
import re

import sympy as sp
from sympy.parsing.sympy_parser import auto_number, eval_expr, implicit_multiplication, stringify_expr

MAX_LEN = 300

# студенческое имя → имя sympy
ALIASES = {
    "arcctg": "acot", "arctg": "atan", "arcsin": "asin", "arccos": "acos",
    "ctg": "cot", "tg": "tan", "ln": "log", "cth": "coth",
    "sh": "sinh", "ch": "cosh", "th": "tanh", "abs": "Abs",
}

FUNCS = {
    "sin": sp.sin, "cos": sp.cos, "tan": sp.tan, "cot": sp.cot, "sec": sp.sec, "csc": sp.csc,
    "asin": sp.asin, "acos": sp.acos, "atan": sp.atan, "acot": sp.acot,
    "sinh": sp.sinh, "cosh": sp.cosh, "tanh": sp.tanh, "coth": sp.coth,
    "asinh": sp.asinh, "acosh": sp.acosh, "atanh": sp.atanh,
    "log": sp.log, "exp": sp.exp, "sqrt": sp.sqrt, "Abs": sp.Abs,
}
CONSTS = {"pi": sp.pi, "e": sp.E, "E": sp.E, "oo": sp.oo}

# для подсказки «sinx → sin(x)»: все имена функций, которые может набрать студент
_STUDENT_FUNCS = sorted(set(FUNCS) | set(ALIASES), key=len, reverse=True)

_ALIAS_RE = re.compile(
    r"(?<![A-Za-z])(" + "|".join(sorted(ALIASES, key=len, reverse=True)) + r")(?![A-Za-z])")
_CONST_RE = re.compile(r"^(.*?)\s*[+-]\s*(?:C|c|const)\s*$", re.S)
_ALLOWED_CHARS_RE = re.compile(r"^[A-Za-z0-9+\-*/().\s]*$")
_BAD_DOT_RE = re.compile(r"(?<!\d)\.|\.(?!\d)")
_NAME_RE = re.compile(r"[A-Za-z]+")
_SIMPLE_ARG_RE = re.compile(r"\d+(?:\.\d+)?[A-Za-z]*|[A-Za-z]+")
_EXP_RE = re.compile(r"\d+(?:\.\d+)?")

_GLOBALS = {"__builtins__": {}, "Integer": sp.Integer, "Float": sp.Float, "Rational": sp.Rational}
_TRANSFORMS = (auto_number, implicit_multiplication)


class ParseError(ValueError):
    """Ввод не разобран. str(e) — сообщение для студента."""


def split_constant(raw: str) -> tuple[str, bool]:
    """Отделяет константу интегрирования (±C, ±c, ±const в конце).
    Возвращает (строка без константы, была ли константа)."""
    m = _CONST_RE.match(raw)
    if m:
        return m.group(1), True
    return raw, False


def _replace_abs(s: str) -> str:
    if s.count("|") % 2:
        raise ParseError("Непарные палочки модуля |…|")
    out, opened = [], False
    for ch in s:
        if ch == "|":
            out.append(")" if opened else " Abs(")
            opened = not opened
        else:
            out.append(ch)
    return "".join(out)


def _match_paren(s: str, i: int) -> int:
    """s[i] == '(' → индекс парной ')'."""
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "(":
            depth += 1
        elif s[j] == ")":
            depth -= 1
            if depth == 0:
                return j
    raise ParseError("Непарные скобки")


def _skip_spaces(s: str, i: int) -> int:
    while i < len(s) and s[i] == " ":
        i += 1
    return i


def _apply_funcs(s: str) -> str:
    """sin 2x → sin(2x), sin**2 x → sin(x)**2, sin**2(2x) → sin(2x)**2, log Abs(..) → log(Abs(..))."""
    out, i = [], 0
    while i < len(s):
        m = _NAME_RE.match(s, i)
        if m and (i == 0 or not s[i - 1].isalpha()) and m.group() in FUNCS:
            text, i = _apply_one(s, i, m.group())
            out.append(text)
        elif m and (i == 0 or not s[i - 1].isalpha()):
            out.append(m.group())
            i = m.end()
        elif s[i] == "(":
            j = _match_paren(s, i)
            out.append("(" + _apply_funcs(s[i + 1:j]) + ")")
            i = j + 1
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def _apply_one(s: str, i: int, name: str) -> tuple[str, int]:
    i += len(name)
    exp = None
    j = _skip_spaces(s, i)
    if s.startswith("**", j):
        k = _skip_spaces(s, j + 2)
        if k < len(s) and s[k] == "(":
            end = _match_paren(s, k)
            exp, i = s[k:end + 1], end + 1
        else:
            m = _EXP_RE.match(s, k)
            if not m:
                raise ParseError(f"Не понял степень у {name}")
            exp, i = m.group(), m.end()
        j = _skip_spaces(s, i)
    if j < len(s) and s[j] == "(":
        end = _match_paren(s, j)
        arg, i = _apply_funcs(s[j + 1:end]), end + 1
    else:
        m = _NAME_RE.match(s, j)
        if m and m.group() in FUNCS:
            arg, i = _apply_one(s, j, m.group())
        else:
            m = _SIMPLE_ARG_RE.match(s, j)
            if not m:
                raise ParseError(f"Не понял аргумент у {name}: поставь скобки, например {name}(x)")
            arg, i = m.group(), m.end()
    text = f"{name}({arg})"
    if exp is not None:
        text = f"{text}**{exp}"
    return text, i


def normalize(raw: str) -> str:
    """Строковая нормализация: ^ → **, tg → tan, ln → log, |..| → Abs(..), √, запятая и т.п."""
    s = raw.strip()
    s = re.sub(r"(\d),(\d)", r"\1.\2", s)
    s = s.replace("^", "**").replace("√", " sqrt ").replace("\t", " ")
    s = _ALIAS_RE.sub(lambda m: ALIASES[m.group(1)], s)
    s = _replace_abs(s)
    s = _apply_funcs(s)
    s = re.sub(r"(\d)([A-Za-z(])", r"\1*\2", s)   # 2x → 2*x, 2sin → 2*sin, 2( → 2*(
    return re.sub(r" +", " ", s).strip()


def _unknown_name_error(name: str, var: str) -> ParseError:
    for f in _STUDENT_FUNCS:
        if name.startswith(f) and len(name) > len(f):
            return ParseError(f"Не понял «{name}». Отдели аргумент: {f}({name[len(f):]}) или {f} {name[len(f):]}")
    return ParseError(f"Неизвестное имя «{name}». Переменная здесь — {var}")


MAX_EXPONENT = 1000           # x^1001 и больше — не ответ, а способ повесить бота (9^9^9)
MAX_DIGITS = 2000             # и число-степень не длиннее стольких цифр


class _TooBig(Exception):
    pass


def _magnitude(node: ast.AST) -> float | None:
    """Грубое значение числовой части дерева до вычисления; None — есть переменная или функция.
    Слишком большая степень где угодно → _TooBig. Сами степени не вычисляем."""
    if isinstance(node, ast.Expression):
        return _magnitude(node.body)
    if isinstance(node, ast.Constant):
        try:
            return float(node.value)
        except (TypeError, ValueError, OverflowError):
            return None
    if isinstance(node, ast.UnaryOp):
        v = _magnitude(node.operand)
        return None if v is None else -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp):
        a, b = _magnitude(node.left), _magnitude(node.right)
        if isinstance(node.op, ast.Pow):
            if b is not None and abs(b) > MAX_EXPONENT:
                raise _TooBig
            if a is None or b is None:
                return None
            if a and abs(b) * abs(math.log10(abs(a))) > MAX_DIGITS:
                raise _TooBig
        if a is None or b is None:
            return None
        try:
            return {ast.Add: lambda: a + b, ast.Sub: lambda: a - b, ast.Mult: lambda: a * b, ast.Div: lambda: a / b,
                    ast.Pow: lambda: abs(a) ** b}[type(node.op)]()
        except (OverflowError, ZeroDivisionError, KeyError):
            return None if not isinstance(node.op, ast.Pow) else math.inf
    if isinstance(node, ast.Call):
        args = [_magnitude(x) for x in node.args]
        name = getattr(node.func, "id", "")
        if name in ("Integer", "Float", "Rational"):
            try:
                return float(ast.literal_eval(node.args[0]))
            except (ValueError, TypeError, OverflowError, SyntaxError):
                return None
        # exp — тоже степень; корень и прочие функции числа не увеличивают
        if name == "exp" and args and args[0] is not None and abs(args[0]) > MAX_EXPONENT:
            raise _TooBig
        return args[0] if name in ("sqrt", "Abs") and args else None
    if isinstance(node, ast.Name):
        return {"pi": math.pi, "e": math.e, "E": math.e}.get(node.id)
    return None


def parse_answer(raw: str, var: str = "x") -> sp.Expr:
    """Студенческий ввод → sympy.Expr. Разрешены только переменная var,
    функции и константы из белого списка. Иначе ParseError."""
    if len(raw) > MAX_LEN:
        raise ParseError(f"Слишком длинный ввод (больше {MAX_LEN} символов)")
    if not raw.strip():
        raise ParseError("Пустой ввод")
    if "_" in raw or not _ALLOWED_CHARS_RE.match(raw.replace("^", "").replace("|", "")
                                                   .replace("√", "").replace(",", "")):
        raise ParseError("Недопустимые символы. Можно: буквы, цифры, + - * / ^ ( ) | √")

    s = normalize(raw)
    if _BAD_DOT_RE.search(s):
        raise ParseError("Точка допустима только в десятичных числах (2.5)")
    allowed = set(FUNCS) | set(CONSTS) | {var}
    for name in _NAME_RE.findall(s):
        if name not in allowed:
            raise _unknown_name_error(name, var)

    local = {**FUNCS, **CONSTS, var: sp.Symbol(var)}
    try:
        code = stringify_expr(s, local, dict(_GLOBALS), _TRANSFORMS)
        _magnitude(ast.parse(code.strip(), mode="eval"))      # до вычисления: 9^9^9 повесил бы процесс
        expr = eval_expr(code, local, dict(_GLOBALS))
    except _TooBig:
        raise ParseError("Слишком большая степень") from None
    except ParseError:
        raise
    except Exception:
        raise ParseError("Не смог разобрать запись — проверь скобки и знаки") from None

    if not isinstance(expr, sp.Expr):
        raise ParseError("Ожидалось выражение")
    if expr.has(sp.zoo, sp.nan):
        raise ParseError("Выражение не определено (деление на ноль?)")
    if any(sym.name != var for sym in expr.free_symbols):
        raise ParseError(f"Переменная здесь — {var}")
    return expr


def parse_value(raw: str, var: str = "x") -> sp.Expr:
    """Значение предела: число, oo, ∞, −∞. Переменная или комплексное число → ParseError."""
    v = parse_answer(raw.replace("∞", "oo").replace("−", "-"), var)
    if v.free_symbols or (not v.is_infinite and v.is_extended_real is False):
        raise ParseError("Ответ — число, ∞ или −∞")
    return v


def parse_trusted(src: str, var: str = "x") -> sp.Expr:
    """Эталон из шаблона (доверенная строка в синтаксисе sympy) → sympy.Expr."""
    return sp.sympify(src, locals={var: sp.Symbol(var)})
