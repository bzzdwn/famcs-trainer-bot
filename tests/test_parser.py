import pytest
import sympy as sp

from core.parser import MAX_LEN, ParseError, normalize, parse_answer, split_constant

x, t = sp.symbols("x t")


# --- нормализация ---------------------------------------------------------

@pytest.mark.parametrize("raw", ["x^2/2 + C", "x^2/2+C", "x^2/2 + c", "x^2/2 + const", "x^2/2+C ", "x^2/2 - C"])
def test_split_constant_present(raw):
    body, has_c = split_constant(raw)
    assert has_c is True
    assert parse_answer(body) == x**2 / 2


@pytest.mark.parametrize("raw", ["x^2/2", "x^2/2 + 5", "C + x^2/2", "x^2/2 + 2C", "x^2/2 + Cx"])
def test_split_constant_absent(raw):
    _, has_c = split_constant(raw)
    assert has_c is False


def test_split_constant_only_c():
    body, has_c = split_constant("+C")
    assert has_c is True
    with pytest.raises(ParseError):
        parse_answer(body)


def test_parse_answer_does_not_strip_c():
    # +C срезает только split_constant; в parse_answer C — неизвестное имя
    with pytest.raises(ParseError):
        parse_answer("x^2/2 + C")


def test_comma_decimal():
    assert parse_answer("2,5x") == sp.Float(2.5) * x


def test_normalize_power():
    assert "**" in normalize("x^2") and "^" not in normalize("x^2")


# --- функции и константы -------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("tg x", sp.tan(x)),
    ("ctg x", sp.cot(x)),
    ("arctg x", sp.atan(x)),
    ("arcctg x", sp.acot(x)),
    ("arcsin x", sp.asin(x)),
    ("arccos x", sp.acos(x)),
    ("ln x", sp.log(x)),
    ("log(x)", sp.log(x)),
    ("sh x", sp.sinh(x)),
    ("ch x", sp.cosh(x)),
    ("th x", sp.tanh(x)),
    ("sqrt(x)", sp.sqrt(x)),
    ("√x", sp.sqrt(x)),
    ("√(x+1)", sp.sqrt(x + 1)),
    ("x^(1/3)", x ** sp.Rational(1, 3)),
    ("e^x", sp.exp(x)),
    ("exp(x)", sp.exp(x)),
    ("2e^(3x)", 2 * sp.exp(3 * x)),
    ("pi", sp.pi),
    ("oo", sp.oo),
])
def test_functions_and_constants(raw, expected):
    assert parse_answer(raw) == expected


# --- неявное умножение и студенческие записи -----------------------------

@pytest.mark.parametrize("raw, expected", [
    ("2x", 2 * x),
    ("2sin x", 2 * sp.sin(x)),
    ("x(x+1)", x * (x + 1)),
    ("(x+1)(x-1)", (x + 1) * (x - 1)),
    ("x e^x", x * sp.exp(x)),
    ("sin 2x", sp.sin(2 * x)),
    ("sin x cos x", sp.sin(x) * sp.cos(x)),
    ("sin^2 x", sp.sin(x) ** 2),
    ("sin^2(2x)", sp.sin(2 * x) ** 2),
    ("tg^3 x", sp.tan(x) ** 3),
    ("|x|", sp.Abs(x)),
    ("ln|x+1|", sp.log(sp.Abs(x + 1))),
    ("ln|x-1| - ln|x+1|", sp.log(sp.Abs(x - 1)) - sp.log(sp.Abs(x + 1))),
    ("1/2 ln|(x-1)/(x+1)|", sp.log(sp.Abs((x - 1) / (x + 1))) / 2),
])
def test_student_notation(raw, expected):
    assert parse_answer(raw) == expected


def test_other_var():
    assert parse_answer("2/(1+t^2)", var="t") == 2 / (1 + t**2)


# --- отказ: чужие имена, мусор, атаки ------------------------------------

@pytest.mark.parametrize("raw, var", [
    ("y", "x"),            # чужая переменная
    ("x", "t"),            # x при замене на t
    ("foo(x)", "x"),       # неизвестная функция
    ("sinx", "x"),         # слитно — не угадываем
    ("a*x", "x"),          # параметр не подставлен
])
def test_unknown_names(raw, var):
    with pytest.raises(ParseError):
        parse_answer(raw, var=var)


@pytest.mark.parametrize("raw", ["", "   ", "2+", ")(", "x**", "sin()", "1/0", "x +* 2"])
def test_garbage(raw):
    with pytest.raises(ParseError):
        parse_answer(raw)


@pytest.mark.parametrize("raw", [
    "__import__('os').system('ls')",
    "lambda: 1",
    "import os",
    "().__class__",
    "x.__class__",
    "eval('1')",
    "exec('1')",
    "open('/etc/passwd')",
    "Symbol('y')",
    "x" + "+x" * 200,      # длиннее MAX_LEN
])
def test_injection(raw):
    with pytest.raises(ParseError):
        parse_answer(raw)


def test_max_len_boundary():
    raw = "+".join(["x"] * 150)  # 299 символов
    assert len(raw) <= MAX_LEN
    assert parse_answer(raw) == 150 * x


def test_parse_error_message_is_human():
    with pytest.raises(ParseError) as e:
        parse_answer("sinx")
    assert str(e.value)  # непустое сообщение для студента
