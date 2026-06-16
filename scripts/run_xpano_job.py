import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import JobConfig, locate_metashape, run_metashape_pipeline


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--seconds-per-frame", type=float, default=1.0)
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument("--metashape", default=locate_metashape())
    args = parser.parse_args()

    job = JobConfig(
        input_video=Path(args.input),
        output_dir=Path(args.output),
        seconds_per_frame=args.seconds_per_frame,
        max_frames=args.max_frames,
        metashape_exe=args.metashape,
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
