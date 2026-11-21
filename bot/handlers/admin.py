"""Команды преподавателя: /students, /rename, /kick, /broadcast, /reload, /export, /ocrdump. Доступны только ADMIN_TG_ID."""
import asyncio
import logging
from datetime import datetime
from html import escape
from io import BytesIO

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import commands
from bot.bank import reload_bank
from bot.broadcast import BroadcastError, broadcast, recipients, validate_text
from bot.config import Settings
from bot.db import DB, DuplicateStudent
from bot.export import build_xlsx, collect
from bot.ocrdump import build_ocr_dump
from bot.waiting import waiting
from bot.registration import InvalidName, normalize_name


def make_router(admin_tg_id: int) -> Router:
    router = Router()
    router.message.filter(F.from_user.id == admin_tg_id)
    router.callback_query.filter(F.from_user.id == admin_tg_id)
    pending: dict[str, str] = {}                     # текст рассылки, ожидающий подтверждения

    @router.message(Command("broadcast"))
    async def cmd_broadcast(message: Message, command: CommandObject, db: DB):
        try:
            text = validate_text(command.args or "")
        except BroadcastError as e:
            await message.answer(escape(str(e)))
            return
        n = len(recipients(await db.list_students(), admin_tg_id))
        if n == 0:
            await message.answer("Некому отправлять: студентов пока нет.")
            return
        pending["text"] = text
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=f"Отправить всем ({n})", callback_data="bc:send"),
            InlineKeyboardButton(text="Отмена", callback_data="bc:cancel")]])
        await message.answer(f"Разослать это всем студентам ({n})?\n\n{escape(text)}", reply_markup=kb)

    @router.callback_query(F.data.startswith("bc:"))
    async def cb_broadcast(cb: CallbackQuery, db: DB):
        await cb.answer()
        await cb.message.edit_reply_markup(reply_markup=None)
        text = pending.pop("text", None)
        if cb.data == "bc:cancel" or text is None:
            await cb.message.answer("Рассылка отменена." if text else "Это превью уже неактуально.")
            return
        async with waiting(cb.message, "Рассылаю…"):
            rep = await broadcast(recipients(await db.list_students(), admin_tg_id), text,
                                  lambda tg_id, t: cb.bot.send_message(tg_id, t, parse_mode=None))
        msg = f"Доставлено: {rep.delivered}."
        if rep.failed:
            msg += f"\nНе доставлено ({len(rep.failed)}): " + escape(", ".join(rep.failed))
        await cb.message.answer(msg)

    @router.message(Command("students"))
    async def cmd_students(message: Message, db: DB):
        students = await db.list_students()
        if not students:
            await message.answer("Пока никто не зарегистрировался.")
            return
        lines = [f"{s.id}. {escape(s.full_name)}" for s in students]
        await message.answer(f"<b>Студенты ({len(students)})</b>\n" + "\n".join(lines))

    @router.message(Command("rename"))
    async def cmd_rename(message: Message, command: CommandObject, db: DB):
        args = (command.args or "").split(maxsplit=1)
        if len(args) != 2 or not args[0].isdigit():
            await message.answer("Формат: /rename &lt;id&gt; Фамилия Имя")
            return
        try:
            name = normalize_name(args[1])
            ok = await db.rename_student(int(args[0]), name)
        except InvalidName as e:
            await message.answer(escape(str(e)))
            return
        except DuplicateStudent:
            await message.answer("Это имя уже занято.")
            return
        await message.answer(f"Переименован: {escape(name)}" if ok else "Нет студента с таким id.")

    @router.message(Command("kick"))
    async def cmd_kick(message: Message, command: CommandObject, db: DB):
        arg = (command.args or "").strip()
        if not arg.isdigit():
            await message.answer("Формат: /kick &lt;id&gt; (id — из /students)")
            return
        target = next((s for s in await db.list_students() if s.id == int(arg)), None)
        ok = await db.delete_student(int(arg))
        if ok and target and target.tg_id != admin_tg_id:
            try:
                await commands.reset_chat(message.bot, target.tg_id)   # удалённому — снова меню гостя
            except Exception:
                pass
        await message.answer("Удалён." if ok else "Нет студента с таким id.")

    @router.message(Command("reload"))
    async def cmd_reload(message: Message, bank: dict, settings: Settings):
        await message.answer("Перечитываю шаблоны…")
        ok, msg = await asyncio.to_thread(reload_bank, bank, settings.templates_dir)
        await message.answer(("✅ " if ok else "❌ Банк не изменён.\n") + escape(msg))

    @router.message(Command("export"))
    async def cmd_export(message: Message, db: DB, bank: dict):
        async with waiting(message, "Собираю отчёт…"):
            data = await collect(db, bank)
            xlsx = await asyncio.to_thread(build_xlsx, data)
        await message.answer_document(
            BufferedInputFile(xlsx, f"export_{datetime.now():%Y%m%d_%H%M}.xlsx"),
            caption=f"Студентов: {len(data.students)}. Листы: Студенты, Шаблоны, Блиц, Вразнобой")

    @router.message(Command("ocrdump"))
    async def cmd_ocrdump(message: Message, command: CommandObject, db: DB):
        arg = (command.args or "").strip()
        limit = int(arg) if arg.isdigit() else 200
        rows = await db.ocr_dump_rows(limit)
        if not rows:
            await message.answer("Распознаваний пока нет.")
            return
        photos: dict[int, bytes | None] = {}
        async with waiting(message, f"Собираю архив ({len(rows)} фото)…"):
            for r in rows:
                buf = BytesIO()
                try:
                    await message.bot.download(r.file_id, destination=buf)
                    photos[r.id] = buf.getvalue()
                except Exception as exc:
                    logging.warning("ocrdump: фото %s не скачалось: %s", r.id, exc)
                    photos[r.id] = None
            data = await asyncio.to_thread(build_ocr_dump, rows, photos)
        name = f"ocrdump_{datetime.now():%Y%m%d_%H%M}.zip"
        ok = sum(p is not None for p in photos.values())
        await message.answer_document(BufferedInputFile(data, name),
                                      caption=f"Распознаваний: {len(rows)}, фото скачано: {ok}")

    return router
