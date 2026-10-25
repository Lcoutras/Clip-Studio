"""Ties detection, captions, music and rendering together for one recording."""

import copy
import csv
import datetime
import json
import os
import re
import shutil
from pathlib import Path

from . import captions, config, detect, feedback, ffmpeg, highlights, music, render, replay

# Bump when detection changes enough that old results should be thrown away.
DETECTION_VERSION = 12
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".flv", ".ts", ".avi", ".webm"}
CSV_FIELDS = ["clip", "label", "category", "score", "start", "end", "anchor", "tone", "caption",
              "alt_caption", "music", "skip", "reason", "source", "game_volume", "music_volume",
              "framing", "crop_x", "stat", "flag", "note", "reject_reason", "agent", "agent_note", "exports", "story"]

# Finished clips go to output/Exports/<date of export>/; Clip Studio's own working files (analysis,
# captions sheet, raw cuts) go to output/Analysis/<recording>/.
EXPORTS = "Exports"
ANALYSIS = "Analysis"


def exports_dir(cfg, day=None):
    return config.resolve(cfg["output_dir"]) / EXPORTS / (day or datetime.date.today().isoformat())


def _safe(text):
    """Text that's allowed in a Windows file name."""
    return re.sub(r"\s+", " ", re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", text or "")).strip(" .")


def recording_time(video):
    """'Oct 22 6.25 PM' from when the recording started (file names can't contain ':')."""
    start = replay.recording_start(video)
    if start is None:
        return _safe(Path(video).stem)
    return datetime.datetime.fromtimestamp(start).strftime("%b %d %I.%M %p").replace(" 0", " ")


# How each kind of clip is described when posted: search words first (TikTok and YouTube are
# searched like search engines), then a question that gets people commenting, then 5 hashtags.
POSTS = {
    "Goal": (["Rocket League goal", "Ranked Rocket League goal"],
             ["Rate this goal 1-10 👇", "Would you have scored that?", "Clean or lucky? 👇"],
             "#rocketleaguegoals"),
    "Assist": (["Rocket League assist", "Rocket League teamwork"],
               ["Pass of the game? 👇", "Who else passes like this?"], "#rocketleague3v3"),
    "Save": (["Rocket League save", "Rocket League defense"],
             ["Would you have saved that?", "Clutch or lucky? 👇"], "#rocketleaguesaves"),
    "Epic Save": (["Rocket League epic save", "Clutch Rocket League save"],
                  ["Would you have saved that?", "Save of the year? 👇"], "#rocketleaguesaves"),
    "Clear": (["Rocket League clear", "Rocket League defense"],
              ["Clutch or lucky? 👇", "Not in my net 🚫"], "#rocketleaguesaves"),
    "Center": (["Rocket League pass", "Rocket League center"],
               ["Who's finishing this? 👇", "Pass or shoot? 👇"], "#rocketleague3v3"),
    "Aerial": (["Rocket League aerial", "Rocket League mechanics"],
               ["How many hours to learn this? 👇", "Rate the air roll 1-10"], "#rocketleaguemechanics"),
    "Demo": (["Rocket League demo", "Rocket League demolition"],
             ["Deserved? 😂", "Bump or demo? 👇"], "#rocketleaguedemo"),
    "Post": (["Rocket League post hit", "Rocket League so close"],
             ["Post or goal next time? 😭", "Inches. INCHES. 😭"], "#rocketleaguefunny"),
    "Blooper/Fail": (["Rocket League fail", "Rocket League blooper"],
                     ["Be honest, would you have made it? 😂", "Tag the teammate who does this 😂"],
                     "#rocketleaguefails"),
    "Funny Moment": (["Rocket League funny moment", "Rocket League moment"],
                     ["Thoughts? 👇", "Tag your duo 😂"], "#rocketleaguefunny"),
}
POST_DEFAULT = (["Rocket League clip"], ["Thoughts? 👇"], "#rocketleagueclips")


def post_fields(row, video=""):
    """Everything a scheduler needs to post a clip: the TikTok description (hashtags included),
    the parts on their own, and a YouTube Shorts title. The wording is picked per clip but stays
    the same when the clip is exported again."""
    import zlib
    category = {"Fail?": "Blooper/Fail"}.get(row.get("category"), row.get("category")) or "Manual"
    hooks, questions, tag = POSTS.get(category, POST_DEFAULT)
    pick = zlib.crc32(f"{Path(video).name}|{row.get('clip')}".encode())
    hook, question = hooks[pick % len(hooks)], questions[(pick // 7) % len(questions)]
    caption = (row.get("caption") or "").strip()
    stat = f" ({row['stat'].lower()})" if row.get("stat") else ""
    tags = ["#rocketleague", tag, "#rlclips", "#gaming", "#fyp"]
    first = f"{caption} | {hook}{stat}" if caption else f"{hook}{stat}"
    description = f"{first}\n{question}\n\n{' '.join(tags)}"
    youtube = (f"{hook}: {caption}" if caption else hook)[:90] + " #shorts"
    return {"description": description, "caption": caption, "hashtags": " ".join(tags),
            "category": category, "youtube_title": youtube}


def post_text(row, video=""):
    return post_fields(row, video)["description"] + "\n"


POSTS_CSV = ["file", "file_name", "description", "caption", "hashtags", "category", "recording",
             "youtube_title", "suggested_time"]


def refresh_posts_csv(day_dir, cfg):
    """posts.csv in an export folder: one row per clip, for a scheduler's bulk upload. Rebuilt from
    the clips' .json files, so re-exports and deleted clips are always reflected."""
    day_dir = Path(day_dir)
    items = []
    for meta in sorted(day_dir.glob("*.json"), key=lambda p: p.stat().st_mtime):
        mp4 = meta.with_suffix(".mp4")
        if not mp4.exists():
            continue
        try:
            items.append((mp4, json.loads(meta.read_text(encoding="utf-8"))))
        except ValueError:
            continue
    # Suggested times: the slots in settings, starting the day after the export day.
    times = (cfg.get("schedule") or {}).get("times") or ["12:30", "19:30"]
    try:
        day = datetime.date.fromisoformat(day_dir.name) + datetime.timedelta(days=1)
    except ValueError:
        day = datetime.date.today() + datetime.timedelta(days=1)
    with open(day_dir / "posts.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=POSTS_CSV, extrasaction="ignore")
        w.writeheader()
        for i, (mp4, meta) in enumerate(items):
            slot = day + datetime.timedelta(days=i // len(times))
            w.writerow({**meta, "file": str(mp4), "file_name": mp4.name,
                        "suggested_time": f"{slot.isoformat()} {times[i % len(times)]}"})


def join_clips(parts, out, fps):
    """Join exported clips (in order) into one video. Parts may come from different recordings."""
    first = ffmpeg.probe(parts[0])
    args, graph = [], []
    for i, p in enumerate(parts):
        args += ["-i", p]
        # Make every part identical in size, pixel shape, frame rate and audio format.
        graph.append(f"[{i}:v:0]scale={first.width}:{first.height},setsar=1,fps={fps},format=yuv420p[v{i}];"
                     f"[{i}:a:0]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
    streams = "".join(f"[v{i}][a{i}]" for i in range(len(parts)))
    graph.append(f"{streams}concat=n={len(parts)}:v=1:a=1[v][a]")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    args += ["-filter_complex", ";".join(graph),
             "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out]
    ffmpeg.run(args)
    return Path(out)


def feedback_path(cfg):
    return config.resolve(cfg["output_dir"]) / "feedback.json"


def story_of(row, start=None):
    """The clip's story ({"beats": [{"at": seconds into the recording, "text"}], "payoff"}), with beat
    times made relative to the clip's start when start is given. None when it has none."""
    try:
        story = json.loads(row.get("story") or "null")
    except ValueError:
        return None
    if not isinstance(story, dict):
        return None
    beats = [b for b in story.get("beats") or [] if isinstance(b, dict) and str(b.get("text", "")).strip()]
    if start is not None:
        beats = [{**b, "at": float(b["at"]) - float(start)} for b in beats]
    if not beats and not str(story.get("payoff") or "").strip():
        return None
    return {"beats": beats, "payoff": str(story.get("payoff") or "")}


def is_skipped(row):
    return str(row.get("skip") or "").strip().lower() in ("y", "yes", "x", "1", "true")


def find_videos(target):
    target = Path(target)
    if target.is_dir():
        return sorted(p for p in target.iterdir() if p.suffix.lower() in VIDEO_EXT)
    return [target]


def _slug(text):
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").upper() or "CLIP"


def _workdir(out_dir, row):
    return out_dir / "work" / f"{int(row['clip']):02d}_{float(row['start']):.2f}_{float(row['end']):.2f}"


class Project:
    def __init__(self, video, cfg):
        self.video = Path(video)
        self.cfg = cfg
        self.out_root = config.resolve(cfg["output_dir"])
        self.out_dir = self.out_root / ANALYSIS / self.video.stem
        self.highlights_path = self.out_dir / "highlights.json"
        self.csv_path = self.out_dir / "captions.csv"
        self._info = None
        self._migrate_old_layout()

    def _migrate_old_layout(self):
        """Before 0.24 everything lived in output/<recording>/, with exports in vertical/ and
        horizontal/ next to the working files. Move exports into dated folders and the rest
        into Analysis/."""
        old = self.out_root / self.video.stem
        if self.out_dir.exists() or not ((old / "captions.csv").exists() or (old / "highlights.json").exists()):
            return
        try:
            moved = {}
            for layout in ("vertical", "horizontal"):
                for mp4 in sorted((old / layout).glob("*.mp4")):
                    day = datetime.date.fromtimestamp(mp4.stat().st_mtime).isoformat()
                    dest = exports_dir(self.cfg, day) / mp4.name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(mp4), str(dest))
                    if mp4.with_suffix(".txt").exists():
                        shutil.move(str(mp4.with_suffix(".txt")), str(dest.with_suffix(".txt")))
                    m = re.match(re.escape(self.video.stem) + r"_(\d+)_", mp4.name)
                    if m:
                        moved.setdefault(int(m.group(1)), []).append(f"{layout}={dest.relative_to(self.out_root).as_posix()}")
                shutil.rmtree(old / layout, ignore_errors=True)
            for mp4 in old.glob("*_HIGHLIGHTS_*.mp4"):
                day = datetime.date.fromtimestamp(mp4.stat().st_mtime).isoformat()
                exports_dir(self.cfg, day).mkdir(parents=True, exist_ok=True)
                shutil.move(str(mp4), str(exports_dir(self.cfg, day) / mp4.name))
            self.out_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(self.out_dir))
            if moved and self.csv_path.exists():
                rows = self.read_rows()
                for r in rows:
                    if int(r["clip"]) in moved:
                        r["exports"] = ";".join(moved[int(r["clip"])])
                self.write_rows(rows)
        except OSError as e:   # a file in use: leave things where they are and try again next time
            print(f"  ! couldn't reorganize {old}: {e}")

    @property
    def info(self):
        if self._info is None:
            self._info = ffmpeg.probe(self.video)
        return self._info

    # ------------------------------------------------------------ step 1
    def replay_events(self, ocr):
        """Events from Rocket League replays of matches in this recording.

        Returns (events, notes for the user, [(start, end)] recording spans the replays cover).
        """
        rcfg = self.cfg.get("replays", {})
        self._story_events = []   # 50/50s, post hits... (see replay.story_events), for story mode
        if not rcfg.get("enabled", True):
            return [], [], []
        info = self.info
        try:
            paired = [r for r, v in (rcfg.get("pairs") or {}).items() if Path(v) == self.video]
            found, start = replay.candidates(self.video, info.duration, rcfg.get("dirs", []),
                                             rcfg.get("files", []), forced=paired)
        except Exception as e:  # rrrocket missing or failing shouldn't stop the analysis
            return [], [f"Replays not used: {e}"], []
        if not found:
            sources = list(rcfg.get("files", [])) + [d for d in replay.replay_folders(rcfg.get("dirs", []))
                                                     if Path(d).is_dir()]
            when = f" (this recording started {replay.describe_time(start)})" if start else ""
            return [], ([f"No saved replay matches this recording{when}."] if sources else []), []
        names = self.cfg["detection"].get("player_names") or []
        video_goals = [e.time for e in ocr if "GOAL" in e.label or e.label == "FAIL?"]
        events, notes, spans = [], [], []
        for path, epoch in found:
            try:
                rep = replay.load(path)
            except Exception as e:
                notes.append(f"Couldn't read replay {path.name}: {e}")
                continue
            goals = [g.time for g in rep.events if g.label == "GOAL"]
            offset, hits = None, 0
            priors = [e - start for e in replay.match_starts(epoch)] if start else []
            if str(path) in {str(Path(r)) for r in paired}:
                priors.append(None)   # paired by hand: also try lining up by goals alone
            for prior in priors or [None]:
                o, h = replay.align(goals, video_goals, prior)
                if o is not None and 0 <= o + 30 and o < info.duration and (offset is None or h > hits):
                    offset, hits = o, h
            if offset is None:
                when = replay.describe_time(epoch) if epoch else rep.date
                notes.append(f"Replay {rep.name or path.name} ({when}) didn't line up with this recording")
                continue
            evs = replay.to_events(rep, offset, names, info.duration)
            try:
                self._story_events += replay.story_events(rep, offset, names, info.duration)
            except Exception as e:   # older replays can lack some data: stories are a bonus
                print(f"  story moments not read from {path.name}: {e}")
            if not evs:
                continue
            events += evs
            spans.append((max(0.0, offset), min(info.duration, offset + rep.duration)))
            how = f"{hits} goals matched on screen" if hits else "lined up by clock time"
            when = replay.describe_time(rep.match_start_epoch) if rep.match_start_epoch else rep.date
            notes.append(f"Used replay {rep.name or path.name} ({when}): {len(evs)} events, {how}")
        return events, notes, spans

    def notes(self):
        try:
            with open(self.highlights_path, encoding="utf-8") as f:
                return json.load(f).get("notes", [])
        except (OSError, ValueError, AttributeError):
            return []

    def audio_warnings(self, game_track):
        """Plain-language problems with the recording's audio tracks."""
        info = self.info
        if not info.audio_tracks:
            return ["This recording has no audio, so clips will only have music."]
        levels = [detect.track_loudness(self.video, t) for t in range(info.audio_tracks)]
        print("  audio tracks: " + ", ".join(f"track {i + 1}: {db:.0f} dB" for i, db in enumerate(levels)))
        if levels[game_track] < -60:
            if info.audio_tracks > 1:
                return [f"Audio track {game_track + 1} (the game sound) is silent in this recording, so "
                        "clips will have no game sound. In OBS, check Edit > Advanced Audio Properties: "
                        "the source that captures Rocket League needs Track 1 ticked."]
            return ["This recording's audio is silent, so clips will have no game sound."]
        return []

    def warnings(self):
        try:
            with open(self.highlights_path, encoding="utf-8") as f:
                return json.load(f).get("warnings", [])
        except (OSError, ValueError, AttributeError):
            return []

    def load_highlights(self):
        """Saved highlights, or None if missing or made by an older version of the detector."""
        try:
            with open(self.highlights_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        if isinstance(data, dict) and data.get("version") == DETECTION_VERSION:
            return data["highlights"]
        return None

    def detect(self, rescan=False, on_progress=None):
        saved = None if rescan else self.load_highlights()
        if saved is not None:
            print(f"  using saved detection ({len(saved)} highlights) - pass --rescan to redo it")
            return saved
        if self.highlights_path.exists() and not rescan:
            print("  detection has been improved since this video was scanned - scanning again")
        det = self.cfg["detection"]
        info = self.info
        mins = info.duration / 60
        print(f"  {info.width}x{info.height} @ {info.fps:.0f}fps, {mins:.1f} min, "
              f"{info.audio_tracks} audio track(s)")
        track = self.cfg["audio"]["game_track"] if info.audio_tracks > 1 else 0
        peaks = detect.audio_peaks(self.video, track, det["audio_peak_db"]) if info.audio_tracks else []
        print(f"  audio: {len(peaks)} loud moments")
        if info.duration < 180 and det["ocr_fps"] < 1:
            det = {**det, "ocr_fps": 1.0}   # a short clip: Fast saves seconds and can miss a popup
        timeline = []
        ocr = detect.ocr_events(info, det, on_progress=on_progress, timeline=timeline)
        if det["ocr_fps"] < 1 and peaks:
            extra = detect.recheck_loud_moments(info, det, peaks, ocr)
            if extra:
                print(f"  fast mode: {len(extra)} popups found by re-checking loud moments")
            ocr = sorted(ocr + extra, key=lambda e: e.time)
        is_gameplay = detect.gameplay_checker(timeline)
        warnings = self.audio_warnings(track)
        scoreboard_note = [] if is_gameplay.scoreboard_seen or not det.get("reactions_alone") else [
            "The match scoreboard wasn't visible in this recording, so voice reactions on their own "
            "didn't become clips (a reaction right after a play still counts)."]
        # Reactions: your mic track when voices are recorded separately, otherwise loud moments
        # in the mixed audio that aren't the goal explosion itself.
        voice = self.cfg["audio"]["voice_track"]
        mixed = info.audio_tracks < 2 or voice >= info.audio_tracks or voice == track
        if not mixed:
            reactions = detect.audio_peaks(self.video, voice, det["reaction_db"])
        else:
            reactions = [(t, db) for t, db in peaks if db >= det["reaction_db"]
                         and all(abs(t - e.time) > 1.5 for e in ocr)]
        print(f"  reactions: {len(reactions)}" + (" (from mixed audio)" if mixed else " (from your mic)"))
        markers = []
        marker_file = self.video.with_suffix(".markers.txt")
        if marker_file.exists():
            markers = detect.read_markers(marker_file)
            print(f"  markers: {len(markers)} from {marker_file.name}")
        rep_events, notes, spans = self.replay_events(ocr)
        if rep_events:
            # The replay knows exactly what happened in its match: drop screen-reading guesses there.
            ocr = [e for e in ocr if not any(a - 5 <= e.time <= b + 5 for a, b in spans)] + rep_events
            reactions = [(t, db) for t, db in reactions if any(a <= t <= b for a, b in spans) or is_gameplay(t)]
        if info.height > info.width:
            scoreboard_note.insert(0, "This is already a vertical (edited) video. Clip Studio reads raw game "
                                      "recordings, where the popups are full size, so it may find little here. "
                                      "Import the original recording instead, or use + Add moment here to "
                                      "export this one as it is.")
        notes = scoreboard_note + notes
        for n in notes:
            print(f"  {n}")
        learned_notes = []
        fb = feedback.Feedback(feedback_path(self.cfg))
        learned = fb.learned()
        # Settings tuned from the Clip Agent's lessons apply on top of what your own feedback taught.
        for signal, pts in (det.get("signal_adjust") or {}).items():
            learned["penalty"][signal] = learned["penalty"].get(signal, 0) - float(pts)
        for cat, (s, e) in (det.get("timing_adjust") or {}).items():
            learned["timing"].setdefault(cat, (float(s), float(e)))
        veto = None
        if learned["screens"]:
            def veto(h):
                frames = feedback.clip_thumbnails(self.video, h.start, h.anchor, h.end)
                return feedback.looks_rejected(frames, learned["screens"])
        report = {}
        found = highlights.build(ocr, peaks, markers, det, info.duration, reactions, mixed, is_gameplay,
                                 learned=learned, veto=veto, report=report)
        if report.get("vetoed"):
            n = report["vetoed"]
            learned_notes.append(f"Learned from your feedback: skipped {n} moment{'s' if n != 1 else ''} that "
                                 "looked like screens you rejected as not gameplay.")
        for signal, n in report.get("lowered", {}).items():
            learned_notes.append(f"Learned from your feedback: ranked {feedback.describe(signal)} lower "
                                 f"({n} moment{'s' if n != 1 else ''}).")
        if learned["timing"]:
            learned_notes.append("Learned from your feedback: clips are timed the way you trim them ("
                                 + ", ".join(sorted(learned["timing"])) + ").")
        for n in learned_notes:
            print(f"  {n}")
        notes = notes + learned_notes
        data = [h.to_dict() for h in found]
        # Old clips and captions belong to the old detection; clear them only now, so a cancelled
        # or failed analysis leaves the previous results alone.
        for old in ("raw", "work", "previews"):   # exported clips (in Exports/) are kept
            shutil.rmtree(self.out_dir / old, ignore_errors=True)
        self._carry_flags = self.flagged()
        self.csv_path.unlink(missing_ok=True)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with open(self.highlights_path, "w", encoding="utf-8") as f:
            json.dump({"version": DETECTION_VERSION, "highlights": data, "warnings": warnings,
                       "notes": notes, "story_events": getattr(self, "_story_events", [])}, f, indent=1)
        for w in warnings:
            print(f"  ! {w}")
        print(f"  picked {len(data)} highlights")
        return data

    # ------------------------------------------------------------ step 2
    def story_events(self, start=None, end=None):
        """Story moments from replays (and the detector's own events) between start and end."""
        try:
            with open(self.highlights_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return []
        events = list(data.get("story_events") or [])
        events += [e for h in data.get("highlights", []) for e in h.get("events", [])]
        if start is not None:
            events = [e for e in events if start - 1 <= float(e.get("time", -1)) <= end]
        return events

    def highlight_for(self, row):
        """The detector's original suggestion behind a clip (None for clips you added)."""
        if row.get("source") != "auto":
            return None
        return next((h for h in self.load_highlights() or [] if h["index"] == int(row["clip"])), None)

    def flagged(self):
        """[(anchor, note)] of clips the user flagged for review."""
        if not self.csv_path.exists():
            return []
        return [(float(r["anchor"]), r.get("note") or "") for r in self.read_rows() if r.get("flag")]

    def write_captions(self, found, recaption=False):
        if self.csv_path.exists() and not recaption:
            print(f"  using existing {self.csv_path.name} - edit it to change captions/music")
            return self.read_rows()
        # Flags (and their notes) survive re-analysis: they move to the clip at the same moment.
        carry = getattr(self, "_carry_flags", None) or self.flagged()
        picker = captions.CaptionPicker(self.cfg, config.resolve(self.cfg["output_dir"]) / ".captions_used.json")
        mode = render.voice_mode(self.cfg, self.info) if picker.client else None
        rows = []
        for h in found:
            category = h.get("category", "Goal")
            hidden = category in self.cfg["detection"].get("hidden_categories", [])
            row = {"clip": h["index"], "label": h["label"], "category": category, "score": h["score"],
                   "start": h["start"], "end": h["end"], "anchor": h["anchor"], "music": "",
                   "skip": "yes" if hidden else "", "reason": h.get("reason", ""), "source": "auto",
                   "stat": h.get("stat", "")}
            tone = picker.pick_tone(category)
            library_line = picker.from_library(h["label"], tone, category)
            ai_line = None
            if picker.client:
                print(f"  [{h['index']:02d}] writing AI caption...")
                video, _ = render.extract(self.info, h["start"], h["end"], _workdir(self.out_dir, row),
                                          mode, self.cfg["audio"]["game_track"])
                ai_line = picker.from_ai(video, h["anchor"] - h["start"], h["label"], tone,
                                         _workdir(self.out_dir, row))
            row.update(tone=tone, caption=ai_line or library_line,
                       alt_caption=library_line if ai_line else picker.from_library(h["label"], tone, category))
            rows.append(row)
        picker.save_state()
        for anchor, note in carry:
            near = min(rows, key=lambda r: abs(float(r["anchor"]) - anchor), default=None)
            if near is not None and abs(float(near["anchor"]) - anchor) <= 3:
                near.update(flag="yes", note=note)
        self._carry_flags = None
        # Plays with a story in them (a lost 50/50, a post hit, a second chance... before the goal)
        # get one drafted; it shows in Clip Studio and can be edited or cleared.
        from . import story
        lookback = float(self.cfg["detection"].get("story_lookback", 10))
        for row in rows:
            anchor, end = float(row["anchor"]), float(row["end"])
            if row.get("category") not in story.STORY_CATEGORIES or row.get("label") in story.NOT_MINE:
                continue
            moments = sorted(float(e["time"]) for e in self.story_events(anchor - lookback, end)
                             if e.get("label") in story.STORY_LABELS and anchor - lookback <= float(e["time"]) < anchor - 0.3)
            if len(moments) >= 2:
                # Start the clip just before the story does, so the whole build-up is in it.
                row["start"] = f"{max(0.0, min(float(row['start']), moments[0] - 1.0)):.2f}"
                start = float(row["start"])
                drafted = story.suggest(row, self.story_events(start, end), [], seed_text=self.video.name)
                row["story"] = json.dumps({"beats": drafted["beats"], "payoff": drafted["payoff"]})
                if drafted["title"]:
                    row.update(alt_caption=row.get("caption") or "", caption=drafted["title"])
        # Clips you rejected stay rejected when the recording is analyzed again.
        for key, rec in feedback.Feedback(feedback_path(self.cfg)).data["clips"].items():
            video, anchor = key.rsplit("|", 1)
            if rec["verdict"] != "reject" or video != self.video.name:
                continue
            near = min(rows, key=lambda r: abs(float(r["anchor"]) - float(anchor)), default=None)
            if near is not None and abs(float(near["anchor"]) - float(anchor)) <= 3:
                near.update(skip="yes", reject_reason=rec.get("reason", ""))
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.write_rows(rows)
        return rows

    def read_rows(self):
        with open(self.csv_path, newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))

    def write_rows(self, rows):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        # utf-8-sig so Excel opens it with the right encoding.
        with open(self.csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    def add_manual(self, at, label="HIGHLIGHT"):
        """Add a clip the detector missed, centred on `at` seconds. Returns the new row."""
        det = self.cfg["detection"]
        rows = self.read_rows() if self.csv_path.exists() else []
        picker = captions.CaptionPicker(self.cfg, config.resolve(self.cfg["output_dir"]) / ".captions_used.json")
        tone = picker.pick_tone("Manual")
        at = min(max(0.0, float(at)), self.info.duration)
        row = {"clip": max([int(r["clip"]) for r in rows] + [0]) + 1, "label": label, "category": "Manual",
               "score": "", "start": f"{max(0.0, at - det['pre_seconds']):.2f}",
               "end": f"{min(self.info.duration, at + det['post_seconds'] + 1):.2f}", "anchor": f"{at:.2f}",
               "tone": tone, "caption": picker.from_library(label, tone, "Manual"),
               "alt_caption": picker.from_library(label, tone, "Manual"), "music": "", "skip": "",
               "reason": "Added by you", "source": "manual"}
        picker.save_state()
        rows.append(row)
        rows.sort(key=lambda r: float(r["anchor"]))
        self.write_rows(rows)
        return row

    def compile(self, clips, layout, on_progress=None):
        """Join already-exported clips (in the given order) into one highlight video."""
        rows = {int(r["clip"]): r for r in self.read_rows()}
        parts = [self.exported(rows[c], layout) for c in clips if c in rows]
        parts = [p for p in parts if p]
        if not parts:
            raise RuntimeError("Export the clips first, then build the highlight video from them")
        out = exports_dir(self.cfg) / f"Highlights - {recording_time(self.video)}" \
            f"{' [' + layout + ']' if layout != 'vertical' else ''}.mp4"
        if on_progress:
            on_progress(f"Joining {len(parts)} clips...")
        return join_clips(parts, out, self.cfg["render"]["fps"])

    # ------------------------------------------------------------ step 3
    def output_name(self, row):
        """Name for working files (raw cuts)."""
        return f"{self.video.stem}_{int(row['clip']):02d}_{_slug(row['label'])}"

    def export_name(self, row, layout, formats):
        """'Goal - Called it from kickoff (Oct 22 6.25 PM #3)': what it is, its caption, where it's from."""
        category = _safe((row.get("category") or "Clip").replace("/", "-").replace("?", ""))
        caption = _safe(row.get("caption") or "")[:45].strip(" .")
        name = f"{category} - {caption}" if caption else category
        name += f" ({recording_time(self.video)} #{int(row['clip'])})"
        return name + (f" [{layout}]" if len(formats) > 1 else "")

    def exported(self, row, layout):
        """The clip's latest export in this layout, if it's still there."""
        for item in (row.get("exports") or "").split(";"):
            kind, _, rel = item.partition("=")
            if kind == layout and rel and (self.out_root / rel).exists():
                return self.out_root / rel
        return None

    def clip_config(self, row):
        """Settings for one clip: the global config plus that clip's volume/framing tweaks."""
        cfg = copy.deepcopy(self.cfg)
        if row.get("game_volume") not in (None, ""):
            cfg["audio"]["gain"] = float(row["game_volume"]) / 100
        if row.get("music_volume") not in (None, ""):
            cfg["music"]["volume"] *= float(row["music_volume"]) / 100
        if row.get("framing") in ("blur", "crop"):
            cfg["render"]["vertical_style"] = row["framing"]
        if row.get("crop_x") not in (None, ""):
            cfg["render"]["crop_x"] = float(row["crop_x"])
        return cfg

    def render_all(self, rows, formats, on_clip=None):
        cfg = self.cfg
        mode = render.voice_mode(cfg, self.info)
        library = music.MusicLibrary(cfg)
        if not library.tracks():
            print(f"  ! No music found in {library.root} - clips will only have game audio.")
        mood_of = {h["index"]: h["mood"] for h in self.load_highlights() or []}

        made, exports = [], {}
        for row in rows:
            if is_skipped(row):
                continue
            cfg = self.clip_config(row)
            idx = int(row["clip"])
            start, end, anchor = float(row["start"]), float(row["end"]), float(row["anchor"])
            work = _workdir(self.out_dir, row)
            name = self.output_name(row)
            if on_clip:
                on_clip(row)
            print(f"  [{idx:02d}] {row['label']} ({end - start:.0f}s): \"{row['caption']}\"")
            video, game = render.extract(self.info, start, end, work, mode, cfg["audio"]["game_track"])

            track = None
            no_music = (row.get("music") or "").strip().lower() == "none"
            if row.get("music") and not no_music:
                track = library.find(row["music"].strip())
                if track is None:
                    print(f"    ! music '{row['music']}' not found, picking one")
            if track is None and not no_music:
                # Funny captions get the goofy tracks; everything else follows the play type.
                mood = (cfg["music"].get("mood_by_category") or {}).get(row.get("category")) or (
                    "funny" if row.get("tone") == "funny" else mood_of.get(idx, "hype"))
                track = library.choose(mood)
            song = (track, library.drop_time(track)) if track else None
            if track:
                print(f"    music: {track.name}")

            if cfg["render"]["keep_raw_clip"]:
                render.save_raw(video, game, self.out_dir / "raw" / f"{name}.mp4")
            today = exports_dir(cfg)
            entries = []
            for layout in formats:
                out = today / f"{self.export_name(row, layout, formats)}.mp4"
                previous = self.exported(row, layout)
                render.render(cfg, video, game, anchor - start, layout, row["caption"], row["label"],
                              song, out, work, stat=row.get("stat") or "", story=story_of(row, start))
                fields = post_fields(row, self.video)
                out.with_suffix(".txt").write_text(fields["description"] + "\n", encoding="utf-8")
                out.with_suffix(".json").write_text(json.dumps(
                    {**fields, "recording": self.video.name, "clip": idx, "layout": layout}, indent=1,
                    ensure_ascii=False), encoding="utf-8")
                if previous and previous != out and previous.parent == today:
                    # Re-exported today with a new caption: replace today's older copy.
                    for ext in (".mp4", ".txt", ".json"):
                        previous.with_suffix(ext).unlink(missing_ok=True)
                entries.append(f"{layout}={out.relative_to(self.out_root).as_posix()}")
                made.append(out)
            exports[idx] = entries
        shutil.rmtree(self.out_dir / "work", ignore_errors=True)
        if made:
            refresh_posts_csv(exports_dir(self.cfg), self.cfg)
        if exports and self.csv_path.exists():
            saved = self.read_rows()
            for r in saved:
                if int(r["clip"]) in exports:
                    keep = [e for e in (r.get("exports") or "").split(";")
                            if e and e.split("=")[0] not in formats]
                    r["exports"] = ";".join(keep + exports[int(r["clip"])])
            self.write_rows(saved)
        return made
