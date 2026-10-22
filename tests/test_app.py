"""Background tasks (cancel/remove) and in-app updates."""

import io
import time
import zipfile

import pytest

from rlclip import app, config, updater


def wait_for(cond, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "check", lambda cfg: {"current": "0.7.0", "latest": None, "available": False})
    cfg = config.load()
    cfg["output_dir"] = str(tmp_path / "out")
    return app.Studio(cfg)


def test_cancel_running_and_queued_tasks(studio):
    def slow(job):
        for _ in range(500):
            app._check(job)
            time.sleep(0.01)

    running = studio.submit("analyze", "a.mp4", slow)
    queued = studio.submit("analyze", "b.mp4", slow)
    assert wait_for(lambda: running["status"] == "running")
    studio.cancel(queued["id"])
    assert queued["status"] == "cancelled"
    studio.cancel(running["id"])
    assert wait_for(lambda: running["status"] == "cancelled")
    assert queued["status"] == "cancelled"  # the worker skipped it


def test_remove_cancels_that_recordings_tasks(studio, tmp_path):
    video = tmp_path / "game.mp4"
    video.write_bytes(b"x")
    studio.import_paths([str(video)])
    job = studio.submit("analyze", "game.mp4", lambda j: [app._check(j) or time.sleep(0.01) for _ in range(500)])
    assert wait_for(lambda: job["status"] == "running")
    studio.remove("game.mp4")
    assert wait_for(lambda: job["status"] == "cancelled")
    assert studio.library() == []
    assert video.exists()  # the file itself is never deleted


def make_release(files, root="clip-studio-main"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{root}/", "")
        for name, text in files.items():
            z.writestr(f"{root}/{name}", text)
    return buf.getvalue()


def test_update_replaces_code_but_keeps_user_files(tmp_path, monkeypatch):
    install = tmp_path / "install"
    (install / "rlclip").mkdir(parents=True)
    (install / "music" / "hype").mkdir(parents=True)
    (install / "rlclip" / "VERSION").write_text("0.7.0")
    (install / "rlclip" / "__init__.py").write_text("old")
    (install / "requirements.txt").write_text("numpy\n")
    (install / "config.yaml").write_text("player_names: [me]\n")
    (install / "music" / "hype" / "song.mp3").write_text("my song")
    release = make_release({
        "rlclip/__init__.py": "new", "rlclip/VERSION": "0.8.0", "rlclip/new_module.py": "x",
        "requirements.txt": "numpy\n", "config.yaml": "player_names: []\n",
        "music/README.md": "readme", "output/junk.txt": "no",
    })
    monkeypatch.setattr(config, "ROOT", install)
    monkeypatch.setattr(updater, "_get", lambda url, timeout: release)
    assert updater.apply({"update": {"repo": "me/clip-studio"}}) == "0.8.0"
    assert (install / "rlclip" / "__init__.py").read_text() == "new"
    assert (install / "rlclip" / "new_module.py").exists()
    assert (install / "config.yaml").read_text() == "player_names: [me]\n"   # user's settings kept
    assert (install / "music" / "hype" / "song.mp3").read_text() == "my song"
    assert not (install / "output").exists()


def test_update_rejects_unexpected_download(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    (tmp_path / "requirements.txt").write_text("")
    monkeypatch.setattr(updater, "_get", lambda url, timeout: make_release({"evil.py": "x"}))
    with pytest.raises(RuntimeError):
        updater.apply({"update": {"repo": "me/clip-studio"}})
    assert not (tmp_path / "evil.py").exists()


def test_version_check(monkeypatch):
    monkeypatch.setattr(updater, "__version__", "0.7.0")
    monkeypatch.setattr(updater, "_get", lambda url, timeout: b"0.10.0\n")
    assert updater.check({"update": {"repo": "me/x"}})["available"] is True
    monkeypatch.setattr(updater, "_get", lambda url, timeout: b"0.7.0\n")
    assert updater.check({"update": {"repo": "me/x"}})["available"] is False
