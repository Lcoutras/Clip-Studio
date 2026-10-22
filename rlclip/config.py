"""Loads config.yaml, filling in defaults for anything the user left out."""

import copy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "output_dir": "output",
    "detection": {
        "ocr_fps": 1.0,
        "ocr_region": [0.15, 0.1, 0.85, 0.9],
        "ocr_width": 960,
        "ocr_min_confidence": 0.5,
        "ocr_delay": 0.5,
        "audio_peak_db": 10,
        "min_score": 5,
        "max_clips_per_video": 30,
        "pre_seconds": 5,
        "post_seconds": 2,
        "merge_gap": 6,
        "max_clip_seconds": 18,
        "player_names": [],
        "reaction_db": 12,
        "reaction_tail": 1.5,
        "hidden_categories": ["Fail?"],
    },
    "audio": {
        "voice_removal": "auto",
        "game_track": 0,
        "voice_track": 1,
        "game_volume": 0.35,
        "game_volume_on_play": 1.0,
    },
    "music": {
        "dir": "music",
        "volume": 0.9,
        "fade_out": 1.0,
    },
    "captions": {
        "source": "auto",
        "funny_ratio": 0.6,
        "model": "claude-opus-5-5",
        "library": "captions/library.yaml",
        "font": "auto",
        "show_event_label": True,
        "watermark": "",
    },
    "render": {
        "format": "vertical",
        "vertical_style": "blur",
        "vertical_fg_aspect": 1.3333,
        "slowmo": True,
        "slowmo_speed": 0.5,
        "slowmo_seconds": 1.2,
        "zoom_punch": True,
        "fps": 60,
        "encoder": "libx264",
        "crf": 20,
        "keep_raw_clip": True,
    },
}


def _merge(base, override):
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def resolve(path):
    path = Path(path)
    return path if path.is_absolute() else ROOT / path


def load(path=None):
    cfg = copy.deepcopy(DEFAULTS)
    path = Path(path) if path else ROOT / "config.yaml"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            _merge(cfg, yaml.safe_load(f) or {})
    return cfg
