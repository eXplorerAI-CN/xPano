import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.verify_xpano_output import verify_output


MIXED_BACKBONE_STAGES = (
    "metashape.pano.match",
    "metashape.pano.align",
    "metashape.pano.release",
    "metashape.pano.optimize",
    "metashape.frame.import",
    "metashape.frame.match",
    "metashape.frame.align",
    "metashape.all.optimize",
    "output.validate",
)
NATIVE_FAILURE_MARKERS = ("assertion", "traceback", "exception:")
SUMMARY_INTEGER_FIELDS = (
    "cameras",
    "aligned",
    "panorama_cameras",
    "panorama_aligned",
    "frame_cameras",
    "frame_aligned",
    "sensors",
)


def read_pipeline_stages(log_path):
    stages = []
    for line in Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("PIPELINE_EVENT:"):
            continue
        try:
            event = json.loads(line[len("PIPELINE_EVENT:"):])
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid PIPELINE_EVENT JSON in {log_path}: {exc}") from exc
        stage = event.get("stage")
        if isinstance(stage, str):
            stages.append(stage)
    return stages


def verify_native_log(log_path):
    log_path = Path(log_path)
    if not log_path.is_file():
        raise RuntimeError(f"Missing Metashape stdout log: {log_path}")
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line_number, line in enumerate(lines, start=1):
        normalized = line.casefold()
        marker = next((candidate for candidate in NATIVE_FAILURE_MARKERS if candidate in normalized), None)
        if marker:
            raise RuntimeError(f"Metashape native failure marker {marker!r} at {log_path}:{line_number}: {line}")

    match_calls = sum(line.lstrip().startswith("MatchPhotos:") for line in lines)
    if match_calls != 2:
        raise RuntimeError(f"Expected exactly two native MatchPhotos calls, got {match_calls}: {log_path}")

    stages = read_pipeline_stages(log_path)
    for match_stage in ("metashape.pano.match", "metashape.frame.match"):
        if stages.count(match_stage) != 1:
            raise RuntimeError(
                f"Expected exactly one {match_stage} stage, got {stages.count(match_stage)}: {log_path}"
            )
    cursor = -1
    for required_stage in MIXED_BACKBONE_STAGES:
        try:
            cursor = stages.index(required_stage, cursor + 1)
        except ValueError as exc:
            raise RuntimeError(
                f"Missing or invalid Backbone stage order at {required_stage!r}: {log_path}"
            ) from exc
    return {"match_calls": match_calls, "stages": list(MIXED_BACKBONE_STAGES)}


def read_alignment_summary(summary_path):
    summary_path = Path(summary_path)
    if not summary_path.is_file():
        raise RuntimeError(f"Missing xPano alignment summary: {summary_path}")
    values = {}
    for line in summary_path.read_text(encoding="utf-8", errors="replace").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip()
    for field in SUMMARY_INTEGER_FIELDS:
        if field not in values:
            raise RuntimeError(f"Missing {field!r} in xPano alignment summary: {summary_path}")
        try:
            values[field] = int(values[field])
        except ValueError as exc:
            raise RuntimeError(f"Invalid {field!r} in xPano alignment summary: {summary_path}") from exc
    if values.get("alignment_mode") != "backbone":
        raise RuntimeError(
            "Expected Backbone alignment summary, "
            f"got {values.get('alignment_mode')!r}: {summary_path}"
        )
    return values


def assert_expected(name, actual, expected):
    if expected is not None and actual != expected:
        raise RuntimeError(f"{name}: expected {expected}, got {actual}")


def normalized_absolute_path(path):
    return os.path.normcase(os.path.abspath(os.path.normpath(str(path))))


def require_expected_counts(expected_counts):
    missing = [name for name, value in expected_counts.items() if value is None]
    if missing:
        raise RuntimeError(f"Authoritative acceptance expected counts are required: {', '.join(missing)}")


def verify_backbone_acceptance(
    log_path,
    project_path,
    export_dir,
    expect_cameras=None,
    expect_aligned=None,
    expect_panorama_cameras=None,
    expect_panorama_aligned=None,
    expect_frame_cameras=None,
    expect_frame_aligned=None,
    expect_sensors=None,
    expect_cube_images=None,
    expect_frame_images=None,
    expect_colmap_images=None,
    expect_colmap_cameras=None,
    expect_colmap_points=None,
):
    expected_counts = {
        "cameras": expect_cameras,
        "aligned": expect_aligned,
        "panorama_cameras": expect_panorama_cameras,
        "panorama_aligned": expect_panorama_aligned,
        "frame_cameras": expect_frame_cameras,
        "frame_aligned": expect_frame_aligned,
        "sensors": expect_sensors,
        "cube_images": expect_cube_images,
        "frame_images": expect_frame_images,
        "colmap_images": expect_colmap_images,
        "colmap_cameras": expect_colmap_cameras,
        "colmap_points": expect_colmap_points,
    }
    require_expected_counts(expected_counts)
    project_path = Path(project_path)
    if not project_path.is_file() or project_path.stat().st_size <= 0:
        raise RuntimeError(f"Missing or empty Metashape project: {project_path}")

    native_log = verify_native_log(log_path)
    summary = read_alignment_summary(Path(export_dir) / "xpano_alignment_summary.txt")
    summary_project = summary.get("project")
    if not summary_project or normalized_absolute_path(summary_project) != normalized_absolute_path(project_path):
        raise RuntimeError(
            "Alignment summary project does not match supplied PSX: "
            f"summary={summary_project!r}, supplied={str(project_path)!r}"
        )
    if summary["panorama_cameras"] + summary["frame_cameras"] != summary["cameras"]:
        raise RuntimeError("Alignment summary per-type camera counts do not equal the aggregate camera count")
    if summary["panorama_aligned"] + summary["frame_aligned"] != summary["aligned"]:
        raise RuntimeError("Alignment summary per-type aligned counts do not equal the aggregate aligned count")
    assert_expected("cameras", summary["cameras"], expect_cameras)
    assert_expected("aligned", summary["aligned"], expect_aligned)
    assert_expected("panorama_cameras", summary["panorama_cameras"], expect_panorama_cameras)
    assert_expected("panorama_aligned", summary["panorama_aligned"], expect_panorama_aligned)
    assert_expected("frame_cameras", summary["frame_cameras"], expect_frame_cameras)
    assert_expected("frame_aligned", summary["frame_aligned"], expect_frame_aligned)
    assert_expected("sensors", summary["sensors"], expect_sensors)
    output = verify_output(
        export_dir,
        expect_cube_images=expect_cube_images,
        expect_frame_images=expect_frame_images,
        expect_colmap_images=expect_colmap_images,
        expect_colmap_cameras=expect_colmap_cameras,
        expect_colmap_points=expect_colmap_points,
        expect_single_sparse=True,
    )
    return {
        "log_path": str(Path(log_path)),
        "project_path": str(project_path),
        "match_calls": native_log["match_calls"],
        "stages": native_log["stages"],
        "alignment_summary": summary,
        "output": output,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Verify a mixed Metashape Backbone acceptance run")
    parser.add_argument("--log", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--expect-cameras", type=int, required=True)
    parser.add_argument("--expect-aligned", type=int, required=True)
    parser.add_argument("--expect-panorama-cameras", type=int, required=True)
    parser.add_argument("--expect-panorama-aligned", type=int, required=True)
    parser.add_argument("--expect-frame-cameras", type=int, required=True)
    parser.add_argument("--expect-frame-aligned", type=int, required=True)
    parser.add_argument("--expect-sensors", type=int, required=True)
    parser.add_argument("--expect-cube-images", type=int, required=True)
    parser.add_argument("--expect-frame-images", type=int, required=True)
    parser.add_argument("--expect-colmap-images", type=int, required=True)
    parser.add_argument("--expect-colmap-cameras", type=int, required=True)
    parser.add_argument("--expect-colmap-points", type=int, required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    result = verify_backbone_acceptance(
        log_path=args.log,
        project_path=args.project,
        export_dir=args.output,
        expect_cameras=args.expect_cameras,
        expect_aligned=args.expect_aligned,
        expect_panorama_cameras=args.expect_panorama_cameras,
        expect_panorama_aligned=args.expect_panorama_aligned,
        expect_frame_cameras=args.expect_frame_cameras,
        expect_frame_aligned=args.expect_frame_aligned,
        expect_sensors=args.expect_sensors,
        expect_cube_images=args.expect_cube_images,
        expect_frame_images=args.expect_frame_images,
        expect_colmap_images=args.expect_colmap_images,
        expect_colmap_cameras=args.expect_colmap_cameras,
        expect_colmap_points=args.expect_colmap_points,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
