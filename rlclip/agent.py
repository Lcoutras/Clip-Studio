"""Tools for the Clip Agent: Claude Code running in the agent/ folder, managing recordings for you.

Each command prints plain text the agent reads. The agent decides what's worth posting and
writes the posts; these commands do the mechanical work (analysis, frames, exports, the queue).
Nothing here posts anywhere: finished clips go to output/ready/ for you to upload.

    python -m rlclip agent status                     what's new, what's waiting
    python -m rlclip agent scan <recording> [--fast]  analyze one recording, list its clips
    python -m rlclip agent sheet <recording> <clip>   contact sheet of frames to look at
    python -m rlclip agent review <recording> <clip> --keep | --reject REASON | --flag  [--note ...]
    python -m rlclip agent package <recording> <clip> --post post.json
    python -m rlclip agent queue                      the Ready to post queue
    python -m rlclip agent posted <item> --platform tiktok [--url ...]
    python -m rlclip agent feedback                   what you've rejected and why
"""

import csv
import datetime
import json
import os
import re
import shutil
import time
from pathlib import Path

from . import config, feedback, ffmpeg, pipeline, replay

PLATFORMS = ["tiktok", "youtube", "instagram"]
POST_FIELDS = {
    "caption": "on-screen caption burned into the video (3-7 words)",
    "tiktok": "TikTok caption with 3-5 hashtags",
    "youtube_title": "YouTube Shorts title, under 100 characters",
    "youtube_description": "YouTube description; include #Shorts",
    "instagram": "Instagram Reels caption with hashtags",
    "why": "one line: why this clip is worth posting",
}


def out_root(cfg):
    return config.resolve(cfg["output_dir"])


def agent_dir(cfg):
    d = out_root(cfg) / "agent"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ready_dir(cfg):
    return out_root(cfg) / "ready"


# ------------------------------------------------------------ the app's library and replays
def _app_settings(cfg):
    path = out_root(cfg) / "app_settings.json"
    try:
        return path, json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return path, {}


def add_to_library(cfg, video):
    """So clips the agent finds also show up in Clip Studio."""
    path, settings = _app_settings(cfg)
    lib = settings.setdefault("library", [])
    if str(video) not in lib:
        lib.append(str(video))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, indent=1), encoding="utf-8")


def with_replays(cfg):
    _, settings = _app_settings(cfg)
    return {**cfg, "replays": {**cfg.get("replays", {}), "files": settings.get("replays", []),
                               "pairs": settings.get("pairs", {})}}


def find_video(cfg, name):
    """A recording by full path, or by file name from the library or the watch folders."""
    p = Path(name)
    if p.is_file():
        return p.resolve()
    _, settings = _app_settings(cfg)
    for candidate in [Path(x) for x in settings.get("library", [])] + recordings(cfg, check_length=False):
        if candidate.name == p.name or candidate.stem == p.name:
            return candidate
    raise SystemExit(f"Recording not found: {name}")


# ------------------------------------------------------------ status
def recordings(cfg, check_length=True):
    """Video files in the watch folders, newest first."""
    acfg = cfg.get("agent", {})
    skip = out_root(cfg).resolve()
    cutoff = time.time() - acfg.get("max_age_days", 14) * 86400
    found = []
    for d in acfg.get("watch_dirs", []):
        root = Path(os.path.expanduser(d))
        if not root.is_dir():
            continue
        for f in root.rglob("*"):
            if f.suffix.lower() not in pipeline.VIDEO_EXT or not f.is_file() or skip in f.resolve().parents:
                continue
            if f.stat().st_mtime < cutoff:
                continue
            found.append(f)
    found.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    if check_length:
        keep = []
        for f in found:
            try:
                if ffmpeg.probe(f).duration >= acfg.get("min_minutes", 2) * 60:
                    keep.append(f)
            except ffmpeg.FFmpegError:
                pass
        found = keep
    return found


def _label(path):
    start = replay.recording_start(path)
    if start is None:
        return Path(path).stem
    return datetime.datetime.fromtimestamp(start).strftime("%a %b %d, %I:%M %p").replace(" 0", " ")


def cmd_status(cfg, args):
    recs = recordings(cfg)
    print(f"Watching: {', '.join(cfg.get('agent', {}).get('watch_dirs', []))} "
          f"(last {cfg.get('agent', {}).get('max_age_days', 14)} days)")
    new, done = [], []
    for f in recs:
        proj = pipeline.Project(f, cfg)
        (done if proj.load_highlights() is not None and proj.csv_path.exists() else new).append((f, proj))
    print(f"\nNot analyzed yet ({len(new)}):")
    for f, _ in new:
        mins = ffmpeg.probe(f).duration / 60
        print(f"  {f}  [{_label(f)}, {mins:.0f} min]")
    print(f"\nAnalyzed ({len(done)}):")
    for f, proj in done:
        rows = proj.read_rows()
        reviewed = sum(1 for r in rows if (r.get("note") or "").startswith("Agent:") or r.get("reject_reason"))
        packaged = sum(1 for r in rows if _ready_item(cfg, f, r["clip"]))
        print(f"  {f.name}  [{_label(f)}]: {len(rows)} clips, {reviewed} reviewed, {packaged} packaged")
    q = queue(cfg)
    none = sum(1 for i in q if not i["posted"])
    everywhere = sum(1 for i in q if set(PLATFORMS) <= set(i["posted"]))
    print(f"\nReady to post: {none} not posted yet, {len(q) - none - everywhere} posted on some platforms, "
          f"{everywhere} posted everywhere")
    fb = feedback.Feedback(pipeline.feedback_path(cfg)).summary()
    print(f"Your feedback: {fb['rejected']} rejected, {fb['kept']} exported"
          + (f"; rules: {' | '.join(fb['rules'])}" if fb["rules"] else ""))
    print(f"Notes folder: {agent_dir(cfg)}   Ready folder: {ready_dir(cfg)}")
    knowledge = agent_dir(cfg) / "knowledge.md"
    print(f"Knowledge file: {knowledge} ({'exists' if knowledge.exists() else 'not created yet'})")
    return 0


# ------------------------------------------------------------ scan
def _clip_lines(proj):
    lines = []
    for r in proj.read_rows():
        state = "REJECTED" if pipeline.is_skipped(r) else "kept"
        if r.get("reject_reason"):
            state += f" ({r['reject_reason']})"
        if r.get("flag"):
            state += ", flagged"
        s, e, a = float(r["start"]), float(r["end"]), float(r["anchor"])
        lines.append(f"  clip {r['clip']:>2}  {r.get('category') or '?':<13} {r['label']:<14} score {r.get('score') or '-':<5} "
                     f"play at {a:7.1f}s  window {s:.1f}-{e:.1f}s ({e - s:.0f}s)  {state}"
                     + (f"  stat {r['stat']}" if r.get("stat") else "")
                     + f"\n           why: {r.get('reason') or '-'}"
                     + (f"\n           note: {r['note']}" if r.get("note") else ""))
    return lines


def cmd_scan(cfg, args):
    video = find_video(cfg, args.recording)
    proj = pipeline.Project(video, with_replays(cfg))
    if args.fast:
        proj.cfg = {**proj.cfg, "detection": {**proj.cfg["detection"], "ocr_fps": 0.5}}
    print(f"=== {video.name}  [{_label(video)}]")
    found = proj.detect(rescan=args.rescan)
    if not proj.csv_path.exists() or args.rescan:
        proj.write_captions(found, recaption=True)
    add_to_library(cfg, video)
    for n in proj.notes() + proj.warnings():
        print(f"  NOTE: {n}")
    rows = proj.read_rows()
    print(f"\n{len(rows)} suggested clips (from Clip Studio's detector):")
    print("\n".join(_clip_lines(proj)) or "  (none)")
    return 0


# ------------------------------------------------------------ contact sheet
def cmd_sheet(cfg, args):
    """8 frames across the clip in one picture; the frame at the play is outlined in yellow."""
    from PIL import Image, ImageDraw

    video = find_video(cfg, args.recording)
    proj = pipeline.Project(video, cfg)
    row = _row(proj, args.clip)
    s, e, a = float(row["start"]), float(row["end"]), float(row["anchor"])
    times = sorted(set([round(s + (e - s) * i / 7, 2) for i in range(8)] + [a]))   # 8 across + the play
    tw, th = 480, 270
    cols = 3
    rows_n = (len(times) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw, rows_n * (th + 22)), (20, 20, 24))
    draw = ImageDraw.Draw(sheet)
    work = agent_dir(cfg) / "sheets"
    work.mkdir(parents=True, exist_ok=True)
    for i, t in enumerate(times):
        frame = work / "_frame.jpg"
        ffmpeg.run(["-ss", f"{t:.2f}", "-i", video, "-frames:v", "1", "-vf", f"scale={tw}:{th}", "-q:v", "4", frame])
        x, y = (i % cols) * tw, (i // cols) * (th + 22)
        try:
            sheet.paste(Image.open(frame).convert("RGB"), (x, y + 22))
        except OSError:
            continue
        mark = abs(t - a) < 0.05
        draw.text((x + 6, y + 4), f"{t - a:+.1f}s" + ("  <- the play" if mark else ""),
                  fill=(255, 220, 0) if mark else (200, 200, 200))
        if mark:
            draw.rectangle([x, y + 22, x + tw - 1, y + 22 + th - 1], outline=(255, 220, 0), width=4)
    out = work / f"{video.stem}_clip{int(row['clip']):02d}.jpg"
    sheet.save(out, quality=85)
    (work / "_frame.jpg").unlink(missing_ok=True)
    print(out)
    return 0


# ------------------------------------------------------------ review
def _row(proj, clip):
    if not proj.csv_path.exists():
        raise SystemExit(f"{proj.video.name} hasn't been analyzed: run 'agent scan' first")
    row = next((r for r in proj.read_rows() if int(r["clip"]) == int(clip)), None)
    if row is None:
        raise SystemExit(f"No clip {clip} in {proj.video.name}")
    return row


def _update_row(proj, clip, **fields):
    rows = proj.read_rows()
    for r in rows:
        if int(r["clip"]) == int(clip):
            r.update(fields)
    proj.write_rows(rows)


def cmd_review(cfg, args):
    """The agent's verdict. It's written on the clip (Clip Studio shows it) but isn't treated as
    your feedback: only your own rejections teach the detector."""
    proj = pipeline.Project(find_video(cfg, args.recording), cfg)
    row = _row(proj, args.clip)
    note = f"Agent: {args.note}" if args.note else row.get("note") or ""
    fields = {"note": note}
    if args.keep:
        fields.update(skip="", reject_reason="")
    elif args.reject:
        if args.reject not in feedback.REASONS:
            raise SystemExit(f"--reject must be one of: {', '.join(feedback.REASONS)}")
        fields.update(skip="yes", reject_reason=args.reject)
    if args.flag:
        fields["flag"] = "yes"
    _update_row(proj, args.clip, **fields)
    print(f"clip {args.clip}: " + ", ".join(f"{k}={v}" for k, v in fields.items() if v))
    return 0


# ------------------------------------------------------------ package for posting
def _slug(text):
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")[:40] or "clip"


def _ready_item(cfg, video, clip):
    for meta in ready_dir(cfg).glob("*/meta.json"):
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if m.get("recording") == Path(video).name and int(m.get("clip", -1)) == int(clip):
            return meta.parent
    return None


POST_TEMPLATE = """# {title}

From **{recording}** ({when}), clip {clip} · {category} · {length:.0f}s
Why post it: {why}

Video: `{video_file}` (vertical 1080x1920). Captions and hashtags below are ready to copy.

## TikTok
```
{tiktok}
```
- [ ] Upload in the TikTok app or tiktok.com/upload
- [ ] Optional: swap in a trending sound (exported {music_note})

## YouTube Shorts
Title:
```
{youtube_title}
```
Description:
```
{youtube_description}
```
- [ ] Upload at studio.youtube.com (or the YouTube app > + > Short)

## Instagram Reels
```
{instagram}
```
- [ ] Upload in the Instagram app (Reel)

When it's up, tell the agent ("posted the {category_l} clip on TikTok") or run:
`agent posted {item} --platform tiktok --url <link>`
"""


def cmd_package(cfg, args):
    video = find_video(cfg, args.recording)
    proj = pipeline.Project(video, cfg)
    row = _row(proj, args.clip)
    post = json.loads(Path(args.post).read_text(encoding="utf-8"))
    missing = [k for k in POST_FIELDS if not str(post.get(k) or "").strip()]
    if missing:
        raise SystemExit("post.json is missing: " + ", ".join(f"{k} ({POST_FIELDS[k]})" for k in missing))
    if len(post["youtube_title"]) > 100:
        raise SystemExit("youtube_title must be under 100 characters")
    fields = {"caption": post["caption"].strip(), "skip": "", "reject_reason": ""}
    if post.get("music"):
        fields["music"] = post["music"]
    _update_row(proj, args.clip, **fields)
    row = _row(proj, args.clip)
    layout = "vertical"
    cfg_v = {**cfg, "render": {**cfg["render"], "format": layout}}
    proj.cfg = cfg_v
    print(f"Exporting clip {args.clip} of {video.name}...")
    made = proj.render_all([row], [layout])
    if not made:
        raise SystemExit("Export failed")
    folder = _ready_item(cfg, video, args.clip)
    if folder is None:
        stamp = datetime.date.today().isoformat()
        folder = ready_dir(cfg) / f"{stamp}_{_slug(row.get('category') or row['label'])}_{_slug(post['caption'])}"
        i = 2
        while folder.exists():
            folder = folder.with_name(f"{folder.name}-{i}")
            i += 1
    folder.mkdir(parents=True, exist_ok=True)
    clip_file = folder / f"{_slug(post['caption'])}.mp4"
    for old in folder.glob("*.mp4"):
        old.unlink()
    shutil.copy2(made[0], clip_file)
    meta_path = folder / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {"posted": {}}
    meta.update(recording=video.name, clip=int(row["clip"]), category=row.get("category"),
                anchor=float(row["anchor"]), packaged=time.strftime("%Y-%m-%d %H:%M"), post=post,
                file=clip_file.name)
    meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    music = (row.get("music") or "").strip().lower()
    (folder / "POST.md").write_text(POST_TEMPLATE.format(
        title=post["youtube_title"], recording=video.name, when=_label(video), clip=row["clip"],
        category=row.get("category"), category_l=(row.get("category") or "").lower(),
        length=float(row["end"]) - float(row["start"]), why=post["why"], video_file=clip_file.name,
        tiktok=post["tiktok"], youtube_title=post["youtube_title"],
        youtube_description=post["youtube_description"], instagram=post["instagram"], item=folder.name,
        music_note="with no music" if music == "none" else "with music from your library"),
        encoding="utf-8")
    print(f"Ready to post: {folder}")
    return 0


# ------------------------------------------------------------ the queue
def queue(cfg):
    items = []
    for meta in sorted(ready_dir(cfg).glob("*/meta.json")):
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        items.append({"item": meta.parent.name, "posted": m.get("posted", {}), **m})
    return items


def cmd_queue(cfg, args):
    items = queue(cfg)
    if not items:
        print(f"Nothing in {ready_dir(cfg)} yet.")
    for i in items:
        where = ", ".join(f"{p}" + (f" ({v.get('url')})" if v.get("url") else "") for p, v in i["posted"].items())
        print(f"  {i['item']}: {i.get('category')} from {i.get('recording')} clip {i.get('clip')}"
              f"  -> {'posted on ' + where if where else 'waiting'}")
    return 0


def cmd_posted(cfg, args):
    meta_path = ready_dir(cfg) / args.item / "meta.json"
    if not meta_path.exists():
        raise SystemExit(f"No ready item called {args.item} (see 'agent queue')")
    if args.platform not in PLATFORMS:
        raise SystemExit(f"--platform must be one of: {', '.join(PLATFORMS)}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.setdefault("posted", {})[args.platform] = {"url": args.url or "", "at": time.strftime("%Y-%m-%d %H:%M")}
    meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    log = agent_dir(cfg) / "posted.csv"
    new = not log.exists()
    with open(log, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "platform", "item", "category", "caption", "url"])
        w.writerow([time.strftime("%Y-%m-%d"), args.platform, args.item, meta.get("category"),
                    meta.get("post", {}).get("caption", ""), args.url or ""])
    print(f"Marked {args.item} as posted on {args.platform}")
    return 0


def cmd_feedback(cfg, args):
    print(feedback.Feedback(pipeline.feedback_path(cfg)).report())
    return 0


def add_parser(sub):
    p = sub.add_parser("agent", help="tools for the Clip Agent (see agent/README.md)")
    a = p.add_subparsers(dest="agent_command", required=True)
    a.add_parser("status", help="new recordings, analyzed ones, the queue")
    s = a.add_parser("scan", help="analyze a recording and list its suggested clips")
    s.add_argument("recording")
    s.add_argument("--fast", action="store_true")
    s.add_argument("--rescan", action="store_true")
    s = a.add_parser("sheet", help="contact sheet of frames from a clip")
    s.add_argument("recording")
    s.add_argument("clip", type=int)
    s = a.add_parser("review", help="keep, reject or flag a clip")
    s.add_argument("recording")
    s.add_argument("clip", type=int)
    g = s.add_mutually_exclusive_group()
    g.add_argument("--keep", action="store_true")
    g.add_argument("--reject", metavar="REASON")
    s.add_argument("--flag", action="store_true", help="flag it for Luke to look at")
    s.add_argument("--note")
    s = a.add_parser("package", help="export a clip into the Ready to post queue")
    s.add_argument("recording")
    s.add_argument("clip", type=int)
    s.add_argument("--post", required=True, help="JSON file with: " + ", ".join(POST_FIELDS))
    a.add_parser("queue", help="list the Ready to post queue")
    s = a.add_parser("posted", help="record that a ready clip was posted")
    s.add_argument("item")
    s.add_argument("--platform", required=True)
    s.add_argument("--url")
    a.add_parser("feedback", help="your rejections and what Clip Studio learned")


COMMANDS = {"status": cmd_status, "scan": cmd_scan, "sheet": cmd_sheet, "review": cmd_review,
            "package": cmd_package, "queue": cmd_queue, "posted": cmd_posted, "feedback": cmd_feedback}


def run(cfg, args):
    return COMMANDS[args.agent_command](cfg, args)
