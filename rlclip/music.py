"""Chooses a music track and finds its drop so the play can land on it."""

import json
import random

import numpy as np

from . import config, ffmpeg

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus"}


class MusicLibrary:
    def __init__(self, cfg):
        self.root = config.resolve(cfg["music"]["dir"])
        self.cache_path = self.root / ".drops.json"
        try:
            with open(self.cache_path, encoding="utf-8") as f:
                self.cache = json.load(f)
        except (OSError, ValueError):
            self.cache = {}
        self.recent = []

    def tracks(self, mood=None):
        base = self.root / mood if mood else self.root
        if not base.is_dir():
            return []
        return sorted(p for p in base.rglob("*") if p.suffix.lower() in AUDIO_EXT)

    def choose(self, mood):
        pool = self.tracks(mood) or self.tracks()
        if not pool:
            return None
        fresh = [p for p in pool if p not in self.recent] or pool
        track = random.choice(fresh)
        self.recent = (self.recent + [track])[-max(1, len(pool) // 2):]
        return track

    def find(self, name):
        """Look up a track by file name (used when the captions sheet names one)."""
        for p in self.tracks():
            if p.name.lower() == name.lower() or p.stem.lower() == name.lower():
                return p
        return None

    def drop_time(self, track):
        key = f"{track.relative_to(self.root).as_posix()}|{track.stat().st_mtime:.0f}"
        if key not in self.cache:
            self.cache[key] = find_drop(track)
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, indent=1)
        return self.cache[key]


def find_drop(track, search_seconds=120.0):
    """Time (s) of the biggest jump in loudness in the first couple of minutes."""
    rate, hop = 11025, 0.1
    samples = ffmpeg.read_audio(track, rate=rate, duration=search_seconds + 10)
    n = int(rate * hop)
    if samples.size < n * 60:
        return 0.0
    frames = samples[: samples.size // n * n].reshape(-1, n)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    window = int(2.0 / hop)
    csum = np.concatenate([[0.0], np.cumsum(rms)])
    # The floor stops a quiet intro fading in from counting as a huge "rise".
    floor = 0.15 * float(rms.max())
    best_t, best_rise = 0.0, -1.0
    for i in range(window, len(rms) - window):
        before = (csum[i] - csum[i - window]) / window
        after = (csum[i + window] - csum[i]) / window
        rise = after / (before + floor + 1e-6)
        if rise > best_rise:
            best_rise, best_t = rise, i * hop
    return round(best_t, 2)
