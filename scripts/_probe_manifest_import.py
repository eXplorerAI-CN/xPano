import argparse
from collections import Counter
from pathlib import Path

import Metashape

import metashape_pipeline


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--expect-sensors", type=int)
    parser.add_argument("--expect-frame-sensors", type=int)
    parser.add_argument("--expect-fisheye-sensors", type=int)
    parser.add_argument("--expect-station-groups", type=int)
    parser.add_argument("--expect-folder-groups", type=int)
    args = parser.parse_args()

    doc = Metashape.app.document
    chunk = doc.addChunk()
    doc.chunk = chunk
    manifest = metashape_pipeline.load_manifest(Path(args.manifest))
    station_groups = metashape_pipeline.import_manifest_tracks(chunk, manifest)

    print(f"tracks={len(manifest.get('tracks', []))}", flush=True)
    print(f"cameras={len(chunk.cameras)} groups={len(chunk.camera_groups)} sensors={len(chunk.sensors)}", flush=True)
    print(f"station_groups={len(station_groups)}", flush=True)
    print("sensors:", flush=True)
    for sensor in chunk.sensors:
        users = sum(1 for camera in chunk.cameras if camera.sensor == sensor)
        print(f"  label={sensor.label} type={sensor.type} users={users} fixed={list(sensor.fixed_params)}", flush=True)
    print("groups:", flush=True)
    for group in chunk.camera_groups:
        count = sum(1 for camera in chunk.cameras if camera.group == group)
        print(f"  label={group.label} type={group.type} cameras={count}", flush=True)
    print("camera_sensor_counts:", flush=True)
    counts = Counter(camera.sensor.label if camera.sensor else "<none>" for camera in chunk.cameras)
    for label, count in counts.items():
        print(f"  {label}: {count}", flush=True)

    failures = []
    frame_sensors = [sensor for sensor in chunk.sensors if sensor.type == Metashape.Sensor.Type.Frame]
    fisheye_sensors = [sensor for sensor in chunk.sensors if sensor.type == Metashape.Sensor.Type.Fisheye]
    folder_groups = [group for group in chunk.camera_groups if group.type == Metashape.CameraGroup.Type.Folder]
    checks = [
        ("sensors", args.expect_sensors, len(chunk.sensors)),
        ("frame_sensors", args.expect_frame_sensors, len(frame_sensors)),
        ("fisheye_sensors", args.expect_fisheye_sensors, len(fisheye_sensors)),
        ("station_groups", args.expect_station_groups, len(station_groups)),
        ("folder_groups", args.expect_folder_groups, len(folder_groups)),
    ]
    for name, expected, actual in checks:
        if expected is not None and expected != actual:
            failures.append(f"{name}: expected {expected}, got {actual}")
    if failures:
        raise RuntimeError("; ".join(failures))


if __name__ == "__main__":
    main()
