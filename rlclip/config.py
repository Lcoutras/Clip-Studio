"""Settings: the defaults below, overridden by the user's own settings.yaml.

settings.yaml holds only what the user changed (in-game name, watermark...). Updates replace
the app (and these defaults) but never touch settings.yaml, so improved defaults reach everyone.
Older installs kept every setting in config.yaml; that file is migrated once (see _migrate).
"""

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
        "pre_seconds": 3.5,
        "post_seconds": 2,
        "merge_gap": 6,
        "max_clip_seconds": 18,
        "player_names": [],
        "reaction_db": 12,
        "reaction_tail": 1.5,
        # A voice reaction on its own only becomes a clip when the game made a sound just before it,
        # and at most this many per 10 minutes of recording (the loudest win).
        # Game first: by default your mic only adds to a play the game showed (goal, save, demo,
        # post...). Set true to also let a big reaction on its own become a clip.
        "reactions_alone": False,
        "reaction_needs_game_sound": True,
        "max_reactions_per_10min": 3,
        "hidden_categories": [],
        # Clears, centers and aerial touches only become clips with a big hit in the game audio,
        # and at most this many per 10 minutes (the best ones).
        "max_minor_per_10min": 3,
        # Tuned from what the Clip Agent learned (see agent/README.md). Points added to a kind
        # of clip's score ({"reaction": -2, "popup:ASSIST": 1}), and seconds to move the
        # start/end of a category's clips ({"Goal": [-1.5, 0.5]}).
        "signal_adjust": {},
        "timing_adjust": {},
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
        # Which mood folder each category's music comes from, e.g. {"Blooper/Fail": "funny"}.
        "mood_by_category": {},
    },
    "captions": {
        "source": "auto",
        "funny_ratio": 0.6,
        # Share of funny captions per category, overriding funny_ratio: {"Save": 0.2}.
        "tone_by_category": {},
        "model": "claude-opus-5-5",
        "library": "captions/library.yaml",
        "font": "auto",
        "show_event_label": True,
        "watermark": "",
    },
    # Rocket League replays: Clip Studio always looks in the game's replay folders (Demos and
    # DemosEpic in your Documents, even when Documents is in OneDrive), plus any folders listed
    # here and any replays you import, and uses them for exact events.
    "replays": {
        "enabled": True,
        "dirs": [],
        "files": [],
    },
    # The Clip Agent (see agent/README.md) looks for new recordings here, including subfolders
    # such as NVIDIA's "Videos\Rocket League". Short or old files are ignored.
    "agent": {
        "watch_dirs": ["~/Videos"],
        "min_minutes": 2,
        "max_age_days": 14,
    },
    # In-app updates come from this public GitHub repository.
    "update": {
        "repo": "lcoutras/clip-studio",
        "branch": "main",
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


SETTINGS = "settings.yaml"
LEGACY = "config.yaml"
# Personal choices carried over from an old config.yaml; everything else uses the app's defaults.
_PERSONAL = [("output_dir",), ("detection", "player_names"), ("captions", "watermark"),
             ("captions", "source"), ("render", "format"), ("render", "encoder")]


def _migrate(legacy, settings):
    with open(legacy, encoding="utf-8") as f:
        old = yaml.safe_load(f) or {}
    keep = {}
    for keys in _PERSONAL:
        node = old
        for k in keys:
            node = node.get(k) if isinstance(node, dict) else None
        default = DEFAULTS
        for k in keys:
            default = default.get(k)
        if node is not None and node != default:
            target = keep
            for k in keys[:-1]:
                target = target.setdefault(k, {})
            target[keys[-1]] = node
    header = ("# Your Clip Studio settings. Only list what you want different from the defaults;\n"
              "# see settings.example.yaml for every option. Updates never change this file.\n")
    with open(settings, "w", encoding="utf-8") as f:
        f.write(header + (yaml.safe_dump(keep, sort_keys=False) if keep else ""))
    legacy.replace(legacy.with_name("config.old.yaml"))


def load(path=None):
    cfg = copy.deepcopy(DEFAULTS)
    if path:
        path = Path(path)
    else:
        path = ROOT / SETTINGS
        if not path.exists() and (ROOT / LEGACY).exists():
            _migrate(ROOT / LEGACY, path)
    if path.exists():
        with open(path, encoding="utf-8") as f:
            _merge(cfg, yaml.safe_load(f) or {})
    return cfg
