import copy
import shutil
from pathlib import Path

import pytest
import yaml

from bot.bank import reload_bank
from core.templates import load_templates
from tests.test_templates import VALID

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "data" / "templates"


@pytest.fixture
def tdir(tmp_path):
    d = tmp_path / "templates"
    shutil.copytree(TEMPLATES_DIR, d)
    return d


def test_reload_ok_in_place(tdir):
    extra = tdir / "extra.yaml"                            # отдельный файл: на его методы никто не ссылается
    extra.write_text(yaml.safe_dump([{**copy.deepcopy(VALID), "id": "toy_extra"}], allow_unicode=True), encoding="utf-8")
    bank = load_templates(tdir)
    same_obj, n = bank, len(bank)
    removed = {tid for tid, t in bank.items() if t.file.endswith("extra.yaml")}
    assert removed == {"toy_extra"}
    extra.unlink()                                         # убрали файл с шаблонами
    ok, msg = reload_bank(bank, tdir)
    assert ok and bank is same_obj                         # тот же dict — хендлеры видят новый банк
    assert len(bank) == n - len(removed) and not removed & set(bank)
    assert str(len(bank)) in msg


def test_reload_broken_yaml_keeps_bank(tdir):
    bank = load_templates(tdir)
    before = dict(bank)
    (tdir / "parts.yaml").write_text("- id: [oops\n", encoding="utf-8")
    ok, msg = reload_bank(bank, tdir)
    assert not ok and "parts.yaml" in msg
    assert bank == before


def test_reload_invalid_template_keeps_bank(tdir):
    bank = load_templates(tdir)
    before = dict(bank)
    p = tdir / "parts.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace("difficulty: A", "difficulty: Z"), encoding="utf-8")
    ok, msg = reload_bank(bank, tdir)
    assert not ok and "int_parts_poly_exp_01" in msg
    assert bank == before


def test_reload_selfcheck_failure_keeps_bank(tdir):
    bank = load_templates(tdir)
    before = dict(bank)
    p = tdir / "parts.yaml"
    text = p.read_text(encoding="utf-8")
    p.write_text(text.replace('reference: "exp(a*x)*((p*x + q)/a - p/a**2)"',
                              'reference: "exp(a*x)*((p*x + q)/a + p/a**2)"'), encoding="utf-8")  # опечатка
    ok, msg = reload_bank(bank, tdir)
    assert not ok and "int_parts_poly_exp_01" in msg
    assert bank == before
