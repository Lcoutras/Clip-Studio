"""Replay events -> clips, lining replays up with recordings, and getting rrrocket on Windows."""

import io
import json
import zipfile

from rlclip import config, replay
from rlclip.replay import Replay, ReplayEvent

# Goal times from a real 2v2 replay (seconds since its first frame).
GOALS = [15.6, 62.5, 85.2, 182.6, 214.2, 274.0, 312.1, 352.0, 387.3]


def test_align_corrects_a_wrong_clock_estimate():
    true_offset = 25.0
    banners = [g + true_offset + 0.8 for g in GOALS] + [30.0, 500.0]   # plus unrelated banners
    offset, hits = replay.align(GOALS, banners, prior=18.0)            # clock estimate 7s off
    assert hits == len(GOALS)
    assert abs(offset - (true_offset + 0.8)) < 0.01


def test_align_without_a_clock_needs_several_matching_goals():
    assert replay.align(GOALS, [100.0], prior=None) == (None, 0)
    offset, hits = replay.align(GOALS, [g + 600 for g in GOALS[:4]], prior=None)
    assert hits == 4 and abs(offset - 600) < 0.01


def make_replay(events):
    return Replay("x.replay", "test", 0, "2026-10-22", {"me": 1, "mate": 1, "them": 0}, (1, 2), 400, events)


def test_events_become_your_plays():
    rep = make_replay([
        ReplayEvent(10.0, "AERIAL HIT", "me", 1, 2),
        ReplayEvent(10.5, "GOAL", "me", 1, 6, speed_kph=107),
        ReplayEvent(50.0, "EPIC SAVE", "me", 1, 8), ReplayEvent(50.0, "SAVE", "me", 1, 4),
        ReplayEvent(90.0, "GOAL", "mate", 1, 6, speed_kph=133), ReplayEvent(90.0, "ASSIST", "me", 1, 3),
        ReplayEvent(120.0, "GOAL", "them", 0, 6, speed_kph=90),
        ReplayEvent(150.0, "SAVE", "them", 0, 4),                  # someone else's: ignored
    ])
    events = replay.to_events(rep, offset=100.0, player_names=["Me"], duration=1000)
    got = [(e.time, e.label, e.stat) for e in events if e.weight >= 3]
    assert (110.5, "AERIAL GOAL", "107 KM/H") in got
    assert (150.0, "EPIC SAVE", "") in got
    assert not any(label == "SAVE" for _, label, _ in got)        # the epic save isn't counted twice
    assert (190.0, "ASSIST", "133 KM/H") in got
    assert (220.0, "FAIL?", "90 KM/H") in got
    assert all(t != 250.0 for t, _, _ in got)


def test_rrrocket_is_downloaded_from_the_latest_release(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("rrrocket-0.11.8-x86_64-pc-windows-msvc/rrrocket.exe", b"MZ fake exe")
    release = {"assets": [
        {"name": "rrrocket-0.11.8-x86_64-unknown-linux-musl.tar.gz", "browser_download_url": "https://x/linux"},
        {"name": "rrrocket-0.11.8-x86_64-pc-windows-msvc.zip", "browser_download_url": "https://x/win"},
    ]}

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        return Response(json.dumps(release).encode() if "api.github.com" in url else buf.getvalue())

    monkeypatch.setattr(replay.urllib.request, "urlopen", fake_urlopen)
    path = replay.install_rrrocket()
    assert path.endswith("rrrocket.exe")
    assert (tmp_path / "tools" / "rrrocket.exe").read_bytes() == b"MZ fake exe"


def test_recording_start_from_obs_and_nvidia_names(tmp_path):
    import datetime
    for name in ("2026-10-22 23-44-09.mp4", "2026-10-22_23-44-09.mp4",
                 "Rocket League 2026.10.22 - 23.44.09.02.mp4"):
        f = tmp_path / name
        f.write_bytes(b"")
        assert replay.recording_start(f) == datetime.datetime(2026, 10, 22, 23, 44, 9).timestamp()
