import contextlib
import io
import json
import subprocess
import struct
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.colmap_backend import read_colmap_points3d_file, write_colmap_points3d
from scripts.lichtfeld_densify import LichtfeldDensifyConfig
from scripts.run_lfs_densify_viewer import build_argparser, run


class LfsDensifyViewerTests(unittest.TestCase):
    def test_default_densification_profile_is_balanced_for_4k_material(self):
        args = build_argparser().parse_args([
            "--output-dir",
            "scene",
            "--python-exe",
            "python.exe",
            "--plugin-dir",
            "plugin",
        ])
        config = LichtfeldDensifyConfig()

        self.assertEqual(args.roma, "fast")
        self.assertEqual(args.num_refs, 0.75)
        self.assertEqual(args.nns_per_ref, 3)
        self.assertEqual(args.matches_per_ref, 10000)
        self.assertEqual(args.certainty_thresh, 0.20)
        self.assertEqual(config.roma_setting, "fast")
        self.assertEqual(config.num_refs, 0.75)
        self.assertEqual(config.nns_per_ref, 3)
        self.assertEqual(config.matches_per_ref, 10000)
        self.assertEqual(config.certainty_thresh, 0.20)

    def test_invalid_reference_defaults_fall_back_to_balanced_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sparse = root / "sparse" / "0"
            (root / "images").mkdir(parents=True)
            write_colmap_points3d(
                sparse / "points3D.bin",
                [{"id": 1, "xyz": (0.0, 0.0, 0.0), "rgb": (1, 2, 3), "error": 0.0, "track": []}],
            )
            captured = {}

            def fake_runner(config, **kwargs):
                captured.update({
                    "num_refs": config.num_refs,
                    "nns_per_ref": config.nns_per_ref,
                })
                (sparse / config.out_name).write_bytes(
                    b"ply\nformat binary_little_endian 1.0\nelement vertex 0\n"
                    b"property float x\nproperty float y\nproperty float z\n"
                    b"property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n"
                )

            args = build_argparser().parse_args([
                "--output-dir", str(root),
                "--python-exe", "python.exe",
                "--plugin-dir", "plugin",
                "--num-refs", "0",
                "--nns-per-ref", "0",
            ])
            run(args, densify_runner=fake_runner)

            self.assertEqual(captured, {"num_refs": 0.75, "nns_per_ref": 3})

    def test_script_help_runs_from_arbitrary_working_directory(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "run_lfs_densify_viewer.py"
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, str(script), "--help"],
                cwd=tmp,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--output-dir", result.stdout)

    def test_runs_densify_and_returns_preview_bin_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sparse = root / "sparse" / "0"
            images = root / "images"
            images.mkdir(parents=True)
            write_colmap_points3d(
                sparse / "points3D.bin",
                [{"id": 3, "xyz": (1.0, 2.0, 3.0), "rgb": (10, 20, 30), "error": 0.2, "track": []}],
            )

            def fake_runner(config, **kwargs):
                self.assertEqual(config.scene_root, root.resolve())
                self.assertEqual(config.images_subdir, "images")
                self.assertEqual(config.out_name, "points3D_dense.ply")
                self.assertEqual(config.python_exe, str(Path("python.exe")))
                self.assertEqual(config.roma_setting, "fast")
                self.assertEqual(config.max_points, 500000)
                kwargs["progress_cb"](45)
                (sparse / config.out_name).write_bytes(
                    b"ply\nformat binary_little_endian 1.0\nelement vertex 1\n"
                    b"property float x\nproperty float y\nproperty float z\n"
                    b"property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n"
                    + struct.pack("<fffBBB", 4.0, 5.0, 6.0, 40, 50, 60)
                )

            args = build_argparser().parse_args([
                "--output-dir",
                str(root),
                "--python-exe",
                "python.exe",
                "--plugin-dir",
                "plugin",
                "--roma",
                "fast",
                "--max-points",
                "500000",
                "--steps",
                "50",
            ])
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                result = run(args, densify_runner=fake_runner)

            merged = read_colmap_points3d_file(sparse / "points3D_dense.bin")
            self.assertEqual(result["original_points"], 1)
            self.assertEqual(result["dense_points"], 1)
            self.assertEqual(result["merged_points"], 2)
            self.assertEqual(len(merged), 2)
            self.assertTrue((sparse / "points3D.bin").exists())

            result_lines = [
                line.removeprefix("DENSIFY_RESULT:")
                for line in stdout.getvalue().splitlines()
                if line.startswith("DENSIFY_RESULT:")
            ]
            self.assertEqual(json.loads(result_lines[-1])["output_points_path"], result["output_points_path"])
            self.assertEqual(json.loads(result_lines[-1])["steps"], 50)


if __name__ == "__main__":
    unittest.main()
