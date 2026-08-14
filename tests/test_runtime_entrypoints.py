import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EMBEDDED_PYTHON = ROOT / "binaries" / "python" / "python.exe"


class RuntimeEntrypointTests(unittest.TestCase):
    @unittest.skipUnless(EMBEDDED_PYTHON.is_file(), "bundled Python is not present")
    def test_shipped_entrypoints_start_from_unrelated_working_directory(self):
        entrypoints = [
            ("run_xpano_prepare_project.py", []),
            ("run_xpano_tracks_job.py", []),
            ("runtime_readiness.py", []),
            ("runtime_bootstrap.py", []),
            ("postprocess_colmap_axis.py", []),
            ("run_lfs_densify_viewer.py", []),
            (
                "run_lichtfeld_densify_standalone.py",
                ["--plugin-dir", str(ROOT / "tools" / "lichtfeld-densification-plugin")],
            ),
            ("lichtfeld_training.py", []),
            ("pano_extractor.py", []),
        ]
        env = {
            key: value
            for key, value in os.environ.items()
            if key.upper() not in {"PYTHONPATH", "PYTHONHOME"}
        }
        env["PYTHONNOUSERSITE"] = "1"
        with tempfile.TemporaryDirectory(prefix="xpano entrypoint ") as tmp:
            for name, prefix_args in entrypoints:
                with self.subTest(name=name):
                    completed = subprocess.run(
                        [EMBEDDED_PYTHON, ROOT / "scripts" / name, *prefix_args, "--help"],
                        cwd=tmp,
                        env=env,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=30,
                    )
                    self.assertEqual(
                        completed.returncode,
                        0,
                        f"{name} failed outside the repository:\n{completed.stdout}\n{completed.stderr}",
                    )


if __name__ == "__main__":
    unittest.main()
