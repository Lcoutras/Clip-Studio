"""TikTok uploader, with TikTok's API faked."""

import json
import time

import pytest

from rlclip import config, tiktok


@pytest.fixture
def cfg(tmp_path):
    c = config.load()
    c["output_dir"] = str(tmp_path / "out")
    c["tiktok"] = {"client_key": "k", "client_secret": "s", "max_per_day": 2}
    day = tmp_path / "out" / "Exports" / "2026-10-25"
    day.mkdir(parents=True)
    for i, name in enumerate(["Goal - Called it (Oct 22 6.25 PM #1)", "Save - Not today (Oct 22 6.25 PM #2)",
                              "Clear - Return to sender (Oct 22 6.25 PM #3)", "Highlights - 8.15 PM",
                              "Goal - Called it (Oct 22 6.25 PM #1) [horizontal]"]):
        (day / f"{name}.mp4").write_bytes(b"v" * 100)
        (day / f"{name}.txt").write_text(f"{name.split(' (')[0]} | Rocket League\n\n#rocketleague #gaming #shorts\n")
    tiktok._state_dir(c).joinpath("login.json").write_text(json.dumps(
        {"access_token": "t", "expires_at": time.time() + 3600, "refresh_expires_at": time.time() + 9e6}))
    return c


def test_description_swaps_shorts_for_tiktok_tags(cfg, tmp_path):
    clip = next((tmp_path / "out" / "Exports" / "2026-10-25").glob("Goal*#1).mp4"))
    d = tiktok.description(clip, ["#fyp"])
    assert d == "Goal - Called it | Rocket League\n\n#rocketleague #gaming #fyp"


def test_uploads_new_vertical_clips_once_within_daily_limit(cfg, monkeypatch):
    calls = []

    def fake_post(url, data=None, token=None, form=False):
        calls.append(url.rsplit("/v2/", 1)[1])
        return {"data": {"publish_id": f"p{len(calls)}", "upload_url": "https://upload.example/x"}}

    class Res:
        def read(self):
            return b""
    monkeypatch.setattr(tiktok, "_post", fake_post)
    monkeypatch.setattr(tiktok.urllib.request, "urlopen", lambda req, timeout=0: Res())
    messages = []
    assert tiktok.upload_new(cfg, say=messages.append) == 2          # daily limit 2
    assert calls == ["post/publish/inbox/video/init/"] * 2            # drafts mode by default
    log = tiktok.load_log(cfg)
    assert len(log) == 2 and not any("Highlights" in k or "horizontal" in k for k in log)
    assert "1 more waiting" in messages[-1]
    assert tiktok.settings(cfg)["max_per_run"] is None and tiktok.settings(cfg)["watch_minutes"] == 180
    assert tiktok.upload_new(cfg, say=messages.append) == 0          # nothing twice; limit reached today
    assert "Daily limit reached" in messages[-1]


def test_uploaded_clips_move_out_of_exports_and_are_never_resent(cfg, tmp_path, monkeypatch):
    out = tmp_path / "out"
    day = out / "Exports" / "2026-10-25"
    for i, mp4 in enumerate(sorted(day.glob("*.mp4"))):
        mp4.with_suffix(".json").write_text(json.dumps({"description": "d", "recording": "game.mp4", "clip": i}))
    monkeypatch.setattr(tiktok, "_post", lambda *a, **k: {"data": {"publish_id": "p", "upload_url": "https://upload.example/u"}})

    class Res:
        def read(self):
            return b""
    monkeypatch.setattr(tiktok.urllib.request, "urlopen", lambda req, timeout=0: Res())
    cfg["tiktok"]["max_per_day"] = 10
    assert tiktok.upload_new(cfg, say=lambda m: None) == 3
    left = sorted(p.name for p in day.glob("*.mp4"))
    assert left and all("Highlights" in n or "horizontal" in n for n in left)   # only the skipped kinds remain
    moved = sorted(p.name for p in (out / "Uploaded" / "2026-10-25").glob("*"))
    assert len([n for n in moved if n.endswith(".mp4")]) == 3 and any(n.endswith(".json") for n in moved)
    # The same play exported again later is not uploaded twice.
    again = day / "Goal - New caption (Oct 22 6.25 PM #1).mp4"
    again.write_bytes(b"v")
    uploaded_goal = json.loads(next((out / "Uploaded" / "2026-10-25").glob("Goal*.json")).read_text())
    again.with_suffix(".json").write_text(json.dumps({"description": "d2", "recording": "game.mp4",
                                                      "clip": uploaded_goal["clip"]}))
    said = []
    assert tiktok.upload_new(cfg, say=said.append) == 0
    assert any("uploaded before" in m for m in said)


def test_needs_app_keys(tmp_path):
    c = config.load()
    c["output_dir"] = str(tmp_path)
    with pytest.raises(tiktok.TikTokError):
        tiktok.upload_new(c)


def test_connect_catches_the_sign_in_code(tmp_path, monkeypatch):
    import threading
    import urllib.parse
    import urllib.request
    c = config.load()
    c["output_dir"] = str(tmp_path)
    c["tiktok"] = {"client_key": "k", "client_secret": "s"}
    seen = {}
    monkeypatch.setattr(tiktok, "_post", lambda url, data=None, token=None, form=False: seen.update(data) or
                        {"access_token": "a", "refresh_token": "r", "expires_in": 86400,
                         "refresh_expires_in": 31536000, "scope": "user.info.basic,video.upload"})

    def browser(url):   # what tiktok-callback.html does after TikTok sends the user back
        state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
        port = state.split(".")[0]
        def go():
            time.sleep(0.3)
            urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?code=abc&state={urllib.parse.quote(state)}").read()
        threading.Thread(target=go).start()
    tiktok.connect(c, open_browser=browser)
    assert seen["code"] == "abc" and seen["grant_type"] == "authorization_code"
    assert tiktok.access_token(c) == "a"


def test_tiktok_refusals_are_explained():
    e = tiktok.TikTokError("spam_risk_too_many_pending_share: spam_risk_too_many_pending_share")
    assert "unfinished drafts" in tiktok.explain(e)
    assert tiktok.explain(tiktok.TikTokError("weird: thing")) == "failed: weird: thing"


def test_status_report(cfg, monkeypatch):
    tiktok._log_path(cfg).write_text(json.dumps({"2026-10-25/a.mp4": {"at": "2026-10-25 10:00", "publish_id": "p1"},
                                                 "2026-10-25/b.mp4": {"at": "2026-10-25 10:01", "publish_id": "p2"}}))
    answers = {"p1": {"status": "SEND_TO_USER_INBOX"}, "p2": {"status": "FAILED", "fail_reason": "file_format_check_failed"}}
    monkeypatch.setattr(tiktok, "status", lambda token, pid: answers[pid])
    lines = []
    counts = tiktok.report_status(cfg, say=lines.append)
    assert counts == {"SEND_TO_USER_INBOX": 1, "FAILED": 1}
    assert "System notifications" in lines[0] and "file_format_check_failed" in lines[1]


def test_resend_unposted_puts_clips_back_in_the_queue(cfg, tmp_path, monkeypatch):
    out = tmp_path / "out"
    up = out / "Uploaded" / "2026-10-25"
    up.mkdir(parents=True)
    for n in ("a", "b"):
        (up / f"{n}.mp4").write_bytes(b"v")
        (up / f"{n}.txt").write_text("d")
    tiktok._log_path(cfg).write_text(json.dumps({"2026-10-25/a.mp4": {"publish_id": "p1"},
                                                 "2026-10-25/b.mp4": {"publish_id": "p2"}}))
    monkeypatch.setattr(tiktok, "status", lambda t, pid: {"status": "PUBLISH_COMPLETE" if pid == "p1" else "SEND_TO_USER_INBOX"})
    assert tiktok.resend_unposted(cfg, say=lambda m: None) == 1
    assert (out / "Exports" / "2026-10-25" / "b.mp4").exists() and (up / "a.mp4").exists()
    assert list(tiktok.load_log(cfg)) == ["2026-10-25/a.mp4"]
