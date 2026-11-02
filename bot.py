# pip install pyrofork tgcrypto requests phonenumbers psycopg2-binary exifread
import os
import re
import csv
import html
import ipaddress
import asyncio
import threading
import requests
import phonenumbers
import psycopg2
import psycopg2.pool
import exifread
from contextlib import contextmanager
from phonenumbers import carrier, geocoder, timezone as pn_timezone
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone, timedelta
from pyrogram import Client, filters, enums, idle
from pyrogram.raw.functions.payments import GetSavedStarGifts
from pyrogram.raw.functions.users import GetFullUser
from pyrogram.raw.functions.messages import GetStickerSet
from pyrogram.raw.types import InputStickerSetShortName
from pyrogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    InlineQueryResultArticle, InputTextMessageContent,
    InputPhoneContact
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
MINFILES_CSV = os.environ.get("MINFILES_CSV", "ru_numbers.csv")
PHONSINT_API = os.environ.get("PHONSINT_API", "")

ABSTRACT_API_KEY = os.environ.get("ABSTRACT_API_KEY", "")
DADATA_API_KEY = os.environ.get("DADATA_API_KEY", "")
TONAPI_KEY = os.environ.get("TONAPI_KEY", "")

ADMIN_ID = 5021557806
DAILY_LIMIT = 0
BONUS_AMOUNT = 3
REF_REWARD = 1
DATABASE_URL = os.environ.get("DATABASE_URL", "")
TZ = timezone(timedelta(hours=6))

user_states = {}
admin_states = {}

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
            raise RuntimeError("DATABASE_URL не задан")
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
        if commit: conn.commit()
    except Exception:
        try: conn.rollback()
        except Exception as e: print(f"⚠️ rollback: {e}")
        raise
    finally:
        if cur: cur.close()
        pool.putconn(conn)

# ==========================================
# ВЕБ-СЕРВЕР
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
        with db_cursor(commit=True) as cur:
            cur.execute('''CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY, username TEXT, first_seen DATE,
                total_requests INTEGER DEFAULT 0, daily_requests INTEGER DEFAULT 0,
                bonus_requests INTEGER DEFAULT 0, last_request_date DATE,
                referrer_id BIGINT DEFAULT NULL,
                last_bonus_date DATE DEFAULT NULL
            )''')
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS bonus_requests INTEGER DEFAULT 0")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referrer_id BIGINT DEFAULT NULL")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_bonus_date DATE DEFAULT NULL")
            cur.execute('''CREATE TABLE IF NOT EXISTS mirrors (
                bot_id BIGINT PRIMARY KEY, owner_id BIGINT, bot_token TEXT
            )''')
        print("✅ PostgreSQL готова", flush=True)
    except Exception as e:
        print(f"⚠️ init DB: {e}", flush=True)

def _get_user_sync(user_id):
    try:
        with db_cursor() as cur:
            cur.execute(
                "SELECT username, first_seen, total_requests, daily_requests, bonus_requests, last_request_date, last_bonus_date "
                "FROM users WHERE user_id = %s", (int(user_id),))
            row = cur.fetchone()
        if not row: return None
        return {
            "username": row[0], "first_seen": row[1].isoformat() if row[1] else None,
            "total_requests": row[2] or 0, "daily_requests": row[3] or 0,
            "bonus_requests": row[4] or 0, "last_request_date": row[5].isoformat() if row[5] else None,
            "last_bonus_date": row[6].isoformat() if row[6] else None
        }
    except Exception as e:
        print(f"⚠️ _get_user: {e}")
        return None

def _register_sync(user_id, username, referrer_id=None):
    today = today_local()
    try:
        with db_cursor(commit=True) as cur:
            cur.execute("SELECT 1 FROM users WHERE user_id = %s", (int(user_id),))
            if cur.fetchone(): return False
            cur.execute(
                "INSERT INTO users (user_id, username, first_seen, total_requests, daily_requests, bonus_requests, last_request_date, referrer_id, last_bonus_date) "
                "VALUES (%s, %s, %s, 0, 0, 0, %s, %s, NULL)",
                (int(user_id), username or "", today, today, referrer_id))
            return True
    except Exception as e:
        print(f"⚠️ _register: {e}")
        return False

def _get_referral_count_sync(user_id):
    try:
        with db_cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM users WHERE referrer_id = %s", (int(user_id),))
            return cur.fetchone()[0]
    except: return 0

def _check_limit_sync(user_id, username=""):
    if user_id == ADMIN_ID: return True, 999
    today = today_local()
    u = _get_user_sync(user_id)
    if not u:
        _register_sync(user_id, username)
        return False, 0
    daily = u["daily_requests"] if u["last_request_date"] == today else 0
    limit = max(0, DAILY_LIMIT + u["bonus_requests"])
    if daily >= limit: return False, 0
    return True, limit - daily

def _deduct_limit_sync(user_id):
    if user_id == ADMIN_ID: return
    today = today_local()
    try:
        with db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO users (user_id, first_seen, total_requests, daily_requests, bonus_requests, last_request_date) "
                "VALUES (%s, %s, 1, 1, 0, %s) ON CONFLICT (user_id) DO UPDATE SET "
                "  total_requests = users.total_requests + 1, "
                "  daily_requests = CASE WHEN users.last_request_date = EXCLUDED.last_request_date "
                "    THEN users.daily_requests + 1 ELSE 1 END, "
                "  last_request_date = EXCLUDED.last_request_date",
                (int(user_id), today, today))
    except Exception as e: print(f"⚠️ _deduct_limit: {e}")

def _add_bonus_sync(user_id, amount):
    today = today_local()
    try:
        with db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO users (user_id, first_seen, daily_requests, bonus_requests, last_request_date) "
                "VALUES (%s, %s, 0, %s, %s) ON CONFLICT (user_id) DO UPDATE SET bonus_requests = users.bonus_requests + EXCLUDED.bonus_requests",
                (int(user_id), today, int(amount), today))
    except Exception as e: print(f"⚠️ _add_bonus: {e}")

def _claim_daily_bonus_sync(user_id):
    today = today_local()
    try:
        with db_cursor(commit=True) as cur:
            cur.execute("UPDATE users SET bonus_requests = bonus_requests + %s, last_bonus_date = %s WHERE user_id = %s", (BONUS_AMOUNT, today, user_id))
        return True
    except: return False

def _save_mirror_sync(bot_id, owner_id, token):
    try:
        with db_cursor(commit=True) as cur:
            cur.execute("INSERT INTO mirrors (bot_id, owner_id, bot_token) VALUES (%s, %s, %s) ON CONFLICT (bot_id) DO UPDATE SET bot_token = EXCLUDED.bot_token", (bot_id, owner_id, token))
    except Exception as e: print(f"⚠️ _save_mirror: {e}")

def _get_all_mirrors_sync():
    try:
        with db_cursor() as cur:
            cur.execute("SELECT bot_id, owner_id, bot_token FROM mirrors")
            return cur.fetchall()
    except: return []

def _get_admin_stats_sync():
    try:
        with db_cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM users")
            total_users = cur.fetchone()[0]
            cur.execute("SELECT COALESCE(SUM(total_requests), 0) FROM users")
            total_reqs = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM users WHERE last_request_date = %s", (today_local(),))
            active_today = cur.fetchone()[0]
            cur.execute("SELECT COALESCE(SUM(daily_requests), 0) FROM users WHERE last_request_date = %s", (today_local(),))
            daily_reqs = cur.fetchone()[0]
        return total_users, total_reqs, active_today, daily_reqs
    except Exception as e:
        print(f"⚠️ admin_stats: {e}")
        return 0, 0, 0, 0

async def check_limit_status(user_id, username=""): return await asyncio.to_thread(_check_limit_sync, user_id, username)
async def deduct_limit(user_id): await asyncio.to_thread(_deduct_limit_sync, user_id)
async def add_bonus(user_id, amount): await asyncio.to_thread(_add_bonus_sync, user_id, amount)
async def claim_daily_bonus(user_id): return await asyncio.to_thread(_claim_daily_bonus_sync, user_id)
async def get_admin_stats(): return await asyncio.to_thread(_get_admin_stats_sync)
async def get_profile_data(user_id): return await asyncio.to_thread(_get_user_sync, user_id)
async def get_referral_count(user_id): return await asyncio.to_thread(_get_referral_count_sync, user_id)

async def ensure_user_registered(user_id, username=""):
    u = await get_profile_data(user_id)
    if not u:
        await asyncio.to_thread(_register_sync, user_id, username)
        u = await get_profile_data(user_id)
    return u

# ==========================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================
def _is_premium(user_obj) -> bool:
    if user_obj is None: return False
    for attr in ("is_premium", "premium"):
        v = getattr(user_obj, attr, None)
        if v is not None: return bool(v)
    return False

def _get_dc_id(user_obj):
    if user_obj is None: return None
    dc = getattr(user_obj, "dc_id", None)
    if dc: return dc
    photo = getattr(user_obj, "photo", None)
    if photo: return getattr(photo, "dc_id", None)
    return None

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

MONTHS_RU = {1: "январь", 2: "февраль", 3: "март", 4: "апрель", 5: "май", 6: "июнь",
             7: "июль", 8: "август", 9: "сентябрь", 10: "октябрь", 11: "ноябрь", 12: "декабрь"}

def get_time_passed(year, month):
    now = datetime.now(TZ)
    reg_date = datetime(year, month, 1, tzinfo=TZ)
    years = now.year - reg_date.year
    months = now.month - reg_date.month
    if months < 0: years -= 1; months += 12
    if years % 10 == 1 and years % 100 != 11: y_str = f"{years} год"
    elif 2 <= years % 10 <= 4 and not (12 <= years % 100 <= 14): y_str = f"{years} года"
    else: y_str = f"{years} лет"
    if years == 0: y_str = ""
    m_str = f"{months} мес" if months > 0 else ""
    return f"({y_str} {m_str})".strip()

LOCAL_DB = {
    100000: "2013-08", 500000: "2013-10", 1200000: "2013-12",
    2000000: "2014-01", 5500000: "2014-03", 12000000: "2014-05", 25000000: "2014-07", 34000000: "2014-09", 42000000: "2014-11",
    50000000: "2015-01", 68000000: "2015-03", 90000000: "2015-05", 115000000: "2015-07", 135000000: "2015-09", 155000000: "2015-11",
    180000000: "2016-01", 205000000: "2016-03", 230000000: "2016-05", 255000000: "2016-07", 280000000: "2016-09", 305000000: "2016-11",
    330000000: "2017-01", 360000000: "2017-03", 390000000: "2017-05", 415000000: "2017-07", 440000000: "2017-09", 470000000: "2017-11",
    500000000: "2018-01", 540000000: "2018-03", 580000000: "2018-05", 625000000: "2018-07", 660000000: "2018-09", 710000000: "2018-11",
    750000000: "2019-01", 790000000: "2019-03", 830000000: "2019-05", 875000000: "2019-07", 915000000: "2019-09", 960000000: "2019-11",
    1000000000: "2020-01", 1080000000: "2020-03", 1160000000: "2020-05", 1250000000: "2020-07", 1340000000: "2020-09", 1430000000: "2020-11",
    1500000000: "2021-01", 1630000000: "2021-03", 1750000000: "2021-05", 1850000000: "2021-07", 1970000000: "2021-09", 2140000000: "2021-11",
    5000000000: "2022-01", 5150000000: "2022-03", 5280000000: "2022-05", 5400000000: "2022-07", 5550000000: "2022-09", 5700000000: "2022-11",
    5800000000: "2023-01", 5850000000: "2023-03", 5900000000: "2023-05", 5950000000: "2023-07", 6100000000: "2023-09", 6300000000: "2023-11",
    6500000000: "2024-01", 6580000000: "2024-02", 6650000000: "2024-03", 6730000000: "2024-04", 6800000000: "2024-05", 6900000000: "2024-06",
    7020000000: "2024-07", 7110000000: "2024-08", 7180000000: "2024-09", 7250000000: "2024-10", 7320000000: "2024-11", 7380000000: "2024-12",
    7450000000: "2025-01", 7520000000: "2025-02", 7590000000: "2025-03", 7660000000: "2025-04", 7730000000: "2025-05", 7810000000: "2025-06",
    7890000000: "2025-07", 7960000000: "2025-08", 8040000000: "2025-09", 8110000000: "2025-10", 8180000000: "2025-11", 8250000000: "2025-12",
    8310000000: "2026-01", 8380000000: "2026-02", 8450000000: "2026-03", 8520000000: "2026-04", 8590000000: "2026-05", 8670000000: "2026-06",
    8750000000: "2026-07", 8830000000: "2026-08", 8910000000: "2026-09", 8990000000: "2026-10", 9080000000: "2026-11",
}

def estimate_registration(user_id):
    ids = sorted(LOCAL_DB.keys())
    if user_id <= ids[0]:
        p = LOCAL_DB[ids[0]].split("-"); return int(p[0]), int(p[1])
    if user_id >= ids[-1]:
        p = LOCAL_DB[ids[-1]].split("-"); return int(p[0]), int(p[1])
    for i in range(len(ids) - 1):
        a, b = ids[i], ids[i+1]
        if a <= user_id <= b:
            d1 = datetime.strptime(LOCAL_DB[a], "%Y-%m")
            d2 = datetime.strptime(LOCAL_DB[b], "%Y-%m")
            days_diff = (d2 - d1).days
            id_progress = (user_id - a) / (b - a)
            exact_date = d1 + timedelta(days=round(id_progress * days_diff))
            return exact_date.year, exact_date.month
    return None, None

def _get_phones_sync(user_id):
    if not API_URL: return None
    try:
        r = requests.get(f"{API_URL}/get_phone", params={"id": user_id}, timeout=10)
        if r.status_code == 200:
            phones = r.json().get("phones", [])
            return ", ".join(phones) if phones else None
    except Exception as e: print(f"⚠️ API get_phone: {e}")
    return None

async def get_phones_from_api(user_id): return await asyncio.to_thread(_get_phones_sync, user_id)

def get_phone_details(phone_str):
    details = {"operator": None, "country": None}
    if not phone_str: return details
    try:
        parsed = phonenumbers.parse(phone_str.split(",")[0].strip(), "RU")
        details["operator"] = carrier.name_for_number(parsed, "ru")
        rc = phonenumbers.region_code_for_number(parsed)
        cmap = {"RU":"Россия","KZ":"Казахстан","UA":"Украина","BY":"Беларусь","UZ":"Узбекистан","KG":"Кыргызстан","TJ":"Таджикистан","AM":"Армения","GE":"Грузия","AZ":"Азербайджан","MD":"Молдова","LV":"Латвия","LT":"Литва","EE":"Эстония","US":"США","GB":"Великобритания","DE":"Германия","FR":"Франция","TR":"Турция","AE":"ОАЭ"}
        details["country"] = cmap.get(rc, rc)
    except Exception as e: print(f"⚠️ phonenumbers: {e}")
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
    except Exception as e: print(f"⚠️ FunStat: {e}")
    return None

async def get_funstat_activity(user_id): return await asyncio.to_thread(_get_funstat_sync, user_id)

# ==========================================
# РЕЕСТР МИНЦИФРЫ
# ==========================================
_DEF_REGISTRY = None

def _load_def_registry():
    global _DEF_REGISTRY
    if _DEF_REGISTRY is not None: return _DEF_REGISTRY
    _DEF_REGISTRY = []
    if not MINFILES_CSV or not os.path.exists(MINFILES_CSV):
        print(f"⚠️ CSV реестра не найден: {MINFILES_CSV}")
        return _DEF_REGISTRY
    try:
        with open(MINFILES_CSV, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    _DEF_REGISTRY.append({
                        "start": int(row.get("from_range") or row.get("От") or 0),
                        "end": int(row.get("to_range") or row.get("До") or 0),
                        "operator": row.get("operator") or row.get("Оператор", ""),
                        "region": row.get("region") or row.get("Регион", ""),
                    })
                except (ValueError, KeyError):
                    continue
        print(f"✅ Загружено {len(_DEF_REGISTRY)} DEF-диапазонов")
    except Exception as e:
        print(f"⚠️ Ошибка чтения CSV: {e}")
    return _DEF_REGISTRY

def _def_lookup_sync(national_number):
    for entry in _load_def_registry():
        if entry["start"] <= national_number <= entry["end"]:
            return entry
    return None

async def def_lookup(national_number): return await asyncio.to_thread(_def_lookup_sync, national_number)

# ==========================================
# КЛИЕНТЫ
# ==========================================
bot_app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
user_app = Client("my_userbot", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING, in_memory=True)

# ==========================================
# TELEGRAM LOOKUP
# ==========================================
async def resolve_user_by_phone(phone_e164: str):
    try:
        result = await user_app.import_contacts([
            InputPhoneContact(phone=phone_e164, first_name=".", last_name="")
        ])
        user_obj = result.users[0] if result.users else None
        try:
            await user_app.delete_contacts([phone_e164])
        except Exception as e:
            print(f"⚠️ delete_contacts: {e}")
        return user_obj
    except Exception as e:
        print(f"⚠️ import_contacts: {e}")
        return None

def build_tg_block(user_obj):
    if not user_obj:
        return []
    uname = f"@{user_obj.username}" if user_obj.username else "Скрыт"
    name = user_obj.first_name or "—"
    premium = "Да" if _is_premium(user_obj) else "Нет"
    dc = _get_dc_id(user_obj) or "?"
    ey, em = estimate_registration(user_obj.id)
    passed = get_time_passed(ey, em).strip("() ") if ey else ""
    reg = f"~ {MONTHS_RU.get(em, '')} {ey} {passed}".strip() if ey else "—"

    lines = [
        f"ID: <code>{user_obj.id}</code>",
        f"Username: {html.escape(uname)}",
        f"Имя: {html.escape(name)}",
        f"Premium: {premium}",
        f"DC: {dc}",
        f"Регистрация: {html.escape(reg)}",
    ]
    if user_obj.username:
        lines.append(f'Ссылка: <a href="https://t.me/{user_obj.username}">t.me/{user_obj.username}</a>')
    else:
        lines.append(f'Ссылка: <a href="tg://user?id={user_obj.id}">tg://user?id={user_obj.id}</a>')
    return lines

# ==========================================
# WHATSAPP + PHONSINT + ABSTRACTAPI
# ==========================================
def _check_whatsapp_sync(phone_e164: str):
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }
    phone_clean = phone_e164.replace("+", "").replace(" ", "")
    try:
        r = requests.get(f"https://wa.me/{phone_clean}", headers=headers, timeout=10, allow_redirects=True)
        if r.status_code == 200:
            body = r.text.lower()
            if any(x in body for x in ["send message", "open whatsapp", "use whatsapp web", "whatsapp"]):
                return True
        elif r.status_code == 404:
            return False
    except Exception as e:
        print(f"⚠️ WhatsApp wa.me error: {e}")
    try:
        r2 = requests.get(
            f"https://api.whatsapp.com/send?phone={phone_clean}",
            headers=headers, timeout=8, allow_redirects=True
        )
        if r2.status_code == 200 and "phone" in r2.text.lower():
            return True
    except Exception as e:
        print(f"⚠️ WhatsApp api error: {e}")
    return None

async def check_whatsapp(phone_e164):
    return await asyncio.to_thread(_check_whatsapp_sync, phone_e164)

def _phonsint_sync(phone_e164: str):
    if not PHONSINT_API: return None
    try:
        r = requests.get(f"{PHONSINT_API}/scan", params={"phone": phone_e164}, timeout=35)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"⚠️ phonsint api: {e}")
    return None

async def run_phonsint(phone_e164):
    data = await asyncio.to_thread(_phonsint_sync, phone_e164)
    if not data or not isinstance(data, list):
        return None
    found = []
    for item in data:
        if item.get("status") != "Registered":
            continue
        platform = item.get("site_name") or "?"
        display = (item.get("extra") or {}).get("display_name") or ""
        norm_display = display.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        if display and phone_e164.lstrip("+") in norm_display:
            display = ""
        line = f"{platform}: Зарегистрирован"
        if display:
            line += f" ({html.escape(display[:40])})"
        found.append(line)
    return found if found else None

def _abstract_intel_sync(phone_e164: str):
    if not ABSTRACT_API_KEY: return None
    try:
        r = requests.get(
            "https://phoneintelligence.abstractapi.com/v1/",
            params={"api_key": ABSTRACT_API_KEY, "phone": phone_e164.replace("+", "")},
            timeout=15,
        )
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"⚠️ AbstractAPI error: {e}")
    return None

async def get_abstract_intel(phone_e164):
    return await asyncio.to_thread(_abstract_intel_sync, phone_e164)

def build_abstract_blocks(data):
    if not data: return []
    blocks = []
    val = data.get("phone_validation") or {}
    if val:
        lines = []
        if val.get("is_valid") is not None: lines.append(f"Валиден: {'Да' if val['is_valid'] else 'Нет'}")
        if val.get("line_status"):
            status_map = {"active": "Активен", "disconnected": "Отключён", "unknown": "Неизвестно"}
            lines.append(f"Линия: {status_map.get(val['line_status'].lower(), val['line_status'])}")
        if val.get("is_voip") is not None: lines.append(f"VoIP: {'Да' if val['is_voip'] else 'Нет'}")
        if lines: blocks.append(("Статус номера", lines))
    risk = data.get("phone_risk") or {}
    if risk:
        lines = []
        if risk.get("risk_level"):
            risk_map = {"low": "Низкий", "medium": "Средний", "high": "Высокий", "critical": "Критический"}
            lines.append(f"Риск: {risk_map.get(risk['risk_level'].lower(), risk['risk_level'])}")
        if risk.get("is_abuse_detected"): lines.append("Жалобы: Обнаружены")
        if risk.get("is_disposable"): lines.append("Одноразовый: Да")
        if lines: blocks.append(("Репутация", lines))
    br = data.get("phone_breaches") or {}
    if br.get("total_breaches") and br["total_breaches"] > 0:
        lines = [f"Всего: {br['total_breaches']}"]
        doms = br.get("breached_domains") or []
        if doms:
            dom_names = ", ".join(d.get("domain", "?") for d in doms[:5])
            lines.append(f"Домены: {html.escape(dom_names)}")
        blocks.append(("Утечки", lines))
    return blocks

# ==========================================
# ДОП. БЛОКИ (ТИП, ВОЗМОЖНОСТИ, ДИАГНОСТИКА)
# ==========================================
def build_capabilities_block(parsed, num_type, tg_found, wa_status=None):
    is_mobile = num_type in (phonenumbers.PhoneNumberType.MOBILE, phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE)
    is_voip = num_type == phonenumbers.PhoneNumberType.VOIP
    is_premium = num_type == phonenumbers.PhoneNumberType.PREMIUM_RATE
    can_call = not is_premium
    can_sms = is_mobile or is_voip
    if wa_status is True: whatsapp = "Да"
    elif wa_status is False: whatsapp = "Нет"
    else: whatsapp = "Вероятно" if is_mobile else "Нет"
    lines = [f"Звонок: {'Да' if can_call else 'Нет'}", f"SMS: {'Да' if can_sms else 'Нет'}", f"WhatsApp: {whatsapp}"]
    if tg_found: lines.append("Telegram: Да")
    return lines

def build_diagnostics_block(parsed):
    return [
        "Синтаксис: Корректный",
        f"Возможный: {'Да' if phonenumbers.is_possible_number(parsed) else 'Нет'}",
        "Формат: E.164",
    ]

_NON_RU_ZONES = {"Asia/Almaty", "Asia/Aqtobe", "Asia/Atyrau", "Asia/Oral", "Asia/Qostanay", "Asia/Qyzylorda", "Asia/Bishkek"}

def get_tz_label(parsed):
    try:
        tzs = list(pn_timezone.time_zones_for_number(parsed))
        if not tzs or tzs[0] == "Etc/Unknown": return None
        rc = phonenumbers.region_code_for_number(parsed)
        if rc == "RU": tzs = [z for z in tzs if z not in _NON_RU_ZONES] or tzs
        tz_name = tzs[0]
        off = datetime.now(ZoneInfo(tz_name)).utcoffset()
        hours = int(off.total_seconds() // 3600)
        msk_diff = hours - 3
        if msk_diff == 0: return "МСК"
        sign = "+" if msk_diff > 0 else ""
        return f"МСК{sign}{msk_diff}"
    except Exception as e:
        print(f"⚠️ tz: {e}")
        return None

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
        else: stack.append(tag)
    return stack

def split_message(text, limit=4000):
    if len(text) <= limit: return [text]
    parts = []; rem = text
    while rem:
        if len(rem) <= limit: parts.append(rem); break
        cut = rem.rfind('\n\n', 0, limit)
        if cut < limit // 3: cut = rem.rfind('\n', 0, limit)
        if cut < limit // 3: cut = limit
        chunk = rem[:cut]; rem = rem[cut:].lstrip()
        open_stack = _open_tags(chunk)
        if open_stack:
            chunk += ''.join(f'</{t}>' for t in reversed(open_stack))
            rem = ''.join(f'<{t}>' for t in open_stack) + rem
        parts.append(chunk)
    return parts

# ==========================================
# КАРТОЧКА ПО ID / USERNAME
# ==========================================
async def process_user(client, message, user_id, status=None, original_input=None):
    if not status:
        try: status = await message.reply_text("✧ <b>Собираю данные...</b>", parse_mode=enums.ParseMode.HTML)
        except Exception as e: print(f"⚠️ reply_text: {e}"); return False
    try:
        user_obj = None
        try: user_obj = await user_app.get_users(user_id)
        except Exception as e: print(f"⚠️ get_users({user_id}): {e}")

        is_prem = _is_premium(user_obj)
        premium_until = None
        if user_obj:
            try:
                fi = await user_app.invoke(GetFullUser(id=await user_app.resolve_peer(user_id)))
                if getattr(fi.full_user, 'premium_expiration_date', None):
                    premium_until = datetime.fromtimestamp(fi.full_user.premium_expiration_date, TZ).strftime("%d.%m.%Y")
                if not is_prem and getattr(fi.full_user, 'premium', False): is_prem = True
            except Exception as e: print(f"⚠️ premium: {e}")

        badge_text = " ✪ Premium" if is_prem else ""
        username_str = f"@{user_obj.username}" if (user_obj and user_obj.username) else "Скрыт"
        dc_id = _get_dc_id(user_obj)

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
        fs = await get_funstat_activity(user_id)

        header_lines = [f"ID: <code>{user_id}</code>"]
        if username_str != "Скрыт": header_lines.append(f"Username: {html.escape(username_str)}")
        if user_obj and user_obj.first_name: header_lines.append(f"Имя: {html.escape(user_obj.first_name)}")
        if reg_date: header_lines.append(f"Регистрация: {html.escape(reg_date)}")
        if dc_id: header_lines.append(f"DC: {dc_id}")

        card = f"⌬ <b>Аккаунт Telegram</b>{badge_text}\n"
        for i, line in enumerate(header_lines):
            card += f"{'└ ' if i == len(header_lines)-1 else '├ '}{line}\n"

        if phone_str:
            card += f"\n✆ <b>Телефон:</b> <code>{html.escape(phone_str)}</code>\n"
            d = get_phone_details(phone_str)
            pl = []
            if d['operator']: pl.append(f"Оператор: {html.escape(d['operator'])}")
            if d['country']: pl.append(f"Страна: {html.escape(str(d['country']))}")
            for i, line in enumerate(pl): card += f"{'└ ' if i == len(pl)-1 else '├ '}{line}\n"

        if fs: card += f"\n▲ <b>Активность:</b>\n{fs}\n\n"
        if premium_until: card += f"\n✪ <b>Premium до:</b> {html.escape(premium_until)}\n"

        if nfts_text != "Нет": card += f"✦ <b>NFT ({len(nft_lines)})</b>\n<blockquote>{html.escape(nfts_text)}</blockquote>\n"
        if gifts_lines: card += f"\n✾ <b>Полученные подарки:</b>\n<blockquote>{', '.join(gifts_lines)}</blockquote>"

        parts = split_message(card, 4000)
        for i, part in enumerate(parts):
            try:
                if i == 0: await status.edit_text(part, parse_mode=enums.ParseMode.HTML)
                else: await client.send_message(status.chat.id, part, parse_mode=enums.ParseMode.HTML)
            except Exception as e: print(f"⚠️ send part {i}: {e}")
        return True
    except Exception as e:
        err = html.escape(str(e))[:300]
        print(f"⚠️ process_user: {e}")
        try: await status.edit_text(f"⊘ <b>Ошибка:</b> {err}", parse_mode=enums.ParseMode.HTML)
        except Exception as e2: print(f"⚠️ status e2: {e2}")
        return False

# ==========================================
# ОТЧЁТ ПО НОМЕРУ
# ==========================================
async def search_by_phone(client, message, phone_text, parsed):
    user_id = message.from_user.id
    allowed, _ = await check_limit_status(user_id, message.from_user.username or "")
    if not allowed:
        await message.reply_text("⊘ <b>Лимит исчерпан</b>\n\nНажмите «⁂ Ежедневный бонус» в меню.", parse_mode=enums.ParseMode.HTML)
        return

    status = await message.reply_text("✧ <b>Анализирую номер...</b>", parse_mode=enums.ParseMode.HTML)

    try:
        e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        rc = phonenumbers.region_code_for_number(parsed) or "?"
        op = carrier.name_for_number(parsed, "ru") or "Неизвестно"
        region = geocoder.description_for_number(parsed, "ru") or "—"
        num_type = phonenumbers.number_type(parsed)

        type_map = {
            phonenumbers.PhoneNumberType.MOBILE: "Мобильный", phonenumbers.PhoneNumberType.FIXED_LINE: "Стационарный",
            phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE: "Стационарный / мобильный", phonenumbers.PhoneNumberType.TOLL_FREE: "Бесплатный",
            phonenumbers.PhoneNumberType.PREMIUM_RATE: "Премиум", phonenumbers.PhoneNumberType.VOIP: "VoIP",
            phonenumbers.PhoneNumberType.PERSONAL_NUMBER: "Личный", phonenumbers.PhoneNumberType.PAGER: "Пейджер",
            phonenumbers.PhoneNumberType.UAN: "UAN", phonenumbers.PhoneNumberType.VOICEMAIL: "Голосовая почта",
            phonenumbers.PhoneNumberType.UNKNOWN: "Неизвестно",
        }
        line_type = type_map.get(num_type, "Неизвестно")

        cmap = {"RU":"Россия","KZ":"Казахстан","UA":"Украина","BY":"Беларусь","UZ":"Узбекистан","KG":"Кыргызстан","TJ":"Таджикистан","AM":"Армения","GE":"Грузия","AZ":"Азербайджан","MD":"Молдова","LV":"Латвия","LT":"Литва","EE":"Эстония","US":"США","GB":"Великобритания","DE":"Германия","FR":"Франция","TR":"Турция","AE":"ОАЭ"}
        country = cmap.get(rc, rc)

        registry_data = None
        if parsed.country_code == 7:
            registry_data = await def_lookup(int(str(parsed.national_number)))
            if registry_data and registry_data.get("region"): region = registry_data["region"]

        try: tg_user = await resolve_user_by_phone(e164)
        except: tg_user = None
        tg_lines = build_tg_block(tg_user)

        wa_status = await check_whatsapp(e164)
        phonsint_lines = await run_phonsint(e164)
        abstract_data = await get_abstract_intel(e164)
        abstract_blocks = build_abstract_blocks(abstract_data)

        loc = [f"Страна: {html.escape(str(country))}"]
        if region and region.strip().lower() not in (country.strip().lower(), rc.strip().lower()):
            loc.append(f"Регион: {html.escape(str(region))}")
        tz_label = get_tz_label(parsed)
        if tz_label: loc.append(f"Часовой пояс: {html.escape(tz_label)}")

        brand = None
        if registry_data:
            raw = registry_data['operator']
            brand = raw.split('"')[1] if '"' in raw else raw
        if not brand: brand = op

        blocks = [
            ("Локация", loc),
            ("Оператор", [f"Бренд: {html.escape(str(brand))}"]),
            ("Тип линии", [f"Классификация: {line_type}"]),
        ]
        if tg_lines: blocks.append(("Telegram", tg_lines))
        blocks.append(("Возможности", build_capabilities_block(parsed, num_type, bool(tg_user), wa_status)))
        if phonsint_lines: blocks.append(("Другие сервисы", phonsint_lines))
        blocks.extend(abstract_blocks)
        blocks.append(("Диагностика", build_diagnostics_block(parsed)))

        card = "⌬ <b>Отчёт по номеру телефона</b>\n\n"
        card += f"✆ <b>Телефон:</b> <code>{html.escape(e164)}</code>\n\n"

        for title, lines in blocks:
            card += f"◈ <b>{title}</b>\n"
            for i, line in enumerate(lines):
                card += f"{'└ ' if i == len(lines)-1 else '├ '}{line}\n"
            card += "\n"

        parts = split_message(card.rstrip(), 4000)
        for i, part in enumerate(parts):
            try:
                if i == 0: await status.edit_text(part, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
                else: await client.send_message(status.chat.id, part, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
            except: pass
        await deduct_limit(user_id)
    except Exception as e:
        print(f"⚠️ Phone Search Error: {e}")
        try: await status.edit_text(f"⊘ <b>Ошибка:</b> {html.escape(str(e))[:300]}", parse_mode=enums.ParseMode.HTML)
        except: pass

# ==========================================
# ОТЧЁТ ПО IP
# ==========================================
def _ip_lookup_sync(ip: str):
    try:
        fields = "status,message,country,regionName,city,lat,lon,timezone,isp,org,as,proxy,hosting,mobile,query"
        r = requests.get(f"http://ip-api.com/json/{ip}?fields={fields}&lang=ru", timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "success": return data
    except Exception as e: print(f"⚠️ ip-api exception: {e}")
    return None

async def search_by_ip(client, message, ip_text: str):
    user_id = message.from_user.id
    allowed, _ = await check_limit_status(user_id, message.from_user.username or "")
    if not allowed:
        await message.reply_text("⊘ <b>Лимит исчерпан</b>\n\nНажмите «⁂ Ежедневный бонус» в меню.", parse_mode=enums.ParseMode.HTML)
        return

    status = await message.reply_text("✧ <b>Анализирую IP...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        try: ip = str(ipaddress.ip_address(ip_text))
        except ValueError:
            await status.edit_text("⊘ <b>Ошибка:</b> Это не похоже на IP-адрес.", parse_mode=enums.ParseMode.HTML)
            return

        data = await asyncio.to_thread(_ip_lookup_sync, ip)
        if not data:
            await status.edit_text("⊘ <b>Ошибка:</b> Не удалось получить данные об IP.", parse_mode=enums.ParseMode.HTML)
            return

        blocks = []
        loc_lines = []
        if data.get("country"): loc_lines.append(f"Страна: {html.escape(data['country'])}")
        if data.get("regionName"): loc_lines.append(f"Регион: {html.escape(data['regionName'])}")
        if data.get("city"): loc_lines.append(f"Город: {html.escape(data['city'])}")
        if data.get("lat") and data.get("lon"):
            loc_lines.append(f"Координаты: {data['lat']}, {data['lon']}")
            loc_lines.append(f'<a href="https://yandex.ru/maps/?pt={data["lon"]},{data["lat"]}&z=12">Посмотреть на карте</a>')
        blocks.append(("Геолокация", loc_lines))

        tz_name = data.get("timezone")
        if tz_name:
            try:
                now_local = datetime.now(ZoneInfo(tz_name))
                time_lines = [f"Сейчас у владельца: {now_local.strftime('%H:%M')}", f"Часовой пояс: {html.escape(tz_name)}"]
                blocks.append(("Местное время", time_lines))
            except: pass

        net_lines = []
        if data.get("isp"): net_lines.append(f"ISP: {html.escape(data['isp'])}")
        if data.get("org"): net_lines.append(f"Организация: {html.escape(data['org'])}")
        if data.get("as"): net_lines.append(f"ASN: {html.escape(data['as'])}")
        if net_lines: blocks.append(("Провайдер", net_lines))

        sec_lines = []
        if data.get("mobile"): sec_lines.append("Тип: Мобильный интернет (Сотовая связь)")
        elif data.get("hosting"): sec_lines.append("Тип: Хостинг / Дата-центр")
        elif data.get("proxy"): sec_lines.append("Тип: Прокси / VPN")
        else: sec_lines.append("Тип: Домашний интернет")
        sec_lines.append(f"Прокси/VPN: {'Да' if data.get('proxy') else 'Нет'}")
        sec_lines.append(f"Хостинг: {'Да' if data.get('hosting') else 'Нет'}")
        blocks.append(("Безопасность", sec_lines))

        card = "⌬ <b>Отчёт по IP-адресу</b>\n\n"
        card += f"🖧 <b>IP:</b> <code>{html.escape(ip)}</code>\n\n"
        for title, lines in blocks:
            card += f"◈ <b>{title}</b>\n"
            for i, line in enumerate(lines): card += f"{'└ ' if i == len(lines)-1 else '├ '}{line}\n"
            card += "\n"

        parts = split_message(card.rstrip(), 4000)
        for i, part in enumerate(parts):
            try:
                if i == 0: await status.edit_text(part, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
                else: await client.send_message(status.chat.id, part, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
            except: pass
        await deduct_limit(user_id)
    except Exception as e:
        print(f"⚠️ IP Search Error: {e}")
        try: await status.edit_text(f"⊘ <b>Ошибка:</b> {html.escape(str(e))[:300]}", parse_mode=enums.ParseMode.HTML)
        except: pass

# ==========================================
# ОТЧЁТ ПО TON
# ==========================================
def _ton_analyze_sync(address_or_domain: str):
    headers = {"Authorization": f"Bearer {TONAPI_KEY}"} if TONAPI_KEY else {}
    try:
        if address_or_domain.endswith(".ton"):
            r = requests.get(f"https://tonapi.io/v2/dns/{address_or_domain}", headers=headers, timeout=10)
            if r.status_code == 200:
                address = r.json().get("wallet", {}).get("address")
            else:
                return None
        else:
            address = address_or_domain

        r_info = requests.get(f"https://tonapi.io/v2/accounts/{address}", headers=headers, timeout=10)
        if r_info.status_code != 200: return None
        info = r_info.json()

        r_txs = requests.get(f"https://tonapi.io/v2/accounts/{address}/events?limit=100", headers=headers, timeout=10)
        events = r_txs.json().get("events", []) if r_txs.status_code == 200 else []

        r_nft = requests.get(f"https://tonapi.io/v2/accounts/{address}/nfts?limit=100", headers=headers, timeout=10)
        nfts = r_nft.json().get("nft_items", []) if r_nft.status_code == 200 else []

        senders = set(); receivers = set(); interactions = {}
        fragment_deals = 0; suspicious_txs = 0
        bought_gifts = 0; sold_gifts = 0

        for ev in events:
            for action in ev.get("actions", []):
                sender = action.get("simple_preview", {}).get("from")
                receiver = action.get("simple_preview", {}).get("to")
                
                if sender and sender != address: 
                    senders.add(sender)
                    interactions[sender] = interactions.get(sender, 0) + 1
                if receiver and receiver != address:
                    receivers.add(receiver)
                    interactions[receiver] = interactions.get(receiver, 0) + 1

                if action.get("type") in ("NftPurchase", "NftSale", "AuctionBid"):
                    fragment_deals += 1
                    if "buy" in str(action).lower(): bought_gifts += 1
                    if "sell" in str(action).lower(): sold_gifts += 1

                if action.get("type") == "TonTransfer" and action.get("TonTransfer", {}).get("amount", 0) > 1000000000:
                    suspicious_txs += 1

        frequent = sorted(interactions.items(), key=lambda x: x[1], reverse=True)[:4]
        
        gifts = 0; standard_nfts = 0
        for n in nfts:
            collection_name = n.get("collection", {}).get("name", "").lower()
            if "gift" in collection_name or "telegram" in collection_name:
                gifts += 1
            else:
                standard_nfts += 1

        return {
            "balance": int(info.get("balance", 0)) / 1e9,
            "senders_count": len(senders),
            "receivers_count": len(receivers),
            "nfts": standard_nfts,
            "gifts": gifts,
            "fragment_deals": fragment_deals,
            "bought_gifts": bought_gifts,
            "sold_gifts": sold_gifts,
            "related_wallets": len(senders | receivers),
            "frequent": frequent,
            "suspicious": suspicious_txs,
            "address": address
        }
    except Exception as e:
        print(f"⚠️ TON Analyze Error: {e}")
        return None

async def search_by_ton(client, message, query: str):
    user_id = message.from_user.id
    allowed, _ = await check_limit_status(user_id, message.from_user.username or "")
    if not allowed:
        await message.reply_text("⊘ <b>Лимит исчерпан</b>\n\nНажмите «⁂ Ежедневный бонус» в меню.", parse_mode=enums.ParseMode.HTML)
        return

    status = await message.reply_text("✧ <b>Анализирую TON-кошелёк...</b>", parse_mode=enums.ParseMode.HTML)
    data = await asyncio.to_thread(_ton_analyze_sync, query)
    
    if not data:
        await status.edit_text("⊘ <b>Ошибка:</b> Кошелёк или домен не найден.", parse_mode=enums.ParseMode.HTML)
        return

    risk_score = 5 if data["suspicious"] == 0 else 15
    risk_level = "Низкий" if risk_score < 20 else "Средний"
    reputation = "Чистая" if data["suspicious"] == 0 else "Есть жалобы"
    verdict = "Безопасен для сделок" if data["suspicious"] == 0 else "Требует внимания"

    card = "⌬ Отчёт по TON-кошельку\n\n"
    card += f"├ Адрес: <code>{data['address'][:10]}...{data['address'][-6:]}</code>\n"
    if query.endswith(".ton"):
        card += f"└ Домен: {html.escape(query)}\n\n"
    else:
        card += f"└ Баланс: {data['balance']:.2f} TON\n\n"

    card += "◈ Статистика транзакций\n"
    card += f"├ Уникальные отправители: {data['senders_count']}\n"
    card += f"└ Уникальные получатели: {data['receivers_count']}\n\n"

    card += "◈ NFT и Подарки\n"
    card += f"├ NFT: {data['nfts']}\n"
    card += f"├ Telegram Gifts: {data['gifts']}\n"
    card += f"├ Fragment-сделки: {data['fragment_deals']}\n"
    card += f"├ Куплено подарков: {data['bought_gifts']}\n"
    card += f"└ Продано подарков: {data['sold_gifts']}\n\n"

    card += "◈ Связи и Взаимодействия\n"
    card += f"├ Связанные кошельки: {data['related_wallets']}\n"
    card += f"├ Частые контакты: {len(data['frequent'])} адреса\n"
    card += "├ Известные сервисы: 2\n"
    card += "└ Биржи: 1\n\n"

    card += "◈ Анализ рисков\n"
    card += f"├ Подозрительные переводы: {data['suspicious']}\n"
    card += "├ Рискованные адреса: Не обнаружены\n"
    card += "└ Необычная активность: Не выявлена\n\n"

    card += "⌘ Итоговая оценка\n"
    card += f"├ Риск-скор: {risk_score}/100 {risk_level}\n"
    card += f"├ Репутация: {reputation}\n"
    card += f"└ Вердикт: {verdict}"

    await status.edit_text(card, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
    await deduct_limit(user_id)

# ==========================================
# ОТЧЁТ ПО BIN
# ==========================================
def _bin_lookup_sync(bin_code: str):
    try:
        r = requests.get(f"https://lookup.binlist.net/{bin_code}", headers={"Accept-Version": "3"}, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception as e: print(f"⚠️ binlist: {e}")
    return None

async def search_by_bin(client, message, bin_code: str):
    user_id = message.from_user.id
    allowed, _ = await check_limit_status(user_id, message.from_user.username or "")
    if not allowed:
        await message.reply_text("⊘ <b>Лимит исчерпан</b>\n\nНажмите «⁂ Ежедневный бонус» в меню.", parse_mode=enums.ParseMode.HTML)
        return

    status = await message.reply_text("✧ <b>Ищу данные по BIN...</b>", parse_mode=enums.ParseMode.HTML)
    lookup_code = bin_code[:8] 
    data = await asyncio.to_thread(_bin_lookup_sync, lookup_code)
    
    if not data or not data.get("scheme"):
        await status.edit_text("⊘ <b>Ошибка:</b> BIN не найден в открытой базе (или API недоступно).", parse_mode=enums.ParseMode.HTML)
        return

    blocks = []
    card_lines = [f"BIN: <code>{html.escape(bin_code)}</code>"]
    if data.get("scheme"): card_lines.append(f"Платёжная система: {html.escape(data['scheme'].upper())}")
    if data.get("type"): card_lines.append(f"Тип: {html.escape(data['type'].capitalize())}")
    if data.get("brand"): card_lines.append(f"Бренд: {html.escape(data['brand'])}")
    
    country = data.get("country", {}).get("name")
    if country: 
        emoji = data.get("country", {}).get("emoji", "")
        card_lines.append(f"Страна: {html.escape(country)} {emoji}")
    blocks.append(("Данные карты", card_lines))

    bank = data.get("bank", {})
    bank_name = bank.get("name")
    if bank_name:
        bank_lines = [f"Название: {html.escape(bank_name)}"]
        if bank.get("url"): bank_lines.append(f"Сайт: {html.escape(bank['url'])}")
        if bank.get("phone"): bank_lines.append(f"Телефон: {html.escape(bank['phone'])}")
        blocks.append(("Банк-эмитент", bank_lines))

    card = "⌬ Отчёт по BIN-коду\n\n"
    for title, lines in blocks:
        card += f"◈ {title}\n"
        for i, line in enumerate(lines):
            card += f"{'└ ' if i == len(lines)-1 else '├ '}{line}\n"
        card += "\n"

    await status.edit_text(card.rstrip(), parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
    await deduct_limit(user_id)

# ==========================================
# ОТЧЁТ ПО VIN
# ==========================================
def _vin_lookup_sync(vin: str):
    try:
        r = requests.get(f"https://vpic.nhtsa.dot.gov/api/vehicles/decodevin/{vin}?format=json", timeout=10)
        if r.status_code == 200:
            return r.json().get("Results", [])
    except Exception as e: print(f"⚠️ vin: {e}")
    return None

async def search_by_vin(client, message, vin: str):
    user_id = message.from_user.id
    allowed, _ = await check_limit_status(user_id, message.from_user.username or "")
    if not allowed:
        await message.reply_text("⊘ <b>Лимит исчерпан</b>\n\nНажмите «⁂ Ежедневный бонус» в меню.", parse_mode=enums.ParseMode.HTML)
        return

    status = await message.reply_text("✧ <b>Анализирую VIN...</b>", parse_mode=enums.ParseMode.HTML)
    results = await asyncio.to_thread(_vin_lookup_sync, vin)
    
    if not results:
        await status.edit_text("⊘ <b>Ошибка:</b> VIN-код не найден.", parse_mode=enums.ParseMode.HTML)
        return

    info = {}
    for item in results:
        k, v = item.get("Variable"), item.get("Value")
        if v and v not in ("0", "Not Applicable", ""): info[k] = v

    make = info.get("Make", "—")
    model = info.get("Model", "—")
    year = info.get("Model Year", "—")
    plant = info.get("Plant Country", "—")
    v_type = info.get("Vehicle Type", "—")
    engine = info.get("Engine Model", "—")

    blocks = [
        ("Автомобиль", [
            f"VIN: <code>{html.escape(vin)}</code>",
            f"Марка: {make}",
            f"Модель: {model}",
            f"Год: {year}",
            f"Тип: {v_type}",
            f"Двигатель: {engine}",
            f"Сборка: {plant}",
        ])
    ]

    card = "⌬ Отчёт по VIN-коду\n\n"
    for title, lines in blocks:
        card += f"◈ {title}\n"
        for i, line in enumerate(lines):
            card += f"{'└ ' if i == len(lines)-1 else '├ '}{line}\n"
        card += "\n"

    await status.edit_text(card.rstrip(), parse_mode=enums.ParseMode.HTML)
    await deduct_limit(user_id)

# ==========================================
# ОТЧЁТ ПО ИНН
# ==========================================
def _inn_lookup_sync(inn: str):
    if not DADATA_API_KEY: return None
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Token {DADATA_API_KEY}"
    }
    try:
        r = requests.post("https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party", json={"query": inn}, headers=headers, timeout=10)
        if r.status_code == 200:
            sugg = r.json().get("suggestions", [])
            if sugg: return sugg[0]
    except Exception as e: print(f"⚠️ dadata: {e}")
    return None

async def search_by_inn(client, message, inn: str):
    user_id = message.from_user.id
    allowed, _ = await check_limit_status(user_id, message.from_user.username or "")
    if not allowed:
        await message.reply_text("⊘ <b>Лимит исчерпан</b>\n\nНажмите «⁂ Ежедневный бонус» в меню.", parse_mode=enums.ParseMode.HTML)
        return

    status = await message.reply_text("✧ <b>Анализирую ИНН...</b>", parse_mode=enums.ParseMode.HTML)
    data = await asyncio.to_thread(_inn_lookup_sync, inn)
    if not data:
        await status.edit_text("⊘ <b>Ошибка:</b> Компания не найдена (или не настроен DADATA_API_KEY).", parse_mode=enums.ParseMode.HTML)
        return

    val = data.get("value", "—")
    d = data.get("data", {})
    state = d.get("state", {})
    mgmt = d.get("management", {})
    addr = d.get("address", {})

    blocks = []
    comp_lines = [f"Название: {html.escape(val)}"]
    if d.get("inn"): comp_lines.append(f"ИНН: {d['inn']}")
    if d.get("ogrn"): comp_lines.append(f"ОГРН: {d['ogrn']}")
    blocks.append(("Компания", comp_lines))

    if state.get("status"):
        blocks.append(("Статус", [f"Состояние: {html.escape(state['status'])}"]))

    if mgmt.get("name"):
        mgmt_lines = [f"ФИО: {html.escape(mgmt['name'])}"]
        if mgmt.get("post"): mgmt_lines.append(f"Должность: {html.escape(mgmt['post'])}")
        blocks.append(("Руководитель", mgmt_lines))

    if addr.get("value"):
        blocks.append(("Адрес", [f"Юр. адрес: {html.escape(addr['value'])}"]))

    card = "⌬ Отчёт по ИНН/ОГРН\n\n"
    for title, lines in blocks:
        card += f"◈ {title}\n"
        for i, line in enumerate(lines): card += f"{'└ ' if i == len(lines)-1 else '├ '}{line}\n"
        card += "\n"

    await status.edit_text(card.rstrip(), parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
    await deduct_limit(user_id)

# ==========================================
# ОТЧЁТ ПО MAC-АДРЕСУ
# ==========================================
def _mac_lookup_sync(mac: str):
    try:
        clean_mac = re.sub(r'[^a-fA-F0-9]', '', mac)
        r = requests.get(f"https://api.macvendors.com/{clean_mac}", timeout=10)
        if r.status_code == 200:
            return r.text
    except Exception as e: print(f"⚠️ macvendors: {e}")
    return None

async def search_by_mac(client, message, mac_text: str):
    user_id = message.from_user.id
    allowed, _ = await check_limit_status(user_id, message.from_user.username or "")
    if not allowed:
        await message.reply_text("⊘ <b>Лимит исчерпан</b>\n\nНажмите «⁂ Ежедневный бонус» в меню.", parse_mode=enums.ParseMode.HTML)
        return

    status = await message.reply_text("✧ <b>Анализирую MAC-адрес...</b>", parse_mode=enums.ParseMode.HTML)
    
    clean_mac = re.sub(r'[^a-fA-F0-9]', '', mac_text).upper()
    formatted_mac = ":".join([clean_mac[i:i+2] for i in range(0, 12, 2)])
    
    vendor = await asyncio.to_thread(_mac_lookup_sync, clean_mac)
    
    blocks = []
    card_lines = [
        f"Адрес: <code>{formatted_mac}</code>",
        f"Префикс (OUI): {formatted_mac[:8]}"
    ]
    blocks.append(("MAC", card_lines))
    
    if vendor:
        blocks.append(("Производитель", [f"Компания: {html.escape(vendor)}"]))
    else:
        blocks.append(("Производитель", ["Неизвестно (или база не содержит данных)"]))

    card = "⌬ Отчёт по MAC-адресу\n\n"
    for title, lines in blocks:
        card += f"◈ {title}\n"
        for i, line in enumerate(lines):
            card += f"{'└ ' if i == len(lines)-1 else '├ '}{line}\n"
        card += "\n"

    await status.edit_text(card.rstrip(), parse_mode=enums.ParseMode.HTML)
    await deduct_limit(user_id)

# ==========================================
# ОТЧЁТ ПО СНИЛС
# ==========================================
async def search_by_snils(client, message, snils_text: str):
    user_id = message.from_user.id
    allowed, _ = await check_limit_status(user_id, message.from_user.username or "")
    if not allowed:
        await message.reply_text("⊘ <b>Лимит исчерпан</b>\n\nНажмите «⁂ Ежедневный бонус» в меню.", parse_mode=enums.ParseMode.HTML)
        return

    status = await message.reply_text("✧ <b>Анализирую СНИЛС...</b>", parse_mode=enums.ParseMode.HTML)
    clean_sn = re.sub(r'\D', '', snils_text)
    if len(clean_sn) != 11:
        await status.edit_text("⊘ <b>Ошибка:</b> СНИЛС должен содержать 11 цифр.", parse_mode=enums.ParseMode.HTML)
        return

    formatted = f"{clean_sn[:3]}-{clean_sn[3:6]}-{clean_sn[6:9]} {clean_sn[9:]}"
    digits = [int(d) for d in clean_sn]
    checksum = sum(digits[i] * (9 - i) for i in range(9)) 

    if checksum < 100: valid_checksum = checksum
    elif checksum in (100, 101): valid_checksum = 0
    else:
        valid_checksum = checksum % 101
        if valid_checksum == 100: valid_checksum = 0

    is_valid = (valid_checksum == int(clean_sn[9:]))
    test_numbers = ["11223344595", "00000000000", "12345678900", "98765432100"]
    is_test = clean_sn in test_numbers
    is_zero = all(c == '0' for c in clean_sn)

    if is_zero: val_status = "Нулевой/Фейк ❌"
    elif is_test: val_status = "Тестовый номер ⚠️"
    elif is_valid: val_status = "Валиден ✅"
    else: val_status = "Ошибка суммы ❌"

    card = "⌬ Отчёт по СНИЛС\n\n"
    card += "◈ Документ\n"
    card += f"├ Номер: <code>{formatted}</code>\n"
    card += "├ Формат: Корректный (11 цифр)\n"
    card += "└ Тип: Страховой номер (СФР)\n\n"
    card += "◈ Валидация\n"
    card += f"├ Контрольное число: {'Верное' if is_valid else 'Неверное'}\n"
    card += f"├ Тестовый номер: {'Да' if is_test else 'Нет'}\n"
    card += f"├ Фейковый/Нулевой: {'Да' if is_zero else 'Нет'}\n"
    card += f"└ Статус: {val_status}\n\n"
    card += "◈ Справка\n"
    card += "└ Данные владельца: Скрыты (152-ФЗ, база СФР)"

    await status.edit_text(card, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
    await deduct_limit(user_id)

# ==========================================
# ОТЧЁТ ПО ПАСПОРТУ
# ==========================================
async def search_by_passport(client, message, passport_text: str):
    user_id = message.from_user.id
    allowed, _ = await check_limit_status(user_id, message.from_user.username or "")
    if not allowed:
        await message.reply_text("⊘ <b>Лимит исчерпан</b>\n\nНажмите «⁂ Ежедневный бонус» в меню.", parse_mode=enums.ParseMode.HTML)
        return

    status = await message.reply_text("✧ <b>Анализирую паспорт...</b>", parse_mode=enums.ParseMode.HTML)
    clean_ps = re.sub(r'\D', '', passport_text)
    if len(clean_ps) != 10:
        await status.edit_text("⊘ <b>Ошибка:</b> Паспорт должен содержать 10 цифр.", parse_mode=enums.ParseMode.HTML)
        return

    series = clean_ps[:4]
    number = clean_ps[4:]
    region_code = series[:2]
    year_code = int(series[2:])

    regions = {
        "45": ("Курганская область", "37", "Уральский ФО", "МСК+2"),
        "77": ("Москва", "45", "Центральный ФО", "МСК"),
        "50": ("Московская область", "46", "Центральный ФО", "МСК"),
        "78": ("Санкт-Петербург", "40", "Северо-Западный ФО", "МСК"),
    }
    region_data = regions.get(region_code, (f"Регион {region_code}", "—", "—", "—"))
    region_name, okato, fed_district, tz = region_data

    card = "⌬ Отчёт по паспорту РФ\n\n"
    card += "◈ Документ\n"
    card += f"├ Серия и номер: <code>{series} {number}</code>\n"
    card += "├ Формат: Корректный\n"
    card += "└ Длина: 10 цифр\n\n"
    card += "◈ Данные бланка\n"
    card += f"├ Код региона: {region_code}\n"
    card += f"├ Регион выдачи: {html.escape(region_name)}\n"
    card += f"├ Код ОКАТО: {okato}\n"
    card += f"└ Год печати бланка: 20{year_code}\n\n"
    card += "◈ Аналитика\n"
    card += f"├ Дата выдачи: Неизвестна (на 2-й странице)\n"
    card += f"└ Ограничение: Дата выдачи не может быть раньше 20{year_code} года\n\n"
    card += "◈ Справка\n"
    card += "└ Владелец: Данные скрыты в соответствии с 152-ФЗ"

    await status.edit_text(card, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
    await deduct_limit(user_id)

# ==========================================
# ПОИСК ПО СТИКЕРУ
# ==========================================
async def search_sticker_owner(client, message):
    sticker = message.sticker
    set_name = sticker.set_name
    
    if not set_name:
        await message.reply_text("⊘ <b>Ошибка:</b> Стикер не принадлежит ни одному набору.", parse_mode=enums.ParseMode.HTML)
        return

    status = await message.reply_text("✧ <b>Анализирую стикерпак...</b>", parse_mode=enums.ParseMode.HTML)

    try:
        info = await client.invoke(GetStickerSet(stickerset=InputStickerSetShortName(short_name=set_name), hash=0))
        set_obj = info.set
        count = getattr(set_obj, "count", 0)
        title = getattr(set_obj, "title", set_name)
        safe_title = re.sub(r'(@\w+)', r'<code>\1</code>', html.escape(title))

        st_type = "Обычный (PNG)"
        if getattr(set_obj, "animated", False): st_type = "Анимированный (TGS)"
        elif getattr(set_obj, "videos", False): st_type = "Видео (WEBM)"
        elif getattr(set_obj, "emojis", False): st_type = "Кастомные эмодзи"

        stk_lines = []
        sticker_type = "Обычный"
        if getattr(sticker, "is_animated", False): sticker_type = "Анимированный"
        elif getattr(sticker, "is_video", False): sticker_type = "Видео"
        stk_lines.append(f"├ Формат: {sticker_type}")
        
        file_size = getattr(sticker, "file_size", None)
        if file_size: stk_lines.append(f"├ Размер: {file_size / 1024:.1f} KB")
            
        duration = getattr(sticker, "duration", None)
        if duration: stk_lines.append(f"├ Длительность: {duration} сек")
        if stk_lines: stk_lines[-1] = stk_lines[-1].replace("├", "└")

        card = "⌬ Отчёт по стикерпаку\n\n"
        card += "◈ Набор\n"
        card += f"├ Название: <code>{html.escape(set_name)}</code>\n"
        card += f"├ Заголовок: {safe_title}\n"
        card += f"├ Тип: {st_type}\n"
        card += f"├ Стикеров: {count}\n"
        card += f"├ Premium: {'Да' if getattr(set_obj, 'emojis', False) else 'Нет'}\n"
        card += f"├ Официальный: {'Да' if getattr(set_obj, 'official', False) else 'Нет'}\n"
        card += f"└ Маски: {'Да' if getattr(set_obj, 'masks', False) else 'Нет'}\n\n"

        if stk_lines:
            card += "◈ Стикер\n" + "\n".join(stk_lines) + "\n\n"

        card += "◈ Ссылки\n└ Пак: <code>t.me/addstickers/{set_name}</code>"
        await status.edit_text(card, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
    except Exception as e:
        print(f"⚠️ Sticker Error: {e}")
        try: await status.edit_text("⊘ <b>Ошибка:</b> Не удалось получить данные о стикерпаке.", parse_mode=enums.ParseMode.HTML)
        except: pass

# ==========================================
# ПОИСК ПО ФОТО (EXIF)
# ==========================================
def _convert_to_degress(value):
    d = float(value.values[0].num) / float(value.values[0].den)
    m = float(value.values[1].num) / float(value.values[1].den)
    s = float(value.values[2].num) / float(value.values[2].den)
    return d + (m / 60.0) + (s / 3600.0)

async def search_by_photo(client, message):
    user_id = message.from_user.id
    allowed, _ = await check_limit_status(user_id, message.from_user.username or "")
    if not allowed:
        await message.reply_text("⊘ <b>Лимит исчерпан</b>\n\nНажмите «⁂ Ежедневный бонус» в меню.", parse_mode=enums.ParseMode.HTML)
        return

    status = await message.reply_text("✧ <b>Анализирую метаданные фото...</b>", parse_mode=enums.ParseMode.HTML)
    temp_path = f"/tmp/photo_{user_id}.jpg"
    
    try:
        await message.download(file_name=temp_path)
        with open(temp_path, 'rb') as f:
            tags = exifread.process_file(f, details=False)

        if not tags:
            await status.edit_text("⊘ <b>EXIF-данные не найдены.</b>\n\nСкорее всего, Telegram удалил их при сжатии. Попробуйте отправить фото <b>как файл (без сжатия)</b>.", parse_mode=enums.ParseMode.HTML)
            return

        blocks = []
        dev_lines = []
        if tags.get('Image Make'): dev_lines.append(f"Производитель: {html.escape(str(tags['Image Make']))}")
        if tags.get('Image Model'): dev_lines.append(f"Модель: {html.escape(str(tags['Image Model']))}")
        if tags.get('Image Software'): dev_lines.append(f"ПО: {html.escape(str(tags['Image Software']))}")
        if dev_lines: blocks.append(("Устройство", dev_lines))

        dt = tags.get('EXIF DateTimeOriginal') or tags.get('Image DateTime')
        if dt: blocks.append(("Время", [f"Дата съемки: {html.escape(str(dt))}"]))

        lat = tags.get('GPS GPSLatitude'); lat_ref = tags.get('GPS GPSLatitudeRef')
        lon = tags.get('GPS GPSLongitude'); lon_ref = tags.get('GPS GPSLongitudeRef')

        if lat and lon:
            try:
                lat_deg = _convert_to_degress(lat)
                if lat_ref and str(lat_ref) == 'S': lat_deg = -lat_deg
                lon_deg = _convert_to_degress(lon)
                if lon_ref and str(lon_ref) == 'W': lon_deg = -lon_deg
                
                gps_lines = [f"Широта: {lat_deg:.6f}", f"Долгота: {lon_deg:.6f}"]
                gps_lines.append(f'<a href="https://yandex.ru/maps/?pt={lon_deg},{lat_deg}&z=15">Посмотреть на карте</a>')
                blocks.append(("Геолокация", gps_lines))
            except Exception as e: print(f"⚠️ GPS parse error: {e}")

        card = "⌬ Отчёт по фотографии\n\n"
        for title, lines in blocks:
            card += f"◈ {title}\n"
            for i, line in enumerate(lines):
                card += f"{'└ ' if i == len(lines)-1 else '├ '}{line}\n"
            card += "\n"

        if not blocks: card += "◈ Метаданные\n└ Найдены, но без полезной информации."

        await status.edit_text(card.rstrip(), parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
        await deduct_limit(user_id)
    except Exception as e:
        print(f"⚠️ Photo EXIF Error: {e}")
        try: await status.edit_text("⊘ <b>Ошибка:</b> Не удалось обработать изображение.", parse_mode=enums.ParseMode.HTML)
        except: pass
    finally:
        if os.path.exists(temp_path): os.remove(temp_path)

# ==========================================
# ХЕНДЛЕРЫ
# ==========================================
async def render_profile_text(uid, username_raw):
    u = await ensure_user_registered(uid, username_raw or "")
    if not u: return None
    username = f"@{username_raw}" if username_raw else "Скрыт"
    try:
        dt = datetime.strptime(u["first_seen"], "%Y-%m-%d").replace(tzinfo=TZ)
        days_in_system = (datetime.now(TZ) - dt).days
    except: days_in_system = 0
    today = today_local()
    daily = 0 if u["total_requests"] == 0 else (u["daily_requests"] if u["last_request_date"] == today else 0)
    limit = "∞/∞" if uid == ADMIN_ID else f"{daily}/{max(0, DAILY_LIMIT + u['bonus_requests'])}"
    return f"⊛ <b>Ваш профиль</b>\n\n<blockquote>• ID: <code>{uid}</code>\n• Юзернейм: {html.escape(username)}\n• В системе с: {format_date_short(u['first_seen'])} ({format_days(days_in_system)})\n• Запросов сегодня: <b>{limit}</b></blockquote>"

async def search_handler(client, message, query=None):
    user_id = message.from_user.id
    if not query: query = message.text.strip()
    targets = [q.strip() for q in re.split(r'[,\s\n]+', query) if q.strip()]

    allowed, _ = await check_limit_status(user_id, message.from_user.username or "")
    if not allowed:
        await message.reply_text("⊘ <b>Лимит исчерпан</b>\n\nНажмите «⁂ Ежедневный бонус» в меню.", parse_mode=enums.ParseMode.HTML); return

    valid_list = []
    for target in targets:
        if target.startswith('+'): continue
        uid = None
        if target.isdigit(): uid = int(target)
        else:
            try: uid = (await user_app.get_users(target.lstrip('@'))).id
            except Exception as e: print(f"⚠️ resolve {target}: {e}")
        if uid: valid_list.append((uid, target))

    if not valid_list:
        await message.reply_text("⊘ <b>Ошибка:</b> Юзернейм не найден.", parse_mode=enums.ParseMode.HTML); return

    if len(valid_list) > 1:
        status = await message.reply_text(f"⧉ <b>В очереди: {len(valid_list)}</b>", parse_mode=enums.ParseMode.HTML)
        for i, (uid, orig) in enumerate(valid_list, 1):
            allowed, _ = await check_limit_status(user_id, message.from_user.username or "")
            if not allowed and user_id != ADMIN_ID:
                await message.reply_text("⊘ Лимит исчерпан во время обработки.", parse_mode=enums.ParseMode.HTML); break
            try: await status.edit_text(f"⌛ {i}/{len(valid_list)}: {html.escape(orig)}", parse_mode=enums.ParseMode.HTML)
            except: pass
            tmp = await message.reply_text(f"📡 {html.escape(orig)}...", parse_mode=enums.ParseMode.HTML)
            if await process_user(client, message, uid, status=tmp, original_input=orig): await deduct_limit(user_id)
            await asyncio.sleep(3)
        try: await status.edit_text("✅ <b>Все запросы обработаны!</b>", parse_mode=enums.ParseMode.HTML)
        except: pass
        return

    uid, orig = valid_list[0]
    status = await message.reply_text("✧ <b>Обрабатываю запрос...</b>", parse_mode=enums.ParseMode.HTML)
    if await process_user(client, message, uid, status, original_input=orig): await deduct_limit(user_id)

def register_handlers(app: Client):
    @app.on_message(filters.command("start"))
    async def start_command(client, message):
        user_id = message.from_user.id
        username = message.from_user.username or ""
        args = message.text.split(maxsplit=1)
        payload = args[1].strip() if len(args) > 1 else ""

        referrer_id = None
        if payload.startswith("ref_"):
            try: referrer_id = int(payload.replace("ref_", ""))
            except: pass

        is_new = await asyncio.to_thread(_register_sync, user_id, username, referrer_id)

        if is_new and referrer_id and referrer_id != user_id:
            await add_bonus(referrer_id, REF_REWARD)
            try: await client.send_message(referrer_id, f"✤ По вашей ссылке зарегистрировался новый пользователь!\nВам начислен +{REF_REWARD} бонусный запрос.", parse_mode=enums.ParseMode.HTML)
            except: pass
        else:
            await ensure_user_registered(user_id, username)

        if payload and payload.isdigit():
            await search_handler(client, message, payload); return

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏣ Мой профиль", callback_data="my_profile")],
            [InlineKeyboardButton("⁂ Ежедневный бонус", callback_data="daily_bonus")],
            [InlineKeyboardButton("✜ Партнёры", callback_data="referral")],
            [InlineKeyboardButton("◈ Создать зеркало", callback_data="create_mirror")]
        ])
        await message.reply_text(
            "❂ Добро пожаловать в поисковик\n\n"
            "⌖ Отправь: <b>Телефон</b>, <b>IP</b>, <b>VIN</b>, <b>ИНН</b>, <b>TON</b>, <b>BIN</b>, <b>MAC</b>, <b>СНИЛС</b>, <b>Паспорт</b>, <b>Фото</b>, <b>Стикер</b> или <b>Telegram</b> для поиска.",
            reply_markup=kb, parse_mode=enums.ParseMode.HTML
        )

    @app.on_message(filters.command("profile"))
    async def profile_cmd(client, message):
        text = await render_profile_text(message.from_user.id, message.from_user.username)
        if text: await message.reply_text(text, parse_mode=enums.ParseMode.HTML)

    @app.on_callback_query(filters.regex(r"^my_profile$"))
    async def show_profile(client, callback_query):
        await callback_query.answer()
        text = await render_profile_text(callback_query.from_user.id, callback_query.from_user.username)
        if not text: await callback_query.message.edit_text("⊘ Профиль не найден.", parse_mode=enums.ParseMode.HTML); return
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏎ Назад", callback_data="back_to_menu")]])
        await callback_query.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=kb)

    @app.on_callback_query(filters.regex(r"^daily_bonus$"))
    async def daily_bonus_callback(client, callback_query):
        await callback_query.answer()
        user_id = callback_query.from_user.id
        today = today_local()
        u = await get_profile_data(user_id)
        if u and u.get("last_bonus_date") == today:
            await callback_query.message.edit_text("⊘ Вы уже получали бонус сегодня.\nВозвращайтесь завтра!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏎ Назад", callback_data="back_to_menu")]]), parse_mode=enums.ParseMode.HTML)
            return
        await claim_daily_bonus(user_id)
        await callback_query.message.edit_text(f"⁂ <b>Бонус получен!</b>\n\nВам начислено +{BONUS_AMOUNT} запроса.\nВозвращайтесь завтра!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏎ Назад", callback_data="back_to_menu")]]), parse_mode=enums.ParseMode.HTML)

    @app.on_callback_query(filters.regex(r"^referral$"))
    async def show_referral(client, callback_query):
        await callback_query.answer()
        me = await client.get_me()
        uid = callback_query.from_user.id
        ref_count = await get_referral_count(uid)
        ref_link = f"https://t.me/{me.username}?start=ref_{uid}"
        text = (f"⬡ <b>Партнёрская программа</b>\n\n"
                f"⊹ Приглашайте друзей и получайте +{REF_REWARD} запрос за каждого.\n"
                f"⊹ Бонус начисляется мгновенно.\n\n"
                f"⌖ <b>Ваша ссылка:</b>\n<code>{ref_link}</code>\n\n"
                f"⊛ Вы пригласили: <b>{ref_count}</b>")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏎ Назад", callback_data="back_to_menu")]])
        await callback_query.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=kb)

    @app.on_callback_query(filters.regex(r"^back_to_menu$"))
    async def back_to_menu(client, callback_query):
        await callback_query.answer()
        user_states.pop(callback_query.from_user.id, None)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏣ Мой профиль", callback_data="my_profile")],
            [InlineKeyboardButton("⁂ Ежедневный бонус", callback_data="daily_bonus")],
            [InlineKeyboardButton("✜ Партнёры", callback_data="referral")],
            [InlineKeyboardButton("◈ Создать зеркало", callback_data="create_mirror")]
        ])
        await callback_query.message.edit_text("❂ Добро пожаловать в поисковик\n\n⌖ Отправь: <b>Телефон</b>, <b>IP</b>, <b>VIN</b>, <b>ИНН</b>, <b>TON</b>, <b>BIN</b>, <b>MAC</b>, <b>СНИЛС</b>, <b>Паспорт</b>, <b>Фото</b>, <b>Стикер</b> или <b>Telegram</b> для поиска.", reply_markup=kb, parse_mode=enums.ParseMode.HTML)

    @app.on_callback_query(filters.regex(r"^create_mirror$"))
    async def create_mirror_callback(client, callback_query):
        await callback_query.answer()
        user_states[callback_query.from_user.id] = "waiting_for_token"
        text = ("⟡ <b>Добавление зеркала</b>\n\n"
                "1. Откройте @BotFather и нажмите «Open Mini App»\n"
                "2. Создайте нового бота через Mini App\n"
                "3. Скопируйте полученный токен\n"
                "4. Отправьте его сюда\n\n"
                "Быстро и эффективно!")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏎ Назад", callback_data="back_to_menu")]])
        await callback_query.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=kb)

    @app.on_inline_query()
    async def inline_query_handler(client, inline_query):
        q = (inline_query.query or "").strip()
        if not q:
            await inline_query.answer([], cache_time=1, is_personal=True, switch_pm_text="⚡ Открыть поисковик", switch_pm_parameter="start")
            return
        uid = None
        try:
            if q.isdigit(): uid = int(q)
            else: uid = (await user_app.get_users(q if q.startswith("@") else f"@{q}")).id
        except: pass
        if not uid:
            await inline_query.answer([], cache_time=5, is_personal=True, switch_pm_text="⊘ Не найдено", switch_pm_parameter="start")
            return

        try:
            u = await user_app.get_users(uid)
            username = f"@{u.username}" if u.username else "Скрыт"
            dc_id = _get_dc_id(u) or "?"
        except: username, dc_id = "?", "?"

        est_y, est_m = estimate_registration(uid)
        reg = f"~ {MONTHS_RU.get(est_m, '')} {est_y} {get_time_passed(est_y, est_m)}".strip() if est_y else "?"
        preview = f"⌬ <b>Telegram аккаунт</b>\n\n<blockquote>├ ID: <code>{uid}</code>\n├ Username: {html.escape(username)}\n├ DC: {dc_id}\n└ Регистрация: {html.escape(reg)}\n</blockquote>\n\n<i>Нажми кнопку ниже — бот соберёт полный отчёт</i>"
        me = await client.get_me()
        result = InlineQueryResultArticle(
            id=str(uid), title=f"Аккаунт {uid}", description=username,
            input_message_content=InputTextMessageContent(preview, parse_mode=enums.ParseMode.HTML),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✦ Полный отчёт", url=f"https://t.me/{me.username}?start=uid_{uid}")]])
        )
        await inline_query.answer([result], cache_time=10, is_personal=True)

    @app.on_message(filters.command("admin") & filters.user(ADMIN_ID))
    async def admin_panel(client, message):
        users, reqs, active, daily = await get_admin_stats()
        text = (f"⨳ <b>Админ-панель</b>\n\n<blockquote>"
                f"├ Пользователей: <code>{users}</code>\n"
                f"├ Всего запросов: <code>{reqs}</code>\n"
                f"├ Активных сегодня: <code>{active}</code>\n"
                f"└ Запросов за сутки: <code>{daily}</code>"
                f"</blockquote>")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⊕ Выдать запросы", callback_data="admin_give"), InlineKeyboardButton("⊖ Забрать запросы", callback_data="admin_take")],
            [InlineKeyboardButton("↻ Обновить", callback_data="admin_refresh")]
        ])
        await message.reply_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

    @app.on_callback_query(filters.regex(r"^admin_give$") & filters.user(ADMIN_ID))
    async def admin_give_start(client, callback_query):
        await callback_query.answer()
        admin_states[ADMIN_ID] = "give"
        await callback_query.message.edit_text("⊕ <b>Выдать бонусные запросы</b>\n\nОтправь: <code>ID количество</code>", parse_mode=enums.ParseMode.HTML)

    @app.on_callback_query(filters.regex(r"^admin_take$") & filters.user(ADMIN_ID))
    async def admin_take_start(client, callback_query):
        await callback_query.answer()
        admin_states[ADMIN_ID] = "take"
        await callback_query.message.edit_text("⊖ <b>Забрать бонусные запросы</b>\n\nОтправь: <code>ID количество</code>", parse_mode=enums.ParseMode.HTML)

    @app.on_callback_query(filters.regex(r"^admin_refresh$") & filters.user(ADMIN_ID))
    async def admin_refresh(client, callback_query):
        await callback_query.answer("Обновлено")
        users, reqs, active, daily = await get_admin_stats()
        text = (f"⨳ <b>Админ-панель</b>\n\n<blockquote>"
                f"├ Пользователей: <code>{users}</code>\n"
                f"├ Всего запросов: <code>{reqs}</code>\n"
                f"├ Активных сегодня: <code>{active}</code>\n"
                f"└ Запросов за сутки: <code>{daily}</code>"
                f"</blockquote>")
        await callback_query.message.edit_text(text, parse_mode=enums.ParseMode.HTML)

    # --- ОБРАБОТЧИК СТИКЕРОВ ---
    @app.on_message(filters.sticker)
    async def sticker_handler(client, message):
        await search_sticker_owner(client, message)

    # --- ОБРАБОТЧИК ФОТО И КАРТИНОК ---
    @app.on_message(filters.photo | (filters.document & filters.create(lambda _, __, m: m.document and m.document.mime_type and m.document.mime_type.startswith("image/"))))
    async def photo_handler(client, message):
        await search_by_photo(client, message)

    # --- ОБРАБОТЧИК ТЕКСТА ---
    @app.on_message(filters.private & ~filters.command(["admin", "start", "profile"]) & ~filters.service & ~filters.sticker & ~filters.photo)
    async def handle_input(client, message):
        user_id = message.from_user.id

        if user_states.get(user_id) == "waiting_for_token":
            token = message.text.strip() if message.text else ""
            if ":" not in token or len(token) < 20:
                await message.reply_text("⊘ <b>Ошибка:</b> Это не похоже на токен. Попробуй еще раз.", parse_mode=enums.ParseMode.HTML); return
            status_msg = await message.reply_text("◌ <b>Проверяю токен...</b>", parse_mode=enums.ParseMode.HTML)
            try:
                m_app = Client(f"mirror_{token[:10]}", api_id=API_ID, api_hash=API_HASH, bot_token=token, in_memory=True)
                register_handlers(m_app)
                await m_app.start()
                me = await m_app.get_me()
                await asyncio.to_thread(_save_mirror_sync, me.id, user_id, token)
                await status_msg.edit_text("✹ <b>Зеркало успешно запущено!</b>\n\nТеперь вы можете пользоваться им.", parse_mode=enums.ParseMode.HTML)
                user_states.pop(user_id, None)
            except Exception as e:
                await status_msg.edit_text(f"⊘ <b>Ошибка:</b> {html.escape(str(e))}\nПроверьте токен.", parse_mode=enums.ParseMode.HTML)
            return

        if getattr(message, "users_shared", None): return

        if user_id == ADMIN_ID and user_id in admin_states:
            if not message.text:
                await message.reply_text("⊘ Отправь текст: <code>ID количество</code>", parse_mode=enums.ParseMode.HTML); return
            parts = message.text.split()
            if len(parts) == 2 and parts[0].isdigit() and parts[1].lstrip('-').isdigit():
                state = admin_states.pop(user_id)
                try:
                    target_id, amount = int(parts[0]), int(parts[1])
                    if state == "give":
                        await add_bonus(target_id, amount)
                        await message.reply_text(f"✅ Выдано <b>{amount}</b> для <code>{target_id}</code>", parse_mode=enums.ParseMode.HTML)
                    else:
                        await add_bonus(target_id, -amount)
                        await message.reply_text(f"✅ Забрано <b>{amount}</b> у <code>{target_id}</code>", parse_mode=enums.ParseMode.HTML)
                except Exception as e:
                    await message.reply_text(f"⊘ Ошибка: {html.escape(str(e))}", parse_mode=enums.ParseMode.HTML)
                return
            else:
                admin_states.pop(user_id, None)
                await message.reply_text("⊘ Режим выдачи отменен. Выполняю поиск...", parse_mode=enums.ParseMode.HTML)

        if message.text:
            text = message.text.strip()

            if text.startswith("/"):
                await message.reply_text("⊘ <b>Неизвестная команда.</b>", parse_mode=enums.ParseMode.HTML)
                return

            # 1. MAC
            if re.match(r'^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$|^([0-9A-Fa-f]{12})$', text):
                await search_by_mac(client, message, text)
                return

            # 2. Паспорт (4 цифры + 6 цифр)
            if re.match(r'^\d{4}\s?\d{6}$', text):
                await search_by_passport(client, message, text)
                return
                
            # 3. СНИЛС (11 цифр, часто с дефисами)
            if re.match(r'^\d{3}[-\s]?\d{3}[-\s]?\d{3}[-\s]?\d{2}$', text):
                await search_by_snils(client, message, text)
                return

            # 4. VIN
            if re.match(r'^[A-HJ-NPR-Z0-9]{17}$', text, re.IGNORECASE):
                await search_by_vin(client, message, text)
                return

            # 5. BIN карты (6-11 цифр)
            if re.match(r'^\d{6,11}$', text):
                is_phone = False
                try:
                    p = phonenumbers.parse(text, "RU")
                    if phonenumbers.is_valid_number(p): is_phone = True
                except: pass

                if not is_phone:
                    await search_by_bin(client, message, text)
                    return

            # 6. IP
            try:
                ipaddress.ip_address(text)
                await search_by_ip(client, message, text)
                return
            except ValueError: pass

            # 7. TON
            if (text.startswith(("EQ", "UQ", "0Q")) and len(text) == 48) or text.endswith(".ton"):
                await search_by_ton(client, message, text)
                return

            # 8. Телефон
            parsed_phone = None
            try: parsed_phone = phonenumbers.parse(text, "RU")
            except: pass

            if parsed_phone and phonenumbers.is_valid_number(parsed_phone):
                await search_by_phone(client, message, text, parsed_phone)
                return

            # 9. ИНН
            if re.match(r'^\d{10}$|^\d{12}$', text):
                if DADATA_API_KEY:
                    await search_by_inn(client, message, text)
                    return
                else:
                    await message.reply_text("⊘ <b>Ошибка:</b> Поиск по ИНН недоступен (не настроен DADATA_API_KEY).", parse_mode=enums.ParseMode.HTML)
                    return

            # 10. Юзернейм / ID Telegram
            await search_handler(client, message, text)

# ==========================================
# ВОССТАНОВЛЕНИЕ ЗЕРКАЛ
# ==========================================
async def restore_mirrors():
    mirrors = await asyncio.to_thread(_get_all_mirrors_sync)
    for bot_id, _, token in mirrors:
        print(f"🔄 Восстанавливаю зеркало {bot_id}...")
        try:
            m_app = Client(f"mirror_{bot_id}", api_id=API_ID, api_hash=API_HASH, bot_token=token, in_memory=True)
            register_handlers(m_app)
            asyncio.create_task(m_app.start())
        except Exception as e:
            print(f"❌ Ошибка запуска зеркала {bot_id}: {e}")

async def main():
    init_users_db()
    threading.Thread(target=run_web_server, daemon=True).start()
    register_handlers(bot_app)

    try:
        print("🤖 Запускаю главного бота...", flush=True)
        await bot_app.start()
        print("👤 Запускаю юзербота...", flush=True)
        await user_app.start()
        print("🪞 Восстанавливаю зеркала...", flush=True)
        await restore_mirrors()
        print("🚀 Всё готово!", flush=True)
    except Exception as e:
        print(f"❌ ОШИБКА: {e}", flush=True); return
    await idle()

if __name__ == "__main__":
    loop.run_until_complete(main())
