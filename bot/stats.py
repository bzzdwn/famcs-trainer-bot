"""Личная статистика (/me)."""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from bot.db import DB, TZ

TRAIN_MODES = ("train", "train_direct")
COUNTED_STATUSES = ("solved", "revealed")      # dropped / abandoned не портят статистику
NOT_SPENT = ("parse_error", "timeout", "approx")


@dataclass(frozen=True)
class Stats:
    topics: dict[str, tuple[int, int]]   # topic → (решено без показа ответа, всего доведено до конца)
    points: int                          # контрольных точек в учтённых задачах
    first_try: int                       # из них с первой попытки
    best_blitz: tuple[int, int, int] | None   # (счёт, из скольких, секунд на карточку)
    streak: int                          # дней подряд с активностью
    best_blitz_by_section: dict[str, tuple[int, int, int]] = field(default_factory=dict)

    @property
    def first_try_pct(self) -> float | None:
        return 100.0 * self.first_try / self.points if self.points else None


def local_date(iso: str) -> date:
    """ISO-время из БД → дата по Минску (время без пояса считаем минским)."""
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ).date()


def streak(days: set[date], today: date) -> int:
    """Дней подряд, заканчивая сегодня; если сегодня ещё не заходил — заканчивая вчера."""
    day = today if today in days else today - timedelta(days=1)
    n = 0
    while day in days:
        n += 1
        day -= timedelta(days=1)
    return n


def _in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join("?" * len(values)) + ")"


async def _fetch(db: DB, sql: str, *args) -> list[tuple]:
    async with db.conn.execute(sql, args) as cur:
        return await cur.fetchall()


async def student_stats(db: DB, student_id: int, today: date | None = None) -> Stats:
    today = today or datetime.now(TZ).date()
    counted = (f"s.student_id = ? AND s.mode IN {_in(TRAIN_MODES)} AND s.status IN {_in(COUNTED_STATUSES)}",
               (student_id, *TRAIN_MODES, *COUNTED_STATUSES))

    topics: dict[str, list[int]] = {}
    for topic, status, n in await _fetch(
            db, f"SELECT i.topic, s.status, COUNT(*) FROM sessions s JOIN instances i ON i.id = s.instance_id "
                f"WHERE {counted[0]} GROUP BY i.topic, s.status", *counted[1]):
        t = topics.setdefault(topic, [0, 0])
        t[1] += n
        if status == "solved":
            t[0] += n

    first: dict[tuple[int, int], str] = {}          # первая «настоящая» попытка на точке
    for sid, cp, verdict in await _fetch(
            db, f"SELECT a.session_id, a.checkpoint_idx, a.verdict FROM attempts a "
                f"JOIN sessions s ON s.id = a.session_id "
                f"WHERE {counted[0]} AND a.verdict NOT IN {_in(NOT_SPENT)} ORDER BY a.id",
            *counted[1], *NOT_SPENT):
        first.setdefault((sid, cp), verdict)

    by_section: dict[str, tuple[int, int, int]] = {}
    for sec, score, total, t in await _fetch(
            db, "SELECT COALESCE(section, 'indef_integral'), score, total, COALESCE(time_limit, 10) AS t "
                "FROM blitz_runs WHERE student_id = ? AND finished_at IS NOT NULL "
                "ORDER BY score DESC, t ASC, total ASC", student_id):
        by_section.setdefault(sec, (score, total, t))           # первая строка раздела — лучшая
    best = [by_section["indef_integral"]] if "indef_integral" in by_section else []

    days = {local_date(ts) for (ts,) in await _fetch(
        db, "SELECT a.created_at FROM attempts a JOIN sessions s ON s.id = a.session_id WHERE s.student_id = ?",
        student_id)}

    return Stats(
        topics={k: (v[0], v[1]) for k, v in topics.items()},
        points=len(first),
        first_try=sum(v == "ok" for v in first.values()),
        best_blitz=tuple(best[0]) if best else None,
        streak=streak(days, today),
        best_blitz_by_section=by_section,
    )
