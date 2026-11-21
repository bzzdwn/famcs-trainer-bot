"""/ocrdump: zip с фото и таблицей распознаваний — материал для доводки конвертера и тестов на почерке."""
import csv
import io
import zipfile

from bot.db import OcrRow

CSV_NAME = "ocr.csv"
COLUMNS = ["id", "created_at", "student", "status", "markdown", "recognized",
           "final_input", "edited", "verdict", "photo"]


def build_ocr_dump(rows: list[OcrRow], photos: dict[int, bytes | None]) -> bytes:
    """photos: id записи → JPEG (None — не скачалось). В zip: photos/<id>.jpg и ocr.csv (UTF-8 с BOM для Excel)."""
    buf = io.BytesIO()
    table = io.StringIO()
    w = csv.DictWriter(table, fieldnames=COLUMNS)
    w.writeheader()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for r in rows:
            photo = photos.get(r.id)
            name = f"photos/{r.id}.jpg"
            if photo:
                z.writestr(name, photo)
            w.writerow({
                "id": r.id, "created_at": r.created_at, "student": r.student or "", "status": r.status,
                "markdown": r.markdown or "", "recognized": r.recognized or "", "final_input": r.final_input or "",
                "edited": "" if r.edited is None else int(r.edited), "verdict": r.verdict or "",
                "photo": name if photo else "не скачалось",
            })
        z.writestr(CSV_NAME, "﻿" + table.getvalue())
    return buf.getvalue()
