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

# --- 1. БАЗА ДАННЫХ И АВТОМАТИЧЕСКАЯ МИГРАЦИЯ ---
conn = sqlite3.connect("clients.db")
cursor = conn.cursor()

# Создаем базовые таблицы (если их еще нет)
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

# Проверяем структуру существующей таблицы exercises
cursor.execute("PRAGMA table_info(exercises)")
columns = [col[1] for col in cursor.fetchall()]

if not columns:
    # Таблицы еще нет — создаем сразу в новом формате
    cursor.execute("""
        CREATE TABLE exercises (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            split_id INTEGER,
            name TEXT,
            details TEXT
        )
    """)
elif "split_id" not in columns:
    # Таблица есть, но в старом формате (с client_id) — аккуратно мигрируем данные!
    print("🔄 Обнаружена старая версия базы. Выполняется автоматическая миграция...")
    
    # 1. Для каждого клиента, у которого были упражнения, создаем сплит по умолчанию
    cursor.execute("SELECT DISTINCT client_id, split_name FROM exercises")
    old_splits = cursor.fetchall()
    
    # Временный словарь старых сплитов: (client_id, split_name) -> new_split_id
    split_map = {}
    for c_id, s_name in old_splits:
        name_to_use = s_name if s_name else "Основний спліт"
        cursor.execute("INSERT INTO splits (client_id, name) VALUES (?, ?)", (c_id, name_to_use))
        split_map[(c_id, s_name)] = cursor.lastrowid

    # 2. Создаем новую временную таблицу под правильную структуру
    cursor.execute("""
        CREATE TABLE exercises_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            split_id INTEGER,
            name TEXT,
            details TEXT
        )
    """)

    # 3. Переносим все существующие упражнения, привязывая их к новым сплитам
    cursor.execute("SELECT id, client_id, split_name, name, details FROM exercises")
    old_exercises = cursor.fetchall()
    for ex_id, c_id, s_name, ex_name, ex_details in old_exercises:
        new_split_id = split_map.get((c_id, s_name))
        cursor.execute(
            "INSERT INTO exercises_new (id, split_id, name, details) VALUES (?, ?, ?, ?)",
            (ex_id, new_split_id, ex_name, ex_details)
        )

    # 4. Заменяем старую таблицу новой
    cursor.execute("DROP TABLE exercises")
    cursor.execute("ALTER TABLE exercises_new RENAME TO exercises")
    print("✅ Миграция завершена! Все клиенты и упражнения сохранены.")

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

# --- 4. ВСПОМОГАТЕЛЬНЫЕ КЛАВИАТУРЫ ---
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
    await state.clear()
    await callback.message.edit_text("❌ Дію скасовано.\n /clients — список клієнтів\n /add — додати клієнта")
    await callback.answer()

# --- ДОБАВЛЕНИЕ КЛИЕНТА ---
@router.message(Command("add"))
async def cmd_add_client(message: Message, state: FSMContext):
    await message.answer(
        "Введіть ім'я клієнта (тільки букви, без слешів та спецсимволів, наприклад: <i>Влад</i>):",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(AddClientFlow.waiting_for_name)

@router.message(AddClientFlow.waiting_for_name)
async def process_client_name(message: Message, state: FSMContext):
    name = message.text.strip()
    
    if not re.match(r"^[a-zA-Zа-яА-ЯёЁіІїЇєЄґҐ\s\-]+$", name) or len(name) < 2:
        await message.answer(
            "⚠️ <b>Некоректне ім'я!</b> Тільки букви без спецсимволів. Спробуйте ще раз:",
            reply_markup=get_cancel_keyboard(),
            parse_mode="HTML"
        )
        return

    cursor.execute("INSERT INTO clients (name) VALUES (?)", (name,))
    client_id = cursor.lastrowid
    conn.commit()
    
    await state.clear()
    await state.update_data(client_id=client_id, client_name=name)
    await message.answer(
        f"✅ Клієнт <b>{name}</b> доданий!\n\nТепер введіть назву першого спліту (наприклад: <i>Верх / День 1</i>):",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(AddSplitFlow.waiting_for_split_name)

@router.message(AddSplitFlow.waiting_for_split_name)
async def process_split_name(message: Message, state: FSMContext):
    await state.update_data(split_name=message.text.strip())
    await message.answer(
        "Відправте список вправ (назва та підходи через двокрапку).\nПриклад:\nЖим лежачи: 60/10, 70/8",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(AddSplitFlow.waiting_for_exercises)

@router.message(AddSplitFlow.waiting_for_exercises)
async def process_split_exercises(message: Message, state: FSMContext):
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
            cursor.execute(
                "INSERT INTO exercises (split_id, name, details) VALUES (?, ?, ?)",
                (split_id, parts[0].strip(), parts[1].strip())
            )
            added_count += 1
        except Exception:
            pass
            
    conn.commit()
    await message.answer(f"✅ Спліт <b>{split_name}</b> успішно збережено!\nДодано вправ: {added_count}", parse_mode="HTML")
    await state.clear()

# --- ПРОСМОТР КЛИЕНТОВ ---
@router.message(Command("clients"))
async def cmd_show_clients(message: Message):
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
async def process_client_selection(callback: CallbackQuery):
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
async def process_split_selection(callback: CallbackQuery):
    split_id = callback.data.split("_")[1]
    
    cursor.execute("SELECT name, client_id FROM splits WHERE id = ?", (split_id,))
    split_row = cursor.fetchone()
    if not split_row:
        return
    split_name, client_id = split_row
    
    cursor.execute("SELECT id, name, details FROM exercises WHERE split_id = ?", (split_id,))
    exercises = cursor.fetchall()
    
    text = f"📋 <b>Спліт:</b> {split_name}\n\n"
    builder = InlineKeyboardBuilder()
    
    for i, (ex_id, ex_name, ex_details) in enumerate(exercises, 1):
        text += f"{i}. <b>{ex_name}</b>: {ex_details}\n"
        short_name = ex_name[:15] + "..." if len(ex_name) > 15 else ex_name
        builder.button(text=f"📝 {short_name}", callback_data=f"edit_weight_{ex_id}")
        
    builder.button(text="🔙 Назад до клієнта", callback_data=f"client_{client_id}")
    builder.adjust(1)
    
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("add_split_"))
async def process_add_split_button(callback: CallbackQuery, state: FSMContext):
    client_id = callback.data.split("_")[2]
    cursor.execute("SELECT name FROM clients WHERE id = ?", (client_id,))
    client_row = cursor.fetchone()
    
    await state.update_data(client_id=client_id, client_name=client_row[0])
    await callback.message.answer("Введіть назву нового спліту (наприклад: <i>Низ / День 2</i>):", parse_mode="HTML")
    await state.set_state(AddSplitFlow.waiting_for_split_name)
    await callback.answer()

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

# --- УДАЛЕНИЕ КЛИЕНТА ---
@router.callback_query(F.data.startswith("delete_client_"))
async def process_delete_client(callback: CallbackQuery):
    client_id = callback.data.split("_")[2]
    
    cursor.execute("SELECT name FROM clients WHERE id = ?", (client_id,))
    client_row = cursor.fetchone()
    
    if client_row:
        client_name = client_row[0]
        # Удаляем каскадом сплиты и упражнения
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

# --- ИЗМЕНЕНИЕ ВЕСА ---
@router.callback_query(F.data.startswith("edit_weight_"))
async def process_edit_weight(callback: CallbackQuery, state: FSMContext):
    ex_id = callback.data.split("_")[2]
    await state.update_data(exercise_id=ex_id)
    
    await state.set_state(EditWeight.waiting_for_new_weight)
    await callback.message.answer(
        "Введіть нові дані (наприклад: 40/12, 50/10):",
        reply_markup=get_cancel_keyboard()
    )
    await callback.answer()

@router.message(EditWeight.waiting_for_new_weight)
async def update_weight_in_db(message: Message, state: FSMContext):
    data = await state.get_data()
    ex_id = data['exercise_id']
    new_details = message.text

    cursor.execute("UPDATE exercises SET details = ? WHERE id = ?", (new_details, ex_id))
    conn.commit()

    await message.answer(f"✅ Оновлено!\nНовий запис: {new_details}\n\n<i>(Натисніть /clients)</i>", parse_mode="HTML")
    await state.clear()


# --- ЗАПУСК БОТА ---
async def main():
    logging.basicConfig(level=logging.INFO)
    dp.include_router(router)
    print("Бот запущен.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())