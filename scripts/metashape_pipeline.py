import argparse
import json
import math
import os
import sys
from pathlib import Path

import Metashape

import align_ground_plane
import export_colmap


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--input-root")
    p.add_argument("--manifest")
    p.add_argument("--project", required=True)
    p.add_argument("--export-dir", required=True)
    p.add_argument("--max-frames", type=int, default=0)
    return p.parse_args(sys.argv[1:])


def emit_progress(value):
    print(f"PROGRESS:{int(value)}", flush=True)


def ensure_project(project_path):
    doc = Metashape.app.document
    doc.save(str(project_path))
    return doc


def load_manifest(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def copy_sensor_geometry(dst, src):
    if not src:
        return
    dst.width = src.width
    dst.height = src.height
    dst.pixel_width = src.pixel_width
    dst.pixel_height = src.pixel_height
    dst.focal_length = src.focal_length
    try:
        dst.calibration = src.calibration
    except Exception:
        pass


def configure_fisheye_sensor(sensor):
    sensor.type = Metashape.Sensor.Type.Fisheye
    sensor.pixel_width = 0.0024
    sensor.pixel_height = 0.0024
    sensor.focal_length = 2.5
    sensor.fixed_params = ["B1", "B2", "K4"]
    calib = sensor.calibration
    if calib:
        calib.b1 = 0
        calib.b2 = 0
        calib.k4 = 0


def make_track_sensor(chunk, source_camera, label, sensor_type):
    sensor = chunk.addSensor()
    sensor.label = label
    copy_sensor_geometry(sensor, source_camera.sensor if source_camera else None)
    sensor.type = sensor_type
    if sensor_type == Metashape.Sensor.Type.Fisheye:
        configure_fisheye_sensor(sensor)
    return sensor


def camera_path_name(camera):
    try:
        return Path(camera.photo.path).name.lower()
    except Exception:
        return camera.label.lower()


def add_photos_get_new(chunk, paths, group_key=None):
    before = len(chunk.cameras)
    kwargs = {"load_xmp_accuracy": True}
    if group_key is not None:
        kwargs["group"] = group_key
    chunk.addPhotos([str(p) for p in paths], **kwargs)
    return list(chunk.cameras)[before:]


def import_panorama_track(chunk, track):
    station_groups = []
    left_sensor = None
    right_sensor = None
    left_label = track.get("left_sensor_label", f"{track['track_id']}_left")
    right_label = track.get("right_sensor_label", f"{track['track_id']}_right")

    for frame in track.get("frames", []):
        group = chunk.addCameraGroup()
        group.label = frame.get("group_label", frame.get("frame_id", track["track_id"]))
        group.type = Metashape.CameraGroup.Type.Folder
        station_groups.append(group)

        paths = [frame["left"], frame["right"]]
        new_cameras = add_photos_get_new(chunk, paths, group_key=group.key)
        for camera in new_cameras:
            name = camera_path_name(camera)
            if name == Path(frame["left"]).name.lower() or name.endswith("_left.jpg"):
                if left_sensor is None:
                    left_sensor = make_track_sensor(chunk, camera, left_label, Metashape.Sensor.Type.Fisheye)
                camera.sensor = left_sensor
            elif name == Path(frame["right"]).name.lower() or name.endswith("_right.jpg"):
                if right_sensor is None:
                    right_sensor = make_track_sensor(chunk, camera, right_label, Metashape.Sensor.Type.Fisheye)
                camera.sensor = right_sensor

    return station_groups


def import_photo_track(chunk, track):
    group = chunk.addCameraGroup()
    group.label = track.get("group_label", f"{track['track_id']}_photos")
    group.type = Metashape.CameraGroup.Type.Folder

    photo_sensors = track.get("photo_sensors") or []
    if photo_sensors:
        imported = []
        for sensor_group in photo_sensors:
            photos = sensor_group.get("photos", [])
            if not photos:
                continue
            new_cameras = add_photos_get_new(chunk, photos, group_key=group.key)
            if not new_cameras:
                continue
            sensor = make_track_sensor(
                chunk,
                new_cameras[0],
                sensor_group.get("sensor_label", track.get("sensor_label", f"{track['track_id']}_frame")),
                Metashape.Sensor.Type.Frame,
            )
            for camera in new_cameras:
                camera.sensor = sensor
            imported.extend(new_cameras)
        return imported

    photos = track.get("photos", [])
    if not photos:
        return []
    new_cameras = add_photos_get_new(chunk, photos, group_key=group.key)
    sensors_by_size = {}
    base_label = track.get("sensor_label", f"{track['track_id']}_frame")
    for camera in new_cameras:
        src = camera.sensor
        key = (getattr(src, "width", 0), getattr(src, "height", 0))
        if key not in sensors_by_size:
            suffix = "" if not sensors_by_size else f"_{len(sensors_by_size) + 1:02d}"
            sensor = make_track_sensor(chunk, camera, f"{base_label}{suffix}", Metashape.Sensor.Type.Frame)
            sensors_by_size[key] = sensor
        camera.sensor = sensors_by_size[key]
    return new_cameras


def import_manifest_tracks(chunk, manifest):
    station_groups = []
    for track in manifest.get("tracks", []):
        track_type = track.get("track_type")
        if track_type == "panorama_video":
            station_groups.extend(import_panorama_track(chunk, track))
        elif track_type in {"standard_photos", "aerial_photos"}:
            import_photo_track(chunk, track)
        else:
            raise RuntimeError(f"Unsupported track_type: {track_type}")
    prune_unused_sensors(chunk)
    return station_groups


def used_sensors(chunk):
    sensors = []
    seen = set()
    for camera in chunk.cameras:
        if camera.sensor and camera.sensor.key not in seen:
            sensors.append(camera.sensor)
            seen.add(camera.sensor.key)
    return sensors


def prune_unused_sensors(chunk):
    used = {sensor.key for sensor in used_sensors(chunk)}
    for sensor in list(chunk.sensors):
        if sensor.key not in used:
            try:
                chunk.remove(sensor)
            except Exception:
                pass


def import_legacy_frames(chunk, input_root, max_frames):
    frame_dirs = sorted(p for p in input_root.iterdir() if p.is_dir())
    if max_frames and max_frames > 0:
        frame_dirs = frame_dirs[: max_frames]
    if not frame_dirs:
        raise RuntimeError("No extracted frames found")

    station_groups = []
    for frame_dir in frame_dirs:
        image_paths = sorted(str(p) for p in frame_dir.glob("*.jpg"))
        if len(image_paths) < 2:
            continue
        group = chunk.addCameraGroup()
        group.label = frame_dir.name
        group.type = Metashape.CameraGroup.Type.Folder
        station_groups.append(group)
        chunk.addPhotos(image_paths[:2], group=group.key, load_xmp_accuracy=True)

    for sensor in chunk.sensors:
        configure_fisheye_sensor(sensor)
    return station_groups


def station_distances(chunk):
    distances = []
    for group in chunk.camera_groups:
        cameras = [camera for camera in chunk.cameras if camera.group == group and camera.transform]
        if len(cameras) != 2:
            continue
        centers = [chunk.transform.matrix.mulp(camera.center) for camera in cameras]
        delta = centers[0] - centers[1]
        distances.append(math.sqrt(delta.x * delta.x + delta.y * delta.y + delta.z * delta.z))
    return distances


def write_alignment_summary(chunk, export_dir, project_path):
    aligned = [camera for camera in chunk.cameras if camera.transform]
    distances = station_distances(chunk)
    lines = [
        "xPano Metashape alignment summary",
        f"project={project_path}",
        f"cameras={len(chunk.cameras)}",
        f"aligned={len(aligned)}",
        f"groups={len(chunk.camera_groups)}",
        f"sensors={len(used_sensors(chunk))}",
    ]
    if distances:
        lines.append(
            "station_baseline_min_max_avg="
            f"{min(distances):.9f},{max(distances):.9f},{(sum(distances) / len(distances)):.9f}"
        )
    for sensor in used_sensors(chunk):
        calib = sensor.calibration
        lines.append(
            "sensor="
            f"{sensor.label},type={sensor.type},size={sensor.width}x{sensor.height},"
            f"pixel={sensor.pixel_width},{sensor.pixel_height},focal={sensor.focal_length},"
            f"calib_f={getattr(calib, 'f', None)},fixed={list(sensor.fixed_params)}"
        )
    (export_dir / "xpano_alignment_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    args = parse_args()
    project_path = Path(args.project)
    export_dir = Path(args.export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)

    doc = Metashape.app.document
    chunk = doc.addChunk()
    doc.chunk = chunk

    emit_progress(40)
    if args.manifest:
        manifest = load_manifest(args.manifest)
        station_groups = import_manifest_tracks(chunk, manifest)
    elif args.input_root:
        station_groups = import_legacy_frames(chunk, Path(args.input_root), args.max_frames)
    else:
        raise RuntimeError("Either --manifest or --input-root is required")

    if not chunk.cameras:
        raise RuntimeError("No extracted frame images found")

    for group in station_groups:
        group.type = Metashape.CameraGroup.Type.Station

    emit_progress(55)
    for group in station_groups:
        try:
            group.type = Metashape.CameraGroup.Type.Station
        except Exception:
            pass

    emit_progress(60)
    chunk.matchPhotos(
        downscale=1,
        generic_preselection=True,
        reference_preselection=False,
        filter_stationary_points=False,
        guided_matching=False,
        keypoint_limit=40000,
        tiepoint_limit=0,
    )
    emit_progress(75)
    chunk.alignCameras(adaptive_fitting=True)
    emit_progress(82)

    for group in station_groups:
        try:
            group.type = Metashape.CameraGroup.Type.Folder
        except Exception:
            pass
    chunk.optimizeCameras(fit_b1=False, fit_b2=False, fit_k4=False)
    emit_progress(90)

    ensure_project(project_path)
    write_alignment_summary(chunk, export_dir, project_path)
    emit_progress(96)

    print(">>> 自动地平面校正", flush=True)
    try:
        align_ground_plane.main()
    except Exception as exc:
        print(f"WARN: 地平面校正失败，继续导出: {exc}", flush=True)
    emit_progress(97)

    print(">>> 导出 COLMAP/Cubemap", flush=True)
    export_colmap.run_mixed_export(str(export_dir))
    emit_progress(100)


if __name__ == "__main__":
    main()
