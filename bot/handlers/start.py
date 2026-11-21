"""/start: регистрация по коду группы, /help: шпаргалка по синтаксису."""
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from bot.config import Settings
from bot.db import DB, DuplicateStudent
from bot.registration import InvalidName, check_group_code, normalize_name

router = Router()

CHEATSHEET = (
    "<b>Как вводить ответ</b>\n"
    "<code>x^2</code> — степень, <code>x^(1/3)</code> — корень третьей степени\n"
    "<code>sqrt(x)</code> или <code>√x</code> — квадратный корень\n"
    "<code>sin x</code>, <code>tg x</code>, <code>ctg x</code>, <code>arctg x</code>, <code>arcsin x</code>\n"
    "<code>ln|x|</code> — логарифм (модуль — палочками), <code>e^x</code>, <code>pi</code>, <code>oo</code>\n"
    "<code>2x</code>, <code>x(x+1)</code> — умножение можно не писать\n"
    "<code>sin^2 x</code> = (sin x)², аргумент из нескольких символов — в скобках: <code>sin(2x+1)</code>\n\n"
    "⚠️ В первообразной обязательно <code>+ C</code> в конце.\n"
    "Перед проверкой бот покажет, как понял запись, — проверь картинку.\n\n"
    "/train — тренировка, /blitz — блиц, /me — статистика\n/stop — выйти из задачи, /help — эта шпаргалка"
)


class Reg(StatesGroup):
    code = State()
    name = State()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, db: DB):
    st = await db.get_student_by_tg(message.from_user.id)
    if st:
        await state.clear()
        await message.answer(f"Привет, {escape(st.full_name)}! /train — тренировка, /help — синтаксис.")
        return
    await state.set_state(Reg.code)
    await message.answer("Привет! Это тренажёр по матанализу.\nВведи код группы (его называли на паре):")


@router.message(Reg.code, F.text)
async def reg_code(message: Message, state: FSMContext, settings: Settings):
    if not check_group_code(message.text, settings.group_code):
        await message.answer("Код не подошёл. Попробуй ещё раз:")
        return
    await state.set_state(Reg.name)
    await message.answer("Отлично. Теперь фамилия и имя, например: <i>Иванов Иван</i>")


@router.message(Reg.name, F.text)
async def reg_name(message: Message, state: FSMContext, db: DB):
    try:
        name = normalize_name(message.text)
    except InvalidName as e:
        await message.answer(escape(str(e)))
        return
    try:
        await db.add_student(name, message.from_user.id)
    except DuplicateStudent:
        await message.answer("Такое имя уже зарегистрировано. Если это ошибка — напиши преподавателю.")
        await state.clear()
        return
    await state.clear()
    await message.answer(f"Готово, {escape(name)}! 🎉")
    await message.answer(CHEATSHEET)


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(CHEATSHEET)
