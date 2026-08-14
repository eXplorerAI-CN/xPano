import importlib
import io
import sys
import types
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch


class FakeSensor:
    def __init__(self, key, label, sensor_type):
        self.key = key
        self.label = label
        self.type = sensor_type
        self.width = 4000
        self.height = 3000
        self.pixel_width = 0.001
        self.pixel_height = 0.001
        self.focal_length = 4.0
        self.calibration = types.SimpleNamespace(f=1000)
        self.fixed_params = []


class FakeCamera:
    def __init__(self, label, sensor, aligned):
        self.label = label
        self.sensor = sensor
        self.transform = object() if aligned else None
        self.group = None


class FakeDocument:
    def __init__(self, chunk):
        self.chunk = chunk

    def open(self, _path):
        return None


def import_diagnostic(chunk):
    fake_metashape = types.SimpleNamespace(
        Document=lambda: FakeDocument(chunk),
        Sensor=types.SimpleNamespace(Type=types.SimpleNamespace(Fisheye="Fisheye", Frame="Frame")),
        CameraGroup=types.SimpleNamespace(Type=types.SimpleNamespace(Folder="Folder", Station="Station")),
    )
    with patch.dict(sys.modules, {"Metashape": fake_metashape}):
        sys.modules.pop("scripts.diagnose_metashape_project", None)
        return importlib.import_module("scripts.diagnose_metashape_project")


class DiagnoseMetashapeProjectTests(unittest.TestCase):
    def test_reports_and_checks_per_type_alignment_counts(self):
        fisheye = FakeSensor(1, "pano", "Fisheye")
        frame = FakeSensor(2, "flat", "Frame")
        chunk = types.SimpleNamespace(
            cameras=[
                FakeCamera("pano-left", fisheye, True),
                FakeCamera("pano-right", fisheye, True),
                FakeCamera("flat-one", frame, True),
                FakeCamera("flat-two", frame, False),
            ],
            camera_groups=[],
            sensors=[fisheye, frame],
        )
        diagnostic = import_diagnostic(chunk)
        argv = [
            "diagnose_metashape_project.py",
            "--project",
            "project.psx",
            "--expect-panorama-cameras",
            "2",
            "--expect-panorama-aligned",
            "2",
            "--expect-frame-cameras",
            "2",
            "--expect-frame-aligned",
            "1",
        ]

        output = io.StringIO()
        with patch.object(sys, "argv", argv), redirect_stdout(output):
            diagnostic.main()

        self.assertIn("panorama_cameras=2 panorama_aligned=2", output.getvalue())
        self.assertIn("frame_cameras=2 frame_aligned=1", output.getvalue())


if __name__ == "__main__":
    unittest.main()
