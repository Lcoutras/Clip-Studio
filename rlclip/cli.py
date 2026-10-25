import argparse
import sys
from pathlib import Path

from . import agent, config, detect, ffmpeg, pipeline


def _formats(cfg, override):
    fmt = override or cfg["render"]["format"]
    return ["vertical", "horizontal"] if fmt == "both" else [fmt]


def cmd_run(args, cfg, render_too=True):
    videos = pipeline.find_videos(args.target)
    if not videos:
        print(f"No videos found in {args.target}")
        return 1
    for video in videos:
        print(f"\n=== {video.name}")
        project = pipeline.Project(video, cfg)
        found = project.detect(rescan=args.rescan)
        if not found:
            print("  no highlights found (try lowering detection.min_score in settings.yaml)")
            continue
        rows = project.write_captions(found, recaption=args.recaption or args.rescan)
        if render_too:
            made = project.render_all(rows, _formats(cfg, args.format))
            print(f"  done: {len(made)} edits in {pipeline.exports_dir(cfg)}")
        else:
            print(f"  captions ready to edit: {project.csv_path}")
    return 0


def cmd_render(args, cfg):
    for video in pipeline.find_videos(args.target):
        project = pipeline.Project(video, cfg)
        if not project.csv_path.exists():
            print(f"{video.name}: no captions.csv yet - run 'scan' or 'run' first")
            continue
        print(f"\n=== {video.name}")
        made = project.render_all(project.read_rows(), _formats(cfg, args.format))
        print(f"  done: {len(made)} edits in {pipeline.exports_dir(cfg)}")
    return 0


def cmd_calibrate(args, cfg):
    """Save a frame with the scan area drawn on it, plus what OCR reads there."""
    import cv2

    info = ffmpeg.probe(args.video)
    at = detect.parse_timestamp(args.at)
    full, lines, (x, y, w, h) = detect.ocr_frame(info, cfg["detection"], at)
    img = full.copy()
    cv2.rectangle(img, (x, y), (x + w, y + h), (0, 255, 255), 4)
    out = Path(args.video).with_name(f"{Path(args.video).stem}_calibrate.png")
    cv2.imwrite(str(out), img)
    print(f"Saved {out}  (yellow box = area scanned for popups)")
    print("OCR read:" if lines else "OCR read nothing in the box at that moment.")
    for text, conf in lines:
        print(f"  {text!r:40} conf={conf:.2f}")
    texts = [t for t, c in lines if c >= cfg["detection"]["ocr_min_confidence"]]
    found = detect.classify_frame(texts, cfg["detection"]["player_names"])
    print("Counts as a play: " + (", ".join(kw + (" (yours)" if mine else "") for kw, mine in found)
                                  if found else "no"))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="rlclip", description="Rocket League highlight clipper")
    parser.add_argument("--config", help="path to a settings file (default: settings.yaml)")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("target", help="a video file or a folder of videos")
        p.add_argument("--format", choices=["vertical", "horizontal", "both"])
        p.add_argument("--rescan", action="store_true", help="redo detection even if saved")
        p.add_argument("--recaption", action="store_true", help="rewrite captions.csv")

    common(sub.add_parser("run", help="find highlights, caption and render them"))
    common(sub.add_parser("scan", help="find highlights and write captions.csv, no rendering"))
    p = sub.add_parser("render", help="render from an (edited) captions.csv")
    p.add_argument("target")
    p.add_argument("--format", choices=["vertical", "horizontal", "both"])
    sub.add_parser("studio", help="open the Clip Studio desktop app")
    sub.add_parser("app", help="open Clip Studio in your web browser instead of its own window")
    p = sub.add_parser("calibrate", help="check what the popup detector sees at a timestamp")
    p.add_argument("video")
    p.add_argument("--at", required=True, help="timestamp of a popup, e.g. 12:34")

    agent.add_parser(sub)
    p = sub.add_parser("tiktok", help="upload exports to TikTok (see docs/tiktok-setup.md)")
    p.add_argument("action", choices=["start", "connect", "upload", "status", "resend"],
                   help="start = sign in if needed, then keep uploading new exports")
    p.add_argument("--watch", action="store_true", help="keep watching the Exports folder")
    p.add_argument("--limit", type=int, help="upload at most this many now")

    args = parser.parse_args(argv)
    cfg = config.load(args.config)
    try:
        ffmpeg.require_ffmpeg()
        if args.command == "run":
            return cmd_run(args, cfg)
        if args.command == "scan":
            return cmd_run(args, cfg, render_too=False)
        if args.command == "render":
            return cmd_render(args, cfg)
        if args.command == "agent":
            return agent.run(cfg, args)
        if args.command == "tiktok":
            from . import tiktok
            try:
                if args.action == "connect" or (args.action == "start" and
                                                not (config.resolve(cfg["output_dir"]) / "tiktok" / "login.json").exists()):
                    tiktok.connect(cfg)
                if args.action == "start" or args.watch:
                    tiktok.watch(cfg)
                elif args.action == "upload":
                    tiktok.upload_new(cfg, limit=args.limit)
                elif args.action == "status":
                    tiktok.report_status(cfg)
                elif args.action == "resend":
                    tiktok.resend_unposted(cfg)
            except tiktok.TikTokError as e:
                print(f"\nTikTok: {e}")
                return 1
            return 0
        if args.command in ("studio", "app"):
            from . import desktop
            desktop.main(cfg, windowed=args.command == "studio")
            return 0
        return cmd_calibrate(args, cfg)
    except (ffmpeg.FFmpegError, RuntimeError) as e:
        print(f"\nERROR: {e}")
        return 1
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
