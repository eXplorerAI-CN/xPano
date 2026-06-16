import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import MaterialTrack, MultiTrackJobConfig, locate_metashape, material_tracks_to_job_config, run_multi_track_pipeline
from scripts.xpano_tracks import load_manifest, validate_manifest


def parse_track_args(values):
    tracks = []
    for value in values or []:
        if len(value) < 2:
            raise ValueError("Photo tracks require LABEL followed by one or more paths")
        tracks.append((value[0], value[1:]))
    return tracks


def build_material_tracks(panorama_videos, standard_tracks, aerial_tracks):
    tracks = []
    for path in panorama_videos:
        video = Path(path).resolve()
        tracks.append(MaterialTrack(track_type="panorama_video", label=video.stem, paths=[video]))
    for label, paths in standard_tracks:
        tracks.append(MaterialTrack(track_type="standard_photos", label=label, paths=[Path(path).resolve() for path in paths]))
    for label, paths in aerial_tracks:
        tracks.append(MaterialTrack(track_type="aerial_photos", label=label, paths=[Path(path).resolve() for path in paths]))
    return tracks


def validate_run_args(seconds_per_frame, max_frames):
    if seconds_per_frame <= 0:
        raise ValueError("--seconds-per-frame must be greater than 0")
    if max_frames < 0:
        raise ValueError("--max-frames must be greater than or equal to 0")


def main():
    parser = argparse.ArgumentParser(description="Run xPano multi-material-track workflow")
    parser.add_argument("--output", required=True)
    parser.add_argument("--metashape", default=locate_metashape())
    parser.add_argument("--seconds-per-frame", type=float, default=1.0)
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument("--manifest")
    parser.add_argument("--pano", action="append", default=[], help="Panorama OSV/INSV/MP4 video. Repeat for multiple panorama tracks.")
    parser.add_argument("--standard-track", action="append", nargs="+", default=[], metavar=("LABEL", "PATH"))
    parser.add_argument("--aerial-track", action="append", nargs="+", default=[], metavar=("LABEL", "PATH"))
    parser.add_argument("--keep-generated", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output).resolve()

    if args.manifest:
        manifest_path = Path(args.manifest).resolve()
        validate_manifest(load_manifest(manifest_path))
    else:
        validate_run_args(args.seconds_per_frame, args.max_frames)
        manifest_path = None

    metashape_exe = args.metashape
    if metashape_exe.lower() == "metashape.exe" and not shutil.which("metashape.exe"):
        raise RuntimeError("metashape.exe was not found in PATH")
    if metashape_exe.lower() != "metashape.exe" and not Path(metashape_exe).exists():
        raise FileNotFoundError(metashape_exe)

    if manifest_path:
        job = MultiTrackJobConfig(
            panorama_videos=[],
            standard_photo_tracks=[],
            aerial_photo_tracks=[],
            output_dir=output_dir,
            seconds_per_frame=args.seconds_per_frame,
            max_frames=args.max_frames,
            metashape_exe=metashape_exe,
            overwrite_generated=False,
            manifest_path=manifest_path,
        )
    else:
        tracks = build_material_tracks(args.pano, parse_track_args(args.standard_track), parse_track_args(args.aerial_track))
        job = material_tracks_to_job_config(
            tracks=tracks,
            output_dir=output_dir,
            seconds_per_frame=args.seconds_per_frame,
            max_frames=args.max_frames,
            metashape_exe=metashape_exe,
            overwrite_generated=not args.keep_generated,
        )

    def progress(value):
        print(f"PROGRESS:{value}", flush=True)

    def preview(left, right):
        print(f"PREVIEW:{left}|{right}", flush=True)

    def log(text):
        print(text, flush=True)

    run_multi_track_pipeline(job, progress, preview, log)
    print("xPano multi-track job complete", flush=True)


if __name__ == "__main__":
    main()
