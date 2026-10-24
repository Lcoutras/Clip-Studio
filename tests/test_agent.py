"""The Clip Agent's commands (no recordings needed)."""

import json

import pytest

from rlclip import agent, config, pipeline


class FakeProject:
    def __init__(self, tmp_path):
        self.video = tmp_path / "game.mp4"
        self.csv_path = tmp_path / "captions.csv"
        self.rows = [{"clip": "1", "label": "GOAL", "category": "Goal", "start": "10", "end": "18",
                      "anchor": "14", "skip": "", "note": "", "flag": ""}]

    def read_rows(self):
        return [dict(r) for r in self.rows]

    def write_rows(self, rows):
        self.rows = rows


def test_review_marks_clip_without_teaching_detector(tmp_path, monkeypatch):
    cfg = config.load()
    cfg["output_dir"] = str(tmp_path / "out")
    proj = FakeProject(tmp_path)
    proj.csv_path.write_text("x")
    monkeypatch.setattr(agent, "find_video", lambda cfg, name: proj.video)
    monkeypatch.setattr(pipeline, "Project", lambda video, cfg: proj)
    args = type("A", (), {"recording": "game", "clip": 1, "keep": False, "reject": "boring",
                          "flag": True, "note": "nothing happens"})()
    agent.cmd_review(cfg, args)
    row = proj.rows[0]
    assert row["skip"] == "yes" and row["reject_reason"] == "boring" and row["flag"] == "yes"
    assert row["note"] == "Agent: nothing happens"
    assert not (tmp_path / "out" / "feedback.json").exists()   # only Luke's own rejections teach it

    args.reject = "meh"
    with pytest.raises(SystemExit):
        agent.cmd_review(cfg, args)


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
