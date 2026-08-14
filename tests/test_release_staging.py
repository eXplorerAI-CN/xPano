import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.release_staging import ReleaseStagingError, stage_release_resources


WINDOWS_RUNTIME_NAMES = (
    "msvcp140.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "vcomp140.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
)


class ReleaseStagingTests(unittest.TestCase):
    def test_entrypoint_help_runs_without_repository_pythonpath(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "release_staging.py"
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [sys.executable, str(script), "--help"],
                cwd=tmp,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def make_fixture(self, root):
        numpy_wheels = {
            "cp39": b"numpy-cp39-wheel",
            "cp310": b"numpy-cp310-wheel",
            "cp311": b"numpy-cp311-wheel",
            "cp312": b"numpy-cp312-wheel",
        }
        opencv_wheel = b"opencv-wheel"
        windows_runtime = {
            name: f"runtime-{name}".encode("ascii") for name in WINDOWS_RUNTIME_NAMES
        }
        files = {
            "binaries/python/python.exe": b"python",
            "binaries/python/Lib/site-packages/demo.py": b"demo",
            "binaries/python/Lib/site-packages/tqdm/__init__.py": b"__version__ = '4.68.3'",
            "scripts/run_xpano_tracks_job.py": b"print('run')",
            "scripts/runtime_bootstrap.py": b"print('bootstrap')",
            "scripts/lichtfeld_training.py": b"print('training')",
            "scripts/export_image_cache.py": b"CACHE_SCHEMA_VERSION = 1",
            "scripts/export_remap.py": b"def remap_bilinear(): pass",
            "scripts/fisheye_geometry.py": b"def normalized_fisheye_focal_px(): pass",
            "scripts/metashape_runtime_env.py": b"def build_metashape_process_env(): pass",
            "scripts/metashape_runtime_probe.py": b"print('probe')",
            "scripts/metashape_pipeline.py": b"print('pipeline')",
            "scripts/reexport_colmap_from_project.py": b"print('reexport')",
            "scripts/inspect_metashape_components.py": b"print('inspect components')",
            "scripts/component_selection.py": b"def select_component_key(): pass",
            "scripts/configure_environment.ps1": b"Write-Host ok",
            "scripts/build_release.ps1": b"must not ship",
            "scripts/__pycache__/bad.pyc": b"must not ship",
            "tools/colmap/bin/colmap.exe": b"colmap",
            "tools/colmap/bin/needed.dll": b"dll",
            "tools/colmap/bin/helper_test.exe": b"must not ship",
            "tools/colmap/plugins/imageformats/qjpeg.dll": b"plugin",
            "tools/colmap/plugins/debug.pdb": b"must not ship",
            "tools/colmap/_downloads/archive.zip": b"must not ship",
            "tools/lichtfeld-densification-plugin/densify.py": b"def build_argparser(): pass",
            "tools/lichtfeld-densification-plugin/core/pipeline.py": b"pass",
            "tools/lichtfeld-densification-plugin/third_party/dinov3/hubconf.py": b"def dinov3_vitl16(): pass",
            "tools/lichtfeld-densification-plugin/third_party/dinov3/LICENSE.md": b"DINOv3 License",
            "tools/lichtfeld-densification-plugin/.git/config": b"must not ship",
            "tools/lichtfeld-densification-plugin/__pycache__/bad.pyc": b"must not ship",
            "tools/torch-cache/model.bin": b"must not ship",
            "tools/webview2/MicrosoftEdgeWebView2RuntimeInstallerX64.exe": b"webview",
            "runtime/densify-runtime-manifest.json": b"{}",
            "runtime/pip.pyz": b"pip",
            "runtime/THIRD_PARTY_NOTICES.txt": b"NumPy BSD-3-Clause\nOpenCV Apache-2.0\n",
            "runtime/lichtfeld-studio/bin/LichtFeld-Studio.exe": b"lichtfeld",
            "runtime/lichtfeld-studio/bin/__pycache__/runtime.cpython-312.pyc": b"cache",
            "runtime/lichtfeld-studio/LICENSE": b"GPL-3.0",
            "runtime/lichtfeld-studio/share/LichtFeld-Studio/locales/en.json": b'{"language":"en"}',
            "runtime/lichtfeld-studio/share/LichtFeld-Studio/assets/rmlui/rendering.rml": b"<rml/>",
            "luts/dji-osmo360-dlogm-rec709-v1.cube": b"dji-lut",
            **{
                f"runtime/windows-x64/{name}": content
                for name, content in windows_runtime.items()
            },
            "tools/offline-wheels/metashape/numpy-1.26.4-cp39-cp39-win_amd64.whl": numpy_wheels["cp39"],
            "tools/offline-wheels/app/numpy-1.26.4-cp310-cp310-win_amd64.whl": numpy_wheels["cp310"],
            "tools/offline-wheels/app/numpy-1.26.4-cp311-cp311-win_amd64.whl": numpy_wheels["cp311"],
            "tools/offline-wheels/metashape/numpy-1.26.4-cp312-cp312-win_amd64.whl": numpy_wheels["cp312"],
            "tools/offline-wheels/metashape/opencv_python_headless-4.10.0.84-cp37-abi3-win_amd64.whl": opencv_wheel,
            "tools/offline-wheels/app/piexif-1.1.3-py2.py3-none-any.whl": b"piexif-wheel",
            "tools/offline-wheels/app/tqdm-4.68.3-py3-none-any.whl": b"tqdm-wheel",
            "requirements.txt": b"numpy==1.26.4",
            "metashape_requirements.txt": b"numpy==1.26.4",
        }
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        manifest = {
            "schemaVersion": 1,
            "runtimeVersion": "bundled-test-1",
            "platform": "windows-x86_64",
            "metashape": {
                "profiles": {
                    "cp39": ["numpy-cp39", "opencv-abi3"],
                    "cp310": ["numpy-cp310", "opencv-abi3"],
                    "cp311": ["numpy-cp311", "opencv-abi3"],
                    "cp312": ["numpy-cp312", "opencv-abi3"],
                },
                "artifacts": [
                    {
                        "id": "numpy-cp39",
                        "filename": "numpy-1.26.4-cp39-cp39-win_amd64.whl",
                        "path": "tools/offline-wheels/metashape/numpy-1.26.4-cp39-cp39-win_amd64.whl",
                        "size": len(numpy_wheels["cp39"]),
                        "sha256": hashlib.sha256(numpy_wheels["cp39"]).hexdigest(),
                        "license": "BSD-3-Clause",
                    },
                    *[
                        {
                            "id": f"numpy-{abi}",
                            "filename": f"numpy-1.26.4-{abi}-{abi}-win_amd64.whl",
                            "path": (
                                f"tools/offline-wheels/app/numpy-1.26.4-{abi}-{abi}-win_amd64.whl"
                                if abi in {"cp310", "cp311"}
                                else f"tools/offline-wheels/metashape/numpy-1.26.4-{abi}-{abi}-win_amd64.whl"
                            ),
                            "size": len(numpy_wheels[abi]),
                            "sha256": hashlib.sha256(numpy_wheels[abi]).hexdigest(),
                            "license": "BSD-3-Clause",
                        }
                        for abi in ("cp310", "cp311", "cp312")
                    ],
                    {
                        "id": "opencv-abi3",
                        "filename": "opencv_python_headless-4.10.0.84-cp37-abi3-win_amd64.whl",
                        "path": "tools/offline-wheels/metashape/opencv_python_headless-4.10.0.84-cp37-abi3-win_amd64.whl",
                        "size": len(opencv_wheel),
                        "sha256": hashlib.sha256(opencv_wheel).hexdigest(),
                        "license": "Apache-2.0",
                    },
                ],
            },
        }
        (root / "runtime/bundled-runtime-manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        (root / "runtime/windows-runtime-manifest.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "platform": "windows-x86_64",
                    "files": [
                        {
                            "name": name,
                            "size": len(content),
                            "sha256": hashlib.sha256(content).hexdigest(),
                        }
                        for name, content in windows_runtime.items()
                    ],
                }
            ),
            encoding="utf-8",
        )
        lichtfeld_root = root / "runtime" / "lichtfeld-studio"
        lichtfeld_files = []
        for path in sorted(lichtfeld_root.rglob("*")):
            if not path.is_file():
                continue
            content = path.read_bytes()
            lichtfeld_files.append(
                {
                    "path": path.relative_to(lichtfeld_root).as_posix(),
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            )
        (root / "runtime/lichtfeld-studio-manifest.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "runtime": "lichtfeld-studio",
                    "version": "0.5.3",
                    "upstreamCommit": "d8c50c6a",
                    "archive": {
                        "filename": "LichtFeld-Studio-windows-v0.5.3.zip",
                        "size": 1,
                        "sha256": "0" * 64,
                    },
                    "sentinels": [
                        "LICENSE",
                        "bin/LichtFeld-Studio.exe",
                        "share/LichtFeld-Studio/locales/en.json",
                        "share/LichtFeld-Studio/assets/rmlui/rendering.rml",
                    ],
                    "files": lichtfeld_files,
                }
            ),
            encoding="utf-8",
        )

    def make_lichtfeld_archive(self, root, archive):
        runtime = root / "runtime/lichtfeld-studio"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
            for path in sorted(runtime.rglob("*")):
                if path.is_file():
                    package.write(path, path.relative_to(runtime).as_posix())
        manifest_path = root / "runtime/lichtfeld-studio-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        content = archive.read_bytes()
        manifest["archive"] = {
            "filename": archive.name,
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def test_stage_uses_allowlist_and_writes_sorted_hash_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            stage = Path(tmp) / "stage"
            root.mkdir()
            self.make_fixture(root)
            ffmpeg = Path(tmp) / "ffmpeg.exe"
            ffprobe = Path(tmp) / "ffprobe.exe"
            webview2_loader = Path(tmp) / "WebView2Loader.dll"
            ffmpeg.write_bytes(b"ffmpeg-real")
            ffprobe.write_bytes(b"ffprobe-real")
            webview2_loader.write_bytes(b"webview-loader-real")

            manifest_path = stage_release_resources(
                root,
                stage,
                ffmpeg,
                ffprobe,
                webview2_loader,
                version="1.2.3",
            )

            self.assertTrue((stage / "binaries/python/python.exe").is_file())
            self.assertTrue((stage / "scripts/run_xpano_tracks_job.py").is_file())
            self.assertTrue((stage / "scripts/configure_environment.ps1").is_file())
            self.assertTrue((stage / "scripts/lichtfeld_training.py").is_file())
            self.assertTrue((stage / "scripts/export_image_cache.py").is_file())
            self.assertTrue((stage / "scripts/export_remap.py").is_file())
            self.assertTrue((stage / "scripts/fisheye_geometry.py").is_file())
            self.assertTrue((stage / "scripts/metashape_runtime_env.py").is_file())
            self.assertTrue((stage / "scripts/metashape_runtime_probe.py").is_file())
            self.assertTrue((stage / "scripts/metashape_pipeline.py").is_file())
            self.assertTrue((stage / "scripts/reexport_colmap_from_project.py").is_file())
            self.assertTrue((stage / "scripts/inspect_metashape_components.py").is_file())
            self.assertTrue((stage / "runtime/lichtfeld-studio/bin/LichtFeld-Studio.exe").is_file())
            self.assertTrue((stage / "luts/dji-osmo360-dlogm-rec709-v1.cube").is_file())
            self.assertTrue((stage / "tools/offline-wheels/metashape/numpy-1.26.4-cp39-cp39-win_amd64.whl").is_file())
            self.assertTrue((stage / "runtime/bundled-runtime-manifest.json").is_file())
            self.assertTrue((stage / "runtime/THIRD_PARTY_NOTICES.txt").is_file())
            self.assertTrue((stage / "tools/ffmpeg/bin/ffmpeg.exe").is_file())
            for name in WINDOWS_RUNTIME_NAMES:
                self.assertEqual(
                    (stage / "tools/colmap/bin" / name).read_bytes(),
                    f"runtime-{name}".encode("ascii"),
                )
                self.assertEqual(
                    (stage / "binaries/python" / name).read_bytes(),
                    f"runtime-{name}".encode("ascii"),
                )
            self.assertEqual((stage / "WebView2Loader.dll").read_bytes(), b"webview-loader-real")
            self.assertFalse((stage / "scripts/build_release.ps1").exists())
            self.assertFalse((stage / "tools/torch-cache").exists())
            self.assertFalse((stage / "tools/colmap/_downloads").exists())
            self.assertFalse((stage / "tools/lichtfeld-densification-plugin/.git").exists())
            self.assertFalse(any(stage.rglob("*.pyc")))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            paths = [item["path"] for item in manifest["files"]]
            self.assertEqual(manifest["version"], "1.2.3")
            self.assertEqual(paths, sorted(paths))
            self.assertIn("WebView2Loader.dll", paths)
            self.assertIn("luts/dji-osmo360-dlogm-rec709-v1.cube", paths)
            self.assertIn("tools/ffmpeg/bin/ffmpeg.exe", paths)
            self.assertTrue(all(len(item["sha256"]) == 64 for item in manifest["files"]))

    def test_stage_rejects_missing_export_acceleration_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            stage = Path(tmp) / "stage"
            root.mkdir()
            self.make_fixture(root)
            (root / "scripts/export_remap.py").unlink()
            ffmpeg = Path(tmp) / "ffmpeg.exe"
            ffprobe = Path(tmp) / "ffprobe.exe"
            webview2_loader = Path(tmp) / "WebView2Loader.dll"
            ffmpeg.write_bytes(b"ffmpeg-real")
            ffprobe.write_bytes(b"ffprobe-real")
            webview2_loader.write_bytes(b"webview-loader-real")

            with self.assertRaisesRegex(ReleaseStagingError, "export_remap.py"):
                stage_release_resources(
                    root,
                    stage,
                    ffmpeg,
                    ffprobe,
                    webview2_loader,
                    version="1.2.3",
                )

    def test_stage_rejects_missing_lichtfeld_runtime_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            stage = Path(tmp) / "stage"
            root.mkdir()
            self.make_fixture(root)
            (root / "runtime/lichtfeld-studio-manifest.json").unlink()
            ffmpeg = Path(tmp) / "ffmpeg.exe"
            ffprobe = Path(tmp) / "ffprobe.exe"
            webview2_loader = Path(tmp) / "WebView2Loader.dll"
            ffmpeg.write_bytes(b"ffmpeg-real")
            ffprobe.write_bytes(b"ffprobe-real")
            webview2_loader.write_bytes(b"webview-loader-real")

            with self.assertRaisesRegex(ReleaseStagingError, "LichtFeld runtime manifest"):
                stage_release_resources(
                    root,
                    stage,
                    ffmpeg,
                    ffprobe,
                    webview2_loader,
                    version="1.2.3",
                )

    def test_stage_rejects_corrupt_lichtfeld_dynamic_resource(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            stage = Path(tmp) / "stage"
            root.mkdir()
            self.make_fixture(root)
            (root / "runtime/lichtfeld-studio/share/LichtFeld-Studio/locales/en.json").write_bytes(
                b"corrupt"
            )
            ffmpeg = Path(tmp) / "ffmpeg.exe"
            ffprobe = Path(tmp) / "ffprobe.exe"
            webview2_loader = Path(tmp) / "WebView2Loader.dll"
            ffmpeg.write_bytes(b"ffmpeg-real")
            ffprobe.write_bytes(b"ffprobe-real")
            webview2_loader.write_bytes(b"webview-loader-real")

            with self.assertRaisesRegex(ReleaseStagingError, "source tree differs.*corrupt"):
                stage_release_resources(
                    root,
                    stage,
                    ffmpeg,
                    ffprobe,
                    webview2_loader,
                    version="1.2.3",
                )

    def test_stage_rehydrates_lichtfeld_from_the_pinned_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            stage = Path(tmp) / "stage"
            archive = Path(tmp) / "LichtFeld-Studio-windows-v0.5.3.zip"
            root.mkdir()
            self.make_fixture(root)
            self.make_lichtfeld_archive(root, archive)
            shutil.rmtree(root / "runtime/lichtfeld-studio")
            ffmpeg = Path(tmp) / "ffmpeg.exe"
            ffprobe = Path(tmp) / "ffprobe.exe"
            webview2_loader = Path(tmp) / "WebView2Loader.dll"
            ffmpeg.write_bytes(b"ffmpeg-real")
            ffprobe.write_bytes(b"ffprobe-real")
            webview2_loader.write_bytes(b"webview-loader-real")

            stage_release_resources(
                root,
                stage,
                ffmpeg,
                ffprobe,
                webview2_loader,
                version="1.2.3",
                lichtfeld_archive=archive,
            )

            self.assertEqual(
                (stage / "runtime/lichtfeld-studio/bin/LichtFeld-Studio.exe").read_bytes(),
                b"lichtfeld",
            )
            self.assertFalse(
                (stage / "runtime/lichtfeld-studio/bin/__pycache__/runtime.cpython-312.pyc").exists()
            )

    def test_stage_rejects_missing_metashape_runner_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            stage = Path(tmp) / "stage"
            root.mkdir()
            self.make_fixture(root)
            (root / "scripts/metashape_runtime_probe.py").unlink()
            ffmpeg = Path(tmp) / "ffmpeg.exe"
            ffprobe = Path(tmp) / "ffprobe.exe"
            webview2_loader = Path(tmp) / "WebView2Loader.dll"
            ffmpeg.write_bytes(b"ffmpeg-real")
            ffprobe.write_bytes(b"ffprobe-real")
            webview2_loader.write_bytes(b"webview-loader-real")

            with self.assertRaisesRegex(ReleaseStagingError, "metashape_runtime_probe.py"):
                stage_release_resources(
                    root,
                    stage,
                    ffmpeg,
                    ffprobe,
                    webview2_loader,
                    version="1.2.3",
                )

    def test_stage_rejects_missing_metashape_production_entrypoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            stage = Path(tmp) / "stage"
            root.mkdir()
            self.make_fixture(root)
            (root / "scripts/metashape_pipeline.py").unlink()
            ffmpeg = Path(tmp) / "ffmpeg.exe"
            ffprobe = Path(tmp) / "ffprobe.exe"
            webview2_loader = Path(tmp) / "WebView2Loader.dll"
            ffmpeg.write_bytes(b"ffmpeg-real")
            ffprobe.write_bytes(b"ffprobe-real")
            webview2_loader.write_bytes(b"webview-loader-real")

            with self.assertRaisesRegex(ReleaseStagingError, "metashape_pipeline.py"):
                stage_release_resources(
                    root,
                    stage,
                    ffmpeg,
                    ffprobe,
                    webview2_loader,
                    version="1.2.3",
                )

    def test_stage_rejects_missing_component_selection_dependency(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            stage = Path(tmp) / "stage"
            root.mkdir()
            self.make_fixture(root)
            (root / "scripts/component_selection.py").unlink()
            ffmpeg = Path(tmp) / "ffmpeg.exe"
            ffprobe = Path(tmp) / "ffprobe.exe"
            webview2_loader = Path(tmp) / "WebView2Loader.dll"
            ffmpeg.write_bytes(b"ffmpeg-real")
            ffprobe.write_bytes(b"ffprobe-real")
            webview2_loader.write_bytes(b"webview-loader-real")

            with self.assertRaisesRegex(ReleaseStagingError, "component_selection.py"):
                stage_release_resources(
                    root,
                    stage,
                    ffmpeg,
                    ffprobe,
                    webview2_loader,
                    version="1.2.3",
                )

    def test_stage_rejects_missing_component_inspection_entrypoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            stage = Path(tmp) / "stage"
            root.mkdir()
            self.make_fixture(root)
            (root / "scripts/inspect_metashape_components.py").unlink()
            ffmpeg = Path(tmp) / "ffmpeg.exe"
            ffprobe = Path(tmp) / "ffprobe.exe"
            loader = Path(tmp) / "WebView2Loader.dll"
            ffmpeg.write_bytes(b"ffmpeg-real")
            ffprobe.write_bytes(b"ffprobe-real")
            loader.write_bytes(b"webview-loader-real")

            with self.assertRaisesRegex(ReleaseStagingError, "inspect_metashape_components.py"):
                stage_release_resources(
                    root,
                    stage,
                    ffmpeg,
                    ffprobe,
                    loader,
                    version="1.2.3",
                )

    def test_full_offline_stage_includes_exact_densify_artifact_closure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            stage = Path(tmp) / "stage"
            root.mkdir()
            self.make_fixture(root)
            cpu = b"cpu-wheel"
            cuda = b"cuda-wheel"
            densify_manifest = {
                "schemaVersion": 1,
                "runtimeVersion": "densify-test-1",
                "platform": "windows-x86_64",
                "pythonAbi": "cp312",
                "profiles": {
                    "cpu": {"artifacts": ["cpu"]},
                    "cuda": {"artifacts": ["cuda"]},
                },
                "artifacts": [
                    {
                        "id": "cpu",
                        "filename": "cpu-1-py3-none-any.whl",
                        "size": len(cpu),
                        "sha256": hashlib.sha256(cpu).hexdigest(),
                        "urls": ["https://invalid/cpu.whl"],
                    },
                    {
                        "id": "cuda",
                        "filename": "cuda-1-py3-none-any.whl",
                        "size": len(cuda),
                        "sha256": hashlib.sha256(cuda).hexdigest(),
                        "urls": ["https://invalid/cuda.whl"],
                    },
                ],
            }
            (root / "runtime/densify-runtime-manifest.json").write_text(
                json.dumps(densify_manifest), encoding="utf-8"
            )
            artifact_root = root / "tools/offline-densify-artifacts/sha256"
            artifact_root.mkdir(parents=True)
            (artifact_root / hashlib.sha256(cpu).hexdigest()).write_bytes(cpu)
            (artifact_root / hashlib.sha256(cuda).hexdigest()).write_bytes(cuda)
            ffmpeg = root / "ffmpeg.exe"
            ffprobe = root / "ffprobe.exe"
            loader = root / "WebView2Loader.dll"
            ffmpeg.write_bytes(b"ffmpeg")
            ffprobe.write_bytes(b"ffprobe")
            loader.write_bytes(b"loader")

            stage_release_resources(
                root,
                stage,
                ffmpeg,
                ffprobe,
                loader,
                version="1.2.3",
                full_offline_artifacts=artifact_root,
            )

            bundled = stage / "runtime/densify-artifacts/sha256"
            self.assertEqual((bundled / hashlib.sha256(cpu).hexdigest()).read_bytes(), cpu)
            self.assertEqual((bundled / hashlib.sha256(cuda).hexdigest()).read_bytes(), cuda)

    def test_stage_rejects_missing_bundled_dinov3_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            stage = Path(tmp) / "stage"
            root.mkdir()
            self.make_fixture(root)
            (root / "tools/lichtfeld-densification-plugin/third_party/dinov3/hubconf.py").unlink()
            ffmpeg = Path(tmp) / "ffmpeg.exe"
            ffprobe = Path(tmp) / "ffprobe.exe"
            loader = Path(tmp) / "WebView2Loader.dll"
            ffmpeg.write_bytes(b"ffmpeg")
            ffprobe.write_bytes(b"ffprobe")
            loader.write_bytes(b"loader")

            with self.assertRaisesRegex(ReleaseStagingError, "dinov3"):
                stage_release_resources(
                    root,
                    stage,
                    ffmpeg,
                    ffprobe,
                    loader,
                    version="1.2.3",
                )

    def test_full_offline_stage_rejects_incomplete_artifact_closure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            stage = Path(tmp) / "stage"
            root.mkdir()
            self.make_fixture(root)
            payload = b"required-wheel"
            (root / "runtime/densify-runtime-manifest.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "runtimeVersion": "densify-test-1",
                        "platform": "windows-x86_64",
                        "pythonAbi": "cp312",
                        "profiles": {"cpu": {"artifacts": ["required"]}, "cuda": {"artifacts": ["required"]}},
                        "artifacts": [
                            {
                                "id": "required",
                                "filename": "required-1-py3-none-any.whl",
                                "size": len(payload),
                                "sha256": hashlib.sha256(payload).hexdigest(),
                                "urls": ["https://invalid/required.whl"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            artifact_root = root / "tools/offline-densify-artifacts/sha256"
            artifact_root.mkdir(parents=True)
            ffmpeg = root / "ffmpeg.exe"
            ffprobe = root / "ffprobe.exe"
            loader = root / "WebView2Loader.dll"
            ffmpeg.write_bytes(b"ffmpeg")
            ffprobe.write_bytes(b"ffprobe")
            loader.write_bytes(b"loader")

            with self.assertRaises(ReleaseStagingError):
                stage_release_resources(
                    root,
                    stage,
                    ffmpeg,
                    ffprobe,
                    loader,
                    version="1.2.3",
                    full_offline_artifacts=artifact_root,
                )

    def test_stage_rejects_missing_manifest_wheel(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            stage = Path(tmp) / "stage"
            root.mkdir()
            self.make_fixture(root)
            (root / "tools/offline-wheels/metashape/numpy-1.26.4-cp39-cp39-win_amd64.whl").unlink()
            ffmpeg = root / "ffmpeg.exe"
            ffprobe = root / "ffprobe.exe"
            loader = root / "WebView2Loader.dll"
            ffmpeg.write_bytes(b"ffmpeg")
            ffprobe.write_bytes(b"ffprobe")
            loader.write_bytes(b"loader")

            with self.assertRaises(ReleaseStagingError):
                stage_release_resources(root, stage, ffmpeg, ffprobe, loader, version="1.2.3")

    def test_stage_rejects_tampered_manifest_wheel(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            stage = Path(tmp) / "stage"
            root.mkdir()
            self.make_fixture(root)
            wheel = root / "tools/offline-wheels/metashape/numpy-1.26.4-cp39-cp39-win_amd64.whl"
            wheel.write_bytes(b"tampered")
            ffmpeg = root / "ffmpeg.exe"
            ffprobe = root / "ffprobe.exe"
            loader = root / "WebView2Loader.dll"
            ffmpeg.write_bytes(b"ffmpeg")
            ffprobe.write_bytes(b"ffprobe")
            loader.write_bytes(b"loader")

            with self.assertRaises(ReleaseStagingError):
                stage_release_resources(root, stage, ffmpeg, ffprobe, loader, version="1.2.3")

    def test_stage_rejects_wheel_assigned_to_the_wrong_python_abi(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            stage = Path(tmp) / "stage"
            root.mkdir()
            self.make_fixture(root)
            manifest_path = root / "runtime/bundled-runtime-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["metashape"]["profiles"]["cp310"] = ["numpy-cp39", "opencv-abi3"]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            ffmpeg = root / "ffmpeg.exe"
            ffprobe = root / "ffprobe.exe"
            loader = root / "WebView2Loader.dll"
            ffmpeg.write_bytes(b"ffmpeg")
            ffprobe.write_bytes(b"ffprobe")
            loader.write_bytes(b"loader")

            with self.assertRaises(ReleaseStagingError):
                stage_release_resources(root, stage, ffmpeg, ffprobe, loader, version="1.2.3")

    def test_stage_rejects_missing_lichtfeld_training_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            stage = Path(tmp) / "stage"
            self.make_fixture(root)
            (root / "runtime/lichtfeld-studio/bin/LichtFeld-Studio.exe").unlink()
            ffmpeg = root / "ffmpeg.exe"
            ffprobe = root / "ffprobe.exe"
            webview2_loader = root / "WebView2Loader.dll"
            ffmpeg.write_bytes(b"ffmpeg-real")
            ffprobe.write_bytes(b"ffprobe-real")
            webview2_loader.write_bytes(b"webview-loader-real")

            with self.assertRaises(ReleaseStagingError):
                stage_release_resources(root, stage, ffmpeg, ffprobe, webview2_loader, version="1.2.3")

    def test_stage_rejects_zero_byte_tool_link_instead_of_shipping_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            stage = Path(tmp) / "stage"
            root.mkdir()
            self.make_fixture(root)
            ffmpeg = Path(tmp) / "ffmpeg.exe"
            ffprobe = Path(tmp) / "ffprobe.exe"
            webview2_loader = Path(tmp) / "WebView2Loader.dll"
            ffmpeg.write_bytes(b"")
            ffprobe.write_bytes(b"ffprobe-real")
            webview2_loader.write_bytes(b"webview-loader-real")

            with self.assertRaises(ReleaseStagingError):
                stage_release_resources(
                    root,
                    stage,
                    ffmpeg,
                    ffprobe,
                    webview2_loader,
                    version="1.2.3",
                )

    def test_stage_rejects_missing_or_corrupt_windows_runtime_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            stage = Path(tmp) / "stage"
            root.mkdir()
            self.make_fixture(root)
            (root / "runtime/windows-x64/msvcp140_2.dll").write_bytes(b"tampered")
            ffmpeg = Path(tmp) / "ffmpeg.exe"
            ffprobe = Path(tmp) / "ffprobe.exe"
            webview2_loader = Path(tmp) / "WebView2Loader.dll"
            ffmpeg.write_bytes(b"ffmpeg-real")
            ffprobe.write_bytes(b"ffprobe-real")
            webview2_loader.write_bytes(b"webview-loader-real")

            with self.assertRaisesRegex(ReleaseStagingError, "windows runtime"):
                stage_release_resources(
                    root,
                    stage,
                    ffmpeg,
                    ffprobe,
                    webview2_loader,
                    version="1.2.3",
                )


if __name__ == "__main__":
    unittest.main()
