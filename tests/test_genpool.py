"""Процесс-генератор и запас серий блица. Падения настоящие: процесс убивает себя SIGSEGV."""
import asyncio
import os
import signal
import time
from pathlib import Path

import pytest

from bot.blitz import BLITZ_SIZE, N_OPTIONS, BlitzRun
from bot.genpool import STOCK_SIZE, BlitzStock, GenError, GenPool
from core.checker import OK, check_antiderivative
from core.generator import generate
from core.hints import answer_hints
from core.templates import load_templates

BANK = load_templates(Path(__file__).resolve().parent.parent / "data" / "templates")


# функции для процесса-генератора (должны импортироваться по имени модуля)
def segfault():
    os.kill(os.getpid(), signal.SIGSEGV)


def segfault_once(flag):
    if not os.path.exists(flag):
        open(flag, "w").close()
        os.kill(os.getpid(), signal.SIGSEGV)
    return "выжил"


def boom():
    raise ValueError("обычная ошибка")


def niceness():
    return os.nice(0)


@pytest.fixture
async def pool():
    p = GenPool(timeout=15)
    yield p
    await asyncio.to_thread(p.close)


# --- процесс -----------------------------------------------------------------------

async def test_runs_in_another_single_process(pool):
    pids = await asyncio.gather(*(pool.run(os.getpid) for _ in range(4)))
    assert len(set(pids)) == 1 and pids[0] != os.getpid()            # одно ядро — один процесс


async def test_low_priority(pool):
    assert await pool.run(niceness) > niceness()                      # проверка ответов важнее


async def test_segfault_does_not_kill_bot(pool):
    with pytest.raises(GenError):
        await pool.run(segfault)
    assert await pool.run(os.getpid) != os.getpid()                  # следующий запрос проходит


async def test_segfault_once_is_retried(pool, tmp_path):
    assert await pool.run(segfault_once, str(tmp_path / "flag")) == "выжил"


async def test_neighbours_survive_segfault(pool):
    """Рядом с упавшим запросом остальные (в очереди) доходят до конца."""
    res = await asyncio.gather(pool.run(segfault), *(pool.run(niceness) for _ in range(3)), return_exceptions=True)
    assert isinstance(res[0], GenError) and all(isinstance(r, int) for r in res[1:])


async def test_hang_is_cut_by_timeout():
    p = GenPool(timeout=1)
    try:
        await p.run(os.getpid)                                        # прогрев, чтобы не мерить старт процесса
        t0 = time.monotonic()
        with pytest.raises(GenError):
            await p.run(time.sleep, 60)
        assert time.monotonic() - t0 < 12                             # две попытки по секунде + перезапуск
        assert await p.run(os.getpid)
    finally:
        await asyncio.to_thread(p.close)


async def test_ordinary_error_is_not_retried_and_visible(pool):
    with pytest.raises(GenError, match="обычная ошибка"):
        await pool.run(boom)


async def test_loop_is_free_while_generating(pool):
    """Пока генератор занят, бот отвечает: цикл событий не блокируется."""
    await pool.run(os.getpid)
    job = asyncio.create_task(pool.run(time.sleep, 1.0))
    ticks = 0
    while not job.done():
        await asyncio.sleep(0.02)
        ticks += 1
    assert ticks > 20


async def test_busy_flag(pool):
    assert not pool.busy
    job = asyncio.create_task(pool.run(time.sleep, 0.5))
    await asyncio.sleep(0.05)
    assert pool.busy
    await job
    assert not pool.busy


# --- результат тот же, что без процесса ------------------------------------------------

@pytest.mark.parametrize("tid", ["int_exp_lcm_01", "mix_binom_01", "mix_abs_sq_01", "int_diff_under_01"])
async def test_instance_same_as_direct(pool, tid):
    inst = await pool.instance(BANK[tid], 5)
    assert inst == generate(BANK[tid], 5)
    cp = next(c for c in inst.checkpoints if c.type == "antiderivative")
    assert check_antiderivative(cp.reference + " + C", inst.integrand, domain=cp.domain, symbolic=False).status == OK


@pytest.mark.parametrize("direct", [False, True])
async def test_hints_same_as_direct(pool, direct):
    inst = generate(BANK["int_exp_lcm_01"], 3)
    assert await pool.hints(inst, direct) == answer_hints(inst, direct)


async def test_blitz_tasks_same_as_direct(pool):
    assert await pool.blitz_tasks(BANK, 7) == BlitzRun(BANK, seed=7).tasks


def test_run_from_ready_tasks():
    tasks = BlitzRun(BANK, seed=7).tasks
    run = BlitzRun.from_tasks(tasks, time_limit=25)                    # время на карточку от задач не зависит
    assert run.tasks == tasks and run.time_limit == 25
    assert run.answer(run.current().options.index(run.current().correct), 1.0).verdict == "ok"
    with pytest.raises(ValueError):
        BlitzRun.from_tasks(tasks, time_limit=7)


# --- запас ---------------------------------------------------------------------------------

class FakePool:
    """Счётчик вместо процесса: запас проверяем отдельно от генерации."""
    def __init__(self):
        self.made, self.busy, self.gate = 0, False, None

    async def blitz_tasks(self, bank, seed):
        if self.gate:
            await self.gate.wait()
        self.made += 1
        return (f"серия {self.made}", seed, len(bank))


async def filled(n):
    p = FakePool()
    s = BlitzStock(p, dict(BANK), size=n)
    while await s.refill_once():
        pass
    return p, s


async def test_refill_stops_at_size():
    p, s = await filled(5)
    assert len(s) == 5 == p.made and not await s.refill_once()


async def test_thirty_students_at_once_need_no_generation():
    p, s = await filled(STOCK_SIZE)
    made = p.made
    got = await asyncio.gather(*(s.take() for _ in range(STOCK_SIZE)))
    assert p.made == made and len(s) == 0
    assert len(set(got)) == STOCK_SIZE                                 # одна серия не достаётся двоим


async def test_empty_stock_generates():
    p = FakePool()
    s = BlitzStock(p, dict(BANK), size=2)
    assert (await s.take())[0] == "серия 1" and len(s) == 0


async def test_refill_yields_to_students():
    p = FakePool()
    s = BlitzStock(p, dict(BANK), size=2)
    p.busy = True
    assert not await s.refill_once() and p.made == 0
    p.busy = False
    assert await s.refill_once() and len(s) == 1


async def test_seeds_differ():
    _, s = await filled(10)
    assert len({(await s.take())[1] for _ in range(10)}) == 10


async def test_reset_drops_old_series():
    p, s = await filled(3)
    s.reset()
    assert len(s) == 0


async def test_reset_drops_series_in_progress():
    p = FakePool()
    p.gate = asyncio.Event()
    s = BlitzStock(p, dict(BANK), size=3)
    job = asyncio.create_task(s.refill_once())
    await asyncio.sleep(0.01)
    s.reset()                                                          # шаблоны перечитали, пока серия готовилась
    p.gate.set()
    await job
    assert len(s) == 0


async def test_stock_sees_reloaded_bank():
    """reload_bank меняет тот же dict — новые серии берутся из нового банка."""
    p = FakePool()
    bank = dict(BANK)
    s = BlitzStock(p, bank, size=1)
    bank.pop("int_diff_under_01")
    s.reset()
    assert (await s.take())[2] == len(BANK) - 1


async def test_keep_full_survives_generator_failure():
    class Flaky(FakePool):
        async def blitz_tasks(self, bank, seed):
            self.made += 1
            if self.made == 1:
                raise GenError("упал")
            return (self.made,)
    p = Flaky()
    s = BlitzStock(p, dict(BANK), size=2)
    job = asyncio.create_task(s.keep_full())
    for _ in range(300):
        await asyncio.sleep(0.02)
        if len(s) == 2:
            break
    job.cancel()
    assert len(s) == 2                                                 # одна неудача не останавливает пополнение


async def test_real_series_from_stock_is_valid(pool):
    s = BlitzStock(pool, dict(BANK), size=1)
    assert await s.refill_once()
    tasks = await s.take()
    assert len(tasks) == BLITZ_SIZE and len({t.template_id for t in tasks}) == BLITZ_SIZE
    for t in tasks:
        assert len(set(t.options)) == N_OPTIONS and t.correct in t.options


async def test_student_request_waits_at_most_one_refill(pool):
    await pool.run(os.getpid)
    s = BlitzStock(pool, dict(BANK), size=30)
    job = asyncio.create_task(s.keep_full())
    await asyncio.sleep(0.3)
    t0 = time.monotonic()
    await pool.instance(BANK["int_diff_under_01"], 1)
    waited = time.monotonic() - t0
    job.cancel()
    assert waited < 4                                                  # не ждёт, пока наберутся все 30
