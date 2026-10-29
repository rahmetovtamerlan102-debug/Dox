import os
import requests
import sqlite3
import asyncio
from datetime import datetime
from pyrogram import Client, filters, enums, idle
from pyrogram.raw.functions.payments import GetSavedStarGifts

# ==========================================
# 1. КОНФИГУРАЦИЯ (ИЗ RENDER)
# ==========================================
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")
API_URL = os.environ.get("API_URL", "")

CACHE_DB = "cache.db"

LOCAL_DB = {
    100000000: "2013-08", 200000000: "2014-01", 300000000: "2014-07",
    400000000: "2014-12", 500000000: "2015-04", 700000000: "2015-10",
    1000000000: "2016-04", 1500000000: "2017-01", 2000000000: "2017-07",
    3000000000: "2018-06", 4000000000: "2019-04", 5000000000: "2020-01",
    6000000000: "2021-02", 7000000000: "2022-06", 7500000000: "2023-05",
    8000000000: "2024-06", 8500000000: "2025-01",
}
MONTHS_RU = {1: "январь", 2: "февраль", 3: "март", 4: "апрель", 5: "май", 6: "июнь", 7: "июль", 8: "август", 9: "сентябрь", 10: "октябрь", 11: "ноябрь", 12: "декабрь"}

# ==========================================
# 2. КЭШ
# ==========================================
def init_cache():
    conn = sqlite3.connect(CACHE_DB)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS cache (
        user_id INTEGER PRIMARY KEY, username TEXT, reg_date TEXT, phones TEXT, gifts TEXT
    )''')
    conn.commit()
    conn.close()

def get_cache(user_id):
    conn = sqlite3.connect(CACHE_DB)
    c = conn.cursor()
    c.execute("SELECT username, reg_date, phones, gifts FROM cache WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row

def set_cache(user_id, username, reg_date, phones, gifts):
    conn = sqlite3.connect(CACHE_DB)
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO cache (user_id, username, reg_date, phones, gifts) 
                 VALUES (?, ?, ?, ?, ?)''', (user_id, username, reg_date, phones, gifts))
    conn.commit()
    conn.close()

# ==========================================
# 3. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================
def get_time_passed(year, month):
    now = datetime.now()
    reg_date = datetime(year, month, 1)
    years = now.year - reg_date.year
    months = now.month - reg_date.month
    if months < 0:
        years -= 1; months += 12
    y_str = f"{years} год" if years % 10 == 1 and years % 100 != 11 else \
            f"{years} года" if 2 <= years % 10 <= 4 and not (12 <= years % 100 <= 14) else f"{years} лет"
    if years == 0: y_str = ""
    m_str = f"{months} мес" if months > 0 else ""
    return f"({y_str} {m_str})".strip()

def estimate_registration(user_id):
    ids = sorted(LOCAL_DB.keys())
    if user_id <= ids[0]: p = LOCAL_DB[ids[0]].split("-"); return int(p[0]), int(p[1])
    if user_id >= ids[-1]: p = LOCAL_DB[ids[-1]].split("-"); return int(p[0]), int(p[1])
    for i in range(len(ids) - 1):
        id_start, id_end = ids[i], ids[i+1]
        if id_start <= user_id <= id_end:
            dt_start = datetime.strptime(LOCAL_DB[id_start], "%Y-%m")
            dt_end = datetime.strptime(LOCAL_DB[id_end], "%Y-%m")
            months_diff = (dt_end.year - dt_start.year) * 12 + (dt_end.month - dt_start.month)
            if months_diff <= 0: months_diff = 1
            id_ratio = (user_id - id_start) / (id_end - id_start)
            months_to_add = int(id_ratio * months_diff)
            est_y = dt_start.year + (dt_start.month + months_to_add - 1) // 12
            est_m = (dt_start.month + months_to_add - 1) % 12 + 1
            return est_y, est_m
    return None, None

def get_phones_from_api(user_id):
    if not API_URL: return None
    try:
        response = requests.get(f"{API_URL}/get_phone", params={"id": user_id}, timeout=5)
        if response.status_code == 200:
            phones = response.json().get("phones", [])
            return ", ".join(phones) if phones else None
    except Exception:
        pass
    return None

# ==========================================
# 4. КЛИЕНТЫ
# ==========================================
bot_app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
user_app = Client("my_userbot", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING)

@bot_app.on_message(filters.command("start"))
async def start_command(client, message):
    await message.reply_text("👋 Отправь мне **ID** или **@username**, и я пробью его по базе.")

@bot_app.on_message(filters.text & filters.private)
async def search_handler(client, message):
    query = message.text.strip()
    user_id = None
    username_str = "Скрыт"
    peer_target = None # То, по чему будем запрашивать подарки

    status = await message.reply_text(f"📡 Обрабатываю запрос...", parse_mode=enums.ParseMode.HTML)

    # --- ЛОГИКА ОПРЕДЕЛЕНИЯ ID ---
    if query.isdigit():
        user_id = int(query)
        peer_target = user_id
        try:
            # Пробуем узнать ник по ID
            u = await user_app.get_users(user_id)
            if u.username: username_str = f"@{u.username}"
        except: pass
    else:
        # Введен юзернейм
        username = query if query.startswith('@') else f"@{query}"
        peer_target = username # Для подарков используем сам ник
        
        try:
            target_user = await user_app.get_users(username)
            user_id = target_user.id
            username_str = f"@{target_user.username}" if target_user.username else username
        except Exception:
            await status.edit_text("❌ Не удалось найти этот юзернейм. Попробуйте ввести ID.")
            return

    if not user_id: return

    await status.edit_text(f"📡 Собираю данные для ID `{user_id}`...", parse_mode=enums.ParseMode.HTML)

    # --- ПРОВЕРКА КЭША ---
    cached_data = get_cache(user_id)
    if cached_data:
        cached_user, reg_date, phones, gifts_text = cached_data
        card = (
            f"👤 <b>Аккаунт Telegram</b> [КЭШ]\n"
            f"├ ID: <code>{user_id}</code>\n"
            f"├ Username: {cached_user}\n"
            f"└ Регистрация: {reg_date}\n\n"
            f"📞 <b>Телефон:</b> <code>{phones}</code>\n"
        )
        if gifts_text and gifts_text != "Нет":
            card += f"\n🎁 <b>Подарки:</b>\n<blockquote>{gifts_text}</blockquote>"
        await status.edit_text(card, parse_mode=enums.ParseMode.HTML)
        return

    # --- ЕСЛИ В КЭШЕ НЕТ ---
    est_y, est_m = estimate_registration(user_id)
    reg_date = f"~ {MONTHS_RU.get(est_m, '')} {est_y} {get_time_passed(est_y, est_m)}" if est_y else "Неизвестно"

    phones = get_phones_from_api(user_id)
    phone_str = phones if phones else "Не найден в базе"

    # --- ПОИСК ПОДАРКОВ (по юзу или по ID) ---
    gifts_text = "Нет"
    try:
        # Если есть ник, используем его (это надежнее)
        if username_str != "Скрыт":
            peer = await user_app.resolve_peer(username_str)
        else:
            peer = await user_app.resolve_peer(user_id)
            
        result = await user_app.invoke(GetSavedStarGifts(peer=peer, offset="", limit=100))
        if result.gifts:
            sender_ids = []
            for g in result.gifts:
                if not getattr(g, 'name_hidden', False) and g.from_id:
                    sender_id = getattr(g.from_id, 'user_id', None)
                    if sender_id: sender_ids.append(str(sender_id))
            if sender_ids:
                gifts_text = ", ".join(sender_ids)
    except Exception as e:
        print(f"Ошибка подарков: {e}") # Выведется в логи Render, если что-то не так

    set_cache(user_id, username_str, reg_date, phone_str, gifts_text)

    card = (
        f"👤 <b>Аккаунт Telegram</b>\n"
        f"├ ID: <code>{user_id}</code>\n"
        f"├ Username: {username_str}\n"
        f"└ Регистрация: {reg_date}\n\n"
        f"📞 <b>Телефон:</b> <code>{phone_str}</code>\n"
    )
    if gifts_text != "Нет":
        card += f"\n🎁 <b>Подарки:</b>\n<blockquote>{gifts_text}</blockquote>"

    await status.edit_text(card, parse_mode=enums.ParseMode.HTML)

async def main():
    init_cache()
    await bot_app.start()
    await user_app.start()
    print("🤖 Бот и Юзербот запущены!")
    await idle()
    await bot_app.stop()
    await user_app.stop()

if __name__ == "__main__":
    asyncio.run(main())
