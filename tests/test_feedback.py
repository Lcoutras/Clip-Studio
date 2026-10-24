"""Learning from rejected and exported clips."""

import numpy as np

from rlclip import config, feedback, highlights
from rlclip.detect import Event


def _row(anchor, category="Funny Moment", start=None, end=None):
    return {"anchor": anchor, "category": category, "label": "FUNNY MOMENT", "source": "auto",
            "start": start if start is not None else anchor - 3.5, "end": end if end is not None else anchor + 2}


def _hl(source="reaction", label="FUNNY MOMENT", anchor=0.0):
    return {"events": [{"source": source, "label": label, "weight": 6, "detail": "+20dB"}], "score": 6,
            "start": anchor - 3.5, "end": anchor + 2}


def test_signal_names():
    assert feedback.signal_of([Event(1, "GOAL", 7, "hype", "ocr", ""), Event(2, "SAVE", 3, "hype", "ocr", "")]) == "popup:GOAL"
    assert feedback.signal_of([{"source": "reaction", "label": "FUNNY MOMENT", "weight": 5,
                                "detail": "+14dB (mixed audio)"}]) == "reaction (mixed audio)"
    assert feedback.signal_of([{"source": "manual", "label": "HIGHLIGHT", "weight": 9}]) == ""
    assert feedback.describe("reaction") == "voice reactions on their own"
    assert feedback.describe("replay:EPIC SAVE") == "Epic Saves from replays"


def test_repeated_rejections_rank_that_kind_lower(tmp_path):
    fb = feedback.Feedback(tmp_path / "feedback.json")
    for t in (10, 60, 120):
        fb.reject("a.mp4", _row(t), _hl(anchor=t), "boring")
    fb.reject("a.mp4", _row(200), _hl(anchor=200), "timing")   # about the trim: doesn't count
    fb = feedback.Feedback(tmp_path / "feedback.json")         # saved to disk
    learned = fb.learned()
    assert learned["penalty"]["reaction"] >= 3
    assert any("voice reactions" in r for r in fb.rules())

    det = config.load()["detection"]
    det["reactions_alone"] = True   # the option this test is about
    report = {}
    found = highlights.build([], [(99.0, 15.0)], [], det, 300, [(100.0, 22.0)], learned=learned, report=report)
    assert found == [] and report["lowered"] == {"reaction": 1}
    assert len(highlights.build([], [(99.0, 15.0)], [], det, 300, [(100.0, 22.0)])) == 1


def test_unreject_forgets_and_keeps_balance(tmp_path):
    fb = feedback.Feedback(tmp_path / "feedback.json")
    for t in (10, 60, 120):
        fb.reject("a.mp4", _row(t), _hl(anchor=t), "boring")
    fb.unreject("a.mp4", _row(61))   # the clip moved a second after re-analysis: same record
    assert "reaction" not in fb.learned()["penalty"]
    fb.reject("a.mp4", _row(60), _hl(anchor=60), "boring")
    for t in (300, 400, 500, 600):
        fb.keep("a.mp4", _row(t), _hl(anchor=t))
    assert "reaction" not in fb.learned()["penalty"]   # kept more often than rejected


def test_trims_on_exported_clips_set_timing(tmp_path):
    fb = feedback.Feedback(tmp_path / "feedback.json")
    for t in (50, 150, 250):
        fb.keep("a.mp4", _row(t, "Goal", start=t - 5.5, end=t + 2), _hl("ocr", "GOAL", anchor=t))
    learned = fb.learned()
    assert learned["timing"] == {"Goal": (-2.0, 0.0)}
    det = config.load()["detection"]
    goal = [Event(100.0, "GOAL", 7, "hype", "ocr", "")]
    h = highlights.build(goal, [], [], det, 300, learned=learned)[0]
    assert h.start == 100 - det["pre_seconds"] - 2


def test_rejected_screen_is_recognized():
    rng = np.random.default_rng(1)
    menu = rng.integers(0, 255, 32 * 18, dtype=np.uint8)
    noisy = np.clip(menu.astype(int) + rng.integers(-5, 5, menu.size), 0, 255).astype(np.uint8)
    other = rng.integers(0, 255, 32 * 18, dtype=np.uint8)
    screens = [[menu.tobytes()] * 3]
    assert feedback.looks_rejected([noisy.tobytes(), noisy.tobytes(), other.tobytes()], screens)
    assert not feedback.looks_rejected([noisy.tobytes(), other.tobytes(), other.tobytes()], screens)

    det = config.load()["detection"]
    det["reactions_alone"] = True   # the option this test is about
    report = {}
    found = highlights.build([], [(99.0, 15.0)], [], det, 300, [(100.0, 22.0)], veto=lambda h: True, report=report)
    assert found == [] and report["vetoed"] == 1
    replay_goal = [Event(100.0, "GOAL", 8, "hype", "replay", "your goal")]
    assert highlights.build(replay_goal, [], [], det, 300, veto=lambda h: True)   # replays are trusted


def test_report_text(tmp_path):
    fb = feedback.Feedback(tmp_path / "feedback.json")
    fb.reject("a.mp4", {**_row(10), "note": "lobby"}, _hl(anchor=10), "not_gameplay", [b"\0" * 576])
    text = fb.report()
    assert "Not gameplay: 1" in text and "a.mp4 @ 10s" in text and "lobby" in text
    assert fb.learned()["screens"]
    fb.reset()
    assert fb.summary()["rejected"] == 0
