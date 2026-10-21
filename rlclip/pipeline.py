"""Ties detection, captions, music and rendering together for one recording."""

import csv
import json
import re
import shutil
from pathlib import Path

from . import captions, config, detect, ffmpeg, highlights, music, render

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".flv", ".ts", ".avi", ".webm"}
CSV_FIELDS = ["clip", "label", "score", "start", "end", "anchor", "tone", "caption", "alt_caption",
              "music", "skip"]


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
    def detect(self, rescan=False):
        if self.highlights_path.exists() and not rescan:
            with open(self.highlights_path, encoding="utf-8") as f:
                data = json.load(f)
            print(f"  using saved detection ({len(data)} highlights) - pass --rescan to redo it")
            return data
        det = self.cfg["detection"]
        info = self.info
        mins = info.duration / 60
        print(f"  {info.width}x{info.height} @ {info.fps:.0f}fps, {mins:.1f} min, "
              f"{info.audio_tracks} audio track(s)")
        track = self.cfg["audio"]["game_track"] if info.audio_tracks > 1 else 0
        peaks = detect.audio_peaks(self.video, track, det["audio_peak_db"]) if info.audio_tracks else []
        print(f"  audio: {len(peaks)} loud moments")
        ocr = detect.ocr_events(info, det)
        markers = []
        marker_file = self.video.with_suffix(".markers.txt")
        if marker_file.exists():
            markers = detect.read_markers(marker_file)
            print(f"  markers: {len(markers)} from {marker_file.name}")
        found = highlights.build(ocr, peaks, markers, det, info.duration)
        data = [h.to_dict() for h in found]
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with open(self.highlights_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
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
            row = {"clip": h["index"], "label": h["label"], "score": h["score"],
                   "start": h["start"], "end": h["end"], "anchor": h["anchor"], "music": "", "skip": ""}
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
        # utf-8-sig so Excel opens it with the right encoding.
        with open(self.csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        return rows

    def read_rows(self):
        with open(self.csv_path, newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))

    # ------------------------------------------------------------ step 3
    def render_all(self, rows, formats):
        cfg = self.cfg
        mode = render.voice_mode(cfg, self.info)
        library = music.MusicLibrary(cfg)
        if not library.tracks():
            print(f"  ! No music found in {library.root} - clips will only have game audio.")
        mood_of = {}
        if self.highlights_path.exists():
            with open(self.highlights_path, encoding="utf-8") as f:
                mood_of = {h["index"]: h["mood"] for h in json.load(f)}

        made = []
        for row in rows:
            if str(row.get("skip", "")).strip().lower() in ("y", "yes", "x", "1", "true"):
                continue
            idx = int(row["clip"])
            start, end, anchor = float(row["start"]), float(row["end"]), float(row["anchor"])
            work = _workdir(self.out_dir, row)
            name = f"{self.video.stem}_{idx:02d}_{_slug(row['label'])}"
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
