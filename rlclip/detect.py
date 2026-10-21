"""Finds candidate moments in a recording.

Two signals are used:
  * On-screen text: Rocket League pops up "EPIC SAVE", "AERIAL GOAL", "DEMOLITION"...
    We sample frames, OCR the middle of the screen, and match those words.
  * Audio: goal explosions and crowd roars show up as sharp loudness spikes.
"""

import difflib
import re
import sys
from dataclasses import dataclass

import numpy as np

from . import ffmpeg

# keyword -> (label shown on clip, weight, music mood)
EVENTS = {
    "BICYCLE GOAL": ("BICYCLE GOAL", 10, "hype"),
    "BACKWARDS GOAL": ("BACKWARDS GOAL", 8, "funny"),
    "TURTLE GOAL": ("TURTLE GOAL", 9, "funny"),
    "OVERTIME GOAL": ("OVERTIME GOAL", 10, "hype"),
    "AERIAL GOAL": ("AERIAL GOAL", 8, "hype"),
    "POOL SHOT": ("POOL SHOT", 7, "hype"),
    "LONG GOAL": ("LONG GOAL", 7, "hype"),
    "SWISH": ("SWISH GOAL", 7, "hype"),
    "HAT TRICK": ("HAT TRICK", 9, "hype"),
    "EPIC SAVE": ("EPIC SAVE", 8, "hype"),
    "SAVIOR": ("SAVIOR", 7, "hype"),
    "EXTERMINATION": ("EXTERMINATION", 7, "funny"),
    "PLAYMAKER": ("PLAYMAKER", 6, "hype"),
    "GOAL": ("GOAL", 5, "hype"),
    "DEMOLITION": ("DEMO", 4, "funny"),
    "SAVE": ("SAVE", 4, "chill"),
    "ASSIST": ("ASSIST", 3, "chill"),
    "SHOT ON GOAL": ("SHOT", 1, "chill"),
}
# Longest first so "EPIC SAVE" wins over "SAVE" and "AERIAL GOAL" over "GOAL".
_KEYWORDS = sorted(EVENTS, key=len, reverse=True)


@dataclass
class Event:
    time: float          # seconds into the recording, best guess of the actual moment
    label: str
    weight: float
    mood: str
    source: str          # "ocr", "audio" or "marker"
    detail: str = ""


def _normalize(text):
    return re.sub(r"[^A-Z ]", "", text.upper().replace("!", " ")).strip()


def match_keywords(texts):
    """Stat-feed keywords found in a frame's OCR lines (given in reading order).

    OCR often splits one popup into several lines ("EPIC" / "SAVE") and misreads a
    letter now and then ("EPICS SAVE"), so lines are joined and long keywords are
    matched fuzzily. Each piece of text is only used for one keyword.
    """
    squashed = _normalize(" ".join(texts)).replace(" ", "")
    found = []
    for kw in _KEYWORDS:
        target = kw.replace(" ", "")
        while target in squashed:
            found.append(kw)
            squashed = squashed.replace(target, "#" * len(target), 1)
        if len(target) < 7:
            continue  # short words like SAVE / GOAL must match exactly
        for size in (len(target), len(target) + 1, len(target) - 1):
            for i in range(0, len(squashed) - size + 1):
                window = squashed[i:i + size]
                if "#" in window:
                    continue
                if difflib.SequenceMatcher(None, window, target).ratio() >= 0.85:
                    found.append(kw)
                    squashed = squashed[:i] + "#" * size + squashed[i + size:]
                    break
    return found


# Text that only shows up on menus, the scoreboard (Tab), the end-of-match awards
# screen, challenges and the save-replay box. Those screens are full of words like
# GOALS / SAVES / "2EpicSaves", so a frame containing any of these is never a play.
MENU_MARKERS = ("SCOREBOARD", "PING", "CHALLENGE", "REWARD", "CLAIM", "WINS:", "DIVISION",
                "REPLAY", "MVP", "PLAYLIST", "MATCHES", "CANCEL", "TIER")
_POINTS = re.compile(r"\+\s?\d{2,3}(?!\d)")
_DIGIT_LOOKALIKES = str.maketrans("01584", "OISBA")


def _fold(text):
    """Uppercase letters/digits only, with OCR look-alikes merged (P0STMATES85 == POSTMATES85)."""
    return re.sub(r"[^A-Z0-9]", "", text.upper()).translate(_DIGIT_LOOKALIKES)


def classify_frame(texts, player_names=()):
    """Return [(keyword, is_mine)] for real in-game stat popups in one frame's OCR lines.

    A play only counts when the frame shows your stat popup with its points
    ("EPIC SAVE +75", "GOAL +100") or the goal banner with your name ("POSTMATES85 SCORED!").
    Goals scored by anyone else are ignored.
    """
    if not texts or len(texts) > 12:
        return []
    raw = " ".join(texts).upper()
    if any(marker in raw for marker in MENU_MARKERS):
        return []
    folded = _fold(raw)
    names = [_fold(n) for n in player_names if n]
    mine = any(n in folded for n in names)
    scored = "SCORED" in folded
    points = bool(_POINTS.search(raw))
    accepted = []
    for kw in match_keywords(texts):
        is_goal = "GOAL" in kw and kw != "SHOT ON GOAL"
        if is_goal and scored and names and not mine:
            continue  # someone else's goal banner
        if points or (is_goal and scored and mine):
            accepted.append((kw, mine))
    return accepted


# ---------------------------------------------------------------- audio

def audio_peaks(path, track, threshold_db, hop=0.05):
    """Return [(onset_time, loudness_jump_db)] for spikes well above the surrounding level."""
    rate = 8000
    samples = ffmpeg.read_audio(path, track=track, rate=rate)
    if samples.size < rate * 10:
        return []
    n = int(rate * hop)
    frames = samples[: samples.size // n * n].reshape(-1, n)
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-5)
    # Smooth to ~0.25s so single clicks don't count.
    k = max(1, int(0.25 / hop))
    db = np.convolve(db, np.ones(k) / k, mode="same")

    # Rolling baseline: median level of the surrounding 30s, interpolated.
    block = int(30 / hop)
    centers, med = [], []
    for i in range(0, len(db), block // 2):
        seg = db[i:i + block]
        if seg.size > block // 4:
            centers.append(i + seg.size / 2)
            med.append(np.median(seg))
    if not centers:
        return []
    rise = db - np.interp(np.arange(len(db)), centers, med)
    # Ignore the first/last 2s (encoder start-up noise, recording cut-off).
    edge = int(2 / hop)
    rise[:edge] = rise[-edge:] = 0

    peaks = []
    min_gap = int(5 / hop)
    taken = np.zeros(len(rise), dtype=bool)
    for i in np.argsort(rise)[::-1]:
        if rise[i] < threshold_db:
            break
        if taken[max(0, i - min_gap): i + min_gap].any():
            continue
        taken[i] = True
        # Walk back to where the sound started (half-way up the jump): that is the hit.
        j = i
        while j > max(0, i - int(2 / hop)) and rise[j - 1] > rise[i] / 2:
            j -= 1
        peaks.append((j * hop, float(rise[i])))
    return sorted(peaks)


# ---------------------------------------------------------------- OCR

def _ocr_engine():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()


def _ocr_lines(engine, img):
    try:
        result, _ = engine(img, use_cls=False)
    except TypeError:
        result, _ = engine(img)
    lines = []
    for box, text, conf in result or []:
        ys = [pt[1] for pt in box]
        xs = [pt[0] for pt in box]
        lines.append((text, float(conf), (sum(ys) / len(ys), min(xs))))
    # Reading order: top to bottom (rows ~30px apart), then left to right.
    lines.sort(key=lambda l: (round(l[2][0] / 30), l[2][1]))
    return [(text, conf) for text, conf, _ in lines]


def _scan_geometry(info, region, width):
    x1, y1, x2, y2 = region
    cw = int(info.width * (x2 - x1)) // 2 * 2
    ch = int(info.height * (y2 - y1)) // 2 * 2
    cx, cy = int(info.width * x1), int(info.height * y1)
    ow = min(width, cw) // 2 * 2
    oh = int(round(ch * ow / cw / 2)) * 2
    return cw, ch, cx, cy, ow, oh


def ocr_events(info, det, progress=True, on_progress=None):
    """Scan the whole recording for stat-feed popups."""
    fps = det["ocr_fps"]
    cw, ch, cx, cy, ow, oh = _scan_geometry(info, det["ocr_region"], det["ocr_width"])
    vf = f"fps={fps},crop={cw}:{ch}:{cx}:{cy},scale={ow}:{oh}"
    proc = ffmpeg.pipe(["-i", str(info.path), "-an", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "bgr24", "-"])
    engine = _ocr_engine()
    frame_bytes = ow * oh * 3
    names = det.get("player_names") or []

    events, last_seen = [], {}
    i = 0
    while True:
        buf = proc.stdout.read(frame_bytes)
        if len(buf) < frame_bytes:
            break
        t = i / fps
        i += 1
        img = np.frombuffer(buf, dtype=np.uint8).reshape(oh, ow, 3)
        lines = _ocr_lines(engine, img)
        texts = [text for text, conf in lines if conf >= det["ocr_min_confidence"]]
        for kw, mine in classify_frame(texts, names):
            # A popup stays on screen for a few seconds: only count its first appearance.
            if t - last_seen.get(kw, -99) < 4:
                last_seen[kw] = t
                continue
            last_seen[kw] = t
            label, weight, mood = EVENTS[kw]
            if mine and "GOAL" in kw:
                weight += 2
            events.append(Event(max(0.0, t - det["ocr_delay"]), label, weight, mood, "ocr", " ".join(texts)))
        if on_progress and i % max(1, int(10 * fps)) == 0:
            on_progress(min(100.0, 100 * t / info.duration), len(events))
        if progress and i % int(60 * fps) == 0:
            pct = min(100, 100 * t / info.duration)
            sys.stdout.write(f"\r  scanning video... {pct:5.1f}%  ({len(events)} events)")
            sys.stdout.flush()
    proc.wait()
    if progress:
        sys.stdout.write(f"\r  scanning video... 100.0%  ({len(events)} events)\n")
    return events


def ocr_frame(info, det, at):
    """OCR one frame (used by the calibrate command). Returns (image, lines, crop box)."""
    cw, ch, cx, cy, ow, oh = _scan_geometry(info, det["ocr_region"], det["ocr_width"])
    proc = ffmpeg.pipe(["-ss", f"{at:.3f}", "-i", str(info.path), "-frames:v", "1",
                        "-f", "rawvideo", "-pix_fmt", "bgr24", "-"])
    buf = proc.stdout.read()
    proc.wait()
    full = np.frombuffer(buf, dtype=np.uint8).reshape(info.height, info.width, 3)
    import cv2
    crop = cv2.resize(full[cy:cy + ch, cx:cx + cw], (ow, oh))
    return full, _ocr_lines(_ocr_engine(), crop), (cx, cy, cw, ch)


# ---------------------------------------------------------------- markers

def parse_timestamp(text):
    parts = [float(p) for p in text.strip().split(":")]
    seconds = 0.0
    for p in parts:
        seconds = seconds * 60 + p
    return seconds


def read_markers(path):
    """Lines like '12:34' or '1:02:03 nasty flick' -> always-included events."""
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            stamp, _, note = line.partition(" ")
            try:
                t = parse_timestamp(stamp)
            except ValueError:
                print(f"  ! skipping marker line: {line}")
                continue
            events.append(Event(t, "HIGHLIGHT", 100, "hype", "marker", note.strip()))
    return events
