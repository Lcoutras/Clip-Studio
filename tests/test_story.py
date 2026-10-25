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


def test_replay_story_moments():
    from rlclip import replay
    rep = replay.Replay(path="", name="", match_start_epoch=0, date="", players={"me": 0, "mate": 0, "opp": 1},
                        score=(1, 0), duration=60, events=[replay.ReplayEvent(20.0, "GOAL", "me", 0, 7)])
    rep.touches = [(10.0, "me"), (10.2, "opp"), (11.5, "opp"),      # a 50/50 the opponent won...
                   (13.0, "me")]                                     # ...and you won it back
    # The ball bounces off the crossbar at 17 s with no car near it, then sits on the line.
    rep.ball_track = [(16.8, 0, 4900, 600, 0), (17.0, 100, 5050, 640, 0), (17.2, 150, 4950, 560, 0)]
    rep.ball_track += [(18.0 + i * 0.2, 0, 4980 + i * 10, 93, 0) for i in range(10)]
    rep.car_tracks = {"me": [(17.0, 0, 3000, 17)], "opp": [(17.0, 500, -2000, 17)]}
    labels = [(e["label"], e["time"]) for e in replay.story_events(rep, 100.0, ["me"], 1000)]
    assert ("LOST 50", 110.0) in labels and ("SECOND CHANCE", 113.0) in labels
    assert ("CROSSBAR", 117.0) in labels and any(l == "HANGING" for l, _ in labels)
