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
    """Crea l'mp3 e restituisce i tempi delle parole [(inizio, fine), ...] in secondi."""
    import edge_tts

    async def go():
        try:
            comm = edge_tts.Communicate(text, VOICE, rate=RATE, boundary="WordBoundary")
        except TypeError:  # versioni vecchie di edge-tts
            comm = edge_tts.Communicate(text, VOICE, rate=RATE)
        audio = bytearray()
        bounds = []
        async for ch in comm.stream():
            kind = ch.get("type")
            if kind == "audio":
                audio.extend(ch["data"])
            elif kind in ("WordBoundary", "SentenceBoundary"):
                off = ch["offset"] / 1e7
                bounds.append((off, off + ch["duration"] / 1e7))
        with open(path, "wb") as f:
            f.write(audio)
        return bounds

    last = None
    for attempt in range(6):
        try:
            bounds = asyncio.run(go())
            if os.path.exists(path) and os.path.getsize(path) > 1000:
                return bounds
        except Exception as e:
            last = e
        log(f"  voce: tentativo {attempt + 1} fallito ({last}), riprovo")
        time.sleep(3 + attempt * 3)
    raise RuntimeError("edge-tts non ha prodotto audio")


def make_voice(sentences):
    wavs, durs, all_bounds = [], [], []
    for i, s in enumerate(sentences):
        mp3 = f"{OUT}/s{i:02d}.mp3"
        wav = f"{OUT}/s{i:02d}.wav"
        bounds = tts_sentence(s, mp3)
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp3, "-ar", "44100", "-ac", "1",
             "-af", f"apad=pad_dur={PAD}", wav])
        wavs.append(wav)
        durs.append(duration(wav))
        all_bounds.append(bounds)
    with open(f"{OUT}/voice_list.txt", "w") as f:
        for w in wavs:
            f.write(f"file '{os.path.basename(w)}'\n")
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", f"{OUT}/voice_list.txt", "-c", "copy", f"{OUT}/voice.wav"])
    return durs, all_bounds


def word_times(words, bounds, d):
    """Tempo (inizio, fine) di ogni parola dentro la frase. Usa i tempi di edge-tts se
    coincidono con le parole, altrimenti li stima in proporzione alla lunghezza."""
    n = len(words)
    speak = max(d - PAD, 0.3)
    if bounds and len(bounds) == n:
        return [(min(s, speak), min(max(e, s + 0.05), d)) for s, e in bounds]
    if bounds:
        s0, s1 = bounds[0][0], max(b[1] for b in bounds)
    else:
        s0, s1 = 0.0, speak
    if s1 <= s0:
        s0, s1 = 0.0, speak
    weights = [len(w) + 1 for w in words]
    wsum = sum(weights)
    out, cur = [], s0
    for w in weights:
        span = (s1 - s0) * w / wsum
        out.append((cur, cur + span))
        cur += span
    return out


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


def cycle_starts(total):
    """Inizio di ogni ciclo del pulsante SUBSCRIBE (ogni 6,4 secondi)."""
    out, t = [], 0.6
    while t < total - 0.8:
        out.append(t)
        t += 6.4
    return out


def subscribe_events(total):
    """Pulsante SUBSCRIBE in alto: pulsa, viene 'premuto' e diventa SUBSCRIBED, poi ricomincia."""
    cx, cy = 540, 255
    W, H, R = 440, 100, 26
    shape = (f"m {R} 0 l {W - R} 0 b {W - R // 2} 0 {W} {R // 2} {W} {R} l {W} {H - R} "
             f"b {W} {H - R // 2} {W - R // 2} {H} {W - R} {H} l {R} {H} "
             f"b {R // 2} {H} 0 {H - R // 2} 0 {H - R} l 0 {R} b 0 {R // 2} {R // 2} 0 {R} 0")
    bs = chr(92)  # backslash

    def tags(*parts):
        return "{" + "".join(bs + p for p in parts) + "}"

    pulse = ("t(0,200," + bs + "fscx108" + bs + "fscy108)" + bs + "t(200,350," + bs + "fscx100" + bs + "fscy100)")
    pulse2 = ("t(1800,1950," + bs + "fscx107" + bs + "fscy107)" + bs + "t(1950,2100," + bs + "fscx100" + bs + "fscy100)")
    press = "t(3300,3450," + bs + "fscx92" + bs + "fscy92)"
    pop = ("t(0,160," + bs + "fscx110" + bs + "fscy110)" + bs + "t(160,300," + bs + "fscx100" + bs + "fscy100)")
    ev = []
    for t in cycle_starts(total):
        a_end = min(t + 3.6, total)
        b_end = min(t + 5.6, total)
        base_in = ["an5", f"pos({cx},{cy})"]
        # fase 1: pulsante rosso SUBSCRIBE
        red = tags(*base_in, "bord0", "shad0", "1c&H0000FF&", "fad(120,0)", "fscx70", "fscy70",
                   pulse, pulse2, press, "p1")
        txt = tags("an5", f"pos({cx},{cy - 3})", "fad(120,0)", "fscx70", "fscy70", pulse, pulse2, press)
        ev.append(f"Dialogue: 2,{ts(t)},{ts(a_end)},Btn,,0,0,0,,{red}{shape}{tags('p0')}")
        ev.append(f"Dialogue: 3,{ts(t)},{ts(a_end)},Btn,,0,0,0,,{txt}SUBSCRIBE")
        # fase 2: pulsante grigio SUBSCRIBED con spunta
        if a_end < total - 0.3:
            gray = tags(*base_in, "bord0", "shad0", "1c&H555555&", "fscx92", "fscy92", pop, "p1")
            txt2 = tags("an5", f"pos({cx},{cy - 3})", "fscx92", "fscy92", pop)
            ev.append(f"Dialogue: 2,{ts(a_end)},{ts(b_end)},Btn,,0,0,0,,{gray}{shape}{tags('p0')}")
            ev.append(f"Dialogue: 3,{ts(a_end)},{ts(b_end)},Btn,,0,0,0,,{txt2}SUBSCRIBED \u2713")
    return ev


def make_ass(script, durs, all_bounds):
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,DejaVu Sans,78,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,7,2,2,70,70,330,1
Style: Brand,DejaVu Sans,44,&H0000D7FF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,2,0,1,5,2,8,60,60,95,1
Style: Hook,DejaVu Sans,86,&H0000FFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,8,2,8,70,70,370,1
Style: Btn,DejaVu Sans,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    total = sum(durs)
    bs = chr(92)
    lines = [f"Dialogue: 0,{ts(0)},{ts(total)},Brand,,0,0,0,,PRESS PLAY DAILY"]
    hook = clean_text(script.get("hook", "")).upper()
    if hook:
        pop = ("{" + bs + "fad(90,250)" + bs + "fscx55" + bs + "fscy55" + bs + "t(0,170," + bs + "fscx110" + bs +
               "fscy110)" + bs + "t(170,320," + bs + "fscx100" + bs + "fscy100)}")
        lines.append(f"Dialogue: 1,{ts(0)},{ts(min(3.2, total))},Hook,,0,0,0,,{pop}{hook}")
    # barra di avanzamento dorata sopra il video
    ms = int(total * 1000)
    bar = ("{" + bs + "an7" + bs + "pos(0,638)" + bs + "bord0" + bs + "shad0" + bs + "1c&H00D7FF&" + bs +
           "fscx1" + bs + f"t(0,{ms}," + bs + "fscx100)" + bs + "p1}m 0 0 l 1080 0 l 1080 14 l 0 14{" + bs + "p0}")
    lines.append(f"Dialogue: 1,{ts(0)},{ts(total)},Btn,,0,0,0,,{bar}")
    lines += subscribe_events(total)
    yellow = "{" + bs + "1c&H00FFFF&}"
    white = "{" + bs + "1c&HFFFFFF&}"
    t = 0.0
    for sentence, d, bounds in zip(script["sentences"], durs, all_bounds):
        words = clean_text(sentence).split()
        if not words:
            t += d
            continue
        wt = word_times(words, bounds, d)
        idx = list(range(len(words)))
        chunks = [idx[i:i + 4] for i in range(0, len(idx), 4)]
        if len(chunks) > 1 and len(chunks[-1]) == 1:
            last = chunks.pop()
            chunks[-1] = chunks[-1] + last
        for ci, ch in enumerate(chunks):
            next_start = wt[chunks[ci + 1][0]][0] if ci + 1 < len(chunks) else None
            for k, wi in enumerate(ch):
                st = t + wt[wi][0]
                if k + 1 < len(ch):
                    en = t + wt[ch[k + 1]][0]
                elif next_start is not None:
                    en = t + next_start
                else:
                    en = t + min(wt[wi][1] + 0.15, d)
                if en - st < 0.05:
                    en = st + 0.05
                parts = []
                for wj in ch:
                    w = words[wj].upper()
                    parts.append(yellow + w + white if wj == wi else w)
                lines.append(f"Dialogue: 0,{ts(st)},{ts(en)},Cap,,0,0,0,,{' '.join(parts)}")
        t += d
    with open(f"{OUT}/subs.ass", "w", encoding="utf-8") as f:
        f.write(header + "\n".join(lines) + "\n")


# ----------------------------------------------------------------- EFFETTI SONORI
SFX_SOUNDS = {
    "whoosh": ("anoisesrc=d=0.45:c=pink:r=44100,highpass=f=400,lowpass=f=5000,"
               "afade=t=in:d=0.22,afade=t=out:st=0.22:d=0.23"),
    "pop": "sine=f=760:d=0.14:r=44100,afade=t=out:d=0.14",
    "click": "sine=f=1500:d=0.05:r=44100,afade=t=out:d=0.05",
}
SFX_GAIN = {"whoosh": 0.30, "pop": 0.45, "click": 0.40}


def make_sfx(total, cut_times, click_times):
    """Crea out/sfx.wav: pop all'apertura, whoosh sui cambi scena, click quando il
    pulsante SUBSCRIBE viene premuto. Restituisce True se riuscito."""
    try:
        for name, expr in SFX_SOUNDS.items():
            run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", expr,
                 "-ac", "1", "-ar", "44100", f"{OUT}/{name}.wav"])
        events = {"pop": [0.03], "whoosh": [max(t - 0.06, 0.05) for t in cut_times],
                  "click": list(click_times)}
        order = ["whoosh", "pop", "click"]
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-t", f"{total:.2f}",
               "-i", "anullsrc=r=44100:cl=mono"]
        for n in order:
            cmd += ["-i", f"{n}.wav"]
        parts, labels = [], ["[0:a]"]
        for i, n in enumerate(order, start=1):
            times = [t for t in events[n] if 0 <= t < total - 0.1]
            if not times:
                continue
            outs = "".join(f"[{n}{j}]" for j in range(len(times)))
            parts.append(f"[{i}:a]asplit={len(times)}{outs}" if len(times) > 1
                         else f"[{i}:a]anull[{n}0]")
            for j, t in enumerate(times):
                ms = int(t * 1000)
                parts.append(f"[{n}{j}]adelay={ms}|{ms},volume={SFX_GAIN[n]}[d{n}{j}]")
                labels.append(f"[d{n}{j}]")
        parts.append("".join(labels) + f"amix=inputs={len(labels)}:duration=first:normalize=0[sfx]")
        cmd += ["-filter_complex", ";".join(parts), "-map", "[sfx]", "-ac", "1", "sfx.wav"]
        r = subprocess.run(cmd, cwd=OUT, capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            log("Effetti sonori non creati:", r.stderr[-600:])
            return False
        return True
    except Exception as e:
        log("Effetti sonori non creati:", str(e)[:200])
        return False


# ----------------------------------------------------------------- MUSICA + MIX FINALE
def find_music():
    files = sorted(glob.glob("music*.mp3"))
    if not files:
        return None
    return os.path.abspath(random.choice(files))


def final_render(total, music, has_sfx):
    base = ["ffmpeg", "-y", "-loglevel", "error", "-i", "joined.mp4", "-i", "voice.wav"]
    tail = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-c:a", "aac",
            "-b:a", "160k", "-shortest", "-movflags", "+faststart", "short.mp4"]
    attempts = []
    if music or has_sfx:
        cmd = list(base)
        parts = ["[0:v]ass=subs.ass[v]"]
        labels = ["[1:a]"]
        idx = 2
        if music:
            mdur = duration(music)
            off = random.uniform(0, mdur - total - 2) if mdur > total + 4 else 0.0
            cmd += ["-stream_loop", "-1", "-ss", f"{off:.2f}", "-i", music]
            parts.append(f"[{idx}:a]volume={MUSIC_VOL},afade=t=in:d=1,"
                         f"afade=t=out:st={max(total - 1.5, 0):.2f}:d=1.5[m]")
            labels.append("[m]")
            idx += 1
        if has_sfx:
            cmd += ["-i", "sfx.wav"]
            labels.append(f"[{idx}:a]")
            idx += 1
        parts.append("".join(labels) + f"amix=inputs={len(labels)}:duration=first:"
                     "dropout_transition=0:normalize=0,alimiter=limit=0.95[a]")
        attempts.append((cmd + ["-filter_complex", ";".join(parts), "-map", "[v]", "-map", "[a]"] + tail,
                         True))
    attempts.append((base + ["-filter_complex", "[0:v]ass=subs.ass[v]", "-map", "[v]", "-map", "1:a"] + tail,
                     False))
    for cmd, rich in attempts:
        r = subprocess.run(cmd, cwd=OUT, capture_output=True, text=True, timeout=900)
        if r.returncode == 0:
            if rich:
                log("Audio: voce" + (" + musica " + os.path.basename(music) if music else "")
                    + (" + effetti sonori" if has_sfx else ""))
            return
        log("Montaggio finale fallito" + (" con musica/effetti, riprovo senza" if rich else ""))
        log(r.stderr[-1500:])
    sys.exit("ERRORE nel montaggio finale")


# ----------------------------------------------------------------- MAIN
def main():
    with open(f"{OUT}/script.json", encoding="utf-8") as f:
        script = json.load(f)

    log("1/5 Genero la voce...")
    durs, all_bounds = make_voice(script["sentences"])
    total = sum(durs)
    log(f"Durata voce: {total:.1f}s")

    log("2/5 Scarico i video...")
    trailers = get_trailers(script["movies"], total)

    log("3/5 Preparo le clip verticali...")
    cuts = plan_cuts(trailers, total)
    render_segments(cuts)

    log("4/5 Sottotitoli ed effetti sonori...")
    make_ass(script, durs, all_bounds)
    acc, cut_times = 0.0, []
    for _, _, c in cuts[:-1]:
        acc += c
        cut_times.append(acc)
    click_times = [t + 3.45 for t in cycle_starts(total)]
    has_sfx = make_sfx(total, cut_times, click_times)

    log("5/5 Montaggio finale...")
    final_render(total, find_music(), has_sfx)
    log(f"FATTO: {OUT}/short.mp4, {duration(OUT + '/short.mp4'):.1f}s")


if __name__ == "__main__":
    main()
