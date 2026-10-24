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
    "TAGame.PRI_TA:MatchSaves": ("SAVE", 4),
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
            elif name in STATS and "Int" in attr:
                value, before = attr["Int"], counters.get((aid, name), 0)
                counters[(aid, name)] = value
                if value > before:
                    label, weight = STATS[name]
                    events.append(ReplayEvent(t, label, aid, -1, weight))
            elif name == "TAGame.RBActor_TA:ReplicatedRBState" and aid in balls:
                loc = (attr.get("RigidBody") or {}).get("location")
                if loc:
                    ball_track.append((t, loc["x"], loc["y"], loc["z"]))

    teams = {p.get("Name"): p.get("Team", -1) for p in props.get("PlayerStats", [])}
    for e in events:
        e.player = names.get(e.player, "?")
        e.team = teams.get(e.player, -1)
        if e.label == "GOAL":
            e.speed_kph, e.height_uu = _ball_at(ball_track, e.time)
    events.sort(key=lambda e: e.time)

    return Replay(
        path=str(path), name=props.get("ReplayName", ""),
        match_start_epoch=float(props.get("MatchStartEpoch") or 0), date=props.get("Date", ""),
        players=teams, score=(props.get("Team0Score", 0), props.get("Team1Score", 0)),
        duration=frames[-1]["time"] - t0, events=events,
        stats=[{k: p.get(k) for k in ("Name", "Team", "Goals", "Assists", "Saves", "Shots", "Score")}
               for p in props.get("PlayerStats", [])])


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
