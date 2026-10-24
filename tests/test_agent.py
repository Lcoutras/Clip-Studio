"""The Clip Agent's commands (no recordings needed)."""

import json

import pytest

from rlclip import agent, config, pipeline


def test_queue_and_posted(tmp_path):
    cfg = config.load()
    cfg["output_dir"] = str(tmp_path)
    item = tmp_path / "ready" / "2026-10-24_Goal_x"
    item.mkdir(parents=True)
    (item / "meta.json").write_text(json.dumps({"recording": "game.mp4", "clip": 1, "category": "Goal",
                                                "post": {"caption": "x"}, "posted": {}}))
    args = type("A", (), {"item": item.name, "platform": "tiktok", "url": "https://t/1"})()
    agent.cmd_posted(cfg, args)
    q = agent.queue(cfg)
    assert q[0]["posted"]["tiktok"]["url"] == "https://t/1"
    assert agent._ready_item(cfg, "game.mp4", 1) == item
    assert (tmp_path / "agent" / "posted.csv").read_text().splitlines()[1].split(",")[1:3] == ["tiktok", item.name]
    args.platform = "myspace"
    with pytest.raises(SystemExit):
        agent.cmd_posted(cfg, args)


def test_recordings_skip_output_and_old_files(tmp_path):
    import os
    import time
    cfg = config.load()
    cfg["output_dir"] = str(tmp_path / "out")
    cfg["agent"] = {"watch_dirs": [str(tmp_path)], "max_age_days": 14}
    (tmp_path / "out" / "x").mkdir(parents=True)
    (tmp_path / "out" / "x" / "clip.mp4").write_bytes(b"0")
    (tmp_path / "NVIDIA").mkdir()
    new = tmp_path / "NVIDIA" / "new.mp4"
    new.write_bytes(b"0")
    old = tmp_path / "old.mp4"
    old.write_bytes(b"0")
    os.utime(old, (time.time() - 30 * 86400,) * 2)
    assert agent.recordings(cfg, check_length=False) == [new]


def _setup(tmp_path, monkeypatch, rows):
    """A fake analyzed recording in the app's library, wired into the agent commands."""
    cfg = config.load()
    cfg["output_dir"] = str(tmp_path / "out")
    cfg["music"]["dir"] = str(tmp_path / "music")
    video = tmp_path / "game.mp4"
    video.write_bytes(b"0")
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "app_settings.json").write_text(json.dumps({"library": [str(video)]}))
    proj = pipeline.Project(video, cfg)
    proj.write_rows(rows)
    (proj.out_dir / "highlights.json").write_text(json.dumps({"version": pipeline.DETECTION_VERSION, "highlights": [
        {"index": int(r["clip"]), "start": float(r["start"]), "end": float(r["end"]), "mood": "hype",
         "events": [{"source": "reaction", "label": "FUNNY MOMENT", "weight": 6, "detail": "+20dB"}]}
        for r in rows]}))
    monkeypatch.setattr(agent, "_label", lambda path: "Oct 22")
    return cfg, proj


def _rows(n):
    return [{"clip": str(i), "label": "FUNNY MOMENT", "category": "Funny Moment", "score": str(6 + i % 3),
             "start": str(i * 30), "end": str(i * 30 + 8), "anchor": str(i * 30 + 4), "skip": "",
             "caption": "Old line", "tone": "motivational", "source": "auto"} for i in range(1, n + 1)]


def _review(cfg, clip, **kw):
    base = {"recording": "game.mp4", "clip": clip, "approve": False, "reject": None, "flag": False, "note": None,
            "caption": None, "tone": None, "music": None, "category": None, "start": None, "end": None}
    return agent.cmd_review(cfg, type("A", (), {**base, **kw})())


def test_review_edits_logs_and_respects_luke(tmp_path, monkeypatch, capsys):
    cfg, proj = _setup(tmp_path, monkeypatch, _rows(2))
    _review(cfg, 1, approve=True, caption="Way better line", tone="funny", start=28.0, note="start sooner")
    row = proj.read_rows()[0]
    assert (row["agent"], row["caption"], row["alt_caption"], row["tone"], row["start"]) == \
        ("approved", "Way better line", "Old line", "funny", "28.00")
    with pytest.raises(SystemExit):
        _review(cfg, 1, caption="this caption is far too long to read in one second ok")
    with pytest.raises(SystemExit):
        _review(cfg, 1, start=40.0)        # would cut the play off
    with pytest.raises(SystemExit):
        _review(cfg, 1, music="not-a-track.mp3")
    # Luke rejected clip 2 himself: the agent can't bring it back.
    rows = proj.read_rows()
    rows[1].update(skip="yes", reject_reason="boring")
    proj.write_rows(rows)
    _review(cfg, 2, approve=True, note="looks fine")
    assert proj.read_rows()[1]["skip"] == "yes"
    log = [json.loads(l) for l in (tmp_path / "out" / "agent" / "decisions.jsonl").read_text().splitlines()]
    assert log[0]["before"]["caption"] == "Old line" and log[0]["after"]["start"] == "28.00"
    assert log[0]["signal"] == "reaction"
    assert not (tmp_path / "out" / "feedback.json").exists()   # only Luke's own rejections teach the detector


def test_todo_lists_unreviewed_top_clips(tmp_path, monkeypatch, capsys):
    cfg, proj = _setup(tmp_path, monkeypatch, _rows(4))
    _review(cfg, 1, reject="boring", note="nothing happens")
    capsys.readouterr()
    agent.cmd_todo(cfg, type("A", (), {"top": 2})())
    out = capsys.readouterr().out
    assert "3 to review, showing the top 2" in out and "clip  1 " not in out
    assert "caption: 'Old line'" in out


def test_distill_proposes_and_applies_settings(tmp_path, monkeypatch, capsys):
    cfg, proj = _setup(tmp_path, monkeypatch, _rows(6))
    for clip in range(1, 6):
        _review(cfg, clip, reject="boring", note="mic noise, nothing on screen")
    _review(cfg, 6, approve=True, tone="funny", start=178.0)
    proposals, evidence, decided, overrides = agent.distill(cfg)
    assert proposals["detection"]["signal_adjust"] == {"reaction": -2.0}
    # Luke disagrees with one rejection: the evidence follows him.
    rows = proj.read_rows()
    rows[0].update(skip="", reject_reason="")
    proj.write_rows(rows)
    proposals, *_ = agent.distill(cfg)
    assert proposals["detection"]["signal_adjust"] == {"reaction": -1.0}   # 4 of 6 rejected

    monkeypatch.setattr(config, "ROOT", tmp_path)
    (tmp_path / "settings.yaml").write_text("detection:\n  player_names: [postmates85]\n")
    agent.cmd_distill(cfg, None)
    agent.cmd_apply_settings(cfg, None)
    applied = config.load(tmp_path / "settings.yaml")
    assert applied["detection"]["signal_adjust"] == {"reaction": -1.0}
    assert applied["detection"]["player_names"] == ["postmates85"]
    agent.cmd_handoff(cfg, None)
    text = (tmp_path / "out" / "agent" / "handoff.md").read_text()
    assert "mic noise" in text and "signal_adjust" in text


def test_signal_adjust_setting_changes_ranking():
    from rlclip import highlights
    from rlclip.detect import Event
    det = config.load()["detection"]
    assist = [Event(100.0, "ASSIST", 6, "hype", "ocr", "")]
    learned = {"penalty": {"popup:ASSIST": 2.0}, "timing": {}}
    assert highlights.build(assist, [], [], det, 300, learned=learned) == []


def test_caption_add_and_library(tmp_path, monkeypatch):
    from rlclip import captions
    monkeypatch.setattr(config, "ROOT", tmp_path)
    (tmp_path / "captions").mkdir()
    (tmp_path / "captions" / "library.yaml").write_text("GOAL:\n  funny: [Old one]\n")
    cfg = config.load()
    cfg["captions"]["library"] = "captions/library.yaml"
    agent.cmd_caption_add(cfg, type("A", (), {"label": "GOAL", "tone": "funny", "line": "Brand  new line"})())
    lib = captions.load_library(cfg["captions"])
    assert lib["GOAL"]["funny"] == ["Brand new line", "Old one"]
    with pytest.raises(SystemExit):
        agent.cmd_caption_add(cfg, type("A", (), {"label": "NOPE", "tone": "funny", "line": "x"})())


def test_music_add_from_file_and_credit(tmp_path, monkeypatch):
    import shutil
    import subprocess
    if not shutil.which("ffmpeg"):
        pytest.skip("needs ffmpeg")
    cfg = config.load()
    cfg["output_dir"] = str(tmp_path / "out")
    cfg["music"]["dir"] = str(tmp_path / "music")
    src = tmp_path / "song.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=30",
                    "-f", "lavfi", "-i", "color=c=black:s=64x64:d=30", "-shortest", str(src)], check=True)
    base = {"source": str(src), "mood": "hype", "title": "Boost", "artist": "Some Artist", "license": "ncs",
            "attribution": None, "source_url": "https://ncs.io/x", "energy": "high", "notes": "drop at 0:10"}
    with pytest.raises(SystemExit):   # NCS needs a credit line
        agent.cmd_music_add(cfg, type("A", (), base)())
    agent.cmd_music_add(cfg, type("A", (), {**base, "attribution": "Music: Boost by Some Artist"})())
    lib = music_lib = __import__("rlclip.music", fromlist=["x"]).MusicLibrary(cfg)
    assert [t.name for t in lib.tracks("hype")] == ["Some-Artist_Boost.mp3"]
    assert music_lib.credit("Some-Artist_Boost.mp3") == "Music: Boost by Some Artist"
    with pytest.raises(SystemExit):
        agent._download("https://www.youtube.com/watch?v=x", tmp_path / "x")
    # A file already in the music folder is cataloged where it is, not copied.
    by_hand = tmp_path / "music" / "funny" / "Fluffing a Duck.mp3"
    by_hand.parent.mkdir(parents=True)
    shutil.copy(lib.tracks("hype")[0], by_hand)
    agent.cmd_music_add(cfg, type("A", (), {**base, "source": str(by_hand), "mood": "funny", "license": "cc-by",
                                            "title": "Fluffing a Duck", "artist": "Kevin MacLeod",
                                            "attribution": "Music: Fluffing a Duck by Kevin MacLeod (incompetech.com) CC BY 4.0"})())
    assert [t.name for t in lib.tracks("funny")] == ["Fluffing a Duck.mp3"]
    assert lib.credit("Fluffing a Duck.mp3").startswith("Music: Fluffing a Duck")
