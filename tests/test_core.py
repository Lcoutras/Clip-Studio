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


# Real OCR text from a user's recording (one string per frame).
REAL_FRAMES = {
    "own goal popup": ("GOAL +100 POSTMATES85SGD", [("GOAL", True)]),
    "own aerial goal": ("[ALM] AERIALGOAL +20 POSTMATES85SC0RED!", [("AERIAL GOAL", True)]),
    "hat trick": ("HATTRICK +25 YOU SCOREDBY PASSENGERPRINCESS [ALM]postmates85", [("HAT TRICK", True)]),
    "opponent goal banner": ("e! e! 2000800 AYYR0N7321SC0RED! GOAL!", []),
    "scoreboard": ("SCOREBOARD R1 ALMOST SCORE GOALS ASSISTS SAVES SHOTS PING 210 PASSENGERPRINCESS "
                   "[ALM]postmates85 736 4 132", []),
    "awards screen": ("FEARLESSWARFARE PASSENGERPRINCESS [ALM]postmates85 GUARDIANANGEL 2EpicSaves "
                      "S24WINS:57 [ALM]fearlesswarfare EXECUTIONER 2Demolitions 1OvertimeGoal FINISHER", []),
    "challenges menu": ("ROCKEE WEEKLY CH Get 3 Assists in READYTO Win 5 Online Ma Play5Matches VIEW", []),
    "save replay box": ("currenttime. RAZFER TENIKTARASSE OVERLORX SAVEREPLAY Enter a name for "
                        "yourreplay [optionall: OK CANCEL", []),
    "loading tip": ("vewhenthegame currenttime. SAVEBYTHE BALL PLAYER ZERO", []),
}


def test_real_frames_only_count_own_popups():
    for name, (text, expected) in REAL_FRAMES.items():
        assert detect.classify_frame(text.split(" "), ["postmates85"]) == expected, name


def test_highlights_merge_and_rank():
    det = config.load()["detection"]
    ocr = [
        detect.Event(100.5, "SHOT", 1, "chill", "ocr"),
        detect.Event(103.0, "EPIC SAVE", 8, "hype", "ocr"),
        detect.Event(300.0, "SHOT", 1, "chill", "ocr"),
    ]
    peaks = [(102.7, 18.0), (500.0, 22.0)]
    found = highlights.build(ocr, peaks, [], det, duration=600)
    labels = [h.label for h in found]
    assert labels == ["EPIC SAVE"]   # a lone SHOT is below min_score, audio alone isn't a clip
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


def test_reactions_extend_plays_and_make_funny_moments():
    det = config.load()["detection"]
    det["reactions_alone"] = True   # the option this test is about
    ocr = [detect.Event(100.0, "GOAL", 7, "hype", "ocr", "GOAL +100")]
    reactions = [(103.0, 20.0), (300.0, 22.0)]   # one right after the goal, one on its own
    found = highlights.build(ocr, [(100.0, 25.0), (298.0, 15.0)], [], det, 600, reactions)
    goal, funny = found
    assert goal.category == "Goal" and goal.end == round(103.0 + det["reaction_tail"], 2)
    assert "reaction" in goal.reason and "goal explosion" in goal.reason
    assert funny.category == "Funny Moment" and funny.label == "FUNNY MOMENT"


def test_other_players_goal_is_a_possible_fail():
    assert detect.other_scorer("e! e! 2000800 AYYR0N7321SC0RED! GOAL!".split(" "), ["postmates85"])
    assert not detect.other_scorer("[ALM] AERIALGOAL +20 POSTMATES85SC0RED!".split(" "), ["postmates85"])
    assert highlights.category_of("FAIL?") == "Blooper/Fail"
    assert highlights.category_of("OTHER GOAL") == "Goal"      # every goal is a Goal clip now


def test_reactions_in_menus_are_ignored():
    """Talking in the main menu once became a 'Funny Moment' clip of the menu."""
    det = config.load()["detection"]
    det["reactions_alone"] = True   # the option this test is about
    menu = [(t, 3.0, False) for t in range(0, 60)]        # menu: no match scoreboard
    match = [(t, 30.0, True) for t in range(60, 120)]      # match: scoreboard on screen
    is_gameplay = detect.gameplay_checker(menu + match)
    reactions = [(30.0, 25.0), (90.0, 25.0)]
    fails = [detect.Event(40.0, "OTHER GOAL", 5, "hype", "ocr"), detect.Event(100.0, "OTHER GOAL", 5, "hype", "ocr")]
    found = highlights.build(fails, [(28.0, 15.0), (88.0, 15.0)], [], det, 200, reactions, is_gameplay=is_gameplay)
    assert all(h.anchor >= 60 for h in found)
    assert {h.category for h in found} == {"Funny Moment", "Goal"}


def test_no_scoreboard_means_no_reaction_clips():
    """A clip of the menu, loading screen and stadium intro never shows a scoreboard; its
    movement looks like a match, so movement alone must not decide."""
    det = config.load()["detection"]
    intro = [(t, 45.0, False) for t in range(0, 40)]
    is_gameplay = detect.gameplay_checker(intro)
    assert not is_gameplay.scoreboard_seen
    assert highlights.build([], [], [], det, 40, [(20.0, 30.0)], is_gameplay=is_gameplay) == []


def test_caption_library_is_short_and_valid():
    """Short-form text should read at scroll speed: about 3-7 words (docs/content-playbook.md)."""
    import yaml
    with open(config.resolve("captions/library.yaml"), encoding="utf-8") as f:
        library = yaml.safe_load(f)
    lines = [line for section in library.values() for pool in section.values() for line in pool]
    assert all(isinstance(line, str) for line in lines)
    too_long = [line for line in lines if len(line.split()) > 8]
    assert too_long == ["What a save! What a save! What a save!"]   # the quick-chat meme stays


def test_old_config_yaml_migrates_personal_settings_only(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    (tmp_path / "config.yaml").write_text(
        "detection:\n  pre_seconds: 5\n  player_names: [\"postmates85\"]\ncaptions:\n  watermark: \"@me\"\n")
    cfg = config.load()
    assert cfg["detection"]["player_names"] == ["postmates85"]
    assert cfg["captions"]["watermark"] == "@me"
    assert cfg["detection"]["pre_seconds"] == config.DEFAULTS["detection"]["pre_seconds"]  # new default wins
    assert (tmp_path / "settings.yaml").exists() and (tmp_path / "config.old.yaml").exists()
    assert not (tmp_path / "config.yaml").exists()


def test_scoreboard_only_during_matches():
    """Strips cut from real frames: two live matches (your team's box is on the left, so the
    colours swap) and the menu, loading screen and stadium intro that once became clips."""
    import numpy as np
    from PIL import Image
    folder = config.ROOT / "tests" / "fixtures" / "scoreboard"
    def visible(name):
        rgb = np.asarray(Image.open(folder / name).convert("RGB"))
        return detect.scoreboard_visible(rgb[..., ::-1])   # the scan hands over BGR
    assert visible("match_orange_left.png") and visible("match_blue_left.png")
    for name in ("main_menu.png", "main_menu_2.png", "loading.png", "stadium_intro.png", "team_intro_vs.png"):
        assert not visible(name), name


def test_gameplay_uses_scoreboard_when_seen_and_keeps_post_goal_reactions():
    det = config.load()["detection"]
    timeline = [(t, 40.0, False) for t in range(0, 60)]     # stadium intro: lots of movement, no scoreboard
    timeline += [(t, 30.0, True) for t in range(60, 120)]   # the match
    timeline += [(t, 30.0, False) for t in range(120, 135)] # goal replay hides the scoreboard
    is_gameplay = detect.gameplay_checker(timeline)
    assert not is_gameplay(30) and is_gameplay(90)
    goal = [detect.Event(118.0, "GOAL", 7, "hype", "ocr", "GOAL +100")]
    found = highlights.build(goal, [], [], det, 200, [(30.0, 25.0), (123.0, 25.0)], is_gameplay=is_gameplay)
    assert [h.label for h in found] == ["GOAL"]               # no clip from the intro
    assert found[0].end >= 123.0                             # the celebration during the replay is kept


def test_lone_assist_is_a_clip_with_the_pass():
    det = config.load()["detection"]
    found = highlights.build([detect.Event(300.0, "ASSIST", *detect.EVENTS["ASSIST"][1:], "ocr")], [], [], det, 600)
    assert [h.category for h in found] == ["Assist"]
    assert found[0].start == 300.0 - det["pre_seconds"] - 2


def test_captions_follow_the_category(tmp_path):
    cfg = config.load()
    picker = captions.CaptionPicker(cfg, tmp_path / "used.json")
    assist = set(picker.library["ASSIST"]["funny"])
    fail = set(picker.library["FAIL?"]["funny"])
    post = set(picker.library["POST"]["funny"])
    assert picker.from_library("ASSIST", "funny") in assist
    assert picker.from_library("ASSIST", "funny", "Assist") in assist
    assert picker.from_library("GOAL", "funny", "Fail?") in fail          # re-labelled in Clip Studio
    assert picker.from_library("FUNNY MOMENT", "funny", "Post") in post   # made a Post clip by hand
    assert picker.from_library("HIGHLIGHT", "funny", "Post") in post
    assert highlights.category_of("POST") == "Post" and "Post" in highlights.CATEGORIES


def test_changing_category_changes_the_stamp_type():
    assert highlights.label_for("Blooper/Fail", "FUNNY MOMENT") == "BLOOPER"
    assert highlights.label_for("Post", "GOAL") == "POST"
    assert highlights.label_for("Save", "SAVIOR") == "SAVIOR"           # a more specific type that fits stays
    assert highlights.label_for("Goal", "AERIAL GOAL") == "AERIAL GOAL"
    assert highlights.label_for("Epic Save", "SAVE") == "EPIC SAVE"
    assert highlights.stamp_text("GOAL AGAINST") == "GOAL!" and highlights.stamp_text("BLOOPER") == ""


def test_reactions_need_something_in_the_game_and_are_capped():
    """52 'Big reaction on the mic' clips in one 20-minute session: chatting isn't a play."""
    det = config.load()["detection"]
    det["reactions_alone"] = True   # the option this test is about
    talk = [(t, 20.0 + (t % 7)) for t in range(30, 1200, 23)]          # loud mic all session
    game = [(t - 1.5, 15.0) for t, _ in talk[::2]]                      # the game did something before half
    found = highlights.build([], game, [], det, 1200, talk)
    assert len(found) == round(1200 / 600 * det["max_reactions_per_10min"])   # loudest few only
    assert all(any(h.anchor - 4 <= g <= h.anchor + 0.5 for g, _ in game) for h in found)
    assert highlights.build([], [], [], det, 1200, talk) == []          # no game sound: just talking


def test_goal_replay_frames_are_not_new_plays():
    replay = "SCORED BY [TTV] postmates85 PASSENGER PRINCESS 0:36 68 MPH GOAL PRESS TO TOGGLE VIEW PRESS TO SKIP"
    assert detect.classify_frame(replay.split(" "), ["postmates85"]) == []
    assert not detect.other_scorer("SCORED BY AYYR0N7321 GOAL PRESS TOSKIP".split(" "), ["postmates85"])


def test_game_first_voice_alone_is_never_a_clip():
    """Every clip Luke rejected was a mic reaction on its own: by default only the game makes clips."""
    det = config.load()["detection"]
    goal = [detect.Event(100.0, "GOAL", 7, "hype", "ocr", "GOAL +100")]
    talk = [(103.0, 30.0), (300.0, 40.0), (500.0, 35.0)]
    found = highlights.build(goal, [(100.0, 25.0), (299.0, 20.0), (499.0, 20.0)], [], det, 600, talk)
    assert [h.label for h in found] == ["GOAL"]
    assert found[0].end >= 103.0   # the celebration still extends the goal clip


def test_clears_and_centers_need_a_big_hit_and_are_capped():
    det = config.load()["detection"]
    clears = [detect.Event(float(t), "CLEAR", *detect.EVENTS["CLEAR BALL"][1:], "ocr") for t in range(50, 600, 40)]
    assert highlights.build(clears, [], [], det, 600) == []                    # no big hit in the game audio
    hits = [(e.time - 0.3, 14.0) for e in clears]                              # every one was a loud hit
    found = highlights.build(clears, hits, [], det, 600)
    assert 0 < len(found) <= round(600 / 600 * det["max_minor_per_10min"])
    assert {h.category for h in found} == {"Clear"}
