"""Picks or writes a caption for each highlight.

Library captions come from captions/library.yaml. AI captions send a few frames of the
clip to Claude and ask for one short line about what actually happens in the play.
"""

import base64
import json
import os
import random

import yaml

from . import config, ffmpeg

FAMILIES = [("GOAL", "GOAL"), ("SAVE", "SAVE"), ("SAVIOR", "SAVE"), ("DEMO", "DEMO")]

SYSTEM_PROMPT = """You write the on-screen caption for short vertical Rocket League highlight clips \
(TikTok / YouTube Shorts). The clip has no voice-over, so the caption carries the personality.

Rules:
- At most 9 words. No hashtags, no emojis, no quotation marks.
- Make it about what is actually happening in these frames when you can tell \
(a flick, a whiff, a desperate save, a demo, an open net, an aerial).
- "funny": a pun, a roast of the opponent, or deadpan understatement. Keep it friendly, nothing mean-spirited about real people.
- "motivational": a punchy, sincere line about grinding, clutch moments or improving.
- Never invent a rank, score or player name you cannot see."""


# Lines you (or the Clip Agent) add. Updates replace library.yaml but never this file.
CUSTOM = "captions/my_captions.yaml"


def load_library(cfg):
    """The built-in caption library plus your own lines from my_captions.yaml."""
    with open(config.resolve(cfg["library"]), encoding="utf-8") as f:
        library = yaml.safe_load(f) or {}
    custom = config.resolve(CUSTOM)
    if custom.exists():
        with open(custom, encoding="utf-8") as f:
            for key, tones in (yaml.safe_load(f) or {}).items():
                for tone, lines in (tones or {}).items():
                    pool = library.setdefault(key, {}).setdefault(tone, [])
                    library[key][tone] = (lines or []) + [l for l in pool if l not in (lines or [])]
    return library


def add_custom(label, tone, line):
    """Add a line to my_captions.yaml. Returns False if it's already there."""
    custom = config.resolve(CUSTOM)
    data = {}
    if custom.exists():
        with open(custom, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    lines = data.setdefault(label, {}).setdefault(tone, [])
    if line in lines:
        return False
    lines.append(line)
    custom.parent.mkdir(parents=True, exist_ok=True)
    with open(custom, "w", encoding="utf-8") as f:
        f.write("# Your own caption lines (added by you or the Clip Agent). Updates never touch this file.\n")
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
    return True


class CaptionPicker:
    def __init__(self, cfg, state_path):
        self.cfg = cfg["captions"]
        self.library = load_library(self.cfg)
        self.state_path = state_path
        try:
            with open(state_path, encoding="utf-8") as f:
                self.used = set(json.load(f))
        except (OSError, ValueError):
            self.used = set()
        self.client = None
        source = self.cfg["source"]
        has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
        if source == "ai" or (source == "auto" and has_key):
            try:
                import anthropic
                self.client = anthropic.Anthropic()
            except Exception as e:  # missing package or credentials
                print(f"  ! AI captions unavailable ({e}); using the caption library")

    def save_state(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(sorted(self.used), f, indent=1)

    def pick_tone(self, category=None):
        ratio = (self.cfg.get("tone_by_category") or {}).get(category, self.cfg["funny_ratio"])
        return "funny" if random.random() < float(ratio) else "motivational"

    def _pool(self, label, tone):
        keys = [label] + [fam for word, fam in FAMILIES if word in label] + ["ANY"]
        for key in keys:
            lines = (self.library.get(key) or {}).get(tone) or []
            # An unquoted "a: b" line in YAML loads as a dict; turn it back into text.
            lines = [", ".join(f"{k}: {v}" for k, v in l.items()) if isinstance(l, dict) else str(l)
                     for l in lines]
            if lines:
                return lines
        return ["Certified clip moment"]

    def from_library(self, label, tone):
        pool = self._pool(label, tone)
        fresh = [c for c in pool if c not in self.used]
        if not fresh:
            # Everything in this pool has been used: allow repeats again.
            self.used -= set(pool)
            fresh = pool
        caption = random.choice(fresh)
        self.used.add(caption)
        return caption

    def from_ai(self, clip_path, event_time, label, tone, workdir):
        """Returns a caption string, or None if the AI call fails for any reason."""
        if self.client is None:
            return None
        import anthropic

        content = []
        for i, offset in enumerate((-3.0, -1.2, -0.3, 0.8)):
            t = max(0.0, event_time + offset)
            jpg = workdir / f"ai_frame_{i}.jpg"
            try:
                ffmpeg.run(["-ss", f"{t:.2f}", "-i", clip_path, "-frames:v", "1",
                            "-vf", "scale=768:-2", "-q:v", "4", jpg])
            except ffmpeg.FFmpegError:
                continue
            data = base64.standard_b64encode(jpg.read_bytes()).decode("utf-8")
            content.append({"type": "image",
                            "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
        if not content:
            return None
        examples = self._pool(label, tone)[:4]
        content.append({"type": "text", "text": (
            f"The game flagged this play as: {label}.\n"
            f"Frames are in order, the last one is just after the moment.\n"
            f"Tone: {tone}. Style examples (don't copy them): {' / '.join(examples)}.\n"
            "Write the caption.")})
        try:
            response = self.client.beta.messages.create(
                model=self.cfg["model"],
                max_tokens=2000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": content}],
                output_config={
                    "effort": "low",
                    "format": {
                        "type": "json_schema",
                        "schema": {
                            "type": "object",
                            "properties": {"caption": {"type": "string"}},
                            "required": ["caption"],
                            "additionalProperties": False,
                        },
                    },
                },
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError:
            print("  ! Anthropic API key was rejected; switching to the caption library")
            self.client = None
            return None
        except anthropic.APIError as e:
            print(f"  ! AI caption failed ({type(e).__name__}); using the library for this clip")
            return None
        if response.stop_reason == "refusal":
            return None
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            caption = json.loads(text)["caption"].strip().strip('"')
        except (ValueError, KeyError):
            return None
        return caption or None
