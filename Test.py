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
# Новая таблица для тренировочных дней (сплитов)
cursor.execute("""
    CREATE TABLE IF NOT EXISTS splits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        client_id INTEGER,
        name TEXT
    )
""")
cursor.execute("""
    CREATE TABLE IF NOT EXISTS exercises (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        split_id INTEGER,
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

class AddSplitFlow(StatesGroup):
    waiting_for_name = State()    
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
    
    cursor.execute("INSERT INTO clients (name) VALUES (?)", (name,))
    client_id = cursor.lastrowid
    conn.commit()    

    await state.clear()
    await message.answer(
        f"Клієнт: <b>{name}</b>\nВведіть назву спліту (наприклад: <i>Спина / Плечі</i>):",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    await state.update_data(client_id=client_id, client_name=name)
    await state.set_state(AddSplitFlow.waiting_for_split_name)

# --- БЛОК ДОБАВЛЕНИЯ СПЛИТА ---
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
    
   cursor.execute("INSERT INTO splits (client_id, name) VALUES (?, ?)", (client_id, split_name))
    split_id = cursor.lastrowid
    
    lines = message.text.split('\n')
    for line in lines:
        if not line.strip():
            continue
        try:
            parts = line.split(':', 1)
            cursor.execute(
                "INSERT INTO exercises (split_id, name, details) VALUES (?, ?, ?)",
                (split_id, parts[0].strip(), parts[1].strip())
            )
        except Exception:
            pass
            
    conn.commit()
    await message.answer(f"✅ Спліт <b>{split_name}</b> успішно збережено!", parse_mode="HTML")
    await state.clear()



# --- БЛОК ПРОСМОТРА КЛИЕНТОВ (/clients) ---
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
    
    # Получаем список сплитов этого клиента
    cursor.execute("SELECT id, name FROM splits WHERE client_id = ?", (client_id,))
    splits = cursor.fetchall()
    
    builder = InlineKeyboardBuilder()
    text = f"🏋️ <b>Клієнт:</b> {client_name}\n\nОберіть тренувальний день:"
    
    for split_id, split_name in splits:
        builder.button(text=f"📋 {split_name}", callback_data=f"split_{split_id}")
        
    # Кнопка добавления еще одного сплита этому клиенту
    builder.button(text="➕ Додати новий спліт", callback_data=f"add_split_{client_id}")
    # Кнопка удаления клиента
    builder.button(text="🗑️ Видалити клієнта", callback_data=f"delete_client_{client_id}")
    # Кнопка назад
    builder.button(text="🔙 Назад до списку", callback_data="back_to_clients")
    builder.adjust(1)
    
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()

# --- УДАЛЕНИЕ КЛИЕНТА ---
@router.callback_query(F.data.startswith("delete_client_"))
async def process_delete_client(callback: CallbackQuery):
    client_id = callback.data.split("_")[2]
    
    # Узнаем имя клиента перед удалением, чтобы красиво написать в ответе
    cursor.execute("SELECT name FROM clients WHERE id = ?", (client_id,))
    client_row = cursor.fetchone()
    
    if client_row:
        client_name = client_row[0]
        # Удаляем самого клиента и все его упражнения каскадом
        cursor.execute("DELETE FROM exercises WHERE client_id = ?", (client_id,))
        cursor.execute("DELETE FROM clients WHERE id = ?", (client_id,))
        conn.commit()
        
        await callback.message.edit_text(
            f"🗑️ Клієнт <b>{client_name}</b> та всі його тренування успішно видалені з бази.",
            parse_mode="HTML"
        )
    else:
        await callback.message.edit_text("⚠️ Клієнта вже не існує в базі.")
        
    await callback.answer()
#ПРОСМОТР КОНКРЕТНОГО СПЛИТА І КНОПКА ДОБАВЛЕНИЯ СПЛИТА
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

# Кнопка «Додати новий спліт» из меню клиента
@router.callback_query(F.data.startswith("add_split_"))
async def process_add_split_button(callback: CallbackQuery, state: FSMContext):
    client_id = callback.data.split("_")[2]
    cursor.execute("SELECT name FROM clients WHERE id = ?", (client_id,))
    client_row = cursor.fetchone()
    
    await state.update_data(client_id=client_id, client_name=client_row[0])
    await callback.message.answer("Введіть назву нового спліту (наприклад: <i>Низ / День 2</i>):", parse_mode="HTML")
    await state.set_state(AddSplitFlow.waiting_for_split_name)
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