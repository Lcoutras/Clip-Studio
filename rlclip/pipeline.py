"""Ties detection, captions, music and rendering together for one recording."""

import copy
import csv
import json
import re
import shutil
from pathlib import Path

from . import captions, config, detect, ffmpeg, highlights, music, render

# Bump when detection changes enough that old results should be thrown away.
DETECTION_VERSION = 3
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".flv", ".ts", ".avi", ".webm"}
CSV_FIELDS = ["clip", "label", "category", "score", "start", "end", "anchor", "tone", "caption",
              "alt_caption", "music", "skip", "reason", "source", "game_volume", "music_volume",
              "framing", "crop_x"]


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
        self.out_dir = config.resolve(cfg["output_dir"]) / self.video.stem
        self.highlights_path = self.out_dir / "highlights.json"
        self.csv_path = self.out_dir / "captions.csv"
        self._info = None

    @property
    def info(self):
        if self._info is None:
            self._info = ffmpeg.probe(self.video)
        return self._info

    # ------------------------------------------------------------ step 1
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
        ocr = detect.ocr_events(info, det, on_progress=on_progress)
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
        found = highlights.build(ocr, peaks, markers, det, info.duration, reactions, mixed)
        data = [h.to_dict() for h in found]
        # Old clips and captions belong to the old detection; clear them only now, so a cancelled
        # or failed analysis leaves the previous results alone.
        for old in ("vertical", "horizontal", "raw", "work", "previews"):
            shutil.rmtree(self.out_dir / old, ignore_errors=True)
        self.csv_path.unlink(missing_ok=True)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with open(self.highlights_path, "w", encoding="utf-8") as f:
            json.dump({"version": DETECTION_VERSION, "highlights": data}, f, indent=1)
        print(f"  picked {len(data)} highlights")
        return data

    # ------------------------------------------------------------ step 2
    def write_captions(self, found, recaption=False):
        if self.csv_path.exists() and not recaption:
            print(f"  using existing {self.csv_path.name} - edit it to change captions/music")
            return self.read_rows()
        picker = captions.CaptionPicker(self.cfg, config.resolve(self.cfg["output_dir"]) / ".captions_used.json")
        mode = render.voice_mode(self.cfg, self.info) if picker.client else None
        rows = []
        for h in found:
            category = h.get("category", "Goal")
            hidden = category in self.cfg["detection"].get("hidden_categories", [])
            row = {"clip": h["index"], "label": h["label"], "category": category, "score": h["score"],
                   "start": h["start"], "end": h["end"], "anchor": h["anchor"], "music": "",
                   "skip": "yes" if hidden else "", "reason": h.get("reason", ""), "source": "auto"}
            tone = picker.pick_tone()
            library_line = picker.from_library(h["label"], tone)
            ai_line = None
            if picker.client:
                print(f"  [{h['index']:02d}] writing AI caption...")
                video, _ = render.extract(self.info, h["start"], h["end"], _workdir(self.out_dir, row),
                                          mode, self.cfg["audio"]["game_track"])
                ai_line = picker.from_ai(video, h["anchor"] - h["start"], h["label"], tone,
                                         _workdir(self.out_dir, row))
            row.update(tone=tone, caption=ai_line or library_line,
                       alt_caption=library_line if ai_line else picker.from_library(h["label"], tone))
            rows.append(row)
        picker.save_state()
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
        tone = picker.pick_tone()
        at = min(max(0.0, float(at)), self.info.duration)
        row = {"clip": max([int(r["clip"]) for r in rows] + [0]) + 1, "label": label, "category": "Manual",
               "score": "", "start": f"{max(0.0, at - det['pre_seconds']):.2f}",
               "end": f"{min(self.info.duration, at + det['post_seconds'] + 1):.2f}", "anchor": f"{at:.2f}",
               "tone": tone, "caption": picker.from_library(label, tone),
               "alt_caption": picker.from_library(label, tone), "music": "", "skip": "",
               "reason": "Added by you", "source": "manual"}
        picker.save_state()
        rows.append(row)
        rows.sort(key=lambda r: float(r["anchor"]))
        self.write_rows(rows)
        return row

    def compile(self, clips, layout, on_progress=None):
        """Join already-exported clips (in the given order) into one highlight video."""
        rows = {int(r["clip"]): r for r in self.read_rows()}
        parts = [self.out_dir / layout / f"{self.output_name(rows[c])}.mp4" for c in clips if c in rows]
        parts = [p for p in parts if p.exists()]
        if not parts:
            raise RuntimeError("Export the clips first, then build the highlight video from them")
        out = self.out_dir / f"{self.video.stem}_HIGHLIGHTS_{layout}.mp4"
        first = ffmpeg.probe(parts[0])
        fps = self.cfg["render"]["fps"]
        args, graph = [], []
        for i, p in enumerate(parts):
            args += ["-i", p]
            # Make every part identical in size, pixel shape, frame rate and audio format.
            graph.append(f"[{i}:v:0]scale={first.width}:{first.height},setsar=1,fps={fps},format=yuv420p[v{i}];"
                         f"[{i}:a:0]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
        streams = "".join(f"[v{i}][a{i}]" for i in range(len(parts)))
        graph.append(f"{streams}concat=n={len(parts)}:v=1:a=1[v][a]")
        args += ["-filter_complex", ";".join(graph),
                 "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                 "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out]
        if on_progress:
            on_progress(f"Joining {len(parts)} clips...")
        ffmpeg.run(args)
        return out

    # ------------------------------------------------------------ step 3
    def output_name(self, row):
        return f"{self.video.stem}_{int(row['clip']):02d}_{_slug(row['label'])}"

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

        made = []
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
            if row.get("music"):
                track = library.find(row["music"].strip())
                if track is None:
                    print(f"    ! music '{row['music']}' not found, picking one")
            if track is None:
                # Funny captions get the goofy tracks; everything else follows the play type.
                mood = "funny" if row.get("tone") == "funny" else mood_of.get(idx, "hype")
                track = library.choose(mood)
            song = (track, library.drop_time(track)) if track else None
            if track:
                print(f"    music: {track.name}")

            if cfg["render"]["keep_raw_clip"]:
                render.save_raw(video, game, self.out_dir / "raw" / f"{name}.mp4")
            for layout in formats:
                out = self.out_dir / layout / f"{name}.mp4"
                render.render(cfg, video, game, anchor - start, layout, row["caption"], row["label"],
                              song, out, work)
                made.append(out)
        shutil.rmtree(self.out_dir / "work", ignore_errors=True)
        return made
