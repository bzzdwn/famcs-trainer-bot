"""Меню команд Telegram: своё для гостя, студента и преподавателя (BotCommandScopeChat)."""
from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

GUEST = [
    BotCommand(command="start", description="Регистрация"),
    BotCommand(command="help", description="Как вводить ответ"),
]
STUDENT = [
    BotCommand(command="train", description="Тренировка"),
    BotCommand(command="blitz", description="Блиц: 10 задач на выбор метода"),
    BotCommand(command="me", description="Моя статистика"),
    BotCommand(command="stop", description="Выйти из задачи или блица"),
    BotCommand(command="help", description="Как вводить ответ"),
]
ADMIN_EXTRA = [
    BotCommand(command="students", description="Список студентов"),
    BotCommand(command="rename", description="Переименовать: /rename id Фамилия Имя"),
    BotCommand(command="kick", description="Удалить: /kick id"),
    BotCommand(command="broadcast", description="Сообщение всем студентам: /broadcast текст"),
    BotCommand(command="export", description="Отчёт XLSX: студенты, шаги, путаница методов"),
    BotCommand(command="reload", description="Перечитать шаблоны"),
    BotCommand(command="ocrdump", description="Архив распознаваний фото"),
]


def commands_for(registered: bool, admin: bool) -> list[BotCommand]:
    if not registered:
        return GUEST + (ADMIN_EXTRA if admin else [])
    return STUDENT + (ADMIN_EXTRA if admin else [])


async def set_default(bot: Bot) -> None:
    """Меню по умолчанию — для тех, кто ещё не зарегистрирован."""
    await bot.set_my_commands(GUEST, scope=BotCommandScopeDefault())


async def set_for_chat(bot: Bot, chat_id: int, registered: bool, admin: bool) -> None:
    await bot.set_my_commands(commands_for(registered, admin), scope=BotCommandScopeChat(chat_id=chat_id))


async def reset_chat(bot: Bot, chat_id: int) -> None:
    """Вернуть чату меню по умолчанию (после /kick)."""
    await bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=chat_id))
