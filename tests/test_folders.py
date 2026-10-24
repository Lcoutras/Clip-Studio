"""Where exports and working files go."""

import datetime
import os
import time

from rlclip import config, pipeline


def _cfg(tmp_path):
    cfg = config.load()
    cfg["output_dir"] = str(tmp_path / "output")
    return cfg


def test_export_names_say_what_and_where(tmp_path):
    video = tmp_path / "2026-10-22 18-25-59.mp4"
    proj = pipeline.Project(video, _cfg(tmp_path))
    row = {"clip": "3", "category": "Blooper/Fail", "caption": 'Whiff: of the "year"?'}
    assert proj.export_name(row, "vertical", ["vertical"]) == "Blooper-Fail - Whiff of the year (Oct 22 6.25 PM #3)"
    assert proj.export_name(row, "horizontal", ["vertical", "horizontal"]).endswith("#3) [horizontal]")
    assert proj.out_dir == tmp_path / "output" / "Analysis" / video.stem
    assert pipeline.exports_dir(proj.cfg).name == datetime.date.today().isoformat()


def test_old_folders_are_reorganized(tmp_path):
    cfg = _cfg(tmp_path)
    video = tmp_path / "game.mp4"
    old = tmp_path / "output" / "game"
    (old / "vertical").mkdir(parents=True)
    (old / "highlights.json").write_text("{}")
    clip = old / "vertical" / "game_02_EPIC-SAVE.mp4"
    clip.write_bytes(b"x")
    clip.with_suffix(".txt").write_text("title")
    stamp = time.mktime((2026, 10, 20, 12, 0, 0, 0, 0, -1))
    os.utime(clip, (stamp, stamp))
    (old / "captions.csv").write_text("clip,label,start,end,anchor\n2,EPIC SAVE,1,9,5\n", encoding="utf-8")
    proj = pipeline.Project(video, cfg)   # opening it moves things
    moved = tmp_path / "output" / "Exports" / "2026-10-20" / "game_02_EPIC-SAVE.mp4"
    assert moved.exists() and moved.with_suffix(".txt").exists()
    assert not old.exists() and (proj.out_dir / "highlights.json").exists()
    assert proj.exported(proj.read_rows()[0], "vertical") == moved
