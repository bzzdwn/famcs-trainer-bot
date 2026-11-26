"""/export: XLSX-отчёт для преподавателя — студенты, шаги шаблонов, путаница методов (блиц и «Вразнобой»)."""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from bot.db import DB, TZ
from bot.flow import MAX_ATTEMPTS, METHOD_IDX
from bot.stats import COUNTED_STATUSES, NOT_SPENT, TRAIN_MODES, local_date, streak
from bot.topics import TOPIC_NAMES, method_options, topic_title
from core.templates import Template

SHEETS = ["Студенты", "Шаблоны", "Блиц", "Вразнобой"]
ERRORS = ("wrong", "sign", "factor", "no_const")          # содержательные ошибки (не опечатки)
TOP_N = 3


@dataclass(frozen=True)
class StudentRow:
    name: str
    days_active: int
    streak: int
    solved: int                       # задач решено без показа ответа
    finished: int                     # задач доведено до конца (solved + revealed)
    first_try: float | None           # доля шагов с 1-й попытки, 0..1 (как в /me)
    topic_first_try: dict[str, float | None]
    best_blitz: str | None            # «9/10 · 10 с»
    blitz_runs: int                   # доигранных серий
    last_active: date | None


@dataclass(frozen=True)
class CheckpointRow:
    template_id: str
    topic: str
    difficulty: str
    step: int                         # номер шага для людей, с 1
    ctype: str
    attempts: int                     # потраченных попыток (без опечаток, таймаутов и «показать ответ»)
    error_rate: float | None          # доля содержательных ошибок среди попыток, 0..1
    parse_errors: int
    hint_rate: float | None           # доля дошедших до шага, кто брал подсказку
    revealed_rate: float | None       # доля дошедших до шага, кому показан ответ (кнопкой или после 3 ошибок)
    top_wrong: list[tuple[str, int]] = field(default_factory=list)


@dataclass(frozen=True)
class Matrix:
    methods: list[str]                               # порядок строк и столбцов
    counts: dict[tuple[str, str | None], int]        # (верный, выбранный | None — не успел) → сколько раз

    def accuracy(self, method: str) -> float | None:
        total = sum(n for (c, _), n in self.counts.items() if c == method)
        return self.counts.get((method, method), 0) / total if total else None


@dataclass(frozen=True)
class ExportData:
    topics: list[str]
    students: list[StudentRow]
    checkpoints: list[CheckpointRow]
    blitz: Matrix
    mix: Matrix


CTYPE_RU = {"choice": "выбор", "expr": "выражение", "transformed_integrand": "интеграл после замены",
            "antiderivative": "ответ", "value": "значение", "method": "метод"}
NOT_ATTEMPT = (*NOT_SPENT, "revealed")       # не тратят попытку


async def _fetch(db: DB, sql: str, *args) -> list[tuple]:
    async with db.conn.execute(sql, args) as cur:
        return await cur.fetchall()


def _rate(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def _students(students, sessions, attempts, runs, topics, today) -> list[StudentRow]:
    rows = []
    for st_id, name in students:
        mine = [a for a in attempts if a["student"] == st_id]
        days = {local_date(a["created_at"]) for a in mine}
        counted = [s for s in sessions if s["student"] == st_id
                   and s["mode"] in TRAIN_MODES and s["status"] in COUNTED_STATUSES]
        counted_ids = {s["id"] for s in counted}
        first: dict[tuple[int, int], tuple[str, str]] = {}          # (сессия, шаг) → (тема, вердикт)
        for a in mine:
            if a["session"] in counted_ids and a["verdict"] not in NOT_SPENT:
                first.setdefault((a["session"], a["idx"]), (a["topic"], a["verdict"]))
        by_topic: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for topic, verdict in first.values():
            by_topic[topic][1] += 1
            by_topic[topic][0] += verdict == "ok"
        ok_total = sum(v[0] for v in by_topic.values())
        my_runs = [r for r in runs if r[0] == st_id]
        best = max(my_runs, key=lambda r: (r[1], -r[3], -r[2]), default=None)   # счёт ↑, время ↓
        rows.append(StudentRow(
            name=name, days_active=len(days), streak=streak(days, today),
            solved=sum(s["status"] == "solved" for s in counted), finished=len(counted),
            first_try=_rate(ok_total, len(first)),
            topic_first_try={t: _rate(*by_topic[t]) if t in by_topic else None for t in topics},
            best_blitz=f"{best[1]}/{best[2]} · {best[3]} с" if best else None, blitz_runs=len(my_runs),
            last_active=max(days) if days else None,
        ))
    return rows


def _checkpoints(attempts, bank) -> list[CheckpointRow]:
    groups: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for a in attempts:
        if a["mode"] in TRAIN_MODES and a["idx"] != METHOD_IDX:
            groups[(a["template_id"], a["idx"])].append(a)
    rows = []
    for (tid, idx), items in sorted(groups.items()):
        tpl = bank.get(tid)
        ctype = tpl.checkpoints[idx].type if tpl and 0 <= idx < len(tpl.checkpoints) else items[0]["ctype"]
        spent = [a for a in items if a["verdict"] not in NOT_ATTEMPT]
        errors = [a for a in items if a["verdict"] in ERRORS]
        per_session: dict[int, list[dict]] = defaultdict(list)
        for a in items:
            per_session[a["session"]].append(a)
        hinted = revealed = 0
        for ss in per_session.values():
            hinted += any(a["hint"] for a in ss)
            n_spent = sum(a["verdict"] not in NOT_ATTEMPT for a in ss)
            has_ok = any(a["verdict"] == "ok" for a in ss)
            revealed += any(a["verdict"] == "revealed" for a in ss) or (n_spent >= MAX_ATTEMPTS and not has_ok)
        wrong = Counter((a["raw"] or "").strip() for a in errors)
        top = sorted(wrong.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_N]
        rows.append(CheckpointRow(
            template_id=tid, topic=items[0]["topic"], difficulty=items[0]["difficulty"], step=idx + 1,
            ctype=ctype, attempts=len(spent), error_rate=_rate(len(errors), len(spent)),
            parse_errors=sum(a["verdict"] == "parse_error" for a in items),
            hint_rate=_rate(hinted, len(per_session)), revealed_rate=_rate(revealed, len(per_session)),
            top_wrong=top,
        ))
    return rows


def _matrix(attempts, bank, pick) -> Matrix:
    counts: Counter = Counter()
    for a in attempts:
        if not pick(a):
            continue
        tpl = bank.get(a["template_id"])
        if not tpl or not tpl.blitz_method:
            continue
        chosen = a["raw"] if a["verdict"] != "skip" and a["raw"] else None
        counts[(tpl.blitz_method, chosen)] += 1
    methods = list(method_options(bank))
    extra = sorted({m for pair in counts for m in pair if m and m not in methods})
    return Matrix(methods + extra, dict(counts))


async def collect(db: DB, bank: dict[str, Template], today: date | None = None) -> ExportData:
    today = today or datetime.now(TZ).date()
    students = sorted(await _fetch(db, "SELECT id, full_name FROM students"), key=lambda r: r[1].casefold())
    sessions = [dict(zip(("id", "student", "mode", "status"), r))
                for r in await _fetch(db, "SELECT id, student_id, mode, status FROM sessions")]
    attempts = [dict(zip(("student", "session", "mode", "status", "template_id", "topic", "difficulty", "idx",
                          "ctype", "raw", "verdict", "hint", "created_at"), r))
                for r in await _fetch(
                    db, "SELECT s.student_id, s.id, s.mode, s.status, i.template_id, i.topic, i.difficulty, "
                        "a.checkpoint_idx, a.checkpoint_type, a.raw_input, a.verdict, a.hint_used, a.created_at "
                        "FROM attempts a JOIN sessions s ON s.id = a.session_id "
                        "JOIN instances i ON i.id = s.instance_id ORDER BY a.id")]
    runs = await _fetch(db, "SELECT student_id, score, total, COALESCE(time_limit, 10) FROM blitz_runs "
                            "WHERE finished_at IS NOT NULL")
    topics = list(TOPIC_NAMES)
    return ExportData(
        topics=topics,
        students=_students(students, sessions, attempts, runs, topics, today),
        checkpoints=_checkpoints(attempts, bank),
        blitz=_matrix(attempts, bank, lambda a: a["mode"] == "blitz"),
        mix=_matrix(attempts, bank, lambda a: a["idx"] == METHOD_IDX and a["verdict"] in ("ok", "wrong")),
    )


# --- XLSX --------------------------------------------------------------------

PCT, DATE = "0%", "DD.MM.YYYY"


def _sheet(wb: Workbook, title: str, header: list[str], rows: list[list], formats: dict[int, str],
           freeze: str = "B2"):
    ws = wb.create_sheet(title)
    ws.append(header)
    for c in ws[1]:
        c.font = Font(bold=True)
        c.alignment = Alignment(wrap_text=True, vertical="top")
    for r in rows:
        ws.append(r)
    for col, fmt in formats.items():
        for row in ws.iter_rows(min_row=2, min_col=col, max_col=col):
            row[0].number_format = fmt
    for i, h in enumerate(header, 1):
        width = max([len(str(h))] + [len(str(r[i - 1])) for r in rows if r[i - 1] is not None])
        ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 8), 60)
    ws.freeze_panes = freeze
    return ws


def _matrix_sheet(wb: Workbook, title: str, m: Matrix, with_skip: bool):
    cols = m.methods + ([None] if with_skip else [])
    header = ["Верный метод \\ выбранный", *m.methods, *(["не успел"] if with_skip else []), "Всего", "% верных"]
    rows = []
    for meth in m.methods:
        cells = [m.counts.get((meth, c)) for c in cols]
        total = sum(n for (c, _), n in m.counts.items() if c == meth)
        rows.append([meth, *cells, total or None, m.accuracy(meth)])
    _sheet(wb, title, header, rows, {len(header): PCT})


def build_xlsx(data: ExportData) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    topic_cols = [topic_title(t) for t in data.topics]
    header = ["ФИО", "Дней активности", "Серия (дней)", "Задач решено", "Задач доведено", "% с 1-й попытки",
              *topic_cols, "Лучший блиц", "Блицев сыграно", "Последняя активность"]
    rows = [[s.name, s.days_active, s.streak, s.solved, s.finished, s.first_try,
             *[s.topic_first_try.get(t) for t in data.topics], s.best_blitz, s.blitz_runs, s.last_active]
            for s in data.students]
    pct_cols = {6: PCT, **{7 + k: PCT for k in range(len(topic_cols))}, len(header): DATE}
    _sheet(wb, SHEETS[0], header, rows, pct_cols)

    header = ["Шаблон", "Тема", "Уровень", "Шаг №", "Тип", "Попыток", "% ошибок", "Опечаток в записи",
              "% брали подсказку", "% показали ответ", "Топ-3 неверных ввода"]
    rows = [[c.template_id, topic_title(c.topic), c.difficulty, c.step, CTYPE_RU.get(c.ctype, c.ctype),
             c.attempts, c.error_rate, c.parse_errors, c.hint_rate, c.revealed_rate,
             " · ".join(f"{t} ({n})" for t, n in c.top_wrong)] for c in data.checkpoints]
    _sheet(wb, SHEETS[1], header, rows, {7: PCT, 9: PCT, 10: PCT})

    _matrix_sheet(wb, SHEETS[2], data.blitz, with_skip=True)
    _matrix_sheet(wb, SHEETS[3], data.mix, with_skip=False)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
