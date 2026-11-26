"""Решённое сам не подкидываем снова, пока в теме есть нерешённое; когда решено всё — по кругу, с самого давнего."""
import random

import pytest

from bot.handlers import train
from bot.topics import ANY_LEVEL, mix_key, pick_template
from tests.test_train_formula import BANK, STUDENT, w  # noqa: F401

TOPIC, LEVEL = "indef_integral.basic", "A"
POOL = sorted(t.id for t in BANK.values() if t.topic == TOPIC and t.difficulty == LEVEL)


def picks(solved, n=300, exclude=None, topic=TOPIC, level=LEVEL):
    rng = random.Random(0)
    return {pick_template(BANK, topic, level, rng, exclude=exclude, solved=solved).id for _ in range(n)}


# --- выбор -----------------------------------------------------------------------------

def test_pool_is_big_enough_for_the_test():
    assert len(POOL) >= 4


def test_without_history_everything_is_possible():
    assert picks({}) == set(POOL) == picks(None)


def test_solved_are_skipped_while_unsolved_remain():
    solved = {POOL[0]: "2026-11-20T10:00:00+03:00", POOL[1]: "2026-11-25T10:00:00+03:00"}
    assert picks(solved) == set(POOL) - set(solved)


def test_all_solved_gives_the_oldest():
    solved = {tid: f"2026-11-{10 + k:02d}T10:00:00+03:00" for k, tid in enumerate(POOL)}
    assert picks(solved) == {POOL[0]}


def test_oldest_but_not_twice_in_a_row():
    solved = {tid: f"2026-11-{10 + k:02d}T10:00:00+03:00" for k, tid in enumerate(POOL)}
    assert picks(solved, exclude=POOL[0]) == {POOL[1]}


def test_last_unsolved_still_not_twice_in_a_row():
    """Единственный нерешённый только что выпадал — лучше самый давний решённый, чем повтор."""
    solved = {tid: f"2026-11-{10 + k:02d}T10:00:00+03:00" for k, tid in enumerate(POOL[1:])}
    assert picks(solved, exclude=POOL[0]) == {POOL[1]}


def test_single_template_repeats():
    one = {POOL[0]: BANK[POOL[0]]}
    t = pick_template(one, TOPIC, LEVEL, random.Random(0), exclude=POOL[0], solved={POOL[0]: "2026-11-20T10:00:00+03:00"})
    assert t.id == POOL[0]                                         # повтор лучше, чем «задач нет»


def test_mix_skips_solved_too():
    sec = [t.id for t in BANK.values() if t.topic.startswith("indef_integral.") and t.difficulty == LEVEL]
    solved = {tid: "2026-11-20T10:00:00+03:00" for tid in sec[:-2]}
    assert picks(solved, topic=mix_key("indef_integral")) == set(sec[-2:])


def test_any_level():
    pool = [t.id for t in BANK.values() if t.topic == TOPIC]
    solved = {tid: "2026-11-20T10:00:00+03:00" for tid in pool[1:]}
    assert picks(solved, level=ANY_LEVEL) == {pool[0]}


# --- что считается решённым ------------------------------------------------------------------

async def finished(db, tid, status, mode="train", uid=STUDENT):
    from core.generator import generate
    st = await db.get_student_by_tg(uid)
    iid = await db.save_instance(generate(BANK[tid], 0), BANK[tid].topic, BANK[tid].difficulty)
    sid = await db.start_session(st.id, iid, mode)
    if status:
        await db.finish_session(sid, status)
    return sid


async def test_only_own_solved_count(w):                                     # noqa: F811
    from tests.test_train_formula import ADMIN
    await finished(w.db, POOL[0], "solved")
    await finished(w.db, POOL[1], "solved", mode="train_direct")
    await finished(w.db, POOL[2], "revealed")                        # показан ответ — не решена
    await finished(w.db, POOL[3], "dropped")
    await finished(w.db, POOL[3], None)                              # не закончена
    await finished(w.db, POOL[2], "solved", uid=ADMIN)               # решил кто-то другой
    st = await w.db.get_student_by_tg(STUDENT)
    assert set(await w.db.solved_templates(st.id)) == {POOL[0], POOL[1]}


async def test_blitz_does_not_count(w):                                       # noqa: F811
    await finished(w.db, POOL[0], "solved", mode="blitz")
    st = await w.db.get_student_by_tg(STUDENT)
    assert await w.db.solved_templates(st.id) == {}


async def test_latest_time_is_kept(w):                                        # noqa: F811
    await finished(w.db, POOL[0], "solved")
    await w.db.conn.execute("UPDATE sessions SET finished_at='2026-11-01T10:00:00+03:00'")
    await finished(w.db, POOL[0], "solved")
    st = await w.db.get_student_by_tg(STUDENT)
    assert (await w.db.solved_templates(st.id))[POOL[0]] > "2026-11-02"


# --- в боте ------------------------------------------------------------------------------------

async def test_bot_avoids_solved(w):                                          # noqa: F811
    for tid in POOL[:-1]:
        await finished(w.db, tid, "solved")
    got = set()
    for _ in range(6):
        train.LAST_TPL.pop(STUDENT, None)
        await w.feed(cb=f"next:{TOPIC}:{LEVEL}")
        got.add(train.OFFERED[STUDENT].inst.template_id)
    assert got == {POOL[-1]}
