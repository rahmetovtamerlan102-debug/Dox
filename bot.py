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
    InlineKeyboardMarkup, InlineKeyboardButton, 
    InlineQueryResultArticle, InputTextMessageContent
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
DAILY_LIMIT = 0  # По умолчанию 0, запросы только за бонус
BONUS_AMOUNT = 3 # Сколько дает ежедневный бонус
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
            raise RuntimeError("DATABASE_URL не задан. Подключи Postgres add-on на Render.")
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
        print("✅ PostgreSQL готова (users + mirrors + bonus)", flush=True)
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

# Обновленная таблица диапазонов
LOCAL_DB = {
    1: "2013-08",
    100000000: "2014-09",
    200000000: "2016-07",
    300000000: "2017-03",
    400000000: "2017-12",
    500000000: "2018-05",
    600000000: "2018-10",
    700000000: "2019-02",
    800000000: "2019-06",
    900000000: "2019-10",
    1000000000: "2020-01",
    1400000000: "2020-12",
    1800000000: "2021-07",
    2000000000: "2021-09",
    5000000000: "2022-02",
    5500000000: "2022-08",
    6000000000: "2023-01",
    6500000000: "2023-07",
    7000000000: "2023-12",
    7500000000: "2024-08",
    8000000000: "2025-02",
    8500000000: "2025-09",
    9000000000: "2026-04",
}

def estimate_registration(user_id):
    ids = sorted(LOCAL_DB.keys())
    
    if user_id <= ids[0]: 
        p = LOCAL_DB[ids[0]].split("-")
        return int(p[0]), int(p[1])
    if user_id >= ids[-1]: 
        p = LOCAL_DB[ids[-1]].split("-")
        return int(p[0]), int(p[1])
    
    for i in range(len(ids) - 1):
        a = ids[i]
        b = ids[i+1]
        
        if a <= user_id <= b:
            if (b - a) > 500_000_000:
                p = LOCAL_DB[a].split("-")
                return int(p[0]), int(p[1])
            
            d1 = datetime.strptime(LOCAL_DB[a], "%Y-%m")
            d2 = datetime.strptime(LOCAL_DB[b], "%Y-%m")
            
            months_diff = max((d2.year - d1.year) * 12 + (d2.month - d1.month), 1)
            id_progress = (user_id - a) / (b - a)
            add_months = int(id_progress * months_diff)
            
            year = d1.year + (d1.month + add_months - 1) // 12
            month = (d1.month + add_months - 1) % 12 + 1
            return year, month
    
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
# КЛИЕНТЫ
# ==========================================
bot_app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
user_app = Client("my_userbot", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING, in_memory=True)

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

        # Оставляем только Verified и Premium
        badges = []
        if user_obj and user_obj.is_premium: badges.append("⭐ Premium")
        if user_obj and user_obj.is_verified: badges.append("✅ Verified")
        badge_text = f" {', '.join(badges)}" if badges else ""

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
        else:
            card += f"\n✆ <b>Телефон:</b> Не найден\n"

        if fs: card += f"\n▲ <b>Активность:</b>\n{fs}\n\n"
        if premium_until: card += f"\n⭐ <b>Premium до:</b> {html.escape(premium_until)}\n"

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
# ХЕНДЛЕРЫ КОМАНД
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

    valid = []
    for target in targets:
        if target.startswith('+') or (target.isdigit() and len(target) > 10): continue
        uid = None
        if target.isdigit(): uid = int(target)
        else:
            try: 
                clean_target = target.lstrip('@')
                uid = (await user_app.get_users(clean_target)).id
            except Exception as e: print(f"⚠️ resolve {target}: {e}")
        if uid: valid.append((uid, target))

    if not valid:
        await message.reply_text("⊘ <b>Ошибка:</b> Юзернейм не найден.", parse_mode=enums.ParseMode.HTML); return

    if len(valid) > 1:
        status = await message.reply_text(f"⧉ <b>В очереди: {len(valid)}</b>", parse_mode=enums.ParseMode.HTML)
        for i, (uid, orig) in enumerate(valid, 1):
            allowed, _ = await check_limit_status(user_id, message.from_user.username or "")
            if not allowed and user_id != ADMIN_ID:
                await message.reply_text("⊘ Лимит исчерпан во время обработки.", parse_mode=enums.ParseMode.HTML); break
            try: await status.edit_text(f"⌛ {i}/{len(valid)}: {html.escape(orig)}", parse_mode=enums.ParseMode.HTML)
            except: pass
            tmp = await message.reply_text(f"📡 {html.escape(orig)}...", parse_mode=enums.ParseMode.HTML)
            if await process_user(client, message, uid, status=tmp, original_input=orig): await deduct_limit(user_id)
            await asyncio.sleep(3)
        try: await status.edit_text("✅ <b>Все запросы обработаны!</b>", parse_mode=enums.ParseMode.HTML)
        except: pass
        return

    uid, orig = valid[0]
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
            try:
                await client.send_message(
                    referrer_id, 
                    f"✤ По вашей ссылке зарегистрировался новый пользователь!\nВам начислен +{REF_REWARD} бонусный запрос.",
                    parse_mode=enums.ParseMode.HTML
                )
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
            "❂ Добро пожаловать в поисковик — бесплатный поиск по данным\n\n"
            "⌖ Отправь ID или @username для поиска.",
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
            await callback_query.message.edit_text(
                "⊘ Вы уже получали бонус сегодня.\nВозвращайтесь завтра!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏎ Назад", callback_data="back_to_menu")]]),
                parse_mode=enums.ParseMode.HTML
            )
            return
            
        await claim_daily_bonus(user_id)
        await callback_query.message.edit_text(
            f"⁂ <b>Бонус получен!</b>\n\nВам начислено +{BONUS_AMOUNT} запроса.\nВозвращайтесь завтра!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏎ Назад", callback_data="back_to_menu")]]),
            parse_mode=enums.ParseMode.HTML
        )

    @app.on_callback_query(filters.regex(r"^referral$"))
    async def show_referral(client, callback_query):
        await callback_query.answer()
        me = await client.get_me()
        uid = callback_query.from_user.id
        ref_count = await get_referral_count(uid)
        ref_link = f"https://t.me/{me.username}?start=ref_{uid}"
        
        text = (
            f"⬡ <b>Партнёрская программа</b>\n\n"
            f"⊹ Приглашайте друзей и получайте +{REF_REWARD} запрос за каждого.\n"
            f"⊹ Бонус начисляется мгновенно.\n\n" 
            f"⌖ <b>Ваша ссылка:</b>\n"
            f"<code>{ref_link}</code>\n\n"
            f"⊛ Вы пригласили: <b>{ref_count}</b>"
        )
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
        await callback_query.message.edit_text(
            "❂ Добро пожаловать в поисковик — бесплатный поиск по данным\n\n⌖ Отправь ID или @username для поиска.",
            reply_markup=kb, parse_mode=enums.ParseMode.HTML
        )

    @app.on_callback_query(filters.regex(r"^create_mirror$"))
    async def create_mirror_callback(client, callback_query):
        await callback_query.answer()
        user_states[callback_query.from_user.id] = "waiting_for_token"
        text = (
            "⟡ <b>Добавление зеркала</b>\n\n"
            "1. Откройте @BotFather и нажмите «Open Mini App»\n"
            "2. Создайте нового бота через Mini App\n"
            "3. Скопируйте полученный токен\n"
            "4. Отправьте его сюда\n\n"
            "Быстро и эффективно!"
        )
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
            dc_id = u.dc_id or "?"
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
        text = (
            f"⨳ <b>Админ-панель</b>\n\n"
            f"<blockquote>"
            f"├ Пользователей: <code>{users}</code>\n"
            f"├ Всего запросов: <code>{reqs}</code>\n"
            f"├ Активных сегодня: <code>{active}</code>\n"
            f"└ Запросов за сутки: <code>{daily}</code>"
            f"</blockquote>"
        )
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
        text = (
            f"⨳ <b>Админ-панель</b>\n\n"
            f"<blockquote>"
            f"├ Пользователей: <code>{users}</code>\n"
            f"├ Всего запросов: <code>{reqs}</code>\n"
            f"├ Активных сегодня: <code>{active}</code>\n"
            f"└ Запросов за сутки: <code>{daily}</code>"
            f"</blockquote>"
        )
        await callback_query.message.edit_text(text, parse_mode=enums.ParseMode.HTML)

    @app.on_message(filters.private & ~filters.command(["admin", "start", "profile"]) & ~filters.service)
    async def handle_input(client, message):
        user_id = message.from_user.id
        
        if user_states.get(user_id) == "waiting_for_token":
            token = message.text.strip() if message.text else ""
            if ":" not in token or len(token) < 20:
                await message.reply_text("⊘ <b>Ошибка:</b> Это не похоже на токен. Попробуй еще раз.", parse_mode=enums.ParseMode.HTML); return
            status = await message.reply_text("◌ <b>Проверяю токен...</b>", parse_mode=enums.ParseMode.HTML)
            try:
                m_app = Client(f"mirror_{token[:10]}", api_id=API_ID, api_hash=API_HASH, bot_token=token, in_memory=True)
                register_handlers(m_app)
                await m_app.start()
                me = await m_app.get_me()
                await asyncio.to_thread(_save_mirror_sync, me.id, user_id, token)
                await status.edit_text("✹ <b>Зеркало успешно запущено!</b>\n\nТеперь вы можете пользоваться им.", parse_mode=enums.ParseMode.HTML)
                user_states.pop(user_id, None)
            except Exception as e:
                await status.edit_text(f"⊘ <b>Ошибка:</b> {html.escape(str(e))}\nПроверьте токен.", parse_mode=enums.ParseMode.HTML)
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
            await search_handler(client, message, message.text.strip())

# ==========================================
# ЗАПУСК И ВОССТАНОВЛЕНИЕ ЗЕРКАЛ
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
