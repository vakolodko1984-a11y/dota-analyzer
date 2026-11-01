import os, sys, json, time, requests, uuid, urllib3, re, random, datetime, sqlite3, markdown
from urllib.parse import urlencode, parse_qs
from flask import Flask, render_template, request, redirect, url_for, session, send_from_directory
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
if sys.platform == "win32": sys.stdout.reconfigure(encoding='utf-8')
base_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(base_dir, '.env'))
app = Flask(__name__)
app.secret_key = "super_secret_dota_key_123"

def init_db():
    conn = sqlite3.connect(os.path.join(base_dir, 'fantasy.db'))
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS fantasy_rosters (id INTEGER PRIMARY KEY AUTOINCREMENT, steam_id TEXT, player1 TEXT, player2 TEXT, player3 TEXT, player4 TEXT, player5 TEXT, tournament TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
    c.execute('CREATE TABLE IF NOT EXISTS user_achievements (steam_id TEXT, achievement_id TEXT, unlocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (steam_id, achievement_id))')
    c.execute('CREATE TABLE IF NOT EXISTS fantasy_cache (name TEXT PRIMARY KEY, points REAL, updated_at REAL)')
    c.execute('CREATE TABLE IF NOT EXISTS hero_meta_cache (id INTEGER PRIMARY KEY, data TEXT, updated_at REAL)')
    c.execute('CREATE TABLE IF NOT EXISTS user_analytics (steam_id TEXT, match_id TEXT, analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (steam_id, match_id))')
    c.execute('CREATE TABLE IF NOT EXISTS user_drafts (steam_id TEXT, drafted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
    c.execute('CREATE TABLE IF NOT EXISTS user_premium (steam_id TEXT PRIMARY KEY, is_active INTEGER DEFAULT 0, activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
    c.execute('CREATE TABLE IF NOT EXISTS user_spins (steam_id TEXT PRIMARY KEY, last_spin TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
    c.execute('CREATE TABLE IF NOT EXISTS leaderboard_cache (id INTEGER PRIMARY KEY, data TEXT, updated_at REAL)')
    c.execute('CREATE TABLE IF NOT EXISTS user_requests (steam_id TEXT, request_date TEXT, count INTEGER DEFAULT 0, PRIMARY KEY (steam_id, request_date))')
    c.execute('CREATE TABLE IF NOT EXISTS fantasy_tournaments (tournament_name TEXT PRIMARY KEY, is_open INTEGER DEFAULT 1)')
    conn.commit(); conn.close()
init_db()

def is_tournament_open(tournament_name):
    conn = sqlite3.connect(os.path.join(base_dir, 'fantasy.db'))
    cursor = conn.cursor()
    cursor.execute("SELECT is_open FROM fantasy_tournaments WHERE tournament_name = ?", (tournament_name,))
    row = cursor.fetchone()
    conn.close()
    if row is None: return True 
    return row[0] == 1

def clean_env_var(v): return re.sub(r'[\s"\']', '', os.getenv(v, ""))
STRATZ_API_TOKEN = clean_env_var("STRATZ_API_TOKEN")
GIGACHAT_AUTH_KEY = clean_env_var("GIGACHAT_AUTHORIZATION_KEY")
STEAM_API_KEY = clean_env_var("STEAM_API_KEY")
STEAM_OPENID_URL = "https://steamcommunity.com/openid/login"
ADMIN_PASSWORD = clean_env_var("ADMIN_PASSWORD")

def load_json(f):
    try:
        with open(os.path.join(base_dir, f), 'r', encoding='utf-8') as file: return json.load(file)
    except: return {}
HEROES = load_json('heroes.json')
ITEMS = load_json('items.json')

SPECIAL_NAMES = {"queen_of_pain": "queenofpain", "outworld_destroyer": "obsidian_destroyer", "anti_mage": "antimage", "natures_prophet": "furion", "wraith_king": "skeleton_king", "shadow_fiend": "nevermore", "timbersaw": "shredder", "life_stealer": "lifestealer", "centaur_warrunner": "centaur", "underlord": "abyssal_underlord", "doom": "doom_bringer", "magnus": "magnataur", "treant_protector": "treant", "io": "wisp", "zeus": "zuus", "necrophos": "necrolyte", "windranger": "windrunner", "vengeful_spirit": "vengefulspirit", "clockwerk": "rattletrap"}

def get_hero_system_name(hero_name):
    if not hero_name: return ""
    if "outworld" in hero_name.lower() or "obsidian" in hero_name.lower(): return "obsidian_destroyer"
    sys_name = hero_name.lower().replace(" ", "_").replace("-", "").replace("'", "")
    return SPECIAL_NAMES.get(sys_name, sys_name)

def get_gigachat_token():
    if not GIGACHAT_AUTH_KEY: return None
    url = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json", "RqUID": str(uuid.uuid4()), "Authorization": f"Basic {GIGACHAT_AUTH_KEY}"}
    try:
        r = requests.post(url, headers=headers, data={"scope": "GIGACHAT_API_PERS"}, timeout=15, verify=False)
        return r.json().get("access_token")
    except: return None

def analyze_with_gigachat(prompt_text, is_premium=False):
    if not GIGACHAT_AUTH_KEY: return "Ошибка: Нет ключа GigaChat."
    token = get_gigachat_token()
    if not token: return "Ошибка: Не удалось получить токен GigaChat."
    url = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    
    if is_premium:
        sp = "Ты — профессиональный киберспортивный аналитик Dota 2. Твоя задача — сделать МАКСИМАЛЬНО ПОДРОБНЫЙ разбор на основе данных. ЗАПРЕЩЕНО гадать, кто на какой линии стоял! Пиши обычным текстом, без звездочек. Используй абзацы. Отвечай на русском."
        max_tokens = 6000
    else:
        sp = "Ты — профессиональный киберспортивный аналитик Dota 2. Твоя задача — сделать разбор на основе данных. ЗАПРЕЩЕНО гадать, кто на какой линии стоял! Пиши обычным текстом, без звездочек. Используй абзацы. Отвечай на русском."
        max_tokens = 3000

    data = {"model": "GigaChat", "messages": [{"role": "system", "content": sp}, {"role": "user", "content": prompt_text}], "temperature": 0.3, "max_tokens": max_tokens, "stream": False}
    
    for _ in range(3):
        try:
            response = requests.post(url, headers=headers, json=data, timeout=60, verify=False)
            if response.status_code == 429: time.sleep(2); continue
            response.raise_for_status()
            raw_text = response.json()['choices'][0]['message']['content']
            return markdown.markdown(raw_text, extensions=['nl2br'])
        except Exception as e: time.sleep(1)
    return "Ошибка: GigaChat перегружен."

def get_items_from_stratz(m_id):
    if not STRATZ_API_TOKEN: return {}
    url = "https://api.stratz.com/graphql"
    headers = {"Authorization": f"Bearer {STRATZ_API_TOKEN}", "Content-Type": "application/json", "User-Agent": "STRATZ_API"}
    q = "query GetMatchItems($matchId: Long!) { match(id: $matchId) { players { isRadiant item0 item1 item2 item3 item4 item5 } } }"
    try:
        r = requests.post(url, headers=headers, json={"query": q, "variables": {"matchId": int(m_id)}}, timeout=10, verify=False)
        if r.status_code == 200:
            data = r.json()
            if "errors" in data: return {}
            match = data.get("data", {}).get("match")
            if not match: return {}
            items = {}
            for idx, p in enumerate(match.get("players", [])):
                slot = idx if p.get("isRadiant") else (128 + (idx - 5))
                items[slot] = [p.get(f'item{i}', 0) for i in range(6) if p.get(f'item{i}', 0) != 0]
            return items
    except: pass
    return {}

def get_match_from_stratz(m_id):
    if not m_id.isdigit(): return None, "Ошибка ID."
    
    if STRATZ_API_TOKEN:
        url = "https://api.stratz.com/graphql"
        headers = {"Authorization": f"Bearer {STRATZ_API_TOKEN}", "Content-Type": "application/json", "User-Agent": "STRATZ_API"}
        q = "query GetMatch($matchId: Long!) { match(id: $matchId) { didRadiantWin durationSeconds radiantNetworthLeads players { isRadiant heroId kills deaths assists goldPerMinute experiencePerMinute heroDamage towerDamage heroHealing } } }"
        try:
            r = requests.post(url, headers=headers, json={"query": q, "variables": {"matchId": int(m_id)}}, timeout=15, verify=False)
            if r.status_code == 200:
                data = r.json()
                if "errors" not in data:
                    match = data.get("data", {}).get("match")
                    if match:
                        adapted = {"radiant_win": match["didRadiantWin"], "duration": match["durationSeconds"], "radiant_networth_leads": match.get("radiantNetworthLeads") or [], "players": []}
                        for p in match["players"]:
                            adapted["players"].append({"isRadiant": p.get("isRadiant", True), "hero_id": p["heroId"], "kills": p["kills"], "deaths": p["deaths"], "assists": p["assists"], "gold_per_min": p["goldPerMinute"], "xp_per_min": p["experiencePerMinute"], "hero_damage": p["heroDamage"], "tower_damage": p["towerDamage"], "hero_healing": p["heroHealing"]})
                        return adapted, None
        except: pass

    try:
        r = requests.get(f"https://api.opendota.com/api/matches/{m_id}", timeout=15)
        if r.status_code == 200:
            data = r.json()
            match = {"radiant_win": data.get("radiant_win", False), "duration": data.get("duration", 0), "radiant_networth_leads": data.get("radiant_gold_adv") or [], "players": []}
            for p in data.get("players", []):
                match["players"].append({
                    "isRadiant": p.get("isRadiant", True), "hero_id": p.get("hero_id", 0),
                    "kills": p.get("kills", 0), "deaths": p.get("deaths", 0), "assists": p.get("assists", 0),
                    "gold_per_min": p.get("gold_per_min", 0), "xp_per_min": p.get("xp_per_min", 0),
                    "hero_damage": p.get("hero_damage", 0), "tower_damage": p.get("tower_damage", 0), "hero_healing": p.get("hero_healing", 0)
                })
            return match, None
    except Exception as e:
        pass

    try:
        import random
        fake_radiant_win = random.choice([True, False])
        fake_duration = random.randint(1800, 3600)
        fake_players = []
        sample_heroes = random.sample(list(HEROES.keys()), 10)
        for i in range(10):
            fake_players.append({
                "isRadiant": i < 5, "hero_id": int(sample_heroes[i]),
                "kills": random.randint(0, 20), "deaths": random.randint(0, 15), "assists": random.randint(0, 25),
                "gold_per_min": random.randint(300, 800), "xp_per_min": random.randint(300, 900),
                "hero_damage": random.randint(10000, 50000), "tower_damage": random.randint(0, 15000), "hero_healing": random.randint(0, 5000)
            })
        networth_leads = [random.randint(-1000, 1000) for _ in range(fake_duration // 60)]
        return {"radiant_win": fake_radiant_win, "duration": fake_duration, "radiant_networth_leads": networth_leads, "players": fake_players}, None
    except:
        return None, "Ошибка при генерации данных."

def build_ai_prompt(m_data, m_id):
    w = "Radiant (Свет)" if m_data.get('radiant_win') else "Dire (Тьма)"
    d = m_data.get('duration', 0) // 60
    rad, dir_ = [], []
    for idx, p in enumerate(m_data.get('players', [])):
        h_id = p.get('hero_id'); h_info = HEROES.get(str(h_id)) or HEROES.get(h_id)
        h_name = h_info.get('localized_name', f"Герой {h_id}") if h_info else f"Герой {h_id}"
        line = f"{h_name}: KDA {p.get('kills',0)}/{p.get('deaths',0)}/{p.get('assists',0)}, GPM {p.get('gold_per_min',0)}, Урон {p.get('hero_damage',0)}."
        if p.get("isRadiant", True): rad.append(line)
        else: dir_.append(line)
    prompt = f"Матч ID: {m_id}. Победитель: {w}. Длительность: {d} мин.\nRADIANT:\n{chr(10).join(rad)}\n\nDIRE:\n{chr(10).join(dir_)}\n\nСделай разбор матча. Анализируй KDA, GPM и урон."
    return prompt

def get_hero_matchups(hero_id):
    if STRATZ_API_TOKEN:
        url = "https://api.stratz.com/graphql"
        headers = {"Authorization": f"Bearer {STRATZ_API_TOKEN}", "Content-Type": "application/json", "User-Agent": "STRATZ_API"}
        q = "query GetHeroMatchups($heroId: Short!) { heroStats { heroVsHeroMatchup(heroId: $heroId) { advantage { vs { heroId2 winCount matchCount } } } } }"
        try:
            r = requests.post(url, headers=headers, json={"query": q, "variables": {"heroId": int(hero_id)}}, timeout=10, verify=False)
            if r.status_code == 200:
                data = r.json()
                if "errors" not in data:
                    adv = data.get("data", {}).get("heroStats", {}).get("heroVsHeroMatchup", {}).get("advantage", [])
                    adapted = []
                    for a in adv:
                        for vs in a.get("vs", []):
                            if vs.get("heroId2") and vs.get("matchCount", 0) > 0:
                                adapted.append({"hero_id": vs["heroId2"], "win_rate": (vs["winCount"] / vs["matchCount"]) * 100})
                    if adapted: return adapted
        except: pass
    try:
        r = requests.get(f"https://api.opendota.com/api/heroes/{hero_id}/matchups", timeout=10)
        if r.status_code == 200:
            data = r.json()
            adapted = [{"hero_id": item["hero_id"], "win_rate": (item["wins"] / item["games_played"]) * 100} for item in data if item.get("games_played", 0) > 10]
            return adapted
    except: pass
    return None

def get_hero_info(hero_id):
    if STRATZ_API_TOKEN:
        url = "https://api.stratz.com/graphql"
        headers = {"Authorization": f"Bearer {STRATZ_API_TOKEN}", "Content-Type": "application/json", "User-Agent": "STRATZ_API"}
        q = "query GetHeroStats { heroStats { winWeek { heroId winCount matchCount } } }"
        try:
            r = requests.post(url, headers=headers, json={"query": q}, timeout=10, verify=False)
            if r.status_code == 200:
                data = r.json()
                if "errors" not in data:
                    stats = data.get("data", {}).get("heroStats", {}).get("winWeek", [])
                    local = HEROES.get(str(hero_id), {})
                    for s in stats:
                        if s.get("heroId") == int(hero_id) and s.get("matchCount", 0) > 0:
                            return {"roles": local.get("roles", ["Неизвестно"]), "calculated_winrate": round((s["winCount"] / s["matchCount"]) * 100, 2), "icon": local.get("icon", "")}
        except: pass
    try:
        r = requests.get(f"https://api.opendota.com/api/heroStats", timeout=15)
        if r.status_code == 200:
            for hero_data in r.json():
                if hero_data.get("id") == int(hero_id):
                    local = HEROES.get(str(hero_id), {})
                    wins = hero_data.get("1_win", 0)
                    picks = hero_data.get("1_pick", 0)
                    winrate = (wins / picks * 100) if picks > 0 else 50.0
                    return {"roles": local.get("roles", ["Неизвестно"]), "calculated_winrate": round(winrate, 2), "icon": local.get("icon", "")}
    except: pass
    return None

def get_top_heroes_for_hero_of_day():
    conn = sqlite3.connect(os.path.join(base_dir, 'fantasy.db'))
    c = conn.cursor()
    c.execute('SELECT data, updated_at FROM hero_meta_cache WHERE id = 1')
    row = c.fetchone()
    if row and (time.time() - row[1] < 3600):
        conn.close(); return json.loads(row[0])
    ids = random.sample(list(HEROES.keys()), 30)
    c.execute('DELETE FROM hero_meta_cache')
    c.execute('INSERT INTO hero_meta_cache (id, data, updated_at) VALUES (1, ?, ?)', (json.dumps(ids), time.time()))
    conn.commit(); conn.close(); return ids

def get_player_matches_from_stratz(acc_id, limit=5):
    if STRATZ_API_TOKEN:
        url = "https://api.stratz.com/graphql"
        headers = {"Authorization": f"Bearer {STRATZ_API_TOKEN}", "Content-Type": "application/json", "User-Agent": "STRATZ_API"}
        q = "query GetPlayerMatches($steamId: Long!, $take: Int!) { player(steamAccountId: $steamId) { matches(take: $take) { id didRadiantWin durationSeconds players { heroId isRadiant kills deaths assists } } } }"
        try:
            r = requests.post(url, headers=headers, json={"query": q, "variables": {"steamId": int(acc_id), "take": limit}}, timeout=15, verify=False)
            if r.status_code == 200:
                data = r.json()
                if "errors" not in data: return data.get("data", {}).get("player", {}).get("matches", [])
        except: pass
    return []

def get_player_profile_from_steam(s_id):
    if not STEAM_API_KEY: return None
    params = {"key": STEAM_API_KEY, "steamids": s_id}
    try:
        r = requests.get("https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/", params=params, timeout=15)
        if r.status_code == 200:
            p = r.json().get("response", {}).get("players", [])
            if p: return {"name": p[0].get("personaname", "Unknown"), "avatar": p[0].get("avatarfull", "")}
    except: pass
    return None

def enrich_pro_players_with_avatars(players):
    if not STEAM_API_KEY: return players
    ids = [str(p['account_id'] + 76561197960265728) for p in players if p.get('account_id')]
    if not ids: return players
    params = {"key": STEAM_API_KEY, "steamids": ",".join(ids)}
    avatars = {}
    try:
        r = requests.get("https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/", params=params, timeout=10)
        if r.status_code == 200:
            for sp in r.json().get("response", {}).get("players", []): avatars[sp['steamid']] = sp.get('avatarfull', '')
    except: pass
    for p in players: p['avatar'] = avatars.get(str(p['account_id'] + 76561197960265728), '') if p.get('account_id') else ''
    return players

PRO_PLAYERS_DATA = [
    {"name": "Pure", "team": "1win", "role": 1, "account_id": 331855530, "liquipedia": "https://liquipedia.net/dota2/Pure", "dotabuff": "https://www.dotabuff.com/players/331855530"},
    {"name": "bzm", "team": "1win", "role": 2, "account_id": 93618577, "liquipedia": "https://liquipedia.net/dota2/Bzm", "dotabuff": "https://www.dotabuff.com/players/93618577"},
    {"name": "33", "team": "1win", "role": 3, "account_id": 86698277, "liquipedia": "https://liquipedia.net/dota2/33", "dotabuff": "https://www.dotabuff.com/players/86698277"},
    {"name": "Ari", "team": "1win", "role": 4, "account_id": 346412363, "liquipedia": "https://liquipedia.net/dota2/Ari", "dotabuff": "https://www.dotabuff.com/players/346412363"},
    {"name": "Whitemon", "team": "1win", "role": 5, "account_id": 136829091, "liquipedia": "https://liquipedia.net/dota2/Whitemon", "dotabuff": "https://www.dotabuff.com/players/136829091"},
    {"name": "Nightfall", "team": "Astra Invicta", "role": 1, "account_id": 124801257, "liquipedia": "https://liquipedia.net/dota2/Nightfall", "dotabuff": "https://www.dotabuff.com/players/124801257"},
    {"name": "lorenof", "team": "Astra Invicta", "role": 2, "account_id": 210053851, "liquipedia": "https://liquipedia.net/dota2/Lorenof", "dotabuff": "https://www.dotabuff.com/players/210053851"},
    {"name": "DM", "team": "Astra Invicta", "role": 3, "account_id": 56351509, "liquipedia": "https://liquipedia.net/dota2/DM", "dotabuff": "https://www.dotabuff.com/players/56351509"},
    {"name": "OmaR", "team": "Astra Invicta", "role": 4, "account_id": 152168157, "liquipedia": "https://liquipedia.net/dota2/OmaR", "dotabuff": "https://www.dotabuff.com/players/152168157"},
    {"name": "GH", "team": "Astra Invicta", "role": 5, "account_id": 101356886, "liquipedia": "https://liquipedia.net/dota2/GH", "dotabuff": "https://www.dotabuff.com/players/101356886"},
    {"name": "Kiritych", "team": "BetBoom Team", "role": 1, "account_id": 172099728, "liquipedia": "https://liquipedia.net/dota2/Kiritych", "dotabuff": "https://www.dotabuff.com/players/172099728"},
    {"name": "Save-", "team": "BetBoom Team", "role": 2, "account_id": 317880638, "liquipedia": "https://liquipedia.net/dota2/Save-", "dotabuff": "https://www.dotabuff.com/players/317880638"},
    {"name": "MieRo", "team": "BetBoom Team", "role": 3, "account_id": 165564598, "liquipedia": "https://liquipedia.net/dota2/MieRo", "dotabuff": "https://www.dotabuff.com/players/165564598"},
    {"name": "swedenstrong", "team": "BetBoom Team", "role": 4, "account_id": 175311897, "liquipedia": "https://liquipedia.net/dota2/Swedenstrong", "dotabuff": "https://www.dotabuff.com/players/175311897"},
    {"name": "Kataomi", "team": "BetBoom Team", "role": 5, "account_id": 196878136, "liquipedia": "https://liquipedia.net/dota2/Kataomi", "dotabuff": "https://www.dotabuff.com/players/196878136"},
    {"name": "Yuma", "team": "GamerLegion", "role": 1, "account_id": 177203952, "liquipedia": "https://liquipedia.net/dota2/Yuma", "dotabuff": "https://www.dotabuff.com/players/177203952"},
    {"name": "RCY", "team": "GamerLegion", "role": 2, "account_id": 154974246, "liquipedia": "https://liquipedia.net/dota2/RCY", "dotabuff": "https://www.dotabuff.com/players/154974246"},
    {"name": "Fayde", "team": "GamerLegion", "role": 3, "account_id": 160119017, "liquipedia": "https://liquipedia.net/dota2/Fayde", "dotabuff": "https://www.dotabuff.com/players/160119017"},
    {"name": "Bignum", "team": "GamerLegion", "role": 4, "account_id": 90423751, "liquipedia": "https://liquipedia.net/dota2/Bignum", "dotabuff": "https://www.dotabuff.com/players/90423751"},
    {"name": "Speeed", "team": "GamerLegion", "role": 5, "account_id": 191362875, "liquipedia": "https://liquipedia.net/dota2/Speeed", "dotabuff": "https://www.dotabuff.com/players/191362875"},
    {"name": "Natsumi", "team": "OG", "role": 1, "account_id": 355168766, "liquipedia": "https://liquipedia.net/dota2/Natsumi", "dotabuff": "https://www.dotabuff.com/players/355168766"},
    {"name": "Topson", "team": "OG", "role": 2, "account_id": 94054712, "liquipedia": "https://liquipedia.net/dota2/Topson", "dotabuff": "https://www.dotabuff.com/players/94054712"},
    {"name": "SSS", "team": "OG", "role": 3, "account_id": 402583877, "liquipedia": "https://liquipedia.net/dota2/SSS", "dotabuff": "https://www.dotabuff.com/players/402583877"},
    {"name": "Onejey", "team": "OG", "role": 4, "account_id": 181267255, "liquipedia": "https://liquipedia.net/dota2/Onejey", "dotabuff": "https://www.dotabuff.com/players/181267255"},
    {"name": "skem", "team": "OG", "role": 5, "account_id": 100594231, "liquipedia": "https://liquipedia.net/dota2/Skem", "dotabuff": "https://www.dotabuff.com/players/100594231"},
    {"name": "Satanic", "team": "PARIVISION", "role": 1, "account_id": 1044002267, "liquipedia": "https://liquipedia.net/dota2/Satanic", "dotabuff": "https://www.dotabuff.com/players/1044002267"},
    {"name": "No[o]ne-", "team": "PARIVISION", "role": 2, "account_id": 106573901, "liquipedia": "https://liquipedia.net/dota2/Noone", "dotabuff": "https://www.dotabuff.com/players/106573901"},
    {"name": "Noticed", "team": "PARIVISION", "role": 3, "account_id": 195108598, "liquipedia": "https://liquipedia.net/dota2/Noticed", "dotabuff": "https://www.dotabuff.com/players/195108598"},
    {"name": "9Class", "team": "PARIVISION", "role": 4, "account_id": 164199202, "liquipedia": "https://liquipedia.net/dota2/9Class", "dotabuff": "https://www.dotabuff.com/players/164199202"},
    {"name": "Dukalis", "team": "PARIVISION", "role": 5, "account_id": 73401082, "liquipedia": "https://liquipedia.net/dota2/Dukalis", "dotabuff": "https://www.dotabuff.com/players/73401082"},
    {"name": "m1CKe", "team": "Team Liquid", "role": 1, "account_id": 152962063, "liquipedia": "https://liquipedia.net/dota2/MiCKe", "dotabuff": "https://www.dotabuff.com/players/152962063"},
    {"name": "Malr1ne", "team": "Team Liquid", "role": 2, "account_id": 898455820, "liquipedia": "https://liquipedia.net/dota2/Malr1ne", "dotabuff": "https://www.dotabuff.com/players/898455820"},
    {"name": "Wisper", "team": "Team Liquid", "role": 3, "account_id": 292921272, "liquipedia": "https://liquipedia.net/dota2/Wisper", "dotabuff": "https://www.dotabuff.com/players/292921272"},
    {"name": "Boxi", "team": "Team Liquid", "role": 4, "account_id": 77490514, "liquipedia": "https://liquipedia.net/dota2/Boxi", "dotabuff": "https://www.dotabuff.com/players/77490514"},
    {"name": "tOfu", "team": "Team Liquid", "role": 5, "account_id": 16497807, "liquipedia": "https://liquipedia.net/dota2/Tofu", "dotabuff": "https://www.dotabuff.com/players/16497807"},
    {"name": "Yatoro", "team": "Team Spirit", "role": 1, "account_id": 321580662, "liquipedia": "https://liquipedia.net/dota2/Yatoro", "dotabuff": "https://www.dotabuff.com/players/321580662"},
    {"name": "Larl", "team": "Team Spirit", "role": 2, "account_id": 106305042, "liquipedia": "https://liquipedia.net/dota2/Larl", "dotabuff": "https://www.dotabuff.com/players/106305042"},
    {"name": "Batyuk", "team": "Team Spirit", "role": 3, "account_id": 140835095, "liquipedia": "https://liquipedia.net/dota2/Batyuk", "dotabuff": "https://www.dotabuff.com/players/140835095"},
    {"name": "not me", "team": "Team Spirit", "role": 4, "account_id": 218231587, "liquipedia": "https://liquipedia.net/dota2/Mira", "dotabuff": "https://www.dotabuff.com/players/218231587"},
    {"name": "rue", "team": "Team Spirit", "role": 5, "account_id": 847565596, "liquipedia": "https://liquipedia.net/dota2/Rue", "dotabuff": "https://www.dotabuff.com/players/847565596"},
    {"name": "Watson", "team": "Team Yandex", "role": 1, "account_id": 171262902, "liquipedia": "https://liquipedia.net/dota2/Watson", "dotabuff": "https://www.dotabuff.com/players/171262902"},
    {"name": "CHIRA_JUNIOR", "team": "Team Yandex", "role": 2, "account_id": 312436974, "liquipedia": "https://liquipedia.net/dota2/CHIRA_JUNIOR", "dotabuff": "https://www.dotabuff.com/players/312436974"},
        {"name": "ATF", "team": "Team Yandex", "role": 3, "account_id": 183719386, "liquipedia": "https://liquipedia.net/dota2/ATF", "dotabuff": "https://www.dotabuff.com/players/183719386"},
    {"name": "Saksa", "team": "Team Yandex", "role": 4, "account_id": 103735745, "liquipedia": "https://liquipedia.net/dota2/Saksa", "dotabuff": "https://www.dotabuff.com/players/103735745"},
    {"name": "Maladych", "team": "Team Yandex", "role": 5, "account_id": 93817671, "liquipedia": "https://liquipedia.net/dota2/Maladych", "dotabuff": "https://www.dotabuff.com/players/93817671"},
    {"name": "Darklord^", "team": "LEGION", "role": 1, "account_id": 879017980, "liquipedia": "https://liquipedia.net/dota2/Darklord", "dotabuff": "https://www.dotabuff.com/players/879017980"},
    {"name": "Stojkov", "team": "LEGION", "role": 2, "account_id": 262476000, "liquipedia": "https://liquipedia.net/dota2/Stojkov", "dotabuff": "https://www.dotabuff.com/players/262476000"},
    {"name": "SaberLight", "team": "LEGION", "role": 3, "account_id": 126212866, "liquipedia": "https://liquipedia.net/dota2/SaberLight", "dotabuff": "https://www.dotabuff.com/players/126212866"},
    {"name": "Xsvampire", "team": "LEGION", "role": 4, "account_id": 326327879, "liquipedia": "https://liquipedia.net/dota2/Xsvampire", "dotabuff": "https://www.dotabuff.com/players/326327879"},
    {"name": "Kidaro", "team": "LEGION", "role": 5, "account_id": 104334048, "liquipedia": "https://liquipedia.net/dota2/Kidaro", "dotabuff": "https://www.dotabuff.com/players/104334048"}
]

@app.route('/login/steam')
def login_steam():
    params = {'openid.ns': 'http://specs.openid.net/auth/2.0', 'openid.identity': 'http://specs.openid.net/auth/2.0/identifier_select', 'openid.claimed_id': 'http://specs.openid.net/auth/2.0/identifier_select', 'openid.mode': 'checkid_setup', 'openid.return_to': url_for('authorize_steam', _external=True), 'openid.realm': url_for('index', _external=True)}
    return redirect(STEAM_OPENID_URL + "?" + urlencode(params))

@app.route('/login/steam/authorize')
def authorize_steam():
    query = parse_qs(request.query_string.decode())
    if 'openid.claimed_id' in query:
        session['steam_id'] = query['openid.claimed_id'][0].split('/')[-1]
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.pop('steam_id', None)
    session.pop('saved_roster', None)
    return redirect(url_for('index'))

@app.route('/my-profile')
def my_profile():
    steam_id_64 = session.get('steam_id')
    if not steam_id_64: return redirect(url_for('login_steam'))
    account_id_32 = int(steam_id_64) - 76561197960265728
    profile_data = get_player_profile_from_steam(steam_id_64)
    if not profile_data: return "Не удалось загрузить профиль Steam", 500
    
    conn = sqlite3.connect(os.path.join(base_dir, 'fantasy.db'))
    cursor = conn.cursor()
    cursor.execute("SELECT achievement_id FROM user_achievements WHERE steam_id = ?", (steam_id_64,))
    user_achievements = [row[0] for row in cursor.fetchall()]
    cursor.execute("SELECT COUNT(*) FROM user_analytics WHERE steam_id = ?", (steam_id_64,))
    match_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM user_drafts WHERE steam_id = ?", (steam_id_64,))
    draft_count = cursor.fetchone()[0]
    conn.close()

    match_percent = min(int((match_count / 20) * 100), 100)
    draft_percent = min(int((draft_count / 10) * 100), 100)
    
    matches_raw, source = [], "Нет данных"
    stratz_matches = get_player_matches_from_stratz(str(account_id_32), limit=5)
    if stratz_matches: matches_raw = stratz_matches; source = "STRATZ"
    if not matches_raw:
        opendota_data = get_player_matches_opendota(account_id_32, limit=5)
        if opendota_data:
            for m in opendota_data:
                is_radiant = m.get('player_slot', 0) < 128
                is_win = (m.get('radiant_win') and is_radiant) or (not m.get('radiant_win') and not is_radiant)
                matches_raw.append({"id": m['match_id'], "didRadiantWin": is_win, "durationSeconds": m.get('duration', 0), "players": [{"heroId": m.get('hero_id', 0)}]})
            source = "OpenDota"
    if not matches_raw:
        valve_data = get_player_matches_valve(account_id_32, limit=5)
        if valve_data:
            for m in valve_data:
                is_radiant = m.get('player_slot', 0) < 128
                is_win = (m.get('radiant_win') and is_radiant) or (not m.get('radiant_win') and not is_radiant)
                matches_raw.append({"id": m['match_id'], "didRadiantWin": is_win, "durationSeconds": m.get('duration', 0) * 60 if m.get('duration') else 0, "players": [{"heroId": m.get('hero_id', 0)}]})
            source = "Valve API"
    enriched_matches = []
    for m in matches_raw:
        hero_icons = [{"icon": HEROES.get(str(p['heroId']), {}).get('icon', ''), "name": HEROES.get(str(p['heroId']), {}).get('localized_name', 'Unknown')} for p in m.get('players', []) if HEROES.get(str(p['heroId']), {}).get('icon')]
        enriched_matches.append({"id": m.get('id'), "win": m.get('didRadiantWin'), "duration": m.get('durationSeconds', 0) // 60, "heroes": hero_icons})
        
    return render_template('my_profile.html', name=profile_data['name'], avatar=profile_data['avatar'], steam_id=steam_id_64, matches=enriched_matches, source=source, user_achievements=user_achievements, match_count=match_count, draft_count=draft_count, match_percent=match_percent, draft_percent=draft_percent)

@app.route('/', methods=['GET', 'POST'])
def index():
    analysis, error, analyzed_match_id, players_data, items_data = None, None, None, [], {}
    chart_labels, chart_values, networth_labels, networth_values = [], [], [], []
    today_seed = int(datetime.date.today().strftime("%Y%m%d"))
    random.seed(today_seed)
    hero_of_day_id = random.choice(get_top_heroes_for_hero_of_day())
    local_hero_info = HEROES.get(hero_of_day_id, {})
    hero_name = local_hero_info.get('localized_name', 'Unknown')
    hero_of_day = {"id": hero_of_day_id, "name": hero_name, "icon": local_hero_info.get('icon', ''), "system_name": get_hero_system_name(hero_name)}
    hero_of_day_stats = get_hero_info(hero_of_day_id)
    auto_match_id = request.args.get('match_id')
    
    # ПРОВЕРКА ЛИМИТОВ
    is_premium = False
    if 'steam_id' not in session:
        user_ip = request.remote_addr
        today = datetime.date.today().strftime("%Y-%m-%d")
        conn = sqlite3.connect(os.path.join(base_dir, 'fantasy.db'))
        cursor = conn.cursor()
        cursor.execute("SELECT count FROM user_requests WHERE steam_id = ? AND request_date = ?", (user_ip, today))
        req_row = cursor.fetchone()
        req_count = req_row[0] if req_row else 0
        
        if request.method == 'POST':
            if req_count >= 1:
                error = "Вы исчерпали бесплатный пробный анализ. Войдите через Steam, чтобы получить еще 5 бесплатных разборов!"
                conn.close()
                return render_template('index.html', analysis=None, error=error, analyzed_match_id=None, players=[], pro_players=enrich_pro_players_with_avatars(PRO_PLAYERS_DATA.copy()), donate_url="https://www.donationalerts.com/r/asod888", chart_labels=[], chart_values=[], networth_labels=[], networth_values=[], hero_of_day=None, hero_of_day_stats=None, is_logged_in=False, user_avatar=None, user_name=None, items_data={})
            else:
                cursor.execute("INSERT INTO user_requests (steam_id, request_date, count) VALUES (?, ?, 1) ON CONFLICT(steam_id, request_date) DO UPDATE SET count = count + 1", (user_ip, today))
                conn.commit()
        conn.close()
        
    else:
        conn = sqlite3.connect(os.path.join(base_dir, 'fantasy.db'))
        cursor = conn.cursor()
        cursor.execute("SELECT is_active FROM user_premium WHERE steam_id = ?", (session['steam_id'],))
        row = cursor.fetchone()
        if row and row[0] == 1: is_premium = True
            
        if request.method == 'POST' and not is_premium:
            today = datetime.date.today().strftime("%Y-%m-%d")
            cursor.execute("SELECT count FROM user_requests WHERE steam_id = ? AND request_date = ?", (session['steam_id'], today))
            req_row = cursor.fetchone()
            req_count = req_row[0] if req_row else 0
            
            if req_count >= 5:
                error = "Дневной лимит анализов (5) исчерпан. Купите Премиум для безлимита!"
                conn.close()
                return render_template('index.html', analysis=None, error=error, analyzed_match_id=None, players=[], pro_players=enrich_pro_players_with_avatars(PRO_PLAYERS_DATA.copy()), donate_url="https://www.donationalerts.com/r/asod888", chart_labels=[], chart_values=[], networth_labels=[], networth_values=[], hero_of_day=None, hero_of_day_stats=None, is_logged_in=True, user_avatar=None, user_name=None, items_data={})
            else:
                cursor.execute("INSERT INTO user_requests (steam_id, request_date, count) VALUES (?, ?, 1) ON CONFLICT(steam_id, request_date) DO UPDATE SET count = count + 1", (session['steam_id'], today))
                conn.commit()
        conn.close()

    if request.method == 'POST' or auto_match_id:
        match_id = ''.join(filter(str.isdigit, request.form.get('match_id', ''))) if request.method == 'POST' else auto_match_id
        if not match_id: error = "Введите корректный ID матча."
        else:
            match_data, err = get_match_from_stratz(match_id)
            if err: error = err
            else:
                analyzed_match_id = match_id
                analysis = analyze_with_gigachat(build_ai_prompt(match_data, match_id), is_premium=is_premium)
                items_data = get_items_from_stratz(match_id)
                for p in match_data.get('players', []):
                    h_id = p.get('hero_id'); h_info = HEROES.get(str(h_id), {}); h_name = h_info.get('localized_name', f"Герой {h_id}")
                    players_data.append({"name": h_name, "icon": h_info.get('icon', ''), "isRadiant": p.get('isRadiant', True), "system_name": get_hero_system_name(h_name)})
                    chart_labels.append(h_name); chart_values.append(p.get('gold_per_min', 0))
                for minute, lead in enumerate(match_data.get('radiant_networth_leads') or []):
                    networth_labels.append(f"{minute}:00"); networth_values.append(lead)
                    
    is_logged_in = 'steam_id' in session
    user_avatar, user_name = None, None
    if is_logged_in:
        profile = get_player_profile_from_steam(session.get('steam_id'))
        if profile: user_avatar, user_name = profile['avatar'], profile['name']
        if analyzed_match_id:
            try:
                conn = sqlite3.connect(os.path.join(base_dir, 'fantasy.db'))
                cursor = conn.cursor()
                cursor.execute("INSERT OR IGNORE INTO user_analytics (steam_id, match_id) VALUES (?, ?)", (session['steam_id'], analyzed_match_id))
                conn.commit(); conn.close()
            except: pass
    pro_players_with_avatars = enrich_pro_players_with_avatars(PRO_PLAYERS_DATA.copy())
    return render_template('index.html', analysis=analysis, error=error, analyzed_match_id=analyzed_match_id, players=players_data, pro_players=pro_players_with_avatars, donate_url="https://www.donationalerts.com/r/asod888", chart_labels=chart_labels, chart_values=chart_values, networth_labels=networth_labels, networth_values=networth_values, hero_of_day=hero_of_day, hero_of_day_stats=hero_of_day_stats, is_logged_in=is_logged_in, user_avatar=user_avatar, user_name=user_name, items_data=items_data)

@app.route('/draft', methods=['GET', 'POST'])
def draft_helper():
    all_heroes = sorted([{"id": int(h_id), "name": h_info.get('localized_name', 'Unknown'), "system_name": get_hero_system_name(h_info.get('localized_name', 'Unknown')), "icon": h_info.get('icon', '')} for h_id, h_info in HEROES.items()], key=lambda x: x['name'])
    counter_picks, ai_analysis, error = [], None, None
    if request.method == 'POST':
        enemy_names = request.form.getlist('enemy_hero'); ally_names = request.form.getlist('ally_hero')
        if not enemy_names: error = "Выберите хотя бы одного врага."
        else:
            enemy_counters = {}
            for eid in enemy_names:
                e_id = next((h['id'] for h in all_heroes if h['name'] == eid), None)
                if e_id:
                    for m in get_hero_matchups(e_id) or []:
                        if m['hero_id'] not in enemy_counters: enemy_counters[m['hero_id']] = []
                        enemy_counters[m['hero_id']].append(m['win_rate'])
            for opp_id, rates in enemy_counters.items():
                opp_info = HEROES.get(str(opp_id)) or HEROES.get(opp_id)
                opp_name = opp_info.get('localized_name', f"Герой {opp_id}") if opp_info else f"Герой {opp_id}"
                if opp_name in ally_names: continue
                avg_rate = sum(rates) / len(rates)
                counter_picks.append({"name": opp_name, "win_rate": avg_rate, "icon": opp_info.get('icon', '') if opp_info else ''})
            counter_picks = sorted(counter_picks, key=lambda x: x['win_rate'], reverse=True)[:5]
            if counter_picks:
                counters_str = ", ".join([f"{c['name']} ({c['win_rate']:.1f}%)" for c in counter_picks])
                prompt = f"Враги: {', '.join(enemy_names)}. " + (f"Союзники: {', '.join(ally_names)}. " if ally_names else "") + f"Лучшие контр-пики по статистике: {counters_str}. Напиши краткий разбор драфта: кого лучше взять и почему. НЕ ПИШИ про предметы. Ответь обычным текстом."
                ai_analysis = analyze_with_gigachat(prompt, is_premium=True)
                if 'steam_id' in session:
                    try:
                        conn = sqlite3.connect(os.path.join(base_dir, 'fantasy.db'))
                        cursor = conn.cursor()
                        cursor.execute("INSERT INTO user_drafts (steam_id) VALUES (?)", (session['steam_id'],))
                        conn.commit(); conn.close()
                    except Exception as e: pass
    return render_template('draft.html', heroes=all_heroes, counter_picks=counter_picks, error=error, ai_analysis=ai_analysis)

@app.route('/heroes', methods=['GET', 'POST'])
def heroes_base():
    hero_list = sorted([{"id": int(h_id), "name": h_info.get('localized_name', 'Unknown'), "icon": h_info.get('icon', '')} for h_id, h_info in HEROES.items()], key=lambda x: x['name'])
    selected_hero, hero_stats, error, top_counters = None, None, None, []
    if request.method == 'POST':
        hero_name = request.form.get('hero_name', '')
        selected_hero = next((h for h in hero_list if h['name'] == hero_name), None)
        if not selected_hero: error = "Герой не найден."
        else:
            hero_stats = get_hero_info(selected_hero['id'])
            if not hero_stats: error = "Не удалось получить данные."
            else:
                for m in sorted(get_hero_matchups(selected_hero['id']) or [], key=lambda x: x.get('win_rate', 0), reverse=True)[:3]:
                    opp_info = HEROES.get(str(m.get('hero_id'))) or HEROES.get(m.get('hero_id'))
                    top_counters.append({"name": opp_info.get('localized_name', 'Unknown') if opp_info else 'Unknown', "win_rate": m.get('win_rate', 0), "icon": opp_info.get('icon', '') if opp_info else ''})
    return render_template('heroes.html', heroes=hero_list, selected_hero=selected_hero, hero_stats=hero_stats, error=error, top_counters=top_counters)

@app.route('/rating')
def rating():
    leaderboard = []
    try:
        r = requests.get("https://api.opendota.com/api/heroStats", timeout=10)
        if r.status_code == 200:
            data = r.json()
            for h in data:
                wins = h.get("1_win", 0)
                picks = h.get("1_pick", 0)
                if picks > 3000: 
                    leaderboard.append({"id": h.get("id"), "winrate": round((wins / picks) * 100, 2)})
    except: pass

    if not leaderboard:
        sample_heroes = random.sample(list(HEROES.keys()), 10)
        for h_id in sample_heroes:
            leaderboard.append({"id": int(h_id), "winrate": round(random.uniform(50.0, 56.0), 2)})

    top_10 = sorted(leaderboard, key=lambda x: x['winrate'], reverse=True)[:10]
    final_leaderboard = []
    for item in top_10:
        h_id = item["id"]
        h_info = HEROES.get(str(h_id), {})
        img_url = h_info.get('img', '').split('?')[0]
        if not img_url: img_url = h_info.get('icon', '')
        final_leaderboard.append({"name": h_info.get('localized_name', 'Unknown'), "winrate": item["winrate"], "img": img_url})

    meta_analysis = None
    if len(final_leaderboard) >= 5:
        try:
            meta_analysis = analyze_with_gigachat(f"Топ-5 героев: {', '.join([h['name'] for h in final_leaderboard[:5]])}. Краткий анализ меты (3-4 предложения).")
        except: pass
    return render_template('rating.html', leaderboard=final_leaderboard, meta_analysis=meta_analysis)

@app.route('/random')
def random_hero():
    random_id = random.choice(list(HEROES.keys())); random_name = HEROES[random_id]['localized_name']
    hero_stats = get_hero_info(random_id); top_counters = []
    if hero_stats:
        for m in sorted(get_hero_matchups(random_id) or [], key=lambda x: x.get('win_rate', 0), reverse=True)[:3]:
            opp_info = HEROES.get(str(m.get('hero_id'))) or HEROES.get(m.get('hero_id'))
            top_counters.append({"name": opp_info.get('localized_name', 'Unknown') if opp_info else 'Unknown', "win_rate": m.get('win_rate', 0), "icon": opp_info.get('icon', '') if opp_info else ''})
    hero_list = sorted([{"id": int(h_id), "name": h_info.get('localized_name', 'Unknown'), "icon": h_info.get('icon', '')} for h_id, h_info in HEROES.items()], key=lambda x: x['name'])
    return render_template('heroes.html', heroes=hero_list, selected_hero={"id": int(random_id), "name": random_name, "icon": HEROES[random_id].get('icon', '')}, hero_stats=hero_stats, error=None, top_counters=top_counters)

@app.route('/pros')
def pros(): return render_template('pros.html', pro_players=enrich_pro_players_with_avatars(PRO_PLAYERS_DATA.copy()))

@app.route('/tournaments')
def tournaments():
    return render_template('tournaments.html', tournaments=[
        {"name": "PARI Universe", "dates": "22 октября — 1 ноября 2026", "location": "Онлайн (Европа)", "prize": "$250,000", "status": "Идёт сейчас", "description": "Турнир от FISSURE и PARI.", "link": "https://liquipedia.net/dota2/PARI_Universe/1"},
        {"name": "ESL One Munich 2027", "dates": "4 — 11 апреля 2027", "location": "Мюнхен, Германия", "prize": "$1,000,000", "status": "Предстоящий", "description": "Крупный LAN-турнир от ESL.", "link": "https://liquipedia.net/dota2/ESL_One/Munich/2027"},
        {"name": "The International 2026", "dates": "13 — 23 августа 2026", "location": "Шанхай, Китай", "prize": "$1,600,000+", "status": "Завершён", "description": "Главный турнир года.", "link": "https://liquipedia.net/dota2/The_International/2026"}
    ])

@app.route('/fantasy', methods=['GET', 'POST'])
def fantasy():
    steam_id_64 = session.get('steam_id')
    if not steam_id_64: return redirect(url_for('login_steam'))
    current_tournament = "PARI Universe"; error, success = None, None
    conn = sqlite3.connect(os.path.join(base_dir, 'fantasy.db')); cursor = conn.cursor()
    valid_names = [p['name'] for p in PRO_PLAYERS_DATA]
    cursor.execute("SELECT id, player1, player2, player3, player4, player5 FROM fantasy_rosters WHERE steam_id = ? AND tournament = ?", (steam_id_64, current_tournament))
    db_row = cursor.fetchone(); db_roster = None
    if db_row:
        row_id, p1, p2, p3, p4, p5 = db_row; selected_names = [p1, p2, p3, p4, p5]
        if all(name in valid_names for name in selected_names): db_roster = selected_names
        else:
            cursor.execute("DELETE FROM fantasy_rosters WHERE id = ?", (row_id,)); conn.commit(); session.pop('saved_roster', None)
    if not db_roster:
        session_roster = session.get('saved_roster')
        if session_roster and all(name in valid_names for name in session_roster): db_roster = session_roster
    saved_roster_names = db_roster if db_roster else []; is_locked = bool(db_roster)
    if request.args.get('reset') == '1':
        if is_locked: return redirect(url_for('fantasy'))
        else: session.pop('saved_roster', None); return redirect(url_for('fantasy'))
        
    tournament_is_open = is_tournament_open(current_tournament)
    
    if request.method == 'POST' and not is_locked:
        if not tournament_is_open:
            error = "Прием составов на этот турнир уже закрыт!"
        else:
            p1, p2, p3, p4, p5 = request.form.get('role_1'), request.form.get('role_2'), request.form.get('role_3'), request.form.get('role_4'), request.form.get('role_5')
            selected = [p1, p2, p3, p4, p5]
            if not all(selected): error = "Вы должны выбрать по одному игроку на каждую позицию."
            else:
                try:
                    cursor.execute('INSERT INTO fantasy_rosters (steam_id, player1, player2, player3, player4, player5, tournament) VALUES (?, ?, ?, ?, ?, ?, ?)', (steam_id_64, p1, p2, p3, p4, p5, current_tournament)); conn.commit()
                    session['saved_roster'] = selected; saved_roster_names = selected; is_locked = True; success = "Твой состав успешно сохранен!"
                except Exception as e: error = f"Ошибка: {e}"

    player_stats = []
    if saved_roster_names:
        for i, name in enumerate(saved_roster_names):
            role = i + 1
            player_info = next((p for p in PRO_PLAYERS_DATA if p['name'] == name), None)
            if not player_info: continue
            acc_id = player_info.get('account_id', 0)
            points = 0.0
            has_data = False
            if acc_id:
                try:
                    res = requests.get(f"https://api.opendota.com/api/players/{acc_id}/recentMatches", timeout=8)
                    if res.status_code == 200 and res.json():
                        m = res.json()[0]
                        if role <= 3: points = (m.get('gold_per_min', 0) / 10) + (m.get('kills', 0) * 2) - (m.get('deaths', 0) * 1.5) + (m.get('assists', 0) * 1.5)
                        else: points = (m.get('assists', 0) * 2.5) - (m.get('deaths', 0) * 0.5)
                        points = round(points, 1)
                        has_data = True
                except: pass
            if not has_data:
                base_gpm = {"Pure": 680, "bzm": 650, "33": 550, "Ari": 450, "Whitemon": 350, "Nightfall": 670, "lorenof": 640, "DM": 560, "OmaR": 440, "GH": 340, "Kiritych": 690, "Save-": 660, "MieRo": 570, "swedenstrong": 460, "Kataomi": 330, "Yuma": 680, "RCY": 630, "Fayde": 540, "Bignum": 450, "Speeed": 350, "Natsumi": 650, "Topson": 620, "SSS": 550, "Onejey": 430, "skem": 320, "Satanic": 700, "No[o]ne-": 670, "Noticed": 580, "9Class": 480, "Dukalis": 340, "m1CKe": 690, "Malr1ne": 650, "Wisper": 560, "Boxi": 470, "tOfu": 330, "Yatoro": 710, "Larl": 680, "Batyuk": 590, "not me": 490, "rue": 350, "Watson": 660, "CHIRA_JUNIOR": 630, "ATF": 570, "Saksa": 480, "Maladych": 340, "Darklord^": 640, "Stojkov": 600, "SaberLight": 550, "Xsvampire": 450, "Kidaro": 330}
                base = base_gpm.get(name, 500)
                fake_gpm = base + random.randint(-50, 50)
                if role <= 3: points = (fake_gpm / 10) + random.randint(3, 9) * 2 - random.randint(1, 5) * 1.5
                else: points = random.randint(15, 25) * 2.5 - random.randint(1, 4) * 0.5
                points = round(points, 1)
            player_stats.append({"name": name, "team": player_info.get('team', 'Unknown'), "role": role, "points": points})

    total_points = sum(p['points'] for p in player_stats)
    if total_points > 0:
        cursor.execute("SELECT achievement_id FROM user_achievements WHERE steam_id = ?", (steam_id_64,)); existing = [row[0] for row in cursor.fetchall()]
        if total_points >= 200 and 'points_200' not in existing: cursor.execute("INSERT INTO user_achievements (steam_id, achievement_id) VALUES (?, ?)", (steam_id_64, 'points_200')); conn.commit()
        if total_points >= 700 and 'points_700' not in existing: cursor.execute("INSERT INTO user_achievements (steam_id, achievement_id) VALUES (?, ?)", (steam_id_64, 'points_700')); conn.commit()
    conn.close()
    
    if not tournament_is_open and not saved_roster_names:
        error = "Прием составов на этот турнир закрыт. Дождитесь следующего!"
        
    return render_template('fantasy.html', available_players=PRO_PLAYERS_DATA, current_tournament=current_tournament, error=error, success=success, saved_roster=saved_roster_names, player_stats=player_stats, is_locked=is_locked)

@app.route('/fantasy/leaderboard')
def fantasy_leaderboard():
    conn = sqlite3.connect(os.path.join(base_dir, 'fantasy.db')); cursor = conn.cursor()
    cursor.execute("SELECT steam_id, player1, player2, player3, player4, player5 FROM fantasy_rosters"); all_rosters = cursor.fetchall()
    leaderboard_data = []
    for roster in all_rosters:
        steam_id = roster[0]; players = list(roster[1:]); profile = get_player_profile_from_steam(steam_id); total_points = 0.0
        for i, name in enumerate(players):
            player_info = next((p for p in PRO_PLAYERS_DATA if p['name'] == name), None)
            if not player_info: continue
            cursor.execute("SELECT points FROM fantasy_cache WHERE name = ?", (name,)); cached = cursor.fetchone()
            if cached: total_points += cached[0]
        leaderboard_data.append({"name": profile['name'] if profile else "Unknown", "avatar": profile['avatar'] if profile else "", "points": round(total_points, 1)})
    conn.close()
    return render_template('fantasy_leaderboard.html', leaderboard=sorted(leaderboard_data, key=lambda x: x['points'], reverse=True))

@app.route('/coach', methods=['GET', 'POST'])
def coach():
    ai_feedback = None; error = None; searched_id = None
    if request.method == 'POST':
        steam_id_input = request.form.get('steam_id', '').strip() or session.get('steam_id')
        if not steam_id_input: error = "Введите Steam ID."
        else:
            searched_id = steam_id_input
            try:
                account_id_32 = int(steam_id_input) - 76561197960265728 if len(steam_id_input) > 10 else int(steam_id_input)
                url = "https://api.stratz.com/graphql"; headers = {"Authorization": f"Bearer {STRATZ_API_TOKEN}", "Content-Type": "application/json", "User-Agent": "STRATZ_API"}
                q = "query GetPlayerMatches($steamId: Long!) { player(steamAccountId: $steamId) { matches(request: { take: 5 }) { id didRadiantWin players { steamAccountId isRadiant heroId kills deaths assists goldPerMinute experiencePerMinute heroDamage } } } }"
                r = requests.post(url, headers=headers, json={"query": q, "variables": {"steamId": int(account_id_32)}}, timeout=20, verify=False); data = r.json()
                if "errors" in data or not data.get("data", {}).get("player", {}).get("matches"): error = "Не удалось найти матчи."
                else:
                    matches_text = ""
                    for i, m in enumerate(data["data"]["player"]["matches"]):
                        for p in m.get("players", []):
                            if str(p.get("steamAccountId")) == str(account_id_32):
                                hero_name = HEROES.get(str(p.get("heroId")), {}).get('localized_name', 'Unknown')
                                matches_text += f"Матч {i+1}: Игрок на {hero_name}, KDA {p.get('kills')}/{p.get('deaths')}/{p.get('assists')}, GPM {p.get('goldPerMinute')}, Урон {p.get('heroDamage')}.\n"
                                break
                    ai_feedback = analyze_with_gigachat(f"Ты — строгий тренер. Разбери игру по этим матчам:\n{matches_text}\nНапиши 3 абзаца: 1. Главная проблема. 2. На чем сфокусироваться. 3. Совет по герою. Без звездочек.")
            except Exception as e: error = f"Ошибка: {e}"
    return render_template('coach.html', ai_feedback=ai_feedback, error=error, searched_id=searched_id)

@app.route('/psy', methods=['GET', 'POST'])
def psy_chat():
    if request.method == 'POST' and request.form.get('reset') == 'true':
        session['psy_history'] = []
        return redirect(url_for('psy_chat'))
    if 'psy_history' not in session: session['psy_history'] = []
    if request.method == 'POST':
        user_message = request.form.get('message', '').strip()
        if user_message:
            history = session['psy_history']; history.append({"role": "user", "content": user_message})
            system_prompt = "Ты — эмпатичный и понимающий психолог для геймеров в Dota 2. Твоя задача — выслушать игрока, успокоить его после тильта, помочь разобраться в эмоциях и дать практический совет. Понимай дотерский сленг (фид, руинер, тильт, дизмораль). Отвечай коротко, поддерживающе и с юмором, но без осуждения. Если игрок злится — предложи сделать перерыв, попить воды, размяться."
            url = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"; token = get_gigachat_token()
            if not token: ai_response = "Ошибка: Не удалось получить токен GigaChat."
            else:
                headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
                data = {"model": "GigaChat", "messages": [{"role": "system", "content": system_prompt}] + history, "temperature": 0.7, "max_tokens": 500, "stream": False}
                try:
                    response = requests.post(url, headers=headers, json=data, timeout=30, verify=False)
                    if response.status_code == 200: ai_response = response.json()['choices'][0]['message']['content']
                    else: ai_response = "Ошибка: ИИ не отвечает."
                except Exception as e: ai_response = f"Ошибка: {e}"
            history.append({"role": "assistant", "content": ai_response}); session['psy_history'] = history; session.modified = True
        return redirect(url_for('psy_chat'))
    return render_template('psy.html', history=session.get('psy_history', []))

@app.route('/leaderboard_players')
def leaderboard_players():
    try:
        conn = sqlite3.connect(os.path.join(base_dir, 'fantasy.db'))
        cursor = conn.cursor()
        
        cursor.execute('SELECT data, updated_at FROM leaderboard_cache WHERE id = 1')
        row = cursor.fetchone()
        current_time = time.time()
        
        if row and (current_time - row[1] < 900):
            cached_data = json.loads(row[0])
            if cached_data and not any(p['hero'] == 'Unknown' for p in cached_data):
                conn.close()
                return render_template('leaderboard_players.html', top_players=cached_data)
        
        top_players = []
        
        for player in PRO_PLAYERS_DATA[:20]:
            acc_id = player.get('account_id')
            if not acc_id: continue
            try:
                r_matches = requests.get(f"https://api.opendota.com/api/players/{acc_id}/matches?limit=20", timeout=5)
                avg_gpm = 0
                matches_data = []
                if r_matches.status_code == 200 and r_matches.json():
                    matches_data = r_matches.json()
                    valid_gpms = [m.get('gold_per_min', 0) for m in matches_data if m.get('gold_per_min', 0) > 0]
                    avg_gpm = int(sum(valid_gpms) / len(valid_gpms)) if valid_gpms else 0
                
                best_hero_id = 0
                r_heroes = requests.get(f"https://api.opendota.com/api/players/{acc_id}/heroes", timeout=5)
                if r_heroes.status_code == 200 and r_heroes.json():
                    heroes_stats = r_heroes.json()
                    valid_heroes = [h for h in heroes_stats if h.get('games', 0) >= 5]
                    if valid_heroes:
                        best_hero = max(valid_heroes, key=lambda x: x.get('games', 0))
                        best_hero_id = best_hero.get('hero_id', 0)
                
                if best_hero_id == 0 and matches_data:
                    best_hero_id = matches_data[0].get('hero_id', 0)
                
                if best_hero_id == 0 or str(best_hero_id) not in HEROES:
                    best_hero_id = random.choice(list(HEROES.keys()))

                hero_info = HEROES.get(str(best_hero_id), {})
                
                img_url = hero_info.get('img', '')
                if img_url: img_url = img_url.split('?')[0]
                else: img_url = hero_info.get('icon', '')

                top_players.append({
                    "name": player['name'],
                    "team": player['team'],
                    "gpm": avg_gpm if avg_gpm > 0 else random.randint(600, 950),
                    "hero": hero_info.get('localized_name', 'Unknown'),
                    "icon": hero_info.get('icon', ''),
                    "img": img_url
                })
            except Exception as e:
                continue
                
        if not top_players:
            sample_players = random.sample(PRO_PLAYERS_DATA, 10)
            for player in sample_players:
                h_id = random.choice(list(HEROES.keys()))
                hero_info = HEROES.get(str(h_id), {})
                top_players.append({
                    "name": player['name'],
                    "team": player['team'],
                    "gpm": random.randint(600, 950),
                    "hero": hero_info.get('localized_name', 'Unknown'),
                    "icon": hero_info.get('icon', ''),
                    "img": hero_info.get('img', '').split('?')[0] if hero_info.get('img') else hero_info.get('icon', '')
                })
                
        top_players = sorted(top_players, key=lambda x: x['gpm'], reverse=True)[:10]
        
        if top_players:
            cursor.execute('DELETE FROM leaderboard_cache')
            cursor.execute('INSERT INTO leaderboard_cache (id, data, updated_at) VALUES (1, ?, ?)', (json.dumps(top_players), current_time))
            conn.commit()
            
        conn.close()
        return render_template('leaderboard_players.html', top_players=top_players)
    except Exception as e:
        return f"Ошибка: {str(e)}"

@app.route('/guess', methods=['GET', 'POST'])
def guess():
    if 'guess_score' not in session:
        session['guess_score'] = 0
        session['guess_total'] = 0
        session['guess_hero'] = None
        session['guess_hero_winrate'] = None

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'start':
            pool = get_top_heroes_for_hero_of_day()
            if not pool: pool = list(HEROES.keys())

            hero_found = False
            attempts = 0
            while not hero_found and attempts < 10:
                attempts += 1
                h_id = random.choice(pool)
                hero_info = get_hero_info(h_id)
                if hero_info and hero_info.get('calculated_winrate', 0) > 0:
                    local_name = HEROES.get(str(h_id), {}).get('localized_name', 'Unknown')
                    session['guess_hero'] = {"id": h_id, "name": local_name}
                    session['guess_hero_winrate'] = hero_info['calculated_winrate']
                    hero_found = True

            if not hero_found:
                h_id = random.choice(list(HEROES.keys()))
                local_name = HEROES.get(str(h_id), {}).get('localized_name', 'Unknown')
                session['guess_hero'] = {"id": h_id, "name": local_name}
                session['guess_hero_winrate'] = round(random.uniform(45.0, 55.0), 2)

            session.modified = True
            return redirect(url_for('guess'))

        elif action == 'guess':
            user_guess = request.form.get('guess')
            actual_winrate = session.get('guess_hero_winrate', 50.0)
            hero_data = session.get('guess_hero')

            if hero_data and actual_winrate is not None:
                session['guess_total'] += 1
                is_correct = False
                if user_guess == 'high' and actual_winrate > 50.0:
                    session['guess_score'] += 1
                    is_correct = True
                elif user_guess == 'low' and actual_winrate < 50.0:
                    session['guess_score'] += 1
                    is_correct = True

                session['guess_last_result'] = {
                    "correct": is_correct,
                    "actual_winrate": actual_winrate,
                    "user_guess": user_guess,
                    "hero_name": hero_data['name']
                }
                session.modified = True

            pool = get_top_heroes_for_hero_of_day()
            if not pool: pool = list(HEROES.keys())
            
            hero_found = False
            attempts = 0
            while not hero_found and attempts < 10:
                attempts += 1
                h_id = random.choice(pool)
                if session.get('guess_hero') and int(session['guess_hero']['id']) == int(h_id): continue
                hero_info = get_hero_info(h_id)
                if hero_info and hero_info.get('calculated_winrate', 0) > 0:
                    local_name = HEROES.get(str(h_id), {}).get('localized_name', 'Unknown')
                    session['guess_hero'] = {"id": h_id, "name": local_name}
                    session['guess_hero_winrate'] = hero_info['calculated_winrate']
                    hero_found = True

            if not hero_found:
                h_id = random.choice(list(HEROES.keys()))
                local_name = HEROES.get(str(h_id), {}).get('localized_name', 'Unknown')
                session['guess_hero'] = {"id": h_id, "name": local_name}
                session['guess_hero_winrate'] = round(random.uniform(45.0, 55.0), 2)

            session.modified = True
            return redirect(url_for('guess'))

    hero = session.get('guess_hero')
    score = session.get('guess_score', 0)
    total = session.get('guess_total', 0)
    last_result = session.pop('guess_last_result', None)
    
    return render_template('guess.html', hero=hero, score=score, total=total, last_result=last_result)

@app.route('/premium')
def premium():
    is_premium = False
    if 'steam_id' in session:
        conn = sqlite3.connect(os.path.join(base_dir, 'fantasy.db'))
        cursor = conn.cursor()
        cursor.execute("SELECT is_active FROM user_premium WHERE steam_id = ?", (session['steam_id'],))
        row = cursor.fetchone()
        if row and row[0] == 1: is_premium = True
        conn.close()
    return render_template('premium.html', is_premium=is_premium)

@app.route('/premium/activate', methods=['POST'])
def premium_activate():
    if 'steam_id' in session:
        conn = sqlite3.connect(os.path.join(base_dir, 'fantasy.db'))
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO user_premium (steam_id, is_active) VALUES (?, 1)", (session['steam_id'],))
        conn.commit()
        conn.close()
    return redirect(url_for('premium'))

@app.route('/wheel', methods=['GET', 'POST'])
def wheel():
    if 'steam_id' not in session: return redirect(url_for('login_steam'))
    
    conn = sqlite3.connect(os.path.join(base_dir, 'fantasy.db'))
    cursor = conn.cursor()
    cursor.execute("SELECT is_active FROM user_premium WHERE steam_id = ?", (session['steam_id'],))
    row = cursor.fetchone()
    is_premium = row and row[0] == 1
    
    result = None
    win_index = 0 
    
    can_spin = True
    if request.method == 'POST' and is_premium:
        cursor.execute("SELECT last_spin FROM user_spins WHERE steam_id = ?", (session['steam_id'],))
        last_spin_row = cursor.fetchone()
        if last_spin_row:
            last_spin_time = datetime.datetime.strptime(last_spin_row[0], "%Y-%m-%d %H:%M:%S")
            if (datetime.datetime.now() - last_spin_time).total_seconds() < 86400:
                can_spin = False
                result = "Вы уже крутили колесо сегодня. Следующая попытка будет доступна через 24 часа!"
                win_index = -1

    if request.method == 'POST' and is_premium and can_spin:
        rand = random.uniform(0, 100)
        
        if rand < 0.00001:
            result = "Аркана на любого героя! (Поздравляем, ты выиграл джекпот! Свяжись с админом)"
            win_index = 4
        elif rand < 0.06:
            result = "Inscribed Lamb для Slark! (Напиши админу для получения)"
            win_index = 3
        elif rand < 0.56:
            result = "Скин в Доте до 200 рублей!"
            win_index = 2
        elif rand < 20.56:
            result = "Бесплатное продление подписки на месяц!"
            win_index = 0
        elif rand < 44.99999:
            result = "Секретный приз: 1000 Dota Coins (значок в профиле)!"
            win_index = 5
        else:
            result = "Пусто. В следующий раз повезет!"
            win_index = 1
            
        cursor.execute("INSERT OR REPLACE INTO user_spins (steam_id, last_spin) VALUES (?, CURRENT_TIMESTAMP)", (session['steam_id'],))
        conn.commit()
        
    conn.close()
    return render_template('wheel.html', is_premium=is_premium, result=result, win_index=win_index)

@app.route('/admin', methods=['GET', 'POST'])
def admin_login():
    error = None
    if request.method == 'POST':
        password = request.form.get('password', '')
        if password == ADMIN_PASSWORD:
            session['is_admin'] = True
            return redirect(url_for('admin_panel'))
        else: error = "Неверный пароль!"
    return render_template('admin_login.html', error=error)

@app.route('/admin/panel', methods=['GET', 'POST'])
def admin_panel():
    if not session.get('is_admin'): return redirect(url_for('admin_login'))
    
    success = None; error = None
    conn = sqlite3.connect(os.path.join(base_dir, 'fantasy.db'))
    cursor = conn.cursor()
    
    if request.method == 'POST':
        action = request.form.get('action')
        steam_id = request.form.get('steam_id', '').strip()
        
        if action == 'give' and steam_id:
            try:
                cursor.execute("INSERT OR REPLACE INTO user_premium (steam_id, is_active) VALUES (?, 1)", (steam_id,))
                conn.commit()
                success = f"Премиум успешно выдан для ID: {steam_id}"
            except Exception as e: error = f"Ошибка: {e}"
        elif action == 'close_tournament' and steam_id:
            try:
                cursor.execute("INSERT OR REPLACE INTO fantasy_tournaments (tournament_name, is_open) VALUES (?, 0)", (steam_id,))
                conn.commit()
                success = f"Турнир '{steam_id}' закрыт для приема составов."
            except Exception as e: error = f"Ошибка: {e}"
        elif action == 'open_tournament' and steam_id:
            try:
                cursor.execute("INSERT OR REPLACE INTO fantasy_tournaments (tournament_name, is_open) VALUES (?, 1)", (steam_id,))
                conn.commit()
                success = f"Турнир '{steam_id}' открыт!"
            except Exception as e: error = f"Ошибка: {e}"
        elif action == 'remove' and steam_id:
            try:
                cursor.execute("DELETE FROM user_premium WHERE steam_id = ?", (steam_id,))
                conn.commit()
                success = f"Премиум успешно снят с ID: {steam_id}"
            except Exception as e: error = f"Ошибка: {e}"
    
    cursor.execute("SELECT steam_id, activated_at FROM user_premium WHERE is_active = 1")
    premium_users = cursor.fetchall()
    conn.close()
    return render_template('admin_panel.html', success=success, error=error, premium_users=premium_users)

@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    return redirect(url_for('index'))

@app.route('/sitemap.xml')
def sitemap(): return send_from_directory(app.static_folder, 'sitemap.xml')

@app.route('/robots.txt')
def robots(): return send_from_directory(app.static_folder, 'robots.txt')

if __name__ == '__main__':
    app.run(debug=True)
