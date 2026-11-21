"""Файл покрытия Демидовича согласован с банком шаблонов."""
from pathlib import Path

import yaml

from core.templates import load_templates

ROOT = Path(__file__).resolve().parent.parent
BANK = load_templates(ROOT / "data" / "templates")
COV = yaml.safe_load((ROOT / "data" / "demidovich.yaml").read_text(encoding="utf-8"))


def test_format():
    for num, e in COV.items():
        assert isinstance(num, int) and 1628 <= num <= 2180, num            # отдел III
        assert e["status"] in ("covered", "skip"), num
        if e["status"] == "covered":
            assert e.get("template") in BANK, f"{num}: нет шаблона {e.get('template')}"
        else:
            assert e.get("reason"), f"{num}: у пропуска нужна причина"


def test_every_example_is_marked_covered():
    for tid, tpl in BANK.items():
        for ex in tpl.examples:
            e = COV.get(int(ex.number))
            assert e and e["status"] == "covered" and e["template"] == tid, f"{tid}: № {ex.number} не отмечен"
