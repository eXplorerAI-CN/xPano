import argparse
from collections import Counter
import math
from pathlib import Path

import Metashape


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--expect-cameras", type=int)
    parser.add_argument("--expect-aligned", type=int)
    parser.add_argument("--expect-panorama-cameras", type=int)
    parser.add_argument("--expect-panorama-aligned", type=int)
    parser.add_argument("--expect-frame-cameras", type=int)
    parser.add_argument("--expect-frame-aligned", type=int)
    parser.add_argument("--expect-groups", type=int)
    parser.add_argument("--expect-sensors", type=int)
    parser.add_argument("--expect-fisheye-sensors", type=int)
    parser.add_argument("--expect-frame-sensors", type=int)
    parser.add_argument("--expect-folder-groups", type=int)
    parser.add_argument("--expect-station-groups", type=int)
    parser.add_argument("--expect-fixed-fisheye", action="store_true")
    return parser.parse_args()


def assert_expected(name, actual, expected, failures):
    if expected is not None and actual != expected:
        failures.append(f"{name}: expected {expected}, got {actual}")


def main():
    args = parse_args()
    doc = Metashape.Document()
    doc.open(str(Path(args.project)))
    chunk = doc.chunk
    if chunk is None:
        raise RuntimeError("No active chunk")

    cameras = list(chunk.cameras)
    aligned = [camera for camera in cameras if camera.transform]
    panorama_cameras = [
        camera
        for camera in cameras
        if camera.sensor and camera.sensor.type == Metashape.Sensor.Type.Fisheye
    ]
    panorama_aligned = [camera for camera in panorama_cameras if camera.transform]
    frame_cameras = [
        camera
        for camera in cameras
        if camera.sensor and camera.sensor.type == Metashape.Sensor.Type.Frame
    ]
    frame_aligned = [camera for camera in frame_cameras if camera.transform]
    groups = list(chunk.camera_groups)
    sensors = list(chunk.sensors)
    fisheye_sensors = [sensor for sensor in sensors if sensor.type == Metashape.Sensor.Type.Fisheye]
    frame_sensors = [sensor for sensor in sensors if sensor.type == Metashape.Sensor.Type.Frame]
    folder_groups = [group for group in groups if group.type == Metashape.CameraGroup.Type.Folder]
    station_groups = [group for group in groups if group.type == Metashape.CameraGroup.Type.Station]
    sensor_counts = Counter(camera.sensor.label if camera.sensor else "<none>" for camera in cameras)
    group_counts = Counter(camera.group.label if camera.group else "<none>" for camera in cameras)

    print(f"cameras={len(cameras)} aligned={len(aligned)} groups={len(groups)} sensors={len(sensors)}")
    print(
        f"panorama_cameras={len(panorama_cameras)} panorama_aligned={len(panorama_aligned)} "
        f"frame_cameras={len(frame_cameras)} frame_aligned={len(frame_aligned)}"
    )
    print(
        f"fisheye_sensors={len(fisheye_sensors)} frame_sensors={len(frame_sensors)} "
        f"folder_groups={len(folder_groups)} station_groups={len(station_groups)}"
    )
    print("sensor_counts:")
    for label, count in sensor_counts.most_common():
        print(f"  {label}: {count}")
    print("group_counts_sample:")
    for label, count in list(group_counts.items())[:12]:
        print(f"  {label}: {count}")

    print("sensors:")
    for sensor in sensors:
        calib = sensor.calibration
        print(
            "  "
            f"label={sensor.label!r} key={sensor.key} type={sensor.type} "
            f"size={sensor.width}x{sensor.height} "
            f"pixel={sensor.pixel_width},{sensor.pixel_height} "
            f"focal={sensor.focal_length} "
            f"calib_f={getattr(calib, 'f', None)} "
            f"fixed={list(sensor.fixed_params)}"
        )

    print("camera_sample:")
    for camera in cameras[:12]:
        group = camera.group.label if camera.group else "<none>"
        group_type = camera.group.type if camera.group else "<none>"
        sensor = camera.sensor.label if camera.sensor else "<none>"
        print(
            "  "
            f"label={camera.label!r} group={group!r} group_type={group_type} "
            f"sensor={sensor!r} aligned={camera.transform is not None}"
        )

    print("station_baseline_sample:")
    for group in groups[:12]:
        grouped = [camera for camera in cameras if camera.group == group and camera.transform]
        if len(grouped) != 2:
            print(f"  {group.label}: camera_count={len(grouped)}")
            continue
        centers = [chunk.transform.matrix.mulp(camera.center) for camera in grouped]
        delta = centers[0] - centers[1]
        distance = math.sqrt(delta.x * delta.x + delta.y * delta.y + delta.z * delta.z)
        print(
            f"  {group.label}: "
            f"{grouped[0].label} <-> {grouped[1].label} "
            f"distance={distance:.9f}"
        )

    failures = []
    assert_expected("cameras", len(cameras), args.expect_cameras, failures)
    assert_expected("aligned", len(aligned), args.expect_aligned, failures)
    assert_expected("panorama_cameras", len(panorama_cameras), args.expect_panorama_cameras, failures)
    assert_expected("panorama_aligned", len(panorama_aligned), args.expect_panorama_aligned, failures)
    assert_expected("frame_cameras", len(frame_cameras), args.expect_frame_cameras, failures)
    assert_expected("frame_aligned", len(frame_aligned), args.expect_frame_aligned, failures)
    assert_expected("groups", len(groups), args.expect_groups, failures)
    assert_expected("sensors", len(sensors), args.expect_sensors, failures)
    assert_expected("fisheye_sensors", len(fisheye_sensors), args.expect_fisheye_sensors, failures)
    assert_expected("frame_sensors", len(frame_sensors), args.expect_frame_sensors, failures)
    assert_expected("folder_groups", len(folder_groups), args.expect_folder_groups, failures)
    assert_expected("station_groups", len(station_groups), args.expect_station_groups, failures)
    if args.expect_fixed_fisheye:
        for sensor in fisheye_sensors:
            fixed = list(sensor.fixed_params)
            if fixed != ["B1", "B2", "K4"]:
                failures.append(f"fisheye fixed_params for {sensor.label}: expected ['B1', 'B2', 'K4'], got {fixed}")
    if failures:
        raise RuntimeError("; ".join(failures))


if __name__ == "__main__":
    main()
