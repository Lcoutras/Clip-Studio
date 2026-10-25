"""Story mode: drafting a story and turning it into timed lines."""

import json

from rlclip import pipeline, story


def _row(**kw):
    return {"clip": "3", "start": "100.0", "end": "113.0", "anchor": "110.0", "category": "Goal",
            "label": "GOAL", "caption": "Clean", **kw}


def test_posts_before_a_goal_make_the_scenic_route_story():
    events = [{"time": 103.0, "label": "SHOT"}, {"time": 107.2, "label": "POST"},
              {"time": 108.6, "label": "POST"}, {"time": 110.0, "label": "GOAL"}]
    hits = [(101.5, 14.0), (105.0, 16.0), (107.25, 20.0)]
    s = story.suggest(_row(), events, hits, seed_text="game.mp4")
    assert s["title"] == "The net made me work for this one."
    assert s["payoff"] in ("Took the scenic route.", "Third time's the charm.")
    times = [b["at"] for b in s["beats"]]
    assert times == sorted(times) and all(100 <= t < 110 for t in times)
    assert all(b - a >= story.MIN_GAP - 1e-6 for a, b in zip(times, times[1:]))   # readable
    assert any(b["text"] in ("POST.", "CROSSBAR.", "Off the bar...") for b in s["beats"])
    assert len(s["beats"]) <= story.MAX_BEATS
    assert story.suggest(_row(), events, hits, seed_text="game.mp4") == s     # same clip, same story


def test_a_plain_save_gets_an_opener_and_a_payoff():
    s = story.suggest(_row(category="Save", label="SAVE"), [], [], seed_text="x")
    assert s["beats"][0]["at"] == 100.2 and s["payoff"] in ("Not today.", "Denied.") and s["title"] == ""


def test_story_times_are_made_relative_to_the_clip():
    row = {"story": json.dumps({"beats": [{"at": 104.5, "text": "Lost the 50..."}, {"at": 106, "text": " "}],
                                "payoff": "Took the scenic route."})}
    assert pipeline.story_of(row, start=100.0) == {"beats": [{"at": 4.5, "text": "Lost the 50..."}],
                                                    "payoff": "Took the scenic route."}
    assert pipeline.story_of({"story": ""}) is None
    assert pipeline.story_of({"story": json.dumps({"beats": [], "payoff": ""})}) is None
