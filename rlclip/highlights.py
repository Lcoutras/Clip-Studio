"""Turns raw events into ranked, non-overlapping highlight windows."""

from dataclasses import asdict, dataclass, field

from .detect import Event


@dataclass
class Highlight:
    index: int
    start: float
    end: float
    anchor: float        # the moment of the play (seconds into the recording)
    label: str
    score: float
    mood: str
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


def build(ocr_events, peaks, markers, det, duration):
    events = []
    for e in ocr_events:
        e.time, jump_db = _refine_with_audio(e, peaks)
        e.weight += min(2.0, max(0.0, (jump_db - 6) / 4))
        events.append(e)

    # Loud moments with no popup (crowd roar, a crazy flick) count as low-weight "HYPE".
    covered = [e.time for e in events]
    for t, jump_db in peaks:
        if all(abs(t - c) > 4 for c in covered):
            weight = min(6.0, 2 + (jump_db - det["audio_peak_db"]) / 2)
            events.append(Event(t, "HYPE", weight, "hype", "audio", f"+{jump_db:.0f}dB"))
    events += markers
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
        rest = sum(e.weight for e in group) - best.weight
        score = round(best.weight + 0.3 * rest, 2)
        # Only the moments that matter set the window; a stray audio blip shouldn't add 8s of lead-in.
        span = [e for e in group if e.weight >= best.weight * 0.5]
        start = min(e.time for e in span) - det["pre_seconds"]
        end = max(e.time for e in span) + det["post_seconds"]
        # Keep long clusters watchable: centre on the best moment.
        if end - start > det["max_clip_seconds"]:
            start = best.time - det["max_clip_seconds"] * 0.7
            end = start + det["max_clip_seconds"]
        start, end = max(0.0, start), min(duration, end)
        highlights.append(Highlight(0, round(start, 2), round(end, 2), round(best.time, 2),
                                    best.label, score, best.mood, group))

    picked = [h for h in highlights if h.score >= det["min_score"]]
    picked.sort(key=lambda h: h.score, reverse=True)
    picked = picked[: det["max_clips_per_video"]]
    picked.sort(key=lambda h: h.start)
    for i, h in enumerate(picked, 1):
        h.index = i
    return picked
