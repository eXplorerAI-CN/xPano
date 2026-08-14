import Metashape

import metashape_pipeline


def main():
    doc = Metashape.app.document
    doc.clear()
    chunk = doc.addChunk()

    src = chunk.addSensor()
    src.label = "auto_fisheye_source"
    src.width = 4000
    src.height = 3000
    src.type = Metashape.Sensor.Type.Fisheye
    src.pixel_width = 0.0024
    src.pixel_height = 0.0024
    src.focal_length = 2.5

    camera = chunk.addCamera()
    camera.sensor = src

    frame = metashape_pipeline.make_track_sensor(
        chunk,
        camera,
        "ordinary_frame",
        Metashape.Sensor.Type.Frame,
    )
    print(f"source_type={src.type} source_calibration_type={src.calibration.type}", flush=True)
    print(f"frame_type={frame.type} frame_calibration_type={frame.calibration.type}", flush=True)
    print(f"frame_size={frame.width}x{frame.height} frame_f={frame.calibration.f}", flush=True)

    if frame.type != Metashape.Sensor.Type.Frame:
        raise RuntimeError("Frame sensor type was not set to Frame")
    if frame.calibration.type != Metashape.Sensor.Type.Frame:
        raise RuntimeError("Frame calibration type was not reset to Frame")


if __name__ == "__main__":
    main()
