import hashlib
import importlib
import json
import os
import subprocess
import sys
import zipfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.runtime_bootstrap import BootstrapError, bootstrap_local_runtime
from scripts.runtime_readiness import (
    RuntimeReadinessError,
    ensure_metashape_runtime,
    load_bundled_runtime_manifest,
    main,
    metashape_python,
    metashape_profile,
    probe_lichtfeld_training,
    probe_bundled_resources,
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


class RuntimeReadinessTests(unittest.TestCase):
    def write_lichtfeld_runtime(self, root):
        runtime = root / "runtime" / "lichtfeld-studio"
        files = {
            "LICENSE": b"GPL-3.0",
            "bin/LichtFeld-Studio.exe": b"lichtfeld",
            "bin/lfs_core.dll": b"core",
            "bin/lfs_visualizer.dll": b"visualizer",
            "bin/vulkan-1.dll": b"vulkan",
            "bin/lichtfeld/py.typed": b"",
            "share/LichtFeld-Studio/locales/en.json": b'{"language":"en"}',
            "share/LichtFeld-Studio/assets/rmlui/rendering.rml": b"<rml />",
            "share/LichtFeld-Studio/assets/rmlui/scene_tree.rml": b"<rml />",
        }
        records = []
        for relative, content in files.items():
            path = runtime / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            records.append({"path": relative, "size": len(content), "sha256": digest(content)})
        manifest = {
            "schemaVersion": 1,
            "runtime": "lichtfeld-studio",
            "version": "0.5.3",
            "sentinels": sorted(files),
            "files": records,
        }
        manifest_path = root / "runtime" / "lichtfeld-studio-manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return runtime / "bin" / "LichtFeld-Studio.exe"

    def test_lichtfeld_training_probe_checks_sentinels_gpu_dataset_and_output_before_training(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = self.write_lichtfeld_runtime(root)
            dataset = root / "dataset"
            (dataset / "images").mkdir(parents=True)
            for name in ("cameras.bin", "images.bin", "points3D.bin"):
                path = dataset / "sparse" / "0" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"colmap")
            output = root / "project" / "work" / "training" / "runs"

            with patch("scripts.runtime_readiness._probe_lfs_version", return_value="LichtFeld Studio v0.5.3"), patch(
                "scripts.runtime_readiness.probe_cuda_device", return_value={"status": "ready", "deviceCount": 1}
            ), patch(
                "scripts.runtime_readiness.probe_vulkan_device", return_value={"status": "ready", "deviceCount": 1}
            ):
                result = probe_lichtfeld_training(root, executable, root / "profile", dataset, output)

            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["version"], "0.5.3")
            self.assertEqual(result["cuda"]["deviceCount"], 1)
            self.assertEqual(result["vulkan"]["deviceCount"], 1)
            self.assertTrue(output.is_dir())

    def test_lichtfeld_training_probe_fails_with_a_stable_code_when_a_gui_resource_is_tampered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = self.write_lichtfeld_runtime(root)
            (root / "runtime" / "lichtfeld-studio" / "share" / "LichtFeld-Studio" / "assets" / "rmlui" / "rendering.rml").write_bytes(b"tampered")

            with self.assertRaises(RuntimeReadinessError) as raised:
                probe_lichtfeld_training(root, executable, root / "profile", root / "dataset", root / "output")

            self.assertEqual(raised.exception.code, "LFS_RUNTIME_CORRUPT")

    def test_lichtfeld_training_probe_reports_invalid_dataset_without_hiding_a_ready_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = self.write_lichtfeld_runtime(root)

            with patch("scripts.runtime_readiness._probe_lfs_version", return_value="LichtFeld Studio v0.5.3"), patch(
                "scripts.runtime_readiness.probe_cuda_device", return_value={"status": "ready", "deviceCount": 1}
            ), patch(
                "scripts.runtime_readiness.probe_vulkan_device", return_value={"status": "ready", "deviceCount": 1}
            ):
                result = probe_lichtfeld_training(root, executable, root / "profile", root / "missing-dataset", root / "output")

            self.assertEqual(result["status"], "not_ready")
            self.assertEqual(result["dataset"]["code"], "TRAINING_DATASET_INVALID")
            self.assertEqual(result["cuda"]["status"], "ready")

    def test_lichtfeld_probe_command_emits_a_structured_result(self):
        emitted = []
        with patch(
            "scripts.runtime_readiness.probe_lichtfeld_training",
            return_value={
                "status": "ready",
                "version": "0.5.3",
                "cuda": {"status": "ready", "deviceCount": 1},
                "vulkan": {"status": "ready", "deviceCount": 1},
            },
        ) as probe, patch("scripts.runtime_readiness._emit", side_effect=lambda prefix, payload: emitted.append((prefix, payload))):
            result = main([
                "lichtfeld-probe",
                "--root",
                "C:/xPano",
                "--state-root",
                "C:/state",
                "--backend",
                "colmap",
                "--lfs-executable",
                "C:/xPano/runtime/lichtfeld-studio/bin/LichtFeld-Studio.exe",
                "--profile-root",
                "C:/xPano-profile",
                "--dataset",
                "C:/project/dataset",
                "--output",
                "C:/project/work/training/runs",
            ])

        self.assertEqual(result, 0)
        self.assertEqual(emitted[-1][0], "LFS_READINESS_RESULT:")
        self.assertEqual(emitted[-1][1]["version"], "0.5.3")
        probe.assert_called_once()

    def test_bare_metashape_command_is_resolved_from_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / "Metashape 工具" / "metashape.exe"
            python = executable.parent / "python" / "python.exe"
            python.parent.mkdir(parents=True)
            executable.write_bytes(b"exe")
            python.write_bytes(b"python")

            with patch("shutil.which", return_value=str(executable)):
                resolved_executable, resolved_python = metashape_python('  "metashape.exe"  ')

            self.assertEqual(resolved_executable, executable.resolve())
            self.assertEqual(resolved_python, python.resolve())

    def test_entrypoint_imports_sibling_modules_without_repository_pythonpath(self):
        script = Path(__file__).resolve().parents[1] / "scripts/runtime_readiness.py"
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [sys.executable, str(script), "--help"],
                cwd=tmp,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_bundled_resource_probe_executes_every_external_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = {
                "python": root / "binaries/python/python.exe",
                "ffmpeg": root / "tools/ffmpeg/bin/ffmpeg.exe",
                "ffprobe": root / "tools/ffmpeg/bin/ffprobe.exe",
                "colmap": root / "tools/colmap/bin/colmap.exe",
                "lichtfeld": root / "runtime/lichtfeld-studio/bin/LichtFeld-Studio.exe",
                "pip": root / "runtime/pip.pyz",
                "manifest": root / "runtime/bundled-runtime-manifest.json",
            }
            for path in paths.values():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"runtime")
            python_ok = subprocess.CompletedProcess([], 0, "ok", "")

            with patch("scripts.runtime_readiness._run_python", return_value=python_ok), patch(
                "scripts.runtime_readiness._probe_tool",
                side_effect=[(True, ""), (True, ""), (False, "missing MSVCP140.dll"), (True, "")],
            ) as probe:
                resources = probe_bundled_resources(root)

            self.assertEqual(resources["colmap"]["status"], "corrupt")
            self.assertIn("MSVCP140.dll", resources["colmap"]["detail"])
            self.assertEqual(
                [call.args[1:] for call in probe.call_args_list],
                [
                    (["-version"], "ffmpeg version"),
                    (["-version"], "ffprobe version"),
                    (["-h"], "COLMAP"),
                    (["--version"], "LichtFeld Studio v"),
                ],
            )

    def test_missing_windows_media_foundation_is_reported_before_opencv_import(self):
        from scripts.runtime_readiness import _missing_media_foundation_dlls

        with patch("scripts.runtime_readiness.os.name", "nt"), patch.dict(
            "scripts.runtime_readiness.os.environ", {"SystemRoot": r"C:\Windows"}, clear=True
        ), patch("scripts.runtime_readiness.Path.is_file", return_value=False):
            missing = _missing_media_foundation_dlls()

        self.assertEqual(missing, ["MFPlat.dll", "MF.dll", "MFReadWrite.dll"])

    def write_manifest(self, root, wheel_data=b"wheel"):
        wheel = root / "wheels" / "numpy-1-cp39-cp39-win_amd64.whl"
        wheel.parent.mkdir(parents=True)
        wheel.write_bytes(wheel_data)
        manifest = root / "bundled-runtime-manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "runtimeVersion": "bundle-test-1",
                    "platform": "windows-x86_64",
                    "metashape": {
                        "profiles": {"cp39": ["numpy-cp39"]},
                        "artifacts": [
                            {
                                "id": "numpy-cp39",
                                "filename": wheel.name,
                                "path": "wheels/" + wheel.name,
                                "size": len(wheel_data),
                                "sha256": digest(wheel_data),
                                "license": "BSD-3-Clause",
                            }
                        ],
                    },
                }
            ),
            encoding="utf-8",
        )
        return manifest, wheel

    def test_manifest_selects_exact_supported_abi_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path, wheel = self.write_manifest(root)

            manifest = load_bundled_runtime_manifest(manifest_path, root)
            selected = metashape_profile(manifest, "cp39")

            self.assertEqual([item["id"] for item in selected], ["numpy-cp39"])
            self.assertEqual(selected[0]["source"], wheel.resolve())

    def test_bundled_pip_is_a_python39_compatible_zipapp(self):
        pip_pyz = Path(__file__).resolve().parents[1] / "runtime/pip.pyz"
        with zipfile.ZipFile(pip_pyz) as archive:
            self.assertIn("__main__.py", archive.namelist())
            metadata_name = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
            metadata = archive.read(metadata_name).decode("utf-8")

        self.assertIn("Requires-Python: >=3.8", metadata)

    def test_manifest_rejects_unsupported_abi_without_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path, _wheel = self.write_manifest(root)
            manifest = load_bundled_runtime_manifest(manifest_path, root)

            with self.assertRaises(RuntimeReadinessError) as raised:
                metashape_profile(manifest, "cp38")

            self.assertEqual(raised.exception.code, "UNSUPPORTED_ABI")

    def test_local_runtime_activates_only_after_probe_and_reuses_valid_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wheel = root / "demo-1-py3-none-any.whl"
            wheel.write_bytes(b"wheel")
            artifact = {
                "id": "demo",
                "filename": wheel.name,
                "source": wheel,
                "size": wheel.stat().st_size,
                "sha256": digest(b"wheel"),
            }
            installs = []

            def install(wheels, site_packages):
                installs.append([item.name for item in wheels])
                site_packages.mkdir(parents=True)
                (site_packages / "ready.txt").write_text("ok", encoding="utf-8")

            def probe(site_packages):
                return (site_packages / "ready.txt").is_file()

            result = bootstrap_local_runtime(
                runtime_name="metashape",
                runtime_id="meta-cp39-v1",
                runtime_version="v1",
                state_root=root / "state-root",
                artifacts=[artifact],
                install=install,
                probe=probe,
            )
            repeated = bootstrap_local_runtime(
                runtime_name="metashape",
                runtime_id="meta-cp39-v1",
                runtime_version="v1",
                state_root=root / "state-root",
                artifacts=[artifact],
                install=install,
                probe=probe,
            )

            active = json.loads((root / "state-root/state/active-metashape.json").read_text(encoding="utf-8"))
            self.assertEqual(active["runtimePath"], result["runtimePath"])
            self.assertTrue(repeated["reused"])
            self.assertEqual(len(installs), 1)

    def test_failed_local_runtime_preserves_previous_active_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_root = root / "state-root"
            previous = state_root / "runtimes/metashape/previous"
            previous.mkdir(parents=True)
            active_path = state_root / "state/active-metashape.json"
            active_path.parent.mkdir(parents=True)
            active_payload = {"runtimeId": "previous", "runtimePath": str(previous)}
            active_path.write_text(json.dumps(active_payload), encoding="utf-8")
            wheel = root / "demo-1-py3-none-any.whl"
            wheel.write_bytes(b"wheel")
            artifact = {
                "id": "demo",
                "filename": wheel.name,
                "source": wheel,
                "size": wheel.stat().st_size,
                "sha256": digest(b"wheel"),
            }

            with self.assertRaises(BootstrapError):
                bootstrap_local_runtime(
                    runtime_name="metashape",
                    runtime_id="broken",
                    runtime_version="v2",
                    state_root=state_root,
                    artifacts=[artifact],
                    install=lambda _wheels, site: site.mkdir(parents=True),
                    probe=lambda _site: False,
                )

            self.assertEqual(json.loads(active_path.read_text(encoding="utf-8")), active_payload)
            self.assertFalse((state_root / "runtimes/metashape/broken").exists())

    def test_local_runtime_fails_before_install_when_disk_budget_is_insufficient(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wheel = root / "demo-1-py3-none-any.whl"
            wheel.write_bytes(b"wheel")
            artifact = {
                "id": "demo",
                "filename": wheel.name,
                "source": wheel,
                "size": wheel.stat().st_size,
                "sha256": digest(b"wheel"),
            }

            with self.assertRaises(BootstrapError) as raised:
                bootstrap_local_runtime(
                    runtime_name="metashape",
                    runtime_id="meta-cp39-v1",
                    runtime_version="v1",
                    state_root=root / "state-root",
                    artifacts=[artifact],
                    install=lambda _wheels, _site: self.fail("install must not run"),
                    probe=lambda _site: False,
                    available_disk_bytes=1,
                )

            self.assertEqual(raised.exception.code, "DISK_FULL")

    def test_metashape_runtime_uses_the_real_runner_before_accepting_native_imports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / "Metashape" / "metashape.exe"
            python = executable.parent / "python" / "python.exe"
            python.parent.mkdir(parents=True)
            executable.write_bytes(b"exe")
            python.write_bytes(b"python")

            with patch("scripts.runtime_readiness.load_bundled_runtime_manifest", return_value={"runtimeVersion": "bundle-test-1"}), patch(
                "scripts.runtime_readiness.metashape_python", return_value=(executable, python)
            ), patch("scripts.runtime_readiness.python_abi", return_value="cp39"), patch(
                "scripts.runtime_readiness.metashape_profile", return_value=[]
            ), patch("scripts.runtime_readiness._probe_metashape_runtime", return_value=(True, "")) as probe:
                result = ensure_metashape_runtime(root, root / "state", executable)

            self.assertEqual(result["source"], "metashape")
            self.assertEqual(result["sitePackages"], "")
            probe.assert_called_once_with(executable, root)

    def test_metashape_runtime_provisions_wheels_until_the_real_runner_imports_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / "Metashape" / "metashape.exe"
            python = executable.parent / "python" / "python.exe"
            site_packages = root / "state" / "runtimes" / "metashape" / "runtime" / "site-packages"
            python.parent.mkdir(parents=True)
            executable.write_bytes(b"exe")
            python.write_bytes(b"python")
            bootstrap_result = {
                "runtimeId": "runtime",
                "runtimePath": str(site_packages.parent),
                "sitePackages": str(site_packages),
                "reused": False,
            }

            def bootstrap_with_runner_probe(*_args, **kwargs):
                self.assertTrue(kwargs["probe"](site_packages))
                return bootstrap_result

            with patch("scripts.runtime_readiness.load_bundled_runtime_manifest", return_value={"runtimeVersion": "bundle-test-1"}), patch(
                "scripts.runtime_readiness.metashape_python", return_value=(executable, python)
            ), patch("scripts.runtime_readiness.python_abi", return_value="cp39"), patch(
                "scripts.runtime_readiness.metashape_profile", return_value=[]
            ), patch("scripts.runtime_readiness._probe_metashape_runtime", side_effect=[(False, "native runtime is missing NumPy"), (True, "")]) as probe, patch(
                "scripts.runtime_readiness.bootstrap_local_runtime", side_effect=bootstrap_with_runner_probe
            ) as bootstrap:
                result = ensure_metashape_runtime(root, root / "state", executable)

            self.assertEqual(result["source"], "xpano")
            self.assertEqual(result["sitePackages"], str(site_packages))
            self.assertEqual(probe.call_args_list, [
                unittest.mock.call(executable, root),
                unittest.mock.call(executable, root, site_packages),
            ])
            self.assertTrue(callable(bootstrap.call_args.kwargs["probe"]))

    def test_real_runner_probe_uses_the_same_isolated_dependency_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / "Metashape" / "metashape.exe"
            site_packages = root / "runtime" / "site-packages"
            site_packages.mkdir(parents=True)
            (site_packages / "numpy.libs").mkdir()
            (site_packages / "cv2").mkdir()
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"exe")
            probe_output = "XPANO_METASHAPE_RUNTIME_READY:{\"numpy\": \"1.26.4\"}\n"
            completed = subprocess.CompletedProcess([], 0, probe_output, "")

            with patch("scripts.runtime_readiness.subprocess.run", return_value=completed) as run:
                from scripts.runtime_readiness import probe_metashape_runtime

                self.assertTrue(probe_metashape_runtime(executable, root, site_packages))

            args, kwargs = run.call_args
            self.assertEqual(args[0], [
                str(executable),
                "-r",
                str(root / "scripts" / "metashape_runtime_probe.py"),
                "--xpano-site-packages",
                str(site_packages),
            ])
            self.assertEqual(kwargs["env"]["PYTHONPATH"].split(os.pathsep)[0], str(site_packages))
            self.assertEqual(kwargs["env"]["PATH"].split(os.pathsep)[:3], [
                str(site_packages / "numpy.libs"),
                str(site_packages / "cv2"),
                str(site_packages),
            ])

    def test_probe_command_reports_the_real_metashape_runner_status(self):
        resources = {"python": {"status": "ready", "path": "python.exe"}}
        metashape = Path("C:/Metashape/metashape.exe")
        python = Path("C:/Metashape/python/python.exe")
        emitted = []

        with patch("scripts.runtime_readiness.probe_bundled_resources", return_value=resources), patch(
            "scripts.runtime_readiness.metashape_python", return_value=(metashape, python)
        ), patch("scripts.runtime_readiness.python_abi", return_value="cp39"), patch(
            "scripts.runtime_readiness.load_bundled_runtime_manifest", return_value={}
        ), patch("scripts.runtime_readiness.metashape_profile", return_value=[]), patch(
            "scripts.runtime_readiness.probe_metashape_runtime", return_value=False
        ) as probe, patch("scripts.runtime_readiness._emit", side_effect=lambda _prefix, payload: emitted.append(payload)):
            result = main([
                "probe",
                "--root",
                "C:/xPano",
                "--state-root",
                "C:/state",
                "--backend",
                "metashape",
                "--metashape",
                str(metashape),
            ])

        self.assertEqual(result, 0)
        self.assertEqual(emitted[-1]["metashape"]["status"], "dependencies_missing")
        probe.assert_called_once_with(metashape, Path("C:/xPano"))

    def test_script_local_activation_imports_runtime_without_pythonpath(self):
        from scripts import metashape_runtime_env

        module_name = "xpano_runner_only_dependency"
        original_path = list(sys.path)
        original_runtime = os.environ.get("XPANO_METASHAPE_SITE_PACKAGES")
        original_pythonpath = os.environ.get("PYTHONPATH")
        original_module = sys.modules.pop(module_name, None)
        with tempfile.TemporaryDirectory() as tmp:
            site_packages = Path(tmp) / "site-packages"
            (site_packages / "numpy.libs").mkdir(parents=True)
            (site_packages / "cv2").mkdir()
            (site_packages / f"{module_name}.py").write_text("VALUE = 'runner-runtime'\n", encoding="utf-8")
            try:
                sys.path[:] = [item for item in sys.path if Path(item or ".").resolve() != site_packages.resolve()]
                os.environ["XPANO_METASHAPE_SITE_PACKAGES"] = str(site_packages)
                os.environ.pop("PYTHONPATH", None)
                with patch.object(metashape_runtime_env.os, "add_dll_directory", return_value=object()) as add_dll:
                    activated = metashape_runtime_env.activate_metashape_runtime()
                    imported = importlib.import_module(module_name)

                self.assertEqual(activated, site_packages.resolve())
                self.assertEqual(imported.VALUE, "runner-runtime")
                self.assertEqual(sys.path[0], str(site_packages.resolve()))
                self.assertEqual(
                    add_dll.call_args_list,
                    [
                        unittest.mock.call(str(site_packages / "numpy.libs")),
                        unittest.mock.call(str(site_packages / "cv2")),
                    ],
                )
            finally:
                sys.path[:] = original_path
                if original_runtime is None:
                    os.environ.pop("XPANO_METASHAPE_SITE_PACKAGES", None)
                else:
                    os.environ["XPANO_METASHAPE_SITE_PACKAGES"] = original_runtime
                if original_pythonpath is None:
                    os.environ.pop("PYTHONPATH", None)
                else:
                    os.environ["PYTHONPATH"] = original_pythonpath
                sys.modules.pop(module_name, None)
                if original_module is not None:
                    sys.modules[module_name] = original_module

    def test_probe_activates_explicit_runtime_when_runner_strips_custom_environment(self):
        scripts = Path(__file__).resolve().parents[1] / "scripts"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            site_packages = root / "external runtime" / "site-packages"
            (site_packages / "numpy.libs").mkdir(parents=True)
            (site_packages / "cv2").mkdir()
            (site_packages / "cv2" / "__init__.py").write_text("__version__ = '4.10-test'\n", encoding="utf-8")
            (site_packages / "numpy").mkdir()
            (site_packages / "numpy" / "__init__.py").write_text("__version__ = '1.26-test'\n", encoding="utf-8")
            (site_packages / "Metashape.py").write_text(
                "class App:\n    version = 'runner-test'\napp = App()\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.pop("PYTHONPATH", None)
            env.pop("XPANO_METASHAPE_SITE_PACKAGES", None)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(scripts / "metashape_runtime_probe.py"),
                    "--xpano-site-packages",
                    str(site_packages),
                ],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertIn("XPANO_METASHAPE_RUNTIME_READY:", completed.stdout)
            payload = json.loads(completed.stdout.split("XPANO_METASHAPE_RUNTIME_READY:", 1)[1])
            self.assertEqual(
                Path(payload["numpyPath"]).resolve().parents[1],
                site_packages.resolve(),
            )
            self.assertEqual(
                Path(payload["opencvPath"]).resolve().parents[1],
                site_packages.resolve(),
            )

    def test_metashape_scripts_activate_runtime_before_optional_imports(self):
        scripts = Path(__file__).resolve().parents[1] / "scripts"
        probe = (scripts / "metashape_runtime_probe.py").read_text(encoding="utf-8")
        pipeline = (scripts / "metashape_pipeline.py").read_text(encoding="utf-8")
        reexport = (scripts / "reexport_colmap_from_project.py").read_text(encoding="utf-8")

        self.assertLess(probe.index("activate_metashape_runtime("), probe.index("import cv2"))
        self.assertLess(pipeline.index("activate_metashape_runtime("), pipeline.index("import export_colmap"))
        self.assertLess(reexport.index("activate_metashape_runtime("), reexport.index("import export_colmap"))


if __name__ == "__main__":
    unittest.main()
