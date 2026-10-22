"""In-app updates from the project's public GitHub repository.

check() compares this copy's VERSION with the one on GitHub. apply() downloads the
repository and copies the new files over this folder, leaving the user's own things alone
(output, music, fonts, the Python environment and config.yaml), then reinstalls packages if
requirements.txt changed.
"""

import io
import os
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import PurePosixPath

from . import __version__, config, ffmpeg

# Top-level names an update never touches.
KEEP = {"output", "music", "fonts", ".venv", "config.yaml", ".git"}


def _get(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": "ClipStudio-updater", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


def _parse(version):
    return tuple(int(p) for p in version.strip().split(".") if p.isdigit())


def _source(cfg):
    up = cfg.get("update", {})
    return up.get("repo", ""), up.get("branch", "main")


def check(cfg):
    """{'current', 'latest', 'available'} - raises on network trouble."""
    repo, branch = _source(cfg)
    if not repo:
        return {"current": __version__, "latest": None, "available": False}
    latest = _get(f"https://raw.githubusercontent.com/{repo}/{branch}/rlclip/VERSION", 8).decode().strip()
    return {"current": __version__, "latest": latest, "available": _parse(latest) > _parse(__version__)}


def apply(cfg, on_progress=lambda msg: None):
    """Download and install the latest version. Returns the new version string."""
    repo, branch = _source(cfg)
    on_progress("Downloading update...")
    data = _get(f"https://codeload.github.com/{repo}/zip/refs/heads/{branch}", 180)
    archive = zipfile.ZipFile(io.BytesIO(data))
    names = archive.namelist()
    root = names[0].split("/")[0]
    if f"{root}/rlclip/__init__.py" not in names:
        raise RuntimeError("The download doesn't look like Clip Studio - update cancelled")

    old_requirements = (config.ROOT / "requirements.txt").read_text(encoding="utf-8")
    on_progress("Installing update...")
    for info in archive.infolist():
        rel = PurePosixPath(info.filename).relative_to(root) if "/" in info.filename else None
        if rel is None or info.is_dir() or not rel.parts:
            continue
        if rel.parts[0] in KEEP or ".." in rel.parts:
            continue
        dest = config.ROOT.joinpath(*rel.parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(archive.read(info))

    new_version = (config.ROOT / "rlclip" / "VERSION").read_text(encoding="utf-8").strip()
    if (config.ROOT / "requirements.txt").read_text(encoding="utf-8") != old_requirements:
        on_progress("Installing new components (this can take a minute)...")
        python = sys.executable
        if python.lower().endswith("pythonw.exe"):
            python = python[:-len("pythonw.exe")] + "python.exe"
        result = subprocess.run([python, "-m", "pip", "install", "-r", str(config.ROOT / "requirements.txt")],
                                capture_output=True, text=True, creationflags=ffmpeg.NO_WINDOW)
        if result.returncode != 0:
            raise RuntimeError("Updated, but installing new components failed:\n" + result.stderr[-1500:])
    return new_version


def restart(windowed=True):
    """Start a fresh copy of the app, then quit this one."""
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | ffmpeg.NO_WINDOW
    subprocess.Popen([sys.executable, "-m", "rlclip", "studio" if windowed else "app"],
                     cwd=str(config.ROOT), creationflags=flags, close_fds=True)
    os._exit(0)
