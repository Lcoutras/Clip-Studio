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
import subprocess
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import captions, config, highlights, music, pipeline

WEB = Path(__file__).resolve().parent / "web"


class Studio:
    def __init__(self, cfg):
        self.cfg = cfg
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
            job.update(status="running", message="Starting...")
            try:
                fn(job)
                job.update(status="done", progress=100)
                job.setdefault("result", None)
                if job["message"].endswith("..."):
                    job["message"] = "Done"
            except Exception as e:  # show any failure in the app instead of dying
                traceback.print_exc()
                job.update(status="error", message=str(e)[-400:])

    def analyze(self, name, rescan=False):
        def run(job):
            proj = self.project(name)
            job["message"] = "Analyzing audio..."

            def progress(pct, events):
                job.update(progress=round(5 + pct * 0.9, 1),
                           message=f"Analyzing gameplay... {pct:.0f}% ({events} moments)")

            found = proj.detect(rescan=rescan, on_progress=progress)
            job.update(progress=96, message="Writing captions...")
            proj.write_captions(found, recaption=True)
            job["message"] = f"Found {len(found)} suggested clips"
        return self.submit("analyze", name, run)

    def export(self, name, clips):
        def run(job):
            proj = self.project(name)
            rows = [r for r in proj.read_rows() if int(r["clip"]) in clips and not pipeline.is_skipped(r)]
            done = []

            def on_clip(row):
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
            missing = [r for r in rows
                       if not (proj.out_dir / layout / f"{proj.output_name(r)}.mp4").exists()]
            if missing:
                job["message"] = f"Exporting {len(missing)} clips first..."
                proj.render_all(missing, self.formats(),
                                on_clip=lambda r: job.update(message=f"Exporting clip {r['clip']} first..."))
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
                "format": self.formats()[0]}

    def clips(self, name):
        proj = self.project(name)
        if not proj.csv_path.exists():
            return {"clips": [], "source": "/source/" + _quote(name)}
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
        return {"clips": result, "source": "/source/" + _quote(name)}

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
                if path == "/api/remove":
                    studio.remove(data["video"])
                    return self._json({"ok": True})
                if path == "/api/analyze":
                    return self._json(studio.analyze(data["video"], bool(data.get("rescan"))))
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


def start_server(cfg, port=8765):
    """Start the backend on a background thread. Returns (studio, url)."""
    studio = Studio(cfg)
    server = None
    for p in range(port, port + 30):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", p), make_handler(studio))
            port = p
            break
        except OSError:
            continue
    if server is None:
        raise RuntimeError("Could not find a free port for Clip Studio")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return studio, f"http://127.0.0.1:{port}/"
