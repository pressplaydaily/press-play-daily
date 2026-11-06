"""Press Play Daily - passo 3: carica out/short.mp4 su YouTube (pubblico)
e salva il gioco usato in history.json"""
import json
import os
import sys

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


def log(*a):
    print(*a, flush=True)


def main():
    needed = ["YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"]
    missing = [n for n in needed if not os.environ.get(n)]
    if missing:
        sys.exit("ERRORE: mancano i segreti GitHub: " + ", ".join(missing))

    with open("out/script.json", encoding="utf-8") as f:
        script = json.load(f)

    creds = Credentials(
        None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    try:
        creds.refresh(Request())
    except Exception as e:
        sys.exit(f"ERRORE: il refresh token non e' valido o e' scaduto ({e}). "
                 "Va rigenerato con OAuth Playground.")

    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    body = {
        "snippet": {
            "title": script["title"][:100],
            "description": script["description"][:4900],
            "tags": script["tags"][:15],
            "categoryId": "20",
            "defaultLanguage": "en",
            "defaultAudioLanguage": "en",
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload("out/short.mp4", mimetype="video/mp4",
                            chunksize=8 * 1024 * 1024, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            log(f"Caricamento {int(status.progress() * 100)}%")
    vid = resp["id"]
    log(f"CARICATO: https://youtube.com/shorts/{vid}")
    log("Stato privacy restituito da YouTube:", resp.get("status", {}).get("privacyStatus"))

    history = []
    if os.path.exists("history.json"):
        try:
            with open("history.json", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = []
    history.append({
        "game": script["game_query"],
        "angle": script["angle"],
        "title": script["title"],
        "video_id": vid,
        "date": script["date"],
    })
    with open("history.json", "w", encoding="utf-8") as f:
        json.dump(history[-300:], f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
