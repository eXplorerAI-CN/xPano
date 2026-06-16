import sys
from pathlib import Path

import Metashape


def main():
    paths = [str(Path(p)) for p in sys.argv[1:]]
    doc = Metashape.app.document
    chunk = doc.addChunk()
    chunk.addPhotos(paths)
    print(f"after addPhotos cameras={len(chunk.cameras)} sensors={len(chunk.sensors)}", flush=True)
    for sensor in chunk.sensors:
        print(f"sensor before key={sensor.key} label={sensor.label} type={sensor.type}", flush=True)

    sensor = chunk.addSensor()
    sensor.label = "manual_track_sensor"
    sensor.type = Metashape.Sensor.Type.Frame
    if chunk.cameras and chunk.cameras[0].sensor:
        src = chunk.cameras[0].sensor
        sensor.width = src.width
        sensor.height = src.height
        sensor.pixel_width = src.pixel_width
        sensor.pixel_height = src.pixel_height
        sensor.focal_length = src.focal_length
        sensor.calibration = src.calibration
    for camera in chunk.cameras:
        camera.sensor = sensor
    print(f"after assignment cameras={len(chunk.cameras)} sensors={len(chunk.sensors)}", flush=True)
    for camera in chunk.cameras:
        print(f"camera={camera.label} sensor={camera.sensor.label}", flush=True)
    for sensor in chunk.sensors:
        users = sum(1 for camera in chunk.cameras if camera.sensor == sensor)
        print(f"sensor after key={sensor.key} label={sensor.label} users={users}", flush=True)


if __name__ == "__main__":
    main()
