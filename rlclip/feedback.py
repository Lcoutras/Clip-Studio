"""Learns from the clips you reject (and the ones you export) so later analyses suggest better clips.

Everything stays on this PC in output/feedback.json, which updates never touch:
- a kind of clip you keep rejecting (say, voice reactions on their own) is ranked lower,
- screens you reject as "not gameplay" (a menu, a loading screen) are recognized and skipped,
- the way you trim exported clips (more lead-in, a longer ending) becomes the default for that type.
"""

import json
import statistics
import threading
import time
from pathlib import Path

REASONS = {
    "not_gameplay": "Not gameplay",
    "not_mine": "Not my play",
    "boring": "Nothing happened",
    "timing": "Bad timing",
    "duplicate": "Duplicate",
    "other": "Other",
}
# Rejections that say the clip shouldn't have been suggested at all. "Bad timing" and
# "Duplicate" are about the trim or a repeat, so they don't count against that kind of clip.
AGAINST_SIGNAL = {"", "not_gameplay", "not_mine", "boring", "other"}
MIN_REJECTS = 3        # rejections of one kind of clip before it's ranked lower
MIN_TRIMS = 3          # exported clips of one category before its timing changes
MAX_PENALTY = 4.0      # points; the bar for a clip is min_score (5)
SCREEN_SIZE = (32, 18)
SCREEN_DIFF = 10       # mean difference (0-255) under which two thumbnails are "the same screen"

_lock = threading.Lock()


def signal_of(events):
    """What made a clip: 'reaction', 'popup:GOAL', 'replay:EPIC SAVE'... ('' for your own clips)."""
    def get(e, key):
        return e.get(key) if isinstance(e, dict) else getattr(e, key, None)
    if not events:
        return ""
    best = max(events, key=lambda e: get(e, "weight") or 0)
    source, label = get(best, "source"), get(best, "label")
    if source == "reaction":
        return "reaction (mixed audio)" if "mixed" in (get(best, "detail") or "") else "reaction"
    if source == "ocr":
        return f"popup:{label}"
    if source == "replay":
        return f"replay:{label}"
    return ""   # markers and clips you added are never second-guessed


def describe(signal):
    if signal.startswith("reaction"):
        return "voice reactions on their own" + (" (mixed audio)" if "mixed" in signal else "")
    kind, _, label = signal.partition(":")
    if label == "FAIL?":
        return "other players' goals"
    label = label.title().replace("Mvp", "MVP")
    return f"{label} popups" if kind == "popup" else f"{label}s from replays"


class Feedback:
    def __init__(self, path):
        self.path = Path(path)
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("clips", {})      # "video|anchor" -> verdict record
        self.data.setdefault("screens", [])    # thumbnails of clips rejected as not gameplay

    def save(self):
        with _lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
            tmp.replace(self.path)

    def key(self, video, anchor):
        """The record for the clip at this moment; re-analysis can move a clip by a second or two."""
        name, anchor = Path(video).name, float(anchor)
        near = [k for k in self.data["clips"]
                if k.rsplit("|", 1)[0] == name and abs(float(k.rsplit("|", 1)[1]) - anchor) <= 3]
        if near:
            return min(near, key=lambda k: abs(float(k.rsplit("|", 1)[1]) - anchor))
        return f"{name}|{anchor:.1f}"

    # ------------------------------------------------------------ recording verdicts
    def reject(self, video, row, highlight, reason="", screens=None):
        key = self.key(video, row["anchor"])
        old = self.data["clips"].get(key, {})
        self.data["clips"][key] = {
            "verdict": "reject", "reason": reason if reason in REASONS else "",
            "note": row.get("note") or "", "category": row.get("category") or "",
            "label": row.get("label") or "", "signal": signal_of((highlight or {}).get("events")),
            "score": (highlight or {}).get("score"), "at": time.strftime("%Y-%m-%d %H:%M"),
        }
        self.data["screens"] = [s for s in self.data["screens"] if s["key"] != key]
        if screens and reason == "not_gameplay":
            self.data["screens"].append({"key": key, "frames": [f.hex() for f in screens]})
        if old != self.data["clips"][key]:
            self.save()

    def unreject(self, video, row):
        key = self.key(video, row["anchor"])
        if self.data["clips"].get(key, {}).get("verdict") == "reject":
            del self.data["clips"][key]
            self.data["screens"] = [s for s in self.data["screens"] if s["key"] != key]
            self.save()

    def keep(self, video, row, highlight):
        """An exported clip: a good suggestion. Its trim shows how you like that type timed."""
        key = self.key(video, row["anchor"])
        rec = {"verdict": "keep", "category": row.get("category") or "", "label": row.get("label") or "",
               "signal": signal_of((highlight or {}).get("events")), "at": time.strftime("%Y-%m-%d %H:%M")}
        if highlight and row.get("source") == "auto":
            rec["start_delta"] = round(float(row["start"]) - float(highlight["start"]), 2)
            rec["end_delta"] = round(float(row["end"]) - float(highlight["end"]), 2)
        self.data["clips"][key] = rec

    # ------------------------------------------------------------ what has been learned
    def counts(self):
        out = {}
        for rec in self.data["clips"].values():
            if not rec.get("signal"):
                continue
            c = out.setdefault(rec["signal"], {"reject": 0, "keep": 0})
            if rec["verdict"] == "keep":
                c["keep"] += 1
            elif rec.get("reason", "") in AGAINST_SIGNAL:
                c["reject"] += 1
        return out

    def learned(self):
        penalty = {}
        for signal, c in self.counts().items():
            total = c["reject"] + c["keep"]
            rate = c["reject"] / total if total else 0
            if c["reject"] >= MIN_REJECTS and rate > 0.5:
                penalty[signal] = round(min(MAX_PENALTY, MAX_PENALTY * 2 * (rate - 0.5) + 1), 1)
        timing = {}
        by_cat = {}
        for rec in self.data["clips"].values():
            if rec["verdict"] == "keep" and "start_delta" in rec:
                by_cat.setdefault(rec["category"], []).append((rec["start_delta"], rec["end_delta"]))
        for cat, deltas in by_cat.items():
            if len(deltas) >= MIN_TRIMS:
                s = statistics.median(d[0] for d in deltas)
                e = statistics.median(d[1] for d in deltas)
                s = max(-3.0, min(3.0, s)) if abs(s) >= 0.5 else 0.0
                e = max(-3.0, min(3.0, e)) if abs(e) >= 0.5 else 0.0
                if s or e:
                    timing[cat] = (round(s, 1), round(e, 1))
        screens = [[bytes.fromhex(f) for f in s["frames"]] for s in self.data["screens"]]
        return {"penalty": penalty, "timing": timing, "screens": screens}

    def rules(self):
        """What the app does differently because of your feedback, in plain words."""
        lines = []
        counts = self.counts()
        learned = self.learned()
        for signal, pts in sorted(learned["penalty"].items(), key=lambda x: -x[1]):
            c = counts[signal]
            lines.append(f"Ranking {describe(signal)} lower (−{pts:g}): you rejected {c['reject']} "
                         f"of {c['reject'] + c['keep']}.")
        if learned["screens"]:
            n = len(learned["screens"])
            lines.append(f"Skipping moments that look like the {n} screen{'s' if n != 1 else ''} "
                         "you rejected as not gameplay.")
        for cat, (s, e) in sorted(learned["timing"].items()):
            parts = []
            if s:
                parts.append(f"start {abs(s):g}s {'later' if s > 0 else 'earlier'}")
            if e:
                parts.append(f"end {abs(e):g}s {'later' if e > 0 else 'earlier'}")
            lines.append(f"{cat} clips {' and '.join(parts)}, like you trim them.")
        return lines

    def summary(self):
        recs = list(self.data["clips"].values())
        rejects = [r for r in recs if r["verdict"] == "reject"]
        reasons = {}
        for r in rejects:
            label = REASONS.get(r.get("reason"), "No reason given")
            reasons[label] = reasons.get(label, 0) + 1
        return {"rejected": len(rejects), "kept": len(recs) - len(rejects), "reasons": reasons,
                "rules": self.rules()}

    def report(self):
        """A plain-text report to share (e.g. with Claude) so the built-in defaults can improve."""
        s = self.summary()
        lines = ["Clip Studio feedback report", time.strftime("%Y-%m-%d %H:%M"), "",
                 f"Rejected clips: {s['rejected']}   Exported clips: {s['kept']}", "", "Reject reasons:"]
        lines += [f"  {k}: {v}" for k, v in sorted(s["reasons"].items(), key=lambda x: -x[1])] or ["  (none)"]
        lines += ["", "By kind of clip (rejected / exported):"]
        for signal, c in sorted(self.counts().items(), key=lambda x: -x[1]["reject"]):
            lines.append(f"  {describe(signal)}: {c['reject']} / {c['keep']}")
        lines += ["", "What the app learned:"] + [f"  {r}" for r in s["rules"] or ["(nothing yet)"]]
        lines += ["", "Rejected clips:"]
        for key, r in self.data["clips"].items():
            if r["verdict"] == "reject":
                video, anchor = key.rsplit("|", 1)
                lines.append(f"  {video} @ {float(anchor):.0f}s  {r['category']} ({describe(r['signal']) if r['signal'] else 'added by you'})"
                             f"  -> {REASONS.get(r.get('reason'), 'no reason')}" + (f": {r['note']}" if r.get("note") else ""))
        return "\n".join(lines) + "\n"

    def reset(self):
        self.data = {"clips": {}, "screens": []}
        self.save()


# ------------------------------------------------------------ screens
def thumbnail(video, t):
    """A tiny grayscale picture of the frame at t seconds, for recognizing a screen again."""
    from . import ffmpeg
    w, h = SCREEN_SIZE
    proc = ffmpeg.pipe(["-ss", f"{max(0.0, t):.2f}", "-i", str(video), "-frames:v", "1", "-an",
                        "-vf", f"scale={w}:{h},format=gray", "-f", "rawvideo", "-"])
    raw = proc.stdout.read()
    proc.wait()
    return raw if len(raw) == w * h else None


def clip_thumbnails(video, start, anchor, end):
    times = [start + 1, anchor, end - 1] if end - start > 3 else [anchor]
    return [t for t in (thumbnail(video, x) for x in times) if t]


def same_screen(a, b):
    import numpy as np
    if not a or not b or len(a) != len(b):
        return False
    diff = np.abs(np.frombuffer(a, np.uint8).astype(np.int16) - np.frombuffer(b, np.uint8).astype(np.int16))
    return float(diff.mean()) < SCREEN_DIFF


def looks_rejected(frames, screens):
    """Most of this clip's thumbnails match one screen you rejected as not gameplay."""
    if not frames:
        return False
    need = 1 if len(frames) == 1 else 2
    for rejected in screens:
        if sum(1 for f in frames if any(same_screen(f, r) for r in rejected)) >= need:
            return True
    return False
