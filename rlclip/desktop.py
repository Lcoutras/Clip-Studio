"""Clip Studio desktop window.

Opens the app in its own window (Windows' built-in Edge WebView2, via pywebview) with no
console. Falls back to the default browser if pywebview isn't installed.
"""

import sys
import threading
import time
import traceback
import webbrowser

from . import app, config


def _log_to_file(cfg):
    # pythonw has no console; keep a log so problems can be diagnosed.
    log_dir = config.resolve(cfg["output_dir"])
    log_dir.mkdir(parents=True, exist_ok=True)
    log = open(log_dir / "clipstudio.log", "a", buffering=1, encoding="utf-8")
    sys.stdout = sys.stderr = log
    print(f"\n=== Clip Studio started {time.strftime('%Y-%m-%d %H:%M:%S')}")


class Api:
    """Functions the page can call as window.pywebview.api.<name>()."""

    def __init__(self, studio):
        self.studio = studio
        self.window = None

    def browse(self):
        import webview
        types = ("Videos (*.mp4;*.mkv;*.mov;*.flv;*.ts;*.webm;*.avi)", "All files (*.*)")
        kind = webview.FileDialog.OPEN if hasattr(webview, "FileDialog") else webview.OPEN_DIALOG
        paths = self.window.create_file_dialog(kind, allow_multiple=True, file_types=types)
        return list(paths or [])

    def browse_folder(self):
        import webview
        kind = webview.FileDialog.FOLDER if hasattr(webview, "FileDialog") else webview.FOLDER_DIALOG
        paths = self.window.create_file_dialog(kind)
        return list(paths or [])


def _attach_drop(window, studio):
    """Let users drop video files onto the window. pywebview passes real file paths."""
    try:
        from webview.dom import DOMEventHandler
    except ImportError:
        return

    def on_drop(event):
        files = (event.get("dataTransfer") or {}).get("files") or []
        paths = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
        if not paths:
            return
        try:
            added = studio.import_paths(paths)
            window.evaluate_js(f"window.onImported && window.onImported({added!r})")
        except Exception as e:
            window.evaluate_js(f"alert({str(e)!r})")

    try:
        window.dom.document.events.dragover += DOMEventHandler(lambda e: None, True, True)
        window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)
    except Exception:
        traceback.print_exc()


def main(cfg, windowed=True):
    if sys.stdout is None or sys.executable.lower().endswith("pythonw.exe"):
        _log_to_file(cfg)
    studio, url = app.start_server(cfg)
    print(f"Clip Studio backend at {url}")
    try:
        import webview
    except ImportError:
        webview = None
    if not windowed or webview is None:
        print("Opening in your browser. Keep this window open while you use Clip Studio.")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return
    api = Api(studio)
    window = webview.create_window("Clip Studio", url, width=1500, height=940, min_size=(1100, 680),
                                   js_api=api, background_color="#0f1115", text_select=True)
    api.window = window
    window.events.loaded += lambda: threading.Thread(target=_attach_drop, args=(window, studio),
                                                     daemon=True).start()
    webview.start()
