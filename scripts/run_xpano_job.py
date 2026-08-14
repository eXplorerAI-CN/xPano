import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.pipeline_core import JobConfig, locate_metashape, run_metashape_pipeline


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--frames-per-second", type=float)
    parser.add_argument("--seconds-per-frame", dest="legacy_seconds_per_frame", type=float, help=argparse.SUPPRESS)
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument("--metashape", default=locate_metashape())
    parser.add_argument("--metashape-site-packages", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.frames_per_second is not None and args.legacy_seconds_per_frame is not None:
        raise ValueError("Use --frames-per-second or legacy --seconds-per-frame, not both")
    if args.legacy_seconds_per_frame is not None:
        if not math.isfinite(args.legacy_seconds_per_frame) or args.legacy_seconds_per_frame <= 0:
            raise ValueError("--seconds-per-frame must be greater than 0")
        args.frames_per_second = 1.0 / args.legacy_seconds_per_frame
    if args.frames_per_second is None:
        args.frames_per_second = 1.0
    if not math.isfinite(args.frames_per_second) or args.frames_per_second <= 0:
        raise ValueError("--frames-per-second must be greater than 0")

    job = JobConfig(
        input_video=Path(args.input),
        output_dir=Path(args.output),
        frames_per_second=args.frames_per_second,
        max_frames=args.max_frames,
        metashape_exe=args.metashape,
        metashape_site_packages=(
            Path(args.metashape_site_packages).resolve()
            if args.metashape_site_packages
            else None
        ),
    )

    def progress(value):
        print(f"PROGRESS:{value}", flush=True)

    def preview(left, right):
        print(f"PREVIEW:{left}|{right}", flush=True)

    def log(text):
        print(text, flush=True)

    run_metashape_pipeline(job, progress, preview, log)


if __name__ == "__main__":
    main()
