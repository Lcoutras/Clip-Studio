"""Clip Studio backend: a small local web server the desktop window (or a browser) talks to.

It listens on 127.0.0.1 only, so nothing is reachable from other computers. Analysis and
exports run one at a time on a background thread; the UI polls /api/state for progress.
"""

import json
import mimetypes
import os
import queue
import random
import re
import socket
import subprocess
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__, captions, config, highlights, music, pipeline, updater

WEB = Path(__file__).resolve().parent / "web"


class Cancelled(Exception):
    pass


def _check(job):
    if job.get("cancel"):
        raise Cancelled()


class Studio:
    def __init__(self, cfg, windowed=True):
        self.cfg = cfg
        self.windowed = windowed
        self.update = {"current": __version__, "latest": None, "available": False}
        self.out_root = config.resolve(cfg["output_dir"])
        self.out_root.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.out_root / "app_settings.json"
        try:
            self.settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.settings = {}
        self.settings.setdefault("library", [])
        self.jobs = []
        self.lock = threading.Lock()
        self.queue = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._update_checker, daemon=True).start()

    # ------------------------------------------------------------ updates
    def _update_checker(self):
        while True:
            self.check_update()
            time.sleep(6 * 3600)

    def check_update(self):
        try:
            self.update = updater.check(self.cfg)
        except Exception as e:  # offline, repo not set up yet... just try again later
            print(f"update check failed: {e}")
        return self.update

    def install_update(self):
        if any(j["status"] in ("queued", "running") and j["kind"] != "update" for j in self.jobs):
            raise ValueError("Finish or cancel the current tasks first, then update")

        def run(job):
            version = updater.apply(self.cfg, on_progress=lambda m: job.update(message=m))
            job.update(message=f"Updated to {version} - restarting...", result="restart")
        return self.submit("update", "Clip Studio", run)

    def restart(self):
        threading.Timer(0.5, updater.restart, args=(self.windowed,)).start()

    # ------------------------------------------------------------ library
    def save_settings(self):
        self.settings_path.write_text(json.dumps(self.settings, indent=1), encoding="utf-8")

    def library(self):
        return [Path(p) for p in self.settings["library"] if Path(p).is_file()]

    def import_paths(self, paths):
        """Add video files (or every video in a folder) to the library. Returns names added."""
        added = []
        for raw in paths:
            if not raw:
                continue
            path = Path(str(raw).strip().strip('"'))
            files = pipeline.find_videos(path) if path.is_dir() else [path]
            for f in files:
                if not f.is_file() or f.suffix.lower() not in pipeline.VIDEO_EXT:
                    continue
                if str(f) not in self.settings["library"]:
                    self.settings["library"].append(str(f))
                added.append(f.name)
        self.save_settings()
        if not added:
            raise ValueError("No video files found there (mp4, mkv, mov...)")
        return added

    def remove(self, name):
        """Take a recording off the list (the video file itself is left alone)."""
        self.cancel_video(name)
        self.settings["library"] = [p for p in self.settings["library"] if Path(p).name != name]
        self.save_settings()

    def video(self, name):
        for path in self.library():
            if path.name == name:
                return path
        raise KeyError(f"Recording not in the library: {name}")

    def project(self, name):
        return pipeline.Project(self.video(name), self.cfg)

    def media_url(self, path):
        rel = Path(path).resolve().relative_to(self.out_root.resolve()).as_posix()
        return "/media/" + "/".join(map(_quote, rel.split("/")))

    # ------------------------------------------------------------ jobs
    def submit(self, kind, video, fn):
        job = {"id": len(self.jobs) + 1, "kind": kind, "video": video, "status": "queued",
               "progress": 0, "message": "Waiting for the current task..."}
        with self.lock:
            self.jobs.append(job)
        self.queue.put((job, fn))
        return job

    def _worker(self):
        while True:
            job, fn = self.queue.get()
            if job["status"] == "cancelled":
                continue
            job.update(status="running", message="Starting...")
            try:
                fn(job)
                _check(job)
                job.update(status="done", progress=100)
                job.setdefault("result", None)
                if job["message"].endswith("..."):
                    job["message"] = "Done"
            except Cancelled:
                job.update(status="cancelled", message="Cancelled")
            except Exception as e:  # show any failure in the app instead of dying
                if job.get("cancel"):
                    job.update(status="cancelled", message="Cancelled")
                    continue
                traceback.print_exc()
                job.update(status="error", message=str(e)[-400:])

    def cancel(self, job_id):
        """Stop a running task (at the next safe point) or drop a waiting one."""
        for job in self.jobs:
            if job["id"] == job_id and job["status"] in ("queued", "running"):
                job["cancel"] = True
                if job["status"] == "queued":
                    job.update(status="cancelled", message="Cancelled")
                else:
                    job["message"] = "Cancelling..."
                return job
        raise KeyError(f"No active task {job_id}")

    def cancel_video(self, name):
        for job in self.jobs:
            if job["video"] == name and job["status"] in ("queued", "running"):
                self.cancel(job["id"])

    # How often the screen is read for popups. Fast reads every 2 seconds: about twice as
    # quick, but a popup shown for under 2 seconds can be missed.
    SPEEDS = {"standard": 1.0, "fast": 0.5}

    def analyze(self, name, rescan=False, speed="standard"):
        def run(job):
            proj = self.project(name)
            if speed in self.SPEEDS:
                proj.cfg = {**proj.cfg, "detection": {**proj.cfg["detection"], "ocr_fps": self.SPEEDS[speed]}}
            job["message"] = "Analyzing audio..."

            def progress(pct, events):
                _check(job)
                job.update(progress=round(5 + pct * 0.9, 1),
                           message=f"Analyzing gameplay... {pct:.0f}% ({events} moments)")

            found = proj.detect(rescan=rescan, on_progress=progress)
            _check(job)
            job.update(progress=96, message="Writing captions...")
            proj.write_captions(found, recaption=True)
            job["message"] = f"Found {len(found)} suggested clips"
            if proj.warnings():
                job["message"] += " (see the warning above the clip list)"
        return self.submit("analyze", name, run)

    def export(self, name, clips):
        def run(job):
            proj = self.project(name)
            rows = [r for r in proj.read_rows() if int(r["clip"]) in clips and not pipeline.is_skipped(r)]
            done = []

            def on_clip(row):
                _check(job)
                done.append(row["clip"])
                job.update(progress=round(100 * (len(done) - 1) / max(1, len(rows))),
                           message=f"Exporting clip {row['clip']} ({len(done)} of {len(rows)})...")

            proj.render_all(rows, self.formats(), on_clip=on_clip)
            job["message"] = f"Exported {len(rows)} clip{'s' if len(rows) != 1 else ''}"
        return self.submit("export", name, run)

    def compile(self, name, clips):
        def run(job):
            proj = self.project(name)
            rows = [r for r in proj.read_rows() if int(r["clip"]) in clips and not pipeline.is_skipped(r)]
            layout = self.formats()[0]
            _check(job)
            missing = [r for r in rows
                       if not (proj.out_dir / layout / f"{proj.output_name(r)}.mp4").exists()]
            if missing:
                job["message"] = f"Exporting {len(missing)} clips first..."
                def on_clip(r):
                    _check(job)
                    job["message"] = f"Exporting clip {r['clip']} first..."
                proj.render_all(missing, self.formats(), on_clip=on_clip)
            _check(job)
            job.update(progress=80)
            out = proj.compile([int(r["clip"]) for r in rows], layout,
                               on_progress=lambda m: job.update(message=m))
            job.update(message=f"Highlight video saved: {out.name}", result=self.media_url(out))
        return self.submit("compile", name, run)

    def formats(self):
        fmt = self.cfg["render"]["format"]
        return ["vertical", "horizontal"] if fmt == "both" else [fmt]

    # ------------------------------------------------------------ API
    def state(self):
        videos = []
        for path in self.library():
            proj = pipeline.Project(path, self.cfg)
            saved = proj.load_highlights()
            scanned = saved is not None and proj.csv_path.exists()
            rows = proj.read_rows() if scanned else []
            videos.append({"name": path.name, "path": str(path), "scanned": scanned,
                           "clips": len(rows), "kept": sum(1 for r in rows if not pipeline.is_skipped(r))})
        lib = music.MusicLibrary(self.cfg)
        return {"videos": videos, "jobs": self.jobs[-30:],
                "music": [p.relative_to(lib.root).as_posix() for p in lib.tracks()],
                "categories": highlights.CATEGORIES,
                "hidden_categories": self.cfg["detection"].get("hidden_categories", []),
                "format": self.formats()[0], "version": __version__, "update": self.update}

    def clips(self, name):
        proj = self.project(name)
        if not proj.csv_path.exists():
            return {"clips": [], "source": "/source/" + _quote(name), "warnings": []}
        layout = self.formats()[0]
        result = []
        for row in proj.read_rows():
            rendered = proj.out_dir / layout / f"{proj.output_name(row)}.mp4"
            result.append({
                **row, "clip": int(row["clip"]), "start": float(row["start"]), "end": float(row["end"]),
                "anchor": float(row["anchor"]), "skip": pipeline.is_skipped(row),
                "category": row.get("category") or highlights.category_of(row["label"]),
                "game_volume": float(row.get("game_volume") or 100),
                "music_volume": float(row.get("music_volume") or 100),
                "framing": row.get("framing") or self.cfg["render"]["vertical_style"],
                "crop_x": float(row.get("crop_x") or 0.5),
                "rendered": self.media_url(rendered) + f"?v={int(rendered.stat().st_mtime)}"
                if rendered.exists() else None,
            })
        return {"clips": result, "source": "/source/" + _quote(name), "warnings": proj.warnings()}

    def update_clip(self, name, clip, fields):
        proj = self.project(name)
        rows = proj.read_rows()
        numeric = {"start", "end", "game_volume", "music_volume", "crop_x"}
        allowed = numeric | {"caption", "music", "skip", "tone", "framing", "category"}
        for row in rows:
            if int(row["clip"]) != clip:
                continue
            for key, value in fields.items():
                if key not in allowed:
                    continue
                if key in numeric:
                    value = f"{max(0.0, float(value)):.2f}"
                elif key == "skip":
                    value = "yes" if value else ""
                row[key] = value
            if float(row["end"]) - float(row["start"]) < 1:
                raise ValueError("A clip must be at least 1 second long")
        proj.write_rows(rows)

    def add_manual(self, name, at):
        return self.project(name).add_manual(at)

    def suggest_caption(self, label, tone):
        picker = captions.CaptionPicker(self.cfg, self.out_root / ".captions_used.json")
        if tone not in ("funny", "motivational"):
            tone = random.choice(["funny", "motivational"])
        line = picker.from_library(label, tone)
        picker.save_state()
        return {"caption": line, "tone": tone}

    def open_folder(self, name):
        folder = self.project(name).out_dir
        folder.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(folder)  # noqa: S606 - opens Explorer on the user's own output folder
        else:
            subprocess.Popen(["xdg-open", str(folder)])
        return str(folder)


def _quote(text):
    from urllib.parse import quote
    return quote(text, safe="")


def make_handler(studio):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # keep the log quiet

        def _json(self, data, code=200):
            body = json.dumps(data).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _file(self, path):
            path = Path(path)
            if not path.is_file():
                self.send_error(404)
                return
            size = path.stat().st_size
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if path.suffix.lower() == ".mkv":
                ctype = "video/x-matroska"
            start, end = 0, size - 1
            match = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
            if match and (match.group(1) or match.group(2)):
                if match.group(1):
                    start = int(match.group(1))
                    end = int(match.group(2)) if match.group(2) else size - 1
                else:
                    start = max(0, size - int(match.group(2)))
                end = min(end, size - 1)
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                with open(path, "rb") as f:
                    f.seek(start)
                    remaining = end - start + 1
                    while remaining > 0:
                        chunk = f.read(min(1 << 20, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # the player stopped reading early; that's normal when seeking

        def do_GET(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            try:
                if url.path == "/":
                    return self._file(WEB / "index.html")
                if url.path.startswith("/static/"):
                    target = (WEB / url.path[len("/static/"):]).resolve()
                    if WEB.resolve() not in target.parents:
                        return self.send_error(403)
                    return self._file(target)
                if url.path.startswith("/media/"):
                    target = (studio.out_root / unquote(url.path[len("/media/"):])).resolve()
                    if studio.out_root.resolve() not in target.parents:
                        return self.send_error(403)
                    return self._file(target)
                if url.path.startswith("/source/"):
                    # Only recordings the user imported can be streamed.
                    return self._file(studio.video(unquote(url.path[len("/source/"):])))
                if url.path == "/api/state":
                    return self._json(studio.state())
                if url.path == "/api/clips":
                    return self._json(studio.clips(query["video"][0]))
                self.send_error(404)
            except KeyError as e:
                self._json({"error": str(e)}, 404)
            except Exception as e:
                traceback.print_exc()
                self._json({"error": str(e)}, 500)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
                path = urlparse(self.path).path
                if path == "/api/import":
                    return self._json({"added": studio.import_paths(data.get("paths", []))})
                if path == "/api/update/check":
                    return self._json(studio.check_update())
                if path == "/api/update":
                    return self._json(studio.install_update())
                if path == "/api/restart":
                    studio.restart()
                    return self._json({"ok": True})
                if path == "/api/cancel":
                    return self._json(studio.cancel(int(data["job"])))
                if path == "/api/remove":
                    studio.remove(data["video"])
                    return self._json({"ok": True})
                if path == "/api/analyze":
                    return self._json(studio.analyze(data["video"], bool(data.get("rescan")),
                                                     data.get("speed", "standard")))
                if path == "/api/export":
                    return self._json(studio.export(data["video"], [int(c) for c in data["clips"]]))
                if path == "/api/compile":
                    return self._json(studio.compile(data["video"], [int(c) for c in data["clips"]]))
                if path == "/api/clip":
                    studio.update_clip(data["video"], int(data["clip"]), data["fields"])
                    return self._json({"ok": True})
                if path == "/api/add":
                    return self._json(studio.add_manual(data["video"], float(data["at"])))
                if path == "/api/caption":
                    return self._json(studio.suggest_caption(data.get("label", ""), data.get("tone")))
                if path == "/api/open":
                    return self._json({"folder": studio.open_folder(data["video"])})
                self.send_error(404)
            except Exception as e:
                traceback.print_exc()
                self._json({"error": str(e)}, 400)

    return Handler


class _Server(ThreadingHTTPServer):
    # Never share a port: on Windows, address reuse lets a second server bind a port an older
    # copy of the app still holds, and requests then reach the wrong (old) server.
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def start_server(cfg, port=0, windowed=True):
    """Start the backend on a background thread. Returns (studio, url).

    port=0 lets Windows pick a free port, so old or duplicate copies can't collide.
    """
    studio = Studio(cfg, windowed)
    server = _Server(("127.0.0.1", port), make_handler(studio))
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return studio, f"http://127.0.0.1:{port}/"
