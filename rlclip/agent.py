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

from . import captions, config, feedback, ffmpeg, highlights, music, pipeline, replay

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
    tw, th = 360, 203   # small frames: plenty to judge a play, cheaper for the agent to look at
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


def _luke_rejected(row):
    return pipeline.is_skipped(row) and row.get("agent") != "rejected" and bool(row.get("reject_reason"))


def cmd_review(cfg, args):
    """The agent's verdict and edits. Clip Studio shows them, and Luke has the final say: only his
    own rejections teach the detector, and the agent can't un-reject a clip he rejected."""
    proj = pipeline.Project(find_video(cfg, args.recording), cfg)
    row = _row(proj, args.clip)
    fields = {}
    verdict = None
    if args.reject:
        if args.reject not in feedback.REASONS:
            raise SystemExit(f"--reject must be one of: {', '.join(feedback.REASONS)}")
        fields.update(skip="yes", reject_reason=args.reject)
        verdict = "rejected"
    elif args.approve:
        if _luke_rejected(row):
            print("Luke rejected this clip in Clip Studio, so it stays rejected.")
        else:
            fields.update(skip="", reject_reason="")
            verdict = "approved"
    if args.flag:
        fields["flag"] = "yes"
        verdict = verdict or "flagged"
    if verdict:
        fields["agent"] = verdict
    if args.note:
        fields["agent_note"] = args.note.strip()
    if args.caption is not None:
        text = " ".join(args.caption.split())
        if not text or len(text.split()) > 9 or "#" in text:
            raise SystemExit("--caption must be 1-9 words with no hashtags")
        if text != row.get("caption"):
            fields.update(alt_caption=row.get("caption") or "", caption=text)
    if args.tone:
        if args.tone not in ("funny", "motivational"):
            raise SystemExit("--tone must be funny or motivational")
        fields["tone"] = args.tone
    if args.music is not None:
        choice = args.music.strip()
        if choice.lower() in ("", "auto"):
            fields["music"] = ""
        elif choice.lower() == "none":
            fields["music"] = "none"
        else:
            track = music.MusicLibrary(cfg).find(choice)
            if track is None:
                raise SystemExit(f"No track called {choice!r} in the music folder (see 'music-list')")
            fields["music"] = track.name
    if args.category:
        if args.category not in highlights.CATEGORIES:
            raise SystemExit(f"--category must be one of: {', '.join(highlights.CATEGORIES)}")
        fields["category"] = args.category
    if args.start is not None or args.end is not None:
        start = float(args.start if args.start is not None else row["start"])
        end = float(args.end if args.end is not None else row["end"])
        anchor = float(row["anchor"])
        if not start < anchor < end or end - start < 3 or end - start > 60:
            raise SystemExit("The clip must contain the play (its anchor) and be 3-60 seconds long")
        fields.update(start=f"{max(0.0, start):.2f}", end=f"{end:.2f}")
    if not fields:
        raise SystemExit("Nothing to change: pass --approve, --reject, --flag, --caption, --music...")
    _update_row(proj, args.clip, **fields)
    log_decision(cfg, proj, row, fields)
    print(f"clip {args.clip}: " + ", ".join(f"{k}={v!r}" for k, v in fields.items() if k != "alt_caption"))
    rendered = proj.out_dir / "vertical" / f"{proj.output_name(row)}.mp4"
    if rendered.exists() and set(fields) & {"caption", "music", "start", "end"}:
        print("  (it was already exported: re-export it in Clip Studio to apply the changes)")
    return 0


DECIDED = ["agent", "skip", "reject_reason", "flag", "caption", "tone", "music", "category", "start", "end"]


def log_decision(cfg, proj, before, fields):
    """Every verdict and edit, with what it changed: the raw material for teaching the app
    (see 'distill' and 'handoff')."""
    h = proj.highlight_for(before) or {}
    rec = {"at": time.strftime("%Y-%m-%d %H:%M"), "recording": proj.video.name, "clip": int(before["clip"]),
           "label": before.get("label"), "category": before.get("category"), "score": before.get("score"),
           "signal": feedback.signal_of(h.get("events")), "reason": before.get("reason"),
           "anchor": float(before["anchor"]), "note": fields.get("agent_note", ""),
           "before": {k: before.get(k, "") for k in DECIDED if k in fields},
           "after": {k: v for k, v in fields.items() if k in DECIDED}}
    with open(agent_dir(cfg) / "decisions.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def cmd_todo(cfg, args):
    """Clips from recordings Luke analyzed in Clip Studio that the agent hasn't reviewed yet."""
    _, settings = _app_settings(cfg)
    lib = music.MusicLibrary(cfg)
    total = 0
    for video in [Path(p) for p in settings.get("library", []) if Path(p).is_file()]:
        proj = pipeline.Project(video, cfg)
        if proj.load_highlights() is None or not proj.csv_path.exists():
            continue
        rows = proj.read_rows()
        luke = [r for r in rows if _luke_rejected(r)]
        hidden = [r for r in rows if pipeline.is_skipped(r) and not r.get("reject_reason") and not r.get("agent")]
        todo = [r for r in rows if not r.get("agent") and r not in luke and r not in hidden]
        if not todo:
            continue
        todo.sort(key=lambda r: -float(r.get("score") or 0))
        shown = todo[:args.top] if args.top else todo
        total += len(shown)
        print(f"=== {video.name}  [{_label(video)}]  {len(todo)} to review"
              + (f", showing the top {len(shown)} by score" if len(shown) < len(todo) else "")
              + (f"; {len(luke)} rejected by Luke" if luke else "")
              + (f"; {len(hidden)} hidden by default (e.g. Fail?)" if hidden else ""))
        for r in shown:
            s, e = float(r["start"]), float(r["end"])
            track = r.get("music") or "auto (picked by mood at export)"
            credit = lib.credit(r.get("music")) if r.get("music") else ""
            print(f"  clip {r['clip']:>2}  {r.get('category'):<13} {r['label']:<14} score {r.get('score') or '-':<5} "
                  f"{e - s:.0f}s  play at {float(r['anchor']):.1f}s"
                  + (f"  stat {r['stat']}" if r.get("stat") else "")
                  + f"\n           why:     {r.get('reason') or '-'}"
                  + f"\n           caption: {r.get('caption')!r} ({r.get('tone') or '?'})"
                  + f"\n           music:   {track}" + (f"  [needs credit: {credit}]" if credit else "")
                  + (f"\n           Luke's note: {r['note']}" if r.get("note") else ""))
    if not total:
        print("Nothing to review: every analyzed clip has a verdict. "
              "New clips appear here after Luke analyzes recordings in Clip Studio.")
    return 0


# ------------------------------------------------------------ captions and music
def cmd_caption_add(cfg, args):
    labels = set(captions.load_library(cfg["captions"])) | {"ANY"}
    if args.label not in labels:
        raise SystemExit(f"label must be one of: {', '.join(sorted(labels))}")
    if args.tone not in ("funny", "motivational"):
        raise SystemExit("tone must be funny or motivational")
    line = " ".join(args.line.split())
    if not line or len(line.split()) > 9 or "#" in line:
        raise SystemExit("A caption is 1-9 words with no hashtags")
    added = captions.add_custom(args.label, args.tone, line)
    print(f"{'Added' if added else 'Already there'}: [{args.label} / {args.tone}] {line}  "
          f"(in {config.resolve(captions.CUSTOM)})")
    return 0


def _audio_info(path):
    import subprocess
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format",
                          str(path)], capture_output=True, text=True, creationflags=ffmpeg.NO_WINDOW)
    if out.returncode != 0:
        return None, 0
    data = json.loads(out.stdout)
    has_audio = any(s.get("codec_type") == "audio" for s in data.get("streams", []))
    return has_audio, float(data.get("format", {}).get("duration") or 0)


def cmd_music_list(cfg, args):
    lib = music.MusicLibrary(cfg)
    tracks = lib.tracks()
    if not tracks:
        print(f"No music yet in {lib.root}. Add some with 'music-add'.")
        return 0
    catalog = lib.catalog()
    print(f"{len(tracks)} tracks in {lib.root}:")
    for t in tracks:
        rel = t.relative_to(lib.root).as_posix()
        meta = catalog.get(rel, {})
        _, dur = _audio_info(t)
        mood = rel.split("/")[0] if "/" in rel else "-"
        line = f"  {t.name}  [{mood}, {dur:.0f}s, drop at {lib.drop_time(t):.0f}s]"
        if meta:
            line += (f"  {meta.get('title')} by {meta.get('artist')}, {meta.get('license')}"
                     + (f", energy {meta['energy']}" if meta.get("energy") else "")
                     + (" - NEEDS CREDIT" if meta.get("license") in music.NEEDS_CREDIT else "")
                     + (f" - {meta['notes']}" if meta.get("notes") else ""))
        else:
            line += "  (added by hand; no catalog details)"
        print(line)
    return 0


# Downloading from these breaks their terms or isn't licensed for reuse.
BLOCKED_HOSTS = ("youtube.com", "youtu.be", "tiktok.com", "instagram.com", "spotify.com", "music.apple.com",
                 "soundcloud.com", "deezer.com", "tidal.com", "amazon.")
MAX_DOWNLOAD = 80 * 1024 * 1024


def _download(url, dest):
    import urllib.request
    from urllib.parse import urlparse
    host = urlparse(url).netloc.lower()
    if any(b in host for b in BLOCKED_HOSTS):
        raise SystemExit(f"Not downloading from {host}: its terms don't allow it and the music isn't licensed "
                         "for reuse. Use a royalty-free library with a direct download link.")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (ClipStudio music-add)"})
    with urllib.request.urlopen(req, timeout=60) as res:
        kind = res.headers.get("Content-Type", "")
        if "text/html" in kind:
            raise SystemExit("That link is a web page, not a file. Find the direct download link (often the "
                             "Download button's address).")
        size = 0
        with open(dest, "wb") as f:
            while chunk := res.read(1 << 16):
                size += len(chunk)
                if size > MAX_DOWNLOAD:
                    raise SystemExit("The download is over 80 MB; that's not a single song.")
                f.write(chunk)


def cmd_music_add(cfg, args):
    if args.license not in music.LICENSES:
        raise SystemExit("--license must be one of: " + ", ".join(f"{k} ({v})" for k, v in music.LICENSES.items()))
    if args.license in music.NEEDS_CREDIT and not args.attribution:
        raise SystemExit("This license needs credit: pass --attribution \"Music: <title> by <artist> (<link>)\"")
    if args.license == "other" and not args.notes:
        raise SystemExit("For --license other, explain the terms in --notes")
    if args.mood not in ("hype", "funny", "chill"):
        raise SystemExit("--mood must be hype, funny or chill")
    lib = music.MusicLibrary(cfg)
    work = agent_dir(cfg) / "downloads"
    work.mkdir(parents=True, exist_ok=True)
    src = Path(args.source)
    if not src.is_file():
        if not re.match(r"https?://", args.source):
            raise SystemExit("source must be a file on this PC or an http(s) link")
        src = work / "download.bin"
        print("Downloading...")
        _download(args.source, src)
    has_audio, dur = _audio_info(src)
    if not has_audio:
        raise SystemExit("That file has no audio in it.")
    if not 20 <= dur <= 600:
        raise SystemExit(f"That's {dur:.0f}s long; tracks should be 20 seconds to 10 minutes.")
    root = lib.root.resolve()
    if root in src.resolve().parents and src.suffix.lower() in music.AUDIO_EXT:
        # Already in the music folder (added by hand): catalog it where it is, no copy.
        out = src.resolve()
        rel = out.relative_to(root).as_posix()
        if "/" in rel and rel.split("/")[0] != args.mood:
            print(f"Note: it's in the {rel.split('/')[0]} folder, so it's used for that mood. "
                  f"Move the file to {args.mood}/ to change that.")
    else:
        out = lib.root / args.mood / f"{_slug(args.artist)}_{_slug(args.title)}.mp3"
        out.parent.mkdir(parents=True, exist_ok=True)
        # mp3 (also pulls the audio out of an mp4).
        ffmpeg.run(["-i", src, "-vn", "-map", "0:a:0", "-c:a", "libmp3lame", "-b:a", "192k", out])
        if src.parent == work:
            src.unlink(missing_ok=True)
    rel = out.relative_to(root if out.is_absolute() and root in out.parents else lib.root).as_posix()
    lib.add_to_catalog({"file": rel, "mood": rel.split("/")[0] if "/" in rel else args.mood, "title": args.title,
                        "artist": args.artist, "license": args.license, "attribution": args.attribution or "",
                        "source_url": args.source_url or (args.source if "://" in args.source else ""),
                        "energy": args.energy or "", "notes": args.notes or "",
                        "added": time.strftime("%Y-%m-%d")})
    track = lib.root / rel
    print(f"Cataloged {rel} ({dur:.0f}s, drop at {lib.drop_time(track):.0f}s)")
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
    lib = music.MusicLibrary(cfg)
    if not fields.get("music") and not row.get("music"):
        # Pick the track now (as an export would) so its credit can go in the post.
        mood = (cfg["music"].get("mood_by_category") or {}).get(row.get("category")) or (
            "funny" if row.get("tone") == "funny" else None) or next(
            (h["mood"] for h in proj.load_highlights() or [] if h["index"] == int(row["clip"])), "hype")
        track = lib.choose(mood)
        if track:
            fields["music"] = track.name
    _update_row(proj, args.clip, **fields)
    row = _row(proj, args.clip)
    credit = lib.credit(row.get("music"))
    if credit:
        for key in ("tiktok", "youtube_description", "instagram"):
            if credit not in post[key]:
                post[key] = post[key].rstrip() + "\n" + credit
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
    track = (row.get("music") or "").strip()
    (folder / "POST.md").write_text(POST_TEMPLATE.format(
        title=post["youtube_title"], recording=video.name, when=_label(video), clip=row["clip"],
        category=row.get("category"), category_l=(row.get("category") or "").lower(),
        length=float(row["end"]) - float(row["start"]), why=post["why"], video_file=clip_file.name,
        tiktok=post["tiktok"], youtube_title=post["youtube_title"],
        youtube_description=post["youtube_description"], instagram=post["instagram"], item=folder.name,
        music_note="with no music" if track.lower() == "none" else f"with {track or 'music from your library'}"),
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


# ------------------------------------------------------------ teaching the app
def decisions(cfg):
    """{(recording, clip): merged record} - the agent's latest verdict and edits per clip."""
    path = agent_dir(cfg) / "decisions.jsonl"
    merged = {}
    if not path.exists():
        return merged
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        key = (rec["recording"], rec["clip"])
        old = merged.get(key)
        if old:
            rec["before"] = {**rec["before"], **old["before"]}   # the original value wins
            rec["after"] = {**old["after"], **rec["after"]}
            rec["note"] = rec.get("note") or old.get("note")
        merged[key] = rec
    return merged


def _overrides(cfg, decided):
    """Where Luke later disagreed with the agent in Clip Studio: {(recording, clip): 'kept'/'rejected'}."""
    out = {}
    _, settings = _app_settings(cfg)
    for video in [Path(p) for p in settings.get("library", []) if Path(p).is_file()]:
        proj = pipeline.Project(video, cfg)
        if not proj.csv_path.exists():
            continue
        for r in proj.read_rows():
            rec = decided.get((video.name, int(r["clip"])))
            if not rec:
                continue
            verdict = rec["after"].get("agent")
            if verdict == "rejected" and not pipeline.is_skipped(r):
                out[(video.name, int(r["clip"]))] = "kept"
            elif verdict == "approved" and pipeline.is_skipped(r):
                out[(video.name, int(r["clip"]))] = "rejected"
    return out


MIN_VERDICTS = 5     # agent verdicts on one kind of clip before proposing a score change
MIN_EDITS = 3        # timing/music/tone edits in a category before proposing a default


def distill(cfg):
    """Turn the agent's decisions into proposed settings, plus the evidence for each."""
    import statistics
    decided = decisions(cfg)
    overrides = _overrides(cfg, decided)
    proposals = {"detection": {"signal_adjust": {}, "timing_adjust": {}},
                 "captions": {"tone_by_category": {}}, "music": {"mood_by_category": {}}}
    evidence = []
    # Which kinds of clips are worth suggesting: agent verdicts, corrected by Luke's overrides.
    by_signal = {}
    for key, rec in decided.items():
        verdict = rec["after"].get("agent")
        if verdict not in ("approved", "rejected") or not rec.get("signal"):
            continue
        if key in overrides:
            verdict = "approved" if overrides[key] == "kept" else "rejected"
        c = by_signal.setdefault(rec["signal"], {"approved": 0, "rejected": 0})
        c[verdict] += 1
    for signal, c in sorted(by_signal.items()):
        n = c["approved"] + c["rejected"]
        if n < MIN_VERDICTS:
            continue
        rate = c["rejected"] / n
        adjust = -2.0 if rate >= 0.8 else -1.0 if rate >= 0.6 else 1.0 if rate <= 0.15 else 0.0
        current = float((cfg["detection"].get("signal_adjust") or {}).get(signal, 0))
        if adjust and adjust != current:
            proposals["detection"]["signal_adjust"][signal] = adjust
            evidence.append(f"{feedback.describe(signal)}: {c['rejected']} of {n} rejected -> score {adjust:+g}")
    # Timing, tone and music: what the agent changed, per category.
    timing, tones, moods = {}, {}, {}
    lib = music.MusicLibrary(cfg)
    catalog = lib.catalog()
    mood_of = {rel.split("/")[-1].lower(): row.get("mood") for rel, row in catalog.items()}
    for t in lib.tracks():
        rel = t.relative_to(lib.root).as_posix()
        mood_of.setdefault(t.name.lower(), rel.split("/")[0] if "/" in rel else None)
    for rec in decided.values():
        if rec["after"].get("agent") == "rejected":
            continue
        cat = rec["after"].get("category") or rec.get("category")
        b, a = rec["before"], rec["after"]
        if "start" in a and b.get("start") not in (None, ""):
            timing.setdefault(cat, []).append((float(a["start"]) - float(b["start"]), float(a["end"]) - float(b["end"])))
        if a.get("tone"):
            tones.setdefault(cat, []).append(a["tone"])
        if a.get("music") and a["music"] != "none" and mood_of.get(a["music"].lower()):
            moods.setdefault(cat, []).append(mood_of[a["music"].lower()])
    for cat, deltas in timing.items():
        if len(deltas) >= MIN_EDITS:
            ds = round(statistics.median(d[0] for d in deltas), 1)
            de = round(statistics.median(d[1] for d in deltas), 1)
            if abs(ds) >= 0.5 or abs(de) >= 0.5:
                proposals["detection"]["timing_adjust"][cat] = [ds, de]
                evidence.append(f"{cat} timing: agent moved start {ds:+g}s / end {de:+g}s (median of {len(deltas)})")
    for cat, ts in tones.items():
        if len(ts) >= MIN_EDITS:
            ratio = round(ts.count("funny") / len(ts), 1)
            proposals["captions"]["tone_by_category"][cat] = ratio
            evidence.append(f"{cat} captions: {ts.count('funny')} of {len(ts)} set to funny -> funny share {ratio:g}")
    for cat, ms in moods.items():
        if len(ms) >= MIN_EDITS:
            top = max(set(ms), key=ms.count)
            if ms.count(top) / len(ms) >= 0.6:
                proposals["music"]["mood_by_category"][cat] = top
                evidence.append(f"{cat} music: {ms.count(top)} of {len(ms)} picks from {top}")
    proposals = {k: {kk: vv for kk, vv in v.items() if vv} for k, v in proposals.items()}
    proposals = {k: v for k, v in proposals.items() if v}
    return proposals, evidence, decided, overrides


def cmd_distill(cfg, args):
    import yaml
    proposals, evidence, decided, overrides = distill(cfg)
    verdicts = [r["after"].get("agent") for r in decided.values()]
    print(f"Decisions so far: {len(decided)} clips ({verdicts.count('approved')} approved, "
          f"{verdicts.count('rejected')} rejected, {verdicts.count('flagged')} flagged). "
          f"Luke overrode {len(overrides)}.")
    if not proposals:
        print(f"No settings to propose yet: needs {MIN_VERDICTS}+ verdicts on a kind of clip, "
              f"or {MIN_EDITS}+ similar edits in a category.")
        return 0
    print("\nEvidence:\n" + "\n".join(f"  - {e}" for e in evidence))
    out = agent_dir(cfg) / "proposed_settings.yaml"
    out.write_text("# Proposed by the Clip Agent from its reviews. Apply with: tool.py apply-settings\n"
                   + yaml.safe_dump(proposals, sort_keys=False), encoding="utf-8")
    print(f"\nProposed settings ({out}):\n" + yaml.safe_dump(proposals, sort_keys=False))
    return 0


def cmd_apply_settings(cfg, args):
    """Merge proposed_settings.yaml into settings.yaml (only after Luke says yes)."""
    import yaml
    src = agent_dir(cfg) / "proposed_settings.yaml"
    if not src.exists():
        raise SystemExit("Nothing proposed yet: run 'distill' first")
    proposals = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
    path = config.ROOT / config.SETTINGS
    current = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    current = current or {}
    for section, values in proposals.items():
        for key, mapping in values.items():
            current.setdefault(section, {}).setdefault(key, {}).update(mapping)
    header = ("# Your Clip Studio settings. Only list what you want different from the defaults;\n"
              "# see settings.example.yaml for every option. Updates never change this file.\n")
    path.write_text(header + yaml.safe_dump(current, sort_keys=False, allow_unicode=True), encoding="utf-8")
    src.rename(src.with_name(f"applied_settings_{time.strftime('%Y-%m-%d_%H%M')}.yaml"))
    print(f"Applied to {path}. They take effect the next time a recording is analyzed (and for music "
          "and captions, the next export).")
    return 0


def cmd_handoff(cfg, args):
    """One file with everything the agent learned, to send to Claude so it becomes the app's defaults."""
    import yaml
    proposals, evidence, decided, overrides = distill(cfg)
    lines = ["# Clip Agent hand-off", time.strftime("%Y-%m-%d %H:%M"), "",
             "Everything the Clip Agent learned while reviewing Clip Studio's output. The goal is to build",
             "this into Clip Studio's defaults so the app gets it right without the agent.", ""]
    verdicts = [r["after"].get("agent") for r in decided.values()]
    lines += ["## Verdicts", f"{len(decided)} clips reviewed: {verdicts.count('approved')} approved, "
              f"{verdicts.count('rejected')} rejected, {verdicts.count('flagged')} flagged. "
              f"Luke overrode {len(overrides)}.", ""]
    reasons = {}
    for r in decided.values():
        if r["after"].get("agent") == "rejected":
            reasons[r["after"].get("reject_reason")] = reasons.get(r["after"].get("reject_reason"), 0) + 1
    lines += [f"- rejected as {feedback.REASONS.get(k, k)}: {v}" for k, v in sorted(reasons.items(), key=lambda x: -x[1])]
    lines += ["", "## Proposed settings", "Evidence:"] + [f"- {e}" for e in evidence or ["(not enough data yet)"]]
    lines += ["", "```yaml", yaml.safe_dump(proposals, sort_keys=False).strip() or "{}", "```", ""]
    lines += ["## Caption rewrites (detector's pick -> agent's)"]
    rewrites = [(r["before"].get("caption"), r["after"]["caption"], r.get("category"))
                for r in decided.values() if "caption" in r["after"]]
    lines += [f"- [{c}] {b!r} -> {a!r}" for b, a, c in rewrites] or ["(none)"]
    custom = config.resolve(captions.CUSTOM)
    lines += ["", "## Caption lines added to the library", "```yaml",
              custom.read_text(encoding="utf-8").strip() if custom.exists() else "(none)", "```", ""]
    lines += ["## Music catalog"]
    cat = music.MusicLibrary(cfg).catalog()
    lines += [f"- {r['file']}: {r['title']} by {r['artist']} ({r['license']}, energy {r.get('energy') or '?'})"
              for r in cat.values()] or ["(no catalogued tracks)"]
    lines += ["", "## Agent notes on individual clips"]
    lines += [f"- {k[0]} clip {k[1]} [{r.get('category')}, {feedback.describe(r['signal']) if r.get('signal') else 'manual'}]"
              f" {r['after'].get('agent') or 'edited'}: {r.get('note') or ''}"
              + (f"  (Luke {overrides[k]} it)" if k in overrides else "")
              for k, r in decided.items()] or ["(none)"]
    knowledge = agent_dir(cfg) / "knowledge.md"
    lines += ["", "## The agent's knowledge file", "",
              knowledge.read_text(encoding="utf-8") if knowledge.exists() else "(none)", ""]
    lines += ["## Luke's own feedback in Clip Studio", "", feedback.Feedback(pipeline.feedback_path(cfg)).report()]
    out = agent_dir(cfg) / "handoff.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(out)
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
    s = a.add_parser("todo", help="clips Luke analyzed that the agent hasn't reviewed")
    s.add_argument("--top", type=int, default=8, help="per recording, highest score first (0 = all)")
    s = a.add_parser("review", help="approve, reject or flag a clip, and fix its caption, music or timing")
    s.add_argument("recording")
    s.add_argument("clip", type=int)
    g = s.add_mutually_exclusive_group()
    g.add_argument("--approve", "--keep", action="store_true", dest="approve")
    g.add_argument("--reject", metavar="REASON")
    s.add_argument("--flag", action="store_true", help="flag it for Luke to look at")
    s.add_argument("--note", help="the agent's note, shown in Clip Studio")
    s.add_argument("--caption", help="new on-screen caption (the old one is kept as the alternative)")
    s.add_argument("--tone", help="funny or motivational")
    s.add_argument("--music", help="a track's file name, 'auto' or 'none'")
    s.add_argument("--category")
    s.add_argument("--start", type=float, help="seconds into the recording")
    s.add_argument("--end", type=float)
    s = a.add_parser("caption-add", help="add a line to the caption library (captions/my_captions.yaml)")
    s.add_argument("label", help="a library section, e.g. GOAL, EPIC SAVE, ASSIST, FUNNY MOMENT, ANY")
    s.add_argument("tone", help="funny or motivational")
    s.add_argument("line")
    a.add_parser("music-list", help="tracks in the music folder with mood, length, drop and license")
    s = a.add_parser("music-add", help="add a licensed track from a download link or a file (mp3, mp4, wav...)")
    s.add_argument("source", help="direct download link or a file on this PC")
    s.add_argument("--mood", required=True, help="hype, funny or chill")
    s.add_argument("--title", required=True)
    s.add_argument("--artist", required=True)
    s.add_argument("--license", required=True, help=", ".join(music.LICENSES))
    s.add_argument("--attribution", help="the credit line the license asks for")
    s.add_argument("--source-url", help="the page the track came from")
    s.add_argument("--energy", help="low, medium or high")
    s.add_argument("--notes")
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
    a.add_parser("distill", help="turn the agent's decisions into proposed settings")
    a.add_parser("apply-settings", help="write the proposed settings into settings.yaml (after Luke agrees)")
    a.add_parser("handoff", help="write handoff.md: everything learned, to build into the app")


COMMANDS = {"status": cmd_status, "scan": cmd_scan, "sheet": cmd_sheet, "review": cmd_review,
            "package": cmd_package, "queue": cmd_queue, "posted": cmd_posted, "feedback": cmd_feedback,
            "todo": cmd_todo, "caption-add": cmd_caption_add, "music-list": cmd_music_list,
            "music-add": cmd_music_add, "distill": cmd_distill, "apply-settings": cmd_apply_settings,
            "handoff": cmd_handoff}


def run(cfg, args):
    return COMMANDS[args.agent_command](cfg, args)
