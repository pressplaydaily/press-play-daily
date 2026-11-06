"""Press Play Daily - passo 1: sceglie un gioco, prende i dati da Steam e
fa scrivere a Gemini lo script di uno Short. Crea out/script.json"""
import json
import os
import random
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone

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


def gemini_json(prompt):
    key = os.environ.get("GEMINI_KEY")
    if not key:
        sys.exit("ERRORE: manca il segreto GEMINI_KEY su GitHub (Settings > Secrets and variables > Actions).")
    models = ["gemini-flash-latest", "gemini-2.5-flash", "gemini-2.0-flash"]
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.9, "responseMimeType": "application/json"},
    }
    last = ""
    for m in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
        for attempt in range(3):
            try:
                r = requests.post(url, params={"key": key}, json=payload, timeout=120)
            except Exception as e:
                last = str(e)
                time.sleep(10)
                continue
            if r.status_code == 200:
                try:
                    parts = r.json()["candidates"][0]["content"]["parts"]
                    text = "".join(p.get("text", "") for p in parts)
                    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
                    log(f"Gemini ok con il modello {m}")
                    return json.loads(text)
                except Exception as e:
                    last = f"risposta non valida: {e}"
                    break
            last = f"{m}: HTTP {r.status_code} {r.text[:200]}"
            if r.status_code == 404:
                break
            time.sleep((attempt + 1) * 15)
    sys.exit("ERRORE Gemini: " + last)


def build_prompt(d, angle_text):
    info = {
        "name": d.get("name"),
        "short_description": clean_html(d.get("short_description")),
        "release_date": (d.get("release_date") or {}).get("date"),
        "genres": [g["description"] for g in d.get("genres", [])],
        "developers": d.get("developers"),
        "publishers": d.get("publishers"),
        "metacritic": (d.get("metacritic") or {}).get("score"),
    }
    return f"""You write scripts for a YouTube Shorts gaming channel called Press Play Daily.
Write ONE vertical Short about this game. Angle: {angle_text}.

GAME INFO FROM STEAM (use this as your main source of truth):
{json.dumps(info, ensure_ascii=False)}

RULES:
- English only. Energetic, clear, conversational, like a friend talking to another gamer.
- Total narration: 85 to 110 words, split in 8 to 12 short sentences (max 16 words each).
- The FIRST sentence is a strong hook that makes people keep watching.
- The LAST sentence is a punchy closing line that asks viewers a question to answer in the comments.
- Use only facts from the info above or facts you are 100 percent sure about.
  Never invent numbers, sales figures, dates, quotes, rumors or release details.
- Opinions are fine, but present them as opinions. No insults, no profanity, no spoilers.
- Do not say the words trailer, video, Steam or "in this Short".
- Do not use emojis, hashtags or special symbols inside the sentences.

Return ONLY a JSON object with these keys:
"title": catchy YouTube title, max 65 characters, no hashtags,
"hook": on-screen text, max 6 words, all about curiosity,
"sentences": list of strings (the narration, one sentence each),
"description": 2 sentences for the YouTube description (no hashtags),
"hashtags": list of 4 to 6 hashtags (with #), relevant to the game and gaming."""


def valid(js):
    try:
        s = js["sentences"]
        words = sum(len(x.split()) for x in s)
        return (isinstance(s, list) and 6 <= len(s) <= 14 and 70 <= words <= 130
                and isinstance(js["title"], str) and isinstance(js["hook"], str)
                and isinstance(js["description"], str) and isinstance(js["hashtags"], list))
    except Exception:
        return False


def main():
    os.makedirs("out", exist_ok=True)
    history = load_history()
    details = None
    chosen = None
    for g in pick_candidates(history)[:10]:
        try:
            appid = steam_find_appid(g)
            if not appid:
                log("Non trovato su Steam:", g)
                continue
            details = steam_details(appid)
        except Exception as e:
            log("Errore Steam con", g, e)
            continue
        if details:
            chosen = g
            break
        log("Nessun trailer disponibile per", g)
    if not details:
        sys.exit("ERRORE: nessun gioco con trailer trovato su Steam.")

    used = {h.get("angle") for h in history if h.get("game") == chosen}
    free = [a for a in ANGLES if a[0] not in used] or ANGLES
    angle_id, angle_tpl = random.choice(free)
    name = details["name"]
    log(f"Gioco scelto: {name} (cercato come '{chosen}'), angolo: {angle_id}")

    js = None
    for _ in range(3):
        js = gemini_json(build_prompt(details, angle_tpl.format(g=name)))
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
    desc = (js["description"].strip() + "\n\n" + hashtags +
            "\n\nFootage comes from official publisher trailers. All game titles, "
            "characters and footage belong to their respective owners. #Shorts")

    out = {
        "game_query": chosen,
        "game": name,
        "angle": angle_id,
        "title": title,
        "hook": re.sub(r"[{}]", "", js["hook"]).strip(),
        "sentences": sentences,
        "description": desc,
        "tags": tags[:8] + [name, "gaming", "shorts"],
        "movies": details["movies"],
        "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with open("out/script.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    log("TITOLO:", title)
    log("SCRIPT:", " ".join(sentences))


if __name__ == "__main__":
    main()
