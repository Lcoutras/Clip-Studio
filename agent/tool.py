"""Runs Clip Studio's agent commands from this folder:  ../.venv/Scripts/python.exe tool.py <command>"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rlclip import cli  # noqa: E402

sys.exit(cli.main(["agent", *sys.argv[1:]]))
