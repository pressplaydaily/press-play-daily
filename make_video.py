"""Press Play Daily - passo 2: voce + trailer di Steam + sottotitoli = Short.
Legge out/script.json e crea out/short.mp4"""
import asyncio
import glob
import json
import math
import os
import random
import re
import subprocess
import sys
import time

import requests

OUT = "out"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PressPlayDaily/1.0)"}
VOICE = "en-US-AndrewNeural"
RATE = "+6%"
PAD = 0.22  # pausa tra le frasi (secondi)
MUSIC_VOL = 0.16  # volume della musica di sottofondo (0.16 = discreto)


def log(*a):
    print(*a, flush=True)


def run(cmd, timeout=900):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        log("COMANDO FALLITO:", " ".join(cmd)[:300])
        log(r.stderr[-1500:])
        raise RuntimeError("ffmpeg/ffprobe error")
    return r


def duration(path):
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path])
    return float(r.stdout.strip())


# ---------------------------------------------------------------- VOCE
def tts_sentence(text, path):
    import edge_tts

    async def go():
        await edge_tts.Communicate(text, VOICE, rate=RATE).save(path)

    last = None
    for attempt in range(6):
        try:
            asyncio.run(go())
            if os.path.exists(path) and os.path.getsize(path) > 1000:
                return
        except Exception as e:
            last = e
        log(f"  voce: tentativo {attempt + 1} fallito ({last}), riprovo")
        time.sleep(3 + attempt * 3)
    raise RuntimeError("edge-tts non ha prodotto audio")


def make_voice(sentences):
    wavs, durs = [], []
    for i, s in enumerate(sentences):
        mp3 = f"{OUT}/s{i:02d}.mp3"
        wav = f"{OUT}/s{i:02d}.wav"
        tts_sentence(s, mp3)
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp3, "-ar", "44100", "-ac", "1",
             "-af", f"apad=pad_dur={PAD}", wav])
        wavs.append(wav)
        durs.append(duration(wav))
    with open(f"{OUT}/voice_list.txt", "w") as f:
        for w in wavs:
            f.write(f"file '{os.path.basename(w)}'\n")
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", f"{OUT}/voice_list.txt", "-c", "copy", f"{OUT}/voice.wav"])
    return durs


# ------------------------------------------------------------- TRAILER
def download_url(url, dest):
    """Scarica un file video (mp4/webm diretto oppure playlist HLS)."""
    if ".m3u8" in url or ".mpd" in url:
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", url, "-an", "-c:v", "copy", dest],
            timeout=600)
    else:
        with requests.get(url, headers=HEADERS, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)


def candidate_urls(movie):
    urls = []
    for key in ("mp4", "webm"):
        v = movie.get(key)
        if isinstance(v, dict):
            for q in ("max", "480"):
                if v.get(q):
                    urls.append(v[q])
    for key in ("hls_h264", "dash_h264"):
        if movie.get(key):
            urls.append(movie[key])
    return urls


def get_trailers(movies, need_seconds):
    files, total = [], 0.0
    for idx, mv in enumerate(movies[:8]):
        dest = f"{OUT}/trailer{idx}.mp4"
        ok = False
        for url in candidate_urls(mv):
            try:
                log(f"Scarico trailer {idx}: {url[:90]}")
                download_url(url, dest)
                d = duration(dest)
                if d >= 8:
                    files.append((dest, d))
                    total += d
                    ok = True
                    log(f"  ok, durata {d:.0f}s")
                    break
                log("  troppo corto, scarto")
            except Exception as e:
                log("  non riuscito:", str(e)[:150])
        if total >= need_seconds * 1.5 and len(files) >= 2:
            break
    if not files:
        raise RuntimeError("nessun trailer scaricato da Steam")
    return files


def plan_cuts(trailers, total_len):
    """Primi 2 tagli veloci (1,5s) per catturare subito, poi tagli da ~3,4s.
    Si parte dalla parte centrale dei trailer, di solito la piu' dinamica
    (l'inizio e' spesso pieno di loghi)."""
    fast = 2
    fast_len = 1.5
    rest_total = max(total_len - fast * fast_len, 3.0)
    n_rest = max(3, math.ceil(rest_total / 3.4))
    lens = [fast_len] * fast + [rest_total / n_rest] * n_rest
    cuts = []
    counters = [0] * len(trailers)
    for k, cut in enumerate(lens):
        ti = k % len(trailers)
        path, dur = trailers[ti]
        start_min = 2.0 if dur > 15 else 0.3
        end_max = dur - (3.0 if dur > 15 else 0.3) - cut
        span = max(end_max - start_min, 0.5)
        j = counters[ti]
        counters[ti] += 1
        offset = 0.35 * span
        start = start_min + (offset + j * 3.4 * 1.3) % span
        cuts.append((path, round(start, 2), round(cut, 3)))
    return cuts


VF = ("[0:v]split=2[a][b];"
      "[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,"
      "boxblur=6:2,scale=1080:1920,eq=brightness=-0.18[bg];"
      "[b]scale=1080:-2[fg];"
      "[bg][fg]overlay=(W-w)/2:(H-h)/2,fps=30,format=yuv420p[v]")


def render_segments(cuts):
    names = []
    for i, (path, start, cut) in enumerate(cuts):
        seg = f"{OUT}/seg{i:03d}.mp4"
        run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(start), "-t", str(cut),
             "-i", path, "-an", "-filter_complex", VF, "-map", "[v]",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-r", "30", seg])
        names.append(os.path.basename(seg))
    with open(f"{OUT}/seg_list.txt", "w") as f:
        for n in names:
            f.write(f"file '{n}'\n")
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", f"{OUT}/seg_list.txt", "-c", "copy", f"{OUT}/joined.mp4"])


# ---------------------------------------------------------- SOTTOTITOLI
def ts(t):
    t = max(t, 0)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def clean_text(s):
    return re.sub(r"[{}\\\n]", " ", s).strip()


def make_ass(script, durs):
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,DejaVu Sans,78,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,7,2,2,70,70,330,1
Style: Brand,DejaVu Sans,46,&H0000D7FF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,2,0,1,5,2,8,60,60,150,1
Style: Hook,DejaVu Sans,86,&H0000FFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,8,2,8,70,70,290,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    total = sum(durs)
    lines = [f"Dialogue: 0,{ts(0)},{ts(total)},Brand,,0,0,0,,PRESS PLAY DAILY"]
    hook = clean_text(script.get("hook", "")).upper()
    if hook:
        pop = r"{\fad(90,250)\fscx55\fscy55\t(0,170,\fscx110\fscy110)\t(170,320,\fscx100\fscy100)}"
        lines.append(f"Dialogue: 1,{ts(0)},{ts(min(3.2, total))},Hook,,0,0,0,,{pop}{hook}")
    t = 0.0
    for sentence, d in zip(script["sentences"], durs):
        words = clean_text(sentence).split()
        chunks = [words[i:i + 4] for i in range(0, len(words), 4)]
        # evita un ultimo pezzo con una parola sola
        if len(chunks) > 1 and len(chunks[-1]) == 1:
            last = chunks.pop()
            chunks[-1] = chunks[-1] + last
        weights = [sum(len(w) + 1 for w in c) for c in chunks]
        wsum = sum(weights)
        speak = max(d - PAD, 0.3)
        cur = t
        for c, w in zip(chunks, weights):
            span = speak * w / wsum
            text = " ".join(c).upper()
            lines.append(f"Dialogue: 0,{ts(cur)},{ts(cur + span)},Cap,,0,0,0,,{text}")
            cur += span
        t += d
    with open(f"{OUT}/subs.ass", "w", encoding="utf-8") as f:
        f.write(header + "\n".join(lines) + "\n")


# ----------------------------------------------------------------- MUSICA + MIX FINALE
def find_music():
    files = sorted(glob.glob("music*.mp3"))
    if not files:
        return None
    return os.path.abspath(random.choice(files))


def final_render(total, music):
    base = ["ffmpeg", "-y", "-loglevel", "error", "-i", "joined.mp4", "-i", "voice.wav"]
    tail = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-c:a", "aac",
            "-b:a", "160k", "-shortest", "-movflags", "+faststart", "short.mp4"]
    attempts = []
    if music:
        mdur = duration(music)
        off = random.uniform(0, mdur - total - 2) if mdur > total + 4 else 0.0
        fc = (f"[0:v]ass=subs.ass[v];"
              f"[2:a]volume={MUSIC_VOL},afade=t=in:d=1,"
              f"afade=t=out:st={max(total - 1.5, 0):.2f}:d=1.5[m];"
              f"[1:a][m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]")
        attempts.append(base + ["-stream_loop", "-1", "-ss", f"{off:.2f}", "-i", music,
                                "-filter_complex", fc, "-map", "[v]", "-map", "[a]"] + tail)
    attempts.append(base + ["-filter_complex", "[0:v]ass=subs.ass[v]",
                            "-map", "[v]", "-map", "1:a"] + tail)
    for i, cmd in enumerate(attempts):
        r = subprocess.run(cmd, cwd=OUT, capture_output=True, text=True, timeout=900)
        if r.returncode == 0:
            if music and i == 0:
                log("Musica di sottofondo aggiunta:", os.path.basename(music))
            return
        log("Montaggio finale fallito" + (" con la musica, riprovo senza" if music and i == 0 else ""))
        log(r.stderr[-1500:])
    sys.exit("ERRORE nel montaggio finale")


# ----------------------------------------------------------------- MAIN
def main():
    with open(f"{OUT}/script.json", encoding="utf-8") as f:
        script = json.load(f)

    log("1/4 Genero la voce...")
    durs = make_voice(script["sentences"])
    total = sum(durs)
    log(f"Durata voce: {total:.1f}s")

    log("2/4 Scarico i video...")
    trailers = get_trailers(script["movies"], total)

    log("3/4 Preparo le clip verticali...")
    cuts = plan_cuts(trailers, total)
    render_segments(cuts)

    log("4/4 Montaggio finale con sottotitoli e musica...")
    make_ass(script, durs)
    final_render(total, find_music())
    log(f"FATTO: {OUT}/short.mp4, {duration(OUT + '/short.mp4'):.1f}s")


if __name__ == "__main__":
    main()
