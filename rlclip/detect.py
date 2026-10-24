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
    "ASSIST": ("ASSIST", 6, "hype"),   # your pass set up a goal: a clip on its own
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
    source: str          # "ocr", "audio", "marker", "replay" or "reaction"
    detail: str = ""
    stat: str = ""       # a number worth showing on the clip, e.g. "107 KM/H"


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
# The button prompts on goal replays ("PRESS (X) TO SKIP", "PRESS (A) TO TOGGLE VIEW"). The replay shows
# the scorer's banner again a few seconds after the real goal; it isn't a new play. ("SCORED BY" isn't
# a marker: the live hat-trick popup says it too.)
REPLAY_MARKERS = ("TOSKIP", "TOGGLEVIEW")


def is_replay_frame(raw):
    compact = re.sub(r"[^A-Z]", "", raw.upper())
    return any(m in compact for m in REPLAY_MARKERS)


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
    if any(marker in raw for marker in MENU_MARKERS) or is_replay_frame(raw):
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


def other_scorer(texts, player_names=()):
    """True when the frame shows the goal banner for somebody else's goal (a possible fail)."""
    if not texts or len(texts) > 12 or not player_names:
        return False
    raw = " ".join(texts).upper()
    if any(marker in raw for marker in MENU_MARKERS) or is_replay_frame(raw):
        return False
    folded = _fold(raw)
    return "SCORED" in folded and not any(_fold(n) in folded for n in player_names if n)


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


# The match scoreboard at the top centre: your team's score box, the clock, the other team's box.
# It's only on screen during a live match (also in replays), never in menus, loading screens or the
# stadium intro. Fractions of the frame; the boxes are blue and orange, in either order.
SCOREBOARD = (0.38, 0.0, 0.62, 0.12)                  # strip that's scanned (x1, y1, x2, y2)
_BOX_Y = (0.10, 0.92)                                  # boxes' rows inside that strip
_BOX_LEFT, _BOX_RIGHT = (0.0625, 0.3125), (0.729, 0.979)


def scoreboard_visible(strip_bgr):
    """True when the strip shows a saturated blue box on one side and an orange one on the other.
    Measured on real frames: menus, loading screens and the stadium intro show no orange there."""
    h, w = strip_bgr.shape[:2]
    rows = strip_bgr[int(h * _BOX_Y[0]):int(h * _BOX_Y[1])].astype(np.int16)

    def colours(x0, x1):
        p = rows[:, int(w * x0):int(w * x1)]
        b, g, r = p[..., 0], p[..., 1], p[..., 2]
        blue = ((b > 150) & (r < 110) & (b > g + 60)).mean()
        orange = ((r > 170) & (r > g + 40) & (g > 50) & (b < 120)).mean()
        return blue, orange

    lb, lo = colours(*_BOX_LEFT)
    rb, ro = colours(*_BOX_RIGHT)
    return bool((lb > 0.15 and ro > 0.15) or (lo > 0.15 and rb > 0.15))


def ocr_events(info, det, progress=True, on_progress=None, timeline=None):
    """Scan the whole recording for stat-feed popups.

    If `timeline` is a list, (time, motion, scoreboard) is appended for every sample;
    see gameplay_checker().
    """
    fps = det["ocr_fps"]
    cw, ch, cx, cy, ow, oh = _scan_geometry(info, det["ocr_region"], det["ocr_width"])
    # The scoreboard strip is stacked under the text area so each frame is decoded only once.
    sx1, sy1, sx2, sy2 = SCOREBOARD
    sw, sh = int(info.width * (sx2 - sx1)) // 2 * 2, max(2, int(info.height * (sy2 - sy1)) // 2 * 2)
    sbh = max(2, int(round(sh * ow / sw / 2)) * 2)
    graph = (f"[0:v]fps={fps},split[a][b];[a]crop={cw}:{ch}:{cx}:{cy},scale={ow}:{oh}[t];"
             f"[b]crop={sw}:{sh}:{int(info.width * sx1)}:{int(info.height * sy1)},scale={ow}:{sbh}[s];"
             f"[t][s]vstack")
    proc = ffmpeg.pipe(["-i", str(info.path), "-an", "-filter_complex", graph,
                        "-f", "rawvideo", "-pix_fmt", "bgr24", "-"])
    engine = _ocr_engine()
    frame_bytes = ow * (oh + sbh) * 3
    names = det.get("player_names") or []

    events, last_seen = [], {}
    prev_thumb, prev_lines, skipped = None, [], 0
    i = 0
    while True:
        buf = proc.stdout.read(frame_bytes)
        if len(buf) < frame_bytes:
            break
        t = i / fps
        i += 1
        stacked = np.frombuffer(buf, dtype=np.uint8).reshape(oh + sbh, ow, 3)
        img, strip = stacked[:oh], stacked[oh:]
        # Menus, loading screens and pauses don't change between samples; an unchanged frame
        # reads the same, so reuse the last result instead of running OCR again.
        thumb = img[::8, ::8].astype(np.int16)
        if prev_thumb is not None and np.abs(thumb - prev_thumb).mean() < 1.5:
            lines = prev_lines
            skipped += 1
        else:
            lines = _ocr_lines(engine, img)
        if timeline is not None:
            # Motion per second, so Fast mode (a sample every 2s) uses the same scale.
            motion = 99.0 if prev_thumb is None else float(np.abs(thumb - prev_thumb).mean()) * fps
            timeline.append((t, motion, scoreboard_visible(strip)))
        prev_thumb, prev_lines = thumb, lines
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
        if other_scorer(texts, names) and t - last_seen.get("OTHER", -99) >= 8:
            last_seen["OTHER"] = t
            events.append(Event(max(0.0, t - det["ocr_delay"]), "FAIL?", 5, "funny", "ocr", " ".join(texts)))
        if on_progress and i % max(1, int(10 * fps)) == 0:
            try:
                on_progress(min(100.0, 100 * t / info.duration), len(events))
            except BaseException:
                # The caller cancelled: stop decoding right away.
                proc.kill()
                proc.wait()
                raise
        if progress and i % int(60 * fps) == 0:
            pct = min(100, 100 * t / info.duration)
            sys.stdout.write(f"\r  scanning video... {pct:5.1f}%  ({len(events)} events)")
            sys.stdout.flush()
    proc.wait()
    if progress:
        sys.stdout.write(f"\r  scanning video... 100.0%  ({len(events)} events, "
                         f"{skipped} unchanged frames skipped)\n")
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


def gameplay_checker(timeline):
    """Returns is_gameplay(t): True when the match scoreboard is on screen around t.

    Movement alone isn't enough: the stadium fly-over before kickoff moves as much as a match.
    If a recording never shows the scoreboard (too few sightings to trust), nothing counts as
    gameplay; is_gameplay.scoreboard_seen tells the caller so it can explain.
    """
    import bisect
    times = [entry[0] for entry in timeline]
    seen = sum(1 for entry in timeline if len(entry) > 2 and entry[2]) >= 3

    def is_gameplay(t):
        if not seen:
            return False
        lo, hi = bisect.bisect_left(times, t - 3), bisect.bisect_right(times, t + 1)
        window = timeline[lo:hi]
        return bool(window) and sum(1 for entry in window if entry[2]) * 2 > len(window)
    is_gameplay.scoreboard_seen = seen
    return is_gameplay


def track_loudness(path, track):
    """Overall loudness of one audio track in dB (about -90 means silent)."""
    samples = ffmpeg.read_audio(path, track=track, rate=2000)
    if samples.size == 0:
        return -120.0
    return float(20 * np.log10(np.sqrt((samples ** 2).mean()) + 1e-6))


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
