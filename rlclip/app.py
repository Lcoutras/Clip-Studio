"""Clip Studio: a small local web app for reviewing, trimming and rendering clips.

Runs a web server on 127.0.0.1 only (nothing is reachable from other computers) and opens
it in the default browser. Scans and renders run one at a time on a background thread.
"""

import json
import mimetypes
import queue
import random
import re
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import captions, config, music, pipeline

WEB = Path(__file__).resolve().parent / "web"


class Studio:
    def __init__(self, cfg, folder=None):
        self.cfg = cfg
        self.out_root = config.resolve(cfg["output_dir"])
        self.out_root.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.out_root / "app_settings.json"
        try:
            self.settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.settings = {}
        if folder:
            self.settings["folder"] = str(folder)
        self.jobs = []
        self.lock = threading.Lock()
        self.queue = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

    # ------------------------------------------------------------ helpers
    def save_settings(self):
        self.settings_path.write_text(json.dumps(self.settings, indent=1), encoding="utf-8")

    def videos(self):
        folder = self.settings.get("folder")
        if not folder or not Path(folder).is_dir():
            return []
        return pipeline.find_videos(folder)

    def project(self, name):
        for video in self.videos():
            if video.name == name:
                return pipeline.Project(video, self.cfg)
        raise KeyError(f"recording not found: {name}")

    def media_url(self, path):
        rel = Path(path).resolve().relative_to(self.out_root.resolve()).as_posix()
        return "/media/" + rel

    # ------------------------------------------------------------ jobs
    def submit(self, kind, video, fn):
        job = {"id": len(self.jobs) + 1, "kind": kind, "video": video, "status": "queued",
               "progress": 0, "message": "Waiting..."}
        with self.lock:
            self.jobs.append(job)
        self.queue.put((job, fn))
        return job

    def _worker(self):
        while True:
            job, fn = self.queue.get()
            job["status"] = "running"
            try:
                fn(job)
                job.update(status="done", progress=100, message="Done")
            except Exception as e:  # report any failure in the UI instead of dying
                traceback.print_exc()
                job.update(status="error", message=str(e)[-500:])

    def scan(self, name, rescan=False):
        def run(job):
            proj = self.project(name)
            job["message"] = "Scanning for plays..."

            def progress(pct, events):
                job.update(progress=round(pct * 0.85, 1),
                           message=f"Scanning for plays... {pct:.0f}% ({events} found)")

            found = proj.detect(rescan=rescan, on_progress=progress)
            job.update(progress=86, message="Writing captions...")
            proj.write_captions(found, recaption=rescan)
            job.update(progress=90, message="Making previews...")
            proj.make_previews()
        return self.submit("scan", name, run)

    def render(self, name, clips):
        def run(job):
            proj = self.project(name)
            rows = [r for r in proj.read_rows() if int(r["clip"]) in clips]
            done = []

            def on_clip(row):
                done.append(row["clip"])
                job.update(progress=round(100 * (len(done) - 1) / max(1, len(rows))),
                           message=f"Rendering clip {row['clip']} ({len(done)} of {len(rows)})...")

            fmt = self.cfg["render"]["format"]
            formats = ["vertical", "horizontal"] if fmt == "both" else [fmt]
            proj.render_all(rows, formats, on_clip=on_clip)
        return self.submit("render", name, run)

    # ------------------------------------------------------------ API
    def state(self):
        out = []
        for video in self.videos():
            proj = pipeline.Project(video, self.cfg)
            saved = proj.load_highlights()
            out.append({"name": video.name, "scanned": saved is not None and proj.csv_path.exists(),
                        "clips": len(saved or [])})
        return {"folder": self.settings.get("folder", ""), "videos": out, "jobs": self.jobs[-20:],
                "music": [p.relative_to(music.MusicLibrary(self.cfg).root).as_posix()
                          for p in music.MusicLibrary(self.cfg).tracks()]}

    def clips(self, name):
        proj = self.project(name)
        found = {h["index"]: h for h in proj.load_highlights() or []}
        windows = proj.make_previews(create=False) if found else {}
        missing = any(not proj.preview_path(i).exists() for i in found)
        busy = any(j["video"] == name and j["status"] in ("queued", "running") for j in self.jobs)
        if missing and not busy:
            self.submit("previews", name, lambda job: proj.make_previews())
        fmt = self.cfg["render"]["format"]
        layout = "horizontal" if fmt == "horizontal" else "vertical"
        result = []
        for row in proj.read_rows() if proj.csv_path.exists() else []:
            idx = int(row["clip"])
            h = found.get(idx, {})
            rendered = proj.out_dir / layout / f"{proj.output_name(row)}.mp4"
            preview = proj.preview_path(idx)
            result.append({
                **row, "clip": idx, "start": float(row["start"]), "end": float(row["end"]),
                "anchor": float(row["anchor"]), "skip": bool(str(row.get("skip", "")).strip()),
                "events": [e["label"] for e in h.get("events", [])],
                "preview": self.media_url(preview) if preview.exists() else None,
                "preview_start": windows.get(idx, 0.0),
                "rendered": self.media_url(rendered) + f"?v={int(rendered.stat().st_mtime)}"
                if rendered.exists() else None,
            })
        return result

    def update_clip(self, name, clip, fields):
        proj = self.project(name)
        rows = proj.read_rows()
        allowed = {"start", "end", "caption", "music", "skip", "tone"}
        for row in rows:
            if int(row["clip"]) == clip:
                for key, value in fields.items():
                    if key not in allowed:
                        continue
                    if key in ("start", "end"):
                        value = f"{max(0.0, float(value)):.2f}"
                    if key == "skip":
                        value = "yes" if value else ""
                    row[key] = value
                if float(row["end"]) - float(row["start"]) < 1:
                    raise ValueError("A clip must be at least 1 second long")
        proj.write_rows(rows)

    def suggest_caption(self, label, tone):
        picker = captions.CaptionPicker(self.cfg, self.out_root / ".captions_used.json")
        if tone not in ("funny", "motivational"):
            tone = random.choice(["funny", "motivational"])
        line = picker.from_library(label, tone)
        picker.save_state()
        return {"caption": line, "tone": tone}


def make_handler(studio):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # keep the console quiet

        def _json(self, data, code=200):
            body = json.dumps(data).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _file(self, path):
            if not path.is_file():
                self.send_error(404)
                return
            size = path.stat().st_size
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
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
            except (BrokenPipeError, ConnectionResetError):
                pass  # the browser stopped the video early; that's normal

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
                    from urllib.parse import unquote
                    target = (studio.out_root / unquote(url.path[len("/media/"):])).resolve()
                    if studio.out_root.resolve() not in target.parents:
                        return self.send_error(403)
                    return self._file(target)
                if url.path == "/api/state":
                    return self._json(studio.state())
                if url.path == "/api/clips":
                    return self._json(studio.clips(query["video"][0]))
                self.send_error(404)
            except Exception as e:
                traceback.print_exc()
                self._json({"error": str(e)}, 500)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
                path = urlparse(self.path).path
                if path == "/api/folder":
                    folder = Path(data["folder"].strip().strip('"'))
                    if not folder.is_dir():
                        return self._json({"error": f"Folder not found: {folder}"}, 400)
                    studio.settings["folder"] = str(folder)
                    studio.save_settings()
                    return self._json({"ok": True})
                if path == "/api/scan":
                    return self._json(studio.scan(data["video"], bool(data.get("rescan"))))
                if path == "/api/render":
                    return self._json(studio.render(data["video"], [int(c) for c in data["clips"]]))
                if path == "/api/clip":
                    studio.update_clip(data["video"], int(data["clip"]), data["fields"])
                    return self._json({"ok": True})
                if path == "/api/caption":
                    return self._json(studio.suggest_caption(data.get("label", ""), data.get("tone")))
                self.send_error(404)
            except Exception as e:
                traceback.print_exc()
                self._json({"error": str(e)}, 400)

    return Handler


def serve(cfg, folder=None, port=8765, open_browser=True):
    studio = Studio(cfg, folder)
    if folder:
        studio.save_settings()
    server = None
    for p in range(port, port + 20):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", p), make_handler(studio))
            port = p
            break
        except OSError:
            continue
    if server is None:
        raise RuntimeError("Could not find a free port for the app")
    url = f"http://127.0.0.1:{port}/"
    print(f"Clip Studio is running at {url}")
    print("Keep this window open while you use it. Close it to quit.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
