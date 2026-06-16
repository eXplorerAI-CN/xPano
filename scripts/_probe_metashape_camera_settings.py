import Metashape


def printable_attrs(obj):
    rows = []
    for name in dir(obj):
        if name.startswith("_"):
            continue
        if not any(token in name.lower() for token in ["axis", "axes", "type", "fisheye", "shutter", "film", "calib", "pixel", "focal", "fixed"]):
            continue
        try:
            value = getattr(obj, name)
        except Exception as exc:
            value = f"<error: {exc}>"
        if callable(value):
            continue
        rows.append((name, repr(value)))
    return rows


def main():
    doc = Metashape.app.document
    doc.clear()
    chunk = doc.addChunk()
    camera = chunk.addCamera()
    sensor = chunk.addSensor()
    camera.sensor = sensor

    print("Metashape", Metashape.app.version, flush=True)
    print("Sensor.Type members:", flush=True)
    for name in dir(Metashape.Sensor.Type):
        if not name.startswith("_"):
            print(f"  {name}={getattr(Metashape.Sensor.Type, name)!r}", flush=True)

    print("Sensor attrs:", flush=True)
    for name, value in printable_attrs(sensor):
        print(f"  {name}={value}", flush=True)

    print("Calibration attrs:", flush=True)
    for name, value in printable_attrs(sensor.calibration):
        print(f"  {name}={value}", flush=True)

    print("Camera attrs:", flush=True)
    for name, value in printable_attrs(camera):
        print(f"  {name}={value}", flush=True)

    sensor.type = Metashape.Sensor.Type.Fisheye
    sensor.pixel_width = 0.0024
    sensor.pixel_height = 0.0024
    sensor.focal_length = 2.5
    sensor.fixed_params = ["B1", "B2", "K4"]
    print("After fisheye physical init:", flush=True)
    print(f"  sensor.type={sensor.type!r}", flush=True)
    print(f"  calibration.type={sensor.calibration.type!r}", flush=True)
    print(f"  calibration.f={sensor.calibration.f!r}", flush=True)
    print(f"  fixed_params={list(sensor.fixed_params)!r}", flush=True)


if __name__ == "__main__":
    main()
