import argparse
import math
import shutil
from dataclasses import dataclass
from pathlib import Path

import Metashape


@dataclass(frozen=True)
class Variant:
    name: str
    camera_type: str
    import_mode: str
    station_align: bool
    release_optimize: bool
    fixed_initial: bool = True
    adaptive_fitting: bool = False
    filter_stationary_points: bool = True


VARIANTS = [
    Variant("v01_regular_fisheye_station_release_fixed", "fisheye", "regular", True, True),
    Variant("v02_regular_fisheye_station_only_fixed", "fisheye", "regular", True, False),
    Variant("v03_regular_fisheye_folder_only_fixed", "fisheye", "regular", False, True),
    Variant("v04_multiplane_fisheye_station_release_fixed", "fisheye", "multiplane", True, True),
    Variant("v05_regular_frame_folder_only_fixed", "frame", "regular", False, True),
    Variant("v06_readme_exact_grouped_fisheye_station_release", "fisheye", "folder_groups", True, True, True, True, False),
    Variant("v07_readme_exact_grouped_fisheye_station_only", "fisheye", "folder_groups", True, False, True, True, False),
]


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--max-frames", type=int, default=50)
    parser.add_argument("--only", nargs="*")
    return parser.parse_args()


def emit(message):
    print(message, flush=True)


def image_paths_from_frames(frames_root, max_frames):
    paths = sorted(Path(frames_root).rglob("*.jpg"))
    if max_frames:
        paths = paths[: max_frames * 2]
    return paths[: len(paths) - (len(paths) % 2)]


def set_sensor_model(chunk, camera_type, fixed_initial):
    sensor_type = Metashape.Sensor.Type.Fisheye if camera_type == "fisheye" else Metashape.Sensor.Type.Frame
    for sensor in chunk.sensors:
        sensor.type = sensor_type
        sensor.pixel_width = 0.0024
        sensor.pixel_height = 0.0024
        sensor.focal_length = 2.5
        sensor.fixed_params = ["B1", "B2", "K4"] if fixed_initial else []
        calib = sensor.calibration
        if calib:
            calib.b1 = 0
            calib.b2 = 0
            calib.k4 = 0


def add_station_groups(chunk):
    groups = []
    for idx in range(0, len(chunk.cameras), 2):
        group = chunk.addCameraGroup()
        group.label = f"station_{idx // 2 + 1:05d}"
        group.type = Metashape.CameraGroup.Type.Station
        chunk.cameras[idx].group = group
        if idx + 1 < len(chunk.cameras):
            chunk.cameras[idx + 1].group = group
        groups.append(group)
    return groups


def import_images(chunk, image_paths, import_mode):
    if import_mode == "multiplane":
        filegroups = [2] * (len(image_paths) // 2)
        chunk.addPhotos(
            [str(path) for path in image_paths],
            filegroups=filegroups,
            layout=Metashape.MultiplaneLayout,
            load_xmp_accuracy=True,
        )
    elif import_mode == "folder_groups":
        for idx in range(0, len(image_paths), 2):
            group = chunk.addCameraGroup()
            group.label = image_paths[idx].parent.name
            group.type = Metashape.CameraGroup.Type.Folder
            chunk.addPhotos(
                [str(image_paths[idx]), str(image_paths[idx + 1])],
                group=group.key,
                load_xmp_accuracy=True,
            )
    else:
        chunk.addPhotos([str(path) for path in image_paths], load_xmp_accuracy=True)


def point_bbox(chunk):
    points = []
    if chunk.tie_points:
        for point in chunk.tie_points.points:
            if point.valid and abs(point.coord[3]) > 1e-12:
                p = chunk.transform.matrix.mulp(Metashape.Vector([point.coord[i] / point.coord[3] for i in range(3)]))
                points.append(p)
    if not points:
        return None
    mins = [min(p[i] for p in points) for i in range(3)]
    maxs = [max(p[i] for p in points) for i in range(3)]
    return mins, maxs, [maxs[i] - mins[i] for i in range(3)]


def camera_bbox(chunk):
    centers = [chunk.transform.matrix.mulp(camera.center) for camera in chunk.cameras if camera.transform]
    if not centers:
        return None
    mins = [min(c[i] for c in centers) for i in range(3)]
    maxs = [max(c[i] for c in centers) for i in range(3)]
    return mins, maxs, [maxs[i] - mins[i] for i in range(3)]


def station_distances(chunk):
    distances = []
    for group in chunk.camera_groups:
        cameras = [camera for camera in chunk.cameras if camera.group == group and camera.transform]
        if len(cameras) != 2:
            continue
        c0 = chunk.transform.matrix.mulp(cameras[0].center)
        c1 = chunk.transform.matrix.mulp(cameras[1].center)
        delta = c0 - c1
        distances.append(math.sqrt(sum(delta[i] * delta[i] for i in range(3))))
    return distances


def summarize(chunk):
    aligned = [camera for camera in chunk.cameras if camera.transform]
    tie_points = sum(1 for point in chunk.tie_points.points if point.valid) if chunk.tie_points else 0
    pb = point_bbox(chunk)
    cb = camera_bbox(chunk)
    sd = station_distances(chunk)
    sensor_lines = []
    for sensor in chunk.sensors:
        calib = sensor.calibration
        sensor_lines.append(
            f"sensor {sensor.label}: type={sensor.type}, f={getattr(calib, 'f', None)}, "
            f"fixed={list(sensor.fixed_params)}, b1={getattr(calib, 'b1', None)}, "
            f"b2={getattr(calib, 'b2', None)}, k4={getattr(calib, 'k4', None)}"
        )
    return "\n".join(
        [
            f"cameras={len(chunk.cameras)} aligned={len(aligned)} sensors={len(chunk.sensors)} tie_points={tie_points}",
            f"camera_bbox={cb}",
            f"point_bbox={pb}",
            f"station_distance_min_max_avg={None if not sd else (min(sd), max(sd), sum(sd) / len(sd))}",
            *sensor_lines,
        ]
    )


def run_variant(variant, image_paths, out_root):
    emit(f"=== {variant.name} ===")
    variant_dir = out_root / variant.name
    if variant_dir.exists():
        shutil.rmtree(variant_dir)
    variant_dir.mkdir(parents=True)
    project_path = variant_dir / f"{variant.name}.psx"

    doc = Metashape.app.document
    doc.clear()
    chunk = doc.addChunk()
    doc.chunk = chunk

    import_images(chunk, image_paths, variant.import_mode)
    set_sensor_model(chunk, variant.camera_type, variant.fixed_initial)

    if variant.station_align:
        if variant.import_mode == "folder_groups":
            for group in chunk.camera_groups:
                group.type = Metashape.CameraGroup.Type.Station
        else:
            add_station_groups(chunk)
    else:
        for idx in range(0, len(chunk.cameras), 2):
            group = chunk.addCameraGroup()
            group.label = f"folder_{idx // 2 + 1:05d}"
            group.type = Metashape.CameraGroup.Type.Folder
            chunk.cameras[idx].group = group
            if idx + 1 < len(chunk.cameras):
                chunk.cameras[idx + 1].group = group

    chunk.matchPhotos(
        downscale=1,
        generic_preselection=True,
        reference_preselection=False,
        filter_stationary_points=variant.filter_stationary_points,
        keypoint_limit=40000,
        tiepoint_limit=0,
    )
    chunk.alignCameras(adaptive_fitting=variant.adaptive_fitting)

    if variant.release_optimize:
        for group in chunk.camera_groups:
            group.type = Metashape.CameraGroup.Type.Folder
        chunk.optimizeCameras(fit_b1=False, fit_b2=False, fit_k4=False)

    doc.save(str(project_path))
    report = summarize(chunk)
    (variant_dir / "summary.txt").write_text(report, encoding="utf-8")
    emit(report)


def main():
    args = parse_args()
    frames_root = Path(args.frames_root)
    out_root = Path(args.output_root)
    out_root.mkdir(parents=True, exist_ok=True)
    image_paths = image_paths_from_frames(frames_root, args.max_frames)
    if not image_paths:
        raise RuntimeError("No images found")

    selected = set(args.only or [])
    for variant in VARIANTS:
        if selected and variant.name not in selected:
            continue
        run_variant(variant, image_paths, out_root)


if __name__ == "__main__":
    main()
