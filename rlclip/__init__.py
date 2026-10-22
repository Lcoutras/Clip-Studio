"""Rocket League highlight clipper."""

from pathlib import Path

__version__ = (Path(__file__).with_name("VERSION").read_text(encoding="utf-8").strip()
               if Path(__file__).with_name("VERSION").exists() else "0.0.0")
