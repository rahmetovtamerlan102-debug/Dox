import os
import requests
import sqlite3
import asyncio
import threading
import html
import re
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
OSINT_BOT_USERNAME = "@evenfa_bot" 

LOCAL_DB = {
    100000000: "2013-08", 200000000: "2014-01", 300000000: "2014-07",
    400000000: "2014-12", 500000000: "2015-04", 700000000: "2015-10",
    1000000000: "2016-04", 1500000000: "2017-01", 2000000000: "2017-07",
    3000000000: "2018-06", 4000000000: "2019-04", 5000000000: "2020-01",
    6000000000: "2021-02", 7000000000: "2022-06", 7500000000: "2023-05",
    8000000000: "2024-06", 8500000000: "2025-01",
}
MONTHS_RU = {1: "январь", 2: "февраль", 3: "март", 4: "апрель", 5: "май", 6: "июнь", 7: "июль", 8: "август", 9: "сентябрь", 10: "октябрь", 11: "ноябрь", 12: "декабрь"}

# Очередь для сообщений от @evenfa_bot
osint_queue = asyncio.Queue()

# ==========================================
# 4. КЭШ
# ==========================================
def init_cache():
    try:
        conn = sqlite3.connect(CACHE_DB)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS cache (
            user_id INTEGER PRIMARY KEY, username TEXT, reg_date TEXT, phones TEXT, gifts TEXT, nfts TEXT, dc_id INTEGER, interests TEXT
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
        c.execute("SELECT username, reg_date, phones, gifts, nfts, dc_id, interests FROM cache WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        conn.close()
        return row
    except:
        return None

def set_cache(user_id, username, reg_date, phones, gifts, nfts, dc_id, interests):
    try:
        conn = sqlite3.connect(CACHE_DB)
        c = conn.cursor()
        c.execute('''INSERT OR REPLACE INTO cache (user_id, username, reg_date, phones, gifts, nfts, dc_id, interests) 
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', (user_id, username, reg_date, phones, gifts, nfts, dc_id, interests))
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
# 6. КЛИЕНТЫ
# ==========================================
bot_app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
user_app = Client("my_userbot", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING, in_memory=True)

# Глобальный обработчик всех сообщений от @evenfa_bot
@user_app.on_message(filters.chat(OSINT_BOT_USERNAME) & filters.incoming)
async def osint_handler(client, message):
    await osint_queue.put(message)

# ==========================================
# 7. ФУНКЦИЯ ПОЛУЧЕНИЯ ИНТЕРЕСОВ (ЧАСТОТА СЛОВ / ИСТОРИЯ)
# ==========================================
async def get_interests_from_bot(target):
    if not OSINT_BOT_USERNAME: return None
    
    while not osint_queue.empty():
        osint_queue.get_nowait()
        
    try:
        print(f"📤 Отправляю '{target}' боту {OSINT_BOT_USERNAME}...", flush=True)
        await user_app.send_message(OSINT_BOT_USERNAME, str(target))
        
        profile_msg = None
        for _ in range(4):
            try:
                msg = await asyncio.wait_for(osint_queue.get(), timeout=5.0)
                if msg.reply_markup:
                    profile_msg = msg
                    break
            except asyncio.TimeoutError:
                pass

        if not profile_msg:
            print("⚠️ @evenfa_bot не прислал меню с кнопками", flush=True)
            return None

        print("✅ Меню получено!", flush=True)

        async def click_and_wait(msg, keyword_list, step_name):
            target_btn = None
            if msg.reply_markup and msg.reply_markup.inline_keyboard:
                for row in msg.reply_markup.inline_keyboard:
                    for btn in row:
                        text_lower = btn.text.lower()
                        if any(kw in btn.text or kw in text_lower for kw in keyword_list):
                            target_btn = btn
                            break
                    if target_btn: break
            
            if not target_btn and msg.reply_markup.inline_keyboard:
                target_btn = msg.reply_markup.inline_keyboard[0][0]
                print(f"⚠️ {step_name}: Не нашли по тексту, жмем на первую", flush=True)

            if target_btn:
                print(f"👆 {step_name}: Нажимаем на '{target_btn.text}'", flush=True)
                await user_app.send_callback_query(chat_id=msg.chat.id, message_id=msg.id, data=target_btn.callback_data)
                
                for _ in range(4):
                    try:
                        res_msg = await asyncio.wait_for(osint_queue.get(), timeout=5.0)
                        if res_msg.text:
                            print(f"✅ {step_name}: Получен текст!", flush=True)
                            return res_msg.text
                        elif res_msg.caption:
                            return res_msg.caption
                    except asyncio.TimeoutError:
                        pass
                print(f"⚠️ {step_name}: Бот не ответил", flush=True)
            return None

        # --- ШАГ 1: ЧАСТОТА СЛОВ ---
        text_result = await click_and_wait(profile_msg, ["🗣️", "част", "слов"], "Частота слов")
        
        # --- ШАГ 2: ИСТОРИЯ ИЗМЕНЕНИЙ (если частоты нет) ---
        if not text_result or "не найден" in text_result.lower() or "нет данных" in text_result.lower():
            print("🔄 Частота слов недоступна. Пробуем Историю изменений...", flush=True)
            
            await user_app.send_message(OSINT_BOT_USERNAME, str(target))
            new_profile_msg = None
            for _ in range(4):
                try:
                    msg = await asyncio.wait_for(osint_queue.get(), timeout=5.0)
                    if msg.reply_markup:
                        new_profile_msg = msg
                        break
                except asyncio.TimeoutError:
                    pass

            if new_profile_msg:
                text_result = await click_and_wait(new_profile_msg, ["истор", "измен", "имя", "ник"], "История изменений")

        # --- ШАГ 3: ПАРСИМ ПОЛУЧЕННЫЙ ТЕКСТ ---
        if text_result:
            # Извлекаем юзернеймы (все, что начинается с @)
            usernames = re.findall(r'@\w+', text_result)
            usernames = list(dict.fromkeys(usernames)) # Удаляем дубликаты
            
            # Извлекаем имена (строки после даты и стрелки ➜)
            names = re.findall(r'\d{4}-\d{2}-\d{2}\s*➜\s*(.+)', text_result)
            
            output_lines = []
            if names:
                # Берем первые 5 имен
                output_lines.append(f"├ Имена: {', '.join(names[:5])}")
            if usernames:
                # Берем первые 5 ников
                output_lines.append(f"└ Юзернеймы: {', '.join(usernames[:5])}")
                
            if output_lines:
                return "\n".join(output_lines)

        return None
            
    except Exception as e:
        print(f"⚠️ Ошибка @evenfa_bot: {e}", flush=True)
    return None

# ==========================================
# 8. ОСТАЛЬНАЯ ЛОГИКА
# ==========================================
request_user_obj = RequestPeerTypeUser(button_id=1, is_bot=False, max=1)
reply_keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton("Выбрать пользователя", request_user=request_user_obj)]], resize_keyboard=True)

@bot_app.on_message(filters.command("start"))
async def start_command(client, message):
    await message.reply_text("⌬ Отправь мне ID, @username или контакт, и я покажу информацию.", reply_markup=reply_keyboard, parse_mode=enums.ParseMode.HTML)

@bot_app.on_message(filters.contact & filters.private)
async def handle_contact(client, message):
    user_id = message.contact.user_id
    if not user_id:
        return
    await process_user(client, message, user_id, original_input=user_id)

@bot_app.on_message(filters.private)
async def handle_user_shared(client, message):
    shared = getattr(message, "chats_shared", None) or getattr(message, "users_shared", None)
    if shared and getattr(shared, "users", None):
        for item in shared.users:
            user_id = getattr(item, "user_id", None)
            if user_id:
                await process_user(client, message, user_id, original_input=user_id)
        return
    if message.text:
        await search_handler(client, message)

async def search_handler(client, message):
    query = message.text.strip()
    status = await message.reply_text("✧ <b>Обрабатываю запрос...</b>", parse_mode=enums.ParseMode.HTML)

    if query.isdigit():
        await process_user(client, message, int(query), status, original_input=query)
    else:
        username = query if query.startswith('@') else f"@{query}"
        try:
            target_user = await user_app.get_users(username)
            await process_user(client, message, target_user.id, status, original_input=username)
        except Exception as e:
            print(f"⚠️ Ошибка поиска: {e}", flush=True)
            await status.edit_text("✖ <b>Ошибка:</b> Юзернейм не найден.", parse_mode=enums.ParseMode.HTML)

async def process_user(client, message, user_id, status=None, original_input=None):
    if not status:
        status = await message.reply_text("✧ <b>Собираю данные...</b>", parse_mode=enums.ParseMode.HTML)

    user_obj = None
    try:
        user_obj = await user_app.get_users(user_id)
    except: pass

    # ПРОВЕРКА КЭША
    cached_data = get_cache(user_id)
    if cached_data:
        cached_user, reg_date, cached_phones, cached_gifts, cached_nfts, dc_id, cached_interests = cached_data
        
        if not cached_interests:
            print(f"🔄 В кэше нет интересов. Парсим...", flush=True)
            cached_interests = await get_interests_from_bot(original_input or user_id)
            set_cache(user_id, cached_user, reg_date, cached_phones, cached_gifts, cached_nfts, dc_id, cached_interests or "")

        card = f"⌬ <b>Аккаунт Telegram</b> [КЭШ]\n├ ID: <code>{user_id}</code>\n"
        if cached_user and cached_user != "Скрыт": card += f"├ Username: {cached_user}\n"
        if dc_id and dc_id != "Неизвестно": card += f"├ Регистрация: {reg_date}\n└ DC: {dc_id}\n"
        else: card += f"└ Регистрация: {reg_date}\n"
        
        if cached_phones and cached_phones != "Не найден":
            card += f"\n✆ <b>Телефон:</b> <code>{cached_phones}</code>\n"
            d = get_phone_details(cached_phones)
            d_lines = [f"Оператор: {d['operator']}" for _ in [1] if d['operator']] + [f"Страна: {d['country']}" for _ in [1] if d['country']]
            for i, line in enumerate(d_lines): card += f"{'└ ' if i == len(d_lines)-1 else '├ '}{line}\n"
            
        if cached_nfts and cached_nfts != "Нет": card += f"\n✦ <b>NFT ({len(cached_nfts.split(', '))})</b>\n<blockquote>{cached_nfts}</blockquote>\n"
        if cached_gifts and cached_gifts != "Нет": card += f"\n✾ <b>Подарки:</b>\n<blockquote>{cached_gifts}</blockquote>"
        if cached_interests: card += f"\n🧠 <b>Интересы / История:</b>\n<blockquote>{html.escape(cached_interests[:300])}</blockquote>"
            
        await status.edit_text(card, parse_mode=enums.ParseMode.HTML)
        return

    # ЕСЛИ В КЭШЕ НЕТ
    username_str = f"@{user_obj.username}" if user_obj and user_obj.username else "Скрыт"
    dc_id = user_obj.dc_id if user_obj and user_obj.dc_id else "Неизвестно"

    est_y, est_m = estimate_registration(user_id)
    reg_date = f"~ {MONTHS_RU.get(est_m, '')} {est_y} {get_time_passed(est_y, est_m)}" if est_y else "Неизвестно"

    phone_str = get_phones_from_api(user_id)

    gifts_lines, nft_lines = [], []
    try:
        peer = await user_app.resolve_peer(username_str if username_str != "Скрыт" else user_id)
        result = await user_app.invoke(GetSavedStarGifts(peer=peer, offset="", limit=100))
        if result.gifts:
            for g in result.gifts:
                gift = getattr(g, "gift", None)
                if getattr(gift, "slug", None):
                    nft_lines.append(f"{gift.title} #{gift.num}")
                elif not getattr(g, 'name_hidden', False) and g.from_id:
                    sid = getattr(g.from_id, 'user_id', None)
                    if sid: gifts_lines.append(f"<code>{sid}</code>")
    except Exception as e: print(f"❌ Ошибка подарков: {e}", flush=True)

    gifts_text = ", ".join(gifts_lines) if gifts_lines else "Нет"
    nfts_text = ", ".join(nft_lines) if nft_lines else "Нет"
    
    print(f"🔄 Запускаю парсинг интересов/истории...", flush=True)
    interests_text = await get_interests_from_bot(original_input or user_id)
    set_cache(user_id, username_str, reg_date, phone_str or "", gifts_text, nfts_text, dc_id, interests_text or "")

    card = f"⌬ <b>Аккаунт Telegram</b>\n├ ID: <code>{user_id}</code>\n"
    if username_str != "Скрыт": card += f"├ Username: {username_str}\n"
    if dc_id != "Неизвестно": card += f"├ Регистрация: {reg_date}\n└ DC: {dc_id}\n"
    else: card += f"└ Регистрация: {reg_date}\n"
    
    if phone_str:
        card += f"\n✆ <b>Телефон:</b> <code>{phone_str}</code>\n"
        d = get_phone_details(phone_str)
        d_lines = [f"Оператор: {d['operator']}" for _ in [1] if d['operator']] + [f"Страна: {d['country']}" for _ in [1] if d['country']]
        for i, line in enumerate(d_lines): card += f"{'└ ' if i == len(d_lines)-1 else '├ '}{line}\n"

    if nfts_text != "Нет": card += f"\n✦ <b>NFT ({len(nft_lines)})</b>\n<blockquote>{nfts_text}</blockquote>\n"
    if gifts_text != "Нет": card += f"\n✾ <b>Подарки:</b>\n<blockquote>{gifts_text}</blockquote>"
    if interests_text: card += f"\n🧠 <b>Интересы / История:</b>\n<blockquote>{html.escape(interests_text[:300])}</blockquote>"

    await status.edit_text(card, parse_mode=enums.ParseMode.HTML)

async def main():
    init_cache()
    threading.Thread(target=run_web_server, daemon=True).start()
    try:
        print("🤖 Запускаю бота...", flush=True)
        await bot_app.start()
        print("👤 Запускаю юзербота...", flush=True)
        await user_app.start()
        print("🚀 Всё готово!", flush=True)
    except Exception as e:
        print(f"❌ ОШИБКА: {e}", flush=True)
        return
    await idle()
    await bot_app.stop()
    await user_app.stop()

if __name__ == "__main__":
    loop.run_until_complete(main())
