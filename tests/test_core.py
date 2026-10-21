"""Quick checks for the parts that don't need a real recording.  Run: python -m pytest tests"""

from types import SimpleNamespace

from rlclip import captions, config, detect, highlights


def test_keywords_joined_and_fuzzy():
    assert detect.match_keywords(["EPIC", "SAVE"]) == ["EPIC SAVE"]
    assert detect.match_keywords(["EPICS", "SAVE"]) == ["EPIC SAVE"]
    assert detect.match_keywords(["AERlAL GOAL"]) == ["AERIAL GOAL"]
    assert detect.match_keywords(["+100", "SAVE"]) == ["SAVE"]
    assert detect.match_keywords(["SHOT ON GOAL", "SAVE"]) == ["SHOT ON GOAL", "SAVE"]
    assert detect.match_keywords(["BOOST", "120"]) == []


def test_highlights_merge_and_rank():
    det = config.load()["detection"]
    ocr = [
        detect.Event(100.5, "SHOT", 1, "chill", "ocr"),
        detect.Event(103.0, "EPIC SAVE", 8, "hype", "ocr"),
        detect.Event(300.0, "ASSIST", 3, "chill", "ocr"),
    ]
    peaks = [(102.7, 18.0), (500.0, 22.0)]
    found = highlights.build(ocr, peaks, [], det, duration=600)
    labels = [h.label for h in found]
    assert labels == ["EPIC SAVE", "HYPE"]          # lone ASSIST is below min_score
    save = found[0]
    assert save.anchor == 102.7                      # snapped to the audio hit
    assert save.start == round(102.7 - det["pre_seconds"], 2)


def test_markers(tmp_path):
    f = tmp_path / "m.markers.txt"
    f.write_text("# my picks\n1:02 nasty flick\n1:00:05\n")
    times = [e.time for e in detect.read_markers(f)]
    assert times == [62, 3605]


def test_library_and_ai_caption(tmp_path, monkeypatch):
    cfg = config.load()
    cfg["captions"]["source"] = "library"
    picker = captions.CaptionPicker(cfg, tmp_path / "used.json")
    line = picker.from_library("AERIAL GOAL", "funny")
    assert isinstance(line, str) and line

    # Fake API client: check we parse the structured response.
    calls = {}

    def create(**kwargs):
        calls.update(kwargs)
        text = SimpleNamespace(type="text", text='{"caption": "Sent to the moon"}')
        return SimpleNamespace(stop_reason="end_turn", content=[text])

    picker.client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    monkeypatch.setattr(captions.ffmpeg, "run", lambda args: open(args[-1], "wb").write(b"\xff\xd8"))
    out = picker.from_ai(tmp_path / "clip.mp4", 5.0, "AERIAL GOAL", "funny", tmp_path)
    assert out == "Sent to the moon"
    assert calls["model"] == "claude-opus-5-5"
    assert sum(1 for c in calls["messages"][0]["content"] if c["type"] == "image") == 4
