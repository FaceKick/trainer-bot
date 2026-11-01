import asyncio
import logging
import sqlite3
import subprocess
import sys
import os
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --- 1. БАЗА ДАННЫХ ---
conn = sqlite3.connect("clients.db")
cursor = conn.cursor()

cursor.execute("""
    CREATE TABLE IF NOT EXISTS clients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT
    )
""")
cursor.execute("""
    CREATE TABLE IF NOT EXISTS exercises (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        client_id INTEGER,
        split_name TEXT,
        name TEXT,
        details TEXT
    )
""")
conn.commit()

# --- 2. НАСТРОЙКА БОТА ---
BOT_TOKEN = "9256637883:AAHGIGHweeAlKhpKCUSmJeC7TtM7Kyfp1mQ"
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()

# --- 3. МАШИНЫ СОСТОЯНИЙ (FSM) ---
class AddClientFlow(StatesGroup):
    waiting_for_name = State()
    waiting_for_split = State()
    waiting_for_exercises = State()

class EditWeight(StatesGroup):
    waiting_for_new_weight = State()
    exercise_id = State()

# --- 4. ОБРАБОТЧИКИ КОМАНД ---

@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Привіт! Я бот для управління тренуваннями.\n\n"
        "Доступні команди:\n"
        "/add - Додати нового клієнта\n"
        "/clients - Переглянути клієнтів та їх плани"
    )

# --- БЛОК ДОБАВЛЕНИЯ КЛИЕНТА (/add) ---
@router.message(Command("add"))
async def cmd_add_client(message: Message, state: FSMContext):
    await message.answer("Введіть ім'я клієнта (наприклад: Влад):")
    await state.set_state(AddClientFlow.waiting_for_name)

@router.message(AddClientFlow.waiting_for_name)
async def process_client_name(message: Message, state: FSMContext):
    await state.update_data(client_name=message.text)
    await message.answer("Введіть назву спліту (наприклад: Спина / Плечі):")
    await state.set_state(AddClientFlow.waiting_for_split)

@router.message(AddClientFlow.waiting_for_split)
async def process_split_name(message: Message, state: FSMContext):
    await state.update_data(split_name=message.text)
    await message.answer(
        "Відправте список вправ (назва та підходи через двокрапку).\n\n"
        "Приклад:\n"
        "Тяга вертикального блоку широким хватом: 30/14, 40/12, 50/10"
    )
    await state.set_state(AddClientFlow.waiting_for_exercises)

@router.message(AddClientFlow.waiting_for_exercises)
async def process_exercises(message: Message, state: FSMContext):
    data = await state.get_data()
    client_name = data['client_name']
    split_name = data['split_name']
    
    cursor.execute("INSERT INTO clients (name) VALUES (?)", (client_name,))
    client_id = cursor.lastrowid
    
    lines = message.text.split('\n')
    added_count = 0
    
    for line in lines:
        if not line.strip():
            continue
        try:
            parts = line.split(':', 1)
            ex_name = parts[0].strip()
            details = parts[1].strip()
            
            cursor.execute(
                """INSERT INTO exercises 
                (client_id, split_name, name, details) 
                VALUES (?, ?, ?, ?)""",
                (client_id, split_name, ex_name, details)
            )
            added_count += 1
        except Exception:
            pass # Игнорируем строки с ошибками (без двоеточия)
            
    conn.commit()
    await message.answer(f"✅ Клієнт <b>{client_name}</b> доданий!\nВправ збережено: {added_count}", parse_mode="HTML")
    await state.clear()

# --- БЛОК ПРОСМОТРА КЛИЕНТОВ (/clients) ---
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
    
    await message.answer("Оберіть клієнта:", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("client_"))
async def process_client_selection(callback: CallbackQuery):
    client_id = callback.data.split("_")[1]
    
    cursor.execute("SELECT name FROM clients WHERE id = ?", (client_id,))
    client_row = cursor.fetchone()
    if not client_row:
        return
    client_name = client_row[0]
    
    cursor.execute("SELECT id, split_name, name, details FROM exercises WHERE client_id = ?", (client_id,))
    exercises = cursor.fetchall()
    
    if not exercises:
        await callback.message.answer(f"У клієнта {client_name} немає вправ.")
        await callback.answer()
        return
        
    split_name = exercises[0][1]
    text = f"🏋️ <b>Клієнт:</b> {client_name}\n📋 <b>Спліт:</b> {split_name}\n\n"
    
    builder = InlineKeyboardBuilder()
    for i, (ex_id, _, ex_name, ex_details) in enumerate(exercises, 1):
        text += f"{i}. <b>{ex_name}</b>: {ex_details}\n"
        short_name = ex_name[:15] + "..." if len(ex_name) > 15 else ex_name
        builder.button(text=f"📝 {short_name}", callback_data=f"edit_weight_{ex_id}")
        
    builder.adjust(1)
    await callback.message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()

# --- БЛОК ИЗМЕНЕНИЯ ВЕСА ---
@router.callback_query(F.data.startswith("edit_weight_"))
async def process_edit_weight(callback: CallbackQuery, state: FSMContext):
    ex_id = callback.data.split("_")[2]
    await state.update_data(exercise_id=ex_id)
    
    await state.set_state(EditWeight.waiting_for_new_weight)
    await callback.message.answer("Введіть нові дані (наприклад: 40/12, 50/10, 60/8):")
    await callback.answer()

@router.message(EditWeight.waiting_for_new_weight)
async def update_weight_in_db(message: Message, state: FSMContext):
    data = await state.get_data()
    ex_id = data['exercise_id']
    new_details = message.text

    cursor.execute("UPDATE exercises SET details = ? WHERE id = ?", (new_details, ex_id))
    conn.commit()

    await message.answer(f"✅ Оновлено!\nНовий запис: {new_details}\n\n<i>(Натисніть /clients щоб переглянути оновлений план)</i>", parse_mode="HTML")
    await state.clear()
# Узнать свой Telegram ID можнопросто написав команду /myid своему боту
ADMIN_ID = 524827756  # <-- ЗАМЕНИ НА СВОЙ ЦИФРОВОЙ ID

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
        # Выполняем git pull на сервере
        result = subprocess.run(["git", "pull"], capture_output=True, text=True, check=True)
        output = result.stdout.strip()

        if "Already up to date." in output:
            await message.answer("✅ Бот вже працює на найновішій версії.")
        else:
            await message.answer(f"📥 Код оновлено:\n<pre>{output}</pre>\n🚀 Перезапускаюсь...", parse_mode="HTML")
            # Завершаем процесс. Служба systemd на сервере сама поднимет его через 3 секунды
            os._exit(0)
    except Exception as e:
        await message.answer(f"⚠️ Помилка оновлення:\n<pre>{e}</pre>", parse_mode="HTML")

# --- ЗАПУСК БОТА ---
async def main():
    logging.basicConfig(level=logging.INFO)
    dp.include_router(router)
    print("Бот запущен. Нажмите Ctrl+C в терминале для остановки.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())