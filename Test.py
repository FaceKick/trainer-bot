import asyncio
import logging
import sqlite3
import re
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
import subprocess
import os

# --- 1. БАЗА ДАННЫХ И МИГРАЦИИ ---
conn = sqlite3.connect("clients.db")
cursor = conn.cursor()

cursor.execute("""
    CREATE TABLE IF NOT EXISTS clients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT
    )
""")
cursor.execute("""
    CREATE TABLE IF NOT EXISTS splits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        client_id INTEGER,
        name TEXT
    )
""")

cursor.execute("PRAGMA table_info(exercises)")
columns = [col[1] for col in cursor.fetchall()]

if not columns:
    cursor.execute("""
        CREATE TABLE exercises (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            split_id INTEGER,
            name TEXT,
            details TEXT,
            order_num INTEGER DEFAULT 1
        )
    """)
else:
    if "split_id" not in columns:
        cursor.execute("SELECT DISTINCT client_id, split_name FROM exercises")
        old_splits = cursor.fetchall()
        split_map = {}
        for c_id, s_name in old_splits:
            name_to_use = s_name if s_name else "Основний спліт"
            cursor.execute("INSERT INTO splits (client_id, name) VALUES (?, ?)", (c_id, name_to_use))
            split_map[(c_id, s_name)] = cursor.lastrowid

        cursor.execute("""
            CREATE TABLE exercises_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                split_id INTEGER,
                name TEXT,
                details TEXT,
                order_num INTEGER DEFAULT 1
            )
        """)
        cursor.execute("SELECT id, client_id, split_name, name, details FROM exercises")
        old_exercises = cursor.fetchall()
        for ex_id, c_id, s_name, ex_name, ex_details in old_exercises:
            new_split_id = split_map.get((c_id, s_name))
            cursor.execute(
                "INSERT INTO exercises_new (id, split_id, name, details, order_num) VALUES (?, ?, ?, ?, 1)",
                (ex_id, new_split_id, ex_name, ex_details)
            )
        cursor.execute("DROP TABLE exercises")
        cursor.execute("ALTER TABLE exercises_new RENAME TO exercises")

    cursor.execute("PRAGMA table_info(exercises)")
    current_cols = [col[1] for col in cursor.fetchall()]
    if "order_num" not in current_cols:
        cursor.execute("ALTER TABLE exercises ADD COLUMN order_num INTEGER DEFAULT 1")
        cursor.execute("SELECT id, split_id FROM exercises ORDER BY id ASC")
        rows = cursor.fetchall()
        counters = {}
        for row_id, s_id in rows:
            counters[s_id] = counters.get(s_id, 0) + 1
            cursor.execute("UPDATE exercises SET order_num = ? WHERE id = ?", (counters[s_id], row_id))

conn.commit()

# --- 2. НАСТРОЙКА БОТА ---
BOT_TOKEN = "9256637883:AAHGIGHweeAlKhpKCUSmJeC7TtM7Kyfp1mQ"
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()

ADMIN_ID = 524827756

# --- 3. МАШИНЫ СОСТОЯНИЙ (FSM) ---
class AddClientFlow(StatesGroup):
    waiting_for_name = State()

class AddSplitFlow(StatesGroup):
    waiting_for_split_name = State()
    waiting_for_exercises = State()

class EditWeight(StatesGroup):
    waiting_for_new_weight = State()
    exercise_id = State()

class EditSplitName(StatesGroup):
    waiting_for_new_name = State()
    split_id = State()

class InsertExercise(StatesGroup):
    waiting_for_position = State()
    waiting_for_data = State()
    split_id = State()

class DeleteExercise(StatesGroup):
    waiting_for_number = State()
    split_id = State()

# --- 4. МЕХАНИЗМ АВТООЧИСТКИ СООБЩЕНИЙ ---
async def save_msg(state: FSMContext, message_id: int):
    """Сохраняет ID сообщений цепочки ввода для последующего удаления."""
    data = await state.get_data()
    msg_ids = data.get("messages_to_delete", [])
    msg_ids.append(message_id)
    await state.update_data(messages_to_delete=msg_ids)

async def clear_old_messages(bot: Bot, chat_id: int, state: FSMContext):
    """Удаляет все служебные сообщения и ответы ввода пользователя."""
    data = await state.get_data()
    msg_ids = data.get("messages_to_delete", [])
    for msg_id in msg_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass
    await state.update_data(messages_to_delete=[])

def get_cancel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад / Скасувати", callback_data="cancel_action")]
    ])

# --- 5. ОБРАБОТЧИКИ КОМАНД ---

@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Привіт! Я бот для управління тренуваннями.\n\n"
        "Доступні команди:\n"
        "/add - Додати нового клієнта\n"
        "/clients - Переглянути клієнтів та їх спліти"
    )

@router.message(Command("myid"))
async def cmd_myid(message: Message):
    await message.answer(f"Твій ID: <code>{message.from_user.id}</code>", parse_mode="HTML")

@router.message(Command("update"))
async def cmd_update_bot(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У вас немає прав для цієї команди.")
        return

    await message.answer("🔄 Перевіряю оновлення на GitHub...")
    try:
        result = subprocess.run(["git", "pull"], capture_output=True, text=True, check=True)
        output = result.stdout.strip()

        if "Already up to date." in output:
            await message.answer("✅ Бот вже працює на найновішій версії.")
        else:
            await message.answer(f"📥 Код оновлено:\n<pre>{output}</pre>\n🚀 Перезапускаюсь...", parse_mode="HTML")
            os._exit(0)
    except Exception as e:
        await message.answer(f"⚠️ Помилка оновлення:\n<pre>{e}</pre>", parse_mode="HTML")

@router.callback_query(F.data == "cancel_action")
async def process_cancel(callback: CallbackQuery, state: FSMContext):
    await clear_old_messages(bot, callback.message.chat.id, state)
    await state.clear()
    await callback.message.edit_text("❌ Дію скасовано.\n\nСкористайтеся /clients або /add")
    await callback.answer()

# --- ДОБАВЛЕНИЕ КЛИЕНТА ---
@router.message(Command("add"))
async def cmd_add_client(message: Message, state: FSMContext):
    await clear_old_messages(bot, message.chat.id, state)
    await state.clear()
    
    sent_msg = await message.answer(
        "Введіть ім'я клієнта (тільки букви, наприклад: <i>Влад</i>):",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    await save_msg(state, message.message_id)
    await save_msg(state, sent_msg.message_id)
    await state.set_state(AddClientFlow.waiting_for_name)

@router.message(AddClientFlow.waiting_for_name)
async def process_client_name(message: Message, state: FSMContext):
    await save_msg(state, message.message_id)
    name = message.text.strip()
    
    if not re.match(r"^[a-zA-Zа-яА-ЯёЁіІїЇєЄґҐ\s\-]+$", name) or len(name) < 2:
        err_msg = await message.answer(
            "⚠️ <b>Некоректне ім'я!</b> Тільки букви без спецсимволів. Спробуйте ще раз:",
            reply_markup=get_cancel_keyboard(),
            parse_mode="HTML"
        )
        await save_msg(state, err_msg.message_id)
        return

    cursor.execute("INSERT INTO clients (name) VALUES (?)", (name,))
    client_id = cursor.lastrowid
    conn.commit()

    await clear_old_messages(bot, message.chat.id, state)
    
    sent_msg = await message.answer(
        f"✅ Клієнт <b>{name}</b> доданий!\n\nТепер введіть назву першого спліту (наприклад: <i>Верх / День 1</i>):",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    await state.update_data(client_id=client_id, client_name=name)
    await save_msg(state, sent_msg.message_id)
    await state.set_state(AddSplitFlow.waiting_for_split_name)

@router.message(AddSplitFlow.waiting_for_split_name)
async def process_split_name(message: Message, state: FSMContext):
    await save_msg(state, message.message_id)
    split_name = message.text.strip()
    await state.update_data(split_name=split_name)
    
    await clear_old_messages(bot, message.chat.id, state)

    sent_msg = await message.answer(
        "Відправте список вправ (назва та підходи через двокрапку).\nПриклад:\nЖим лежачи: 60/10, 70/8\nРозведення: 14/12",
        reply_markup=get_cancel_keyboard()
    )
    await save_msg(state, sent_msg.message_id)
    await state.set_state(AddSplitFlow.waiting_for_exercises)

@router.message(AddSplitFlow.waiting_for_exercises)
async def process_split_exercises(message: Message, state: FSMContext):
    await save_msg(state, message.message_id)
    data = await state.get_data()
    client_id = data['client_id']
    split_name = data['split_name']
    
    cursor.execute("INSERT INTO splits (client_id, name) VALUES (?, ?)", (client_id, split_name))
    split_id = cursor.lastrowid
    
    lines = message.text.split('\n')
    added_count = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            parts = line.split(':', 1)
            added_count += 1
            cursor.execute(
                "INSERT INTO exercises (split_id, name, details, order_num) VALUES (?, ?, ?, ?)",
                (split_id, parts[0].strip(), parts[1].strip(), added_count)
            )
        except Exception:
            pass
            
    conn.commit()
    await clear_old_messages(bot, message.chat.id, state)
    await state.clear()
    
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Відкрити спліт", callback_data=f"split_{split_id}")
    builder.button(text="🏋️ До клієнта", callback_data=f"client_{client_id}")
    builder.adjust(1)

    await message.answer(
        f"✅ Спліт <b>{split_name}</b> успішно збережено!\nДодано вправ: {added_count}",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

# --- ПРОСМОТР КЛИЕНТОВ ---
@router.message(Command("clients"))
async def cmd_show_clients(message: Message, state: FSMContext):
    await clear_old_messages(bot, message.chat.id, state)
    await state.clear()
    try:
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    except Exception:
        pass

    cursor.execute("SELECT id, name FROM clients")
    clients = cursor.fetchall()
    
    if not clients:
        await message.answer("Список порожній. Додайте клієнта через /add")
        return
        
    builder = InlineKeyboardBuilder()
    for client_id, client_name in clients:
        builder.button(text=client_name, callback_data=f"client_{client_id}")
    builder.adjust(1)
    
    await message.answer("🏋️ Оберіть клієнта:", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("client_"))
async def process_client_selection(callback: CallbackQuery, state: FSMContext):
    await clear_old_messages(bot, callback.message.chat.id, state)
    await state.clear()

    client_id = callback.data.split("_")[1]
    
    cursor.execute("SELECT name FROM clients WHERE id = ?", (client_id,))
    client_row = cursor.fetchone()
    if not client_row:
        await callback.message.edit_text("⚠️ Клієнта не знайдено.")
        await callback.answer()
        return
    client_name = client_row[0]
    
    cursor.execute("SELECT id, name FROM splits WHERE client_id = ?", (client_id,))
    splits = cursor.fetchall()
    
    builder = InlineKeyboardBuilder()
    text = f"🏋️ <b>Клієнт:</b> {client_name}\n\nОберіть тренувальний день:"
    
    for split_id, split_name in splits:
        builder.button(text=f"📋 {split_name}", callback_data=f"split_{split_id}")
        
    builder.button(text="➕ Додати новий спліт", callback_data=f"add_split_{client_id}")
    builder.button(text="🗑️ Видалити клієнта", callback_data=f"delete_client_{client_id}")
    builder.button(text="🔙 Назад до списку", callback_data="back_to_clients")
    builder.adjust(1)
    
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("split_"))
async def process_split_selection(callback: CallbackQuery, state: FSMContext):
    await clear_old_messages(bot, callback.message.chat.id, state)
    await state.clear()

    split_id = callback.data.split("_")[1]
    
    cursor.execute("SELECT name, client_id FROM splits WHERE id = ?", (split_id,))
    split_row = cursor.fetchone()
    if not split_row:
        await callback.message.edit_text("⚠️ Спліт не знайдено.")
        await callback.answer()
        return
    split_name, client_id = split_row
    
    cursor.execute("SELECT id, name, details, order_num FROM exercises WHERE split_id = ? ORDER BY order_num ASC", (split_id,))
    exercises = cursor.fetchall()
    
    text = f"📋 <b>Спліт:</b> {split_name}\n\n"
    builder = InlineKeyboardBuilder()
    
    if exercises:
        for ex_id, ex_name, ex_details, order_num in exercises:
            text += f"{order_num}. <b>{ex_name}</b>: {ex_details}\n"
            short_name = ex_name[:15] + "..." if len(ex_name) > 15 else ex_name
            builder.button(text=f"📝 {order_num}. {short_name}", callback_data=f"edit_weight_{ex_id}")
    else:
        text += "<i>У цьому спліті поки немає вправ.</i>\n"
        
    builder.button(text="➕ Додати вправу по номеру", callback_data=f"ins_ex_{split_id}")
    if exercises:
        builder.button(text="🗑️ Видалити вправу по номеру", callback_data=f"del_ex_{split_id}")
    builder.button(text="✏️ Перейменувати спліт", callback_data=f"rename_split_{split_id}")
    builder.button(text="🗑️ Видалити цей спліт", callback_data=f"delete_split_{split_id}")
    builder.button(text="🔙 Назад до клієнта", callback_data=f"client_{client_id}")
    builder.adjust(1)
    
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()

# --- ДОБАВЛЕНИЕ УПРАЖНЕНИЯ ПО НОМЕРУ (ПОЗИЦИИ) ---
@router.callback_query(F.data.startswith("ins_ex_"))
async def process_insert_exercise_btn(callback: CallbackQuery, state: FSMContext):
    split_id = callback.data.split("_")[2]
    await state.update_data(split_id=split_id)
    
    cursor.execute("SELECT COUNT(*) FROM exercises WHERE split_id = ?", (split_id,))
    count = cursor.fetchone()[0]
    next_pos = count + 1
    
    sent_msg = await callback.message.answer(
        f"Введіть <b>номер позиції</b>, куди вставити вправу (від 1 до {next_pos}):\n"
        f"<i>(Якщо в кінець списку — введіть {next_pos})</i>",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    await save_msg(state, sent_msg.message_id)
    await state.set_state(InsertExercise.waiting_for_position)
    await callback.answer()

@router.message(InsertExercise.waiting_for_position)
async def process_position_step(message: Message, state: FSMContext):
    await save_msg(state, message.message_id)
    data = await state.get_data()
    split_id = data['split_id']
    
    cursor.execute("SELECT COUNT(*) FROM exercises WHERE split_id = ?", (split_id,))
    count = cursor.fetchone()[0]
    
    try:
        pos = int(message.text.strip())
        if pos < 1 or pos > count + 1:
            raise ValueError
    except ValueError:
        err_msg = await message.answer(f"⚠️ Введіть коректне число від 1 до {count + 1}:", reply_markup=get_cancel_keyboard())
        await save_msg(state, err_msg.message_id)
        return

    await state.update_data(target_pos=pos)
    await clear_old_messages(bot, message.chat.id, state)
    
    sent_msg = await message.answer(
        f"Введіть вправу для позиції №{pos} у форматі:\n<b>Назва: підходи/вага</b>\n\nПриклад:\nПідтягування: 4х10, +10кг/6",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    await save_msg(state, sent_msg.message_id)
    await state.set_state(InsertExercise.waiting_for_data)

@router.message(InsertExercise.waiting_for_data)
async def process_exercise_data_save(message: Message, state: FSMContext):
    await save_msg(state, message.message_id)
    data = await state.get_data()
    split_id = data['split_id']
    target_pos = data['target_pos']
    
    text = message.text.strip()
    if ":" not in text:
        err_msg = await message.answer("⚠️ Розділіть назву і вагу двокрапкою (наприклад: <i>Тяга: 50/10</i>):", reply_markup=get_cancel_keyboard(), parse_mode="HTML")
        await save_msg(state, err_msg.message_id)
        return
        
    parts = text.split(":", 1)
    ex_name = parts[0].strip()
    ex_details = parts[1].strip()

    cursor.execute("UPDATE exercises SET order_num = order_num + 1 WHERE split_id = ? AND order_num >= ?", (split_id, target_pos))
    cursor.execute("INSERT INTO exercises (split_id, name, details, order_num) VALUES (?, ?, ?, ?)", (split_id, ex_name, ex_details, target_pos))
    conn.commit()

    await clear_old_messages(bot, message.chat.id, state)
    await state.clear()
    
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Повернутися до спліту", callback_data=f"split_{split_id}")
    builder.adjust(1)
    
    await message.answer(f"✅ Вправу <b>{ex_name}</b> додано під номером {target_pos}!", reply_markup=builder.as_markup(), parse_mode="HTML")

# --- УДАЛЕНИЕ УПРАЖНЕНИЯ ПО НОМЕРУ ---
@router.callback_query(F.data.startswith("del_ex_"))
async def process_del_exercise_btn(callback: CallbackQuery, state: FSMContext):
    split_id = callback.data.split("_")[2]
    await state.update_data(split_id=split_id)
    
    cursor.execute("SELECT COUNT(*) FROM exercises WHERE split_id = ?", (split_id,))
    count = cursor.fetchone()[0]
    
    sent_msg = await callback.message.answer(
        f"Введіть <b>номер вправи</b>, яку потрібно видалити (від 1 до {count}):",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    await save_msg(state, sent_msg.message_id)
    await state.set_state(DeleteExercise.waiting_for_number)
    await callback.answer()

@router.message(DeleteExercise.waiting_for_number)
async def process_delete_exercise_confirm(message: Message, state: FSMContext):
    await save_msg(state, message.message_id)
    data = await state.get_data()
    split_id = data['split_id']
    
    cursor.execute("SELECT id, name FROM exercises WHERE split_id = ? AND order_num = ?", (split_id, message.text.strip()))
    row = cursor.fetchone()
    
    if not row:
        err_msg = await message.answer("⚠️ Вправу з таким номером не знайдено. Спробуйте ще раз:", reply_markup=get_cancel_keyboard())
        await save_msg(state, err_msg.message_id)
        return

    ex_id, ex_name = row
    deleted_num = int(message.text.strip())
    
    cursor.execute("DELETE FROM exercises WHERE id = ?", (ex_id,))
    cursor.execute("UPDATE exercises SET order_num = order_num - 1 WHERE split_id = ? AND order_num > ?", (split_id, deleted_num))
    conn.commit()

    await clear_old_messages(bot, message.chat.id, state)
    await state.clear()
    
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Повернутися до спліту", callback_data=f"split_{split_id}")
    builder.adjust(1)
    
    await message.answer(f"🗑️ Вправу №{deleted_num} (<b>{ex_name}</b>) видалено!", reply_markup=builder.as_markup(), parse_mode="HTML")

# --- УПРАВЛЕНИЕ СПЛИТОМ: ДОБАВЛЕНИЕ, ПЕРЕИМЕНОВАНИЕ И УДАЛЕНИЕ ---
@router.callback_query(F.data.startswith("add_split_"))
async def process_add_split_button(callback: CallbackQuery, state: FSMContext):
    client_id = callback.data.split("_")[2]
    cursor.execute("SELECT name FROM clients WHERE id = ?", (client_id,))
    client_row = cursor.fetchone()
    
    await state.update_data(client_id=client_id, client_name=client_row[0])
    sent_msg = await callback.message.answer(
        "Введіть назву нового спліту (наприклад: <i>Низ / День 2</i>):", 
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    await save_msg(state, sent_msg.message_id)
    await state.set_state(AddSplitFlow.waiting_for_split_name)
    await callback.answer()

@router.callback_query(F.data.startswith("rename_split_"))
async def process_rename_split(callback: CallbackQuery, state: FSMContext):
    split_id = callback.data.split("_")[2]
    await state.update_data(split_id=split_id)
    sent_msg = await callback.message.answer(
        "Введіть нову назву для цього спліту:", 
        reply_markup=get_cancel_keyboard()
    )
    await save_msg(state, sent_msg.message_id)
    await state.set_state(EditSplitName.waiting_for_new_name)
    await callback.answer()

@router.message(EditSplitName.waiting_for_new_name)
async def process_split_new_name(message: Message, state: FSMContext):
    await save_msg(state, message.message_id)
    data = await state.get_data()
    split_id = data['split_id']
    new_name = message.text.strip()
    
    cursor.execute("UPDATE splits SET name = ? WHERE id = ?", (new_name, split_id))
    cursor.execute("SELECT client_id FROM splits WHERE id = ?", (split_id,))
    client_id = cursor.fetchone()[0]
    conn.commit()

    await clear_old_messages(bot, message.chat.id, state)
    await state.clear()
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 До спліту", callback_data=f"split_{split_id}")
    builder.button(text="🏋️ До клієнта", callback_data=f"client_{client_id}")
    builder.adjust(1)
    
    await message.answer(f"✅ Назву змінено на: <b>{new_name}</b>", reply_markup=builder.as_markup(), parse_mode="HTML")

@router.callback_query(F.data.startswith("delete_split_"))
async def process_delete_split(callback: CallbackQuery):
    split_id = callback.data.split("_")[2]
    
    cursor.execute("SELECT client_id, name FROM splits WHERE id = ?", (split_id,))
    split_row = cursor.fetchone()
    
    if split_row:
        client_id, split_name = split_row
        cursor.execute("DELETE FROM exercises WHERE split_id = ?", (split_id,))
        cursor.execute("DELETE FROM splits WHERE id = ?", (split_id,))
        conn.commit()
        
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 Назад до клієнта", callback_data=f"client_{client_id}")
        await callback.message.edit_text(f"🗑️ Спліт <b>{split_name}</b> видалено.", reply_markup=builder.as_markup(), parse_mode="HTML")
    else:
        await callback.message.edit_text("⚠️ Спліт вже видалено.")
        
    await callback.answer()

# --- НАВИГАЦИЯ И УДАЛЕНИЕ КЛИЕНТА ---
@router.callback_query(F.data == "back_to_clients")
async def process_back_to_clients(callback: CallbackQuery):
    cursor.execute("SELECT id, name FROM clients")
    clients = cursor.fetchall()
    
    if not clients:
        await callback.message.edit_text("Список порожній.")
        await callback.answer()
        return
        
    builder = InlineKeyboardBuilder()
    for client_id, client_name in clients:
        builder.button(text=client_name, callback_data=f"client_{client_id}")
    builder.adjust(1)
    
    await callback.message.edit_text("🏋️ Оберіть клієнта:", reply_markup=builder.as_markup())
    await callback.answer()

@router.callback_query(F.data.startswith("delete_client_"))
async def process_delete_client(callback: CallbackQuery):
    client_id = callback.data.split("_")[2]
    
    cursor.execute("SELECT name FROM clients WHERE id = ?", (client_id,))
    client_row = cursor.fetchone()
    
    if client_row:
        client_name = client_row[0]
        cursor.execute("SELECT id FROM splits WHERE client_id = ?", (client_id,))
        splits = cursor.fetchall()
        for (s_id,) in splits:
            cursor.execute("DELETE FROM exercises WHERE split_id = ?", (s_id,))
        cursor.execute("DELETE FROM splits WHERE client_id = ?", (client_id,))
        cursor.execute("DELETE FROM clients WHERE id = ?", (client_id,))
        conn.commit()
        
        await callback.message.edit_text(
            f"🗑️ Клієнт <b>{client_name}</b> та всі його дані видалені.\n\nСкористайтеся /clients",
            parse_mode="HTML"
        )
    else:
        await callback.message.edit_text("⚠️ Клієнта вже не існує.")
        
    await callback.answer()

# --- ИЗМЕНЕНИЕ ВЕСА УПРАЖНЕНИЯ ---
@router.callback_query(F.data.startswith("edit_weight_"))
async def process_edit_weight(callback: CallbackQuery, state: FSMContext):
    ex_id = callback.data.split("_")[2]
    await state.update_data(exercise_id=ex_id)
    
    sent_msg = await callback.message.answer(
        "Введіть нові дані (наприклад: 40/12, 50/10):",
        reply_markup=get_cancel_keyboard()
    )
    await save_msg(state, sent_msg.message_id)
    await state.set_state(EditWeight.waiting_for_new_weight)
    await callback.answer()

@router.message(EditWeight.waiting_for_new_weight)
async def update_weight_in_db(message: Message, state: FSMContext):
    await save_msg(state, message.message_id)
    data = await state.get_data()
    ex_id = data['exercise_id']
    new_details = message.text

    cursor.execute("UPDATE exercises SET details = ? WHERE id = ?", (new_details, ex_id))
    cursor.execute("SELECT split_id FROM exercises WHERE id = ?", (ex_id,))
    split_row = cursor.fetchone()
    conn.commit()

    await clear_old_messages(bot, message.chat.id, state)
    await state.clear()

    builder = InlineKeyboardBuilder()
    if split_row:
        builder.button(text="📋 Назад до спліту", callback_data=f"split_{split_row[0]}")
    builder.adjust(1)

    await message.answer(
        f"✅ Оновлено!\nНовий запис: {new_details}",
        reply_markup=builder.as_markup()
    )

# --- ЗАПУСК БОТА ---
async def main():
    logging.basicConfig(level=logging.INFO)
    dp.include_router(router)
    print("Бот запущен.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())