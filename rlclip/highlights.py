"""Turns raw events into ranked, non-overlapping highlight windows."""

from dataclasses import asdict, dataclass, field

from .detect import Event
from .feedback import signal_of

CATEGORIES = ["Goal", "Aerial", "Save", "Demo", "Assist", "Post", "Funny Moment", "Fail?", "Manual"]


# The type a clip gets when its category is changed by hand (it sets the stamp and captions).
CATEGORY_LABELS = {"Goal": "GOAL", "Aerial": "AERIAL GOAL", "Save": "SAVE", "Demo": "DEMO", "Assist": "ASSIST",
                   "Post": "POST", "Funny Moment": "FUNNY MOMENT", "Fail?": "FAIL?", "Manual": "HIGHLIGHT"}
# Types that don't get a big stamp on the video (their caption does the talking).
NO_STAMP = ("HYPE", "HIGHLIGHT", "FUNNY MOMENT", "FAIL?")


def label_for(category, label):
    """Keep a more specific type that already fits the category (EPIC SAVE stays EPIC SAVE)."""
    return label if category_of(label) == category else CATEGORY_LABELS.get(category, label)


def category_of(label):
    if label == "AERIAL GOAL":
        return "Aerial"
    if label == "FAIL?":
        return "Fail?"
    if label == "POST":
        return "Post"
    if label == "FUNNY MOMENT":
        return "Funny Moment"
    if label == "AERIAL HIT":
        return "Aerial"
    if label in ("HIGHLIGHT", "MANUAL"):
        return "Manual"
    if "SAVE" in label or label == "SAVIOR":
        return "Save"
    if label in ("DEMO", "EXTERMINATION"):
        return "Demo"
    if label in ("ASSIST", "PLAYMAKER"):
        return "Assist"
    return "Goal"


@dataclass
class Highlight:
    index: int
    start: float
    end: float
    anchor: float        # the moment of the play (seconds into the recording)
    label: str
    score: float
    mood: str
    category: str = "Goal"
    reason: str = ""
    stat: str = ""
    signal: str = ""     # what made it a clip, for learning from your feedback
    events: list = field(default_factory=list)

    def to_dict(self):
        d = asdict(self)
        d["events"] = [asdict(e) if isinstance(e, Event) else e for e in self.events]
        return d


def _refine_with_audio(event, peaks):
    """OCR sees a popup up to a second late; snap to the goal-explosion spike if there is one."""
    # The popup appears just after the play, so only look slightly before/at the OCR hit.
    near = [(t, db) for t, db in peaks if event.time - 1.0 <= t <= event.time + 0.6]
    if not near:
        return event.time, 0.0
    return max(near, key=lambda p: p[1])


def _reason(group, reactions_db, mixed_audio=False):
    parts = []
    # Most important first; minor replay details only when nothing bigger happened.
    group = sorted(group, key=lambda e: -e.weight)
    big = any(e.source == "replay" and e.weight >= 4 for e in group)
    for e in group:
        if e.source == "replay":
            if not big or e.weight >= 4:
                parts.append(e.detail)
        elif e.source == "ocr" and e.label == "FAIL?":
            parts.append("someone else scored (could be a goal against you)")
        elif e.source == "ocr":
            parts.append(f"your {e.label} popup")
        elif e.source == "marker":
            parts.append(f"marked by you{': ' + e.detail if e.detail else ''}")
        elif e.source == "manual":
            parts.append("added by you")
    if any(e.weight > 0 and "explosion" in e.detail for e in group):
        parts.append("goal explosion")
    if reactions_db:
        where = "in the audio" if mixed_audio else "on the mic"
        parts.append(f"big reaction {where} (+{max(reactions_db):.0f} dB)")
    seen = []
    for p in parts:
        if p not in seen:
            seen.append(p)
    text = ", ".join(seen) or "loud moment"
    return text[0].upper() + text[1:]


def build(ocr_events, peaks, markers, det, duration, reactions=None, mixed_audio=False, is_gameplay=None,
          learned=None, veto=None, report=None):
    """reactions: [(time, jump_db)] from the mic track (or the mixed audio if mixed_audio).

    is_gameplay(t): whether a match is being played at t. Reactions and other players' goals
    outside gameplay (menus, lobby, loading screens) are ignored: shouting in the menu isn't a clip.
    learned: what your rejections and exports taught (see feedback.Feedback.learned).
    veto(highlight): True to skip a clip that would be picked (it looks like a screen you rejected).
    report: a dict that gets {"lowered": {signal: clips}, "vetoed": n} for the notes.
    """
    learned = learned or {}
    report = report if report is not None else {}
    report.setdefault("lowered", {})
    report.setdefault("vetoed", 0)
    is_gameplay = is_gameplay or (lambda t: True)
    reactions = list(reactions or [])
    ocr_events = [e for e in ocr_events if e.label != "FAIL?" or is_gameplay(e.time)]
    events = []
    for e in ocr_events:
        e.time, jump_db = _refine_with_audio(e, peaks)
        if jump_db:
            e.weight += min(2.0, max(0.0, (jump_db - 6) / 4))
            e.detail = (e.detail + " | goal explosion") if "GOAL" in e.label else e.detail
        events.append(e)
    events += markers

    # A reaction just after a play belongs to that play (it's the celebration, not a new clip).
    # The window also covers the replay after a goal, so replays never become separate clips.
    attached = {}
    standalone = []
    for t, db in reactions:
        owner = next((e for e in events if e.time - 2 <= t <= e.time + 12), None)
        if owner is not None:
            attached.setdefault(id(owner), []).append((t, db))
        else:
            standalone.append((t, db))
    # A reaction on its own only counts during a match (not in menus, loading screens or intros).
    # Reactions right after a play were kept above even if the goal replay hid the scoreboard.
    standalone = [(t, db) for t, db in standalone if is_gameplay(t)]
    # ...and only when something happened in the game just before it (a hit, explosion or demo
    # in the game audio). Laughing at a Discord joke or talking over a quiet moment isn't a clip.
    if not mixed_audio and det.get("reaction_needs_game_sound", True):
        standalone = [(t, db) for t, db in standalone if any(t - 4.0 <= pt <= t + 0.5 for pt, _ in peaks)]
    # A talkative session would otherwise be all reactions: keep the loudest few.
    per10 = det.get("max_reactions_per_10min", 3)
    if per10:
        cap = max(1, round(duration / 600 * per10))
        standalone = sorted(sorted(standalone, key=lambda r: -r[1])[:cap])
    for t, db in standalone:
        # With voices mixed into the game audio, a "reaction" may just be a loud game sound.
        weight = min(5.5 if mixed_audio else 7.0, 3 + (db - det["reaction_db"]) / 3)
        detail = f"+{db:.0f}dB" + (" (mixed audio)" if mixed_audio else "")
        events.append(Event(t, "FUNNY MOMENT", weight, "funny", "reaction", detail))
    events.sort(key=lambda e: e.time)

    # Group events that happen close together (shot -> save -> goal is one play).
    clusters = []
    for e in events:
        if clusters and e.time - clusters[-1][-1].time <= det["merge_gap"]:
            clusters[-1].append(e)
        else:
            clusters.append([e])

    highlights = []
    for group in clusters:
        best = max(group, key=lambda e: e.weight)
        # Extra popups in the same play (GOAL then HAT TRICK) add a little; repeats of one don't.
        others = {e.label: e.weight for e in group if e.label != best.label}
        reacts = [r for e in group for r in attached.get(id(e), [])]
        if best.source == "reaction":
            reacts.append((best.time, float(best.detail.split("dB")[0].lstrip("+"))))
        score = best.weight + 0.3 * sum(others.values()) + (1.0 if reacts and best.source != "reaction" else 0)
        signal = signal_of(group)
        penalty = learned.get("penalty", {}).get(signal, 0)
        if penalty:
            score -= penalty
            if penalty > 0:
                report["lowered"][signal] = report["lowered"].get(signal, 0) + 1
        category = category_of(best.label)
        # Only the moments that matter set the window; a stray blip shouldn't add lead-in.
        span = [e for e in group if e.weight >= best.weight * 0.5]
        # An assist's pass happens a few seconds before the goal it set up: show it.
        lead = det["pre_seconds"] + (2.0 if best.label in ("ASSIST", "PLAYMAKER") else 0.0)
        start = min(e.time for e in span) - lead
        end = max(e.time for e in span) + det["post_seconds"]
        if reacts:
            # Keep the reaction: end a moment after the last one.
            end = max(end, max(t for t, _ in reacts) + det["reaction_tail"])
        # Time it the way you trim this kind of clip, but never cut off the play itself.
        shift_start, shift_end = learned.get("timing", {}).get(category, (0, 0))
        start = min(start + shift_start, best.time - 1)
        end = max(end + shift_end, best.time + 1)
        if end - start > det["max_clip_seconds"]:
            start = max(start, best.time - det["max_clip_seconds"] * 0.6)
            end = start + det["max_clip_seconds"]
        start, end = max(0.0, start), min(duration, end)
        highlights.append(Highlight(
            0, round(start, 2), round(end, 2), round(best.time, 2), best.label, round(score, 2),
            best.mood, category, _reason(group, [db for _, db in reacts], mixed_audio),
            getattr(best, "stat", ""), signal, group))

    picked = []
    for h in sorted(highlights, key=lambda h: h.score, reverse=True):
        if h.score < det["min_score"] or len(picked) >= det["max_clips_per_video"]:
            continue
        if any(h.start < p.end and p.start < h.end for p in picked):
            continue  # overlaps a better clip
        if veto and h.signal and not h.signal.startswith("replay:") and veto(h):
            report["vetoed"] += 1
            continue
        picked.append(h)
    picked.sort(key=lambda h: h.start)
    for i, h in enumerate(picked, 1):
        h.index = i
    return picked
