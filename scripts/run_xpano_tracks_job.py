import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.pipeline_core import MaterialTrack, MultiTrackJobConfig, locate_metashape, material_tracks_to_job_config, run_multi_track_pipeline
from scripts.dependency_checks import (
    check_pipeline_dependencies,
    format_dependency_report,
    locate_colmap,
    locate_lichtfield,
    require_dependency_checks,
    resolve_executable,
)
from scripts.pipeline_backends import COLMAP_BACKEND, METASHAPE_BACKEND, SUPPORTED_BACKENDS, normalize_backend
from scripts.colmap_backend import COLMAP_DENSITY_PRESETS
from scripts.lichtfeld_densify import locate_densify_python
from scripts.metashape_alignment_modes import ALIGNMENT_MODE_BACKBONE, SUPPORTED_ALIGNMENT_MODES, normalize_alignment_mode
from scripts.xpano_tracks import DEFAULT_ORDINARY_CAMERA_PROFILE, CAMERA_PROFILES, load_manifest, normalize_camera_profile, validate_manifest


def parse_track_args(values):
    tracks = []
    for value in values or []:
        if len(value) < 2:
            raise ValueError("Photo tracks require LABEL followed by one or more paths")
        tracks.append((value[0], value[1:]))
    return tracks


def build_material_tracks(panorama_videos, ordinary_videos, standard_tracks, aerial_tracks, ordinary_camera_profiles=None):
    tracks = []
    ordinary_camera_profiles = ordinary_camera_profiles or []
    if ordinary_camera_profiles and len(ordinary_camera_profiles) != len(ordinary_videos or []):
        raise ValueError("--ordinary-view must be provided once for each --ordinary-video")
    for path in panorama_videos:
        video = Path(path).resolve()
        tracks.append(MaterialTrack(track_type="panorama_video", label=video.stem, paths=[video]))
    for index, path in enumerate(ordinary_videos):
        video = Path(path).resolve()
        profile = ordinary_camera_profiles[index] if ordinary_camera_profiles else DEFAULT_ORDINARY_CAMERA_PROFILE
        tracks.append(MaterialTrack(track_type="ordinary_video", label=video.stem, paths=[video], camera_profile=normalize_camera_profile(profile)))
    for label, paths in standard_tracks:
        tracks.append(MaterialTrack(track_type="standard_photos", label=label, paths=[Path(path).resolve() for path in paths]))
    for label, paths in aerial_tracks:
        tracks.append(MaterialTrack(track_type="aerial_photos", label=label, paths=[Path(path).resolve() for path in paths]))
    return tracks


def build_video_extraction_settings(video_paths, starts, ends, frames_per_second_values, max_frame_values, option_prefix):
    starts = starts or []
    ends = ends or []
    frames_per_second_values = frames_per_second_values or []
    max_frame_values = max_frame_values or []
    count = len(video_paths or [])
    for name, values in [
        (f"--{option_prefix}-start", starts),
        (f"--{option_prefix}-end", ends),
        (f"--{option_prefix}-frames-per-second", frames_per_second_values),
        (f"--{option_prefix}-max-frames", max_frame_values),
    ]:
        if values and len(values) != count:
            raise ValueError(f"{name} must be provided once for each --{option_prefix}")

    settings = {}
    for index, path in enumerate(video_paths or []):
        values = {}
        if starts:
            values["start_time_seconds"] = starts[index]
        if ends:
            values["end_time_seconds"] = ends[index]
        if frames_per_second_values:
            values["frames_per_second"] = frames_per_second_values[index]
        if max_frame_values:
            values["max_frames"] = max_frame_values[index]
        if values:
            settings[str(Path(path).resolve())] = values
    return settings


def build_track_extraction_settings(
    panorama_videos,
    pano_starts,
    pano_ends,
    pano_frames_per_second_values,
    pano_max_frame_values,
    ordinary_videos=None,
    ordinary_starts=None,
    ordinary_ends=None,
    ordinary_frames_per_second_values=None,
    ordinary_max_frame_values=None,
):
    settings = build_video_extraction_settings(
        panorama_videos,
        pano_starts,
        pano_ends,
        pano_frames_per_second_values,
        pano_max_frame_values,
        "pano",
    )
    settings.update(
        build_video_extraction_settings(
            ordinary_videos or [],
            ordinary_starts,
            ordinary_ends,
            ordinary_frames_per_second_values,
            ordinary_max_frame_values,
            "ordinary",
        )
    )
    return settings


def resolve_frames_per_second(frames_per_second=None, legacy_seconds_per_frame=None):
    if frames_per_second is not None and legacy_seconds_per_frame is not None:
        raise ValueError("Use --frames-per-second or legacy --seconds-per-frame, not both")
    if legacy_seconds_per_frame is not None:
        if not math.isfinite(legacy_seconds_per_frame) or legacy_seconds_per_frame <= 0:
            raise ValueError("--seconds-per-frame must be greater than 0")
        return 1.0 / legacy_seconds_per_frame
    return 1.0 if frames_per_second is None else frames_per_second


def legacy_intervals_to_fps(values):
    return [resolve_frames_per_second(legacy_seconds_per_frame=value) for value in values or []]


def validate_run_args(frames_per_second, max_frames):
    if not math.isfinite(frames_per_second) or frames_per_second <= 0:
        raise ValueError("--frames-per-second must be greater than 0")
    if max_frames < 0:
        raise ValueError("--max-frames must be greater than or equal to 0")


def configure_console_output():
    for stream in [sys.stdout, sys.stderr]:
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


def main():
    configure_console_output()
    parser = argparse.ArgumentParser(description="Run xPano multi-material-track workflow")
    parser.add_argument("--output")
    parser.add_argument("--metashape", default=locate_metashape())
    parser.add_argument("--metashape-site-packages", help=argparse.SUPPRESS)
    parser.add_argument("--colmap", default=locate_colmap())
    parser.add_argument("--colmap-density-preset", default="stable", choices=COLMAP_DENSITY_PRESETS)
    parser.add_argument("--colmap-use-gpu", action="store_true", help="Enable COLMAP CUDA/GPU feature extraction and matching.")
    parser.add_argument("--colmap-matcher", default="sequential", choices=["sequential", "exhaustive"])
    parser.add_argument("--colmap-max-image-size", type=int)
    parser.add_argument("--colmap-max-num-features", type=int)
    parser.add_argument("--metashape-keypoint-limit", type=int, default=40000)
    parser.add_argument("--metashape-tiepoint-limit", type=int, default=0)
    parser.add_argument("--metashape-alignment-mode", default=ALIGNMENT_MODE_BACKBONE, choices=sorted(SUPPORTED_ALIGNMENT_MODES))
    parser.add_argument("--component-key")
    parser.add_argument("--up-axis", default="y-up")
    parser.add_argument("--backend", default=METASHAPE_BACKEND, choices=sorted(SUPPORTED_BACKENDS))
    parser.add_argument("--check-env", action="store_true", help="Print dependency diagnostics and exit.")
    parser.add_argument("--strict", action="store_true", help="With --check-env, fail if required dependencies are missing.")
    parser.add_argument("--run-lichtfield", action="store_true")
    parser.add_argument("--lichtfield", default=locate_lichtfield())
    parser.add_argument("--lichtfield-point-count", type=int, default=0)
    parser.add_argument("--lichtfield-bilateral-grid", type=int, default=0)
    parser.add_argument("--run-lfs-densify", action="store_true")
    parser.add_argument("--lfs-densify-python")
    parser.add_argument("--lfs-densify-plugin")
    parser.add_argument("--lfs-densify-roma", default="fast", choices=["precise", "high", "base", "fast", "turbo"])
    parser.add_argument("--lfs-densify-num-refs", type=float, default=0.75)
    parser.add_argument("--lfs-densify-max-points", type=int, default=0)
    parser.add_argument("--frames-per-second", type=float)
    parser.add_argument("--seconds-per-frame", dest="legacy_seconds_per_frame", type=float, help=argparse.SUPPRESS)
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument("--manifest")
    parser.add_argument("--pano", action="append", default=[], help="Panorama OSV/INSV video. Repeat for multiple panorama tracks.")
    parser.add_argument("--pano-start", action="append", type=float, default=[])
    parser.add_argument("--pano-end", action="append", type=float, default=[])
    parser.add_argument("--pano-frames-per-second", action="append", type=float, default=[])
    parser.add_argument("--pano-seconds-per-frame", dest="pano_legacy_seconds_per_frame", action="append", type=float, default=[], help=argparse.SUPPRESS)
    parser.add_argument("--pano-max-frames", action="append", type=int, default=[])
    parser.add_argument("--ordinary-video", action="append", default=[], help="Ordinary video file. Repeat for multiple ordinary video tracks.")
    parser.add_argument("--ordinary-view", action="append", default=[], choices=sorted(CAMERA_PROFILES), help="View preset for each ordinary video: standard or wide.")
    parser.add_argument("--ordinary-start", action="append", type=float, default=[])
    parser.add_argument("--ordinary-end", action="append", type=float, default=[])
    parser.add_argument("--ordinary-frames-per-second", action="append", type=float, default=[])
    parser.add_argument("--ordinary-seconds-per-frame", dest="ordinary_legacy_seconds_per_frame", action="append", type=float, default=[], help=argparse.SUPPRESS)
    parser.add_argument("--ordinary-max-frames", action="append", type=int, default=[])
    parser.add_argument("--standard-track", action="append", nargs="+", default=[], metavar=("LABEL", "PATH"))
    parser.add_argument("--aerial-track", action="append", nargs="+", default=[], metavar=("LABEL", "PATH"))
    parser.add_argument("--keep-generated", action="store_true")
    parser.add_argument("--skip-extract", action="store_true")
    parser.add_argument("--reexport-existing-project", action="store_true")
    parser.add_argument("--existing-project")
    args = parser.parse_args()
    args.frames_per_second = resolve_frames_per_second(args.frames_per_second, args.legacy_seconds_per_frame)
    if args.pano_frames_per_second and args.pano_legacy_seconds_per_frame:
        raise ValueError("Use pano frames-per-second or the legacy interval option, not both")
    if args.ordinary_frames_per_second and args.ordinary_legacy_seconds_per_frame:
        raise ValueError("Use ordinary frames-per-second or the legacy interval option, not both")
    if args.pano_legacy_seconds_per_frame:
        args.pano_frames_per_second = legacy_intervals_to_fps(args.pano_legacy_seconds_per_frame)
    if args.ordinary_legacy_seconds_per_frame:
        args.ordinary_frames_per_second = legacy_intervals_to_fps(args.ordinary_legacy_seconds_per_frame)

    backend = normalize_backend(args.backend)
    run_lichtfield = backend == COLMAP_BACKEND and args.run_lichtfield
    if args.check_env:
        checks = check_pipeline_dependencies(
            backend=backend,
            metashape_exe=args.metashape,
            colmap_exe=args.colmap,
            lichtfield_exe=args.lichtfield,
            run_lichtfield=run_lichtfield,
            run_lfs_densify=args.run_lfs_densify,
            lfs_densify_python=args.lfs_densify_python,
            lfs_densify_plugin=args.lfs_densify_plugin,
        )
        print(format_dependency_report(checks), flush=True)
        if args.strict:
            require_dependency_checks(checks)
        return
    if not args.output:
        raise ValueError("--output is required unless --check-env is used")
    output_dir = Path(args.output).resolve()
    if args.lichtfield_point_count < 0:
        raise ValueError("--lichtfield-point-count must be greater than or equal to 0")
    if args.lichtfield_bilateral_grid < 0:
        raise ValueError("--lichtfield-bilateral-grid must be greater than or equal to 0")
    if args.lfs_densify_max_points < 0:
        raise ValueError("--lfs-densify-max-points must be greater than or equal to 0")
    if args.lfs_densify_num_refs <= 0:
        raise ValueError("--lfs-densify-num-refs must be greater than 0")
    if args.colmap_max_image_size is not None and args.colmap_max_image_size <= 0:
        raise ValueError("--colmap-max-image-size must be greater than 0")
    if args.colmap_max_num_features is not None and args.colmap_max_num_features <= 0:
        raise ValueError("--colmap-max-num-features must be greater than 0")

    if args.manifest:
        manifest_path = Path(args.manifest).resolve()
        validate_manifest(load_manifest(manifest_path))
    elif (args.skip_extract or args.reexport_existing_project) and (output_dir / "work" / "xpano_manifest.json").exists():
        manifest_path = (output_dir / "work" / "xpano_manifest.json").resolve()
        validate_manifest(load_manifest(manifest_path))
    else:
        validate_run_args(args.frames_per_second, args.max_frames)
        manifest_path = None

    metashape_exe = args.metashape
    metashape_site_packages = None
    if backend == METASHAPE_BACKEND:
        metashape_exe = resolve_executable(args.metashape, "metashape.exe")
        if args.metashape_site_packages:
            metashape_site_packages = Path(args.metashape_site_packages).resolve()
            if not metashape_site_packages.is_dir():
                raise FileNotFoundError(
                    f"Metashape site-packages directory not found: {metashape_site_packages}"
                )
    colmap_exe = resolve_executable(args.colmap, "colmap") if backend == COLMAP_BACKEND else args.colmap
    lichtfield_exe = resolve_executable(args.lichtfield, "lichtfield-studio") if run_lichtfield else args.lichtfield
    lfs_densify_plugin = Path(args.lfs_densify_plugin).resolve() if args.lfs_densify_plugin else None
    if args.reexport_existing_project and backend != METASHAPE_BACKEND:
        raise ValueError("--reexport-existing-project is only supported with the Metashape backend")

    if manifest_path:
        job = MultiTrackJobConfig(
            panorama_videos=[],
            standard_photo_tracks=[],
            aerial_photo_tracks=[],
            output_dir=output_dir,
            frames_per_second=args.frames_per_second,
            max_frames=args.max_frames,
            metashape_exe=metashape_exe,
            metashape_site_packages=metashape_site_packages,
            overwrite_generated=False,
            manifest_path=manifest_path,
            backend=backend,
            metashape_keypoint_limit=args.metashape_keypoint_limit,
            metashape_tiepoint_limit=args.metashape_tiepoint_limit,
            metashape_alignment_mode=normalize_alignment_mode(args.metashape_alignment_mode),
            selected_component_key=args.component_key,
            colmap_exe=colmap_exe,
            colmap_density_preset=args.colmap_density_preset,
            colmap_use_gpu=args.colmap_use_gpu,
            colmap_matcher=args.colmap_matcher,
            colmap_max_image_size=args.colmap_max_image_size,
            colmap_max_num_features=args.colmap_max_num_features,
            up_axis=args.up_axis,
            run_lichtfield=run_lichtfield,
            lichtfield_exe=lichtfield_exe,
            lichtfield_point_count=args.lichtfield_point_count,
            lichtfield_bilateral_grid=args.lichtfield_bilateral_grid,
            run_lfs_densify=args.run_lfs_densify,
            lfs_densify_python=args.lfs_densify_python,
            lfs_densify_plugin=lfs_densify_plugin,
            lfs_densify_roma=args.lfs_densify_roma,
            lfs_densify_num_refs=args.lfs_densify_num_refs,
            lfs_densify_max_points=args.lfs_densify_max_points,
            reexport_existing_project=args.reexport_existing_project,
            existing_project_path=args.existing_project,
        )
    else:
        tracks = build_material_tracks(
            args.pano,
            args.ordinary_video,
            parse_track_args(args.standard_track),
            parse_track_args(args.aerial_track),
            args.ordinary_view,
        )
        track_extraction_settings = build_track_extraction_settings(
            args.pano,
            args.pano_start,
            args.pano_end,
            args.pano_frames_per_second,
            args.pano_max_frames,
            args.ordinary_video,
            args.ordinary_start,
            args.ordinary_end,
            args.ordinary_frames_per_second,
            args.ordinary_max_frames,
        )
        job = material_tracks_to_job_config(
            tracks=tracks,
            output_dir=output_dir,
            frames_per_second=args.frames_per_second,
            max_frames=args.max_frames,
            metashape_exe=metashape_exe,
            metashape_site_packages=metashape_site_packages,
            overwrite_generated=not args.keep_generated,
            backend=backend,
            metashape_keypoint_limit=args.metashape_keypoint_limit,
            metashape_tiepoint_limit=args.metashape_tiepoint_limit,
            metashape_alignment_mode=normalize_alignment_mode(args.metashape_alignment_mode),
            selected_component_key=args.component_key,
            colmap_exe=colmap_exe,
            colmap_density_preset=args.colmap_density_preset,
            colmap_use_gpu=args.colmap_use_gpu,
            colmap_matcher=args.colmap_matcher,
            colmap_max_image_size=args.colmap_max_image_size,
            colmap_max_num_features=args.colmap_max_num_features,
            up_axis=args.up_axis,
            run_lichtfield=run_lichtfield,
            lichtfield_exe=lichtfield_exe,
            lichtfield_point_count=args.lichtfield_point_count,
            lichtfield_bilateral_grid=args.lichtfield_bilateral_grid,
            run_lfs_densify=args.run_lfs_densify,
            lfs_densify_python=args.lfs_densify_python,
            lfs_densify_plugin=lfs_densify_plugin,
            lfs_densify_roma=args.lfs_densify_roma,
            lfs_densify_num_refs=args.lfs_densify_num_refs,
            lfs_densify_max_points=args.lfs_densify_max_points,
            reexport_existing_project=args.reexport_existing_project,
            existing_project_path=args.existing_project,
        )
        job.track_extraction_settings = track_extraction_settings

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
