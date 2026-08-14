import argparse
import hashlib
import json
import struct
from datetime import datetime, timezone
from pathlib import Path


def _first_existing(paths):
    return next((path for path in paths if path.is_file()), None)


def _manifest_path(project_root):
    path = _first_existing([
        project_root / "work" / "xpano_manifest.json",
        project_root / "xpano_manifest.json",
    ])
    if not path:
        raise FileNotFoundError("xpano_manifest.json was not found in the project")
    return path


def _model_dir(project_root):
    candidates = [
        project_root / "sparse" / "0",
        project_root / "colmap" / "sparse" / "0",
        project_root / "workspace" / "sparse" / "0",
        project_root / "workspace" / "colmap" / "sparse" / "0",
    ]
    for candidate in candidates:
        if all((candidate / name).is_file() for name in ("cameras.bin", "images.bin", "points3D.bin")):
            return candidate
    raise FileNotFoundError("a complete COLMAP sparse/0 model was not found")


def _u64_header(path):
    with path.open("rb") as stream:
        header = stream.read(8)
    if len(header) != 8:
        raise ValueError(f"invalid COLMAP binary header: {path.name}")
    return struct.unpack("<Q", header)[0]


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _frame_count(manifest):
    return sum(
        len(track.get("frames", []))
        for track in manifest.get("tracks", [])
        if track.get("track_type") == "panorama_video"
    )


def _read_backend(project_root, explicit_backend):
    if explicit_backend:
        return explicit_backend
    summary_path = project_root / "xpano_run_summary.json"
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        backend = str(summary.get("backend") or "").strip().lower()
        if backend:
            return backend
    return "unknown"


def capture_baseline(project_root, case_id, output_path, expected_frame_count=20, backend=None):
    project_root = Path(project_root).resolve()
    output_path = Path(output_path).resolve()
    manifest_path = _manifest_path(project_root)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    frame_count = _frame_count(manifest)
    if frame_count != expected_frame_count:
        raise ValueError(f"expected {expected_frame_count} panorama frames, found {frame_count}")

    model_dir = _model_dir(project_root)
    camera_path = model_dir / "cameras.bin"
    image_path = model_dir / "images.bin"
    point_path = model_dir / "points3D.bin"
    model_relative = model_dir.relative_to(project_root).as_posix()

    baseline = {
        "schemaVersion": 1,
        "caseId": str(case_id),
        "capturedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "backend": _read_backend(project_root, backend),
        "frameCount": frame_count,
        "model": {
            "relativePath": model_relative,
            "cameraCount": _u64_header(camera_path),
            "imageCount": _u64_header(image_path),
            "pointCount": _u64_header(point_path),
        },
        "artifacts": {
            "xpano_manifest.json": _sha256(manifest_path),
            "cameras.bin": _sha256(camera_path),
            "images.bin": _sha256(image_path),
            "points3D.bin": _sha256(point_path),
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temp_path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    json.loads(temp_path.read_text(encoding="utf-8"))
    temp_path.replace(output_path)
    return baseline


def main():
    parser = argparse.ArgumentParser(description="Capture a portable xPano 20-frame regression baseline")
    parser.add_argument("--project", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--expected-frame-count", type=int, default=20)
    parser.add_argument("--backend", choices=["metashape", "colmap"])
    args = parser.parse_args()
    baseline = capture_baseline(
        args.project,
        args.case_id,
        args.output,
        expected_frame_count=args.expected_frame_count,
        backend=args.backend,
    )
    print(json.dumps(baseline, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
