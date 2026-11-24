"""Генерация задач в отдельном процессе и запас готовых серий блица.

Процесс один (на сервере одно ядро) и с низким приоритетом: проверка ответов важнее.
Упал или завис — перезапускается, бот продолжает работать.
"""
import asyncio
import logging
import multiprocessing
import os
import secrets
from collections import deque
from concurrent.futures import TimeoutError as FutureTimeout

from pebble import ProcessExpired, ProcessPool

from bot.blitz import BlitzRun, BlitzTask
from core.generator import Instance, generate
from core.hints import Hint, answer_hints
from core.templates import Template

STOCK_SIZE = 30
NICE = 10
GEN_FAILED = "Не получилось подготовить задачу. Нажми ещё раз."


class GenError(RuntimeError):
    """Процесс-генератор упал или завис дважды подряд, либо генерация закончилась ошибкой."""


def _init() -> None:
    os.nice(NICE)


def _blitz(bank: dict[str, Template], seed: int) -> tuple[BlitzTask, ...]:
    return BlitzRun(bank, seed=seed).tasks


class GenPool:
    def __init__(self, timeout: float = 20.0):
        self._timeout = timeout
        self._pool: ProcessPool | None = None
        self._active = 0

    def _get(self) -> ProcessPool:
        if self._pool is None or not self._pool.active:
            # spawn, а не fork: см. core.checker
            self._pool = ProcessPool(max_workers=1, initializer=_init, context=multiprocessing.get_context("spawn"))
        return self._pool

    async def run(self, fn, *args):
        """fn(*args) в процессе-генераторе. Падение или таймаут — одна повторная попытка, затем GenError."""
        self._active += 1
        try:
            for attempt in (1, 2):
                try:
                    return await asyncio.wrap_future(self._get().schedule(fn, args=args, timeout=self._timeout))
                except (FutureTimeout, ProcessExpired) as exc:
                    logging.warning("генератор: попытка %d, %s: %r", attempt, getattr(fn, "__name__", fn), exc)
                except Exception as exc:
                    logging.warning("генератор: %s: %r", getattr(fn, "__name__", fn), exc)
                    raise GenError(str(exc)) from exc
            raise GenError("процесс-генератор упал или завис")
        finally:
            self._active -= 1

    async def instance(self, tpl: Template, seed: int) -> Instance:
        return await self.run(generate, tpl, seed)

    async def hints(self, inst: Instance, direct: bool) -> tuple[Hint, ...]:
        return await self.run(answer_hints, inst, direct)

    async def blitz_tasks(self, bank: dict[str, Template], seed: int) -> tuple[BlitzTask, ...]:
        return await self.run(_blitz, dict(bank), seed)

    @property
    def busy(self) -> bool:
        """Есть запросы в работе или в очереди (пополнение запаса идёт по одному и ждёт, пока их нет)."""
        return self._active > 0

    def close(self) -> None:
        if self._pool is not None:
            self._pool.stop()
            self._pool.join()
            self._pool = None


class BlitzStock:
    def __init__(self, pool, bank: dict[str, Template], size: int = STOCK_SIZE):
        self._pool, self._bank, self._size = pool, bank, size
        self._ready: deque[tuple[BlitzTask, ...]] = deque()
        self._version = 0                 # растёт при reset: серия, начатая до него, в запас не попадёт

    def __len__(self) -> int:
        return len(self._ready)

    async def _make(self) -> tuple[BlitzTask, ...]:
        return await self._pool.blitz_tasks(self._bank, secrets.randbits(32))

    async def take(self) -> tuple[BlitzTask, ...]:
        """Готовая серия из запаса; пусто — приготовить сейчас."""
        return self._ready.popleft() if self._ready else await self._make()

    async def refill_once(self) -> bool:
        """Добавить одну серию. False — запас полон или генератор нужен студентам."""
        if len(self._ready) >= self._size or self._pool.busy:
            return False
        version = self._version
        tasks = await self._make()
        if version == self._version:
            self._ready.append(tasks)
        return True

    def reset(self) -> None:
        """Шаблоны перечитаны: старые серии (и та, что готовится прямо сейчас) больше не годятся."""
        self._version += 1
        self._ready.clear()

    async def keep_full(self) -> None:
        """Фоновая задача бота: пополнять запас, пока бот работает."""
        while True:
            try:
                added = await self.refill_once()
            except Exception as exc:
                logging.warning("запас блица: %r", exc)
                added = False
            await asyncio.sleep(0.05 if added else 1)
