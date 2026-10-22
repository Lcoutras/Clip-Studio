"""Thin wrappers around the ffmpeg / ffprobe command-line tools."""

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


# Stops a console window flashing up for every ffmpeg call when the desktop app runs.
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
# Heavy decoding/encoding runs at below-normal priority so the PC and the app stay responsive.
BACKGROUND = NO_WINDOW | (subprocess.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 0)


class FFmpegError(RuntimeError):
    pass


def require_ffmpeg():
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise FFmpegError(
                f"'{tool}' was not found. Install it with:  winget install Gyan.FFmpeg\n"
                "then close and reopen this window."
            )


@dataclass
class VideoInfo:
    path: Path
    duration: float
    width: int
    height: int
    fps: float
    audio_tracks: int


def probe(path) -> VideoInfo:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True, creationflags=NO_WINDOW,
    )
    if out.returncode != 0:
        raise FFmpegError(f"ffprobe could not read {path}:\n{out.stderr}")
    data = json.loads(out.stdout)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if video is None:
        raise FFmpegError(f"{path} has no video stream")
    num, den = (video.get("avg_frame_rate") or "60/1").split("/")
    fps = float(num) / float(den) if float(den) else 60.0
    return VideoInfo(
        path=Path(path),
        duration=float(data["format"]["duration"]),
        width=int(video["width"]),
        height=int(video["height"]),
        fps=fps,
        audio_tracks=sum(1 for s in data["streams"] if s["codec_type"] == "audio"),
    )


def run(args, quiet=True):
    """Run ffmpeg with the given arguments (without the leading 'ffmpeg')."""
    cmd = ["ffmpeg", "-hide_banner", "-y", "-loglevel", "error" if quiet else "info", *map(str, args)]
    out = subprocess.run(cmd, capture_output=True, text=True, creationflags=BACKGROUND)
    if out.returncode != 0:
        raise FFmpegError("ffmpeg failed:\n" + " ".join(cmd) + "\n" + out.stderr[-3000:])


def pipe(args):
    """Start ffmpeg writing raw data to stdout; caller reads proc.stdout."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", *map(str, args)]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=10**7,
                            creationflags=BACKGROUND)


def read_audio(path, track=0, rate=8000, start=None, duration=None):
    """Decode one audio track to a mono float32 numpy array."""
    import numpy as np

    args = []
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(path)]
    if duration is not None:
        args += ["-t", f"{duration:.3f}"]
    args += ["-map", f"0:a:{track}", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"]
    proc = pipe(args)
    raw = proc.stdout.read()
    proc.wait()
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
