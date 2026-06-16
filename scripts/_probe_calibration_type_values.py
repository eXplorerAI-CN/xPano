import Metashape


def main():
    doc = Metashape.Document()
    chunk = doc.addChunk()
    sensor = chunk.addSensor()
    sensor.width = 3840
    sensor.height = 3840
    for stype in [Metashape.Sensor.Type.Frame, Metashape.Sensor.Type.Fisheye, Metashape.Sensor.Type.Spherical, Metashape.Sensor.Type.Cylindrical]:
        sensor.type = stype
        sensor.pixel_width = 0.0024
        sensor.pixel_height = 0.0024
        sensor.focal_length = 2.5
        calib = sensor.calibration
        print(f"sensor={stype}, calibration.type={getattr(calib, 'type', None)}, f={getattr(calib, 'f', None)}", flush=True)

    print("Calibration type attr class:", type(sensor.calibration.type), flush=True)
    print("Calibration type dir:", [name for name in dir(sensor.calibration.type) if not name.startswith('_')], flush=True)


if __name__ == "__main__":
    main()
