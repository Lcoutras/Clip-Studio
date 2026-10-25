"""Cuts a highlight out of the recording and renders the finished edit."""

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

from . import ffmpeg, highlights, overlays

SIZES = {"vertical": (1080, 1920), "horizontal": (1920, 1080)}


def voice_mode(cfg, info):
    """Decide how to get rid of voices for this recording."""
    mode = cfg["audio"]["voice_removal"]
    if info.audio_tracks == 0:
        return "none"
    if mode == "track" and info.audio_tracks < 2:
        print("  ! voice_removal is 'track' but this video has only one audio track")
        mode = "auto"
    if mode == "auto":
        if info.audio_tracks >= 2:
            return "track"
        if importlib.util.find_spec("demucs") is not None:
            return "demucs"
        print("  ! Voices are mixed into the game audio and the voice remover isn't installed\n"
              "    (run setup-voice-removal.bat). These clips will use music only.")
        return "mute"
    return mode


def extract(info, start, end, workdir, mode, game_track):
    """Cut the raw clip. Returns (video_path, game_audio_path or None)."""
    workdir.mkdir(parents=True, exist_ok=True)
    video = workdir / "raw.mp4"
    duration = end - start
    if not video.exists():
        ffmpeg.run(["-ss", f"{start:.3f}", "-i", info.path, "-t", f"{duration:.3f}",
                    "-map", "0:v:0", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
                    "-pix_fmt", "yuv420p", video])
    if mode in ("none", "mute"):
        return video, None

    game = workdir / "game.wav"
    if not game.exists():
        ffmpeg.run(["-ss", f"{start:.3f}", "-i", info.path, "-t", f"{duration:.3f}",
                    "-map", f"0:a:{game_track}", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le",
                    workdir / "mix.wav"])
        if mode == "demucs":
            _demucs(workdir / "mix.wav", game, workdir)
        else:  # "track" (game-only track) or "keep" (use the audio as-is)
            (workdir / "mix.wav").replace(game)
    return video, game


def _demucs(src, dst, workdir):
    print("    removing voices (AI vocal separation)...")
    out = workdir / "demucs"
    result = subprocess.run([sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs",
                             "-o", str(out), str(src)], capture_output=True, text=True,
                            creationflags=ffmpeg.BACKGROUND)
    stem = out / "htdemucs" / src.stem / "no_vocals.wav"
    if result.returncode != 0 or not stem.exists():
        raise RuntimeError("Voice removal (demucs) failed:\n" + result.stderr[-2000:])
    shutil.move(str(stem), dst)
    shutil.rmtree(out, ignore_errors=True)


def save_raw(video, game, dst):
    """Keep an unedited horizontal copy of the play (voices already removed)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if game is None:
        shutil.copyfile(video, dst)
    else:
        ffmpeg.run(["-i", video, "-i", game, "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", dst])


def _atempo(speed):
    filters = []
    while speed < 0.5:
        filters.append("atempo=0.5")
        speed /= 0.5
    filters.append(f"atempo={speed:.4f}")
    return ",".join(filters)


def _encoder_args(r):
    enc = r["encoder"]
    if enc == "libx264":
        return ["-c:v", "libx264", "-preset", "medium", "-crf", str(r["crf"])]
    if enc.endswith("_nvenc"):
        return ["-c:v", enc, "-preset", "p5", "-rc", "vbr", "-cq", str(r["crf"] + 2), "-b:v", "0"]
    return ["-c:v", enc]


def render(cfg, video, game, event_time, layout, caption, label, music, out_path, workdir, stat="", story=None):
    """music is None or (track_path, drop_time_seconds). story: {"beats": [{"at": seconds into the
    clip, "text": ...}], "payoff": ...}: lines under the gameplay that change as the play unfolds
    (the caption becomes the title and the payoff replaces the stamp)."""
    r, a = cfg["render"], cfg["audio"]
    vinfo = ffmpeg.probe(video)
    duration = vinfo.duration
    W, H = SIZES[layout]
    fps = r["fps"]

    # ---- timeline: optional slow motion leading into the moment
    te = min(max(event_time, 0.0), duration)
    s0 = te - r["slowmo_seconds"]
    slowmo = r["slowmo"] and s0 >= 0.5 and te < duration - 0.3
    speed = min(max(r["slowmo_speed"], 0.1), 0.99)
    if slowmo:
        te_out = s0 + (te - s0) / speed
        total = duration + (te - s0) * (1 / speed - 1)
    else:
        te_out, total = te, duration

    def out_t(t):
        """A time in the source clip -> the same moment in the output (slow motion stretches it)."""
        if not slowmo or t <= s0:
            return t
        return s0 + (min(t, te) - s0) / speed + max(0.0, t - te)

    story = story if story and (story.get("beats") or story.get("payoff")) else None
    font = overlays.font_path(cfg["captions"]["font"])
    cap_png = workdir / f"caption_{layout}.png"
    overlays.caption_image(caption, (W, H), layout, font, cfg["captions"]["watermark"],
                           two_tone=bool(story)).save(cap_png)
    # Story lines: (png, from, to) in output time. Each beat lasts until the next one; the payoff
    # lands on the play and stays to the end.
    lines = []
    if story:
        beats = sorted((b for b in story.get("beats") or [] if str(b.get("text", "")).strip()),
                       key=lambda b: float(b["at"]))
        payoff = str(story.get("payoff") or "").strip()
        for i, b in enumerate(beats):
            start = out_t(max(0.0, float(b["at"])))
            end = out_t(float(beats[i + 1]["at"])) if i + 1 < len(beats) else (te_out if payoff else total)
            if end - start > 0.05:
                png = workdir / f"beat{i}_{layout}.png"
                overlays.story_image(b["text"], (W, H), layout, font, "beat").save(png)
                lines.append((png, start, end))
        if payoff:
            png = workdir / f"payoff_{layout}.png"
            overlays.story_image(payoff, (W, H), layout, font, "payoff").save(png)
            lines.append((png, te_out, total + 1))
    label_png = None
    if not story and cfg["captions"]["show_event_label"] and highlights.stamp_text(label):
        label_png = workdir / f"label_{layout}.png"
        overlays.label_image(highlights.STAMP_TEXT.get(label, label), (W, H), layout, font, stat).save(label_png)

    args = ["-i", video]
    graph = []
    n = 1
    game_idx = music_idx = None
    if game is not None:
        args += ["-i", game]
        game_idx, n = n, n + 1
    if music is not None:
        track, drop = music
        offset = drop - te_out
        if offset > 0:
            args += ["-ss", f"{offset:.3f}"]
        args += ["-i", track]
        music_idx, n = n, n + 1
    args += ["-loop", "1", "-framerate", str(fps), "-i", cap_png]
    cap_idx, n = n, n + 1
    if label_png:
        args += ["-loop", "1", "-framerate", str(fps), "-i", label_png]
        label_idx, n = n, n + 1
    line_idx = []
    for png, _, _ in lines:
        args += ["-loop", "1", "-framerate", str(fps), "-i", png]
        line_idx.append(n)
        n += 1

    # ---- video
    if slowmo:
        graph.append(
            f"[0:v]split=3[v1][v2][v3];"
            f"[v1]trim=0:{s0:.3f},setpts=PTS-STARTPTS[va];"
            f"[v2]trim={s0:.3f}:{te:.3f},setpts=(PTS-STARTPTS)/{speed}[vb];"
            f"[v3]trim={te:.3f},setpts=PTS-STARTPTS[vc];"
            f"[va][vb][vc]concat=n=3:v=1:a=0[vs]")
    else:
        graph.append("[0:v]null[vs]")
    graph.append(f"[vs]fps={fps}[vf]")
    if r["zoom_punch"]:
        d = 0.35
        graph.append(
            f"[vf]zoompan=z='if(between(it,{te_out:.3f},{te_out + d:.3f}),"
            f"1+0.1*(1-(it-{te_out:.3f})/{d}),1)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d=1:s={vinfo.width}x{vinfo.height}:fps={fps}[vz]")
    else:
        graph.append("[vf]null[vz]")

    if layout == "vertical" and r["vertical_style"] == "crop":
        graph.append(f"[vz]crop=ih*9/16:ih:(iw-ow)*{float(r.get('crop_x', 0.5)):.3f}:0,scale={W}:{H}[comp]")
    elif layout == "vertical":
        graph.append(
            f"[vz]split[bg][fg];"
            f"[bg]scale=-2:480,crop=270:480,boxblur=8:2,eq=brightness=-0.12,scale={W}:{H}[bgb];"
            f"[fg]crop='min(iw,ih*{r['vertical_fg_aspect']})':ih,scale={W}:-2[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2[comp]")
    else:
        graph.append(f"[vz]scale={W}:{H}:force_original_aspect_ratio=decrease,"
                     f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2[comp]")

    graph.append(f"[{cap_idx}:v]format=rgba,fade=t=in:st=0.15:d=0.35:alpha=1[cap];"
                 f"[comp][cap]overlay=0:0:shortest=1[c1]")
    last = "c1"
    if label_png:
        graph.append(f"[{label_idx}:v]format=rgba,fade=t=in:st={te_out:.3f}:d=0.12:alpha=1,"
                     f"fade=t=out:st={te_out + 1.6:.3f}:d=0.3:alpha=1[lab];"
                     f"[c1][lab]overlay=0:0:shortest=1[c2]")
        last = "c2"
    for k, ((_, start, end), idx) in enumerate(zip(lines, line_idx)):
        graph.append(f"[{idx}:v]format=rgba[s{k}];[{last}][s{k}]overlay=0:0:shortest=1:"
                     f"enable='between(t,{start:.3f},{end:.3f})'[st{k}]")
        last = f"st{k}"
    graph.append(f"[{last}]setsar=1,format=yuv420p[vout]")

    # ---- audio
    audio_out = None
    if game_idx is not None:
        if slowmo:
            graph.append(
                f"[{game_idx}:a]asplit=3[a1][a2][a3];"
                f"[a1]atrim=0:{s0:.3f},asetpts=PTS-STARTPTS[aa];"
                f"[a2]atrim={s0:.3f}:{te:.3f},asetpts=PTS-STARTPTS,{_atempo(speed)}[ab];"
                f"[a3]atrim={te:.3f},asetpts=PTS-STARTPTS[ac];"
                f"[aa][ab][ac]concat=n=3:v=0:a=1[gs]")
        else:
            graph.append(f"[{game_idx}:a]anull[gs]")
        gain = float(a.get("gain", 1.0))
        base = (a["game_volume"] if music_idx is not None else 1.0) * gain
        peak = max(base, a["game_volume_on_play"] * gain)
        graph.append(f"[gs]volume='if(between(t,{te_out - 0.3:.3f},{te_out + 1.8:.3f}),{peak},{base})'"
                     f":eval=frame,atrim=0:{total:.3f}[gv]")
        audio_out = "gv"
    if music_idx is not None:
        delay = max(0.0, te_out - music[1])
        fo = min(cfg["music"]["fade_out"], total / 3)
        chain = f"[{music_idx}:a]aresample=48000,aformat=channel_layouts=stereo"
        if delay > 0:
            chain += f",adelay={int(delay * 1000)}:all=1"
        chain += (f",apad,atrim=0:{total:.3f},asetpts=PTS-STARTPTS,afade=t=in:st=0:d=0.4,"
                  f"afade=t=out:st={total - fo:.3f}:d={fo:.3f},volume={cfg['music']['volume']}[mv]")
        graph.append(chain)
        if audio_out:
            graph.append("[gv][mv]amix=inputs=2:duration=longest:normalize=0[mix]")
            audio_out = "mix"
        else:
            audio_out = "mv"
    if audio_out:
        graph.append(f"[{audio_out}]alimiter=limit=0.95[aout]")
    else:
        # Silent track, so every clip has audio (needed to join clips into a highlight video).
        args += ["-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        graph.append(f"[{n}:a]anull[aout]")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    args += ["-filter_complex", ";".join(graph), "-map", "[vout]"]
    args += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
    args += [*_encoder_args(r), "-r", str(fps), "-t", f"{total:.3f}", "-movflags", "+faststart", out_path]
    ffmpeg.run(args)
    return Path(out_path)
