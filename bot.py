# pip install pyrofork tgcrypto requests phonenumbers psycopg2-binary
import os
import re
import html
import asyncio
import threading
import requests
import phonenumbers
import psycopg2
import psycopg2.pool
from contextlib import contextmanager
from phonenumbers import carrier
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone, timedelta
from pyrogram import Client, filters, enums, idle
from pyrogram.raw.functions.payments import GetSavedStarGifts
from pyrogram.raw.functions.users import GetFullUser
from pyrogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, 
    RequestPeerTypeUser, InlineQueryResultArticle, InputTextMessageContent
)
from pyrogram.errors import FloodWait
from pyrogram.raw.types import (
    UpdateNewMessage, MessageService,
    MessageActionRequestedPeer, MessageActionRequestedPeerSentMe
)

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

DATABASE_URL = os.environ.get("DATABASE_URL", "")
OSINT_BOT_USERNAME = "@evenfa_bot"

TZ = timezone(timedelta(hours=6))

def today_local():
    return datetime.now(TZ).date().isoformat()

# ==========================================
# POSTGRES POOL
# ==========================================
_db_pool = None

def get_pool():
    global _db_pool
    if _db_pool is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL не задан.")
        dsn = DATABASE_URL
        if dsn.startswith("postgres://"):
            dsn = "postgresql://" + dsn[len("postgres://"):]
        _db_pool = psycopg2.pool.SimpleConnectionPool(1, 10, dsn=dsn)
    return _db_pool

@contextmanager
def db_cursor(commit=False):
    pool = get_pool()
    conn = pool.getconn()
    cur = None
    try:
        cur = conn.cursor()
        yield cur
        if commit:
            conn.commit()
    except Exception:
        try: conn.rollback()
        except Exception as e: print(f"⚠️ rollback: {e}")
        raise
    finally:
        if cur: cur.close()
        pool.putconn(conn)

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
# БАЗА ДАННЫХ (PostgreSQL)
# ==========================================
def init_users_db():
    try:
        with db_cursor(commit=True) as cur:
            cur.execute('''CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                first_seen DATE,
                total_requests INTEGER DEFAULT 0,
                daily_requests INTEGER DEFAULT 0,
                bonus_requests INTEGER DEFAULT 0,
                last_request_date DATE
            )''')
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS bonus_requests INTEGER DEFAULT 0")
        print("✅ PostgreSQL готова", flush=True)
    except Exception as e:
        print(f"⚠️ init DB: {e}", flush=True)

def _get_user_sync(user_id):
    try:
        with db_cursor() as cur:
            cur.execute(
                "SELECT username, first_seen, total_requests, daily_requests, bonus_requests, last_request_date "
                "FROM users WHERE user_id = %s", (int(user_id),))
            row = cur.fetchone()
        if not row: return None
        return {
            "username": row[0],
            "first_seen": row[1].isoformat() if row[1] else None,
            "total_requests": row[2] or 0,
            "daily_requests": row[3] or 0,
            "bonus_requests": row[4] or 0,
            "last_request_date": row[5].isoformat() if row[5] else None,
        }
    except Exception as e:
        print(f"⚠️ _get_user: {e}")
        return None

def _register_sync(user_id, username):
    today = today_local()
    try:
        with db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO users (user_id, username, first_seen, total_requests, daily_requests, bonus_requests, last_request_date) "
                "VALUES (%s, %s, %s, 0, 0, 0, %s) "
                "ON CONFLICT (user_id) DO NOTHING",
                (int(user_id), username or "", today, today))
    except Exception as e:
        print(f"⚠️ _register: {e}")

def _check_limit_sync(user_id, username=""):
    if user_id == ADMIN_ID:
        return True, 999
    today = today_local()
    u = _get_user_sync(user_id)
    if not u:
        _register_sync(user_id, username)
        return True, DAILY_LIMIT
    daily = u["daily_requests"] if u["last_request_date"] == today else 0
    limit = max(0, DAILY_LIMIT + u["bonus_requests"])
    if daily >= limit:
        return False, 0
    return True, limit - daily

def _deduct_limit_sync(user_id):
    if user_id == ADMIN_ID: return
    today = today_local()
    try:
        with db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO users (user_id, first_seen, total_requests, daily_requests, bonus_requests, last_request_date) "
                "VALUES (%s, %s, 1, 1, 0, %s) "
                "ON CONFLICT (user_id) DO UPDATE SET "
                "  total_requests = users.total_requests + 1, "
                "  daily_requests = CASE WHEN users.last_request_date = EXCLUDED.last_request_date "
                "    THEN users.daily_requests + 1 ELSE 1 END, "
                "  last_request_date = EXCLUDED.last_request_date",
                (int(user_id), today, today))
    except Exception as e:
        print(f"⚠️ _deduct_limit: {e}")

def _add_bonus_sync(user_id, amount):
    today = today_local()
    try:
        with db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO users (user_id, first_seen, daily_requests, bonus_requests, last_request_date) "
                "VALUES (%s, %s, 0, %s, %s) "
                "ON CONFLICT (user_id) DO UPDATE SET bonus_requests = users.bonus_requests + EXCLUDED.bonus_requests",
                (int(user_id), today, int(amount), today))
    except Exception as e:
        print(f"⚠️ _add_bonus: {e}")

def _get_admin_stats_sync():
    try:
        with db_cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM users")
            total_users = cur.fetchone()[0]
            cur.execute("SELECT COALESCE(SUM(total_requests), 0) FROM users")
            total_reqs = cur.fetchone()[0]
        return total_users, total_reqs
    except Exception as e:
        print(f"⚠️ admin_stats: {e}")
        return 0, 0

async def check_limit_status(user_id, username=""):
    return await asyncio.to_thread(_check_limit_sync, user_id, username)

async def deduct_limit(user_id):
    await asyncio.to_thread(_deduct_limit_sync, user_id)

async def add_bonus(user_id, amount):
    await asyncio.to_thread(_add_bonus_sync, user_id, amount)

async def get_admin_stats():
    return await asyncio.to_thread(_get_admin_stats_sync)

async def get_profile_data(user_id):
    return await asyncio.to_thread(_get_user_sync, user_id)

async def ensure_user_registered(user_id, username=""):
    u = await get_profile_data(user_id)
    if not u:
        await asyncio.to_thread(_register_sync, user_id, username)
        u = await get_profile_data(user_id)
    return u

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
    except Exception as e:
        print(f"⚠️ format_date: {e}")
        return date_str

MONTHS_RU = {1: "январь", 2: "февраль", 3: "март", 4: "апрель", 5: "май", 6: "июнь", 7: "июль", 8: "август", 9: "сентябрь", 10: "октябрь", 11: "ноябрь", 12: "декабрь"}
MONTHS_SHORT = {"01": "янв", "02": "фев", "03": "мар", "04": "апр", "05": "мая", "06": "июн", "07": "июл", "08": "авг", "09": "сен", "10": "окт", "11": "ноя", "12": "дек"}

def get_time_passed(year, month):
    now = datetime.now(TZ)
    reg_date = datetime(year, month, 1, tzinfo=TZ)
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
        a, b = ids[i], ids[i+1]
        if a <= user_id <= b:
            d1 = datetime.strptime(LOCAL_DB[a], "%Y-%m"); d2 = datetime.strptime(LOCAL_DB[b], "%Y-%m")
            md = max((d2.year - d1.year) * 12 + (d2.month - d1.month), 1)
            add = int((user_id - a) / (b - a) * md)
            return d1.year + (d1.month + add - 1)//12, (d1.month + add - 1) % 12 + 1
    return None, None

def _get_phones_sync(user_id):
    if not API_URL: return None
    try:
        r = requests.get(f"{API_URL}/get_phone", params={"id": user_id}, timeout=10)
        if r.status_code == 200:
            phones = r.json().get("phones", [])
            return ", ".join(phones) if phones else None
    except Exception as e:
        print(f"⚠️ API get_phone: {e}")
    return None

async def get_phones_from_api(user_id):
    return await asyncio.to_thread(_get_phones_sync, user_id)

def get_phone_details(phone_str):
    details = {"operator": None, "country": None}
    if not phone_str: return details
    try:
        parsed = phonenumbers.parse(phone_str.split(",")[0].strip(), "RU")
        details["operator"] = carrier.name_for_number(parsed, "ru")
        rc = phonenumbers.region_code_for_number(parsed)
        cmap = {"RU":"Россия","KZ":"Казахстан","UA":"Украина","BY":"Беларусь","UZ":"Узбекистан","KG":"Кыргызстан","TJ":"Таджикистан","AM":"Армения","GE":"Грузия","AZ":"Азербайджан","MD":"Молдова","LV":"Латвия","LT":"Литва","EE":"Эстония","US":"США","GB":"Великобритания","DE":"Германия","FR":"Франция","TR":"Турция","AE":"ОАЭ"}
        details["country"] = cmap.get(rc, rc)
    except Exception as e:
        print(f"⚠️ phonenumbers: {e}")
    return details

def _get_funstat_sync(user_id):
    if not FUNSTAT_TOKEN: return None
    try:
        r = requests.get(
            f"https://telelog.org/api/v1/users/{user_id}/stats_min",
            headers={"Authorization": f"Bearer {FUNSTAT_TOKEN}", "accept": "application/json"},
            timeout=10)
        if r.status_code == 200:
            data = r.json()
            lines = []
            for k, label in [("total_msg_count","Всего сообщений"),("total_groups","Всего чатов"),("reactions_count","Получено реакций")]:
                if data.get(k) is not None: lines.append(f"{label}: {data[k]}")
            for k, label in [("first_msg_date","Первое появление"),("last_msg_date","Последнее появление")]:
                if data.get(k): lines.append(f"{label}:\n{data[k].replace('T',' ').replace('Z','')}")
            if lines: return f"<blockquote>{chr(10).join(html.escape(str(l)) for l in lines)}</blockquote>"
    except Exception as e:
        print(f"⚠️ FunStat: {e}")
    return None

async def get_funstat_activity(user_id):
    return await asyncio.to_thread(_get_funstat_sync, user_id)

# ==========================================
# СЛОВАРИ ДЛЯ ИНТЕРЕСОВ
# ==========================================
_TO_KIRILLIC = {
    'a':'а','b':'в','c':'с','d':'д','e':'е','f':'ф','g':'г','h':'н','i':'и',
    'j':'й','k':'к','l':'л','m':'м','n':'п','o':'о','p':'п','q':'к','r':'р',
    's':'с','t':'т','u':'и','v':'в','w':'в','x':'х','y':'у','z':'з',
    'α':'а','β':'в','γ':'у','δ':'д','ε':'е','ζ':'з','η':'н','θ':'т','ι':'и',
    'κ':'к','λ':'л','μ':'м','ν':'в','ξ':'х','ο':'о','π':'п','ρ':'р','σ':'с',
    'ς':'с','τ':'т','υ':'и','φ':'ф','χ':'х','ψ':'п','ω':'о',
    'ց':'г','ᴜ':'и','ᴠ':'в','ᴧ':'л','ᴨ':'п','ᴦ':'р','ᴋ':'к','ᴍ':'м',
    'ᴏ':'о','ᴘ':'п','ᴛ':'т','ᴅ':'д','ᴢ':'з','ᴊ':'й','ᴡ':'в','ʏ':'у',
    'ɪ':'и','ᴎ':'н','ᴓ':'о','Ⅼ':'л','Ⅽ':'с','Ⅾ':'д','Ⅿ':'м',
    'ⅰ':'и','ⅼ':'л','ⅽ':'с','ⅾ':'д','ⅿ':'м','℮':'е','ƒ':'ф',
    'ł':'л','қ':'к','ѕ':'с','і':'и','ї':'и','ў':'у','ј':'й',
}
_KIR_TRANS = str.maketrans(_TO_KIRILLIC)

def _full_normalize(s):
    return str(s or "").translate(_KIR_TRANS).lower()

WORDS_KEYWORD_MAP = {
    "Гейминг": ["килл","килаур","kill","чит","cheat","реп","репут","майн","csgo","cs2","dota","дота","кс2","ксго","fortnite","фортнайт","pubg","пабг","gta","гта","warcraft","wow","лол","lol","геншин","genshin","роблокс","roblox","майнкрафт","minecraft","стим","steam","танки","аим","aim","спавн","spawn","лут","loot","клан","clan","сервер","server","ранг","rank"],
    "Маты/Сленг": ["ебан","ебал","еблан","ебат","ебуч","пизд","хуй","хуя","бляд","блят","мраз","долбо","нахуй","нахуя","залуп","гондон","мудак","дебил","туп","кринж","кринге","база","жиза","рофл","кек","азаз","ахах","хех","нуб","читер","читор","краб","мамк","мат","дроч","дик","dick","вacь"],
    "Криптовалюты": ["битк","bitcoin","биткойн","eth","ethereum","эфир","токен","token","крипт","crypto","usdt","tether","ton","sol","solana","bnb","binance","бинанс","defi","nft","web3","блокчейн","blockchain","кошелек","wallet","airdrop","эирдроп","майнер","miner"],
    "AI/Нейросети": ["нейро","neuro","нейросет","нейронк","gpt","чатгпт","chatgpt","midjourney","midjorney","stable","diffusion","candy","openai","опенаи","claude","llm","промпт","prompt"],
    "Операторы/SIM": ["мегафон","megafon","мтс","mts","билайн","beeline","теле2","tele2","оператор","sim","симк","мобил","тариф","связь","мобильн"],
    "Подписки/Контент": ["подпис","подпиш","лайк","репост","канал","shorts","reels","тикток","tiktok","инста","insta","youtube","ютуб","блогер","стрим","стример","донат","буст","видео","video","клип","clip"],
    "Программирование": ["python","питон","код","code","программ","разраб","developer","javascript","backend","frontend","golang","java","php","sql","docker","github","гитхаб","баг","bug","фикс","fix","деплой","deploy"],
    "Инвестиции/Трейдинг": ["трейд","trade","форекс","forex","инвест","invest","брокер","broker","акци","биржа","дивиденд","портфель","portfolio","профит","profit","убыток","лосс","loss","скальп","scalp"],
    "Музыка": ["music","музык","битмейк","beatmaker","диджей","dj","рэп","rap","хип-хоп","hip-hop","трек","track","бит","beat","soundcloud","спотифай","spotify"],
    "Спорт": ["спорт","sport","фитнес","fitness","gym","качалк","бодибилд","кроссфит","бокс","мма","футбол","хоккей","баскетбол","спортзал","тренер","coach"],
    "Авто": ["авто","auto","машин","bmw","mers","mercedes","audi","toyota","тачк","тюнинг","tuning","двигат","движок","колес","руль","wheel","car"],
    "Кино/Аниме": ["аниме","anime","манга","manga","сериал","series","фильм","movie","кино","netflix","нетфликс","дорам","dorama","сезон","season"],
    "Путешествия": ["путеш","travel","вокруг света","trip","tourist","tour","отдых","виза","пляж","beach","отпуск","vacation","аэропорт","airport"],
    "Обучение": ["учус","учит","студент","student","school","школа","универ","обучен","education","курс","course","экзамен","exam","сессия","диплом"],
    "Мода/Красота": ["мода","fashion","красот","beauty","макияж","стиль","style","маникюр","визаж","парикмахер","шмот","одежд"],
    "Бизнес": ["бизнес","business","стартап","startup","предприн","founder","ceo","маркетинг","marketing","продаж","sales","smm","смм","клиент","client"],
    "Продажа физов": ["селл","sell","слив","прод","физ","физы","физов","физиков","пробив","база","дамп","паспорт","инн","снилс","дропы","карт","скам","scam","селлер","селлеры","продаж","продαм","продам"],
    "OSINT/Darknet": ["osint","пробив","детектив","расслед","черн","даркнет","дарк","darknet"],
    "Политика": ["политик","протест","митинг","оппозиция","антивоен","антирос","война","путин","кремл"],
    "Общение": ["общение","чат","меню","раздача","состав","привет","как дела","братан","жека","димас","долг","пацаны","крутои","спам"],
}

def _parse_words_response(text):
    if not text: return []
    words = []
    for line in str(text).split("\n"):
        line = line.strip()
        m = re.match(r'[├|└]\s*\*?\*?(\d+)\s*[-–]\s*(\d+)\*?\*?\s*[\s]*([^\n]+)', line)
        if not m: continue
        try:
            mn = int(m.group(1)); mx = int(m.group(2))
        except ValueError: continue
        for w in m.group(3).strip().rstrip('').strip().split(","):
            wc = re.sub(r'[^\w\-]+', '', w, flags=re.UNICODE).strip()
            if wc and len(wc) >= 3:
                words.append({"word": wc, "avg": (mn + mx) // 2})
    words.sort(key=lambda x: x["avg"], reverse=True)
    return words

def _detect_interests_from_words(words, max_results=3):
    if not words: return []
    all_text = " ".join(_full_normalize(w.get("word", "")) for w in words)
    res = []
    for cat, kws in WORDS_KEYWORD_MAP.items():
        hits = sum(1 for kw in kws if _full_normalize(kw) in all_text)
        if hits > 0: res.append((cat, hits))
    res.sort(key=lambda x: x[1], reverse=True)
    return [r[0] for r in res[:max_results]] if res else ["Общение"]

# ==========================================
# ОЧЕРЕДЬ ДЛЯ @evenfa_bot
# ==========================================
osint_queue: asyncio.Queue = asyncio.Queue()
osint_lock = asyncio.Lock()

async def drain_queue():
    while not osint_queue.empty():
        try: osint_queue.get_nowait()
        except asyncio.QueueEmpty: break

async def wait_msg(timeout=1.0):
    try:
        return await asyncio.wait_for(osint_queue.get(), timeout=timeout)
    except asyncio.TimeoutError:
        return None

def get_btn(message, keywords):
    if not message or not message.reply_markup: return None
    for row in message.reply_markup.inline_keyboard:
        for btn in row:
            if any(k in _full_normalize(btn.text) for k in keywords): return btn
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
        except Exception as e:
            print(f"⚠️ get_users({un}): {e}")
    return ", ".join(list(dict.fromkeys(out)))

# ==========================================
# ПАРСИНГ ОТВЕТА @evenfa_bot
# ==========================================
async def get_interests_from_bot(target, premium_until=None):
    if not OSINT_BOT_USERNAME: return "", "", "", ""
    async with osint_lock:
        await drain_queue()
        try:
            await user_app.send_message(OSINT_BOT_USERNAME, str(target))
            collected = {}
            profile_msg = None
            idle = 0
            for _ in range(30):
                msg = await wait_msg(1.0)
                if msg:
                    collected[msg.id] = msg
                    if msg.reply_markup: profile_msg = msg
                    idle = 0
                elif collected:
                    idle += 1
                    if idle >= 5: break

            history_text_raw = ""
            for msg in collected.values():
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
                            if clean_fav: extra_lines.append(f"Любимый чат: {html.escape(clean_fav)}")

                circ = re.search(r'(?:круж|кружк)[^\d]*(\d+)', norm_text)
                if circ: extra_lines.append(f"Кружочки: {circ.group(1)}")
                voice = re.search(r'голос[^\d]*(\d+)', norm_text)
                if voice: extra_lines.append(f"Голосовые: {voice.group(1)}")
                if premium_until: extra_lines.append(f"Premium до: {html.escape(premium_until)}")
                if extra_lines: chat_info_text = "❖ <b>Информация из чатов:</b>\n<blockquote>" + "\n".join(extra_lines) + "</blockquote>"

                names_matches = re.findall(r'(\d{4}-\d{2}-\d{2})\s*(?:➜|→|->|—|-)\s*([^\n]+)', history_text_raw)
                history_lines = []
                for match in names_matches:
                    clean_name = re.sub(r'[\(\[]@?\S+[\)\]];?', '', match[1].strip())
                    clean_name = re.sub(r'@\w+', '', clean_name).strip(', ')
                    date_str = format_date_short(match[0])
                    name_escaped = html.escape(clean_name)
                    history_lines.append(f"{date_str} → {name_escaped}" if date_str else name_escaped)
                if history_lines: history_text = f"◈ <b>История профиля:</b>\n<blockquote>{chr(10).join(history_lines[:5])}</blockquote>"

            if profile_msg:
                target_btn = get_btn(profile_msg, ["част", "слов", "🗣"])
                if target_btn:
                    print(f"👆 [слова] '{target_btn.text}'", flush=True)
                    try:
                        await user_app.request_callback_answer(profile_msg.chat.id, profile_msg.id, target_btn.callback_data)
                    except Exception as e: print(f"⚠️ click words: {e}")
                    for _ in range(30):
                        msg = await wait_msg(1.0)
                        if msg and msg.text and re.search(r'[├|└]\s*\*?\*?\d+\s*[-–]\s*\d+', msg.text):
                            cats = _detect_interests_from_words(_parse_words_response(msg.text), 3)
                            if cats: interests_text = f"◉ <b>Интересы:</b>\n<blockquote>{chr(10).join(html.escape(c) for c in cats)}</blockquote>"
                            break

                gifts_btn = get_btn(profile_msg, ["подар", "🎁"])
                if gifts_btn:
                    print(f"👆 [подарки] '{gifts_btn.text}'", flush=True)
                    try:
                        ans = await user_app.request_callback_answer(profile_msg.chat.id, profile_msg.id, gifts_btn.callback_data)
                        if ans: print(f"💬 Попап: {ans}", flush=True)
                    except Exception as e: print(f"⚠️ click gifts: {e}")

                    menu_msg = None
                    for _ in range(30):
                        m = await wait_msg(1.0)
                        if m and m.reply_markup:
                            menu_msg = m; break
                    if menu_msg:
                        given_btn = get_btn(menu_msg, ["подарен", "отправ", "⇉", "=>"])
                        if given_btn:
                            print(f"👆 [подарено] '{given_btn.text}'", flush=True)
                            try:
                                ans = await user_app.request_callback_answer(menu_msg.chat.id, menu_msg.id, given_btn.callback_data)
                                if ans: print(f"💬 Попап: {ans}", flush=True)
                            except Exception as e: print(f"⚠️ click given: {e}")
                            for _ in range(10):
                                m = await wait_msg(1.5)
                                if m:
                                    parsed = await parse_given_gifts(m)
                                    if parsed:
                                        sent_gifts_text = parsed
                                        break

            return chat_info_text, history_text, interests_text, sent_gifts_text
        except Exception as e:
            print(f"⚠️ @evenfa_bot: {e}", flush=True)
        return "", "", "", ""

# ==========================================
# КЛИЕНТЫ
# ==========================================
bot_app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
user_app = Client("my_userbot", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING, in_memory=True)

@user_app.on_message(filters.chat(OSINT_BOT_USERNAME) & filters.incoming)
async def osint_handler(client, message):
    await osint_queue.put(message)

@user_app.on_edited_message(filters.chat(OSINT_BOT_USERNAME))
async def osint_edit_handler(client, message):
    await osint_queue.put(message)

# ==========================================
# РАЗРЕЗ СООБЩЕНИЙ
# ==========================================
TAGS_RE = re.compile(r'<(/?)(blockquote|code|b|i|a)[ >]')

def _open_tags(text):
    stack = []
    for m in TAGS_RE.finditer(text):
        close, tag = m.group(1), m.group(2)
        if close:
            if stack and stack[-1] == tag: stack.pop()
        else:
            stack.append(tag)
    return stack

def split_message(text, limit=4000):
    if len(text) <= limit:
        return [text]
    parts = []
    rem = text
    while rem:
        if len(rem) <= limit:
            parts.append(rem); break
        cut = rem.rfind('\n\n', 0, limit)
        if cut < limit // 3: cut = rem.rfind('\n', 0, limit)
        if cut < limit // 3: cut = limit
        chunk = rem[:cut]
        rem = rem[cut:].lstrip()
        open_stack = _open_tags(chunk)
        if open_stack:
            chunk += ''.join(f'</{t}>' for t in reversed(open_stack))
            rem = ''.join(f'<{t}>' for t in open_stack) + rem
        parts.append(chunk)
    return parts

# ==========================================
# КАРТОЧКА ПОЛЬЗОВАТЕЛЯ
# ==========================================
async def process_user(client, message, user_id, status=None, original_input=None):
    if not status:
        try: status = await message.reply_text("✧ <b>Собираю данные...</b>", parse_mode=enums.ParseMode.HTML)
        except Exception as e: print(f"⚠️ reply_text: {e}"); return False
    try:
        user_obj = None
        try: user_obj = await user_app.get_users(user_id)
        except Exception as e: print(f"⚠️ get_users({user_id}): {e}")

        premium_until = None
        if user_obj and user_obj.is_premium:
            try:
                fi = await user_app.invoke(GetFullUser(id=await user_app.resolve_peer(user_id)))
                if getattr(fi.full_user, 'premium_expiration_date', None):
                    premium_until = datetime.fromtimestamp(fi.full_user.premium_expiration_date, TZ).strftime("%d.%m.%Y")
            except Exception as e: print(f"⚠️ premium: {e}")

        username_str = f"@{user_obj.username}" if (user_obj and user_obj.username) else "Скрыт"
        dc_id = user_obj.dc_id if (user_obj and user_obj.dc_id) else None
        est_y, est_m = estimate_registration(user_id)
        reg_date = f"~ {MONTHS_RU.get(est_m, '')} {est_y} {get_time_passed(est_y, est_m)}" if est_y else None
        phone_str = await get_phones_from_api(user_id)

        gifts_lines, nft_lines = [], []
        try:
            peer = await user_app.resolve_peer(username_str if username_str != "Скрыт" else user_id)
            res = await user_app.invoke(GetSavedStarGifts(peer=peer, offset="", limit=100))
            if res.gifts:
                for g in res.gifts:
                    gift = getattr(g, "gift", None)
                    if getattr(gift, "slug", None): nft_lines.append(f"{gift.title} #{gift.num}")
                    elif not getattr(g, 'name_hidden', False) and g.from_id:
                        sid = getattr(g.from_id, 'user_id', None)
                        if sid: gifts_lines.append(f"<code>{sid}</code>")
        except Exception as e: print(f"⚠️ gifts: {e}")

        gifts_lines = list(dict.fromkeys(gifts_lines))
        nfts_text = ", ".join(list(dict.fromkeys(nft_lines))) if nft_lines else "Нет"
        chat_info, history_text, interests_text, sent_gifts_text = await get_interests_from_bot(original_input or user_id, premium_until)

        header_lines = [f"ID: <code>{user_id}</code>"]
        if username_str != "Скрыт": header_lines.append(f"Username: {html.escape(username_str)}")
        if reg_date: header_lines.append(f"Регистрация: {html.escape(reg_date)}")
        if dc_id: header_lines.append(f"DC: {dc_id}")

        card = "⌬ <b>Аккаунт Telegram</b>\n"
        for i, line in enumerate(header_lines):
            card += f"{'└ ' if i == len(header_lines)-1 else '├ '}{line}\n"

        if phone_str:
            card += f"\n✆ <b>Телефон:</b> <code>{html.escape(phone_str)}</code>\n"
            d = get_phone_details(phone_str)
            pl = []
            if d['operator']: pl.append(f"Оператор: {html.escape(d['operator'])}")
            if d['country']: pl.append(f"Страна: {html.escape(str(d['country']))}")
            for i, line in enumerate(pl):
                card += f"{'└ ' if i == len(pl)-1 else '├ '}{line}\n"

        if chat_info: card += f"\n{chat_info}\n"
        fs = await get_funstat_activity(user_id)
        if fs: card += f"\n▲ <b>Активность:</b>\n{fs}\n\n"

        if nfts_text != "Нет": card += f"✦ <b>NFT ({len(nft_lines)})</b>\n<blockquote>{html.escape(nfts_text)}</blockquote>\n"
        if gifts_lines: card += f"\n✾ <b>Полученные подарки:</b>\n<blockquote>{', '.join(gifts_lines)}</blockquote>"
        if sent_gifts_text: card += f"\n\n⎈ <b>Отправленные подарки:</b>\n<blockquote>{sent_gifts_text}</blockquote>"
        if history_text: card += f"\n\n{history_text}"
        if interests_text: card += f"\n\n{interests_text}"

        parts = split_message(card, 4000)
        for i, part in enumerate(parts):
            try:
                if i == 0: await status.edit_text(part, parse_mode=enums.ParseMode.HTML)
                else: await client.send_message(status.chat.id, part, parse_mode=enums.ParseMode.HTML)
            except Exception as e:
                print(f"⚠️ send part {i}: {e}")
        return True
    except Exception as e:
        err = html.escape(str(e))[:300]
        print(f"⚠️ process_user: {e}")
        try: await status.edit_text(f"⊘ <b>Ошибка:</b> {err}", parse_mode=enums.ParseMode.HTML)
        except Exception as e2: print(f"⚠️ status edit: {e2}")
        return False

# ==========================================
# ХЕНДЛЕРЫ КОМАНД И КЛАВИАТУРЫ
# ==========================================
request_user_obj = RequestPeerTypeUser(button_id=1, is_bot=False, max=1)

# Клавиатура в точности как на скрине (две кнопки друг под другом)
reply_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("⏣ Мой профиль")],
        [KeyboardButton("Выбрать пользователя", request_user=request_user_obj)],
    ],
    resize_keyboard=True,
)

async def render_profile_text(uid, username_raw):
    u = await ensure_user_registered(uid, username_raw or "")
    if not u:
        return None
    username = f"@{username_raw}" if username_raw else "Скрыт"
    try:
        dt = datetime.strptime(u["first_seen"], "%Y-%m-%d").replace(tzinfo=TZ)
        days_in_system = (datetime.now(TZ) - dt).days
    except Exception as e:
        print(f"⚠️ profile days: {e}")
        days_in_system = 0
    today = today_local()
    daily = 0 if u["total_requests"] == 0 else (
        u["daily_requests"] if u["last_request_date"] == today else 0
    )
    limit = "∞/∞" if uid == ADMIN_ID else f"{daily}/{max(0, DAILY_LIMIT + u['bonus_requests'])}"
    
    return (
        f"⊛ <b>Ваш профиль</b>\n\n"
        f"<blockquote>"
        f"• ID: <code>{uid}</code>\n"
        f"• Юзернейм: {html.escape(username)}\n"
        f"• В системе с: {format_date_short(u['first_seen'])} ({format_days(days_in_system)})\n"
        f"• Запросов сегодня: <b>{limit}</b>"
        f"</blockquote>"
    )

@bot_app.on_message(filters.command("start"))
async def start_command(client, message):
    await ensure_user_registered(message.from_user.id, message.from_user.username or "")
    await check_limit_status(message.from_user.id, message.from_user.username or "")

    args = message.text.split(maxsplit=1)
    payload = args[1].strip() if len(args) > 1 else ""
    if payload:
        uid_str = payload[4:] if payload.startswith("uid_") else payload
        if uid_str.isdigit():
            await search_handler(client, message, uid_str)
            return

    # Единственное сообщение при старте без лишнего "⌖"
    await message.reply_text(
        "❂ Добро пожаловать в поисковик — бесплатный поиск по данным\n\n"
        "⌖ Отправь ID, @username или выбери пользователя кнопкой снизу.",
        reply_markup=reply_keyboard,
        parse_mode=enums.ParseMode.HTML,
    )

@bot_app.on_message(filters.command("profile"))
async def profile_cmd(client, message):
    text = await render_profile_text(message.from_user.id, message.from_user.username)
    if text:
        await message.reply_text(text, parse_mode=enums.ParseMode.HTML)

@bot_app.on_callback_query(filters.regex(r"^my_profile$"))
async def show_profile(client, callback_query):
    await callback_query.answer()
    text = await render_profile_text(callback_query.from_user.id, callback_query.from_user.username)
    if not text:
        await callback_query.message.edit_text("⊘ Профиль не найден.", parse_mode=enums.ParseMode.HTML)
        return
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏎ Назад", callback_data="back_to_menu")]])
    await callback_query.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=kb)

@bot_app.on_callback_query(filters.regex(r"^back_to_menu$"))
async def back_to_menu(client, callback_query):
    await callback_query.answer()
    await callback_query.message.edit_text(
        "❂ Добро пожаловать в поисковик — бесплатный поиск по данным\n\n"
        "⌖ Отправь ID, @username или выбери пользователя кнопкой снизу.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏣ Мой профиль", callback_data="my_profile")]]),
        parse_mode=enums.ParseMode.HTML
    )

# ==========================================
# ИНЛАЙН РЕЖИМ
# ==========================================
@bot_app.on_inline_query()
async def inline_query_handler(client, inline_query):
    q = (inline_query.query or "").strip()
    if not q:
        await inline_query.answer([], cache_time=1, is_personal=True, switch_pm_text="⚡ Открыть поисковик", switch_pm_parameter="start")
        return

    uid = None
    try:
        if q.isdigit():
            uid = int(q)
        else:
            u = await user_app.get_users(q if q.startswith("@") else f"@{q}")
            uid = u.id
    except Exception as e:
        print(f"⚠️ inline resolve({q}): {e}", flush=True)

    if not uid:
        await inline_query.answer([], cache_time=5, is_personal=True, switch_pm_text="⊘ Не найдено — открыть бота", switch_pm_parameter="start")
        return

    try:
        u = await user_app.get_users(uid)
        username = f"@{u.username}" if u.username else "Скрыт"
        dc_id = u.dc_id or "?"
    except Exception:
        username, dc_id = "?", "?"

    est_y, est_m = estimate_registration(uid)
    reg = f"~ {MONTHS_RU.get(est_m, '')} {est_y} {get_time_passed(est_y, est_m)}".strip() if est_y else "?"

    preview = (
        f"⌬ <b>Telegram аккаунт</b>\n\n"
        f"<blockquote>"
        f"├ ID: <code>{uid}</code>\n"
        f"├ Username: {html.escape(username)}\n"
        f"├ DC: {dc_id}\n"
        f"└ Регистрация: {html.escape(reg)}\n"
        f"</blockquote>\n\n"
        f"<i>Нажми кнопку ниже — бот соберёт полный отчёт</i>"
    )

    me = await client.get_me()
    deep_link = f"https://t.me/{me.username}?start=uid_{uid}"

    result = InlineQueryResultArticle(
        id=str(uid),
        title=f"Аккаунт {uid}",
        description=username,
        input_message_content=InputTextMessageContent(preview, parse_mode=enums.ParseMode.HTML),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✦ Полный отчёт", url=deep_link)]])
    )

    await inline_query.answer([result], cache_time=10, is_personal=True)

# ==========================================
# АДМИН-ПАНЕЛЬ
# ==========================================
admin_states = {}

@bot_app.on_message(filters.command("admin") & filters.user(ADMIN_ID))
async def admin_panel(client, message):
    users, reqs = await get_admin_stats()
    text = f"⨳ <b>Админ-панель</b>\n\n<blockquote>├ Юзеров: <code>{users}</code>\n└ Всего запросов: <code>{reqs}</code></blockquote>"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⊕ Выдать запросы", callback_data="admin_give"),
         InlineKeyboardButton("⊖ Забрать запросы", callback_data="admin_take")],
        [InlineKeyboardButton("↻ Обновить", callback_data="admin_refresh")]
    ])
    await message.reply_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

@bot_app.on_callback_query(filters.regex(r"^admin_give$") & filters.user(ADMIN_ID))
async def admin_give_start(client, callback_query):
    await callback_query.answer()
    admin_states[ADMIN_ID] = "give"
    await callback_query.message.edit_text("⊕ <b>Выдать бонусные запросы</b>\n\nОтправь: <code>ID количество</code>", parse_mode=enums.ParseMode.HTML)

@bot_app.on_callback_query(filters.regex(r"^admin_take$") & filters.user(ADMIN_ID))
async def admin_take_start(client, callback_query):
    await callback_query.answer()
    admin_states[ADMIN_ID] = "take"
    await callback_query.message.edit_text("⊖ <b>Забрать бонусные запросы</b>\n\nОтправь: <code>ID количество</code>", parse_mode=enums.ParseMode.HTML)

@bot_app.on_callback_query(filters.regex(r"^admin_refresh$") & filters.user(ADMIN_ID))
async def admin_refresh(client, callback_query):
    await callback_query.answer("Обновлено")
    users, reqs = await get_admin_stats()
    text = f"⨳ <b>Админ-панель</b>\n\n<blockquote>├ Юзеров: <code>{users}</code>\n└ Всего запросов: <code>{reqs}</code></blockquote>"
    await callback_query.message.edit_text(text, parse_mode=enums.ParseMode.HTML)

# ==========================================
# ПОИСК
# ==========================================
async def search_handler(client, message, query=None):
    user_id = message.from_user.id
    if not query: query = message.text.strip()
    targets = [q.strip() for q in re.split(r'[,\s\n]+', query) if q.strip()]

    allowed, _ = await check_limit_status(user_id, message.from_user.username or "")
    if not allowed:
        await message.reply_text(f"⊘ <b>Лимит исчерпан</b>\n\nВы сделали все запросы на сегодня.\nЛимит обновится завтра в 00:00.", parse_mode=enums.ParseMode.HTML)
        return

    valid = []
    for target in targets:
        if target.startswith('+') or (target.isdigit() and len(target) > 10): continue
        uid = None
        if target.isdigit(): uid = int(target)
        else:
            try: uid = (await user_app.get_users(target if target.startswith('@') else f"@{target}")).id
            except Exception as e: print(f"⚠️ resolve {target}: {e}")
        if uid: valid.append((uid, target))

    if not valid:
        await message.reply_text("⊘ <b>Ошибка:</b> Юзернейм не найден.", parse_mode=enums.ParseMode.HTML)
        return

    if len(valid) > 1:
        status = await message.reply_text(f"⧉ <b>В очереди: {len(valid)}</b>", parse_mode=enums.ParseMode.HTML)
        for i, (uid, orig) in enumerate(valid, 1):
            allowed, _ = await check_limit_status(user_id, message.from_user.username or "")
            if not allowed and user_id != ADMIN_ID:
                await message.reply_text("⊘ Лимит исчерпан во время обработки.", parse_mode=enums.ParseMode.HTML)
                break
            try: await status.edit_text(f"⌛ {i}/{len(valid)}: {html.escape(orig)}", parse_mode=enums.ParseMode.HTML)
            except Exception: pass
            tmp = await message.reply_text(f"📡 {html.escape(orig)}...", parse_mode=enums.ParseMode.HTML)
            ok = await process_user(client, message, uid, status=tmp, original_input=orig)
            if ok: await deduct_limit(user_id)
            await asyncio.sleep(3)
        try: await status.edit_text("✅ <b>Все запросы обработаны!</b>", parse_mode=enums.ParseMode.HTML)
        except Exception: pass
        return

    uid, orig = valid[0]
    status = await message.reply_text("✧ <b>Обрабатываю запрос...</b>", parse_mode=enums.ParseMode.HTML)
    ok = await process_user(client, message, uid, status, original_input=orig)
    if ok: await deduct_limit(user_id)

# ==========================================
# ОБРАБОТКА ВВОДА
# ==========================================
@bot_app.on_message(filters.private & ~filters.command(["admin", "start", "profile"]) & ~filters.service)
async def handle_input(client, message):
    # Ловим нажатие на кнопку "Мой профиль"
    if message.text and message.text.strip() == "⏣ Мой профиль":
        text = await render_profile_text(message.from_user.id, message.from_user.username)
        if text:
            await message.reply_text(text, parse_mode=enums.ParseMode.HTML)
        return

    if getattr(message, "users_shared", None):
        return

    if message.from_user.id == ADMIN_ID and message.from_user.id in admin_states:
        if not message.text:
            await message.reply_text("⊘ Отправь текст: <code>ID количество</code>", parse_mode=enums.ParseMode.HTML); return
        state = admin_states.pop(message.from_user.id)
        try:
            parts = message.text.split()
            if len(parts) != 2:
                await message.reply_text("⊘ Формат: <code>id amount</code>", parse_mode=enums.ParseMode.HTML); return
            target_id, amount = int(parts[0]), int(parts[1])
            if state == "give":
                await add_bonus(target_id, amount)
                await message.reply_text(f"✅ Выдано <b>{amount}</b> бонусных для <code>{target_id}</code>", parse_mode=enums.ParseMode.HTML)
            else:
                await add_bonus(target_id, -amount)
                await message.reply_text(f"✅ Забрано <b>{amount}</b> бонусных у <code>{target_id}</code>", parse_mode=enums.ParseMode.HTML)
        except Exception as e:
            await message.reply_text(f"⊘ Ошибка: {html.escape(str(e))}", parse_mode=enums.ParseMode.HTML)
        return

    contact = getattr(message, "contact", None)
    if contact:
        if contact.user_id:
            await search_handler(client, message, str(contact.user_id))
        else:
            await message.reply_text(f"⊘ <b>Не удалось определить Telegram ID.</b>\n\nTelegram передал только номер: <code>{html.escape(contact.phone_number)}</code>\n\nОтправь через кнопку «Выбрать пользователя».", parse_mode=enums.ParseMode.HTML)
        return

    if message.text:
        await search_handler(client, message, message.text.strip())

# ==========================================
# RAW ОБРАБОТЧИК
# ==========================================
@bot_app.on_raw_update(group=-1)
async def raw_handler(client, update, users, chats):
    if not isinstance(update, UpdateNewMessage): return
    msg = update.message
    if not isinstance(msg, MessageService): return
    if not isinstance(msg.action, (MessageActionRequestedPeerSentMe, MessageActionRequestedPeer)): return

    chat_id = msg.peer_id.user_id
    for peer in msg.action.peers:
        uid = getattr(peer, "user_id", None)
        if not uid: continue
        print(f"========== RAW UPDATE: chat={chat_id} -> uid={uid} ==========", flush=True)

        allowed, _ = await check_limit_status(chat_id, "")
        if not allowed:
            await client.send_message(chat_id, "⊘ <b>Лимит исчерпан</b>\n\nПопробуй завтра.", parse_mode=enums.ParseMode.HTML)
            return
        status = await client.send_message(chat_id, "✧ <b>Обрабатываю запрос...</b>", parse_mode=enums.ParseMode.HTML)
        ok = await process_user(client, status, uid, status=status, original_input=str(uid))
        if ok: await deduct_limit(chat_id)
        return

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
        print(f"❌ ОШИБКА: {e}", flush=True); return
    await idle()
    await bot_app.stop(); await user_app.stop()

if __name__ == "__main__":
    loop.run_until_complete(main())
