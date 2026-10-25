"""Story mode: a title, short beats timed to what happens in the play, and a payoff on the play.

suggest() drafts one from what Clip Studio detected in the clip: popups and replay events in the
build-up (shots, post hits, aerial touches, clears...) and loud hits in the game audio. Lines come
from captions/story.yaml (plus your own captions/my_story.yaml). Edit the result freely.
"""

import zlib

import numpy as np
import yaml

from . import config, ffmpeg

LIBRARY = "captions/story.yaml"
CUSTOM = "captions/my_story.yaml"
MAX_BEATS = 5
MIN_GAP = 1.2      # seconds between beats, so each one can be read


def library():
    with open(config.resolve(LIBRARY), encoding="utf-8") as f:
        lib = yaml.safe_load(f) or {}
    custom = config.resolve(CUSTOM)
    if custom.exists():
        with open(custom, encoding="utf-8") as f:
            for section, entries in (yaml.safe_load(f) or {}).items():
                for key, lines in (entries or {}).items():
                    pool = lib.setdefault(section, {}).setdefault(key, [])
                    lib[section][key] = list(lines or []) + [l for l in pool if l not in (lines or [])]
    return lib


def _pick(pool, seed, used):
    fresh = [l for l in pool if l not in used] or pool
    line = fresh[seed % len(fresh)]
    used.add(line)
    return line


def game_hits(video, track, start, end, rate=8000, jump_db=9.0):
    """Loud moments (ball hits, bumps) in the game audio between start and end: [(time, dB)]."""
    try:
        samples = ffmpeg.read_audio(video, track, rate=rate, start=max(0.0, start), duration=max(0.5, end - start))
    except Exception:
        return []
    hop = int(rate * 0.05)
    if samples.size < hop * 20:
        return []
    frames = samples[: samples.size // hop * hop].reshape(-1, hop)
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-6)
    hits, last = [], -9.0
    for i in range(10, len(db)):
        base = float(np.median(db[i - 10:i]))
        t = max(0.0, start) + i * 0.05
        if db[i] - base >= jump_db and t - last >= 0.6:
            hits.append((round(t, 2), round(float(db[i] - base), 1)))
            last = t
    return hits


def suggest(row, events=(), hits=(), seed_text=""):
    """A story for one clip. events: [{"time", "label"}] detected in the recording (recording
    seconds); hits: [(time, dB)] from the game audio. Returns {"title", "beats", "payoff"}."""
    lib = library()
    start, anchor = float(row["start"]), float(row["anchor"])
    category = {"Fail?": "Blooper/Fail"}.get(row.get("category"), row.get("category")) or "Goal"
    seed = zlib.crc32(f"{seed_text}|{row.get('clip')}".encode())
    used = set()
    build = sorted((e for e in events if start <= float(e["time"]) < anchor - 0.4
                    and e.get("label") in lib.get("events", {})), key=lambda e: float(e["time"]))
    posts = sum(1 for e in build if e["label"] == "POST")
    candidates = [(float(e["time"]), e["label"], 10.0) for e in build]
    for t, db in hits:   # loud hits the popups/replay don't already explain
        if start + 0.8 <= t < anchor - 0.6 and all(abs(t - c[0]) > 0.8 for c in candidates):
            candidates.append((t, "hit", float(db)))
    # Keep the most telling moments: detected events first, then the loudest hits; space them out.
    chosen = []
    for t, label, weight in sorted(candidates, key=lambda c: -c[2]):
        if len(chosen) < MAX_BEATS - 1 and all(abs(t - c[0]) >= MIN_GAP for c in chosen):
            chosen.append((t, label))
    chosen.sort()
    beats = []
    if not chosen or chosen[0][0] - start > 2.0:
        openers = lib.get("openers", {})
        beats.append({"at": round(start + 0.2, 2),
                      "text": _pick(openers.get(category) or openers.get("any") or ["Here we go..."], seed, used)})
    for i, (t, label) in enumerate(chosen):
        # A beat shows just before its moment, so it reads like a setup.
        at = max(start + 0.2, t - 0.5)
        if beats and at - beats[-1]["at"] < MIN_GAP:
            at = beats[-1]["at"] + MIN_GAP
        if at < anchor - 0.3:
            beats.append({"at": round(at, 2), "text": _pick(lib["events"][label], seed + i + 1, used)})
    payoffs, titles = lib.get("payoffs", {}), lib.get("titles", {})
    key = f"{category} after post" if posts and category == "Goal" else category
    payoff = _pick(payoffs.get(key) or payoffs.get(category) or payoffs.get("any"), seed, used)
    title = titles.get(key) or (titles.get("Aerial") if row.get("label") == "AERIAL GOAL" else None)
    return {"title": title[seed % len(title)] if title else "", "beats": beats, "payoff": payoff}
