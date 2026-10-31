# pip install pyrofork tgcrypto requests phonenumbers
import os
import requests
import sqlite3
import asyncio
import threading
import re
import phonenumbers
from phonenumbers import carrier
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, date
from pyrogram import Client, filters, enums, idle
from pyrogram.raw.functions.payments import GetSavedStarGifts
from pyrogram.raw.functions.users import GetFullUser
from pyrogram.types import ReplyKeyboardMarkup, KeyboardButton, RequestPeerTypeUser, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

# ==========================================
# КОНФИГУРАЦИЯ
# ==========================================
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")
API_URL = os.environ.get("API_URL", "")
FUNSTAT_TOKEN = os.environ.get("FUNSTAT_TOKEN", "")

ADMIN_ID = 5021557806
DAILY_LIMIT = 3

CACHE_DB = "cache.db"
USERS_DB = "users.db"
OSINT_BOT_USERNAME = "@evenfa_bot"

# ==========================================
# ВЕБ-СЕРВЕР ДЛЯ RENDER
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
# БАЗА ДАННЫХ
# ==========================================
def init_users_db():
    try:
        conn = sqlite3.connect(USERS_DB)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, 
            username TEXT, 
            first_seen DATE, 
            total_requests INTEGER DEFAULT 0,
            daily_requests INTEGER DEFAULT 0,
            last_request_date DATE
        )''')
        conn.commit()
        conn.close()
        print("✅ База юзеров инициализирована", flush=True)
    except Exception as e:
        print(f"⚠️ Ошибка БД: {e}", flush=True)

def check_limit_status(user_id):
    if user_id == ADMIN_ID: return True, 999 
    today = date.today().isoformat()
    try:
        conn = sqlite3.connect(USERS_DB)
        c = conn.cursor()
        c.execute("SELECT daily_requests, last_request_date FROM users WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        conn.close()
        if not row: return True, DAILY_LIMIT
        daily_req, last_date = row
        if last_date != today: return True, DAILY_LIMIT
        if daily_req >= DAILY_LIMIT: return False, 0
        return True, DAILY_LIMIT - daily_req
    except: return True, DAILY_LIMIT

def deduct_limit(user_id, username=""):
    if user_id == ADMIN_ID: return
    today = date.today().isoformat()
    try:
        conn = sqlite3.connect(USERS_DB)
        c = conn.cursor()
        c.execute("SELECT daily_requests, last_request_date FROM users WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        if not row:
            c.execute("INSERT INTO users (user_id, username, first_seen, total_requests, daily_requests, last_request_date) VALUES (?, ?, ?, 1, 1, ?)", (user_id, username, today, today))
        else:
            daily_req, last_date = row
            if last_date != today:
                c.execute("UPDATE users SET daily_requests = 1, total_requests = total_requests + 1, last_request_date = ? WHERE user_id = ?", (today, user_id))
            else:
                c.execute("UPDATE users SET daily_requests = daily_requests + 1, total_requests = total_requests + 1 WHERE user_id = ?", (user_id,))
        conn.commit(); conn.close()
    except Exception as e: print(f"⚠️ Ошибка БД (deduct): {e}")

def get_profile(user_id):
    try:
        conn = sqlite3.connect(USERS_DB)
        c = conn.cursor()
        c.execute("SELECT first_seen, total_requests, daily_requests, last_request_date FROM users WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        conn.close()
        if row: return row[0], row[1], row[2] if row[3] == date.today().isoformat() else 0
    except: pass
    return date.today().isoformat(), 0, 0

def get_admin_stats():
    try:
        conn = sqlite3.connect(USERS_DB)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        total_users = c.fetchone()[0]
        c.execute("SELECT SUM(total_requests) FROM users")
        total_reqs = c.fetchone()[0] or 0
        conn.close()
        return total_users, total_reqs
    except: return 0, 0

# ==========================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================
def format_days(days):
    if days % 10 == 1 and days % 100 != 11: return f"{days} день"
    elif 2 <= days % 10 <= 4 and not (12 <= days % 100 <= 14): return f"{days} дня"
    else: return f"{days} дней"

def format_date_short(date_str):
    if not date_str: return None
    try:
        parts = str(date_str).split("-")
        if len(parts) == 3: return f"{parts[2]}.{parts[1]}.{parts[0]}"
        return date_str
    except: return date_str

MONTHS_RU = {1: "январь", 2: "февраль", 3: "март", 4: "апрель", 5: "май", 6: "июнь", 7: "июль", 8: "август", 9: "сентябрь", 10: "октябрь", 11: "ноябрь", 12: "декабрь"}
MONTHS_SHORT = {"01": "янв", "02": "фев", "03": "мар", "04": "апр", "05": "мая", "06": "июн", "07": "июл", "08": "авг", "09": "сен", "10": "окт", "11": "ноя", "12": "дек"}

def get_time_passed(year, month):
    now = datetime.now()
    reg_date = datetime(year, month, 1)
    years = now.year - reg_date.year
    months = now.month - reg_date.month
    if months < 0: years -= 1; months += 12
    y_str = f"{years} год" if years % 10 == 1 and years % 100 != 11 else f"{years} года" if 2 <= years % 10 <= 4 and not (12 <= years % 100 <= 14) else f"{years} лет"
    if years == 0: y_str = ""
    m_str = f"{months} мес" if months > 0 else ""
    return f"({y_str} {m_str})".strip()

LOCAL_DB = {
    100000000: "2013-08", 200000000: "2014-01", 300000000: "2014-07", 400000000: "2014-12", 500000000: "2015-04", 700000000: "2015-10",
    1000000000: "2016-04", 1500000000: "2017-01", 2000000000: "2017-07", 3000000000: "2018-06", 4000000000: "2019-04", 5000000000: "2020-01",
    6000000000: "2021-02", 7000000000: "2022-06", 7500000000: "2023-05", 8000000000: "2024-06", 8500000000: "2025-01",
}

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
    except Exception as e: print(f"⚠️ Ошибка API: {e}", flush=True)
    return None

def get_phone_details(phone_str):
    details = {"operator": None, "country": None}
    if not phone_str: return details
    try:
        first_phone = phone_str.split(",")[0].strip()
        parsed = phonenumbers.parse(first_phone, "RU")
        details["operator"] = carrier.name_for_number(parsed, "ru")
        region_code = phonenumbers.region_code_for_number(parsed)
        country_map = {"RU": "Россия", "KZ": "Казахстан", "UA": "Украина", "BY": "Беларусь", "UZ": "Узбекистан", "KG": "Кыргызстан", "TJ": "Таджикистан", "AM": "Армения", "GE": "Грузия", "AZ": "Азербайджан", "MD": "Молдова", "LV": "Латвия", "LT": "Литва", "EE": "Эстония", "US": "США", "GB": "Великобритания", "DE": "Германия", "FR": "Франция", "TR": "Турция", "AE": "ОАЭ"}
        details["country"] = country_map.get(region_code, region_code)
    except Exception as e: print(f"⚠️ Ошибка phonenumbers: {e}", flush=True)
    return details

def get_funstat_activity(user_id):
    if not FUNSTAT_TOKEN: return None
    url = f"https://telelog.org/api/v1/users/{user_id}/stats_min"
    headers = {"Authorization": f"Bearer {FUNSTAT_TOKEN}", "accept": "application/json"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            lines = []
            if data.get('total_msg_count') is not None: lines.append(f"Всего сообщений: {data.get('total_msg_count')}")
            if data.get('total_groups') is not None: lines.append(f"Всего чатов: {data.get('total_groups')}")
            if data.get('reactions_count') is not None: lines.append(f"Получено реакций: {data.get('reactions_count')}")
            first_date = data.get("first_msg_date")
            last_date = data.get("last_msg_date")
            if first_date: lines.append(f"Первое появление:\n{first_date.replace('T', ' ').replace('Z', '')}")
            if last_date: lines.append(f"Последнее появление:\n{last_date.replace('T', ' ').replace('Z', '')}")
            if lines: return f"<blockquote>{chr(10).join(lines)}</blockquote>"
    except Exception as e: print(f"⚠️ Ошибка FunStat: {e}", flush=True)
    return None

# ==========================================
# ПАРСИНГ ОТ @evenfa_bot
# ==========================================
osint_event = asyncio.Event()
last_osint_msg = None
osint_lock = asyncio.Lock()

def get_btn(message, keywords):
    if not message or not message.reply_markup: return None
    for row in message.reply_markup.inline_keyboard:
        for btn in row:
            if any(k in str(btn.text).lower() for k in keywords): return btn
    return None

async def parse_given_gifts(message):
    if not message: return ""
    raw_text = ""
    if getattr(message, "text", None): raw_text = str(message.text)
    elif getattr(message, "caption", None): raw_text = str(message.caption)
    if not raw_text or "evenfa_bot?start=" in raw_text: return ""
    clean_text = re.sub(r'<[^>]+>', '', raw_text)
    clean_text = clean_text.replace("Аккаунт", "").replace("Режим", "").replace("Подарен", "").strip()
    out = []
    for un in re.findall(r'@([a-zA-Z0-9_]{4,32})', clean_text):
        try:
            user = await user_app.get_users(un)
            out.append(f"<code>{user.id}</code>")
        except: pass
    return ", ".join(list(dict.fromkeys(out)))

def _full_normalize(s):
    table = {'a':'а','b':'в','c':'с','d':'д','e':'е','f':'ф','g':'г','h':'н','i':'и','j':'й','k':'к','l':'л','m':'м','n':'п','o':'о','p':'п','q':'к','r':'р','s':'с','t':'т','u':'и','v':'в','w':'в','x':'х','y':'у','z':'з','α':'а','β':'в','γ':'у','δ':'д','ε':'е','ζ':'з','η':'н','θ':'т','ι':'и','κ':'к','λ':'л','μ':'м','ν':'в','ξ':'х','ο':'о','π':'п','ρ':'р','σ':'с','ς':'с','τ':'т','υ':'и','φ':'ф','χ':'х','ψ':'п','ω':'о','ց':'г','ᴜ':'и','ᴠ':'в','ᴧ':'л','ᴨ':'п','ᴦ':'р','ᴋ':'к','ᴍ':'м','ᴏ':'о','ᴘ':'п','ᴛ':'т','ᴅ':'д','ᴢ':'з','ᴊ':'й','ᴡ':'в','ʏ':'у','ɪ':'и','ᴎ':'н','ᴓ':'о','Ⅼ':'л','Ⅽ':'с','Ⅾ':'д','Ⅿ':'м','ⅰ':'и','ⅼ':'л','ⅽ':'с','ⅾ':'д','ⅿ':'м','℮':'е','ƒ':'ф','ł':'л','қ':'к','ѕ':'с','і':'и','ї':'и','ў':'у','ј':'й'}
    return str(s or "").translate(str.maketrans(table)).lower()

def _parse_words_response(text):
    if not text: return []
    words = []
    for line in str(text).split("\n"):
        m = re.match(r'[├|└]\s*\*?\*?(\d+)\s*[-–]\s*(\d+)\*?\*?\s*[\s]*([^\n]+)', line.strip())
        if m:
            try: words.append({"word": re.sub(r'[^\w\-]+', '', w, flags=re.UNICODE).strip(), "avg": (int(m.group(1)) + int(m.group(2))) // 2})
            except: pass
    words.sort(key=lambda x: x["avg"], reverse=True)
    return words

def _detect_interests_from_words(words, max_results=3):
    if not words: return []
    all_text = " ".join(_full_normalize(w.get("word", "")) for w in words)
    KEYWORDS = {"Гейминг": ["килл","kill","чит","майн","csgo","dota","pubg","gta","roblox","steam","танки"], "Маты/Сленг": ["ебан","пизд","хуй","бляд","мраз","долбо","нахуй","залуп","мудак","кринж","кек"], "Криптовалюты": ["битк","bitcoin","eth","эфир","токен","крипт","crypto","usdt","ton","sol","binance","defi","nft","web3"], "Инвестиции": ["трейд","trade","форекс","инвест","invest","брокер","акци","биржа"], "Программирование": ["python","питон","код","code","программ","разраб","developer","javascript","backend","frontend","sql","docker","github"], "Общение": ["общение", "чат", "меню", "раздача", "состав", "привет", "как дела", "братан", "жека", "димас", "долг", "пацаны"]}
    res = []
    for cat, kws in KEYWORDS.items():
        hits = sum(1 for kw in kws if _full_normalize(kw) in all_text)
        if hits > 0: res.append((cat, hits))
    res.sort(key=lambda x: x[1], reverse=True)
    return [r[0] for r in res[:max_results]] if res else ["Общение"]

async def get_interests_from_bot(target, premium_until=None):
    global last_osint_msg, osint_event
    if not OSINT_BOT_USERNAME: return "", "", "", ""
    async with osint_lock:
        last_osint_msg = None; osint_event.clear()
        try:
            await user_app.send_message(OSINT_BOT_USERNAME, str(target))
            collected_messages = {}; profile_msg = None; idle_time = 0
            for _ in range(30):
                try:
                    await asyncio.wait_for(osint_event.wait(), timeout=1.0); osint_event.clear(); idle_time = 0
                    if last_osint_msg:
                        collected_messages[last_osint_msg.id] = last_osint_msg
                        if last_osint_msg.reply_markup: profile_msg = last_osint_msg
                except asyncio.TimeoutError:
                    if collected_messages:
                        idle_time += 1
                        # Увеличили до 5 секунд, чтобы бот успел прислать вторую часть (историю/кружки)
                        if idle_time >= 5: 
                            break

            history_text_raw = ""
            for msg in collected_messages.values():
                if msg.text: history_text_raw += msg.text + "\n"
                elif msg.caption: history_text_raw += msg.caption + "\n"

            print(f"📄 RAW TEXT FROM BOT:\n{history_text_raw}\n-------------------", flush=True)

            chat_info_text = ""; history_text = ""; interests_text = ""; sent_gifts_text = ""

            if history_text_raw:
                norm_text = _full_normalize(history_text_raw)
                extra_lines = []
                fav_idx = norm_text.find("любимый чат")
                if fav_idx == -1: fav_idx = norm_text.find("любимый ч")
                if fav_idx != -1:
                    colon_idx = norm_text.find(":", fav_idx)
                    if colon_idx != -1:
                        end_idx = norm_text.find("\n", colon_idx)
                        if end_idx == -1: end_idx = len(norm_text)
                        raw_fav = history_text_raw[colon_idx+1:end_idx].strip()
                        norm_fav = norm_text[colon_idx+1:end_idx].strip()
                        if norm_fav and not re.search(r'(?:искали|поиск|запрос|none|null)', norm_fav):
                            clean_fav = re.sub(r'^[:\-\s]+', '', raw_fav).strip()
                            if clean_fav: extra_lines.append(f"Любимый чат: {clean_fav}")

                circ_match = re.search(r'(?:круж|кружк)[^\d]*(\d+)', norm_text)
                if circ_match: extra_lines.append(f"Кружочки: {circ_match.group(1)}")
                voice_match = re.search(r'голос[^\d]*(\d+)', norm_text)
                if voice_match: extra_lines.append(f"Голосовые: {voice_match.group(1)}")
                if premium_until: extra_lines.append(f"Premium до: {premium_until}")
                if extra_lines: chat_info_text = "❖ <b>Информация из чатов:</b>\n<blockquote>" + "\n".join(extra_lines) + "</blockquote>"

                names_matches = re.findall(r'(\d{4}-\d{2}-\d{2})\s*(?:➜|→|->|—|-)\s*([^\n]+)', history_text_raw)
                history_lines = []
                for match in names_matches:
                    clean_name = re.sub(r'[\(\[]@?\S+[\)\]];?', '', match[1].strip())
                    clean_name = re.sub(r'@\w+', '', clean_name).strip(', ')
                    date_str = format_date_short(match[0])
                    history_lines.append(f"{date_str} → {clean_name}" if date_str else clean_name)
                if history_lines: history_text = f"◈ <b>История профиля:</b>\n<blockquote>{chr(10).join(history_lines[:5])}</blockquote>"

            if profile_msg:
                target_btn = get_btn(profile_msg, ["част", "слов", "🗣"])
                if target_btn:
                    last_osint_msg = None; osint_event.clear()
                    try: await user_app.request_callback_answer(profile_msg.chat.id, profile_msg.id, target_btn.callback_data)
                    except: pass
                    for _ in range(30):
                        try:
                            await asyncio.wait_for(osint_event.wait(), timeout=1.0); osint_event.clear()
                            if last_osint_msg and last_osint_msg.text and re.search(r'[├|└]\s*\*?\*?\d+\s*[-–]\s*\d+', last_osint_msg.text):
                                cats = _detect_interests_from_words(_parse_words_response(last_osint_msg.text), 3)
                                if cats: interests_text = f"◉ <b>Интересы:</b>\n<blockquote>{chr(10).join(cats)}</blockquote>"
                                break
                        except asyncio.TimeoutError: pass

                gifts_btn = get_btn(profile_msg, ["подар", "🎁"])
                if gifts_btn:
                    last_osint_msg = None; osint_event.clear()
                    try: await asyncio.wait_for(user_app.request_callback_answer(profile_msg.chat.id, profile_msg.id, gifts_btn.callback_data), timeout=5.0)
                    except: pass
                    menu_msg = None
                    for _ in range(15):
                        try:
                            await asyncio.wait_for(osint_event.wait(), timeout=1.0); osint_event.clear()
                            if last_osint_msg and last_osint_msg.reply_markup: menu_msg = last_osint_msg; break
                        except asyncio.TimeoutError: pass
                    if menu_msg:
                        given_btn = get_btn(menu_msg, ["подарен", "отправ", "⇉", "=>"])
                        if given_btn:
                            last_osint_msg = None; osint_event.clear()
                            try: await asyncio.wait_for(user_app.request_callback_answer(menu_msg.chat.id, menu_msg.id, given_btn.callback_data), timeout=5.0)
                            except: pass
                            for _ in range(10):
                                try:
                                    await asyncio.wait_for(osint_event.wait(), timeout=1.5); osint_event.clear()
                                    if last_osint_msg:
                                        sent_gifts_text = await parse_given_gifts(last_osint_msg)
                                        if sent_gifts_text: break
                                except asyncio.TimeoutError: pass

            return chat_info_text, history_text, interests_text, sent_gifts_text
        except Exception as e: print(f"⚠️ Ошибка @evenfa_bot: {e}", flush=True)
        return "", "", "", ""

# ==========================================
# КЛИЕНТЫ
# ==========================================
bot_app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
user_app = Client("my_userbot", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING, in_memory=True)

@user_app.on_message(filters.chat(OSINT_BOT_USERNAME) & filters.incoming)
async def osint_handler(client, message):
    global last_osint_msg, osint_event
    last_osint_msg = message; osint_event.set()

@user_app.on_edited_message(filters.chat(OSINT_BOT_USERNAME))
async def osint_edit_handler(client, message):
    global last_osint_msg, osint_event
    last_osint_msg = message; osint_event.set()

# ==========================================
# ОСНОВНОЙ ОТЧЕТ ПОЛЬЗОВАТЕЛЯ
# ==========================================
async def process_user(client, message, user_id, status=None, original_input=None):
    if not status: status = await message.reply_text("✧ <b>Собираю данные...</b>", parse_mode=enums.ParseMode.HTML)
    user_obj = None
    try: user_obj = await user_app.get_users(user_id)
    except: pass

    premium_until = None
    if user_obj and user_obj.is_premium:
        try:
            full_info = await user_app.invoke(GetFullUser(id=await user_app.resolve_peer(user_id)))
            if getattr(full_info.full_user, 'premium_expiration_date', None):
                premium_until = datetime.fromtimestamp(full_info.full_user.premium_expiration_date).strftime("%d.%m.%Y")
        except: pass

    username_str = f"@{user_obj.username}" if user_obj and user_obj.username else "Скрыт"
    dc_id = user_obj.dc_id if user_obj and user_obj.dc_id else None
    est_y, est_m = estimate_registration(user_id)
    reg_date = f"~ {MONTHS_RU.get(est_m, '')} {est_y} {get_time_passed(est_y, est_m)}" if est_y else None
    phone_str = get_phones_from_api(user_id)

    gifts_lines, nft_lines = [], []
    try:
        peer = await user_app.resolve_peer(username_str if username_str != "Скрыт" else user_id)
        result = await user_app.invoke(GetSavedStarGifts(peer=peer, offset="", limit=100))
        if result.gifts:
            for g in result.gifts:
                gift = getattr(g, "gift", None)
                if getattr(gift, "slug", None): nft_lines.append(f"{gift.title} #{gift.num}")
                elif not getattr(g, 'name_hidden', False) and g.from_id:
                    sid = getattr(g.from_id, 'user_id', None)
                    if sid: gifts_lines.append(f"<code>{sid}</code>")
    except: pass

    gifts_lines = list(dict.fromkeys(gifts_lines))
    nfts_text = ", ".join(list(dict.fromkeys(nft_lines))) if nft_lines else "Нет"
    chat_info, history_text, interests_text, sent_gifts_text = await get_interests_from_bot(original_input or user_id, premium_until)

    header_lines = [f"ID: <code>{user_id}</code>"]
    if username_str != "Скрыт": header_lines.append(f"Username: {username_str}")
    if reg_date: header_lines.append(f"Регистрация: {reg_date}")
    if dc_id: header_lines.append(f"DC: {dc_id}")

    card = f"⌬ <b>Аккаунт Telegram</b>\n"
    for i, line in enumerate(header_lines):
        prefix = "└ " if i == len(header_lines) - 1 else "├ "
        card += f"{prefix}{line}\n"

    if phone_str:
        card += f"\n✆ <b>Телефон:</b> <code>{phone_str}</code>\n"
        d = get_phone_details(phone_str)
        phone_lines = []
        if d['operator']: phone_lines.append(f"Оператор: {d['operator']}")
        if d['country']: phone_lines.append(f"Страна: {d['country']}")
        for i, line in enumerate(phone_lines):
            prefix = "└ " if i == len(phone_lines) - 1 else "├ "
            card += f"{prefix}{line}\n"

    if chat_info: card += f"\n{chat_info}\n"
    funstat_stats = get_funstat_activity(user_id)
    if funstat_stats: card += f"\n▲ <b>Активность:</b>\n{funstat_stats}\n\n"

    if nfts_text != "Нет": card += f"✦ <b>NFT ({len(nft_lines)})</b>\n<blockquote>{nfts_text}</blockquote>\n"
    if gifts_lines: card += f"\n✾ <b>Полученные подарки:</b>\n<blockquote>{', '.join(gifts_lines)}</blockquote>"
    if sent_gifts_text: card += f"\n\n⎈ <b>Отправленные подарки:</b>\n<blockquote>{sent_gifts_text}</blockquote>"
    if history_text: card += f"\n\n{history_text}"
    if interests_text: card += f"\n\n{interests_text}"

    if len(card) > 4000:
        await status.edit_text(card[:4000] + "\n<i>... (продолжение ниже)</i>", parse_mode=enums.ParseMode.HTML)
        await message.reply_text(card[4000:], parse_mode=enums.ParseMode.HTML)
    else:
        await status.edit_text(card, parse_mode=enums.ParseMode.HTML)

# ==========================================
# ХЕНДЛЕРЫ КОМАНД
# ==========================================
request_user_obj = RequestPeerTypeUser(button_id=1, is_bot=False, max=1)
reply_keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton("Выбрать пользователя", request_user=request_user_obj)]], resize_keyboard=True)

@bot_app.on_message(filters.command("start"))
async def start_command(client, message):
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏣ Мой профиль", callback_data="my_profile")]])
    
    await message.reply_text(
        "❂ Добро пожаловать в поисковик — бесплатный поиск по данным\n\n"
        "⌖ Вы находитесь в меню, выберите нужную кнопку",
        reply_markup=kb, 
        parse_mode=enums.ParseMode.HTML
    )
    await message.reply_text(
        "👇 Используйте кнопку ниже для выбора пользователя:",
        reply_markup=reply_keyboard
    )

@bot_app.on_callback_query(filters.regex(r"^my_profile$"))
async def show_profile(client, callback_query):
    user_id = callback_query.from_user.id
    username = f"@{callback_query.from_user.username}" if callback_query.from_user.username else "Скрыт"
    first_seen, total_req, daily_req = get_profile(user_id)
    try: days_in_system = (datetime.now() - datetime.strptime(first_seen, "%Y-%m-%d")).days
    except: days_in_system = 0
    limit_display = f"{daily_req}/{DAILY_LIMIT}" if user_id != ADMIN_ID else "∞/∞"
    text = f"⊛ <b>Ваш профиль</b>\n\n<blockquote>├ ID: <code>{user_id}</code>\n├ Юзернейм: {username}\n├ В системе с: {format_date_short(first_seen)} ({format_days(days_in_system)})\n└ Запросов сегодня: <b>{limit_display}</b></blockquote>"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏎ Назад", callback_data="back_to_menu")]])
    await callback_query.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=kb)

@bot_app.on_callback_query(filters.regex(r"^back_to_menu$"))
async def back_to_menu(client, callback_query):
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏣ Мой профиль", callback_data="my_profile")]])
    await callback_query.message.edit_text("❂ Добро пожаловать в поисковик — бесплатный поиск по данным\n\n⌖ Вы находитесь в меню, выберите нужную кнопку", reply_markup=kb, parse_mode=enums.ParseMode.HTML)

# ==========================================
# АДМИН ПАНЕЛЬ
# ==========================================
admin_states = {}

@bot_app.on_message(filters.command("admin") & filters.user(ADMIN_ID))
async def admin_panel(client, message):
    users, reqs = get_admin_stats()
    text = f"⨳ <b>Админ-панель</b>\n\n<blockquote>├ Юзеров в базе: <code>{users}</code>\n└ Всего запросов: <code>{reqs}</code></blockquote>"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⊕ Выдать запросы", callback_data="admin_give"), InlineKeyboardButton("⊖ Забрать запросы", callback_data="admin_take")],
        [InlineKeyboardButton("↻ Обновить", callback_data="admin_refresh")]
    ])
    await message.reply_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

@bot_app.on_callback_query(filters.regex(r"^admin_give$") & filters.user(ADMIN_ID))
async def admin_give_start(client, callback_query):
    admin_states[ADMIN_ID] = "give"
    await callback_query.message.edit_text("⊕ <b>Выдача запросов</b>\n\nОтправьте ID пользователя и количество через пробел (например: <code>123456789 10</code>)", parse_mode=enums.ParseMode.HTML)

@bot_app.on_callback_query(filters.regex(r"^admin_take$") & filters.user(ADMIN_ID))
async def admin_take_start(client, callback_query):
    admin_states[ADMIN_ID] = "take"
    await callback_query.message.edit_text("⊖ <b>Забор запросов</b>\n\nОтправьте ID пользователя и количество через пробел (например: <code>123456789 10</code>)", parse_mode=enums.ParseMode.HTML)

@bot_app.on_callback_query(filters.regex(r"^admin_refresh$") & filters.user(ADMIN_ID))
async def admin_refresh(client, callback_query):
    users, reqs = get_admin_stats()
    text = f"⨳ <b>Админ-панель</b>\n\n<blockquote>├ Юзеров в базе: <code>{users}</code>\n└ Всего запросов: <code>{reqs}</code></blockquote>"
    await callback_query.message.edit_text(text, parse_mode=enums.ParseMode.HTML)

# ==========================================
# ПОИСК И ОБРАБОТКА ВВОДА
# ==========================================
async def search_handler(client, message, query=None):
    user_id = message.from_user.id
    if not query: query = message.text.strip()
    targets = [q.strip() for q in re.split(r'[,\s\n]+', query) if q.strip()]

    allowed, left = check_limit_status(user_id)
    if not allowed:
        await message.reply_text(f"⊘ <b>Лимит исчерпан</b>\n\nВы сделали <b>{DAILY_LIMIT}</b> запроса сегодня.\nЛимит обновится завтра в 00:00.", parse_mode=enums.ParseMode.HTML)
        return

    valid_targets = []
    for target in targets:
        if target.startswith('+') or (target.isdigit() and len(target) > 10): continue
        uid = None
        if target.isdigit(): uid = int(target)
        else:
            try: uid = (await user_app.get_users(target if target.startswith('@') else f"@{target}")).id
            except Exception as e: print(f"⚠️ Ошибка поиска {target}: {e}")
        if uid: valid_targets.append((uid, target))

    if not valid_targets:
        await message.reply_text("⊘ <b>Ошибка:</b> Юзернейм не найден.", parse_mode=enums.ParseMode.HTML)
        return 

    if len(valid_targets) > 1:
        status = await message.reply_text(f"⧉ <b>В очереди: {len(valid_targets)} запросов.</b>\nНачинаю обработку...", parse_mode=enums.ParseMode.HTML)
        for i, (uid, original_target) in enumerate(valid_targets, 1):
            allowed, left = check_limit_status(user_id)
            if not allowed and user_id != ADMIN_ID:
                await message.reply_text("⊘ <b>Лимит исчерпан во время обработки очереди.</b>", parse_mode=enums.ParseMode.HTML)
                break
            deduct_limit(user_id, message.from_user.username)
            try: await status.edit_text(f"⌛ <b>Обрабатываю {i}/{len(valid_targets)}: {original_target}</b>", parse_mode=enums.ParseMode.HTML)
            except: pass
            temp_msg = await message.reply_text(f"📡 Собираю данные для {original_target}...", parse_mode=enums.ParseMode.HTML)
            try: await process_user(client, message, uid, status=temp_msg, original_input=original_target)
            except FloodWait as e: await asyncio.sleep(e.value)
            except Exception as e: print(f"Ошибка process_user: {e}")
            await asyncio.sleep(3)
        try: await status.edit_text("✅ <b>Все запросы обработаны!</b>", parse_mode=enums.ParseMode.HTML)
        except: pass
        return

    uid, original_target = valid_targets[0]
    deduct_limit(user_id, message.from_user.username)
    status = await message.reply_text("✧ <b>Обрабатываю запрос...</b>", parse_mode=enums.ParseMode.HTML)
    try: await process_user(client, message, uid, status, original_input=original_target)
    except Exception as e: await status.edit_text(f"⊘ <b>Ошибка:</b> {e}", parse_mode=enums.ParseMode.HTML)

@bot_app.on_message(filters.private & ~filters.command(["admin", "start"]))
async def handle_input(client, message):
    print("========== DEBUG ==========", flush=True)
    print("TEXT:", message.text, flush=True)
    print("CONTACT:", message.contact, flush=True)
    print("USERS_SHARED:", getattr(message, "users_shared", None), flush=True)

    # Админка
    if message.from_user.id == ADMIN_ID and message.from_user.id in admin_states:
        state = admin_states[message.from_user.id]
        try:
            parts = message.text.split()
            if len(parts) == 2:
                target_id = int(parts[0])
                amount = int(parts[1])
                conn = sqlite3.connect(USERS_DB)
                c = conn.cursor()
                today = date.today().isoformat()
                c.execute("INSERT OR IGNORE INTO users (user_id, first_seen, daily_requests, last_request_date) VALUES (?, ?, 0, ?)", (target_id, today, today))
                if state == "give":
                    c.execute("UPDATE users SET daily_requests = MAX(0, daily_requests - ?) WHERE user_id = ?", (amount, target_id))
                else:
                    c.execute("UPDATE users SET daily_requests = daily_requests + ? WHERE user_id = ?", (amount, target_id))
                conn.commit(); conn.close()
                await message.reply_text("✅ Готово.")
            else:
                await message.reply_text("Неверный формат.")
        except Exception as e:
            await message.reply_text(f"Ошибка: {e}")
        admin_states.pop(message.from_user.id, None)
        return

    # Выбор пользователя через Telegram
    shared = getattr(message, "users_shared", None)
    if shared:
        print("USER SHARED RECEIVED", flush=True)
        for item in shared.users:
            uid = getattr(item, "user_id", None)
            if uid:
                print("SELECTED ID:", uid, flush=True)
                await search_handler(client, message, str(uid))
        return

    # Отправленный контакт
    contact = getattr(message, "contact", None)
    if contact:
        print("CONTACT RECEIVED", flush=True)
        print("PHONE:", contact.phone_number, flush=True)
        print("USER ID:", contact.user_id, flush=True)
        if contact.user_id:
            await search_handler(client, message, str(contact.user_id))
        else:
            await message.reply_text(
                "⚠️ Telegram не передал ID контакта.\n"
                "Получен только номер телефона."
            )
        return

    if message.text:
        await search_handler(client, message, message.text.strip())

# ==========================================
# ЗАПУСК
# ==========================================
async def main():
    init_users_db()
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
