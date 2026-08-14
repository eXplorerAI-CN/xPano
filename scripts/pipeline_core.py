import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from time import monotonic

from scripts.colmap_backend import colmap_config_for_density_preset, build_colmap_plan, find_sparse_model_path, publish_colmap_output, read_colmap_images, run_colmap_plan
from scripts.colmap_dense_merge import merge_dense_ply_into_colmap_points
from scripts.dependency_checks import resolve_executable
from scripts.lichtfield_cli import LichtfieldStudioConfig, run_lichtfield_command
from scripts.lichtfeld_densify import LichtfeldDensifyConfig, locate_densify_plugin, run_densify_command
from scripts.metashape_alignment_modes import ALIGNMENT_MODE_BACKBONE, normalize_alignment_mode
from scripts.metashape_runtime_env import build_metashape_process_env, metashape_runtime_cli_args
from scripts.pipeline_backends import COLMAP_BACKEND, METASHAPE_BACKEND, normalize_backend
from scripts.runtime_paths import app_root, first_existing, internal_root
from scripts.verify_xpano_output import verify_output
from scripts.xpano_tracks import build_manifest, load_manifest, validate_manifest

RUNTIME_IMPORTS = ("numpy", "cv2", "PIL", "piexif")
REEXPORT_TARGETS = (
    "images",
    "sparse",
    "colmap",
    "xpano_alignment_report.json",
    "work/export_image_cache.json",
)
REEXPORT_MARKER = Path("work") / "reexport_transaction.json"


class ProgressEtaEstimator:
    def __init__(self, clock=monotonic, min_elapsed=1.0):
        self.clock = clock
        self.min_elapsed = min_elapsed
        self.started_at = None
        self.started_current = 0
        self.total = 0

    def update(self, current, total):
        total = max(1, int(total or 1))
        current = max(0, min(int(current or 0), total))
        now = self.clock()

        if self.started_at is None or total != self.total or current < self.started_current:
            self.started_at = now
            self.started_current = current
            self.total = total
            return None

        if current >= total:
            return 0

        completed = current - self.started_current
        elapsed = now - self.started_at
        if completed <= 0 or elapsed < self.min_elapsed:
            return None

        remaining = (total - current) / (completed / elapsed)
        return max(0, int(round(remaining)))


def emit_pipeline_event(
    log_cb,
    phase,
    stage,
    percent,
    phase_percent,
    message,
    current=None,
    total=None,
    eta_seconds=None,
    aligned_cameras=None,
    total_cameras=None,
    alignment_rate=None,
):
    if not log_cb:
        return
    payload = {
        "phase": phase,
        "stage": stage,
        "percent": percent,
        "phasePercent": phase_percent,
        "message": message,
    }
    if current is not None:
        payload["current"] = int(current)
    if total is not None:
        payload["total"] = int(total)
    if eta_seconds is not None:
        payload["etaSeconds"] = int(eta_seconds)
    if aligned_cameras is not None:
        payload["alignedCameras"] = int(aligned_cameras)
    if total_cameras is not None:
        payload["totalCameras"] = int(total_cameras)
    if alignment_rate is not None:
        payload["alignmentRate"] = float(alignment_rate)
    log_cb("PIPELINE_EVENT:" + json.dumps(payload, ensure_ascii=True))


def collect_runtime_import_versions(import_module=importlib.import_module):
    result = {
        "ok": True,
        "python": sys.version,
        "executable": sys.executable,
        "modules": {},
    }
    for module_name in RUNTIME_IMPORTS:
        try:
            module = import_module(module_name)
            result["modules"][module_name] = {
                "ok": True,
                "version": getattr(module, "__version__", "n/a"),
                "file": getattr(module, "__file__", "n/a"),
            }
        except Exception as exc:
            result["ok"] = False
            result["modules"][module_name] = {
                "ok": False,
                "error": repr(exc),
            }
    return result


def write_runtime_import_report(output_path):
    report = collect_runtime_import_versions()
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


@dataclass
class JobConfig:
    input_video: Path
    output_dir: Path
    frames_per_second: float
    max_frames: int
    metashape_exe: str
    overwrite_generated: bool = True
    metashape_site_packages: Path = None


@dataclass
class MaterialTrack:
    track_type: str
    label: str
    paths: list
    camera_profile: str = None


@dataclass
class MultiTrackJobConfig:
    panorama_videos: list
    standard_photo_tracks: list
    aerial_photo_tracks: list
    output_dir: Path
    frames_per_second: float
    max_frames: int
    metashape_exe: str
    overwrite_generated: bool = True
    backend: str = METASHAPE_BACKEND
    manifest_path: Path = None
    metashape_keypoint_limit: int = 40000
    metashape_tiepoint_limit: int = 0
    metashape_alignment_mode: str = ALIGNMENT_MODE_BACKBONE
    selected_component_key: str = None
    colmap_exe: str = "colmap"
    colmap_density_preset: str = "stable"
    colmap_use_gpu: bool = False
    colmap_matcher: str = "sequential"
    colmap_max_image_size: int = None
    colmap_max_num_features: int = None
    up_axis: str = "y-up"
    run_lichtfield: bool = False
    lichtfield_exe: str = "lichtfield-studio"
    lichtfield_point_count: int = 0
    lichtfield_bilateral_grid: int = 0
    run_lfs_densify: bool = False
    lfs_densify_python: str = None
    lfs_densify_plugin: Path = None
    lfs_densify_roma: str = "fast"
    lfs_densify_num_refs: float = 0.75
    lfs_densify_max_points: int = 0
    ordinary_video_tracks: list = field(default_factory=list)
    track_extraction_settings: dict = field(default_factory=dict)
    track_camera_profiles: dict = field(default_factory=dict)
    reexport_existing_project: bool = False
    existing_project_path: Path = None
    metashape_site_packages: Path = None


def material_tracks_to_job_config(
    tracks,
    output_dir,
    frames_per_second,
    max_frames,
    metashape_exe,
    overwrite_generated=True,
    backend=METASHAPE_BACKEND,
    metashape_keypoint_limit=40000,
    metashape_tiepoint_limit=0,
    metashape_alignment_mode=ALIGNMENT_MODE_BACKBONE,
    selected_component_key=None,
    colmap_exe="colmap",
    colmap_density_preset="stable",
    colmap_use_gpu=False,
    colmap_matcher="sequential",
    colmap_max_image_size=None,
    colmap_max_num_features=None,
    up_axis="y-up",
    run_lichtfield=False,
    lichtfield_exe="lichtfield-studio",
    lichtfield_point_count=0,
    lichtfield_bilateral_grid=0,
    run_lfs_densify=False,
    lfs_densify_python=None,
    lfs_densify_plugin=None,
    lfs_densify_roma="fast",
    lfs_densify_num_refs=0.75,
    lfs_densify_max_points=0,
    reexport_existing_project=False,
    existing_project_path=None,
    metashape_site_packages=None,
):
    panorama_videos = []
    ordinary_video_tracks = []
    standard_photo_tracks = []
    aerial_photo_tracks = []
    track_camera_profiles = {}

    for track in tracks:
        paths = [Path(path).resolve() for path in track.paths]
        if not paths:
            raise ValueError(f"Material track {track.label or track.track_type} must contain at least one path")
        if track.track_type == "panorama_video":
            panorama_videos.extend(paths)
        elif track.track_type == "ordinary_video":
            ordinary_video_tracks.extend(paths)
            for path in paths:
                if track.camera_profile:
                    track_camera_profiles[str(path)] = track.camera_profile
        elif track.track_type == "standard_photos":
            standard_photo_tracks.append((track.label, paths))
        elif track.track_type == "aerial_photos":
            aerial_photo_tracks.append((track.label, paths))
        else:
            raise ValueError(f"Unsupported material track type: {track.track_type}")

    return MultiTrackJobConfig(
        panorama_videos=panorama_videos,
        ordinary_video_tracks=ordinary_video_tracks,
        standard_photo_tracks=standard_photo_tracks,
        aerial_photo_tracks=aerial_photo_tracks,
        output_dir=Path(output_dir).resolve(),
        frames_per_second=frames_per_second,
        max_frames=max_frames,
        metashape_exe=metashape_exe,
        metashape_site_packages=(
            Path(metashape_site_packages).resolve()
            if metashape_site_packages
            else None
        ),
        overwrite_generated=overwrite_generated,
        backend=backend,
        metashape_keypoint_limit=metashape_keypoint_limit,
        metashape_tiepoint_limit=metashape_tiepoint_limit,
        metashape_alignment_mode=normalize_alignment_mode(metashape_alignment_mode),
        selected_component_key=selected_component_key,
        colmap_exe=colmap_exe,
        colmap_density_preset=colmap_density_preset,
        colmap_use_gpu=colmap_use_gpu,
        colmap_matcher=colmap_matcher,
        colmap_max_image_size=colmap_max_image_size,
        colmap_max_num_features=colmap_max_num_features,
        up_axis=up_axis,
        run_lichtfield=run_lichtfield,
        lichtfield_exe=lichtfield_exe,
        lichtfield_point_count=lichtfield_point_count,
        lichtfield_bilateral_grid=lichtfield_bilateral_grid,
        run_lfs_densify=run_lfs_densify,
        lfs_densify_python=lfs_densify_python,
        lfs_densify_plugin=Path(lfs_densify_plugin).resolve() if lfs_densify_plugin else None,
        lfs_densify_roma=lfs_densify_roma,
        lfs_densify_num_refs=lfs_densify_num_refs,
        lfs_densify_max_points=lfs_densify_max_points,
        track_camera_profiles=track_camera_profiles,
        reexport_existing_project=reexport_existing_project,
        existing_project_path=Path(existing_project_path).resolve() if existing_project_path else None,
    )


def run_lfs_densification_stage(job, progress_cb, log_cb):
    log_cb("开始 LichtFeld densification 致密化")
    dense_ply = job.output_dir / "sparse" / "0" / "points3D_dense.ply"
    run_densify_command(
        LichtfeldDensifyConfig(
            python_exe=job.lfs_densify_python,
            plugin_dir=job.lfs_densify_plugin or locate_densify_plugin(),
            scene_root=job.output_dir,
            images_subdir="images",
            out_name=dense_ply.name,
            roma_setting=job.lfs_densify_roma,
            num_refs=job.lfs_densify_num_refs,
            max_points=job.lfs_densify_max_points,
        ),
        progress_cb=lambda value: progress_cb(min(99, value)),
        log_cb=log_cb,
    )
    merge_result = merge_dense_ply_into_colmap_points(
        sparse_model_dir=job.output_dir / "sparse" / "0",
        dense_ply_path=dense_ply,
        replace_points_bin=True,
    )
    log_cb(
        "LichtFeld dense points merged into COLMAP: "
        f"{merge_result['original_points']} + {merge_result['dense_points']} = {merge_result['merged_points']}"
    )


def manifest_expected_camera_count(manifest):
    total = 0
    for track in manifest.get("tracks", []):
        track_type = track.get("track_type")
        if track_type == "panorama_video":
            total += 2 * len(track.get("frames", []))
        else:
            total += len(track.get("photos", []))
    return total


def report_alignment_rate(log_cb, aligned, total, percent=95):
    total = max(0, int(total or 0))
    aligned = max(0, int(aligned or 0))
    rate = (aligned / total * 100.0) if total else 0.0
    emit_pipeline_event(
        log_cb,
        phase="align",
        stage="align.rate",
        percent=percent,
        phase_percent=100 if total else 0,
        message=f"Alignment rate {aligned}/{total} ({rate:.1f}%)",
        aligned_cameras=aligned,
        total_cameras=total,
        alignment_rate=rate,
    )


def metashape_process_env(site_packages=None):
    site_packages = site_packages if site_packages is not None else os.environ.get("XPANO_METASHAPE_SITE_PACKAGES", "").strip()
    return build_metashape_process_env(
        internal_root(),
        site_packages,
    )


def job_metashape_site_packages(job):
    explicit = getattr(job, "metashape_site_packages", None)
    if explicit:
        return str(Path(explicit).resolve())
    return os.environ.get("XPANO_METASHAPE_SITE_PACKAGES", "").strip()


def locate_metashape():
    candidates = []
    explicit = os.environ.get("XPANO_METASHAPE")
    if explicit:
        explicit = explicit.strip()
        if len(explicit) >= 2 and explicit.startswith('"') and explicit.endswith('"'):
            explicit = explicit[1:-1].strip()
        return explicit
    env_path = os.environ.get("Path", "")
    for item in env_path.split(os.pathsep):
        item = item.strip()
        if not item:
            continue
        exe = Path(item) / "metashape.exe"
        if exe.exists():
            candidates.append(str(exe))
    for item in [
        r"C:\Program Files\Agisoft\Metashape Pro\metashape.exe",
        r"C:\Program Files\Agisoft\Metashape\metashape.exe",
    ]:
        exe = Path(item)
        if exe.exists():
            candidates.append(str(exe))
    if candidates:
        return candidates[0]
    return "metashape.exe"


def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    return path


def generated_output_paths(output_dir: Path):
    return [
        output_dir / "work",
        output_dir / "images",
        output_dir / "sparse",
        output_dir / "colmap",
        output_dir / "lichtfield",
    ]


def _path_is_within(path: Path, parent: Path):
    path = Path(path).resolve()
    parent = Path(parent).resolve()
    try:
        return path == parent or path.is_relative_to(parent)
    except AttributeError:
        return str(path).startswith(str(parent))


def _remove_path_preserving(path: Path, preserve_paths):
    path = Path(path)
    if not path.exists():
        return
    preserve_paths = [Path(item).resolve() for item in (preserve_paths or [])]
    if any(path.resolve() == preserve for preserve in preserve_paths):
        return
    if path.is_dir():
        keep_children = [preserve for preserve in preserve_paths if _path_is_within(preserve, path)]
        if not keep_children:
            shutil.rmtree(path)
            return
        for child in list(path.iterdir()):
            if any(_path_is_within(preserve, child) for preserve in keep_children):
                _remove_path_preserving(child, preserve_paths)
            elif child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    else:
        path.unlink()


def clear_generated_outputs(output_dir: Path, log_cb, preserve_paths=None):
    for path in generated_output_paths(output_dir):
        if path.exists():
            log_cb(f"清理旧输出: {path}")
            _remove_path_preserving(path, preserve_paths)


def write_run_summary(job: JobConfig):
    backend = normalize_backend(getattr(job, "backend", METASHAPE_BACKEND))
    if backend == COLMAP_BACKEND:
        image_dir = job.output_dir / "images"
        sparse_dir = job.output_dir / "sparse" / "0"
        if not sparse_dir.exists():
            sparse_dir = find_sparse_model_path(job.output_dir / "colmap" / "sparse")
        export_verification = {
            "backend": backend,
            "image_dir": str(image_dir),
            "sparse_model_path": str(sparse_dir),
        }
    else:
        image_dir = job.output_dir / "images"
        sparse_dir = job.output_dir / "sparse" / "0"
        export_verification = verify_output(job.output_dir, expect_single_sparse=True)
    frames_dir = job.output_dir / "work" / "frames"
    manifest_path = getattr(job, "manifest_path", None) or job.output_dir / "work" / "xpano_manifest.json"
    manifest_path = Path(manifest_path)
    manifest = load_manifest(manifest_path) if manifest_path.exists() else {"tracks": []}
    input_videos = [str(path) for path in getattr(job, "panorama_videos", [])]
    if not input_videos and hasattr(job, "input_video"):
        input_videos = [str(job.input_video)]
    summary = {
        "workflow": "xpano_multi_track",
        "input_video": input_videos[0] if len(input_videos) == 1 else "",
        "input_videos": input_videos,
        "output_dir": str(job.output_dir),
        "backend": backend,
        "metashape_alignment_mode": getattr(job, "metashape_alignment_mode", ALIGNMENT_MODE_BACKBONE),
        "frames_per_second": job.frames_per_second,
        "max_frames": job.max_frames,
        "track_count": len(manifest.get("tracks", [])),
        "tracks": [
            {
                "track_id": track.get("track_id"),
                "track_type": track.get("track_type"),
            "device_label": track.get("device_label"),
            "frame_count": len(track.get("frames", [])),
            "photo_count": len(track.get("photos", [])),
            "photo_sensor_count": len(track.get("photo_sensors", [])),
            "camera_profile": track.get("camera_profile"),
        }
            for track in manifest.get("tracks", [])
        ],
        "manifest": str(manifest_path),
        "export_verification": export_verification,
        "frames_jpg": len(list(frames_dir.rglob("*.jpg"))) if frames_dir.exists() else 0,
        "cubemap_images": len(list(image_dir.glob("*.jpg"))) if image_dir.exists() and backend == METASHAPE_BACKEND else 0,
        "colmap_input_images": len(list(image_dir.rglob("*.jpg"))) if image_dir.exists() and backend == COLMAP_BACKEND else 0,
        "colmap_bins": {
            name: (sparse_dir / name).stat().st_size if (sparse_dir / name).exists() else 0
            for name in ["cameras.bin", "images.bin", "points3D.bin"]
        },
        "lfs_densification": {
            "enabled": bool(getattr(job, "run_lfs_densify", False)),
            "output": str(sparse_dir / "points3D_dense.ply"),
            "exists": (sparse_dir / "points3D_dense.ply").exists(),
            "roma": getattr(job, "lfs_densify_roma", ""),
            "max_points": getattr(job, "lfs_densify_max_points", 0),
        },
        "project": str(job.output_dir / "work" / "xpano.psx"),
        "alignment_summary": str(job.output_dir / "xpano_alignment_summary.txt"),
        "alignment_report": str(job.output_dir / "xpano_alignment_report.json"),
    }
    (job.output_dir / "xpano_run_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def run_metashape_reexport_from_existing_project(
    job: MultiTrackJobConfig,
    project_path: Path,
    progress_cb,
    log_cb,
    *,
    export_dir=None,
    reuse_images_dir=None,
    image_cache_path=None,
    image_cache_output=None,
):
    if normalize_backend(getattr(job, "backend", METASHAPE_BACKEND)) != METASHAPE_BACKEND:
        raise ValueError("Existing Metashape project re-export is only available with the Metashape backend")
    project_path = Path(project_path)
    if not project_path.exists():
        raise FileNotFoundError(f"Existing Metashape project not found: {project_path}")
    script = first_existing([
        internal_root() / "scripts" / "reexport_colmap_from_project.py",
        Path(__file__).parent / "scripts" / "reexport_colmap_from_project.py",
    ])
    if not script:
        raise FileNotFoundError("reexport_colmap_from_project.py")

    emit_pipeline_event(
        log_cb,
        phase="export",
        stage="export.reuse_project",
        percent=86,
        phase_percent=0,
        message="Re-exporting existing Metashape project",
    )
    progress_cb(86)
    destination = Path(export_dir) if export_dir else Path(job.output_dir)
    site_packages = job_metashape_site_packages(job)
    cmd = [
        job.metashape_exe,
        "-r",
        str(script),
        "--project",
        str(project_path),
        "--export-dir",
        str(destination),
    ]
    cmd.extend(metashape_runtime_cli_args(site_packages))
    if getattr(job, "selected_component_key", None):
        cmd.extend(["--component-key", str(job.selected_component_key)])
    if reuse_images_dir:
        cmd.extend(["--reuse-images-dir", str(reuse_images_dir)])
    if image_cache_path:
        cmd.extend(["--image-cache-path", str(image_cache_path)])
    if image_cache_output:
        cmd.extend(["--image-cache-output", str(image_cache_output)])
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=metashape_process_env(site_packages),
    )
    for line in proc.stdout:
        line = line.rstrip()
        match = re.search(r"\[(\d+)/(\d+)\]", line)
        if match:
            cur, total = int(match.group(1)), int(match.group(2))
            total = max(total, 1)
            percent = 86 + int(13 * cur / total)
            progress_cb(max(86, min(99, percent)))
            emit_pipeline_event(
                log_cb,
                phase="export",
                stage="export.images",
                percent=max(86, min(99, percent)),
                phase_percent=max(0, min(100, int(100 * cur / total))),
                message=f"正在导出 {cur}/{total} 相机",
                current=cur,
                total=total,
            )
        if line:
            log_cb(line)
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"Metashape re-export failed with return code {rc}")
    if export_dir is not None:
        return
    if job.run_lfs_densify:
        run_lfs_densification_stage(job, progress_cb, log_cb)
    emit_pipeline_event(
        log_cb,
        phase="export",
        stage="output.validate",
        percent=99,
        phase_percent=99,
        message="Validating re-exported images and COLMAP model",
    )
    write_run_summary(job)
    progress_cb(100)
    emit_pipeline_event(
        log_cb,
        phase="complete",
        stage="export.done",
        percent=100,
        phase_percent=100,
        message="Re-export complete",
    )
    log_cb("完成")


def _remove_reexport_path(path):
    path = Path(path)
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _write_json_atomic(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _transaction_directory(output_dir, value, prefix):
    output_dir = Path(output_dir).resolve()
    candidate = Path(value).resolve()
    if candidate.parent != output_dir or not candidate.name.startswith(prefix):
        raise RuntimeError(f"Unsafe re-export transaction path: {candidate}")
    return candidate


def recover_reexport_transaction(output_dir, log_cb):
    output_dir = Path(output_dir).resolve()
    marker_path = output_dir / REEXPORT_MARKER
    if not marker_path.is_file():
        return False
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8-sig"))
        stage_dir = _transaction_directory(output_dir, marker["stageDir"], ".xpano-reexport-stage-")
        backup_dir = _transaction_directory(output_dir, marker["backupDir"], ".xpano-reexport-backup-")
        state = marker.get("state")
        original_targets = set(marker.get("originalTargets", []))
    except Exception as exc:
        raise RuntimeError(f"Invalid re-export transaction marker: {marker_path}: {exc}") from exc

    if state == "committed":
        _remove_reexport_path(stage_dir)
        _remove_reexport_path(backup_dir)
        marker_path.unlink(missing_ok=True)
        log_cb("Cleaned committed re-export transaction")
        return True

    for name in REEXPORT_TARGETS:
        live = output_dir / name
        backup = backup_dir / name
        if backup.exists():
            _remove_reexport_path(live)
            live.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(backup), str(live))
        elif name not in original_targets:
            _remove_reexport_path(live)
    _remove_reexport_path(stage_dir)
    _remove_reexport_path(backup_dir)
    marker_path.unlink(missing_ok=True)
    log_cb("Recovered interrupted re-export transaction")
    return True


def run_reexport_transaction(job, project_path, progress_cb, log_cb):
    output_dir = Path(job.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    recover_reexport_transaction(output_dir, log_cb)
    stage_dir = Path(tempfile.mkdtemp(prefix=".xpano-reexport-stage-", dir=output_dir))
    backup_dir = Path(tempfile.mkdtemp(prefix=".xpano-reexport-backup-", dir=output_dir))
    marker_path = output_dir / REEXPORT_MARKER
    original_targets = [name for name in REEXPORT_TARGETS if (output_dir / name).exists()]
    marker = {
        "state": "prepared",
        "stageDir": str(stage_dir),
        "backupDir": str(backup_dir),
        "originalTargets": original_targets,
    }
    _write_json_atomic(marker_path, marker)
    try:
        run_metashape_reexport_from_existing_project(
            job,
            project_path,
            progress_cb,
            log_cb,
            export_dir=stage_dir,
            reuse_images_dir=output_dir / "images",
            image_cache_path=output_dir / "work" / "export_image_cache.json",
            image_cache_output=stage_dir / "work" / "export_image_cache.json",
        )
        if job.run_lfs_densify:
            run_lfs_densification_stage(replace(job, output_dir=stage_dir), progress_cb, log_cb)
        verify_output(stage_dir, expect_single_sparse=True)

        marker["state"] = "publishing"
        _write_json_atomic(marker_path, marker)
        for name in REEXPORT_TARGETS:
            live = output_dir / name
            staged = stage_dir / name
            backup = backup_dir / name
            if live.exists():
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(live), str(backup))
            if staged.exists():
                live.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(staged), str(live))

        emit_pipeline_event(
            log_cb,
            phase="export",
            stage="output.validate",
            percent=99,
            phase_percent=99,
            message="Validating re-exported images and COLMAP model",
        )
        verify_output(output_dir, expect_single_sparse=True)
        write_run_summary(job)
        marker["state"] = "committed"
        _write_json_atomic(marker_path, marker)
        progress_cb(100)
        emit_pipeline_event(
            log_cb,
            phase="complete",
            stage="export.done",
            percent=100,
            phase_percent=100,
            message="Re-export complete",
        )
        log_cb("完成")
        recover_reexport_transaction(output_dir, log_cb)
    except Exception as exc:
        try:
            recover_reexport_transaction(output_dir, log_cb)
        except Exception as recovery_exc:
            raise RuntimeError(f"Re-export failed and rollback also failed: {recovery_exc}") from exc
        raise


def run_multi_track_pipeline(job: MultiTrackJobConfig, progress_cb, preview_cb, log_cb):
    backend = normalize_backend(job.backend)
    extract_eta = ProgressEtaEstimator()
    export_eta = ProgressEtaEstimator()

    def report_extract_progress(cur, total):
        total = max(total, 1)
        overall = 5 + int(25 * cur / total)
        phase_percent = max(0, min(100, int(100 * cur / total)))
        progress_cb(overall)
        emit_pipeline_event(
            log_cb,
            phase="extract",
            stage="extract.frames",
            percent=overall,
            phase_percent=phase_percent,
            message=f"\u5df2\u62bd\u53d6 {cur}/{total} \u5e27",
            current=cur,
            total=total,
            eta_seconds=extract_eta.update(cur, total),
        )

    def report_export_progress(cur, total):
        total = max(total, 1)
        overall = 97 + int(2 * cur / total)
        phase_percent = max(0, min(100, int(100 * cur / total)))
        progress_cb(overall)
        emit_pipeline_event(
            log_cb,
            phase="export",
            stage="export.images",
            percent=overall,
            phase_percent=phase_percent,
            message=f"\u6b63\u5728\u5bfc\u51fa {cur}/{total} \u76f8\u673a",
            current=cur,
            total=total,
            eta_seconds=export_eta.update(cur, total),
        )

    work_dir = ensure_dir(job.output_dir / "work")
    project_path = Path(job.existing_project_path or (work_dir / "xpano.psx"))

    if getattr(job, "reexport_existing_project", False):
        if not job.manifest_path:
            default_manifest_path = job.output_dir / "work" / "xpano_manifest.json"
            if default_manifest_path.exists():
                job.manifest_path = default_manifest_path
        if job.manifest_path:
            validate_manifest(load_manifest(Path(job.manifest_path).resolve()))
        log_cb("复用现有 Metashape 工程并重新导出 COLMAP")
        run_reexport_transaction(job, project_path, progress_cb, log_cb)
        return

    if job.overwrite_generated:
        preserve_paths = [job.manifest_path] if job.manifest_path else None
        clear_generated_outputs(job.output_dir, log_cb, preserve_paths=preserve_paths)

    log_cb("开始抽帧")
    if job.manifest_path:
        manifest_path = Path(job.manifest_path).resolve()
        validate_manifest(load_manifest(manifest_path))
    else:
        _, manifest_path = build_manifest(
            output_dir=job.output_dir,
            panorama_videos=job.panorama_videos,
            ordinary_videos=getattr(job, "ordinary_video_tracks", []),
            standard_photo_tracks=job.standard_photo_tracks,
            aerial_photo_tracks=job.aerial_photo_tracks,
            frames_per_second=job.frames_per_second,
            max_frames=job.max_frames,
            track_extraction_settings=getattr(job, "track_extraction_settings", {}),
            track_camera_profiles=getattr(job, "track_camera_profiles", {}),
            preview_cb=preview_cb,
            progress_cb=report_extract_progress,
            log_cb=log_cb,
        )
        job.manifest_path = manifest_path

    if backend == COLMAP_BACKEND:
        log_cb("开始 COLMAP 自动处理")
        progress_cb(35)
        emit_pipeline_event(
            log_cb,
            phase="align",
            stage="input.validate",
            percent=35,
            phase_percent=0,
            message="正在校验输入与相机模型",
        )
        job.colmap_exe = resolve_executable(job.colmap_exe, "colmap")
        colmap_config = colmap_config_for_density_preset(job.colmap_density_preset, colmap_exe=job.colmap_exe)
        colmap_overrides = {
            "use_gpu": bool(job.colmap_use_gpu),
            "matcher": getattr(job, "colmap_matcher", "sequential"),
        }
        if getattr(job, "colmap_max_image_size", None) is not None:
            colmap_overrides["max_image_size"] = int(job.colmap_max_image_size)
        if getattr(job, "colmap_max_num_features", None) is not None:
            colmap_overrides["max_num_features"] = int(job.colmap_max_num_features)
        colmap_config = replace(colmap_config, **colmap_overrides)
        manifest = load_manifest(manifest_path)
        emit_pipeline_event(
            log_cb,
            phase="align",
            stage="colmap.images.prepare",
            percent=37,
            phase_percent=3,
            message="正在准备双鱼眼图像与传感器组",
        )
        plan = build_colmap_plan(
            manifest,
            output_dir=job.output_dir / "colmap",
            config=colmap_config,
        )

        def report_colmap_stage(name, index, total):
            stage = {
                "feature_extractor": "colmap.features.extract",
                "sequential_matcher": "colmap.features.match",
                "exhaustive_matcher": "colmap.features.match",
                "mapper": "colmap.mapper",
            }.get(name, f"colmap.{name}")
            label = {
                "feature_extractor": "正在提取 SIFT 特征",
                "sequential_matcher": "正在匹配图像特征",
                "exhaustive_matcher": "正在匹配图像特征",
                "mapper": "正在执行增量重建",
            }.get(name, f"正在执行 COLMAP {name}")
            percent = 38 + int(50 * max(0, index - 1) / max(1, total))
            emit_pipeline_event(
                log_cb,
                phase="align",
                stage=stage,
                percent=percent,
                phase_percent=max(0, min(100, int((percent - 35) / 60 * 100))),
                message=label,
            )

        result = run_colmap_plan(
            plan,
            progress_cb=lambda value: progress_cb(min(95, value)),
            log_cb=log_cb,
            stage_cb=report_colmap_stage,
        )
        native_sparse_model_path = None
        if isinstance(result, dict) and result.get("sparse_model_path"):
            native_sparse_model_path = Path(result["sparse_model_path"])
        else:
            try:
                native_sparse_model_path = Path(find_sparse_model_path(plan.sparse_dir))
            except Exception:
                pass
        emit_pipeline_event(
            log_cb,
            phase="align",
            stage="colmap.model.select",
            percent=91,
            phase_percent=94,
            message="正在选择并检查最佳模型",
        )
        emit_pipeline_event(
            log_cb,
            phase="export",
            stage="export.images",
            percent=95,
            phase_percent=0,
            message="正在导出训练图像",
        )
        final_result = publish_colmap_output(plan, job.output_dir)
        emit_pipeline_event(
            log_cb,
            phase="export",
            stage="export.colmap",
            percent=98,
            phase_percent=60,
            message="正在发布标准 COLMAP 目录",
        )
        sparse_model_path = Path(final_result.get("sparse_model_path") or native_sparse_model_path or find_sparse_model_path(plan.sparse_dir))
        try:
            # WARN: Published cubemap faces are export artifacts, not additional aligned input cameras.
            alignment_model_path = native_sparse_model_path or sparse_model_path
            report_alignment_rate(log_cb, len(read_colmap_images(alignment_model_path)), manifest_expected_camera_count(manifest), percent=95)
        except Exception as exc:
            log_cb(f"WARN: alignment rate unavailable: {exc}")
        if job.run_lichtfield:
            log_cb("开始 LICHT Field Studio 后处理")
            progress_cb(95)
            run_lichtfield_command(
                LichtfieldStudioConfig(
                    executable=job.lichtfield_exe,
                    input_colmap=sparse_model_path,
                    image_dir=Path(final_result.get("image_dir", plan.image_dir)),
                    output_dir=job.output_dir / "lichtfield",
                    point_count=job.lichtfield_point_count,
                    bilateral_grid=job.lichtfield_bilateral_grid,
                ),
                progress_cb=lambda value: progress_cb(95 + int(4 * value / 100)),
                log_cb=log_cb,
            )
        if job.run_lfs_densify:
            run_lfs_densification_stage(job, progress_cb, log_cb)
        write_run_summary(job)
        emit_pipeline_event(
            log_cb,
            phase="export",
            stage="output.validate",
            percent=100,
            phase_percent=100,
            message="正在验证输出完整性",
        )
        progress_cb(100)
        log_cb("完成")
        return

    log_cb("开始 Metashape 自动处理")
    script = first_existing([
        internal_root() / "scripts" / "metashape_pipeline.py",
        Path(__file__).parent / "scripts" / "metashape_pipeline.py",
    ])
    if not script:
        raise FileNotFoundError("metashape_pipeline.py")
    site_packages = job_metashape_site_packages(job)
    cmd = [
        job.metashape_exe,
        "-r",
        str(script),
        "--manifest",
        str(manifest_path),
        "--project",
        str(project_path),
        "--export-dir",
        str(job.output_dir),
        "--max-frames",
        str(job.max_frames),
        "--keypoint-limit",
        str(getattr(job, "metashape_keypoint_limit", 40000)),
        "--tiepoint-limit",
        str(getattr(job, "metashape_tiepoint_limit", 0)),
        "--alignment-mode",
        normalize_alignment_mode(getattr(job, "metashape_alignment_mode", ALIGNMENT_MODE_BACKBONE)),
        "--up-axis",
        str(getattr(job, "up_axis", "+Y")),
    ]
    cmd.extend(metashape_runtime_cli_args(site_packages))
    if getattr(job, "selected_component_key", None):
        cmd.extend(["--component-key", str(job.selected_component_key)])
    progress_cb(35)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=metashape_process_env(site_packages),
    )
    for line in proc.stdout:
        line = line.rstrip()
        if line.startswith("PROGRESS:"):
            try:
                value = int(line.split(":", 1)[1].strip())
                progress_cb(max(35, min(95, value)))
            except Exception:
                pass
        else:
            match = re.search(r"处理中 \[(\d+)/(\d+)\]", line)
            if not match:
                match = re.search(r"\[(\d+)/(\d+)\]", line)
            if match:
                cur, total = int(match.group(1)), int(match.group(2))
                report_export_progress(cur, total)
            if line:
                log_cb(line)
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"Metashape 处理失败，返回码 {rc}")
    if job.run_lfs_densify:
        run_lfs_densification_stage(job, progress_cb, log_cb)
    write_run_summary(job)
    progress_cb(100)
    log_cb("完成")


def run_metashape_pipeline(job: JobConfig, progress_cb, preview_cb, log_cb):
    multi_job = MultiTrackJobConfig(
        panorama_videos=[job.input_video],
        ordinary_video_tracks=[],
        standard_photo_tracks=[],
        aerial_photo_tracks=[],
        output_dir=job.output_dir,
        frames_per_second=job.frames_per_second,
        max_frames=job.max_frames,
        metashape_exe=job.metashape_exe,
        overwrite_generated=job.overwrite_generated,
        metashape_site_packages=job.metashape_site_packages,
        backend=METASHAPE_BACKEND,
        metashape_alignment_mode=ALIGNMENT_MODE_BACKBONE,
    )
    run_multi_track_pipeline(multi_job, progress_cb, preview_cb, log_cb)
