import argparse
import sys
from pathlib import Path

from . import config, detect, ffmpeg, pipeline


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
            print("  no highlights found (try lowering detection.min_score in config.yaml)")
            continue
        rows = project.write_captions(found, recaption=args.recaption or args.rescan)
        if render_too:
            made = project.render_all(rows, _formats(cfg, args.format))
            print(f"  done: {len(made)} edits in {project.out_dir}")
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
        print(f"  done: {len(made)} edits in {project.out_dir}")
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
    found = detect.match_keywords([t for t, c in lines if c >= cfg["detection"]["ocr_min_confidence"]])
    print("Detected: " + (", ".join(found) if found else "nothing"))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="rlclip", description="Rocket League highlight clipper")
    parser.add_argument("--config", help="path to config.yaml")
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
    p = sub.add_parser("calibrate", help="check what the popup detector sees at a timestamp")
    p.add_argument("video")
    p.add_argument("--at", required=True, help="timestamp of a popup, e.g. 12:34")

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
        return cmd_calibrate(args, cfg)
    except (ffmpeg.FFmpegError, RuntimeError) as e:
        print(f"\nERROR: {e}")
        return 1
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
