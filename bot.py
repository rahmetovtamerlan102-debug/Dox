# pip install pyrofork tgcrypto requests phonenumbers
import os, requests, sqlite3, asyncio, threading, re, phonenumbers
from phonenumbers import carrier
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime
from pyrogram import Client, filters, enums, idle
from pyrogram.raw.functions.payments import GetSavedStarGifts
from pyrogram.raw.functions.users import GetFullUser
from pyrogram.types import ReplyKeyboardMarkup, KeyboardButton, RequestPeerTypeUser
from pyrogram.errors import FloodWait

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"Bot is running!")
    def do_HEAD(self):
        self.send_response(200); self.end_headers()

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()

API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")
API_URL = os.environ.get("API_URL", "")
FUNSTAT_TOKEN = os.environ.get("FUNSTAT_TOKEN", "")

CACHE_DB = "cache.db"
OSINT_BOT_USERNAME = "@evenfa_bot" 

LOCAL_DB = {
    100000000: "2013-08", 200000000: "2014-01", 300000000: "2014-07", 400000000: "2014-12", 
    500000000: "2015-04", 700000000: "2015-10", 1000000000: "2016-04", 1500000000: "2017-01", 
    2000000000: "2017-07", 3000000000: "2018-06", 4000000000: "2019-04", 5000000000: "2020-01",
    6000000000: "2021-02", 7000000000: "2022-06", 7500000000: "2023-05", 8000000000: "2024-06", 
    8500000000: "2025-01",
}
MONTHS_RU = {1: "январь", 2: "февраль", 3: "март", 4: "апрель", 5: "май", 6: "июнь", 7: "июль", 8: "август", 9: "сентябрь", 10: "октябрь", 11: "ноябрь", 12: "декабрь"}
MONTHS_SHORT = {"01": "янв", "02": "фев", "03": "мар", "04": "апр", "05": "мая", "06": "июн", "07": "июл", "08": "авг", "09": "сен", "10": "окт", "11": "ноя", "12": "дек"}

def format_date_short(date_str):
    if not date_str: return None
    try:
        parts = date_str.split("-") 
        if len(parts) == 3: return f"{parts[2]} {MONTHS_SHORT.get(parts[1], parts[1])} {parts[0][2:]}"
        return date_str
    except: return date_str

osint_event = asyncio.Event()
last_osint_msg = None
osint_lock = asyncio.Lock()

_TO_KIRILLIC = {'a':'а','b':'в','c':'с','d':'д','e':'е','f':'ф','g':'г','h':'н','i':'и','j':'й','k':'к','l':'л','m':'м','n':'п','o':'о','p':'п','q':'к','r':'р','s':'с','t':'т','u':'и','v':'в','w':'в','x':'х','y':'у','z':'з','α':'а','β':'в','γ':'у','δ':'д','ε':'е','ζ':'з','η':'н','θ':'т','ι':'и','κ':'к','λ':'л','μ':'м','ν':'в','ξ':'х','ο':'о','π':'п','ρ':'р','σ':'с','ς':'с','τ':'т','υ':'и','φ':'ф','χ':'х','ψ':'п','ω':'о','ց':'г','ᴜ':'и','ᴠ':'в','ᴧ':'л','ᴨ':'п','ᴦ':'р','ᴋ':'к','ᴍ':'м','ᴏ':'о','ᴘ':'п','ᴛ':'т','ᴅ':'д','ᴢ':'з','ᴊ':'й','ᴡ':'в','ʏ':'у','ɪ':'и','ᴎ':'н','ᴓ':'о','Ⅼ':'л','Ⅽ':'с','Ⅾ':'д','Ⅿ':'м','ⅰ':'и','ⅼ':'л','ⅽ':'с','ⅾ':'д','ⅿ':'м','℮':'е','ƒ':'ф','ł':'л','қ':'к','ѕ':'с','і':'и','ї':'и','ў':'у','ј':'й'}
_KIR_TRANS = str.maketrans(_TO_KIRILLIC)
def _full_normalize(s): return str(s or "").translate(_KIR_TRANS).lower()

WORDS_KEYWORD_MAP = {
    "Гейминг": ["килл","килаур","kill","чит","cheat","реп","репут","майн","csgo","cs2", "dota","дота","кс2","ксго","fortnite","фортнайт","pubg","пабг","gta","гта", "warcraft","wow","лол","lol","геншин","genshin","роблокс","roblox", "майнкрафт","minecraft","стим","steam","танки","аим","aim","спавн","spawn", "лут","loot","клан","clan","сервер","server","ранг","rank"],
    "Маты/Сленг": ["ебан","ебал","еблан","ебат","ебуч","пизд","хуй","хуя","бляд","блят", "мраз","долбо","нахуй","нахуя","залуп","гондон","мудак","дебил","туп", "кринж","кринге","база","жиза","рофл","кек","азаз","ахах","хех","нуб", "читер","читор","краб","мамк","мат", "дроч", "дик", "dick", "вacь"],
    "Криптовалюты": ["битк","bitcoin","биткойн","eth","ethereum","эфир","токен","token", "крипт","crypto","usdt","tether","ton","sol","solana","bnb","binance", "бинанс","defi","nft","web3","блокчейн","blockchain","кошелек","wallet", "airdrop","эирдроп","майнер","miner"],
    "AI/Нейросети": ["нейро","neuro","нейросет","нейронк","gpt","чатгпт","chatgpt", "midjourney","midjorney","stable","diffusion","candy","openai", "опенаи","claude","llm","промпт","prompt"],
    "Операторы/SIM": ["мегафон","megafon","мтс","mts","билайн","beeline","теле2","tele2", "оператор","sim","симк","мобил","тариф","связь","мобильн"],
    "Подписки/Контент": ["подпис","подпиш","лайк","репост","канал","shorts","reels", "тикток","tiktok","инста","insta","youtube","ютуб","блогер", "стрим","стример","донат","буст","видео","video","клип","clip"],
    "Программирование": ["python","питон","код","code","программ","разраб","developer", "javascript","backend","frontend","golang","java","php","sql", "docker","github","гитхаб","баг","bug","фикс","fix","деплой","deploy"],
    "Инвестиции/Трейдинг": ["трейд","trade","форекс","forex","инвест","invest","брокер", "broker","акци","биржа","дивиденд","портфель","portfolio", "профит","profit","убыток","лосс","loss","скальп","scalp"],
    "Музыка": ["music","музык","битмейк","beatmaker","диджей","dj","рэп","rap","хип-хоп", "hip-hop","трек","track","бит","beat","soundcloud","спотифай","spotify"],
    "Спорт": ["спорт","sport","фитнес","fitness","gym","качалк","бодибилд","кроссфит", "бокс","мма","футбол","хоккей","баскетбол","спортзал","тренер","coach"],
    "Авто": ["авто","auto","машин","bmw","mers","mercedes","audi","toyota","тачк", "тюнинг","tuning","двигат","движок","колес","руль","wheel","car"],
    "Кино/Аниме": ["аниме","anime","манга","manga","сериал","series","фильм","movie", "кино","netflix","нетфликс","дорам","dorama","сезон","season"],
    "Путешествия": ["путеш","travel","вокруг света","trip","tourist","tour","отдых", "виза","пляж","beach","отпуск","vacation","аэропорт","airport"],
    "Обучение": ["учус","учит","студент","student","school","школа","универ","обучен", "education","курс","course","экзамен","exam","сессия","диплом"],
    "Мода/Красота": ["мода","fashion","красот","beauty","макияж","стиль","style", "маникюр","визаж","парикмахер","шмот","одежд"],
    "Бизнес": ["бизнес","business","стартап","startup","предприн","founder","ceo", "маркетинг","marketing","продаж","sales","smm","смм","клиент","client"],
    "Продажа физов": ["селл","sell","слив","прод","физ","физы","физов","физиков", "пробив","база","дамп","паспорт","инн","снилс","дропы", "карт","скам","scam","селлер","селлеры","продаж", "продαм", "продам"],
    "OSINT/Darknet": ["osint","пробив","детектив","расслед","черн","даркнет","дарк","darknet"],
    "Политика": ["политик","протест","митинг","оппозиция","антивоен","антирос","война","путин","кремл"],
    "Общение": ["общение", "чат", "меню", "раздача", "состав", "привет", "как дела", "братан", "жека", "димас", "долг", "пацаны", "крутои", "спам"]
}

def _parse_words_response(text):
    if not text: return []
    words = []
    for line in str(text).split("\n"):
        m = re.match(r'[├|└]\s*\*?\*?(\d+)\s*[-–]\s*(\d+)\*?\*?\s*[\s]*([^\n]+)', line.strip())
        if not m: continue
        try: mn = int(m.group(1)); mx = int(m.group(2))
        except ValueError: continue
        for w in m.group(3).strip().rstrip('').strip().split(","):
            wc = re.sub(r'[^\w\-]+', '', w, flags=re.UNICODE).strip()
            if wc and len(wc) >= 3: words.append({"word": wc, "avg": (mn + mx) // 2})
    words.sort(key=lambda x: x["avg"], reverse=True)
    return words

def _detect_interests_from_words(words, max_results=3):
    if not words: return []
    all_text = " ".join([_full_normalize(w.get("word", "")) for w in words])
    res = []
    for cat, kws in WORDS_KEYWORD_MAP.items():
        hits = sum(1 for kw in kws if _full_normalize(kw) in all_text)
        if hits > 0: res.append((cat, hits))
    res.sort(key=lambda x: x[1], reverse=True)
    return [r[0] for r in res[:max_results]] if res else ["Общение"]

def init_cache():
    try:
        conn = sqlite3.connect(CACHE_DB)
        conn.execute('''CREATE TABLE IF NOT EXISTS cache (user_id INTEGER PRIMARY KEY, username TEXT, reg_date TEXT, phones TEXT, gifts TEXT, nfts TEXT, dc_id INTEGER, interests TEXT)''')
        conn.commit(); conn.close()
        print("✅ Кэш инициализирован", flush=True)
    except Exception as e: print(f"⚠️ Ошибка кэша: {e}", flush=True)

def get_cache(user_id):
    try:
        conn = sqlite3.connect(CACHE_DB); c = conn.cursor()
        c.execute("SELECT username, reg_date, phones, gifts, nfts, dc_id, interests FROM cache WHERE user_id = ?", (user_id,))
        row = c.fetchone(); conn.close(); return row
    except: return None

def set_cache(user_id, username, reg_date, phones, gifts, nfts, dc_id, interests):
    try:
        conn = sqlite3.connect(CACHE_DB); c = conn.cursor()
        c.execute('''INSERT OR REPLACE INTO cache (user_id, username, reg_date, phones, gifts, nfts, dc_id, interests) VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', (user_id, username, reg_date, phones, gifts, nfts, dc_id, interests))
        conn.commit(); conn.close()
    except: pass

def get_time_passed(year, month):
    now = datetime.now(); reg = datetime(year, month, 1)
    years = now.year - reg.year; months = now.month - reg.month
    if months < 0: years -= 1; months += 12
    y_str = f"{years} год" if years % 10 == 1 and years % 100 != 11 else f"{years} года" if 2 <= years % 10 <= 4 and not (12 <= years % 100 <= 14) else f"{years} лет"
    if years == 0: y_str = ""
    m_str = f"{months} мес" if months > 0 else ""
    return f"({y_str} {m_str})".strip()

def estimate_registration(user_id):
    ids = sorted(LOCAL_DB.keys())
    if user_id <= ids[0]: p = LOCAL_DB[ids[0]].split("-"); return int(p[0]), int(p[1])
    if user_id >= ids[-1]: p = LOCAL_DB[ids[-1]].split("-"); return int(p[0]), int(p[1])
    for i in range(len(ids) - 1):
        a, b = ids[i], ids[i+1]
        if a <= user_id <= b:
            d1 = datetime.strptime(LOCAL_DB[a], "%Y-%m"); d2 = datetime.strptime(LOCAL_DB[b], "%Y-%m")
            md = max((d2.year-d1.year)*12 + (d2.month-d1.month), 1)
            add = int((user_id - a) / (b - a) * md)
            return d1.year + (d1.month + add - 1)//12, (d1.month + add - 1) % 12 + 1
    return None, None

def get_phones_from_api(user_id):
    if not API_URL: return None
    try:
        r = requests.get(f"{API_URL}/get_phone", params={"id": user_id}, timeout=10)
        if r.status_code == 200:
            phones = r.json().get("phones", [])
            return ", ".join(phones) if phones else None
    except: pass
    return None

def get_phone_details(phone_str):
    out = {"operator": None, "country": None}
    if not phone_str: return out
    try:
        parsed = phonenumbers.parse(phone_str, "RU")
        out["operator"] = carrier.name_for_number(parsed, "ru")
        rc = phonenumbers.region_code_for_number(parsed)
        cmap = {"RU":"Россия","KZ":"Казахстан","UA":"Украина","BY":"Беларусь","UZ":"Узбекистан","KG":"Кыргызстан","TJ":"Таджикистан","AM":"Армения","GE":"Грузия","AZ":"Азербайджан","MD":"Молдова","LV":"Латвия","LT":"Литва","EE":"Эстония","US":"США","GB":"Великобритания","DE":"Германия","FR":"Франция","TR":"Турция","AE":"ОАЭ"}
        out["country"] = cmap.get(rc, rc)
    except: pass
    return out

def get_funstat_activity(user_id):
    if not FUNSTAT_TOKEN: return None
    try:
        r = requests.get(f"https://telelog.org/api/v1/users/{user_id}/stats_min", headers={"Authorization": f"Bearer {FUNSTAT_TOKEN}"}, timeout=10)
        if r.status_code == 200:
            d = r.json(); lines = []
            for k, label in [("total_msg_count","Всего сообщений"),("total_groups","Всего чатов"),("reactions_count","Получено реакций")]:
                if d.get(k) is not None: lines.append(f"{label}: {d[k]}")
            for k, label in [("first_msg_date","Первое появление"),("last_msg_date","Последнее появление")]:
                if d.get(k): lines.append(f"{label}:\n{d[k].replace('T',' ').replace('Z','')}")
            if lines: return f"<blockquote>{chr(10).join(lines)}</blockquote>"
    except: pass
    return None

async def parse_given_gifts(message):
    if not message: return ""
    raw_text = ""
    if getattr(message, "text", None):
        raw_text = str(message.text)
        if hasattr(message.text, "html") and message.text.html: raw_text = message.text.html
    elif getattr(message, "caption", None):
        raw_text = str(message.caption)
        if hasattr(message.caption, "html") and message.caption.html: raw_text = message.caption.html
    
    if not raw_text or "evenfa_bot?start=" in raw_text: return ""

    out = []
    if "<a href=" in raw_text:
        for match in re.finditer(r'<a href="(?:tg://user\?id=(\d+)|https://t\.me/([a-zA-Z0-9_]+))">([^<]+)</a>', raw_text):
            uid = match.group(1); uname = match.group(2); name = match.group(3).strip()
            if uid: out.append(f"{name} — <code>{uid}</code>")
            elif uname:
                try:
                    user = await user_app.get_users(uname)
                    out.append(f"{name} — <code>{user.id}</code>")
                except: out.append(f"{name} — @{uname}")
    
    if not out:
        clean_text = re.sub(r'<[^>]+>', '', raw_text)
        for un in re.findall(r'(?<![\w/])@([a-zA-Z0-9_]{4,32})', clean_text):
            try:
                user = await user_app.get_users(un)
                out.append(f"<code>{user.id}</code>")
            except: pass
        for uid in re.findall(r'\b(\d{8,12})\b', clean_text):
            out.append(f"<code>{uid}</code>")

    seen = set(); uniq = []
    for x in out:
        if x not in seen: seen.add(x); uniq.append(x)
    return "\n".join(uniq)

bot_app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
user_app = Client("my_userbot", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING, in_memory=True)

@user_app.on_message(filters.chat(OSINT_BOT_USERNAME) & filters.incoming)
@user_app.on_edited_message(filters.chat(OSINT_BOT_USERNAME))
async def osint_handler(client, message):
    global last_osint_msg, osint_event
    last_osint_msg = message; osint_event.set()

async def get_interests_from_bot(target, premium_until=None):
    global last_osint_msg, osint_event
    if not OSINT_BOT_USERNAME: return None
    
    async with osint_lock:
        last_osint_msg = None; osint_event.clear()
        try:
            print(f"📤 Отправляю '{target}'...", flush=True)
            await user_app.send_message(OSINT_BOT_USERNAME, str(target))
            history_text = None; profile_msg = None
            
            for _ in range(20):
                try:
                    await asyncio.wait_for(osint_event.wait(), timeout=1.0); osint_event.clear()
                    if last_osint_msg:
                        if last_osint_msg.text and not history_text: history_text = last_osint_msg.text
                        if last_osint_msg.reply_markup and not profile_msg: profile_msg = last_osint_msg
                        if history_text and profile_msg: break
                except asyncio.TimeoutError: pass
            
            result_lines = []
            if history_text:
                print(f"📄 История получена!", flush=True)
                fav = re.search(r'(?:ᴧюбимый чαт|Любимый чат)\s*:\s*([^\n]+)', history_text)
                circ = re.search(r'(?:Kрγжки|Кружочки)\s*:\s*(\d+)', history_text)
                voice = re.search(r'(?:голοc|голос)\s*:\s*(\d+)', history_text)
                
                extra = []
                if fav and fav.group(1).strip() and not re.search(r'(?:Иcқαли|Искали|Поиск)', fav.group(1), re.IGNORECASE): extra.append(f"Любимый чат: {fav.group(1).strip()}")
                if circ: extra.append(f"Кружочки: {circ.group(1)}")
                if voice: extra.append(f"Голосовые: {voice.group(1)}")
                if premium_until: extra.append(f"Premium до: {premium_until}")
                if extra: result_lines.append(f"\n❖ <b>Информация из чатов:</b>\n<blockquote>{chr(10).join(extra)}</blockquote>")
                
                names = re.findall(r'(\d{4}-\d{2}-\d{2})\s*(?:➜|→|->|—|-)\s*(.+)', history_text)
                ub = re.search(r'(?:uѕerηαmeѕ|usernames|юзернеймы)\s*:?\s*(.*?)(?:\n\n|\Z)', history_text, re.DOTALL | re.IGNORECASE)
                usernames = list(dict.fromkeys(re.findall(r'@([^\s|]+)', ub.group(1)))) if ub else list(dict.fromkeys(re.findall(r'@([^\s|]+)', history_text)))
                
                h_lines = []
                for i in range(max(len(names), len(usernames))):
                    ds = format_date_short(names[i][0]) if i < len(names) else None
                    ns = names[i][1].strip() if i < len(names) else None
                    us = f"@{usernames[i]}" if i < len(usernames) else None
                    parts = [p for p in [ds, f"→ {ns}," if ns else None, us] if p]
                    if parts: h_lines.append(" ".join(parts).replace(" ,", ","))
                if h_lines: result_lines.append(f"\n◈ <b>История профиля:</b>\n<blockquote>{chr(10).join(h_lines[:5])}</blockquote>")

            if profile_msg:
                btn = None
                for row in profile_msg.reply_markup.inline_keyboard:
                    for b in row:
                        if "част" in _full_normalize(b.text) or "слов" in _full_normalize(b.text) or "🗣" in b.text: btn = b; break
                    if btn: break
                if not btn:
                    try: btn = profile_msg.reply_markup.inline_keyboard[1][1]
                    except: btn = None
                
                if btn:
                    last_osint_msg = None; osint_event.clear()
                    await user_app.request_callback_answer(profile_msg.chat.id, profile_msg.id, btn.callback_data)
                    for _ in range(30):
                        try:
                            await asyncio.wait_for(osint_event.wait(), timeout=1.0); osint_event.clear()
                            if last_osint_msg and last_osint_msg.text and re.search(r'[├|└]\s*\*?\*?\d+\s*[-–]\s*\d+', last_osint_msg.text):
                                cats = _detect_interests_from_words(_parse_words_response(last_osint_msg.text), 3)
                                if cats: result_lines.append(f"\n◉ <b>Интересы:</b>\n<blockquote>{chr(10).join(cats)}</blockquote>")
                                break
                        except asyncio.TimeoutError: pass
                
                g_btn = None
                for row in profile_msg.reply_markup.inline_keyboard:
                    for b in row:
                        if "подар" in _full_normalize(b.text) or "🎁" in b.text: g_btn = b; break
                    if g_btn: break
                
                if g_btn:
                    last_osint_msg = None; osint_event.clear()
                    await user_app.request_callback_answer(profile_msg.chat.id, profile_msg.id, g_btn.callback_data)
                    menu = None
                    for _ in range(15):
                        try:
                            await asyncio.wait_for(osint_event.wait(), timeout=1.0); osint_event.clear()
                            if last_osint_msg and last_osint_msg.reply_markup: menu = last_osint_msg; break
                        except asyncio.TimeoutError: pass
                    
                    if menu:
                        s_btn = None
                        for row in menu.reply_markup.inline_keyboard:
                            for b in row:
                                if "дарен" in _full_normalize(b.text) or "отправ" in _full_normalize(b.text) or "⇉" in b.text: s_btn = b; break
                            if s_btn: break
                        
                        if s_btn:
                            last_osint_msg = None; osint_event.clear()
                            await user_app.request_callback_answer(menu.chat.id, menu.id, s_btn.callback_data)
                            for _ in range(20):
                                try:
                                    await asyncio.wait_for(osint_event.wait(), timeout=1.5); osint_event.clear()
                                    if last_osint_msg:
                                        parsed = await parse_given_gifts(last_osint_msg)
                                        if parsed:
                                            result_lines.append(f"\n✿ <b>Отправленные подарки:</b>\n<blockquote>{parsed}</blockquote>")
                                            break
                                except asyncio.TimeoutError: pass
            return "\n".join(result_lines) if result_lines else None
        except Exception as e: print(f"⚠️ Ошибка: {e}", flush=True)
        return None

async def process_user(client, message, user_id, status=None, original_input=None):
    if not status: status = await message.reply_text("✧ <b>Собираю данные...</b>", parse_mode=enums.ParseMode.HTML)
    user_obj = None
    try: user_obj = await user_app.get_users(user_id)
    except: pass
    
    premium_until = None
    if user_obj and user_obj.is_premium:
        try:
            fi = await user_app.invoke(GetFullUser(id=await user_app.resolve_peer(user_id)))
            if fi.full_user.premium_expiration_date: premium_until = datetime.fromtimestamp(fi.full_user.premium_expiration_date).strftime("%d.%m.%Y")
        except: pass
    
    cached = get_cache(user_id)
    if cached:
        c_user, reg, c_ph, c_g, c_n, dc, c_int = cached
        if not c_int:
            c_int = await get_interests_from_bot(original_input or user_id, premium_until)
            set_cache(user_id, c_user, reg, c_ph, c_g, c_n, dc, c_int or "")
        
        h_lines = [f"ID: <code>{user_id}</code>"]
        if c_user and c_user != "Скрыт": h_lines.append(f"Username: {c_user}")
        if reg and reg != "Неизвестно": h_lines.append(f"Регистрация: {reg}")
        if dc and dc != "Неизвестно": h_lines.append(f"DC: {dc}")
        
        card = f"⌬ <b>Аккаунт Telegram</b> [КЭШ]\n" + "\n".join(f"{'└ ' if i == len(h_lines)-1 else '├ '}{l}" for i, l in enumerate(h_lines)) + "\n"
        if c_ph and c_ph != "Не найден":
            d = get_phone_details(c_ph)
            p_lines = [f"Оператор: {d['operator']}"] if d['operator'] else []
            if d['country']: p_lines.append(f"Страна: {d['country']}")
            card += f"\n✆ <b>Телефон:</b> <code>{c_ph}</code>\n" + "\n".join(f"{'└ ' if i == len(p_lines)-1 else '├ '}{l}" for i, l in enumerate(p_lines)) + "\n"
        
        fs = get_funstat_activity(user_id)
        if fs: card += f"\n▲ <b>Активность:</b>\n{fs}\n\n"
        if c_n and c_n != "Нет": card += f"\n✦ <b>NFT</b>\n<blockquote>{c_n}</blockquote>\n"
        if c_g and c_g != "Нет": card += f"\n✾ <b>Полученные подарки:</b>\n<blockquote>{c_g}</blockquote>"
        if c_int: card += f"\n\n{c_int}"
        
        await status.edit_text(card, parse_mode=enums.ParseMode.HTML); return

    username_str = f"@{user_obj.username}" if user_obj and user_obj.username else "Скрыт"
    dc_id = user_obj.dc_id if user_obj else None
    est_y, est_m = estimate_registration(user_id)
    reg_date = f"~ {MONTHS_RU.get(est_m, '')} {est_y} {get_time_passed(est_y, est_m)}" if est_y else None
    phone_str = get_phones_from_api(user_id)
    
    g_lines, n_lines = [], []
    try:
        peer = await user_app.resolve_peer(username_str if username_str != "Скрыт" else user_id)
        res = await user_app.invoke(GetSavedStarGifts(peer=peer, offset="", limit=100))
        if res.gifts:
            for g in res.gifts:
                gift = getattr(g, "gift", None)
                if getattr(gift, "slug", None): n_lines.append(f"{gift.title} #{gift.num}")
                elif not getattr(g, 'name_hidden', False) and g.from_id and getattr(g.from_id, 'user_id', None): g_lines.append(f"<code>{g.from_id.user_id}</code>")
    except: pass
    
    g_txt = ", ".join(list(dict.fromkeys(g_lines))) if g_lines else "Нет"
    n_txt = ", ".join(list(dict.fromkeys(n_lines))) if n_lines else "Нет"
    
    int_txt = await get_interests_from_bot(original_input or user_id, premium_until)
    set_cache(user_id, username_str, reg_date, phone_str or "", g_txt, n_txt, dc_id, int_txt or "")
    
    h_lines = [f"ID: <code>{user_id}</code>"]
    if username_str != "Скрыт": h_lines.append(f"Username: {username_str}")
    if reg_date: h_lines.append(f"Регистрация: {reg_date}")
    if dc_id: h_lines.append(f"DC: {dc_id}")
    
    card = f"⌬ <b>Аккаунт Telegram</b>\n" + "\n".join(f"{'└ ' if i == len(h_lines)-1 else '├ '}{l}" for i, l in enumerate(h_lines)) + "\n"
    if phone_str:
        d = get_phone_details(phone_str)
        p_lines = [f"Оператор: {d['operator']}"] if d['operator'] else []
        if d['country']: p_lines.append(f"Страна: {d['country']}")
        card += f"\n✆ <b>Телефон:</b> <code>{phone_str}</code>\n" + "\n".join(f"{'└ ' if i == len(p_lines)-1 else '├ '}{l}" for i, l in enumerate(p_lines)) + "\n"
    
    fs = get_funstat_activity(user_id)
    if fs: card += f"\n▲ <b>Активность:</b>\n{fs}\n\n"
    if n_txt != "Нет": card += f"✦ <b>NFT</b>\n<blockquote>{n_txt}</blockquote>\n"
    if g_txt != "Нет": card += f"\n✾ <b>Полученные подарки:</b>\n<blockquote>{g_txt}</blockquote>"
    if int_txt: card += f"\n\n{int_txt}"
    
    await status.edit_text(card, parse_mode=enums.ParseMode.HTML)

request_user_obj = RequestPeerTypeUser(button_id=1, is_bot=False, max=1)
reply_keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton("Выбрать пользователя", request_user=request_user_obj)]], resize_keyboard=True)

@bot_app.on_message(filters.command("start"))
async def start_command(client, message):
    await message.reply_text("⌬ Отправь мне ID, @username или контакт.", reply_markup=reply_keyboard, parse_mode=enums.ParseMode.HTML)

@bot_app.on_message(filters.contact & filters.private)
async def handle_contact(client, message):
    if message.contact.user_id: await process_user(client, message, message.contact.user_id, original_input=message.contact.user_id)

@bot_app.on_message(filters.private)
async def handle_user_shared(client, message):
    shared = getattr(message, "chats_shared", None) or getattr(message, "users_shared", None)
    if shared and getattr(shared, "users", None):
        for item in shared.users:
            uid = getattr(item, "user_id", None)
            if uid: await process_user(client, message, uid, original_input=uid)
        return
    if message.text: await search_handler(client, message)

async def search_handler(client, message):
    targets = [q.strip() for q in re.split(r'[,\s\n]+', message.text.strip()) if q.strip()]
    if len(targets) > 1:
        status = await message.reply_text(f"📥 <b>В очереди: {len(targets)} запросов.</b>", parse_mode=enums.ParseMode.HTML)
        for i, target in enumerate(targets, 1):
            try: await status.edit_text(f"⌛ <b>Обрабатываю {i}/{len(targets)}: {target}</b>", parse_mode=enums.ParseMode.HTML)
            except: pass
            uid = None
            if target.isdigit(): uid = int(target)
            else:
                try: uid = (await user_app.get_users(target if target.startswith('@') else f"@{target}")).id
                except: continue
            if uid:
                temp = await message.reply_text(f"📡 Собираю данные для {target}...", parse_mode=enums.ParseMode.HTML)
                try: await process_user(client, message, uid, status=temp, original_input=target)
                except FloodWait as e: await asyncio.sleep(e.value)
                except: pass
                await asyncio.sleep(3)
        try: await status.edit_text("✅ <b>Все запросы обработаны!</b>", parse_mode=enums.ParseMode.HTML)
        except: pass
        return

    target = targets[0]
    status = await message.reply_text("✧ <b>Обрабатываю запрос...</b>", parse_mode=enums.ParseMode.HTML)
    if target.isdigit(): await process_user(client, message, int(target), status, original_input=target)
    else:
        un = target if target.startswith('@') else f"@{target}"
        try: await process_user(client, message, (await user_app.get_users(un)).id, status, original_input=un)
        except: await status.edit_text("✖ <b>Ошибка:</b> Юзернейм не найден.", parse_mode=enums.ParseMode.HTML)

async def main():
    init_cache()
    threading.Thread(target=run_web_server, daemon=True).start()
    try:
        print("🤖 Запускаю...", flush=True)
        await bot_app.start(); await user_app.start()
        print("🚀 Всё готово!", flush=True)
    except Exception as e:
        print(f"❌ ОШИБКА: {e}", flush=True); return
    await idle()
    await bot_app.stop(); await user_app.stop()

if __name__ == "__main__":
    loop.run_until_complete(main())
