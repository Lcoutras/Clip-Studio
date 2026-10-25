"""TikTok uploader: posts the clips in output/Exports to TikTok with their description and hashtags.

Uses TikTok's official Content Posting API with your own (free) TikTok developer app; see
docs/tiktok-setup.md. Nothing goes anywhere but TikTok, and your login stays on this PC.

    python -m rlclip tiktok connect          sign in once (opens the browser)
    python -m rlclip tiktok upload           upload new exports now
    python -m rlclip tiktok upload --watch   keep watching the Exports folder

Two modes (settings.yaml, tiktok.mode):
- "drafts": each clip lands in your TikTok inbox; tap the notification to post it (and add a
  trending sound if you like). Works for public posts before TikTok reviews your app.
- "direct": posts straight to your profile. Until TikTok approves (audits) your app, these posts
  are private and your account must be set to private while posting.
"""

import http.server
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

from . import config, pipeline

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com/v2"
CALLBACK_PORT = 53682
# A tiny page on GitHub Pages that hands TikTok's sign-in code back to this PC
# (TikTok only redirects to https addresses). See docs/tiktok-callback.html.
DEFAULT_REDIRECT = "https://lcoutras.github.io/clip-studio/tiktok-callback.html"
MAX_DESCRIPTION = 2200


class TikTokError(RuntimeError):
    pass


def settings(cfg):
    t = cfg.get("tiktok") or {}
    if not t.get("client_key") or not t.get("client_secret"):
        raise TikTokError("Add your TikTok app's client_key and client_secret to settings.yaml first "
                          "(see docs/tiktok-setup.md).")
    return {"mode": "drafts", "privacy": "PUBLIC_TO_EVERYONE", "max_per_run": None, "max_per_day": None, "watch_minutes": 180,
            "redirect_uri": DEFAULT_REDIRECT, "extra_tags": ["#fyp"], **t}


def _state_dir(cfg):
    d = config.resolve(cfg["output_dir"]) / "tiktok"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _post(url, data=None, token=None, form=False):
    headers = {"Content-Type": "application/x-www-form-urlencoded" if form else "application/json; charset=UTF-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = urllib.parse.urlencode(data).encode() if form else json.dumps(data or {}).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            out = json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            out = json.loads(e.read() or b"{}")
        except ValueError:
            raise TikTokError(f"TikTok answered {e.code}") from None
    err = out.get("error")
    if isinstance(err, dict) and err.get("code") not in (None, "ok"):
        raise TikTokError(f"{err.get('code')}: {err.get('message')}")
    if isinstance(err, str) and err:
        raise TikTokError(f"{err}: {out.get('error_description', '')}")
    return out


# ------------------------------------------------------------ sign-in
def _save_tokens(cfg, tok):
    tok = {**tok, "expires_at": time.time() + float(tok.get("expires_in", 0)) - 60,
           "refresh_expires_at": time.time() + float(tok.get("refresh_expires_in", 0)) - 60}
    (_state_dir(cfg) / "login.json").write_text(json.dumps(tok, indent=1), encoding="utf-8")
    return tok


def connect(cfg, open_browser=webbrowser.open):
    """Sign in to TikTok in the browser and keep the login on this PC."""
    s = settings(cfg)
    scopes = "user.info.basic,video.upload" + (",video.publish" if s["mode"] == "direct" else "")
    state = f"{CALLBACK_PORT}.{secrets.token_urlsafe(16)}"
    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            ok = "code" in got and got.get("state") == state
            self.wfile.write(("<h2>Clip Studio is connected to TikTok. You can close this tab.</h2>" if ok else
                              "<h2>TikTok sign-in didn't finish. Go back to Clip Studio and try again.</h2>").encode())

    server = http.server.HTTPServer(("127.0.0.1", CALLBACK_PORT), Handler)
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_key": s["client_key"], "scope": scopes, "response_type": "code",
        "redirect_uri": s["redirect_uri"], "state": state})
    print("Opening TikTok to sign in. If the browser doesn't open, go to:\n" + url)
    open_browser(url)
    threading.Thread(target=server.handle_request, daemon=True).start()
    deadline = time.time() + 300
    while "code" not in got and "error" not in got and time.time() < deadline:
        time.sleep(0.5)
    server.server_close()
    if got.get("state") != state or "code" not in got:
        raise TikTokError("Sign-in didn't complete: " + (got.get("error_description") or got.get("error") or "timed out"))
    tok = _post(f"{API}/oauth/token/", {"client_key": s["client_key"], "client_secret": s["client_secret"],
                                        "code": got["code"], "grant_type": "authorization_code",
                                        "redirect_uri": s["redirect_uri"]}, form=True)
    _save_tokens(cfg, tok)
    print("Connected. Scopes: " + tok.get("scope", ""))
    return tok


def access_token(cfg):
    path = _state_dir(cfg) / "login.json"
    if not path.exists():
        raise TikTokError("Not connected to TikTok yet: run 'TikTok Uploader.bat' and sign in.")
    tok = json.loads(path.read_text(encoding="utf-8"))
    if time.time() < tok.get("expires_at", 0):
        return tok["access_token"]
    if time.time() >= tok.get("refresh_expires_at", 0):
        raise TikTokError("The TikTok login has expired: sign in again.")
    s = settings(cfg)
    tok = _post(f"{API}/oauth/token/", {"client_key": s["client_key"], "client_secret": s["client_secret"],
                                        "grant_type": "refresh_token", "refresh_token": tok["refresh_token"]},
                form=True)
    return _save_tokens(cfg, tok)["access_token"]


# ------------------------------------------------------------ what to upload
def description(clip, extra_tags=()):
    """The clip's description from its .json (or .txt for older exports), adapted for TikTok."""
    meta = Path(clip).with_suffix(".json")
    if meta.exists():
        try:
            text = json.loads(meta.read_text(encoding="utf-8"))["description"]
            extra = [t for t in extra_tags if t.lower() not in text.lower().split()]
            return (text + (" " + " ".join(extra) if extra else "")).strip()[:MAX_DESCRIPTION]
        except (ValueError, KeyError):
            pass
    txt = Path(clip).with_suffix(".txt")
    text = txt.read_text(encoding="utf-8").strip() if txt.exists() else Path(clip).stem
    tags = [t for t in text.split() if t.startswith("#") and t.lower() != "#shorts"]
    words = [l for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]
    tags += [t for t in extra_tags if t.lower() not in {x.lower() for x in tags}]
    return (" ".join(words) + "\n\n" + " ".join(tags)).strip()[:MAX_DESCRIPTION]


UPLOADED = "Uploaded"   # clips move here (output/Uploaded/<date>/) once TikTok has them


def _identity(clip):
    """(recording, clip number) from the clip's .json: the same play exported twice is one clip."""
    try:
        meta = json.loads(Path(clip).with_suffix(".json").read_text(encoding="utf-8"))
        return f"{meta['recording']}#{meta['clip']}"
    except (OSError, ValueError, KeyError):
        return None


def pending(cfg, done):
    """Vertical clips in output/Exports not uploaded yet, oldest first. Highlight compilations
    and horizontal versions are skipped, and so is a play that was already uploaded (even if it
    was exported again)."""
    root = config.resolve(cfg["output_dir"]) / pipeline.EXPORTS
    sent = {v.get("clip_id") for v in done.values() if v.get("clip_id")}
    clips = [p for p in root.glob("*/*.mp4")
             if "[horizontal]" not in p.name and not p.name.startswith("Highlights")]
    return sorted((p for p in clips if p.relative_to(root).as_posix() not in done and _identity(p) not in sent),
                  key=lambda p: p.stat().st_mtime)


def move_to_uploaded(cfg, clip):
    """Move an uploaded clip (and its .txt/.json) out of Exports into Uploaded/<same date>/, and
    point Clip Studio at its new place so it isn't exported (or uploaded) again."""
    out_root = config.resolve(cfg["output_dir"])
    dest_dir = out_root / UPLOADED / clip.parent.name
    dest_dir.mkdir(parents=True, exist_ok=True)
    old_rel = clip.relative_to(out_root).as_posix()
    for ext in (".mp4", ".txt", ".json"):
        src = clip.with_suffix(ext)
        if src.exists():
            src.replace(dest_dir / src.name)
    new = dest_dir / clip.name
    _repoint_export(cfg, old_rel, new.relative_to(out_root).as_posix())
    if clip.parent.exists():
        pipeline.refresh_posts_csv(clip.parent, cfg)
        if not any(clip.parent.glob("*.mp4")):
            (clip.parent / "posts.csv").unlink(missing_ok=True)
            try:
                clip.parent.rmdir()
            except OSError:
                pass
    return new


def _repoint_export(cfg, old_rel, new_rel):
    """Update the clip's row in its recording's captions sheet (the 'exports' column)."""
    try:
        settings_path = config.resolve(cfg["output_dir"]) / "app_settings.json"
        library = json.loads(settings_path.read_text(encoding="utf-8")).get("library", [])
    except (OSError, ValueError):
        return
    for video in library:
        proj = pipeline.Project(video, cfg)
        if not proj.csv_path.exists():
            continue
        rows = proj.read_rows()
        hit = False
        for r in rows:
            if old_rel in (r.get("exports") or ""):
                r["exports"] = r["exports"].replace(old_rel, new_rel)
                hit = True
        if hit:
            proj.write_rows(rows)
            return


def _log_path(cfg):
    return _state_dir(cfg) / "uploaded.json"


def load_log(cfg):
    try:
        return json.loads(_log_path(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ------------------------------------------------------------ upload
def upload_one(cfg, clip, token, s):
    size = clip.stat().st_size
    source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": size, "total_chunk_count": 1}
    if size > 64 * 1024 * 1024:
        chunk = 10 * 1024 * 1024
        source.update(chunk_size=chunk, total_chunk_count=size // chunk)
    if s["mode"] == "direct":
        info = _post(f"{API}/post/publish/creator_info/query/", {}, token)["data"]
        privacy = s["privacy"] if s["privacy"] in info.get("privacy_level_options", []) else "SELF_ONLY"
        body = {"post_info": {"title": description(clip, s["extra_tags"]), "privacy_level": privacy,
                              "disable_duet": False, "disable_comment": False, "disable_stitch": False},
                "source_info": source}
        init = _post(f"{API}/post/publish/video/init/", body, token)["data"]
    else:
        init = _post(f"{API}/post/publish/inbox/video/init/", {"source_info": source}, token)["data"]
    with open(clip, "rb") as f:
        n = source["total_chunk_count"]
        for i in range(n):
            start = i * source["chunk_size"]
            data = f.read(source["chunk_size"] if i < n - 1 else size - start)
            req = urllib.request.Request(init["upload_url"], data=data, method="PUT", headers={
                "Content-Type": "video/mp4", "Content-Length": str(len(data)),
                "Content-Range": f"bytes {start}-{start + len(data) - 1}/{size}"})
            urllib.request.urlopen(req, timeout=300).read()
    return init["publish_id"]


def status(token, publish_id):
    return _post(f"{API}/post/publish/status/fetch/", {"publish_id": publish_id}, token).get("data", {})


def upload_new(cfg, limit=None, say=print):
    """Upload clips that haven't been uploaded, up to today's limit. Returns how many were sent."""
    s = settings(cfg)
    log = load_log(cfg)
    today = time.strftime("%Y-%m-%d")
    sent_today = sum(1 for v in log.values() if v.get("at", "").startswith(today))
    room = int(s["max_per_run"]) if s.get("max_per_run") else 10 ** 6   # per check (every watch_minutes)
    if s.get("max_per_day"):
        room = min(room, max(0, int(s["max_per_day"]) - sent_today))
    if limit is not None:
        room = min(room, limit)
    root = config.resolve(cfg["output_dir"]) / pipeline.EXPORTS
    for key in list(log):   # uploaded by an older version, before clips moved to Uploaded
        old = root / key
        if old.exists():
            try:
                move_to_uploaded(cfg, old)
            except OSError:
                pass
    todo = pending(cfg, log)
    sent_ids = {v.get("clip_id") for v in log.values() if v.get("clip_id")}
    again = [p for p in root.glob("*/*.mp4") if _identity(p) in sent_ids]
    if again:
        say(f"Skipping {len(again)} clip(s) in Exports that were uploaded before (exported again): "
            + ", ".join(p.name for p in again[:3]) + ("..." if len(again) > 3 else "")
            + ". To send one again, delete its line in output\\tiktok\\uploaded.json.")
    if not todo:
        say("Nothing new in the Exports folder.")
        return 0
    if not room:
        say(f"Daily limit reached ({s['max_per_day']}); {len(todo)} clips wait for tomorrow.")
        return 0
    token = access_token(cfg)
    sent = 0
    for clip in todo[:room]:
        say(f"Uploading {clip.name} ...")
        try:
            pid = upload_one(cfg, clip, token, s)
        except (TikTokError, urllib.error.URLError, OSError) as e:
            say(f"  failed: {e}")
            if "spam_risk" in str(e) or "rate_limit" in str(e):
                break
            continue
        log[clip.relative_to(root).as_posix()] = {"at": time.strftime("%Y-%m-%d %H:%M"), "publish_id": pid,
                                                  "mode": s["mode"], "clip_id": _identity(clip),
                                                  "description": description(clip, s["extra_tags"])}
        _log_path(cfg).write_text(json.dumps(log, indent=1), encoding="utf-8")
        try:
            moved = move_to_uploaded(cfg, clip)
            say(f"  moved to {moved.parent}")
        except OSError as e:   # the file is open somewhere: it stays, the log still prevents a repeat
            say(f"  uploaded, but couldn't move it out of Exports ({e})")
        sent += 1
        say("  sent to your TikTok inbox: open the TikTok notification to post it" if s["mode"] == "drafts"
            else "  posted (it can take a few minutes to show on your profile)")
    left = len(todo) - sent
    if left:
        say(f"{left} more waiting for the next check.")
    return sent


def watch(cfg, say=print):
    s = settings(cfg)
    every = float(s["watch_minutes"])
    say(f"Watching {config.resolve(cfg['output_dir']) / pipeline.EXPORTS} "
        f"{'once a day' if every == 1440 else f'every {every / 60:g} hours' if every % 60 == 0 else f'every {every:g} minutes'}"
        + (f", up to {s['max_per_run']} clips each time" if s.get("max_per_run") else ", sending everything new")
        + ". Close this window to stop.")
    while True:
        try:
            upload_new(cfg, say=say)
        except TikTokError as e:
            say(f"Problem: {e}")
        time.sleep(float(s["watch_minutes"]) * 60)
