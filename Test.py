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

ADMIN_ID = 524827756  # Твой ID для /update

# --- 3. МАШИНЫ СОСТОЯНИЙ (FSM) ---
class AddClientFlow(StatesGroup):
    waiting_for_name = State()
    waiting_for_split = State()
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
        "/clients - Переглянути клієнтів та їх плани"
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

# Отмена действия по кнопке «Назад»
@router.callback_query(F.data == "cancel_action")
async def process_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Дюю скасовано. Головне меню:\n /clients — список клієнтів\n /add — додати клієнта")
    await callback.answer()

# --- БЛОК ДОБАВЛЕНИЯ КЛИЕНТА (/add) С ВАЛИДАЦИЕЙ ИМЕНИ ---
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
    
    # ПРОВЕРКА ИМЕНИ: разрешены только буквы (украинские/русские/английские), пробелы и дефисы. Никаких слешей, цифр и т.д.
    if not re.match(r"^[a-zA-Zа-яА-ЯёЁіІїЇєЄґҐ\s\-]+$", name) or len(name) < 2:
        await message.answer(
            "⚠️ <b>Некоректне ім'я!</b> Ім'я має складатися тільки з букв (без слешів, цифр та спецсимволів).\nСпробуйте ще раз:",
            reply_markup=get_cancel_keyboard(),
            parse_mode="HTML"
        )
        return

    await state.update_data(client_name=name)
    await message.answer(
        f"Клієнт: <b>{name}</b>\nВведіть назву спліту (наприклад: <i>Спина / Плечі</i>):",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(AddClientFlow.waiting_for_split)

@router.message(AddClientFlow.waiting_for_split)
async def process_split_name(message: Message, state: FSMContext):
    await state.update_data(split_name=message.text.strip())
    await message.answer(
        "Відправте список вправ (назва та підходи через двокрапку).\n\n"
        "Приклад:\n"
        "Тяга вертикального блоку: 30/14, 40/12, 50/10",
        reply_markup=get_cancel_keyboard()
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
            pass
            
    conn.commit()
    await message.answer(f"✅ Клієнт <b>{client_name}</b> успішно доданий!\nВправ збережено: {added_count}", parse_mode="HTML")
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
    
    cursor.execute("SELECT id, split_name, name, details FROM exercises WHERE client_id = ?", (client_id,))
    exercises = cursor.fetchall()
    
    builder = InlineKeyboardBuilder()
    
    if not exercises:
        text = f"🏋️ <b>Клієнт:</b> {client_name}\n\nУ цього клієнта поки немає вправ."
    else:
        split_name = exercises[0][1]
        text = f"🏋️ <b>Клієнт:</b> {client_name}\n📋 <b>Спліт:</b> {split_name}\n\n"
        for i, (ex_id, _, ex_name, ex_details) in enumerate(exercises, 1):
            text += f"{i}. <b>{ex_name}</b>: {ex_details}\n"
            short_name = ex_name[:15] + "..." if len(ex_name) > 15 else ex_name
            builder.button(text=f"📝 {short_name}", callback_data=f"edit_weight_{ex_id}")
            
    # Кнопка удаления клиента
    builder.button(text="🗑️ Видалити клієнта", callback_data=f"delete_client_{client_id}")
    # Кнопка возврата к списку клиентов
    builder.button(text="🔙 Назад до списку", callback_data="back_to_clients")
    builder.adjust(1)
    
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
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
        cursor.execute("DELETE FROM exercises WHERE client_id = ?", (client_id,))
        cursor.execute("DELETE FROM clients WHERE id = ?", (client_id,))
        conn.commit()
        
        await callback.message.edit_text(
            f"🗑️ Клієнт <b>{client_name}</b> та всі його тренування успішно видалені.\n\nСкористайтеся /clients щоб переглянути оновлений список.",
            parse_mode="HTML"
        )
    else:
        await callback.message.edit_text("⚠️ Клієнта вже не існує в базі.")
        
    await callback.answer()

# --- БЛОК ИЗМЕНЕНИЯ ВЕСА ---
@router.callback_query(F.data.startswith("edit_weight_"))
async def process_edit_weight(callback: CallbackQuery, state: FSMContext):
    ex_id = callback.data.split("_")[2]
    await state.update_data(exercise_id=ex_id)
    
    await state.set_state(EditWeight.waiting_for_new_weight)
    await callback.message.answer(
        "Введіть нові дані (наприклад: 40/12, 50/10, 60/8):",
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

    await message.answer(f"✅ Оновлено!\nНовий запис: {new_details}\n\n<i>(Натисніть /clients щоб переглянути список)</i>", parse_mode="HTML")
    await state.clear()


# --- ЗАПУСК БОТА ---
async def main():
    logging.basicConfig(level=logging.INFO)
    dp.include_router(router)
    print("Бот запущен.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())