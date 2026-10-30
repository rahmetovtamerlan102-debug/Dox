import os, re, asyncio, threading, requests, phonenumbers
from phonenumbers import carrier
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime
from pyrogram import Client, filters, enums, idle
from pyrogram.raw.functions.payments import GetSavedStarGifts
from pyrogram.raw.functions.users import GetFullUser
from pyrogram.types import ReplyKeyboardMarkup, KeyboardButton, RequestPeerTypeUser
from pyrogram.errors import FloodWait

# ==========================================
# 1. ЦИКЛ + ВЕБ-СЕРВЕР
# ==========================================
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

# ==========================================
# 2. КОНФИГ
# ==========================================
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")
API_URL = os.environ.get("API_URL", "")
FUNSTAT_TOKEN = os.environ.get("FUNSTAT_TOKEN", "")
OSINT_BOT_USERNAME = "@evenfa_bot"

LOCAL_DB = {
    100000000:"2013-08",200000000:"2014-01",300000000:"2014-07",400000000:"2014-12",
    5000000000:"2015-04",7000000000:"2015-10",1000000000:"2016-04",1500000000:"2017-01",
    2000000000:"2017-07",3000000000:"2018-06",4000000000:"2019-04",5000000000:"2020-01",
    6000000000:"2021-02",7000000000:"2022-06",7500000000:"2023-05",8000000000:"2024-06",
    8500000000:"2025-01",
}
MONTHS_RU = {1:"январь",2:"февраль",3:"март",4:"апрель",5:"май",6:"июнь",7:"июль",8:"август",9:"сентябрь",10:"октябрь",11:"ноябрь",12:"декабрь"}
MONTHS_SHORT = {"01":"янв","02":"фев","03":"мар","04":"апр","05":"мая","06":"июн","07":"июл","08":"авг","09":"сен","10":"окт","11":"ноя","12":"дек"}
COUNTRY_MAP = {"RU":"Россия","KZ":"Казахстан","UA":"Украина","BY":"Беларусь","UZ":"Узбекистан","KG":"Кыргызстан","TJ":"Таджикистан","AM":"Армения","GE":"Грузия","AZ":"Азербайджан","MD":"Молдова","LV":"Латвия","LT":"Литва","EE":"Эстония","US":"США","GB":"Великобритания","DE":"Германия","FR":"Франция","TR":"Турция","AE":"ОАЭ"}

def format_date_short(d):
    try:
        p = d.split("-")
        return f"{p[2]} {MONTHS_SHORT.get(p[1], p[1])} {p[0][2:]}" if len(p) == 3 and len(p[0]) == 4 else d
    except: return d

osint_event = asyncio.Event()
last_osint_msg = None
osint_lock = asyncio.Lock()

# ==========================================
# 3. ИНТЕРЕСЫ
# ==========================================
_TO_KIRILLIC = {
    'a':'а','b':'в','c':'с','d':'д','e':'е','f':'ф','g':'г','h':'н','i':'и','j':'й','k':'к',
    'l':'л','m':'м','n':'п','o':'о','p':'п','q':'к','r':'р','s':'с','t':'т','u':'и','v':'в',
    'w':'в','x':'х','y':'у','z':'з','α':'а','β':'в','γ':'у','δ':'д','ε':'е','ζ':'з','η':'н',
    'θ':'т','ι':'и','κ':'к','λ':'л','μ':'м','ν':'в','ξ':'х','ο':'о','Π':'п','π':'п','ρ':'р',
    'σ':'с','ς':'с','τ':'т','υ':'и','φ':'ф','χ':'х','ψ':'п','ω':'о','ց':'г','ᴜ':'и','ᴠ':'в',
    'ᴧ':'l','ᴨ':'п','ᴦ':'р','ᴋ':'ᴋ','ᴍ':'м','ᴏ':'о','ᴘ':'п','ᴛ':'t','ᴅ':'d','ᴣ':'з','ᴊ':'й',
    'ᴡ':'в','ʏ':'у','ɪ':'и','ᴎ':'н','ᴓ':'о','Ⅼ':'л','Ⅽ':'с','Ⅾ':'d','Ⅿ':'м','ⅰ':'и','ⅼ':'l',
    'ⅽ':'с','ⅾ':'d','ⅿ':'m','℮':'е','ƒ':'ф','ł':'l','қ':'k','ѕ':'с','і':'и','ї':'и','ў':'у','ј':'j',
}
_KIR_TRANS = str.maketrans(_TO_KIRILLIC)
def _full_normalize(s): return str(s or "").translate(_KIR_TRANS).lower()

WORDS_KEYWORD_MAP = {
    "Гейминг": ["килл","килаур","kill","cheat","реп","репут","майн","csgo","cs2","dota","дота","кс2","ксго","fortnite","фортнайт","pubg","пабг","gta","гта","warcraft","wow","лол","lol","gen","genshin","roblox","rob","майнкрафт","minecraft","стим","steam","танки","спавн","spawn","лут","loot","ser","ran","rank"],
    "Маты/Сленг": ["ебан","ебал","еблан","ебат","ебуч","пизд","хуй","хуя","бляд","блят","мраз","долбо","зал","m","d","t","k","a","h","b","р","м","v"],
    "Криптовалюты": ["битк","bitc","биткойн","eth","ethereum","эфир","токен","token","крипт","crypto","usdt","tether","ton","sol","solana","bnb","binance","бинанс","defi","nft","web3","блокчейн","blockchain","кошелек","wallet","airdrop","эирдроп","майнер","miner"],
    "AI/Нейросети": ["нейро","neuro","нейросет","нейронк","gpt","chatgpt","midjourney","midjorney","stable","diffusion","candy","openai","опенаи","claude","llm","промпт","prompt"],
    "Операторы/SIM": ["мегафон","megafon","мтс","mts","билайн","beeline","теле2","tele2","оператор","sim","симк","мобил","тариф","связь","мобильн"],
    "Подписки/Контент": ["под","л","р","k","s","r","t","инста","in","блогер","стрим","стример","донат","буст","видео","video","клип","clip"],
    "Программирование": ["python","питон","код","code","программ","разраб","developer","javascript","backend","frontend","golang","java","php","sql","docker","github","гитхаб","баг","bug","фикс","fix","деплой","deploy"],
    "Инвестиции/Трейдинг": ["трейд","trade","форекс","forex","инвест","invest","брокер","broker","акси","биржа","дивиденд","портфель","portfolio","профит","profit","убыток","лосс","loss","скальп","scalp"],
    "Музыка": ["music","музык","битмейк","beatmaker","диджей","dj","р","х","т","s"],
    "Спорт": ["спорт","sport","фитнес","fitness","gym","качалк","бодибилд","кроссфит","бокс","мма","футбол","хок","баск","спортзал","тренер","coach"],
    "Авто": ["авто","auto","машин","bmw","mers","mercedes","audi","toyota","тачк","тюнинг","tuning","двигат","движок","колес","руль","wheel","car"],
    "Кино/Аниме": ["аниме","anime","манга","manga","сериал","series","фильм","movie","кино","netflix","нетфликс","дорам","dorama","сезон","season"],
    "Путешествия": ["путеш","travel","trip","tourist","tour","отдых","виза","пляж","beach","отпуск","vacation","аэропорт","airport"],
    "Обучение": ["учус","учит","студент","student","school","школа","универ","обучен","education","курс","course","экзамен","exam","сессия","диплом"],
    "Мода/Красота": ["мода","fashion","красот","beauty","макияж","стиль","style","маникюр","визаж","парикмахер","шмот","одежд"],
    "Бизнес": ["бизнес","business","стартап","startup","предприн","founder","ceo","маркетинг","marketing","продаж","sales","smm","смм","клиент","client"],
    "Продажа физов": ["селл","sell","слив","прод","физ","физы","физов","физиков","пробив","база","дамп","паспорт","инн","снилс","дропы","карт","скам","scam","селлер","селлеры"],
    "OSINT/Darknet": ["osint","пробив","детектив","расслед","черн","даркнет","дарк","darknet"],
    "Политика": ["пол","п","м","о","а","в","к","р","л"],
    "Общение": ["общение","чат","меню","раздача","состав","привет","как дела","братан","жека","димас","долг","пацаны","крутои","спам"]
}

def _parse_words_response(text):
    if not text: return []
    words = []
    for line in str(text).split("\n"):
        line = line.strip()
        # Формат: ├ 100-200 слово1, слово2, ...
        m = re.match(r'^[├└]\s*\*?\*?(\d+)\s*[-–]\s*(\d+)\s+(.+)$', line)
        if not m: continue
        try: avg = (int(m.group(1)) + int(m.group(2))) // 2
        except: continue
        words_part = m.group(3)
        for w in words_part.split(","):
            wc = re.sub(r'[^\w-]+', '', w, flags=re.UNICODE).strip()
            if wc and len(wc) >= 3:
                words.append({"word": wc, "avg": avg})
    words.sort(key=lambda x: x["avg"], reverse=True)
    return words

def _detect_interests_from_words(words, max_results=3):
    if not words: return []
    all_text = "".join(_full_normalize(w.get("word","")) for w in words)
    res = [(cat, sum(1 for kw in kws if _full_normalize(kw) in all_text)) for cat, kws in WORDS_KEYWORD_MAP.items()]
    res = sorted([r for r in res if r[1] > 0], key=lambda x: x[1], reverse=True)
    return [r[0] for r in res[:max_results]] if res else ["Общение"]

# ==========================================
# 4. ХЕЛПЕРЫ
# ==========================================
def get_time_passed(year, month):
    now = datetime.now()
    reg = datetime(year, month, 1)
    years = now.year - reg.year
    months = now.month - reg.month
    if months < 0: years -= 1; months += 12
    y_str = f"{years} год" if years % 10 == 1 and years % 100 != 11 else \
            f"{years} года" if 2 <= years % 10 <= 4 and not 12 <= years % 100 <= 14 else f"{years} лет"
    if years == 0: y_str = ""
    m_str = f"{months} мес" if months > 0 else ""
    return f"({y_str} {m_str})".strip()

def estimate_registration(user_id):
    ids = sorted(LOCAL_DB.keys())
    if user_id <= ids[0] or user_id >= ids[-1]:
        p = LOCAL_DB[ids[0] if user_id <= ids[0] else ids[-1]].split("-"); return int(p[0]), int(p[1])
    for i in range(len(ids) - 1):
        a, b = ids[i], ids[i+1]
        if a <= user_id <= b:
            d1 = datetime.strptime(LOCAL_DB[a], "%Y-%m")
            d2 = datetime.strptime(LOCAL_DB[b], "%Y-%m")
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
    except Exception as e: print(f"⚠️ API: {e}", flush=True)

def get_phone_details(phone_str):
    out = {"operator": None, "country": None}
    if not phone_str: return out
    try:
        parsed = phonenumbers.parse(phone_str.split(",")[0].strip(), "RU")
        out["operator"] = carrier.name_for_number(parsed, "ru")
        rc = phonenumbers.region_code_for_number(parsed)
        out["country"] = COUNTRY_MAP.get(rc, rc)
    except Exception as e: print(f"⚠️ phone: {e}", flush=True)
    return out

def get_funstat_activity(user_id):
    if not FUNSTAT_TOKEN: return None
    try:
        r = requests.get(f"https://telelog.org/api/v1/users/{user_id}/stats_min",
                         headers={"Authorization": f"Bearer {FUNSTAT_TOKEN}", "accept": "application/json"},
                         timeout=10)
        if r.status_code == 200:
            d = r.json(); lines = []
            for k, label in [("total_msg_count","Всего сообщений"),("total_groups","Всего чатов"),("reactions_count","Получено реакций")]:
                if d.get(k) is not None: lines.append(f"{label}: {d[k]}")
            for k, label in [("first_msg_date","Первое появление"),("last_msg_date","Последнее появление")]:
                if d.get(k): lines.append(f"{label}:\n{d[k].replace('T',' ').replace('Z','')}")
            if lines: return f"<blockquote>{chr(10).join(lines)}</blockquote>"
    except Exception as e: print(f"⚠️ FunStat: {e}", flush=True)

def parse_given_gifts(message):
    """Извлекает ТОЛЬКО ID/username из отправленных подарков. Без мусорных строк."""
    if not message: return ""
    raw = None
    try: raw = message.html
    except:
        try: raw = message.text.html
        except: raw = message.text or message.caption or ""
    if not raw: return ""

    out = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line: continue
        low = line.lower()
        # Отбрасываем заголовки/футер/пагинацию
        if any(x in line for x in ("Аккаунт", "Режим", "Всего", "страниц", "Страниц")): continue
        if "назад" in low or "режим" in low: continue
        if re.search(r'страниц[аы]?\s*\d+', low): continue

        # ID в ссылке tg://user?id=...
        id_match = re.search(r'tg://user\?id=(\d+)', line)
        if id_match:
            uid = id_match.group(1)
            name_match = re.search(r'>([^<]+)</a>', line)
            name = (name_match.group(1).strip() if name_match else "").strip()
            # Убираем emoji/пробелы в имени
            name = re.sub(r'\s+', ' ', name).strip()
            if name:
                out.append(f"{name} — <code>{uid}</code>")
            else:
                out.append(f"<code>{uid}</code>")
            continue

        # @username в ссылке https://t.me/...
        un_match = re.search(r'https://t\.me/([a-zA-Z0-9_]+)', line)
        if un_match:
            un = un_match.group(1)
            name_match = re.search(r'>([^<]+)</a>', line)
            name = (name_match.group(1).strip() if name_match else "").strip()
            name = re.sub(r'\s+', ' ', name).strip()
            if name:
                out.append(f"{name} — @{un}")
            else:
                out.append(f"@{un}")
            continue

    # Убираем дубли
    seen = set(); uniq = []
    for x in out:
        if x not in seen:
            seen.add(x); uniq.append(x)
    return "\n".join(uniq)

# ==========================================
# 5. КЛИЕНТЫ
# ==========================================
bot_app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
user_app = Client("my_userbot", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING, in_memory=True)

@user_app.on_message(filters.chat(OSINT_BOT_USERNAME) & filters.incoming)
@user_app.on_edited_message(filters.chat(OSINT_BOT_USERNAME))
async def osint_handler(client, message):
    global last_osint_msg
    last_osint_msg = message
    osint_event.set()

# ==========================================
# 6. OSINT
# ==========================================
async def _wait_msg(timeout_iters=20, cond=lambda m: bool(m and m.text)):
    global last_osint_msg
    for _ in range(timeout_iters):
        try:
            await asyncio.wait_for(osint_event.wait(), timeout=1.0)
            osint_event.clear()
            if last_osint_msg and cond(last_osint_msg): return last_osint_msg
        except asyncio.TimeoutError: pass
    return None

async def _click(chat_id, msg_id, cb):
    try:
        await user_app.request_callback_answer(chat_id=chat_id, message_id=msg_id, callback_data=cb)
        return True
    except Exception as e:
        print(f"⚠️ click: {e}", flush=True); return False

async def get_osint_data(target, premium_until=None):
    global last_osint_msg
    if not OSINT_BOT_USERNAME: return "", "", "", ""
    async with osint_lock:
        last_osint_msg = None; osint_event.clear()
        try:
            await user_app.send_message(OSINT_BOT_USERNAME, str(target))
            history_text_raw, profile_msg = None, None
            for _ in range(20):
                try:
                    await asyncio.wait_for(osint_event.wait(), timeout=1.0); osint_event.clear()
                    if last_osint_msg:
                        if last_osint_msg.text and not history_text_raw: history_text_raw = last_osint_msg.text
                        if last_osint_msg.reply_markup and not profile_msg: profile_msg = last_osint_msg
                        if history_text_raw and profile_msg: break
                except asyncio.TimeoutError: pass

            chat_info = history_text = interests_text = sent_gifts_text = ""

            # --- История ---
            if history_text_raw:
                fav = re.search(r'(?:ᴧюбимый чαт|Любимый чат)\s*:\s*([^\n]+)', history_text_raw)
                circ = re.search(r'(?:Kрγжки|Кружочки)\s*:\s*(\d+)', history_text_raw)
                voice = re.search(r'(?:голοc|голос)\s*:\s*(\d+)', history_text_raw)
                extra = []
                if fav and fav.group(1).strip(): extra.append(f"Любимый чат: {fav.group(1).strip()}")
                if circ: extra.append(f"Кружочки: {circ.group(1)}")
                if voice: extra.append(f"Голосовые: {voice.group(1)}")
                if premium_until: extra.append(f"Premium до: {premium_until}")
                if extra: chat_info = "❖ <b>Информация из чатов:</b>\n<blockquote>" + "\n".join(extra) + "</blockquote>"

                names = re.findall(r'(\d{4}-\d{2}-\d{2})\s*(?:➜|→|->|—|-)\s*(.+)', history_text_raw)
                ub = re.search(r'(?:uѕerηαmeѕ|usernames|юзернеймы)\s*:?\s*(.*?)(?:\n\n|\Z)', history_text_raw, re.DOTALL | re.IGNORECASE)
                usernames = list(dict.fromkeys(re.findall(r'@([^\s|]+)', ub.group(1)))) if ub else []
                if not usernames: usernames = list(dict.fromkeys(re.findall(r'@([^\s|]+)', history_text_raw)))

                lines = []
                for i in range(max(len(names), len(usernames))):
                    ds = format_date_short(names[i][0]) if i < len(names) else None
                    ns = names[i][1].strip() if i < len(names) else None
                    us = f"@{usernames[i]}" if i < len(usernames) else None
                    parts = [p for p in [ds, f"→ {ns}," if ns else None, us] if p]
                    if parts: lines.append(" ".join(parts).replace(" ,", ","))
                if lines: history_text = f"◈ <b>История профиля:</b>\n<blockquote>{chr(10).join(lines[:5])}</blockquote>"

            # --- Кнопки профиля ---
            if profile_msg and profile_msg.reply_markup:
                # 1. Частота слов
                btn = None
                for row in profile_msg.reply_markup.inline_keyboard:
                    for b in row:
                        n = _full_normalize(b.text)
                        if any(k in n for k in ("част", "слов", "лексик", "топ")) or "🗣" in b.text or "📊" in b.text:
                            btn = b; break
                    if btn: break
                # fallback на 2-ю строку 2-ю кнопку
                if not btn:
                    try: btn = profile_msg.reply_markup.inline_keyboard[1][1]
                    except (IndexError, TypeError): btn = None

                if btn:
                    print(f"👆 [слова] '{btn.text}'", flush=True)
                    last_osint_msg = None; osint_event.clear()
                    await _click(profile_msg.chat.id, profile_msg.id, btn.callback_data)
                    m = await _wait_msg(30, lambda m: bool(m.text or m.caption))
                    if m:
                        txt = m.text or m.caption or ""
                        print(f"🔎 words raw:\n{txt[:1500]}", flush=True)
                        words = _parse_words_response(txt)
                        print(f"🔎 parsed words: {len(words)}", flush=True)
                        cats = _detect_interests_from_words(words)
                        if cats:
                            interests_text = "◉ <b>Интересы:</b>\n<blockquote>" + "\n".join(cats) + "</blockquote>"

                # 2. Подарки → Подарен
                gbtn = None
                for row in profile_msg.reply_markup.inline_keyboard:
                    for b in row:
                        if "подарки" in _full_normalize(b.text) or "🎁" in b.text:
                            gbtn = b; break
                    if gbtn: break

                if gbtn:
                    print(f"👆 [подарки] '{gbtn.text}'", flush=True)
                    last_osint_msg = None; osint_event.clear()
                    await _click(profile_msg.chat.id, profile_msg.id, gbtn.callback_data)
                    menu = await _wait_msg(15, lambda m: bool(m.reply_markup))
                    if menu:
                        gb = None
                        for row in menu.reply_markup.inline_keyboard:
                            for b in row:
                                n = _full_normalize(b.text)
                                if ("даре" in n or "дарен" in n or "⇉" in b.text) and "взаимно" not in n:
                                    gb = b; break
                            if gb: break
                        if gb:
                            print(f"👆 [подарен] '{gb.text}'", flush=True)
                            last_osint_msg = None; osint_event.clear()
                            await _click(menu.chat.id, menu.id, gb.callback_data)
                            m = await _wait_msg(20, lambda m: bool((m.text or m.caption or "").strip()))
                            if m:
                                sent_gifts_text = parse_given_gifts(m)
                                print(f"📄 gifts parsed len={len(sent_gifts_text)}", flush=True)

            return chat_info, history_text, interests_text, sent_gifts_text
        except Exception as e:
            print(f"⚠️ OSINT: {e}", flush=True)
        return "", "", "", ""

# ==========================================
# 7. КАРТОЧКА
# ==========================================
async def process_user(client, message, user_id, status=None, original_input=None):
    status = status or await message.reply_text("✧ <b>Собираю данные...</b>", parse_mode=enums.ParseMode.HTML)
    user_obj = None
    try: user_obj = await user_app.get_users(user_id)
    except: pass

    premium_until = None
    if user_obj and user_obj.is_premium:
        try:
            fi = await user_app.invoke(GetFullUser(id=await user_app.resolve_peer(user_id)))
            if getattr(fi.full_user, 'premium_expiration_date', None):
                premium_until = datetime.fromtimestamp(fi.full_user.premium_expiration_date).strftime("%d.%m.%Y")
        except: pass

    username_str = f"@{user_obj.username}" if user_obj and user_obj.username else "Скрыт"
    dc_id = user_obj.dc_id if user_obj and user_obj.dc_id else None
    est_y, est_m = estimate_registration(user_id)
    reg_date = f"\\~ {MONTHS_RU.get(est_m,'')} {est_y} {get_time_passed(est_y, est_m)}" if est_y else None

    phone_str = get_phones_from_api(user_id)
    gifts_lines, nft_lines = [], []
    try:
        peer = await user_app.resolve_peer(username_str if username_str != "Скрыт" else user_id)
        res = await user_app.invoke(GetSavedStarGifts(peer=peer, offset="", limit=100))
        for g in (res.gifts or []):
            gift = getattr(g, "gift", None)
            if getattr(gift, "slug", None): nft_lines.append(f"{gift.title} #{gift.num}")
            elif not getattr(g, 'name_hidden', False) and g.from_id:
                sid = getattr(g.from_id, 'user_id', None)
                if sid: gifts_lines.append(f"<code>{sid}</code>")
    except Exception as e: print(f"❌ gifts: {e}", flush=True)

    chat_info, history_text, interests_text, sent_gifts_text = await get_osint_data(original_input or user_id, premium_until)

    header = [f"ID: <code>{user_id}</code>"]
    if username_str != "Скрыт": header.append(f"Username: {username_str}")
    if reg_date: header.append(f"Регистрация: {reg_date}")
    if dc_id: header.append(f"DC: {dc_id}")
    card = "⌬ <b>Аккаунт Telegram</b>\n" + "\n".join(
        f"{'└ ' if i == len(header)-1 else '├ '}{line}" for i, line in enumerate(header)) + "\n"

    if phone_str:
        d = get_phone_details(phone_str)
        plines = [f"Оператор: {d['operator']}"] if d['operator'] else []
        if d['country']: plines.append(f"Страна: {d['country']}")
        card += f"\n✆ <b>Телефон:</b> <code>{phone_str}</code>\n" + "\n".join(
            f"{'└ ' if i == len(plines)-1 else '├ '}{line}" for i, line in enumerate(plines)) + "\n"

    if chat_info: card += f"\n{chat_info}\n"
    fs = get_funstat_activity(user_id)
    if fs: card += f"\n▲ <b>Активность:</b>\n{fs}\n\n"
    if nft_lines: card += f"✦ <b>NFT ({len(nft_lines)})</b>\n<blockquote>{', '.join(nft_lines)}</blockquote>\n"
    if gifts_lines: card += f"\n✾ <b>Полученные подарки:</b>\n<blockquote>{', '.join(gifts_lines)}</blockquote>"
    if sent_gifts_text: card += f"\n\n✿ <b>Отправленные подарки:</b>\n<blockquote>{sent_gifts_text}</blockquote>"
    if history_text: card += f"\n\n{history_text}"
    if interests_text: card += f"\n\n{interests_text}"

    if len(card) > 4000:
        await status.edit_text(card[:4000] + "\n<i>... (продолжение ниже)</i>", parse_mode=enums.ParseMode.HTML)
        await message.reply_text(card[4000:], parse_mode=enums.ParseMode.HTML)
    else:
        await status.edit_text(card, parse_mode=enums.ParseMode.HTML)

# ==========================================
# 8. ХЕНДЛЕРЫ БОТА
# ==========================================
request_user_obj = RequestPeerTypeUser(button_id=1, is_bot=False)
reply_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton("Выбрать пользователя", request_user=request_user_obj)]],
    resize_keyboard=True)

@bot_app.on_message(filters.command("start"))
async def start_command(client, message):
    await message.reply_text("⌬ Отправь мне ID, @username или контакт, и я покажу информацию.",
                             reply_markup=reply_keyboard, parse_mode=enums.ParseMode.HTML)

@bot_app.on_message(filters.contact & filters.private)
async def handle_contact(client, message):
    if message.contact.user_id:
        await process_user(client, message, message.contact.user_id, original_input=message.contact.user_id)

@bot_app.on_message(filters.private)
async def handle_input(client, message):
    shared = getattr(message, "users_shared", None) or getattr(message, "chats_shared", None)
    if shared and getattr(shared, "users", None):
        for item in shared.users:
            uid = getattr(item, "user_id", None)
            if uid: await process_user(client, message, uid, original_input=uid)
        return
    if message.text: await search_handler(client, message)

async def search_handler(client, message):
    targets = [q.strip() for q in re.split(r'[,\s\n]+', message.text.strip()) if q.strip()]

    if len(targets) > 1:
        status = await message.reply_text(f"📥 <b>В очереди: {len(targets)} запросов.</b>\nНачинаю обработку...", parse_mode=enums.ParseMode.HTML)
        for i, target in enumerate(targets, 1):
            try: await status.edit_text(f"⌛ <b>Обрабатываю {i}/{len(targets)}: {target}</b>", parse_mode=enums.ParseMode.HTML)
            except: pass
            uid = None
            if target.isdigit(): uid = int(target)
            else:
                un = target if target.startswith('@') else f"@{target}"
                try: uid = (await user_app.get_users(un)).id
                except: print(f"⚠️ Не найден {target}", flush=True); continue
            if uid:
                temp = await message.reply_text(f"📡 Собираю данные для {target}...", parse_mode=enums.ParseMode.HTML)
                try: await process_user(client, message, uid, status=temp, original_input=target)
                except FloodWait as e: await asyncio.sleep(e.value)
                except Exception as e: print(f"⚠️ {e}", flush=True)
                                await asyncio.sleep(3)
        try: await status.edit_text("✅ <b>Все запросы обработаны!</b>", parse_mode=enums.ParseMode.HTML)
        except: pass
        return

    target = targets[0]
    status = await message.reply_text("✧ <b>Обрабатываю запрос...</b>", parse_mode=enums.ParseMode.HTML)
    if target.isdigit():
        await process_user(client, message, int(target), status, original_input=target)
    else:
        un = target if target.startswith('@') else f"@{target}"
        try: await process_user(client, message, (await user_app.get_users(un)).id, status, original_input=un)
        except Exception as e:
            print(f"⚠️ {e}", flush=True)
            await status.edit_text("✖ <b>Ошибка:</b> Юзернейм не найден.", parse_mode=enums.ParseMode.HTML)

# ==========================================
# 9. MAIN
# ==========================================
async def main():
    threading.Thread(target=run_web_server, daemon=True).start()
    try:
        print("🤖 Запускаю бота...", flush=True); await bot_app.start()
        print("👤 Запускаю юзербота...", flush=True); await user_app.start()
        print("🚀 Всё готово!", flush=True)
    except Exception as e:
        print(f"❌ ОШИБКА: {e}", flush=True); return
    await idle()
    await bot_app.stop(); await user_app.stop()

if __name__ == "__main__":
    loop.run_until_complete(main())
