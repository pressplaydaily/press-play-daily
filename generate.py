"""Press Play Daily - passo 1: sceglie un gioco, prende i dati da Steam e
fa scrivere a Gemini lo script di uno Short. Crea out/script.json"""
import json
import os
import random
import re
import sys
import time
from collections import Counter
from datetime import date, datetime, timezone

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PressPlayDaily/1.0)"}

# Giochi molto conosciuti (il nome viene cercato su Steam)
GAMES = [
    "Cyberpunk 2077", "Elden Ring", "Baldur's Gate 3", "Red Dead Redemption 2",
    "The Witcher 3: Wild Hunt", "Grand Theft Auto V", "Counter-Strike 2",
    "Black Myth: Wukong", "Hogwarts Legacy", "Helldivers 2", "God of War",
    "Marvel's Spider-Man Remastered", "The Last of Us Part I", "Resident Evil 4",
    "Monster Hunter: World", "Monster Hunter Wilds", "Assassin's Creed Shadows",
    "Ghost of Tsushima", "The Elder Scrolls V: Skyrim Special Edition",
    "Fallout 4", "Dark Souls III", "Sekiro: Shadows Die Twice", "Death Stranding",
    "Starfield", "Forza Horizon 5", "Battlefield 6", "Apex Legends",
    "Rainbow Six Siege", "PUBG: BATTLEGROUNDS", "Dota 2", "Palworld",
    "ARC Raiders", "DOOM: The Dark Ages", "Marvel Rivals", "Street Fighter 6",
    "Tekken 8", "Mortal Kombat 1", "Final Fantasy VII Remake Intergrade",
    "Resident Evil Village", "Hades II", "Hollow Knight: Silksong",
    "Clair Obscur: Expedition 33", "S.T.A.L.K.E.R. 2", "Lies of P",
    "Dying Light 2", "Far Cry 6", "Horizon Zero Dawn Remastered",
    "Horizon Forbidden West", "Civilization VII", "Cities: Skylines II",
    "The Sims 4", "EA SPORTS FC 26", "Metal Gear Solid Delta", "Silent Hill f",
    "Elden Ring Nightreign", "Black Ops 6", "Stellar Blade", "Wuthering Waves",
]

ANGLES = [
    ("five", "5 things to know before you play {g}"),
    ("still", "why {g} is still worth talking about"),
    ("diff", "what makes {g} different from other games in its genre"),
    ("who", "who {g} is really for"),
    ("worth", "is {g} worth playing in 2026?"),
    ("best", "the best things about {g}, ranked"),
    ("sixty", "{g} explained in 60 seconds: gameplay, story and who made it"),
]


UPCOMING_ANGLES = [
    ("up_radar", "why {g} is on every gamer's radar"),
    ("up_know", "what we know so far about {g}"),
    ("up_hype", "should you be hyped for {g}?"),
    ("up_five", "5 reasons {g} could be a big deal"),
]

TREND_ANGLES = [
    ("tr_why", "why {g} is blowing up right now"),
    ("tr_jump", "should you jump into {g} right now?"),
    ("tr_what", "what is {g} and why are so many players flooding in?"),
    ("tr_hype", "the hype around {g}, explained in 60 seconds"),
]

GTA_RELEASE = date(2026, 11, 19)
GTA_FACTS = {
    "name": "Grand Theft Auto VI (GTA 6)",
    "developer": "Rockstar Games",
    "publisher": "Take-Two Interactive",
    "release_date": "November 19, 2026",
    "platforms_at_launch": "PlayStation 5 and Xbox Series X|S (no PC release date announced)",
    "setting": "the fictional state of Leonida, inspired by Florida, including Vice City",
    "protagonists": "Lucia and Jason, shown in the official trailers",
}
GTA_ANGLES = [
    ("gta_count", "the countdown: what to know about GTA 6 right now"),
    ("gta_official", "what is officially confirmed about GTA 6"),
    ("gta_plat", "GTA 6 platforms and release date, explained simply"),
    ("gta_hype", "why the whole gaming world is talking about GTA 6"),
    ("gta_buy", "should you play GTA 6 on day one? an honest take"),
]
GTA_QUERIES = ["miami", "palm trees", "sports car night", "neon city", "beach sunset",
               "speedboat", "skyline night", "ocean drive", "tropical city", "highway night"]


def log(*a):
    print(*a, flush=True)


def load_history():
    if os.path.exists("history.json"):
        try:
            with open("history.json", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def pick_candidates(history):
    """Ordina i giochi: prima quelli usati meno volte e non usati di recente."""
    counts = Counter(h.get("game") for h in history)
    recent = [h.get("game") for h in history[-10:]]
    games = GAMES[:]
    random.shuffle(games)
    games.sort(key=lambda g: (g in recent, counts.get(g, 0)))
    return games


def steam_find_appid(name):
    r = requests.get(
        "https://store.steampowered.com/api/storesearch/",
        params={"term": name, "l": "english", "cc": "us"},
        headers=HEADERS, timeout=30)
    r.raise_for_status()
    items = [i for i in r.json().get("items", []) if i.get("type", "app") == "app"]
    if not items:
        return None
    low = name.lower()
    for it in items:
        if low in it.get("name", "").lower():
            return it["id"]
    return items[0]["id"]


def steam_details(appid):
    r = requests.get(
        "https://store.steampowered.com/api/appdetails",
        params={"appids": appid, "l": "english", "cc": "us"},
        headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json().get(str(appid), {})
    if not data.get("success"):
        return None
    d = data["data"]
    if d.get("type") != "game":
        return None
    if not d.get("movies"):
        return None
    return d


def clean_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = re.sub(r"&[a-z]+;", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def gemini_models(key):
    """Chiede a Google quali modelli Gemini 'flash' esistono adesso, dal piu' nuovo."""
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                         params={"key": key, "pageSize": 200}, timeout=60)
        r.raise_for_status()
        found = []
        for m in r.json().get("models", []):
            name = m.get("name", "").replace("models/", "")
            if "generateContent" not in m.get("supportedGenerationMethods", []):
                continue
            low = name.lower()
            if "flash" not in low or not low.startswith("gemini"):
                continue
            if any(x in low for x in ("image", "tts", "live", "audio", "native",
                                      "embedding", "exp", "robotics", "computer")):
                continue
            ver = re.search(r"gemini-(\d+(?:\.\d+)?)", low)
            v = float(ver.group(1)) if ver else 0.0
            found.append((v, "preview" not in low, "lite" not in low, name))
        found.sort(reverse=True)
        names = [f[3] for f in found]
        log("Modelli Gemini disponibili (provo i primi):", names[:6])
        return names[:6]
    except Exception as e:
        log("Non riesco a leggere l'elenco dei modelli:", e)
        return []


def gemini_json(prompt):
    key = os.environ.get("GEMINI_KEY")
    if not key:
        sys.exit("ERRORE: manca il segreto GEMINI_KEY su GitHub (Settings > Secrets and variables > Actions).")
    models = gemini_models(key) or ["gemini-flash-latest", "gemini-2.5-flash"]
    errors = []
    for m in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
        use_json_mode = True
        for attempt in range(3):
            gen = {"temperature": 0.9}
            if use_json_mode:
                gen["responseMimeType"] = "application/json"
            payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": gen}
            try:
                r = requests.post(url, params={"key": key}, json=payload, timeout=120)
            except Exception as e:
                errors.append(f"{m}: {e}")
                time.sleep(10)
                continue
            if r.status_code == 200:
                try:
                    parts = r.json()["candidates"][0]["content"]["parts"]
                    text = "".join(p.get("text", "") for p in parts)
                    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
                    js = json.loads(text)
                    log(f"Gemini ok con il modello {m}")
                    return js
                except Exception as e:
                    errors.append(f"{m}: risposta non valida ({e})")
                    break
            errors.append(f"{m}: HTTP {r.status_code} {r.text[:160]}")
            if r.status_code == 400 and use_json_mode:
                use_json_mode = False
                continue
            if r.status_code in (404, 403):
                break
            time.sleep((attempt + 1) * 15)
    log("Tutti i tentativi Gemini:")
    for e in errors:
        log(" -", e.replace("\n", " "))
    sys.exit("ERRORE Gemini: nessun modello ha risposto (vedi sopra).")


def steam_upcoming():
    """Giochi in uscita piu' popolari su Steam (lista aggiornata da Steam)."""
    found = []
    try:
        r = requests.get(
            "https://store.steampowered.com/search/results/",
            params={"filter": "popularcomingsoon", "json": 1, "count": 50, "cc": "us", "l": "english"},
            headers=HEADERS, timeout=30)
        r.raise_for_status()
        for it in r.json().get("items", []):
            m = re.search(r"/apps/(\d+)/", it.get("logo", ""))
            if m:
                found.append((int(m.group(1)), it.get("name", "")))
    except Exception as e:
        log("Elenco 'popolari in uscita' non disponibile:", str(e)[:120])
    if not found:
        try:
            r = requests.get("https://store.steampowered.com/api/featuredcategories",
                             params={"cc": "us", "l": "english"}, headers=HEADERS, timeout=30)
            r.raise_for_status()
            for it in r.json().get("coming_soon", {}).get("items", []):
                if it.get("id"):
                    found.append((int(it["id"]), it.get("name", "")))
        except Exception as e:
            log("Elenco 'in uscita' alternativo non disponibile:", str(e)[:120])
    return found


def steam_trending():
    """Giochi piu' giocati su Steam in questo momento: [(appid, posizione), ...]."""
    found = []
    try:
        r = requests.get("https://api.steampowered.com/ISteamChartsService/GetMostPlayedGames/v1/",
                         headers=HEADERS, timeout=30)
        r.raise_for_status()
        for it in r.json().get("response", {}).get("ranks", []):
            if it.get("appid"):
                found.append((int(it["appid"]), int(it.get("rank", len(found) + 1))))
    except Exception as e:
        log("Classifica 'piu' giocati ora' non disponibile:", str(e)[:120])
    if not found:
        try:
            r = requests.get("https://store.steampowered.com/api/featuredcategories",
                             params={"cc": "us", "l": "english"}, headers=HEADERS, timeout=30)
            r.raise_for_status()
            for i, it in enumerate(r.json().get("top_sellers", {}).get("items", []), start=1):
                if it.get("id"):
                    found.append((int(it["id"]), i))
        except Exception as e:
            log("Classifica 'piu' venduti' non disponibile:", str(e)[:120])
    return found


def build_info(d):
    return {
        "name": d.get("name"),
        "short_description": clean_html(d.get("short_description")),
        "release_date": (d.get("release_date") or {}).get("date"),
        "genres": [g["description"] for g in d.get("genres", [])],
        "developers": d.get("developers"),
        "publishers": d.get("publishers"),
        "metacritic": (d.get("metacritic") or {}).get("score"),
    }


def build_prompt(info, angle_text, extra_rules):
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    return f"""You write scripts for a YouTube Shorts gaming channel called Press Play Daily.
Write ONE vertical Short. Angle: {angle_text}.
Today's date: {today}.

INFO (use this as your main source of truth):
{json.dumps(info, ensure_ascii=False)}
{extra_rules}
RULES:
- English only. Energetic, clear, conversational, like a friend talking to another gamer.
- Total narration: 85 to 110 words, split in 8 to 12 short sentences (max 16 words each).
- The FIRST sentence is the spoken HOOK. It must grab attention in the first 2 seconds.
  Max 12 words. Start in the middle of the action with a bold claim, a surprising contrast,
  a direct challenge to the viewer, or a question that creates curiosity the rest of the video answers.
  Good styles: "Everyone says X, but the truth is Y." / "This game broke a rule most games never dare to." /
  "Think you know X? One detail changes everything." / "Stop scrolling if you love X."
  NEVER start with: "Did you know", "Have you ever", "Welcome", "Today", "In this", "Let's talk about", "Hey guys".
  The hook must be specific to THIS topic, never generic.
- The LAST sentence is a punchy closing line that asks viewers a question to answer in the comments.
- Use only facts from the info above or facts you are 100 percent sure about.
  Never invent numbers, sales figures, dates, quotes, rumors, leaks or release details.
- Opinions are fine, but present them as opinions. No insults, no profanity, no spoilers.
- Do not say the words trailer, video, Steam or "in this Short".
- Do not use emojis, hashtags or special symbols inside the sentences.

Return ONLY a JSON object with these keys:
"title": catchy YouTube title, max 65 characters, no hashtags,
"hook": on-screen text shown big during the first 3 seconds, 3 to 5 words, ALL about curiosity or a bold claim. It must NOT repeat the first sentence word for word; it should tease it (examples: "THE GAME THAT CHANGED EVERYTHING", "NOBODY SAW THIS COMING", "THIS IS WHY IT STILL WINS"),
"sentences": list of strings (the narration, one sentence each),
"description": 2 sentences for the YouTube description (no hashtags),
"hashtags": list of 4 to 6 hashtags (with #), relevant to the topic and gaming."""


def valid(js):
    try:
        s = js["sentences"]
        words = sum(len(x.split()) for x in s)
        return (isinstance(s, list) and 6 <= len(s) <= 14 and 70 <= words <= 130
                and isinstance(js["title"], str) and isinstance(js["hook"], str)
                and isinstance(js["description"], str) and isinstance(js["hashtags"], list))
    except Exception:
        return False


DISCLAIMER_TRAILER = ("Footage comes from official publisher trailers. All game titles, "
                      "characters and footage belong to their respective owners.")
DISCLAIMER_GTA = ("Background footage is generic stock footage and is NOT from the game. "
                  "Information is based on official announcements by Rockstar Games and "
                  "Take-Two Interactive. All names belong to their respective owners.")


def ctx_popular(history):
    for g in pick_candidates(history)[:10]:
        try:
            appid = steam_find_appid(g)
            if not appid:
                log("Non trovato su Steam:", g)
                continue
            d = steam_details(appid)
        except Exception as e:
            log("Errore Steam con", g, e)
            continue
        if d:
            used = {h.get("angle") for h in history if h.get("game") == g}
            free = [a for a in ANGLES if a[0] not in used] or ANGLES
            angle = random.choice(free)
            return {"mode": "popular", "query": g, "name": d["name"], "info": build_info(d),
                    "movies": d["movies"], "angle": angle, "rules": "",
                    "disclaimer": DISCLAIMER_TRAILER, "tags": []}
        log("Nessun trailer disponibile per", g)
    return None


def ctx_upcoming(history):
    done = {h.get("game") for h in history}
    cands = [c for c in steam_upcoming() if c[1] and c[1] not in done]
    random.shuffle(cands)
    for appid, name in cands[:10]:
        try:
            d = steam_details(appid)
        except Exception as e:
            log("Errore Steam con", name, e)
            continue
        if not d:
            continue
        info = build_info(d)
        info["status"] = "NOT RELEASED YET (coming soon)"
        rules = ("\nThis game is NOT released yet. Talk about it in the future tense. "
                 "Do not claim anything about gameplay, features or quality beyond the info above.\n")
        return {"mode": "upcoming", "query": d["name"], "name": d["name"], "info": info,
                "movies": d["movies"], "angle": random.choice(UPCOMING_ANGLES), "rules": rules,
                "disclaimer": DISCLAIMER_TRAILER, "tags": ["upcoming games", "coming soon"]}
    return None


def ctx_trending(history):
    recent = {h.get("game") for h in history[-15:]}
    cands = steam_trending()[:40]
    random.shuffle(cands)
    for appid, rank in cands[:12]:
        try:
            d = steam_details(appid)
        except Exception as e:
            log("Errore Steam con", appid, e)
            continue
        if not d or d["name"] in recent:
            continue
        info = build_info(d)
        info["trending"] = (f"One of the most played games on Steam right now "
                            f"(rank {rank} by current players)")
        rules = ("\nThis game is trending right now. You may say it is one of the most played games "
                 "on Steam at the moment. Do not quote player counts or numbers that are not in the info.\n")
        return {"mode": "trending", "query": d["name"], "name": d["name"], "info": info,
                "movies": d["movies"], "angle": random.choice(TREND_ANGLES), "rules": rules,
                "disclaimer": DISCLAIMER_TRAILER, "tags": ["trending", "steam"]}
    return None


def pixabay_movies():
    key = os.environ.get("PIXABAY_KEY")
    if not key:
        return []
    urls = []
    for q in random.sample(GTA_QUERIES, 5):
        try:
            r = requests.get("https://pixabay.com/api/videos/",
                             params={"key": key, "q": q, "per_page": 20, "safesearch": "true"},
                             headers=HEADERS, timeout=30)
            r.raise_for_status()
            hits = r.json().get("hits", [])
        except Exception as e:
            log("Pixabay non risponde per", q, str(e)[:100])
            continue
        random.shuffle(hits)
        for h in hits[:2]:
            v = h.get("videos", {})
            for size in ("large", "medium"):
                u = (v.get(size) or {}).get("url")
                if u and u not in urls:
                    urls.append(u)
                    break
    return [{"mp4": {"max": u}} for u in urls[:8]]


def ctx_gta(history):
    movies = pixabay_movies()
    if len(movies) < 4:
        log("Poche clip da Pixabay, salto GTA 6")
        return None
    days = (GTA_RELEASE - datetime.now(timezone.utc).date()).days
    info = dict(GTA_FACTS)
    if days > 0:
        info["days_until_release"] = days
    elif days == 0:
        info["status"] = "RELEASES TODAY"
    else:
        info["status"] = f"ALREADY RELEASED {-days} days ago"
    rules = ("\nOnly use the facts above about GTA 6. Do NOT mention gameplay details, map size, price, "
             "story, leaks or rumors. No story spoilers of any kind. "
             "If days_until_release is present, you may mention the countdown. "
             "If the game is released, talk about it as out now, without spoilers.\n")
    used = {h.get("angle") for h in history if h.get("game") == "GTA 6"}
    free = [a for a in GTA_ANGLES if a[0] not in used] or GTA_ANGLES
    return {"mode": "gta6", "query": "GTA 6", "name": "GTA 6", "info": info, "movies": movies,
            "angle": random.choice(free), "rules": rules, "disclaimer": DISCLAIMER_GTA,
            "tags": ["GTA 6", "GTA VI", "Rockstar Games"]}


def choose_context(history):
    r = random.random()
    if os.environ.get("PIXABAY_KEY") and r < 0.10:
        order = [ctx_gta, ctx_trending, ctx_popular]
    elif r < 0.35:
        order = [ctx_upcoming, ctx_trending, ctx_popular]
    elif r < 0.65:
        order = [ctx_trending, ctx_popular, ctx_upcoming]
    else:
        order = [ctx_popular, ctx_trending, ctx_upcoming]
    for fn in order:
        try:
            ctx = fn(history)
        except Exception as e:
            log("Errore in", fn.__name__, str(e)[:150])
            ctx = None
        if ctx:
            return ctx
    return None


def main():
    os.makedirs("out", exist_ok=True)
    history = load_history()
    ctx = choose_context(history)
    if not ctx:
        sys.exit("ERRORE: nessun argomento con video trovato.")
    angle_id, angle_tpl = ctx["angle"]
    name = ctx["name"]
    log(f"Modo: {ctx['mode']} | argomento: {name} | angolo: {angle_id}")

    js = None
    for _ in range(3):
        js = gemini_json(build_prompt(ctx["info"], angle_tpl.format(g=name), ctx["rules"]))
        if valid(js):
            break
        log("Script non valido, riprovo...")
        js = None
    if not js:
        sys.exit("ERRORE: Gemini non ha prodotto uno script valido.")

    sentences = [re.sub(r"[#*_{}\[\]<>]", "", x).strip() for x in js["sentences"] if x.strip()]
    tags = [t.lstrip("#").replace(" ", "") for t in js["hashtags"] if t.strip()]
    title = js["title"].strip().strip('"')[:70]
    if len(title) <= 90 and "#shorts" not in title.lower():
        title += " #Shorts"
    hashtags = " ".join("#" + t for t in tags[:6])
    desc = (js["description"].strip() + "\n\n" + hashtags + "\n\n" +
            ctx["disclaimer"] + " #Shorts")

    out = {
        "mode": ctx["mode"],
        "game_query": ctx["query"],
        "game": name,
        "angle": angle_id,
        "title": title,
        "hook": re.sub(r"[{}]", "", js["hook"]).strip(),
        "sentences": sentences,
        "description": desc,
        "tags": tags[:8] + [name, "gaming", "shorts"] + ctx["tags"],
        "movies": ctx["movies"],
        "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with open("out/script.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    log("TITOLO:", title)
    log("SCRIPT:", " ".join(sentences))


if __name__ == "__main__":
    main()
