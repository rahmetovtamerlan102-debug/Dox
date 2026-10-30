import os, re, asyncio, threading, requests, phonenumbers
from phonenumbers import carrier
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime
from pyrogram import Client, filters, enums, idle
from pyrogram.raw.functions.payments import GetSavedStarGifts
from pyrogram.raw.functions.users import GetFullUser
from pyrogram.types import ReplyKeyboardMarkup, KeyboardButton, RequestPeerTypeUser
from pyrogram.errors import FloodWait

# 1. СЕРВЕР
loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
class H(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
    def do_HEAD(self): self.send_response(200); self.end_headers()
def run_web(): HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), H).serve_forever()

# 2. КОНФИГ
API_ID, API_HASH = int(os.environ.get("API_ID", 0)), os.environ.get("API_HASH", "")
BOT_TOKEN, SESSION_STRING = os.environ.get("BOT_TOKEN", ""), os.environ.get("SESSION_STRING", "")
API_URL, FUNSTAT_TOKEN = os.environ.get("API_URL", ""), os.environ.get("FUNSTAT_TOKEN", "")
OSINT_BOT = "@evenfa_bot"
LOCAL_DB = {100000000:"2013-08",200000000:"2014-01",300000000:"2014-07",400000000:"2014-12",5000000000:"2015-04",7000000000:"2015-10",1000000000:"2016-04",1500000000:"2017-01",2000000000:"2017-07",3000000000:"2018-06",4000000000:"2019-04",5000000000:"2020-01",6000000000:"2021-02",7000000000:"2022-06",7500000000:"2023-05",8000000000:"2024-06",8500000000:"2025-01"}
MONTHS_RU = {1:"январь",2:"февраль",3:"март",4:"апрель",5:"май",6:"июнь",7:"июль",8:"август",9:"сентябрь",10:"октябрь",11:"ноябрь",12:"декабрь"}
MONTHS_SHORT = {"01":"янв","02":"фев","03":"мар","04":"апр","05":"мая","06":"июн","07":"июл","08":"авг","09":"сен","10":"окт","11":"ноя","12":"дек"}
COUNTRY_MAP = {"RU":"Россия","KZ":"Казахстан","UA":"Украина","BY":"Беларусь","UZ":"Узбекистан","KG":"Кыргызстан","TJ":"Таджикистан","AM":"Армения","GE":"Грузия","AZ":"Азербайджан","MD":"Молдова","LV":"Латвия","LT":"Литва","EE":"Эстония","US":"США","GB":"Великобритания","DE":"Германия","FR":"Франция","TR":"Турция","AE":"ОАЭ"}

osint_event, osint_lock, last_osint_msg = asyncio.Event(), asyncio.Lock(), None

# 3. ИНТЕРЕСЫ
_TO_KIRILLIC = {'a':'а','b':'в','c':'с','d':'д','e':'е','f':'ф','g':'г','h':'н','i':'и','j':'й','k':'к','l':'л','m':'м','n':'п','o':'о','p':'п','q':'к','r':'р','s':'с','t':'т','u':'и','v':'в','w':'в','x':'х','y':'у','z':'з','α':'а','β':'в','γ':'у','δ':'д','ε':'е','ζ':'з','η':'н','θ':'т','ι':'и','κ':'к','λ':'л','μ':'м','ν':'в','ξ':'х','ο':'о','Π':'п','π':'п','ρ':'р','σ':'с','ς':'с','τ':'т','υ':'и','φ':'ф','χ':'х','ψ':'п','ω':'о','ց':'г','ᴜ':'и','ᴠ':'в','ᴧ':'l','ᴨ':'п','ᴦ':'р','ᴋ':'ᴋ','ᴍ':'м','ᴏ':'о','ᴘ':'п','ᴛ':'t','ᴅ':'d','ᴣ':'з','ᴊ':'й','ᴡ':'в','ʏ':'у','ɪ':'и','ᴎ':'н','ᴓ':'о','Ⅼ':'л','Ⅽ':'с','Ⅾ':'d','Ⅿ':'м','ⅰ':'и','ⅼ':'l','ⅽ':'с','ⅾ':'d','ⅿ':'m','℮':'е','ƒ':'ф','ł':'l','қ':'k','ѕ':'с','і':'и','ї':'и','ў':'у','ј':'j'}
_KIR_TRANS = str.maketrans(_TO_KIRILLIC)
def _full(s): return str(s or "").translate(_KIR_TRANS).lower()

WORDS_MAP = {
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

def _parse_words(text):
    words = []
    for line in str(text or "").split("\n"):
        m = re.match(r'^[├└]\s*\*?\*?(\d+)\s*[-–]\s*(\d+)\s+(.+)$', line.strip())
        if not m: continue
        try: avg = (int(m.group(1)) + int(m.group(2))) // 2
        except: continue
        for w in m.group(3).split(","):
            wc = re.sub(r'[^\w-]+', '', w, flags=re.UNICODE).strip()
            if wc and len(wc) >= 3: words.append({"w": wc, "a": avg})
    return sorted(words, key=lambda x: x["a"], reverse=True)

def _get_interests(words):
    txt = "".join(_full(w["w"]) for w in words)
    res = [(c, sum(1 for k in kws if _full(k) in txt)) for c, kws in WORDS_MAP.items()]
    res = sorted([r for r in res if r[1] > 0], key=lambda x: x[1], reverse=True)
    return [r[0] for r in res[:3]] if res else ["Общение"]

# 4. ХЕЛПЕРЫ
def _date_short(d):
    try: p = d.split("-"); return f"{p[2]} {MONTHS_SHORT.get(p[1], p[1])} {p[0][2:]}"
    except: return d

def _time_passed(y, m):
    n = datetime.now(); r = datetime(y, m, 1)
    ys, ms = n.year - r.year, n.month - r.month
    if ms < 0: ys -= 1; ms += 12
    y_str = f"{ys} год" if ys%10==1 and ys%100!=11 else f"{ys} года" if 2<=ys%10<=4 and not 12<=ys%100<=14 else f"{ys} лет"
    return f"({y_str if ys else ''} {ms if ms else ''} мес)".strip()

def _estimate_reg(uid):
    ids = sorted(LOCAL_DB.keys())
    if uid <= ids[0] or uid >= ids[-1]: return map(int, LOCAL_DB[ids[0] if uid <= ids[0] else ids[-1]].split("-"))
    for i in range(len(ids)-1):
        a, b = ids[i], ids[i+1]
        if a <= uid <= b:
            d1, d2 = datetime.strptime(LOCAL_DB[a], "%Y-%m"), datetime.strptime(LOCAL_DB[b], "%Y-%m")
            add = int((uid - a) / (b - a) * max((d2.year-d1.year)*12 + d2.month-d1.month, 1))
            return d1.year + (d1.month + add - 1)//12, (d1.month + add - 1) % 12 + 1
    return None, None

def _get_phone(uid):
    if not API_URL: return None
    try:
        r = requests.get(f"{API_URL}/get_phone", params={"id": uid}, timeout=10)
        if r.status_code == 200: return ", ".join(r.json().get("phones", [])) or None
    except: pass

def _phone_details(p):
    if not p: return {}
    try:
        num = phonenumbers.parse(p.split(",")[0].strip(), "RU")
        rc = phonenumbers.region_code_for_number(num)
        return {"operator": carrier.name_for_number(num, "ru"), "country": COUNTRY_MAP.get(rc, rc)}
    except: return {}

def _get_funstat(uid):
    if not FUNSTAT_TOKEN: return None
    try:
        r = requests.get(f"https://telelog.org/api/v1/users/{uid}/stats_min", headers={"Authorization": f"Bearer {FUNSTAT_TOKEN}"}, timeout=10)
        if r.status_code == 200:
            d = r.json(); l = []
            for k, v in [("total_msg_count","Сообщений"),("total_groups","Чатов"),("reactions_count","Реакций")]:
                if d.get(k) is not None: l.append(f"{v}: {d[k]}")
            for k, v in [("first_msg_date","Первое"),("last_msg_date","Последнее")]:
                if d.get(k): l.append(f"{v}:\n{d[k].replace('T',' ').replace('Z','')}")
            return f"<blockquote>{chr(10).join(l)}</blockquote>" if l else None
    except: pass

def _parse_gifts(msg):
    if not msg: return ""
    raw = getattr(msg, 'html', None) or getattr(msg, 'text', None) or getattr(msg, 'caption', None) or ""
    out = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line or any(x in line for x in ("Аккаунт", "Режим", "Всего", "страниц")) or "назад" in line.lower(): continue
        id_m = re.search(r'tg://user\?id=(\d+)', line)
        if id_m:
            name = (re.search(r'>([^<]+)</a>', line) or [None, ""])[1].strip()
            out.append(f"{name} — <code>{id_m.group(1)}</code>" if name else f"<code>{id_m.group(1)}</code>")
            continue
        un_m = re.search(r'https://t\.me/([a-zA-Z0-9_]+)', line)
        if un_m:
            name = (re.search(r'>([^<]+)</a>', line) or [None, ""])[1].strip()
            out.append(f"{name} — @{un_m.group(1)}" if name else f"@{un_m.group(1)}")
    return "\n".join(dict.fromkeys(out))

# 5. КЛИЕНТЫ И ОЖИДАНИЕ
bot = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
user = Client("my_userbot", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING, in_memory=True)

@user.on_message(filters.chat(OSINT_BOT) & filters.incoming)
@user.on_edited_message(filters.chat(OSINT_BOT))
async def on_osint(c, m):
    global last_osint_msg; last_osint_msg = m; osint_event.set()

async def _wait(cond=lambda m: bool(m and m.text), iters=20):
    global last_osint_msg
    for _ in range(iters):
        try:
            await asyncio.wait_for(osint_event.wait(), 1.0); osint_event.clear()
            if last_osint_msg and cond(last_osint_msg): return last_osint_msg
        except asyncio.TimeoutError: pass

async def _click(msg, cb):
    try: await user.request_callback_answer(msg.chat.id, msg.id, cb)
    except Exception as e: print(f"⚠️ click: {e}")

def _find_btn(markup, words):
    if not markup: return None
    for row in markup.inline_keyboard:
        for b in row:
            if any(w in _full(b.text) for w in words): return b

# 6. СБОР ДАННЫХ ИЗ @evenfa_bot
async def get_osint(target, premium=None):
    global last_osint_msg
    async with osint_lock:
        last_osint_msg = None; osint_event.clear()
        try:
            await user.send_message(OSINT_BOT, str(target))
            hist_raw, prof_msg = None, None
            for _ in range(20):
                try:
                    await asyncio.wait_for(osint_event.wait(), 1.0); osint_event.clear()
                    if last_osint_msg:
                        if last_osint_msg.text and not hist_raw: hist_raw = last_osint_msg.text
                        if last_osint_msg.reply_markup and not prof_msg: prof_msg = last_osint_msg
                        if hist_raw and prof_msg: break
                except asyncio.TimeoutError: pass
            
            chat_info = hist_text = interests = gifts_text = ""
            
            # История
            if hist_raw:
                extra = []
                if (m := re.search(r'(?:ᴧюбимый чαт|Любимый чат)\s*:\s*([^\n]+)', hist_raw)): extra.append(f"Любимый чат: {m.group(1).strip()}")
                if (m := re.search(r'(?:Kрγжки|Кружочки)\s*:\s*(\d+)', hist_raw)): extra.append(f"Кружочки: {m.group(1)}")
                if (m := re.search(r'(?:голοc|голос)\s*:\s*(\d+)', hist_raw)): extra.append(f"Голосовые: {m.group(1)}")
                if premium: extra.append(f"Premium до: {premium}")
                if extra: chat_info = f"❖ <b>Информация из чатов:</b>\n<blockquote>{chr(10).join(extra)}</blockquote>"
                
                names = re.findall(r'(\d{4}-\d{2}-\d{2})\s*(?:➜|→|->|—|-)\s*(.+)', hist_raw)
                ub = re.search(r'(?:uѕerηαmeѕ|usernames|юзернеймы)\s*:?\s*(.*?)(?:\n\n|\Z)', hist_raw, re.DOTALL | re.IGNORECASE)
                unames = list(dict.fromkeys(re.findall(r'@([^\s|]+)', ub.group(1)))) if ub else list(dict.fromkeys(re.findall(r'@([^\s|]+)', hist_raw)))
                h_lines = []
                for i in range(max(len(names), len(unames))):
                    parts = [x for x in [_date_short(names[i][0]) if i < len(names) else None, f"→ {names[i][1].strip()}," if i < len(names) else None, f"@{unames[i]}" if i < len(unames) else None] if x]
                    if parts: h_lines.append(" ".join(parts).replace(" ,", ","))
                if h_lines: hist_text = f"◈ <b>История:</b>\n<blockquote>{chr(10).join(h_lines[:5])}</blockquote>"
            
            # Кнопки профиля
            if prof_msg:
                # Слова
                btn = _find_btn(prof_msg.reply_markup, ["част", "слов", "лексик", "топ", "🗣", "📊"]) or (prof_msg.reply_markup.inline_keyboard[1][1] if len(prof_msg.reply_markup.inline_keyboard)>1 and len(prof_msg.reply_markup.inline_keyboard[1])>1 else None)
                if btn:
                    last_osint_msg = None; osint_event.clear(); await _click(prof_msg, btn.callback_data)
                    if (m := await _wait(lambda m: bool(m.text or m.caption), 30)):
                        txt = m.text or m.caption or ""
                        if cats := _get_interests(_parse_words(txt)):
                            interests = f"◉ <b>Интересы:</b>\n<blockquote>{chr(10).join(cats)}</blockquote>"
                
                # Подарки
                gbtn = _find_btn(prof_msg.reply_markup, ["подарки", "🎁"])
                if gbtn:
                    last_osint_msg = None; osint_event.clear(); await _click(prof_msg, gbtn.callback_data)
                    if menu := await _wait(lambda m: bool(m.reply_markup), 15):
                        gb = _find_btn(menu.reply_markup, ["даре", "дарен", "⇉"])
                        if gb and "взаимно" not in _full(gb.text):
                            last_osint_msg = None; osint_event.clear(); await _click(menu, gb.callback_data)
                            if m := await _wait(lambda m: bool(m.text or m.caption), 20):
                                gifts_text = _parse_gifts(m)
            return chat_info, hist_text, interests, gifts_text
        except Exception as e:
            print(f"⚠️ OSINT: {e}")
        return "", "", "", ""

# 7. ГЛАВНАЯ КАРТОЧКА
async def process_user(msg, uid, status=None, orig=None):
    status = status or await msg.reply_text("✧ <b>Собираю данные...</b>", parse_mode=enums.ParseMode.HTML)
    u = None
    try: u = await user.get_users(uid)
    except: pass
    
    prem = None
    if u and u.is_premium:
        try:
            fi = await user.invoke(GetFullUser(id=await user.resolve_peer(uid)))
            if fi.full_user.premium_expiration_date: prem = datetime.fromtimestamp(fi.full_user.premium_expiration_date).strftime("%d.%m.%Y")
        except: pass

    un_str = f"@{u.username}" if u and u.username else "Скрыт"
    ey, em = _estimate_reg(uid)
    reg = f"\\~ {MONTHS_RU.get(em,'')} {ey} {_time_passed(ey, em)}" if ey else None
    
    ph_str = _get_phone(uid)
    gl, nl = [], []
    try:
        res = await user.invoke(GetSavedStarGifts(peer=await user.resolve_peer(un_str if un_str!="Скрыт" else uid), offset="", limit=100))
        for g in (res.gifts or []):
            gift = getattr(g, "gift", None)
            if getattr(gift, "slug", None): nl.append(f"{gift.title} #{gift.num}")
            elif not getattr(g, 'name_hidden', False) and g.from_id:
                if sid := getattr(g.from_id, 'user_id', None): gl.append(f"<code>{sid}</code>")
    except: pass

    c_info, h_text, i_text, s_gifts = await get_osint(orig or uid, prem)
    
    h = [f"ID: <code>{uid}</code>"]
    if un_str != "Скрыт": h.append(f"Username: {un_str}")
    if reg: h.append(f"Регистрация: {reg}")
    if u and u.dc_id: h.append(f"DC: {u.dc_id}")
    
    card = "⌬ <b>Аккаунт Telegram</b>\n" + "\n".join(f"{'└ ' if i==len(h)-1 else '├ '}{x}" for i,x in enumerate(h)) + "\n"
    
    if ph_str:
        d = _phone_details(ph_str)
        p_lines = [x for x in [f"Оператор: {d.get('operator')}" if d.get('operator') else None, f"Страна: {d.get('country')}" if d.get('country') else None] if x]
        card += f"\n✆ <b>Телефон:</b> <code>{ph_str}</code>\n" + "\n".join(f"{'└ ' if i==len(p_lines)-1 else '├ '}{x}" for i,x in enumerate(p_lines)) + "\n"
        
    if c_info: card += f"\n{c_info}\n"
    if fs := _get_funstat(uid): card += f"\n▲ <b>Активность:</b>\n{fs}\n\n"
    if nl: card += f"✦ <b>NFT ({len(nl)})</b>\n<blockquote>{', '.join(nl)}</blockquote>\n"
    if gl: card += f"\n✾ <b>Полученные подарки:</b>\n<blockquote>{', '.join(gl)}</blockquote>"
    if s_gifts: card += f"\n\n✿ <b>Отправленные подарки:</b>\n<blockquote>{s_gifts}</blockquote>"
    if h_text: card += f"\n\n{h_text}"
    if i_text: card += f"\n\n{i_text}"

    if len(card) > 4000:
        await status.edit_text(card[:4000] + "\n<i>... (продолжение ниже)</i>", parse_mode=enums.ParseMode.HTML)
        await msg.reply_text(card[4000:], parse_mode=enums.ParseMode.HTML)
    else:
        await status.edit_text(card, parse_mode=enums.ParseMode.HTML)

# 8. ХЕНДЛЕРЫ БОТА
req_user = RequestPeerTypeUser(button_id=1, is_bot=False)
kb = ReplyKeyboardMarkup([[KeyboardButton("Выбрать пользователя", request_user=req_user)]], resize_keyboard=True)

@bot.on_message(filters.command("start"))
async def start(c, m): await m.reply_text("⌬ Отправь мне ID, @username или контакт.", reply_markup=kb, parse_mode=enums.ParseMode.HTML)

@bot.on_message(filters.contact & filters.private)
async def contact(c, m):
    if m.contact.user_id: await process_user(m, m.contact.user_id, orig=m.contact.user_id)

@bot.on_message(filters.private)
async def handle_msg(c, m):
    shared = getattr(m, "users_shared", None) or getattr(m, "chats_shared", None)
    if shared and getattr(shared, "users", None):
        for item in shared.users:
            if uid := getattr(item, "user_id", None): await process_user(m, uid, orig=uid)
        return
    if not m.text: return
    
    targets = [q.strip() for q in re.split(r'[,\s\n]+', m.text.strip()) if q.strip()]
    if len(targets) > 1:
        st = await m.reply_text(f"📥 <b>В очереди: {len(targets)} запросов.</b>", parse_mode=enums.ParseMode.HTML)
        for i, t in enumerate(targets, 1):
            try: await st.edit_text(f"⌛ <b>Обрабатываю {i}/{len(targets)}: {t}</b>", parse_mode=enums.ParseMode.HTML)
            except: pass
            
            uid = int(t) if t.isdigit() else None
            if not uid:
                try: uid = (await user.get_users(t if t.startswith('@') else f"@{t}")).id
                except: print(f"⚠️ Не найден {t}"); continue
            
            temp = await m.reply_text(f"📡 Собираю данные для {t}...", parse_mode=enums.ParseMode.HTML)
            try: await process_user(m, uid, status=temp, orig=t)
            except FloodWait as e: await asyncio.sleep(e.value)
            except Exception as e: print(f"⚠️ {e}")
            await asyncio.sleep(3)
        try: await st.edit_text("✅ <b>Все запросы обработаны!</b>", parse_mode=enums.ParseMode.HTML)
        except: pass
        return

    t = targets[0]
    st = await m.reply_text("✧ <b>Обрабатываю запрос...</b>", parse_mode=enums.ParseMode.HTML)
    if t.isdigit(): await process_user(m, int(t), st, orig=t)
    else:
        try: await process_user(m, (await user.get_users(t if t.startswith('@') else f"@{t}")).id, st, orig=t)
        except: await st.edit_text("✖ <b>Ошибка:</b> Юзернейм не найден.", parse_mode=enums.ParseMode.HTML)

# 9. ЗАПУСК
async def main():
    threading.Thread(target=run_web, daemon=True).start()
    try:
        await bot.start(); await user.start(); print("🚀 Всё готово!", flush=True)
        await idle()
    except Exception as e: print(f"❌ ОШИБКА: {e}")
    finally:
        await bot.stop(); await user.stop()

if __name__ == "__main__":
    loop.run_until_complete(main())
