import asyncio
from datetime import datetime
import logging
import os
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.types import (
    InputMediaDocument,
    InputMediaPhoto,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from database import init_db, save_shift, get_shifts_by_date

BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(events_isolation=SimpleEventIsolation())

SAVE_REPORT_BTN = "Сохранить отчёт"
_album_reply_tasks: dict[tuple[int, int, str], asyncio.Task] = {}


zero_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="0")]],
    resize_keyboard=True,
    one_time_keyboard=True
)

none_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Нет / Без происшествий")]],
    resize_keyboard=True,
    one_time_keyboard=True
)

photos_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=SAVE_REPORT_BTN)]],
    resize_keyboard=True
)



class ShiftForm(StatesGroup):
    admin_info = State()
    next_admin_info = State()
    cash_start = State()
    cash_income = State()
    card_income = State()
    sbp_income = State()
    transfers = State()
    pc_ps_topup = State()
    bar_income = State()
    refunds = State()
    cash_end = State()
    bonuses_info = State()
    bonuses_count = State()
    pc_issues = State()
    breakdowns = State()
    conflicts = State()
    bar_ending = State()
    completed_tasks = State()
    photos = State()


class ReportSearch(StatesGroup):
    waiting_for_date = State()


def parse_float(text: str) -> float | None:
    try:
        return float(text.replace(",", ".").strip())
    except ValueError:
        return None


def parse_media_item(item: str) -> tuple[str, str]:
    """Return ('photo' | 'doc', file_id) from a stored media entry."""
    if item.startswith("doc:"):
        return "doc", item[4:]
    if item.startswith("photo:"):
        return "photo", item[6:]
    return "photo", item


async def send_shift_photos(message: types.Message, photos: list[str]) -> None:
    """Send stored shift photos (and image documents) grouped into Telegram media batches."""
    if not photos:
        return

    batches: list[tuple[str, list[str]]] = []
    for raw_item in photos:
        kind, file_id = parse_media_item(raw_item)
        if not batches or batches[-1][0] != kind or len(batches[-1][1]) >= 10:
            batches.append((kind, [file_id]))
        else:
            batches[-1][1].append(file_id)

    try:
        for kind, file_ids in batches:
            if len(file_ids) == 1:
                if kind == "doc":
                    await message.answer_document(file_ids[0])
                else:
                    await message.answer_photo(file_ids[0])
            else:
                media = [
                    InputMediaDocument(media=fid) if kind == "doc" else InputMediaPhoto(media=fid)
                    for fid in file_ids
                ]
                await message.answer_media_group(media)
    except Exception:
        logging.exception("Failed to send shift photos")
        await message.answer("⚠️ Не удалось загрузить некоторые фотографии.")


def format_report_text(data: dict, date_str: str) -> str:
    photos = data.get("photos") or []
    photos_label = f"{len(photos)} шт." if photos else "Нет"
    return (
        f"📊 <b>ОТЧЁТ ЗА СМЕНУ ({date_str})</b>\n\n"
        f"<b>Администратор:</b> {data['admin_info']}\n"
        f"<b>Принял смену:</b> {data['next_admin_info']}\n\n"
        f"<b>1. Финансы:</b>\n"
        f"Касса на начало: {data['cash_start']}\n"
        f"<b>Общая выручка по CLS: {data['cls_total']}</b>\n"
        f"— Наличные: {data['cash_income']}\n"
        f"— Карта (эквайринг): {data['card_income']}\n"
        f"— СБП: {data['sbp_income']}\n"
        f"— Переводы: {data['transfers']}\n\n"
        f"Пополнения ПК / PS: {data['pc_ps_topup']}\n"
        f"Выручка бара: {data['bar_income']}\n"
        f"Возвраты (сумма / кому / причина): {data['refunds']}\n"
        f"Касса на конец: {data['cash_end']}\n\n"
        f"<b>2. Бонусы (сумма / причина):</b> {data['bonuses_info']}\n"
        f"Количество случаев: {data['bonuses_count']}\n\n"
        f"<b>3. События за смену:</b>\n"
        f"Проблемы с ПК / периферией: {data['pc_issues']}\n"
        f"Поломки: {data['breakdowns']}\n"
        f"Конфликты (что произошло / как решили): {data['conflicts']}\n"
        f"Бар (что заканчивается): {data['bar_ending']}\n"
        f"Выполненные задачи: {data['completed_tasks']}\n"
        f"Фотографии: {photos_label}"
    )



@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Это бот учёта смен ПК клуба.\n\n"
        "Доступные команды:\n"
        "/new_shift — Заполнить отчёт о смене\n"
        "/report — Посмотреть отчёты за прошлую дату\n"
        "/cancel — Отменить текущее заполнение"
    )


@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=ReplyKeyboardRemove())



@dp.message(Command("new_shift"))
async def start_shift(message: types.Message, state: FSMContext):
    await state.set_state(ShiftForm.admin_info)
    await message.answer(
        "Начинаем заполнение смены.\n\n"
        "Введите <b>Администратор</b> (имя, ночь/день):",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove()
    )


@dp.message(ShiftForm.admin_info)
async def process_admin(message: types.Message, state: FSMContext):
    await state.update_data(admin_info=message.text)
    await state.set_state(ShiftForm.next_admin_info)
    await message.answer("Введите <b>Принял смену</b> (имя, ночь/день):", parse_mode="HTML")


@dp.message(ShiftForm.next_admin_info)
async def process_next_admin(message: types.Message, state: FSMContext):
    await state.update_data(next_admin_info=message.text)
    await state.set_state(ShiftForm.cash_start)
    await message.answer("1. Финансы.\nВведите <b>Касса на начало</b> (число):", parse_mode="HTML")


@dp.message(ShiftForm.cash_start)
async def process_cash_start(message: types.Message, state: FSMContext):
    val = parse_float(message.text)
    if val is None:
        return await message.answer("Пожалуйста, введите корректное число:")
    await state.update_data(cash_start=val)
    await state.set_state(ShiftForm.cash_income)
    await message.answer("Введите выручку по CLS — <b>Наличные</b>:", parse_mode="HTML", reply_markup=zero_kb)


@dp.message(ShiftForm.cash_income)
async def process_cash_income(message: types.Message, state: FSMContext):
    val = parse_float(message.text)
    if val is None:
        return await message.answer("Пожалуйста, введите корректное число:")
    await state.update_data(cash_income=val)
    await state.set_state(ShiftForm.card_income)
    await message.answer("Введите выручку по CLS — <b>Карта (эквайринг)</b>:", parse_mode="HTML", reply_markup=zero_kb)


@dp.message(ShiftForm.card_income)
async def process_card_income(message: types.Message, state: FSMContext):
    val = parse_float(message.text)
    if val is None:
        return await message.answer("Пожалуйста, введите корректное число:")
    await state.update_data(card_income=val)
    await state.set_state(ShiftForm.sbp_income)
    await message.answer("Введите выручку по CLS — <b>СБП</b>:", parse_mode="HTML", reply_markup=zero_kb)


@dp.message(ShiftForm.sbp_income)
async def process_sbp_income(message: types.Message, state: FSMContext):
    val = parse_float(message.text)
    if val is None:
        return await message.answer("Пожалуйста, введите корректное число:")
    await state.update_data(sbp_income=val)
    await state.set_state(ShiftForm.transfers)
    await message.answer("Введите выручку по CLS — <b>Переводы</b>:", parse_mode="HTML", reply_markup=zero_kb)


@dp.message(ShiftForm.transfers)
async def process_transfers(message: types.Message, state: FSMContext):
    val = parse_float(message.text)
    if val is None:
        return await message.answer("Пожалуйста, введите корректное число:")

    data = await state.get_data()
    cls_total = round(data['cash_income'] + data['card_income'] + data['sbp_income'] + val, 2)
    await state.update_data(transfers=val, cls_total=cls_total)

    await state.set_state(ShiftForm.pc_ps_topup)
    await message.answer(
        f"✅ Общая выручка по CLS рассчитана автоматически: <b>{cls_total}</b>\n\n"
        f"Введите <b>Пополнения ПК / PS</b>:",
        parse_mode="HTML",
        reply_markup=zero_kb
    )


@dp.message(ShiftForm.pc_ps_topup)
async def process_pc_ps(message: types.Message, state: FSMContext):
    val = parse_float(message.text)
    if val is None:
        return await message.answer("Пожалуйста, введите корректное число:")
    await state.update_data(pc_ps_topup=val)
    await state.set_state(ShiftForm.bar_income)
    await message.answer("Введите <b>Выручка бара</b>:", parse_mode="HTML", reply_markup=zero_kb)


@dp.message(ShiftForm.bar_income)
async def process_bar_income(message: types.Message, state: FSMContext):
    val = parse_float(message.text)
    if val is None:
        return await message.answer("Пожалуйста, введите корректное число:")
    await state.update_data(bar_income=val)
    await state.set_state(ShiftForm.refunds)
    await message.answer(
        "Введите <b>Возвраты (сумма / кому / причина)</b>:",
        parse_mode="HTML",
        reply_markup=zero_kb
    )


@dp.message(ShiftForm.refunds)
async def process_refunds(message: types.Message, state: FSMContext):
    await state.update_data(refunds=message.text)
    await state.set_state(ShiftForm.cash_end)
    await message.answer("Введите <b>Касса на конец</b>:", parse_mode="HTML", reply_markup=ReplyKeyboardRemove())


@dp.message(ShiftForm.cash_end)
async def process_cash_end(message: types.Message, state: FSMContext):
    val = parse_float(message.text)
    if val is None:
        return await message.answer("Пожалуйста, введите корректное число:")
    await state.update_data(cash_end=val)
    await state.set_state(ShiftForm.bonuses_info)
    await message.answer(
        "2. Бонусы.\nВведите <b>Бонусы (сумма / причина)</b>:",
        parse_mode="HTML",
        reply_markup=none_kb
    )


@dp.message(ShiftForm.bonuses_info)
async def process_bonuses_info(message: types.Message, state: FSMContext):
    await state.update_data(bonuses_info=message.text)
    await state.set_state(ShiftForm.bonuses_count)
    await message.answer("Введите <b>количество случаев</b> начисления бонусов (число):", parse_mode="HTML",
                         reply_markup=zero_kb)


@dp.message(ShiftForm.bonuses_count)
async def process_bonuses_count(message: types.Message, state: FSMContext):
    if not message.text.strip().isdigit():
        return await message.answer("Пожалуйста, введите целое число (например, 0, 1, 2):")
    await state.update_data(bonuses_count=int(message.text.strip()))
    await state.set_state(ShiftForm.pc_issues)
    await message.answer(
        "3. События за смену.\nВведите <b>Проблемы с ПК / периферией</b>:",
        parse_mode="HTML",
        reply_markup=none_kb
    )


@dp.message(ShiftForm.pc_issues)
async def process_pc_issues(message: types.Message, state: FSMContext):
    await state.update_data(pc_issues=message.text)
    await state.set_state(ShiftForm.breakdowns)
    await message.answer("Введите <b>Поломки</b>:", parse_mode="HTML", reply_markup=none_kb)


@dp.message(ShiftForm.breakdowns)
async def process_breakdowns(message: types.Message, state: FSMContext):
    await state.update_data(breakdowns=message.text)
    await state.set_state(ShiftForm.conflicts)
    await message.answer("Введите <b>Конфликты (что произошло / как решили)</b>:", parse_mode="HTML",
                         reply_markup=none_kb)


@dp.message(ShiftForm.conflicts)
async def process_conflicts(message: types.Message, state: FSMContext):
    await state.update_data(conflicts=message.text)
    await state.set_state(ShiftForm.bar_ending)
    await message.answer("Введите <b>Бар (что заканчивается)</b>:", parse_mode="HTML", reply_markup=none_kb)


@dp.message(ShiftForm.bar_ending)
async def process_bar_ending(message: types.Message, state: FSMContext):
    await state.update_data(bar_ending=message.text)
    await state.set_state(ShiftForm.completed_tasks)
    await message.answer("Введите <b>Выполненные задачи</b>:", parse_mode="HTML", reply_markup=none_kb)


async def _notify_album_added(
    message: types.Message,
    state: FSMContext,
    key: tuple[int, int, str],
) -> None:
    try:
        await asyncio.sleep(0.6)
        if await state.get_state() != ShiftForm.photos.state:
            return
        data = await state.get_data()
        count = len(data.get("photos") or [])
        await message.answer(
            f"📸 Фотографии добавлены (всего: {count}). "
            f"Отправьте ещё или нажмите <b>«{SAVE_REPORT_BTN}»</b>:",
            parse_mode="HTML",
            reply_markup=photos_kb,
        )
    except asyncio.CancelledError:
        return
    finally:
        if _album_reply_tasks.get(key) is asyncio.current_task():
            _album_reply_tasks.pop(key, None)


async def _append_media_and_reply(
    message: types.Message,
    state: FSMContext,
    media_item: str,
) -> None:
    data = await state.get_data()
    photos = list(data.get("photos") or [])
    photos.append(media_item)
    await state.update_data(photos=photos)

    if message.media_group_id:
        user_id = message.from_user.id if message.from_user else 0
        key = (message.chat.id, user_id, message.media_group_id)
        prev_task = _album_reply_tasks.pop(key, None)
        if prev_task and not prev_task.done():
            prev_task.cancel()
        _album_reply_tasks[key] = asyncio.create_task(
            _notify_album_added(message, state, key)
        )
        return

    await message.answer(
        f"📸 Фото добавлено (всего: {len(photos)}). "
        f"Отправьте ещё или нажмите <b>«{SAVE_REPORT_BTN}»</b>:",
        parse_mode="HTML",
        reply_markup=photos_kb,
    )


@dp.message(ShiftForm.completed_tasks)
async def process_completed_tasks(message: types.Message, state: FSMContext):
    if not message.text:
        return await message.answer("Пожалуйста, введите текстовый ответ (выполненные задачи):")
    await state.update_data(completed_tasks=message.text, photos=[])
    await state.set_state(ShiftForm.photos)
    await message.answer(
        f"📸 Загрузите <b>фотографии</b> за смену (можно отправить одну или несколько).\n"
        f"Когда закончите (или если фото не требуются), нажмите <b>«{SAVE_REPORT_BTN}»</b>:",
        parse_mode="HTML",
        reply_markup=photos_kb
    )


@dp.message(ShiftForm.photos, F.text == SAVE_REPORT_BTN)
async def process_photos_done(message: types.Message, state: FSMContext):
    data = await state.get_data()
    await save_shift(data)

    today_str = datetime.now().strftime("%d.%m.%Y")
    report = format_report_text(data, today_str)

    await state.clear()
    await message.answer(
        "✅ <b>Смена успешно закрыта и сохранена в базу!</b>\n\n" + report,
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


@dp.message(ShiftForm.photos, F.photo)
async def process_shift_photo(message: types.Message, state: FSMContext):
    await _append_media_and_reply(message, state, f"photo:{message.photo[-1].file_id}")


@dp.message(ShiftForm.photos, F.document, F.document.mime_type.startswith("image/"))
async def process_shift_photo_document(message: types.Message, state: FSMContext):
    await _append_media_and_reply(message, state, f"doc:{message.document.file_id}")


@dp.message(ShiftForm.photos)
async def process_photos_invalid(message: types.Message):
    await message.answer(
        f"Пожалуйста, отправьте фотографию или нажмите кнопку <b>«{SAVE_REPORT_BTN}»</b>.",
        parse_mode="HTML",
        reply_markup=photos_kb,
    )


@dp.message(Command("report"))
async def cmd_report(message: types.Message, state: FSMContext):
    await state.set_state(ReportSearch.waiting_for_date)
    await message.answer(
        "Введите дату, за которую хотите посмотреть отчёты, в формате <b>ДД.ММ.ГГГГ</b> (например, <code>13.11.2026</code>):",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove()
    )


@dp.message(ReportSearch.waiting_for_date)
async def process_report_date(message: types.Message, state: FSMContext):
    date_text = message.text.strip()
    try:
        rows = await get_shifts_by_date(date_text)
    except ValueError:
        return await message.answer("Неверный формат даты. Введите в формате <b>ДД.ММ.ГГГГ</b>:", parse_mode="HTML")

    await state.clear()

    if not rows:
        return await message.answer(f"За дату <b>{date_text}</b> отчётов не найдено.", parse_mode="HTML")

    for row in rows:
        row_dict = dict(row)
        report = format_report_text(row_dict, date_text)
        await message.answer(report, parse_mode="HTML")
        photos = row_dict.get("photos") or []
        if photos:
            await send_shift_photos(message, photos)


async def health_check(request):
    return web.Response(text="Bot is running!")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()


async def main():
    await init_db()
    await start_web_server()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())