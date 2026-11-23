"""Прогон всего банка шаблонов: каждый шаблон × N_INSTANCES экземпляров.
Шаблоны подхватываются из data/templates автоматически."""
import random
import time
from functools import lru_cache
from pathlib import Path

import pytest
import sympy as sp

from core.checker import (
    FACTOR, NO_CONST, OK, SIGN,
    check_antiderivative, check_expr, check_with_timeout, shutdown_pool,
)
from core.generator import N_DISTRACTORS, generate, sample_params
from core.parser import parse_trusted
from core.templates import load_templates

N_INSTANCES = 50
N_TIMED = 5
BANK = load_templates(Path(__file__).resolve().parent.parent / "data" / "templates")
IDS = sorted(BANK)


@lru_cache(maxsize=None)
def instances(tid, n=N_INSTANCES):
    """Экземпляры для seed 0…n−1 без повторов: экземпляр определяется параметрами (build детерминирован),
    поэтому одинаковые наборы параметров проверяем один раз."""
    first: dict[tuple, int] = {}
    for seed in range(n):
        params = sample_params(BANK[tid], random.Random(seed))      # те же, что возьмёт generate(seed)
        first.setdefault(tuple(sorted(params.items())), seed)
    return tuple((seed, generate(BANK[tid], seed)) for seed in first.values())


@pytest.mark.parametrize("tid", IDS)
def test_instances_cover_all_sampled_params(tid):
    # дедупликация ничего не потеряла: наборы параметров те же, что у всех seed подряд
    want = {tuple(sorted(sample_params(BANK[tid], random.Random(s)).items())) for s in range(N_INSTANCES)}
    got = [tuple(sorted(i.params.items())) for _, i in instances(tid)]
    assert set(got) == want and len(got) == len(want)


def antiderivative_cp(inst):
    return next(c for c in inst.checkpoints if c.type == "antiderivative")


def test_bank_size():
    assert len(BANK) >= 15                                   # критерий готовности к 25.11
    levels = [t.difficulty for t in BANK.values()]
    assert levels.count("A") >= 6 and levels.count("B") >= 8 and levels.count("C") >= 1


def test_blitz_methods():
    methods = {t.blitz_method for t in BANK.values() if t.blitz_method}
    assert len(methods) >= 4                                 # в блице 4 различных кнопки
    assert all(t.blitz_method for t in BANK.values())
    assert all(t.checkpoints[0].hint for t in BANK.values())  # разбор ошибок блица берёт hint 1-й точки


@pytest.mark.parametrize("tid", IDS)
def test_generates(tid):
    # generate() внутри делает самопроверку: падение = опечатка в шаблоне
    insts = instances(tid)
    assert len({tuple(sorted(i.params.items())) for _, i in insts}) > 1


@pytest.mark.parametrize("tid", IDS)
def test_reference_accepted_and_mistakes_rejected(tid):
    for seed, inst in instances(tid):
        cp = antiderivative_cp(inst)
        ref, f, dom = cp.reference, inst.integrand, cp.domain
        r = parse_trusted(ref)
        cases = {
            f"{ref} + C": OK,
            f"{sp.expand(r)} + C": OK,
            f"{sp.trigsimp(r)} + C": OK,
            f"-({ref}) + C": SIGN,
            f"2*({ref}) + C": FACTOR,
            ref: NO_CONST,
        }
        for user, want in cases.items():
            got = check_antiderivative(user, f, domain=dom, symbolic=False).status
            assert got == want, f"{tid} seed={seed} params={inst.params}: {user!r} → {got}, ждали {want}"


@pytest.mark.parametrize("tid", IDS)
def test_intermediate_checkpoints(tid):
    for seed, inst in instances(tid):
        for cp in inst.checkpoints:
            ctx = f"{tid} seed={seed} params={inst.params} {cp.type}"
            if cp.type == "choice":
                assert cp.correct and set(cp.correct) <= set(cp.options), ctx
            elif cp.type in ("expr", "transformed_integrand"):
                assert check_expr(cp.expected, cp.expected, var=cp.var, domain=cp.domain,
                                  symbolic=False).status == OK, ctx
                assert len(cp.distractors) == N_DISTRACTORS, ctx
                for d in cp.distractors:
                    got = check_expr(d, cp.expected, var=cp.var, domain=cp.domain, symbolic=False).status
                    assert got != OK, f"{ctx}: дистрактор {d!r} принят"


@pytest.mark.parametrize("tid", IDS)
def test_check_time(tid):
    for seed, inst in instances(tid, N_TIMED):
        cp = antiderivative_cp(inst)
        t0 = time.monotonic()
        v = check_with_timeout("antiderivative", f"{cp.reference} + C", inst.integrand, domain=cp.domain)
        dt = time.monotonic() - t0
        assert v.status == OK, f"{tid} seed={seed}: {v}"
        assert dt < 3.0, f"{tid} seed={seed}: {dt:.2f} с"


@pytest.fixture(scope="module", autouse=True)
def _pool():
    yield
    shutdown_pool()


# --- сверка с задачником: каждый пример из examples ----------------------------

from core.generator import check_example

EXAMPLES = [(tid, ex) for tid in IDS for ex in BANK[tid].examples]


@pytest.mark.parametrize("tid, ex", EXAMPLES, ids=[f"{t}-{e.number}" for t, e in EXAMPLES])
def test_examples_match_book(tid, ex):
    check_example(BANK[tid], ex)
