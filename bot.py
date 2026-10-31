# pip install pyrofork tgcrypto requests phonenumbers psycopg2-binary
import os
import re
import html
import json
import asyncio
import threading
import urllib.parse
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
from pyrogram.raw.types import (
    UpdateNewMessage, MessageService,
    MessageActionRequestedPeerSentMe
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
API_URL = os.environ.get("API_URL", "https://dox-i7rk.onrender.com") # Ссылка на самого себя
FUNSTAT_TOKEN = os.environ.get("FUNSTAT_TOKEN", "")

ADMIN_ID = 5021557806
DAILY_LIMIT = 3
REF_REWARD = 1
DATABASE_URL = os.environ.get("DATABASE_URL", "")
OSINT_BOT_USERNAME = "@evenfa_bot"
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
        if commit: conn.commit()
    except Exception:
        try: conn.rollback()
        except Exception as e: print(f"⚠️ rollback: {e}")
        raise
    finally:
        if cur: cur.close()
        pool.putconn(conn)

# ==========================================
# ВЕБ-СЕРВЕР ДЛЯ RENDER (API + HTML ОТЧЕТ)
# ==========================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_path = urllib.parse.urlparse(self.path)
        path = parsed_path.path.strip('/')

        # 1. АПИ ДЛЯ БОТА (поиск телефона)
        if path == "get_phone":
            query = urllib.parse.parse_qs(parsed_path.query)
            user_id = query.get("id", [""])[0]
            
            # ТУТ ТЫ МОЖЕШЬ СДЕЛАТЬ ЗАПРОС К СВОЕЙ БАЗЕ, ЕСЛИ ОНА ЕСТЬ.
            # Пока для примера отдаем заглушку:
            response_data = {"phones": ["+79313636046"]} 
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps(response_data).encode('utf-8'))
            return

        # 2. КРАСИВЫЙ HTML ОТЧЕТ (при переходе по ссылке dox-i7rk.onrender.com/ID)
        if path and path.isdigit():
            user_id = path
            html_content = f"""
            <!DOCTYPE html>
            <html lang="ru">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Полный отчет по ID {user_id}</title>
                <style>
                    :root {{ --bg: #0e1621; --card: #17212b; --accent: #64b5f6; --text: #fff; --muted: #7b8e9f; --border: #2b3a4a; }}
                    body {{ background: var(--bg); color: var(--text); font-family: 'Segoe UI', sans-serif; margin: 0; padding: 20px; display: flex; justify-content: center; }}
                    .container {{ background: var(--card); border-radius: 12px; padding: 20px; width: 100%; max-width: 500px; box-shadow: 0 4px 12px rgba(0,0,0,0.3); }}
                    h1 {{ color: var(--accent); font-size: 20px; border-bottom: 1px solid var(--border); padding-bottom: 10px; margin-top: 0; }}
                    .row {{ display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid var(--border); }}
                    .row:last-child {{ border-bottom: none; }}
                    .label {{ color: var(--muted); }}
                    .val {{ font-weight: bold; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>⌬ Полный отчет</h1>
                    <div class="row"><span class="label">ID аккаунта</span><span class="val" style="font-family:monospace;">{user_id}</span></div>
                    <div class="row"><span class="label">Статус</span><span class="val" style="color:#4caf50;">Данные загружены</span></div>
                    <!-- Сюда ты можешь добавить вывод данных, если подключишь БД -->
                </div>
            </body>
            </html>
            """
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(html_content.encode('utf-8'))
            return

        # 3. СТАНДАРТНЫЙ ОТВЕТ (если открыть просто сайт)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    print(f"🌐 Веб-сервер запущен на порту {port}", flush=True)
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
                bonus_requests INTEGER DEFAULT 0, last_request_date DATE, referrer_id BIGINT DEFAULT NULL
            )''')
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS bonus_requests INTEGER DEFAULT 0")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referrer_id BIGINT DEFAULT NULL")
            cur.execute('''CREATE TABLE IF NOT EXISTS mirrors (
                bot_id BIGINT PRIMARY KEY, owner_id BIGINT, bot_token TEXT
            )''')
        print("✅ PostgreSQL готова", flush=True)
    except Exception as e:
        print(f"⚠️ init DB: {e}", flush=True)

def _get_user_sync(user_id):
    try:
        with db_cursor() as cur:
            cur.execute("SELECT username, first_seen, total_requests, daily_requests, bonus_requests, last_request_date FROM users WHERE user_id = %s", (int(user_id),))
            row = cur.fetchone()
        if not row: return None
        return {"username": row[0], "first_seen": row[1].isoformat() if row[1] else None, "total_requests": row[2] or 0, "daily_requests": row[3] or 0, "bonus_requests": row[4] or 0, "last_request_date": row[5].isoformat() if row[5] else None}
    except: return None

def _register_sync(user_id, username, referrer_id=None):
    today = today_local()
    try:
        with db_cursor(commit=True) as cur:
            cur.execute("SELECT 1 FROM users WHERE user_id = %s", (int(user_id),))
            if cur.fetchone(): return False
            cur.execute("INSERT INTO users (user_id, username, first_seen, total_requests, daily_requests, bonus_requests, last_request_date, referrer_id) VALUES (%s, %s, %s, 0, 0, 0, %s, %s)", (int(user_id), username or "", today, today, referrer_id))
            return True
    except: return False

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
        return True, DAILY_LIMIT
    daily = u["daily_requests"] if u["last_request_date"] == today else 0
    limit = max(0, DAILY_LIMIT + u["bonus_requests"])
    if daily >= limit: return False, 0
    return True, limit - daily

def _deduct_limit_sync(user_id):
    if user_id == ADMIN_ID: return
    today = today_local()
    try:
        with db_cursor(commit=True) as cur:
            cur.execute("INSERT INTO users (user_id, first_seen, total_requests, daily_requests, bonus_requests, last_request_date) VALUES (%s, %s, 1, 1, 0, %s) ON CONFLICT (user_id) DO UPDATE SET total_requests = users.total_requests + 1, daily_requests = CASE WHEN users.last_request_date = EXCLUDED.last_request_date THEN users.daily_requests + 1 ELSE 1 END, last_request_date = EXCLUDED.last_request_date", (int(user_id), today, today))
    except: pass

def _add_bonus_sync(user_id, amount):
    today = today_local()
    try:
        with db_cursor(commit=True) as cur:
            cur.execute("INSERT INTO users (user_id, first_seen, daily_requests, bonus_requests, last_request_date) VALUES (%s, %s, 0, %s, %s) ON CONFLICT (user_id) DO UPDATE SET bonus_requests = users.bonus_requests + EXCLUDED.bonus_requests", (int(user_id), today, int(amount), today))
    except: pass

def _save_mirror_sync(bot_id, owner_id, token):
    try:
        with db_cursor(commit=True) as cur:
            cur.execute("INSERT INTO mirrors (bot_id, owner_id, bot_token) VALUES (%s, %s, %s) ON CONFLICT (bot_id) DO UPDATE SET bot_token = EXCLUDED.bot_token", (bot_id, owner_id, token))
    except: pass

def _get_all_mirrors_sync():
    try:
        with db_cursor() as cur:
            cur.execute("SELECT bot_id, owner_id, bot_token FROM mirrors")
            return cur.fetchall()
    except: return []

def _get_admin_stats_sync():
    try:
        with db_cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM users"); users = cur.fetchone()[0]
            cur.execute("SELECT COALESCE(SUM(total_requests), 0) FROM users"); reqs = cur.fetchone()[0]
        return users, reqs
    except: return 0, 0

async def check_limit_status(user_id, username=""): return await asyncio.to_thread(_check_limit_sync, user_id, username)
async def deduct_limit(user_id): await asyncio.to_thread(_deduct_limit_sync, user_id)
async def add_bonus(user_id, amount): await asyncio.to_thread(_add_bonus_sync, user_id, amount)
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
        return f"{parts[2]}.{parts[1]}.{parts[0]}" if len(parts) == 3 else date_str
    except: return date_str

MONTHS_RU = {1: "январь", 2: "февраль", 3: "март", 4: "апрель", 5: "май", 6: "июнь", 7: "июль", 8: "август", 9: "сентябрь", 10: "октябрь", 11: "ноябрь", 12: "декабрь"}

def get_time_passed(year, month):
    now = datetime.now(TZ); reg_date = datetime(year, month, 1, tzinfo=TZ)
    years = now.year - reg_date.year; months = now.month - reg_date.month
    if months < 0: years -= 1; months += 12
    y_str = f"{years} год" if years % 10 == 1 and years % 100 != 11 else f"{years} года" if 2 <= years % 10 <= 4 and not (12 <= years % 100 <= 14) else f"{years} лет"
    if years == 0: y_str = ""
    m_str = f"{months} мес" if months > 0 else ""
    return f"({y_str} {m_str})".strip()

LOCAL_DB = {100000000: "2013-08", 300000000: "2014-07", 500000000: "2015-04", 1000000000: "2016-04", 2000000000: "2017-07", 3000000000: "2018-06", 4000000000: "2019-04", 5000000000: "2020-01", 6000000000: "2021-02", 7000000000: "2022-06", 8000000000: "2024-06"}
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

def _get_phones_sync(user_id):
    try:
        # Теперь бот стучится к самому себе через веб-сервер
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
        cmap = {"RU":"Россия","KZ":"Казахстан","UA":"Украина","BY":"Беларусь"}
        details["country"] = cmap.get(rc, rc)
    except: pass
    return details

def _get_funstat_sync(user_id):
    if not FUNSTAT_TOKEN: return None
    try:
        r = requests.get(f"https://telelog.org/api/v1/users/{user_id}/stats_min", headers={"Authorization": f"Bearer {FUNSTAT_TOKEN}"}, timeout=10)
        if r.status_code == 200:
            data = r.json(); lines = []
            for k, label in [("total_msg_count","Сообщений"),("total_groups","Чатов")]:
                if data.get(k) is not None: lines.append(f"{label}: {data[k]}")
            if lines: return f"<blockquote>{chr(10).join(html.escape(str(l)) for l in lines)}</blockquote>"
    except: pass
async def get_funstat_activity(user_id): return await asyncio.to_thread(_get_funstat_sync, user_id)

# ==========================================
# ОЧЕРЕДЬ @evenfa_bot
# ==========================================
osint_queue = asyncio.Queue(); osint_lock = asyncio.Lock()
async def drain_queue():
    while not osint_queue.empty(): osint_queue.get_nowait()
async def wait_msg(timeout=1.0):
    try: return await asyncio.wait_for(osint_queue.get(), timeout=timeout)
    except asyncio.TimeoutError: return None

def get_btn(message, keywords):
    if not message or not message.reply_markup: return None
    for row in message.reply_markup.inline_keyboard:
        for btn in row:
            if any(k in btn.text.lower() for k in keywords): return btn
    return None

async def parse_given_gifts(message):
    if not message: return ""
    raw_text = message.text or message.caption or ""
    if not raw_text or "evenfa_bot?start=" in raw_text: return ""
    out = []
    for un in re.findall(r'@([a-zA-Z0-9_]{4,32})', raw_text):
        try: out.append(f"<code>{(await user_app.get_users(un)).id}</code>")
        except: pass
    return ", ".join(list(dict.fromkeys(out)))

async def get_interests_from_bot(target, premium_until=None):
    async with osint_lock:
        await drain_queue()
        try:
            await user_app.send_message(OSINT_BOT_USERNAME, str(target))
            collected = {}; profile_msg = None; idle = 0
            for _ in range(30):
                msg = await wait_msg(1.0)
                if msg:
                    collected[msg.id] = msg
                    if msg.reply_markup: profile_msg = msg
                    idle = 0
                elif collected:
                    idle += 1
                    if idle >= 5: break

            history_text_raw = "".join([(m.text or m.caption or "") + "\n" for m in collected.values()])
            chat_info_text = ""; history_text = ""; interests_text = ""; sent_gifts_text = ""

            if history_text_raw:
                matches = re.findall(r'(\d{4}-\d{2}-\d{2})\s*(?:➜|→|->|—|-)\s*([^\n]+)', history_text_raw)
                lines = [f"{format_date_short(m[0])} → {html.escape(m[1].strip())}" for m in matches]
                if lines: history_text = f"◈ <b>История:</b>\n<blockquote>{chr(10).join(lines[:5])}</blockquote>"

            if profile_msg:
                gift_btn = get_btn(profile_msg, ["подар", "🎁"])
                if gift_btn:
                    try: await user_app.request_callback_answer(profile_msg.chat.id, profile_msg.id, gift_btn.callback_data)
                    except: pass
                    menu_msg = None
                    for _ in range(30):
                        m = await wait_msg(1.0)
                        if m and m.reply_markup: menu_msg = m; break
                    if menu_msg:
                        given_btn = get_btn(menu_msg, ["подарен", "отправ"])
                        if given_btn:
                            try: await user_app.request_callback_answer(menu_msg.chat.id, menu_msg.id, given_btn.callback_data)
                            except: pass
                            for _ in range(10):
                                m = await wait_msg(1.5)
                                if m and (parsed := await parse_given_gifts(m)):
                                    sent_gifts_text = parsed; break

            return chat_info_text, history_text, interests_text, sent_gifts_text
        except Exception as e: print(f"⚠️ @evenfa_bot: {e}")
        return "", "", "", ""

# ==========================================
# КЛИЕНТЫ
# ==========================================
bot_app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
user_app = Client("my_userbot", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING, in_memory=True)

@user_app.on_message(filters.chat(OSINT_BOT_USERNAME) & filters.incoming)
async def osint_handler(client, message): await osint_queue.put(message)

def split_message(text, limit=4000):
    if len(text) <= limit: return [text]
    parts = []; rem = text
    while rem:
        if len(rem) <= limit: parts.append(rem); break
        cut = rem.rfind('\n', 0, limit)
        if cut < limit // 3: cut = limit
        parts.append(rem[:cut]); rem = rem[cut:].lstrip()
    return parts

async def process_user(client, message, user_id, status=None, original_input=None):
    if not status: status = await message.reply_text("✧ <b>Собираю данные...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        user_obj = await user_app.get_users(user_id)
        username_str = f"@{user_obj.username}" if user_obj.username else "Скрыт"
        dc_id = user_obj.dc_id or None
        phone_str = await get_phones_from_api(user_id)
        
        gifts_lines = []
        try:
            peer = await user_app.resolve_peer(user_id)
            res = await user_app.invoke(GetSavedStarGifts(peer=peer, offset="", limit=100))
            for g in getattr(res, 'gifts', []):
                if not getattr(g, 'name_hidden', False) and getattr(g, 'from_id', None):
                    sid = getattr(g.from_id, 'user_id', None)
                    if sid: gifts_lines.append(f"<code>{sid}</code>")
        except: pass

        chat_info, history_text, interests_text, sent_gifts_text = await get_interests_from_bot(original_input or user_id)

        card = f"⌬ <b>Аккаунт Telegram</b>\n├ ID: <code>{user_id}</code>\n├ Username: {html.escape(username_str)}\n"
        if dc_id: card += f"├ DC: {dc_id}\n"
        
        if phone_str:
            card += f"\n✆ <b>Телефон:</b> <code>{html.escape(phone_str)}</code>\n"
            d = get_phone_details(phone_str)
            pl = []
            if d['operator']: pl.append(f"Оператор: {html.escape(d['operator'])}")
            if d['country']: pl.append(f"Страна: {html.escape(str(d['country']))}")
            for i, line in enumerate(pl): card += f"{'└ ' if i == len(pl)-1 else '├ '}{line}\n"
        else:
            card += f"\n✆ <b>Телефон:</b> Не найден\n"

        if chat_info: card += f"\n{chat_info}\n"
        if gifts_lines: card += f"\n✾ <b>Подарки:</b> <blockquote>{', '.join(gifts_lines)}</blockquote>"
        if sent_gifts_text: card += f"\n\n⎈ <b>Отправлено:</b> <blockquote>{sent_gifts_text}</blockquote>"
        if history_text: card += f"\n\n{history_text}"
        if interests_text: card += f"\n\n{interests_text}"

        report_url = f"{API_URL}/{user_id}"
        buttons = [[InlineKeyboardButton("🔗 Открыть полный отчет", url=report_url)]]

        await status.edit_text(card[:4000], parse_mode=enums.ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))
        return True
    except Exception as e:
        await status.edit_text(f"⊘ <b>Ошибка:</b> {html.escape(str(e))[:300]}", parse_mode=enums.ParseMode.HTML)
        return False

# ==========================================
# ХЕНДЛЕРЫ КОМАНД
# ==========================================
async def render_profile_text(uid, username_raw):
    u = await ensure_user_registered(uid, username_raw or "")
    if not u: return None
    username = f"@{username_raw}" if username_raw else "Скрыт"
    try: days = (datetime.now(TZ) - datetime.strptime(u["first_seen"], "%Y-%m-%d").replace(tzinfo=TZ)).days
    except: days = 0
    today = today_local()
    daily = 0 if u["total_requests"] == 0 else (u["daily_requests"] if u["last_request_date"] == today else 0)
    limit = "∞/∞" if uid == ADMIN_ID else f"{daily}/{max(0, DAILY_LIMIT + u['bonus_requests'])}"
    return f"⊛ <b>Ваш профиль</b>\n\n<blockquote>• ID: <code>{uid}</code>\n• Юзернейм: {html.escape(username)}\n• В системе с: {format_date_short(u['first_seen'])} ({format_days(days)})\n• Запросов сегодня: <b>{limit}</b></blockquote>"

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
                await client.send_message(referrer_id, f"✵ По вашей ссылке зарегистрировался новый пользователь!\nВам начислен +{REF_REWARD} бонусный запрос.", parse_mode=enums.ParseMode.HTML)
            except: pass
        else:
            await ensure_user_registered(user_id, username)
            
        if payload and payload.isdigit():
            await search_handler(client, message, payload); return

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏣ Мой профиль", callback_data="my_profile")],
            [InlineKeyboardButton("✜ Партнёры", callback_data="referral")],
            [InlineKeyboardButton("◈ Создать зеркало", callback_data="create_mirror")]
        ])
        await message.reply_text("❂ Добро пожаловать в поисковик — бесплатный поиск по данным\n\n⌖ Отправь ID или @username для поиска.", reply_markup=kb, parse_mode=enums.ParseMode.HTML)

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

    @app.on_callback_query(filters.regex(r"^referral$"))
    async def show_referral(client, callback_query):
        await callback_query.answer()
        me = await client.get_me()
        uid = callback_query.from_user.id
        ref_count = await get_referral_count(uid)
        ref_link = f"https://t.me/{me.username}?start=ref_{uid}"
        
        text = f"⬡ <b>Партнёрская программа</b>\n\n⊹ Приглашайте друзей и получайте +{REF_REWARD} запрос за каждого.\n⊹ Бонус начисляется мгновенно.\n\n⌖ <b>Ваша ссылка:</b>\n<code>{ref_link}</code>\n\n⊛ Вы пригласили: <b>{ref_count}</b>"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏎ Назад", callback_data="back_to_menu")]])
        await callback_query.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=kb)

    @app.on_callback_query(filters.regex(r"^back_to_menu$"))
    async def back_to_menu(client, callback_query):
        await callback_query.answer()
        user_states.pop(callback_query.from_user.id, None)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏣ Мой профиль", callback_data="my_profile")],
            [InlineKeyboardButton("✜ Партнёры", callback_data="referral")],
            [InlineKeyboardButton("◈ Создать зеркало", callback_data="create_mirror")]
        ])
        await callback_query.message.edit_text("❂ Добро пожаловать в поисковик — бесплатный поиск по данным\n\n⌖ Отправь ID или @username для поиска.", reply_markup=kb, parse_mode=enums.ParseMode.HTML)

    @app.on_callback_query(filters.regex(r"^create_mirror$"))
    async def create_mirror_callback(client, callback_query):
        await callback_query.answer()
        user_states[callback_query.from_user.id] = "waiting_for_token"
        text = "⟡ <b>Добавление зеркала</b>\n\n1. Откройте @BotFather и нажмите «Open Mini App»\n2. Создайте нового бота через Mini App\n3. Скопируйте полученный токен\n4. Отправьте его сюда\n\nБыстро и эффективно!"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏎ Назад", callback_data="back_to_menu")]])
        await callback_query.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=kb)

    @app.on_inline_query()
    async def inline_query_handler(client, inline_query):
        q = (inline_query.query or "").strip()
        if not q: await inline_query.answer([], cache_time=1, is_personal=True, switch_pm_text="⚡ Открыть", switch_pm_parameter="start"); return
        if not q.isdigit(): await inline_query.answer([], cache_time=5, is_personal=True, switch_pm_text="⊘ Только ID", switch_pm_parameter="start"); return

        me = await client.get_me()
        preview = f"⌬ <b>Аккаунт {q}</b>\n\n<i>Нажми кнопку для полного отчёта</i>"
        result = InlineQueryResultArticle(id=q, title=f"Аккаунт {q}", input_message_content=InputTextMessageContent(preview, parse_mode=enums.ParseMode.HTML), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✦ Отчёт", url=f"https://t.me/{me.username}?start={q}")]]))
        await inline_query.answer([result], cache_time=10, is_personal=True)

    @app.on_message(filters.command("admin") & filters.user(ADMIN_ID))
    async def admin_panel(client, message):
        users, reqs = await get_admin_stats()
        text = f"⨳ <b>Админ-панель</b>\n\n<blockquote>├ Юзеров: <code>{users}</code>\n└ Всего запросов: <code>{reqs}</code></blockquote>"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⊕ Выдать", callback_data="admin_give"), InlineKeyboardButton("⊖ Забрать", callback_data="admin_take")], [InlineKeyboardButton("↻ Обновить", callback_data="admin_refresh")]])
        await message.reply_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

    @app.on_callback_query(filters.regex(r"^admin_give$") & filters.user(ADMIN_ID))
    async def admin_give_start(client, callback_query):
        await callback_query.answer()
        admin_states[ADMIN_ID] = "give"
        await callback_query.message.edit_text("⊕ <b>Выдать</b>\n\nОтправь: <code>ID количество</code>", parse_mode=enums.ParseMode.HTML)

    @app.on_callback_query(filters.regex(r"^admin_take$") & filters.user(ADMIN_ID))
    async def admin_take_start(client, callback_query):
        await callback_query.answer()
        admin_states[ADMIN_ID] = "take"
        await callback_query.message.edit_text("⊖ <b>Забрать</b>\n\nОтправь: <code>ID количество</code>", parse_mode=enums.ParseMode.HTML)

    @app.on_callback_query(filters.regex(r"^admin_refresh$") & filters.user(ADMIN_ID))
    async def admin_refresh(client, callback_query):
        await callback_query.answer("Обновлено")
        users, reqs = await get_admin_stats()
        text = f"⨳ <b>Админ-панель</b>\n\n<blockquote>├ Юзеров: <code>{users}</code>\n└ Всего запросов: <code>{reqs}</code></blockquote>"
        await callback_query.message.edit_text(text, parse_mode=enums.ParseMode.HTML)

    @app.on_message(filters.private & ~filters.command(["admin", "start", "profile"]) & ~filters.service)
    async def handle_input(client, message):
        user_id = message.from_user.id
        
        if user_states.get(user_id) == "waiting_for_token":
            token = message.text.strip() if message.text else ""
            if ":" not in token or len(token) < 20:
                await message.reply_text("⊘ <b>Ошибка:</b> Не похоже на токен.", parse_mode=enums.ParseMode.HTML); return
            status = await message.reply_text("◌ <b>Проверяю...</b>", parse_mode=enums.ParseMode.HTML)
            try:
                m_app = Client(f"mirror_{token[:10]}", api_id=API_ID, api_hash=API_HASH, bot_token=token, in_memory=True)
                register_handlers(m_app)
                await m_app.start()
                me = await m_app.get_me()
                await asyncio.to_thread(_save_mirror_sync, me.id, user_id, token)
                await status.edit_text("✹ <b>Зеркало запущено!</b>", parse_mode=enums.ParseMode.HTML)
                user_states.pop(user_id, None)
            except Exception as e:
                await status.edit_text(f"⊘ <b>Ошибка:</b> {html.escape(str(e))}", parse_mode=enums.ParseMode.HTML)
            return

        if getattr(message, "users_shared", None): return

        if user_id == ADMIN_ID and user_id in admin_states:
            if not message.text: await message.reply_text("⊘ <code>ID количество</code>"); return
            parts = message.text.split()
            if len(parts) == 2 and parts[0].isdigit() and parts[1].lstrip('-').isdigit():
                state = admin_states.pop(user_id)
                try:
                    target_id, amount = int(parts[0]), int(parts[1])
                    if state == "give":
                        await add_bonus(target_id, amount)
                        await message.reply_text(f"✅ Выдано <b>{amount}</b>", parse_mode=enums.ParseMode.HTML)
                    else:
                        await add_bonus(target_id, -amount)
                        await message.reply_text(f"✅ Забрано <b>{amount}</b>", parse_mode=enums.ParseMode.HTML)
                except Exception as e: await message.reply_text(f"⊘ Ошибка: {html.escape(str(e))}")
                return
            else:
                admin_states.pop(user_id, None)
                await message.reply_text("⊘ Режим отменен. Ищу...")

        if message.text: await search_handler(client, message, message.text.strip())

async def search_handler(client, message, query=None):
    user_id = message.from_user.id
    targets = [q.strip() for q in re.split(r'[,\s\n]+', query or message.text) if q.strip()]
    allowed, _ = await check_limit_status(user_id, message.from_user.username or "")
    if not allowed: await message.reply_text("⊘ Лимит исчерпан."); return

    valid = []
    for target in targets:
        if target.startswith('+'): continue
        uid = int(target) if target.isdigit() else None
        if not uid:
            try: uid = (await user_app.get_users(target.lstrip('@'))).id
            except: pass
        if uid: valid.append((uid, target))

    if not valid: await message.reply_text("⊘ Не найдено."); return

    for i, (uid, orig) in enumerate(valid, 1):
        status = await message.reply_text(f"⌛ {i}/{len(valid)}: {html.escape(orig)}", parse_mode=enums.ParseMode.HTML)
        if await process_user(client, message, uid, status=status, original_input=orig): await deduct_limit(user_id)
        await asyncio.sleep(2)

# ==========================================
# ЗАПУСК
# ==========================================
async def restore_mirrors():
    mirrors = await asyncio.to_thread(_get_all_mirrors_sync)
    for bot_id, _, token in mirrors:
        try:
            m_app = Client(f"mirror_{bot_id}", api_id=API_ID, api_hash=API_HASH, bot_token=token, in_memory=True)
            register_handlers(m_app)
            asyncio.create_task(m_app.start())
        except: pass

async def main():
    init_users_db()
    threading.Thread(target=run_web_server, daemon=True).start()
    register_handlers(bot_app)
    try:
        await bot_app.start()
        await user_app.start()
        await restore_mirrors()
        print("🚀 Всё готово!", flush=True)
    except Exception as e:
        print(f"❌ ОШИБКА: {e}", flush=True); return
    await idle()

if __name__ == "__main__":
    loop.run_until_complete(main())
