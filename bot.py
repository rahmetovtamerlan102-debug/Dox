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

osint_event = asyncio.Event()
last_osint_msg = None

# ==========================================
# 4. СЛОВАРИ ДЛЯ ИНТЕРЕСОВ
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
    "Продажа физов": ["селл","sell","слив","прод","физ","физы","физов","физиков", "пробив","база","дамп","паспорт","инн","снилс","дропы", "карт","скам","scam","селлер","селлеры","продаж"],
    "OSINT/Darknet": ["osint","пробив","детектив","расслед","черн","даркнет","дарк","darknet"],
    "Политика": ["политик","протест","митинг","оппозиция","антивоен","антирос","война","путин","кремл"],
    "Общение": ["общение", "чат", "меню", "раздача", "состав", "привет", "как дела", "братан", "жека", "димас", "долг"]
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
    return [r[0] for r in res[:max_results]]

# ==========================================
# 5. КЭШ
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
    except: return None

def set_cache(user_id, username, reg_date, phones, gifts, nfts, dc_id, interests):
    try:
        conn = sqlite3.connect(CACHE_DB)
        c = conn.cursor()
        c.execute('''INSERT OR REPLACE INTO cache (user_id, username, reg_date, phones, gifts, nfts, dc_id, interests) 
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', (user_id, username, reg_date, phones, gifts, nfts, dc_id, interests))
        conn.commit()
        conn.close()
    except: pass

# ==========================================
# 6. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
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
# 7. КЛИЕНТЫ
# ==========================================
bot_app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
user_app = Client("my_userbot", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING, in_memory=True)

@user_app.on_message(filters.chat(OSINT_BOT_USERNAME) & filters.incoming)
async def osint_handler(client, message):
    global last_osint_msg, osint_event
    last_osint_msg = message
    osint_event.set()

@user_app.on_edited_message(filters.chat(OSINT_BOT_USERNAME))
async def osint_edit_handler(client, message):
    global last_osint_msg, osint_event
    last_osint_msg = message
    osint_event.set()

# ==========================================
# 8. ФУНКЦИЯ ПОЛУЧЕНИЯ ДАННЫХ ИЗ @evenfa_bot
# ==========================================
async def get_interests_from_bot(target):
    global last_osint_msg, osint_event
    
    if not OSINT_BOT_USERNAME: return None
    
    last_osint_msg = None
    osint_event.clear()
    
    try:
        print(f"📤 Отправляю '{target}' боту {OSINT_BOT_USERNAME}...", flush=True)
        await user_app.send_message(OSINT_BOT_USERNAME, str(target))
        
        history_text = None
        profile_msg = None
        
        # 1. Ждем историю и меню
        for _ in range(20):
            try:
                await asyncio.wait_for(osint_event.wait(), timeout=1.0)
                osint_event.clear()
                
                if last_osint_msg:
                    if last_osint_msg.text and not history_text:
                        history_text = last_osint_msg.text
                    if last_osint_msg.reply_markup and not profile_msg:
                        profile_msg = last_osint_msg
                        
                    if history_text and profile_msg:
                        break
            except asyncio.TimeoutError:
                pass
        
        result_lines = []

        # 2. ПАРСИМ ИСТОРИЮ
        if history_text:
            print(f"📄 История получена!", flush=True)
            names_matches = re.findall(r'(\d{4}-\d{2}-\d{2})\s*(?:➜|→|->|—|-)\s*(.+)', history_text)
            
            history_lines = []
            for date_str, name_part in names_matches:
                username_match = re.search(r'@([A-Za-z0-9_]{5,32})', name_part)
                user_str = f"@{username_match.group(1)}" if username_match else ""
                
                name_str = re.sub(r'@[A-Za-z0-9_]{5,32}', '', name_part).strip()
                
                if name_str.lower() in ["не дало", "нет данных", "не найден"]:
                    continue
                    
                line_parts = [date_str]
                if name_str: line_parts.append(name_str)
                if user_str: line_parts.append(user_str)
                
                if line_parts:
                    history_lines.append(" ".join(line_parts))
            
            if history_lines:
                history_text_block = "\n".join(history_lines[:5])
                result_lines.append(f"◈ <b>История профиля:</b>\n<blockquote>{history_text_block}</blockquote>")

        # 3. ПАРСИМ ЧАСТОТУ СЛОВ
        if profile_msg:
            target_btn = None
            for row in profile_msg.reply_markup.inline_keyboard:
                for btn in row:
                    clean_text = re.sub(r'[^\w\s]', '', btn.text.lower())
                    if "част" in clean_text or "слов" in clean_text or "🗣" in btn.text:
                        target_btn = btn
                        break
                if target_btn: break
                
            if not target_btn:
                try: target_btn = profile_msg.reply_markup.inline_keyboard[1][1] 
                except IndexError: target_btn = profile_msg.reply_markup.inline_keyboard[0][0]

            if target_btn:
                print(f"👆 Нажимаем на: '{target_btn.text}'", flush=True)
                last_osint_msg = None
                osint_event.clear()
                
                try:
                    await user_app.request_callback_answer(
                        chat_id=profile_msg.chat.id,
                        message_id=profile_msg.id,
                        callback_data=target_btn.callback_data
                    )
                except Exception as e:
                    print(f"⚠️ Ошибка нажатия: {e}", flush=True)

                for _ in range(20):
                    try:
                        await asyncio.wait_for(osint_event.wait(), timeout=1.0)
                        osint_event.clear()
                        if last_osint_msg and last_osint_msg.text:
                            if re.search(r'[├|└]\s*\*?\*?\d+\s*[-–]\s*\d+', last_osint_msg.text):
                                words_text = last_osint_msg.text
                                print(f"📄 Частота слов получена!", flush=True)
                                
                                words = _parse_words_response(words_text)
                                cats = _detect_interests_from_words(words, max_results=3)
                                
                                if cats:
                                    result_lines.append(f"\n◉ <b>Интересы:</b>\n<blockquote>{', '.join(cats)}</blockquote>")
                                break
                    except asyncio.TimeoutError:
                        pass

        return "\n".join(result_lines) if result_lines else None
            
    except Exception as e:
        print(f"⚠️ Ошибка @evenfa_bot: {e}", flush=True)
    return None

# ==========================================
# 9. ОСТАЛЬНАЯ ЛОГИКА
# ==========================================
request_user_obj = RequestPeerTypeUser(button_id=1, is_bot=False, max=1)
reply_keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton("Выбрать пользователя", request_user=request_user_obj)]], resize_keyboard=True)

@bot_app.on_message(filters.command("start"))
async def start_command(client, message):
    await message.reply_text("⌬ Отправь мне ID, @username или контакт, и я покажу информацию.", reply_markup=reply_keyboard, parse_mode=enums.ParseMode.HTML)

@bot_app.on_message(filters.contact & filters.private)
async def handle_contact(client, message):
    user_id = message.contact.user_id
    if not user_id: return
    await process_user(client, message, user_id, original_input=user_id)

@bot_app.on_message(filters.private)
async def handle_user_shared(client, message):
    shared = getattr(message, "chats_shared", None) or getattr(message, "users_shared", None)
    if shared and getattr(shared, "users", None):
        for item in shared.users:
            user_id = getattr(item, "user_id", None)
            if user_id: await process_user(client, message, user_id, original_input=user_id)
        return
    if message.text: await search_handler(client, message)

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
    try: user_obj = await user_app.get_users(user_id)
    except: pass

    cached_data = get_cache(user_id)
    if cached_data:
        cached_user, reg_date, cached_phones, cached_gifts, cached_nfts, dc_id, cached_interests = cached_data
        
        if not cached_interests:
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
        if cached_interests: card += f"\n\n{cached_interests}"
            
        await status.edit_text(card, parse_mode=enums.ParseMode.HTML)
        return

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
                if getattr(gift, "slug", None): nft_lines.append(f"{gift.title} #{gift.num}")
                elif not getattr(g, 'name_hidden', False) and g.from_id:
                    sid = getattr(g.from_id, 'user_id', None)
                    if sid: gifts_lines.append(f"<code>{sid}</code>")
    except Exception as e: print(f"❌ Ошибка подарков: {e}", flush=True)

    gifts_text = ", ".join(gifts_lines) if gifts_lines else "Нет"
    nfts_text = ", ".join(nft_lines) if nft_lines else "Нет"
    
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
    if interests_text: card += f"\n\n{interests_text}"

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
