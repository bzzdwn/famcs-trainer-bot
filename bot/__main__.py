"""Запуск: venv/bin/python -m bot"""
import asyncio
import logging
import signal
from html import escape

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from bot import commands
from bot.bank import reload_bank
from bot.config import load_settings
from bot.db import DB
from bot.ocr import YandexOcr
from bot.handlers import admin, blitz, me, start, train
from core.checker import check_with_timeout, shutdown_pool
from core.templates import load_templates



async def reload_on_signal(bot: Bot, bank: dict, settings) -> None:
    """SIGHUP (deploy.sh --templates): перечитать шаблоны без рестарта и сообщить админу."""
    ok, msg = await asyncio.to_thread(reload_bank, bank, settings.templates_dir)
    logging.info("SIGHUP: перезагрузка шаблонов: %s %s", ok, msg)
    try:
        await bot.send_message(settings.admin_tg_id,
                               ("🔄 Шаблоны обновлены. " if ok else "❌ Шаблоны не обновлены, банк прежний.\n")
                               + escape(msg))
    except Exception as exc:
        logging.warning("не смог сообщить админу о перезагрузке: %s", exc)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    bank = load_templates(settings.templates_dir)
    db = await DB.open(settings.db_path)
    abandoned = await db.abandon_unfinished()
    logging.info("шаблонов: %d, закрыто брошенных сессий: %d", len(bank), abandoned)
    await asyncio.to_thread(check_with_timeout, "expr", "x", "x")   # прогрев пула проверки

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    ocr = YandexOcr(settings.yandex_api_key, settings.yandex_folder_id) \
        if settings.yandex_api_key and settings.yandex_folder_id else None
    logging.info("фото-ввод: %s", "включён" if ocr else "выключен (нет YANDEX_API_KEY / YANDEX_FOLDER_ID)")
    dp = Dispatcher(storage=MemoryStorage(), db=db, bank=bank, settings=settings, ocr=ocr)
    dp.include_routers(admin.make_router(settings.admin_tg_id), start.router, blitz.router, me.router,
                       train.router)   # train последним: в нём перехват любого текста
    await commands.set_default(bot)                     # гости: /start, /help
    await commands.set_for_chat(bot, settings.admin_tg_id,
                                registered=await db.get_student_by_tg(settings.admin_tg_id) is not None,
                                admin=True)
    try:
        asyncio.get_running_loop().add_signal_handler(
            signal.SIGHUP, lambda: asyncio.create_task(reload_on_signal(bot, bank, settings)))
    except (NotImplementedError, AttributeError):    # Windows: сигналов нет — только /reload
        pass
    try:
        await dp.start_polling(bot)
    finally:
        await db.close()
        await asyncio.to_thread(shutdown_pool)


if __name__ == "__main__":
    asyncio.run(main())
