import builtins
import importlib
import sys
import types
import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

import numpy as np


class ActiveComponentCamera:
    def __init__(self, chunk, key, component_key):
        self.chunk = chunk
        self.key = key
        self.component_key = str(component_key)

    @property
    def transform(self):
        return object() if str(self.chunk.component.key) == self.component_key else None


class ActiveComponentChunk:
    def __init__(self):
        self.components = [
            types.SimpleNamespace(key="first", label="First"),
            types.SimpleNamespace(key="second", label="Second"),
        ]
        self.component = self.components[0]
        self.cameras = [
            ActiveComponentCamera(self, 1, "first"),
            ActiveComponentCamera(self, 2, "second"),
            ActiveComponentCamera(self, 3, "second"),
        ]

    @property
    def tie_points(self):
        count = 1 if self.component.key == "first" else 2
        return types.SimpleNamespace(points=[object()] * count)


class ExportColmapTests(unittest.TestCase):
    def _load_module_with_metashape(self, metashape):
        previous_metashape = sys.modules.get("Metashape")
        sys.modules.pop("scripts.export_colmap", None)
        sys.modules["Metashape"] = metashape
        try:
            return importlib.import_module("scripts.export_colmap")
        finally:
            if previous_metashape is None:
                sys.modules.pop("Metashape", None)
            else:
                sys.modules["Metashape"] = previous_metashape

    def test_module_import_does_not_require_cv2(self):
        previous_metashape = sys.modules.get("Metashape")
        sys.modules.pop("scripts.export_colmap", None)
        sys.modules.pop("scripts.export_remap", None)
        sys.modules["Metashape"] = types.SimpleNamespace()

        real_import = builtins.__import__

        def import_without_cv2(name, *args, **kwargs):
            if name == "cv2":
                raise ModuleNotFoundError("No module named 'cv2'")
            return real_import(name, *args, **kwargs)

        try:
            with patch("builtins.__import__", side_effect=import_without_cv2):
                module = importlib.import_module("scripts.export_colmap")
        finally:
            sys.modules.pop("scripts.export_colmap", None)
            if previous_metashape is None:
                sys.modules.pop("Metashape", None)
            else:
                sys.modules["Metashape"] = previous_metashape

        self.assertTrue(hasattr(module, "remap_bilinear"))

    def test_frame_sensor_type_wins_over_stale_fisheye_calibration_type(self):
        metashape = types.SimpleNamespace(
            Sensor=types.SimpleNamespace(Type=types.SimpleNamespace(Frame="Frame", Fisheye="Fisheye"))
        )
        module = self._load_module_with_metashape(metashape)
        sensor = types.SimpleNamespace(
            type=metashape.Sensor.Type.Frame,
            calibration=types.SimpleNamespace(type="Fisheye"),
        )

        self.assertFalse(module.sensor_is_fisheye_like(sensor))

    def test_cubemap_save_failure_is_not_silent(self):
        module = self._load_module_with_metashape(types.SimpleNamespace())
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "missing" / "face.jpg"

            with self.assertRaises(Exception):
                module.threaded_remap_and_save(None, None, None, output)

    def test_remap_backend_uses_numpy_when_opencv_is_missing(self):
        module = self._load_module_with_metashape(types.SimpleNamespace())

        self.assertEqual(module.select_remap_backend(None, "auto"), "numpy")

    def test_auto_remap_backend_chooses_measured_fastest_valid_backend(self):
        module = self._load_module_with_metashape(types.SimpleNamespace())
        fake_cv2 = types.SimpleNamespace(
            ocl=types.SimpleNamespace(haveOpenCL=lambda: True),
        )

        with patch(
            "scripts.export_remap.benchmark_remap_backends",
            return_value={"opencv": 0.004, "opencl": 0.020},
        ):
            self.assertEqual(module.select_remap_backend(fake_cv2, "auto"), "opencv")

        with patch(
            "scripts.export_remap.benchmark_remap_backends",
            return_value={"opencv": 0.040, "opencl": 0.010},
        ):
            self.assertEqual(module.select_remap_backend(fake_cv2, "auto"), "opencl")

    def test_forced_opencl_falls_back_to_compiled_cpu_when_unavailable(self):
        module = self._load_module_with_metashape(types.SimpleNamespace())
        fake_cv2 = types.SimpleNamespace(
            ocl=types.SimpleNamespace(haveOpenCL=lambda: False),
        )

        self.assertEqual(module.select_remap_backend(fake_cv2, "opencl"), "opencv")

    def test_opencl_backend_explicitly_enables_opencl_execution(self):
        module = self._load_module_with_metashape(types.SimpleNamespace())
        enabled = []
        fake_cv2 = types.SimpleNamespace(
            ocl=types.SimpleNamespace(
                haveOpenCL=lambda: True,
                setUseOpenCL=enabled.append,
            ),
        )

        engine = module.RemapEngine(fake_cv2, "opencl")

        self.assertEqual(engine.backend, "opencl")
        self.assertEqual(enabled, [True])

    def test_accelerated_remap_failure_falls_back_observably_to_numpy(self):
        module = self._load_module_with_metashape(types.SimpleNamespace())
        source = np.arange(8 * 8 * 3, dtype=np.uint8).reshape(8, 8, 3)
        mx, my = np.meshgrid(
            np.linspace(0.2, 6.8, 6, dtype=np.float32),
            np.linspace(0.4, 6.6, 6, dtype=np.float32),
        )
        warnings = []
        fake_cv2 = types.SimpleNamespace(
            INTER_LINEAR=1,
            BORDER_CONSTANT=0,
            remap=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("driver failed")),
        )
        engine = module.RemapEngine(fake_cv2, "opencv", warnings.append)

        result = engine.remap(source, mx, my)

        np.testing.assert_array_equal(result, module.remap_bilinear(source, mx, my))
        self.assertEqual(engine.backend, "numpy")
        self.assertEqual(len(warnings), 1)
        self.assertIn("driver failed", warnings[0])

    def test_fisheye_grid_matches_metashape_tangential_distortion_order(self):
        module = self._load_module_with_metashape(types.SimpleNamespace())
        calibration = types.SimpleNamespace(
            width=400,
            height=400,
            f=108.5,
            cx=1.25,
            cy=-0.75,
            k1=0.02,
            k2=-0.003,
            k3=0.0004,
            k4=0.0,
            p1=0.004,
            p2=-0.003,
            b1=0.2,
            b2=-0.1,
        )
        output_width = 218
        mx, my = module.build_remap_grid(
            "front",
            output_width,
            calibration,
            np.eye(3),
            "EquidistantFisheye",
        )

        face_width, face_height, cx, cy = module.get_face_configs(output_width)["front"]
        u, v = np.meshgrid(
            np.arange(face_width, dtype=np.float32),
            np.arange(face_height, dtype=np.float32),
        )
        pinhole_focal = output_width / 2.0
        x_ray = (u + 0.5 - cx) / pinhole_focal
        y_ray = (v + 0.5 - cy) / pinhole_focal
        ray_radius = np.hypot(x_ray, y_ray)
        theta = np.arctan2(ray_radius, np.ones_like(ray_radius))
        x = np.divide(x_ray, ray_radius, out=np.zeros_like(theta), where=ray_radius > 1e-10) * theta
        y = np.divide(y_ray, ray_radius, out=np.zeros_like(theta), where=ray_radius > 1e-10) * theta
        radius2 = x * x + y * y
        radial = 1 + calibration.k1 * radius2 + calibration.k2 * radius2**2 + calibration.k3 * radius2**3
        xd = x * radial + calibration.p1 * (radius2 + 2 * x * x) + 2 * calibration.p2 * x * y
        yd = y * radial + calibration.p2 * (radius2 + 2 * y * y) + 2 * calibration.p1 * x * y
        expected_mx = (
            calibration.width / 2.0
            + calibration.cx
            - 0.5
            + xd * calibration.f
            + xd * calibration.b1
            + yd * calibration.b2
        )
        expected_my = calibration.height / 2.0 + calibration.cy - 0.5 + yd * calibration.f

        np.testing.assert_allclose(mx, expected_mx, atol=1e-4)
        np.testing.assert_allclose(my, expected_my, atol=1e-4)

    def test_normalized_4k_fisheye_calibration_covers_every_export_face(self):
        module = self._load_module_with_metashape(types.SimpleNamespace())
        calibration = types.SimpleNamespace(
            width=1920,
            height=1920,
            f=520.8333333333334,
            cx=0.0,
            cy=0.0,
            k1=0.0,
            k2=0.0,
            k3=0.0,
            k4=0.0,
            p1=0.0,
            p2=0.0,
            b1=0.0,
            b2=0.0,
        )
        output_width = 1042
        rotations = {
            "front": np.eye(3),
            "left": np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0]]),
            "right": np.array([[0, 0, -1], [0, 1, 0], [1, 0, 0]]),
            "top": np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]]),
            "bottom": np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]]),
        }

        for face, rotation in rotations.items():
            with self.subTest(face=face):
                mx, my = module.build_remap_grid(
                    face,
                    output_width,
                    calibration,
                    rotation,
                    "EquidistantFisheye",
                )
                self.assertGreaterEqual(
                    module.remap_valid_fraction(mx, my, calibration.width, calibration.height),
                    0.999,
                )

    def test_legacy_unscaled_4k_calibration_is_rejected_before_export(self):
        module = self._load_module_with_metashape(types.SimpleNamespace())
        calibration = types.SimpleNamespace(
            width=1920,
            height=1920,
            f=1041.6666666666667,
            cx=0.0,
            cy=0.0,
            k1=-0.095,
            k2=0.0,
            k3=0.0,
            k4=0.0,
            p1=0.0,
            p2=0.0,
            b1=0.0,
            b2=0.0,
        )
        sensor = types.SimpleNamespace(label="legacy-4k", calibration=calibration)
        rotation = np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0]])
        mx, my = module.build_remap_grid(
            "left",
            2084,
            calibration,
            rotation,
            "EquidistantFisheye",
        )

        with self.assertRaisesRegex(RuntimeError, "re-align"):
            module.validate_fisheye_remap_grid(sensor, "left", mx, my)

    def test_camera_image_signature_ignores_pose_but_invalidates_pixel_inputs(self):
        module = self._load_module_with_metashape(types.SimpleNamespace())
        calibration = types.SimpleNamespace(
            width=100,
            height=80,
            f=50.0,
            cx=0.1,
            cy=-0.2,
            k1=0.01,
            k2=0.0,
            k3=0.0,
            k4=0.0,
            p1=0.0,
            p2=0.0,
            b1=0.0,
            b2=0.0,
            type="Fisheye",
        )
        sensor = types.SimpleNamespace(key=7, type="Fisheye", calibration=calibration)
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.jpg"
            source.write_bytes(b"source-a")
            camera = types.SimpleNamespace(
                key=3,
                transform="pose-a",
                photo=types.SimpleNamespace(path=str(source)),
                sensor=sensor,
            )
            strategy = {"type": "Cubemap", "opt_W": 100, "info_str": "Fisheye"}
            source_cache = {}

            baseline = module.camera_image_signature(camera, strategy, source_cache)
            camera.transform = "pose-b"
            self.assertEqual(baseline, module.camera_image_signature(camera, strategy, source_cache))

            calibration.f = 51.0
            self.assertNotEqual(baseline, module.camera_image_signature(camera, strategy, source_cache))
            calibration.f = 50.0
            source.write_bytes(b"source-b")
            source_cache.clear()
            self.assertNotEqual(baseline, module.camera_image_signature(camera, strategy, source_cache))
            source.write_bytes(b"source-a")
            source_cache.clear()
            self.assertNotEqual(
                baseline,
                module.camera_image_signature(camera, {**strategy, "opt_W": 102}, source_cache),
            )

    def test_mixed_export_activates_selected_component_and_restores_original(self):
        chunk = ActiveComponentChunk()
        metashape = types.SimpleNamespace(app=types.SimpleNamespace(
            document=types.SimpleNamespace(chunk=chunk),
        ))
        module = self._load_module_with_metashape(metashape)

        with patch.object(
            module,
            "_run_active_component_export",
            side_effect=lambda *args, **kwargs: str(chunk.component.key),
        ):
            active_key = module.run_mixed_export(selected_component_key="second")

        self.assertEqual(active_key, "second")
        self.assertEqual(chunk.component.key, "first")

    def test_mixed_export_restores_original_component_after_export_failure(self):
        chunk = ActiveComponentChunk()
        metashape = types.SimpleNamespace(app=types.SimpleNamespace(
            document=types.SimpleNamespace(chunk=chunk),
        ))
        module = self._load_module_with_metashape(metashape)

        def fail_while_selected(*args, **kwargs):
            self.assertEqual(chunk.component.key, "second")
            raise RuntimeError("export failed")

        with patch.object(module, "_run_active_component_export", side_effect=fail_while_selected):
            with self.assertRaisesRegex(RuntimeError, "export failed"):
                module.run_mixed_export(selected_component_key="second")

        self.assertEqual(chunk.component.key, "first")



if __name__ == "__main__":
    unittest.main()
