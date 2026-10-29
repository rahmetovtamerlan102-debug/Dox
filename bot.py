import os
import requests
import sqlite3
import asyncio
import threading
import phonenumbers
from phonenumbers import carrier
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime
from pyrogram import Client, filters, enums, idle
from pyrogram.raw.functions.payments import GetSavedStarGifts
from pyrogram.types import ReplyKeyboardMarkup, KeyboardButton, RequestPeerTypeUser

# ==========================================
# 1. ИСПРАВЛЕНИЕ ЦИКЛА СОБЫТИЙ
# ==========================================
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

# ==========================================
# 2. ВЕБ-СЕРВЕР ДЛЯ RENDER
# ==========================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    print(f"🌐 Веб-сервер слушает порт {port}", flush=True)
    server.serve_forever()

# ==========================================
# 3. КОНФИГУРАЦИЯ
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
# 4. КЭШ
# ==========================================
def init_cache():
    try:
        conn = sqlite3.connect(CACHE_DB)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS cache (
            user_id INTEGER PRIMARY KEY, username TEXT, reg_date TEXT, phones TEXT, gifts TEXT, nfts TEXT, dc_id INTEGER
        )''')
        conn.commit()
        conn.close()
        print("✅ Кэш инициализирован", flush=True)
    except Exception as e:
        print(f"⚠️ Ошибка кэша: {e}", flush=True)

def get_cache(user_id):
    try:
        conn = sqlite3.connect(CACHE_DB)
        c = conn.cursor()
        c.execute("SELECT username, reg_date, phones, gifts, nfts, dc_id FROM cache WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        conn.close()
        return row
    except:
        return None

def set_cache(user_id, username, reg_date, phones, gifts, nfts, dc_id):
    try:
        conn = sqlite3.connect(CACHE_DB)
        c = conn.cursor()
        c.execute('''INSERT OR REPLACE INTO cache (user_id, username, reg_date, phones, gifts, nfts, dc_id) 
                     VALUES (?, ?, ?, ?, ?, ?, ?)''', (user_id, username, reg_date, phones, gifts, nfts, dc_id))
        conn.commit()
        conn.close()
    except:
        pass

# ==========================================
# 5. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
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
        response = requests.get(f"{API_URL}/get_phone", params={"id": user_id}, timeout=10)
        if response.status_code == 200:
            phones = response.json().get("phones", [])
            return ", ".join(phones) if phones else None
    except Exception as e:
        print(f"⚠️ Ошибка API: {e}", flush=True)
    return None

def get_phone_details(phone_str):
    details = {"operator": None, "country": None}
    if not phone_str: return details

    try:
        parsed = phonenumbers.parse(phone_str, "RU")
        details["operator"] = carrier.name_for_number(parsed, "ru")
        
        region_code = phonenumbers.region_code_for_number(parsed)
        country_map = {
            "RU": "Россия", "KZ": "Казахстан", "UA": "Украина", "BY": "Беларусь",
            "UZ": "Узбекистан", "KG": "Кыргызстан", "TJ": "Таджикистан", "AM": "Армения",
            "GE": "Грузия", "AZ": "Азербайджан", "MD": "Молдова", "LV": "Латвия",
            "LT": "Литва", "EE": "Эстония", "US": "США", "GB": "Великобритания",
            "DE": "Германия", "FR": "Франция", "TR": "Турция", "AE": "ОАЭ"
        }
        details["country"] = country_map.get(region_code, region_code)
    except Exception as e:
        print(f"⚠️ Ошибка phonenumbers: {e}", flush=True)

    return details

# ==========================================
# 6. КЛИЕНТЫ И КЛАВИАТУРА
# ==========================================
bot_app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
user_app = Client("my_userbot", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING, in_memory=True)

request_user_obj = RequestPeerTypeUser(
    button_id=1, 
    is_bot=False,
    max=1
)

reply_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("Выбрать пользователя", request_user=request_user_obj)]
    ],
    resize_keyboard=True
)

@bot_app.on_message(filters.command("start"))
async def start_command(client, message):
    await message.reply_text(
        "⌬ Отправь мне ID, @username или контакт, и я покажу информацию.",
        reply_markup=reply_keyboard,
        parse_mode=enums.ParseMode.HTML
    )

@bot_app.on_message(filters.contact & filters.private)
async def handle_contact(client, message):
    user_id = message.contact.user_id
    if not user_id:
        await message.reply_text("✖ <b>Ошибка:</b> Не удалось получить ID из этого контакта.", parse_mode=enums.ParseMode.HTML)
        return
    
    print(f"👤 Получен контакт: {user_id}", flush=True)
    await process_user(client, message, user_id)

@bot_app.on_message(filters.private)
async def handle_user_shared(client, message):
    shared = getattr(message, "chats_shared", None) or getattr(message, "users_shared", None)
    
    if shared and getattr(shared, "users", None):
        for item in shared.users:
            user_id = getattr(item, "user_id", None)
            if not user_id:
                continue
                
            print(f"👤 Выбран пользователь: {user_id}", flush=True)
            await process_user(client, message, user_id)
        return

    if message.text:
        await search_handler(client, message)

async def search_handler(client, message):
    query = message.text.strip()
    print(f"📥 Получен запрос: {query}", flush=True)
    
    status = await message.reply_text("✧ <b>Обрабатываю запрос...</b>", parse_mode=enums.ParseMode.HTML)

    if query.isdigit():
        user_id = int(query)
        await process_user(client, message, user_id, status)
    else:
        username = query if query.startswith('@') else f"@{query}"
        try:
            target_user = await user_app.get_users(username)
            user_id = target_user.id
            await process_user(client, message, user_id, status)
        except Exception as e:
            print(f"⚠️ Ошибка поиска юзернейма: {e}", flush=True)
            await status.edit_text("✖ <b>Ошибка:</b> Не удалось найти этот юзернейм. Попробуйте ввести ID.", parse_mode=enums.ParseMode.HTML)

# --- ОСНОВНАЯ ФУНКЦИЯ СБОРА ДАННЫХ ---
async def process_user(client, message, user_id, status=None):
    if not status:
        status = await message.reply_text("✧ <b>Собираю данные...</b>", parse_mode=enums.ParseMode.HTML)

    user_obj = None
    try:
        user_obj = await user_app.get_users(user_id)
    except Exception as e:
        print(f"⚠️ Ошибка get_users: {e}", flush=True)

    cached_data = get_cache(user_id)
    if cached_data:
        cached_user, reg_date, cached_phones, cached_gifts, cached_nfts, dc_id = cached_data
        
        if (cached_phones and cached_phones != "Не найден") or (cached_gifts and cached_gifts != "Нет") or (cached_nfts and cached_nfts != "Нет"):
            
            card = f"⌬ <b>Аккаунт Telegram</b> [КЭШ]\n"
            card += f"├ ID: <code>{user_id}</code>\n"
            if cached_user and cached_user != "Скрыт":
                card += f"├ Username: {cached_user}\n"
            
            if dc_id and dc_id != "Неизвестно":
                card += f"├ Регистрация: {reg_date}\n"
                card += f"└ DC: {dc_id}\n"
            else:
                card += f"└ Регистрация: {reg_date}\n"
            
            # Телефон
            if cached_phones and cached_phones != "Не найден":
                card += f"\n✆ <b>Телефон:</b> <code>{cached_phones}</code>\n"
                details = get_phone_details(cached_phones)
                d_lines = []
                if details["operator"]: d_lines.append(f"Оператор: {details['operator']}")
                if details["country"]: d_lines.append(f"Страна: {details['country']}")
                
                for i, line in enumerate(d_lines):
                    prefix = "└ " if i == len(d_lines) - 1 else "├ "
                    card += f"{prefix}{line}\n"
                
            # NFT (СВЕРХУ)
            if cached_nfts and cached_nfts != "Нет":
                card += f"\n✦ <b>NFT ({len(cached_nfts.split(', '))})</b>\n<blockquote>{cached_nfts}</blockquote>\n"
            
            # Подарки (ВНИЗУ)
            if cached_gifts and cached_gifts != "Нет":
                card += f"\n✾ <b>Подарки:</b>\n<blockquote>{cached_gifts}</blockquote>"
                
            await status.edit_text(card, parse_mode=enums.ParseMode.HTML)
            return

    # --- ЕСЛИ В КЭШЕ НЕТ ---
    username_str = "Скрыт"
    dc_id = "Неизвестно"
    
    if user_obj:
        if user_obj.username: username_str = f"@{user_obj.username}"
        if user_obj.dc_id: dc_id = user_obj.dc_id

    est_y, est_m = estimate_registration(user_id)
    reg_date = f"~ {MONTHS_RU.get(est_m, '')} {est_y} {get_time_passed(est_y, est_m)}" if est_y else "Неизвестно"

    phones = get_phones_from_api(user_id)
    phone_str = phones if phones else None

    # --- СБОР ПОДАРКОВ ---
    gifts_lines = []
    nft_lines = []
    try:
        target = username_str if username_str != "Скрыт" else user_id
        peer = await user_app.resolve_peer(target)
        
        result = await user_app.invoke(GetSavedStarGifts(peer=peer, offset="", limit=100))
        
        if result.gifts:
            for g in result.gifts:
                gift = getattr(g, "gift", None)
                title = getattr(gift, "title", None)
                slug = getattr(gift, "slug", None)
                num = getattr(gift, "num", None)
                
                if slug:
                    nft_lines.append(f"{title} #{num}")
                else:
                    if not getattr(g, 'name_hidden', False) and g.from_id:
                        sender_id = getattr(g.from_id, 'user_id', None)
                        if sender_id:
                            gifts_lines.append(f"<code>{sender_id}</code>")
    except Exception as e:
        print(f"❌ Ошибка подарков: {e}", flush=True)

    gifts_text = ", ".join(gifts_lines) if gifts_lines else "Нет"
    nfts_text = ", ".join(nft_lines) if nft_lines else "Нет"
    
    set_cache(user_id, username_str, reg_date, phone_str or "", gifts_text, nfts_text, dc_id)

    # Формируем итоговую карточку
    card = f"⌬ <b>Аккаунт Telegram</b>\n"
    card += f"├ ID: <code>{user_id}</code>\n"
    if username_str != "Скрыт":
        card += f"├ Username: {username_str}\n"
    
    if dc_id != "Неизвестно":
        card += f"├ Регистрация: {reg_date}\n"
        card += f"└ DC: {dc_id}\n"
    else:
        card += f"└ Регистрация: {reg_date}\n"
    
    # Телефон
    if phone_str:
        card += f"\n✆ <b>Телефон:</b> <code>{phone_str}</code>\n"
        details = get_phone_details(phone_str)
        d_lines = []
        if details["operator"]: d_lines.append(f"Оператор: {details['operator']}")
        if details["country"]: d_lines.append(f"Страна: {details['country']}")
        
        for i, line in enumerate(d_lines):
            prefix = "└ " if i == len(d_lines) - 1 else "├ "
            card += f"{prefix}{line}\n"

    # NFT (СВЕРХУ)
    if nfts_text != "Нет":
        card += f"\n✦ <b>NFT ({len(nft_lines)})</b>\n<blockquote>{nfts_text}</blockquote>\n"

    # Подарки (ВНИЗУ)
    if gifts_text != "Нет":
        card += f"\n✾ <b>Подарки:</b>\n<blockquote>{gifts_text}</blockquote>"

    await status.edit_text(card, parse_mode=enums.ParseMode.HTML)

async def main():
    init_cache()
    threading.Thread(target=run_web_server, daemon=True).start()
    
    try:
        print("🤖 Запускаю бота...", flush=True)
        await bot_app.start()
        print("✅ Бот запущен", flush=True)
        print("👤 Запускаю юзербота...", flush=True)
        await user_app.start()
        print("✅ Юзербот запущен", flush=True)
        print("🚀 Всё готово!", flush=True)
    except Exception as e:
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА: {e}", flush=True)
        return
    
    await idle()
    await bot_app.stop()
    await user_app.stop()

if __name__ == "__main__":
    loop.run_until_complete(main())
