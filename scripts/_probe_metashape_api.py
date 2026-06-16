import Metashape


def main():
    for params in (["b1", "b2", "k4"], ["B1", "B2", "K4"], ["B1", "B2", "K4", "P1"]):
        doc = Metashape.Document()
        chunk = doc.addChunk()
        sensor = chunk.addSensor()
        sensor.fixed_params = params
        print(f"{params!r} => {list(sensor.fixed_params)!r}", flush=True)

    print("CameraGroup types:", Metashape.CameraGroup.Type.Station, Metashape.CameraGroup.Type.Folder, flush=True)
    print("Sensor types:", Metashape.Sensor.Type.Frame, Metashape.Sensor.Type.Fisheye, flush=True)


if __name__ == "__main__":
    main()
