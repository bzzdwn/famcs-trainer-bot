import copy
from pathlib import Path

import pytest

from core.templates import DEFAULT_DOMAIN, TemplateError, load_templates, parse_template

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "data" / "templates"

VALID = {
    "id": "toy_01",
    "topic": "indef_integral.test",
    "source": "тест",
    "difficulty": "A",
    "var": "x",
    "params": {"a": {"int": [1, 5]}, "s": {"choice": [1, -1]}, "k": {"int": [-3, 3], "exclude": [0]}},
    "constraints": ["a > 1"],
    "integrand": "s*a*cos(a*x)",
    "checkpoints": [
        {"type": "choice", "prompt": "?", "options": ["A", "B"], "correct": ["A"], "hint": "h"},
        {"type": "transformed_integrand", "prompt": "?", "var": "t", "substitution": "sin(a*x)",
         "expected": "s", "skippable": True},
        {"type": "antiderivative", "prompt": "?", "reference": "s*sin(a*x)", "domain": ["-a", "a"]},
    ],
    "blitz_method": "тест",
}


def broken(path: str, value):
    """Копия VALID с изменённым полем; path вида 'checkpoints.0.correct'. value=... → удалить поле."""
    raw = copy.deepcopy(VALID)
    *head, last = path.split(".")
    node = raw
    for key in head:
        node = node[int(key)] if key.isdigit() else node[key]
    if value is ...:
        del node[int(last) if last.isdigit() else last]
    else:
        node[int(last) if last.isdigit() else last] = value
    return raw


# --- валидный шаблон -------------------------------------------------------

def test_parse_valid():
    t = parse_template(VALID)
    assert t.id == "toy_01" and t.var == "x" and t.difficulty == "A"
    assert set(t.params) == {"a", "s", "k"}
    assert t.params["a"].kind == "int" and (t.params["a"].lo, t.params["a"].hi) == (1, 5)
    assert t.params["s"].kind == "choice" and t.params["s"].values == (1, -1)
    assert t.params["k"].exclude == (0,)
    assert [c.type for c in t.checkpoints] == ["choice", "transformed_integrand", "antiderivative"]
    assert t.checkpoints[1].var == "t" and t.checkpoints[1].skippable is True
    assert t.statement is None


def test_defaults():
    raw = broken("checkpoints.2.domain", ...)
    raw = {k: v for k, v in raw.items() if k not in ("constraints", "blitz_method")}
    t = parse_template(raw)
    assert t.checkpoints[2].domain == DEFAULT_DOMAIN
    assert t.constraints == () and t.blitz_method is None
    assert t.checkpoints[1].skippable is True and t.checkpoints[0].skippable is False


# --- ошибки формата --------------------------------------------------------

@pytest.mark.parametrize("path, value", [
    ("id", ...),                                         # нет id
    ("integrand", ...),                                  # нет integrand
    ("checkpoints", []),                                 # нет точек
    ("difficulty", "D"),                                 # неизвестный уровень
    ("checkpoints.0.type", "essay"),                     # неизвестный тип точки
    ("checkpoints.0.correct", ["C"]),                    # correct не из options
    ("checkpoints.0.correct", []),                       # нет верного варианта
    ("checkpoints.2.reference", ...),                    # antiderivative без reference
    ("checkpoints.1.expected", ...),                     # transformed_integrand без expected
    ("checkpoints.1.var", ...),                          # transformed_integrand без своей переменной
    ("checkpoints.2.reference", "s*sin(b*x)"),           # неописанный параметр b
    ("integrand", "s*a*cos(a*y)"),                       # чужая переменная
    ("constraints", ["b > 1"]),                          # неописанный параметр в constraints
    ("checkpoints.2.domain", ["-b", "b"]),               # неописанный параметр в domain
    ("checkpoints.2.domain", [-3.0]),                    # domain не из двух концов
    ("checkpoints.2.refrence", "s*sin(a*x)"),            # опечатка в ключе — не молчим
    ("params.a", {"int": [5, 1]}),                       # lo > hi
    ("params.a", {"float": [1, 5]}),                     # неизвестный тип параметра
    ("params.sin", {"int": [1, 5]}),                     # имя параметра = функция
    ("params.x", {"int": [1, 5]}),                       # имя параметра = переменная
    ("checkpoints.1.same_as", "something"),              # same_as только integrand
    ("integrand", "s*a*cos(a*x"),                        # синтаксис
])
def test_invalid(path, value):
    with pytest.raises(TemplateError):
        parse_template(broken(path, value))


def test_error_mentions_id():
    with pytest.raises(TemplateError, match="toy_01"):
        parse_template(broken("checkpoints.0.type", "essay"))


# --- загрузка с диска ------------------------------------------------------

def test_load_dir():
    bank = load_templates(TEMPLATES_DIR)
    assert len(bank) >= 5
    assert all(t.id == k for k, t in bank.items())
    assert all(t.file for t in bank.values())


def test_duplicate_id(tmp_path):
    text = (TEMPLATES_DIR / "parts.yaml").read_text(encoding="utf-8")
    (tmp_path / "a.yaml").write_text(text, encoding="utf-8")
    (tmp_path / "b.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(TemplateError, match="int_parts_poly_exp_01"):
        load_templates(tmp_path)


def test_error_mentions_file(tmp_path):
    (tmp_path / "bad.yaml").write_text("- id: x1\n  topic: t\n", encoding="utf-8")
    with pytest.raises(TemplateError, match="bad.yaml"):
        load_templates(tmp_path)


# --- same_as с произвольной формулой ---------------------------------------

def with_expr_cp(same_as):
    raw = copy.deepcopy(VALID)
    raw["checkpoints"].insert(1, {"type": "expr", "prompt": "?", "expected": "(x + a)**2", "same_as": same_as})
    return raw


def test_same_as_formula():
    t = parse_template(with_expr_cp("x**2 + 2*a*x + a**2"))
    assert t.checkpoints[1].same_as == "x**2 + 2*a*x + a**2"
    assert parse_template(with_expr_cp("integrand")).checkpoints[1].same_as == "integrand"


@pytest.mark.parametrize("same_as", ["x**2 + 2*b*x", "x**2 + (", "y**2"])
def test_same_as_formula_invalid(same_as):
    with pytest.raises(TemplateError):
        parse_template(with_expr_cp(same_as))


# --- method_alternatives -----------------------------------------------------

def test_method_alternatives_parsed():
    raw = copy.deepcopy(VALID)
    raw["method_alternatives"] = ["другой метод"]
    assert parse_template(raw).method_alternatives == ("другой метод",)
    assert parse_template(VALID).method_alternatives == ()


@pytest.mark.parametrize("value", ["одна строка", [1, 2], ["тест"]])   # не список строк / совпадает с blitz_method
def test_method_alternatives_invalid(value):
    raw = copy.deepcopy(VALID)
    raw["method_alternatives"] = value
    with pytest.raises(TemplateError):
        parse_template(raw)


def test_method_alternatives_need_blitz_method():
    raw = copy.deepcopy(VALID)
    raw["method_alternatives"] = ["другой метод"]
    del raw["blitz_method"]
    with pytest.raises(TemplateError):
        parse_template(raw)


def test_alternative_must_exist_in_bank(tmp_path):
    text = (TEMPLATES_DIR / "parts.yaml").read_text(encoding="utf-8")
    (tmp_path / "parts.yaml").write_text(text.replace('method_alternatives: ["по частям"]',
                                                      'method_alternatives: ["метод Остроградского"]'),
                                         encoding="utf-8")
    with pytest.raises(TemplateError, match="метод Остроградского"):
        load_templates(tmp_path)


def test_bank_alternatives():
    bank = load_templates(TEMPLATES_DIR)
    assert bank["int_trig_tan_01"].method_alternatives == ("универсальная замена",)
    assert bank["int_trig_odd_sin_01"].method_alternatives == ("универсальная замена",)
    assert bank["int_parts_cyclic_01"].method_alternatives == ("по частям",)
