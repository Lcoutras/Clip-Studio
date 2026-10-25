"""Reads Rocket League .replay files with rrrocket (free, open source) and turns them into events.

A replay records exactly what happened: every goal, save, epic save, shot, assist, aerial hit,
demo and post/crossbar hit, per player, with timestamps, plus where the ball was. That's far more
reliable than reading the screen, and it gives real details for captions (shot speed, height).

rrrocket: https://github.com/nickbabcock/rrrocket (MIT). On Windows the app downloads the
official release into tools/ the first time it's needed.
"""

import io
import json
import os
import shutil
import subprocess
import urllib.request
import zipfile
from dataclasses import dataclass, field

from . import config, ffmpeg

TOOLS = "tools"
RRROCKET_RELEASES = "https://api.github.com/repos/nickbabcock/rrrocket/releases/latest"

# PRI_TA counters -> (event label, weight). The counter going up marks the moment it happened.
STATS = {
    "TAGame.PRI_TA:MatchGoals": ("GOAL", 6),
    "TAGame.PRI_TA:MatchEpicSaves": ("EPIC SAVE", 8),
    "TAGame.PRI_TA:MatchSaves": ("SAVE", 5.5),
    "TAGame.PRI_TA:MatchAssists": ("ASSIST", 3),
    "TAGame.PRI_TA:MatchShots": ("SHOT", 1),
    "TAGame.PRI_TA:MatchAerialHits": ("AERIAL HIT", 2),
    "TAGame.PRI_TA:MatchDemolishes": ("DEMO", 4),
    "TAGame.PRI_TA:MatchCrossbarHits": ("POST", 3),
}
UU_PER_S_TO_KPH = 0.036   # Unreal units are centimetres


@dataclass
class ReplayEvent:
    time: float           # seconds since the replay's first frame
    label: str
    player: str
    team: int
    weight: float
    speed_kph: float = 0.0
    height_uu: float = 0.0


@dataclass
class Replay:
    path: str
    name: str
    match_start_epoch: float
    date: str
    players: dict           # name -> team
    score: tuple
    duration: float
    events: list = field(default_factory=list)
    stats: list = field(default_factory=list)
    touches: list = field(default_factory=list)       # [(time, player name)] every ball touch
    ball_track: list = field(default_factory=list)    # [(time, x, y, z, speed uu/s)]
    car_tracks: dict = field(default_factory=dict)    # player name -> [(time, x, y, z)]


# ---------------------------------------------------------------- rrrocket

def _exe_name():
    return "rrrocket.exe" if os.name == "nt" else "rrrocket"


def find_rrrocket():
    local = config.resolve(TOOLS) / _exe_name()
    if local.exists():
        return str(local)
    return shutil.which("rrrocket")


def install_rrrocket(on_progress=lambda m: None):
    """Download the official Windows build of rrrocket into tools/. Returns its path."""
    on_progress("Downloading the replay reader (one time)...")
    req = urllib.request.Request(RRROCKET_RELEASES, headers={"User-Agent": "ClipStudio"})
    with urllib.request.urlopen(req, timeout=30) as res:
        release = json.load(res)
    asset = next((a for a in release.get("assets", [])
                  if "windows" in a["name"].lower() and a["name"].lower().endswith(".zip")), None)
    if asset is None:
        raise RuntimeError("Couldn't find the Windows replay reader download")
    with urllib.request.urlopen(asset["browser_download_url"], timeout=120) as res:
        archive = zipfile.ZipFile(io.BytesIO(res.read()))
    member = next((n for n in archive.namelist() if n.lower().endswith("rrrocket.exe")), None)
    if member is None:
        raise RuntimeError("The replay reader download didn't contain rrrocket.exe")
    dest = config.resolve(TOOLS) / "rrrocket.exe"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(archive.read(member))
    return str(dest)


def parse_json(path, on_progress=lambda m: None):
    exe = find_rrrocket()
    if exe is None:
        if os.name != "nt":
            raise RuntimeError("rrrocket isn't installed")
        exe = install_rrrocket(on_progress)
    on_progress("Reading the replay...")
    out = subprocess.run([exe, "-n", str(path)], capture_output=True, creationflags=ffmpeg.NO_WINDOW)
    if out.returncode != 0:
        raise RuntimeError("Couldn't read the replay: " + out.stderr.decode(errors="replace")[-500:])
    return json.loads(out.stdout)


# ---------------------------------------------------------------- events

def extract(data, path=""):
    props = data.get("properties", {})
    frames = data.get("network_frames", {}).get("frames", [])
    objects = data.get("objects", [])
    if not frames:
        raise RuntimeError("This replay has no match data in it")
    t0 = frames[0]["time"]

    names, counters, events = {}, {}, []
    balls, ball_track = set(), []           # (time, x, y, z) of the live ball
    ball_full = []                          # (time, x, y, z, speed) for story detection
    car_pri, car_track, touches = {}, {}, []
    for frame in frames:
        t = frame["time"] - t0
        for actor in frame.get("new_actors", []):
            if objects[actor["object_id"]].startswith("Archetypes.Ball."):
                balls.add(actor["actor_id"])
        for actor in frame.get("deleted_actors", []):
            balls.discard(actor)
        for upd in frame.get("updated_actors", []):
            name = objects[upd["object_id"]]
            attr = upd.get("attribute", {})
            aid = upd["actor_id"]
            if name == "Engine.PlayerReplicationInfo:PlayerName":
                names[aid] = attr.get("String", "")
            elif name == "Engine.PlayerReplicationInfo:Team":
                names.setdefault(("team", aid), attr)
            elif name == "Engine.Pawn:PlayerReplicationInfo":
                car_pri[aid] = (attr.get("ActiveActor") or {}).get("actor")
            elif name == "TAGame.PRI_TA:BallTouches" and "Int" in attr:
                before = counters.get((aid, name), 0)
                counters[(aid, name)] = attr["Int"]
                if attr["Int"] > before:
                    touches.append((t, aid))
            elif name in STATS and "Int" in attr:
                value, before = attr["Int"], counters.get((aid, name), 0)
                counters[(aid, name)] = value
                if value > before:
                    label, weight = STATS[name]
                    events.append(ReplayEvent(t, label, aid, -1, weight))
            elif name == "TAGame.RBActor_TA:ReplicatedRBState":
                rb = attr.get("RigidBody") or {}
                loc = rb.get("location")
                if not loc:
                    continue
                if aid in balls:
                    ball_track.append((t, loc["x"], loc["y"], loc["z"]))
                    v = rb.get("linear_velocity") or {}
                    # Velocities are stored in units/s x 100 by some replay versions; speed only
                    # matters relative to itself here, so either scale works.
                    sp = (v.get("x", 0) ** 2 + v.get("y", 0) ** 2 + v.get("z", 0) ** 2) ** 0.5 if v else 0.0
                    ball_full.append((t, loc["x"], loc["y"], loc["z"], sp))
                elif aid in car_pri:
                    car_track.setdefault(aid, []).append((t, loc["x"], loc["y"], loc["z"]))

    teams = {p.get("Name"): p.get("Team", -1) for p in props.get("PlayerStats", [])}
    for e in events:
        e.player = names.get(e.player, "?")
        e.team = teams.get(e.player, -1)
        if e.label == "GOAL":
            e.speed_kph, e.height_uu = _ball_at(ball_track, e.time)
    events.sort(key=lambda e: e.time)
    cars = {}
    for car, track in car_track.items():
        who = names.get(car_pri.get(car), "?")
        cars.setdefault(who, []).extend(track)
    for track in cars.values():
        track.sort()

    return Replay(
        path=str(path), name=props.get("ReplayName", ""),
        match_start_epoch=float(props.get("MatchStartEpoch") or 0), date=props.get("Date", ""),
        players=teams, score=(props.get("Team0Score", 0), props.get("Team1Score", 0)),
        duration=frames[-1]["time"] - t0, events=events,
        stats=[{k: p.get(k) for k in ("Name", "Team", "Goals", "Assists", "Saves", "Shots", "Score")}
               for p in props.get("PlayerStats", [])],
        touches=[(t, names.get(aid, "?")) for t, aid in touches], ball_track=ball_full, car_tracks=cars)


def _ball_at(track, t, window=0.6):
    """Ball speed (km/h) just before t and its height when it went in."""
    import bisect
    times = [p[0] for p in track]
    lo, hi = bisect.bisect_left(times, t - window - 0.3), bisect.bisect_right(times, t + 0.1)
    pts = track[lo:hi]
    speed = 0.0
    for a, b in zip(pts, pts[1:]):
        dt = b[0] - a[0]
        if 0.01 < dt < 0.5:
            d = ((b[1] - a[1]) ** 2 + (b[2] - a[2]) ** 2 + (b[3] - a[3]) ** 2) ** 0.5
            speed = max(speed, d / dt * UU_PER_S_TO_KPH)
    height = pts[-1][3] if pts else 0.0
    return round(min(speed, 300.0), 1), round(height, 1)


def load(path, on_progress=lambda m: None):
    return extract(parse_json(path, on_progress), path)


# ---------------------------------------------------------------- matching replays to recordings

def documents_folder():
    """The user's real Documents folder (on many PCs it's redirected into OneDrive)."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:   # 5 = My Documents
            return buf.value
    return os.path.expanduser("~/Documents")


def replay_folders(extra_dirs=()):
    """Where Rocket League saves replays: Demos (Steam) and DemosEpic (Epic Games)."""
    base = os.path.join(documents_folder(), "My Games", "Rocket League", "TAGame")
    found = [os.path.join(base, d) for d in ("Demos", "DemosEpic")]
    return list(dict.fromkeys(found + [os.path.expanduser(d) for d in extra_dirs]))
_STAMP = r"(\d{4})[-.](\d{2})[-.](\d{2})\D{1,3}(\d{2})[-.](\d{2})[-.](\d{2})"


def recording_start(path):
    """When the recording started (epoch seconds), from its file name or creation time.

    OBS names files like '2026-10-22 23-45-10.mp4' (local time); NVIDIA like
    'Rocket League 2026.10.22 - 23.45.10.02.mp4'. Returns None if unknown.
    """
    import datetime
    import re
    from pathlib import Path
    m = re.search(_STAMP, Path(path).name)
    if m:
        try:
            return datetime.datetime(*map(int, m.groups())).timestamp()
        except ValueError:
            pass
    st = os.stat(path)
    if hasattr(st, "st_birthtime"):
        return st.st_birthtime
    if os.name == "nt":
        return st.st_ctime     # creation time on Windows
    return None


def header(path):
    """Just the match summary (fast: skips the play-by-play)."""
    exe = find_rrrocket() or (install_rrrocket() if os.name == "nt" else None)
    if exe is None:
        raise RuntimeError("rrrocket isn't installed")
    out = subprocess.run([exe, str(path)], capture_output=True, creationflags=ffmpeg.NO_WINDOW)
    if out.returncode != 0:
        return None
    return json.loads(out.stdout).get("properties", {})


def match_starts(epoch):
    """Possible match start times for a replay's MatchStartEpoch, most likely first.

    It's a normal UTC timestamp: a user's replay with MatchStartEpoch 23:44 UTC was saved at
    7:55 PM local time (UTC-4). The replay's "Date" field is UTC too. The local wall-clock reading
    is kept as a fallback; lining up with the recording picks whichever fits.
    """
    import datetime
    if not epoch:
        return []
    wall = datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).replace(tzinfo=None)
    local = wall.timestamp()     # the same digits read as this PC's local time
    return [epoch] if abs(local - epoch) < 1 else [epoch, local]


def covers(video_path, duration, epoch):
    """True if a recording (by its start time) spans a match that started at `epoch`."""
    start = recording_start(video_path)
    return start is not None and any(start - 120 <= e <= start + duration for e in match_starts(epoch))


def describe_time(epoch):
    import datetime
    when = datetime.datetime.fromtimestamp(match_starts(epoch)[0])
    return when.strftime("%b %d at %I:%M %p").replace(" 0", " ")


def summary(rep, player_names):
    """What the app shows for an imported replay: result, your stats and a timeline."""
    mine = {n.lower() for n in player_names}
    me = next((p for p in rep.players if p.lower() in mine), None)
    my_team = rep.players.get(me) if me else None
    result = ""
    if my_team in (0, 1):
        us, them = rep.score[my_team], rep.score[1 - my_team]
        result = f"{'Won' if us > them else 'Lost' if us < them else 'Tied'} {us}-{them}"
    timeline = []
    for e in rep.events:
        is_me = e.player.lower() in mine
        if e.label == "SAVE" and any(x.label == "EPIC SAVE" and x.player == e.player and abs(x.time - e.time) <= 1.5
                                     for x in rep.events):
            continue   # an epic save also counts as a save
        if e.label == "GOAL" or (is_me and e.label in ("EPIC SAVE", "SAVE", "ASSIST", "DEMO", "POST")):
            timeline.append({"time": round(e.time, 1), "label": e.label.title(), "player": e.player,
                             "mine": is_me, "stat": f"{e.speed_kph:.0f} km/h" if e.speed_kph >= 20 else ""})
    stats = next((s for s in rep.stats if (s.get("Name") or "").lower() in mine), None)
    return {"name": rep.name, "date": describe_time(rep.match_start_epoch) if rep.match_start_epoch else rep.date,
            "epoch": rep.match_start_epoch, "result": result or f"{rep.score[0]}-{rep.score[1]}",
            "me": me, "stats": stats, "timeline": timeline}


def candidates(video_path, duration, dirs, extra=(), forced=()):
    """Replay files that could belong to this recording. `forced` ones were paired by the user."""
    from pathlib import Path
    files = [Path(p) for p in extra]
    for d in replay_folders(dirs):
        folder = Path(os.path.expanduser(d))
        if folder.is_dir():
            files += sorted(folder.glob("*.replay"))
    start = recording_start(video_path)
    end_guess = Path(video_path).stat().st_mtime
    out = []
    forced = {str(Path(p)) for p in forced}
    files += [Path(p) for p in forced]
    for f in dict.fromkeys(files):
        if not f.exists():
            continue
        paired = str(f) in forced
        if not paired and start is None and abs(f.stat().st_mtime - end_guess) > 3 * 86400:
            continue   # unknown start: only look at replays saved within a few days
        props = header(f) or {}
        epoch = float(props.get("MatchStartEpoch") or 0)
        if (not paired and start is not None
                and not any(start - 120 <= e <= start + duration for e in match_starts(epoch))):
            continue
        out.append((f, epoch))
    return out, start


def align(replay_goals, video_goals, prior=None, tolerance=2.5):
    """Offset (video time - replay time) that lines up the most goals, or None.

    replay_goals: goal times in the replay; video_goals: times the recording shows a
    "... SCORED" banner or your goal popup. prior: rough offset from the clock times.
    """
    best, best_hits = None, 0
    diffs = [v - r for r in replay_goals for v in video_goals]
    if prior is not None:
        diffs = [d for d in diffs if abs(d - prior) <= 90] or [prior]
    for d in diffs:
        hits = [v - r - d for r in replay_goals for v in video_goals if abs(v - r - d) <= tolerance]
        if len(hits) > best_hits:
            best, best_hits = d + sorted(hits)[len(hits) // 2], len(hits)
    needed = 1 if prior is not None else min(3, len(replay_goals))
    if best_hits >= needed:
        return best, best_hits
    return (prior, 0) if prior is not None else (None, 0)


def to_events(rep, offset, player_names, duration):
    """Turn a replay's timeline into clip events at recording times."""
    from .detect import Event
    mine = {n.lower() for n in player_names}
    my_team = next((rep.players[p] for p in rep.players if p.lower() in mine), None)
    events = []
    by_time = sorted(rep.events, key=lambda e: e.time)
    for i, e in enumerate(by_time):
        t = e.time + offset
        if not 0 <= t <= duration:
            continue
        me = e.player.lower() in mine
        near = [x for x in by_time if abs(x.time - e.time) <= 1.5 and x is not e]
        if e.label == "SAVE" and any(x.label == "EPIC SAVE" and x.player == e.player for x in near):
            continue   # an epic save also counts as a save
        if e.label == "GOAL":
            aerial = any(x.label == "AERIAL HIT" and x.player == e.player and x.time <= e.time for x in near)
            label = "AERIAL GOAL" if aerial else "GOAL"
            stat = f"{e.speed_kph:.0f} KM/H" if e.speed_kph >= 20 else ""
            if me:
                events.append(Event(t, label, 9 if aerial else 7, "hype", "replay",
                                    f"Your {label.lower()}" + (f", {stat.lower()} shot" if stat else ""), stat))
            elif my_team is not None and e.team != my_team:
                events.append(Event(t, "GOAL AGAINST", 5, "hype", "replay",
                                    f"{e.player} scored against your team", stat))
            elif any(x.label == "ASSIST" and x.player.lower() in mine for x in near):
                # A teammate's goal you set up is worth a clip of its own.
                events.append(Event(t, "ASSIST", 6, "hype", "replay",
                                    f"Your assist to {e.player}" + (f", {stat.lower()} shot" if stat else ""), stat))
            else:
                events.append(Event(t, "TEAM GOAL", 5, "hype", "replay",
                                    f"Your teammate {e.player} scored" + (f", {stat.lower()} shot" if stat else ""), stat))
            continue
        if e.label == "ASSIST":
            continue   # handled with the goal it led to
        if not me:
            continue
        reason = {"EPIC SAVE": "Your epic save", "SAVE": "Your save", "DEMO": "You demolished someone",
                  "POST": "You hit the post", "ASSIST": "Your assist", "AERIAL HIT": "Your aerial touch",
                  "SHOT": "Your shot on goal"}[e.label]
        label = "POST" if e.label == "POST" else e.label
        mood = "funny" if e.label in ("POST", "DEMO") else "hype"
        events.append(Event(t, label, e.weight, mood, "replay", reason))
    return events


# ---------------------------------------------------------------- story moments
# Field geometry (Unreal units): goal lines at y = +-5120, posts at x = +-893, crossbar at z = 642.8,
# ball radius 92.75.
GOAL_Y, POST_X, BAR_Z = 5120.0, 893.0, 642.8


def _near(track, t, window=0.12):
    """The sample of a track nearest to time t (or None)."""
    import bisect
    if not track:
        return None
    i = bisect.bisect_left(track, (t,))
    best = min((track[j] for j in (i - 1, i) if 0 <= j < len(track)), key=lambda p: abs(p[0] - t))
    return best if abs(best[0] - t) <= window else None


def story_events(rep, offset, player_names, duration):
    """Moments that make a play a story, at recording times: [{"time", "label", "detail"}].
    LOST 50 / WON 50 (you and an opponent hit it at once), SECOND CHANCE (you won it back),
    POST / CROSSBAR (the ball hit the frame), NEAR MISS (just wide or over), HANGING (it sat on
    the goal line before going in)."""
    mine = {n.lower() for n in player_names}
    team = rep.players
    my_team = next((team[p] for p in team if p.lower() in mine), None)
    out = []

    def add(t, label, detail):
        t += offset
        if 0 <= t <= duration and all(not (e["label"] == label and abs(e["time"] - t) < 1.5) for e in out):
            out.append({"time": round(t, 2), "label": label, "detail": detail})

    # 50/50s and second chances, from the order of touches.
    touches = sorted(rep.touches)
    for i, (t, who) in enumerate(touches):
        me = who.lower() in mine
        other = [(t2, w2) for t2, w2 in touches[max(0, i - 2):i + 3]
                 if abs(t2 - t) <= 0.45 and w2 != who and team.get(w2) != team.get(who)]
        if me and other:
            after = next((w for t2, w in touches[i + 1:] if t2 > t + 0.45), None)
            if after is not None:
                won = team.get(after) == my_team
                add(t, "WON 50" if won else "LOST 50", f"50/50 with {other[0][1]}")
        if me and i >= 2:
            prev_t, prev_w = touches[i - 1]
            had = [w for t2, w in touches[max(0, i - 6):i - 1] if t - t2 <= 5 and w.lower() in mine]
            # The opponent really had it (not the other half of a 50/50), then you got it back.
            if team.get(prev_w) != my_team and 0.45 < t - prev_t <= 3 and had:
                add(t, "SECOND CHANCE", f"won it back from {prev_w}")

    # Off the frame, just wide, hanging on the line: from the ball's path near each goal.
    ball = rep.ball_track
    car_tracks = list(rep.car_tracks.values())
    for a, b, c in zip(ball, ball[1:], ball[2:]):
        if abs(b[2]) < GOAL_Y - 260 or abs(b[2]) > GOAL_Y + 300:
            continue
        v1 = (b[1] - a[1], b[2] - a[2], b[3] - a[3])
        v2 = (c[1] - b[1], c[2] - b[2], c[3] - b[3])
        n1 = sum(x * x for x in v1) ** 0.5
        n2 = sum(x * x for x in v2) ** 0.5
        if n1 < 15 or n2 < 5:
            continue
        cos = sum(x * y for x, y in zip(v1, v2)) / (n1 * n2)
        if cos > 0.5:
            continue   # no bounce
        if any((p := _near(track, b[0])) and ((p[1] - b[1]) ** 2 + (p[2] - b[2]) ** 2 + (p[3] - b[3]) ** 2) ** 0.5 < 320
               for track in car_tracks):
            continue   # a car hit it, not the frame
        x, z = abs(b[1]), b[3]
        if 790 <= x <= 1000 and z < BAR_Z + 60:
            add(b[0], "POST", "the ball hit the post")
        elif 540 <= z <= 760 and x < POST_X + 40:
            add(b[0], "CROSSBAR", "the ball hit the crossbar")
        elif (1000 < x < 1350 and z < 760) or (760 < z < 1000 and x < 1100):
            add(b[0], "NEAR MISS", "just wide" if z < 760 else "just over")
    for e in rep.events:
        if e.label != "GOAL":
            continue
        slow = [p for p in ball if e.time - 2.5 <= p[0] <= e.time and abs(p[2]) > GOAL_Y - 450
                and abs(p[1]) < POST_X and p[3] < BAR_Z]
        if len(slow) >= 3:
            dy = abs(slow[-1][2] - slow[0][2]) / max(0.05, slow[-1][0] - slow[0][0])
            if slow[-1][0] - slow[0][0] >= 0.6 and dy < 900:
                add(slow[0][0], "HANGING", "the ball sat on the goal line")
    out.sort(key=lambda e: e["time"])
    return out
