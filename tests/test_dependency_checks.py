import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.dependency_checks import (
    ExecutableCheck,
    check_executable,
    check_lfs_densify_imports,
    check_lfs_densify_runner,
    check_pipeline_dependencies,
    format_dependency_report,
    locate_colmap,
    locate_ffmpeg,
    locate_lichtfield,
    require_dependency_checks,
    resolve_executable,
)
from scripts.pipeline_core import locate_metashape


class DependencyChecksTests(unittest.TestCase):
    def test_resolves_explicit_executable_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "tool.exe"
            exe.write_bytes(b"")

            self.assertEqual(resolve_executable(str(exe), "tool.exe"), str(exe))

    def test_resolves_quoted_unicode_executable_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "Metashape 工具" / "metashape.exe"
            exe.parent.mkdir()
            exe.write_bytes(b"")

            self.assertEqual(resolve_executable(f'  "{exe}"  ', "metashape.exe"), str(exe))

    def test_invalid_explicit_metashape_environment_does_not_fall_back(self):
        missing = r"Z:\missing Metashape\metashape.exe"
        with patch.dict("scripts.pipeline_core.os.environ", {"XPANO_METASHAPE": missing, "Path": ""}, clear=False):
            self.assertEqual(locate_metashape(), missing)

    def test_locates_colmap_from_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "colmap.exe"
            exe.write_bytes(b"")

            with patch.dict("scripts.dependency_checks.os.environ", {"XPANO_COLMAP": str(exe)}, clear=False):
                self.assertEqual(locate_colmap(), str(exe))

    def test_locates_bundled_colmap_exe_before_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundled = root / "tools" / "colmap" / "bin" / "colmap.exe"
            bundled.parent.mkdir(parents=True)
            bundled.write_bytes(b"")

            with patch("scripts.dependency_checks.shutil.which", return_value=r"C:\Tools\colmap.exe"):
                self.assertEqual(locate_colmap(project_root=root), str(bundled))

    def test_locates_nested_bundled_colmap_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundled = root / "tools" / "colmap" / "colmap-x64-windows-nocuda" / "bin" / "colmap.exe"
            bundled.parent.mkdir(parents=True)
            bundled.write_bytes(b"")

            with patch("scripts.dependency_checks.shutil.which", return_value=None):
                self.assertEqual(locate_colmap(project_root=root), str(bundled))

    def test_locates_portable_internal_bundled_colmap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundled = root / "_internal" / "tools" / "colmap" / "bin" / "colmap.exe"
            bundled.parent.mkdir(parents=True)
            bundled.write_bytes(b"")

            with patch("scripts.dependency_checks.shutil.which", return_value=None):
                self.assertEqual(locate_colmap(project_root=root), str(bundled))

    def test_locates_bundled_ffmpeg_before_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundled = root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
            bundled.parent.mkdir(parents=True)
            bundled.write_bytes(b"")

            with patch("scripts.runtime_paths.shutil.which", return_value=r"C:\Tools\ffmpeg.exe"):
                self.assertEqual(locate_ffmpeg(root=root), str(bundled))

    def test_locates_lichtfield_from_path(self):
        with patch("scripts.dependency_checks.shutil.which", return_value=r"C:\Tools\lichtfield-studio.exe"):
            self.assertEqual(locate_lichtfield(), r"C:\Tools\lichtfield-studio.exe")

    def test_reports_missing_required_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("scripts.dependency_checks._project_root", return_value=Path(tmp)), \
                patch("scripts.dependency_checks.shutil.which", return_value=None):
                check = check_executable("COLMAP", "colmap", "colmap", required=True)

        self.assertFalse(check.ok)
        self.assertTrue(check.required)
        self.assertIn("PATH", check.message)

    def test_resolve_colmap_command_can_use_bundled_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundled = root / "tools" / "colmap" / "bin" / "colmap.exe"
            bundled.parent.mkdir(parents=True)
            bundled.write_bytes(b"")

            with patch("scripts.dependency_checks._project_root", return_value=root), \
                patch("scripts.dependency_checks.shutil.which", return_value=None):
                self.assertEqual(resolve_executable("colmap", "colmap"), str(bundled))

    def test_skips_optional_executable(self):
        check = check_executable("Metashape", "metashape.exe", "metashape.exe", required=False)

        self.assertTrue(check.ok)
        self.assertFalse(check.required)

    def test_colmap_lichtfield_dependency_requirements(self):
        def fake_which(command):
            suffix = "" if str(command).lower().endswith(".exe") else ".exe"
            return f"C:/Tools/{command}{suffix}"

        with patch("scripts.dependency_checks.shutil.which", side_effect=fake_which):
            checks = check_pipeline_dependencies(
                backend="colmap",
                metashape_exe="missing-metashape.exe",
                colmap_exe="colmap",
                lichtfield_exe="lichtfield-studio",
                run_lichtfield=True,
            )

        by_name = {check.name: check for check in checks}
        self.assertFalse(by_name["Metashape"].required)
        self.assertTrue(by_name["COLMAP"].required)
        self.assertTrue(by_name["LICHT Field Studio"].required)
        require_dependency_checks(checks)

    def test_lfs_densification_checks_python_and_imports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = root / "plugin"
            python_exe = root / "python.exe"
            plugin.mkdir()
            (plugin / "densify.py").write_text("print('ok')", encoding="utf-8")
            python_exe.write_bytes(b"")

            with patch("scripts.dependency_checks.shutil.which", return_value="C:/Tools/tool.exe"), \
                patch("scripts.dependency_checks.check_lfs_densify_imports") as import_check:
                import_check.return_value = ExecutableCheck(
                    name="LichtFeld densification dependencies",
                    requested=str(python_exe),
                    required=True,
                    ok=True,
                    resolved=str(python_exe),
                )
                with patch("scripts.dependency_checks.check_lfs_densify_runner") as runner_check:
                    runner_check.return_value = ExecutableCheck(
                        name="LichtFeld densification runner",
                        requested=str(plugin),
                        required=True,
                        ok=True,
                        resolved=str(plugin),
                    )
                    checks = check_pipeline_dependencies(
                        backend="colmap",
                        colmap_exe="colmap",
                        run_lfs_densify=True,
                        lfs_densify_python=str(python_exe),
                        lfs_densify_plugin=str(plugin),
                    )

        by_name = {check.name: check for check in checks}
        self.assertTrue(by_name["LichtFeld densification plugin"].ok)
        self.assertTrue(by_name["LichtFeld densification Python"].ok)
        self.assertTrue(by_name["LichtFeld densification dependencies"].ok)
        self.assertTrue(by_name["LichtFeld densification runner"].ok)

    def test_lfs_densification_uses_bundled_xpano_runner_in_frozen_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = root / "plugin"
            xpano_exe = root / "xPano.exe"
            plugin.mkdir()
            (plugin / "densify.py").write_text("print('ok')", encoding="utf-8")
            xpano_exe.write_bytes(b"")

            with patch("scripts.dependency_checks.shutil.which", return_value="C:/Tools/tool.exe"), \
                patch("scripts.dependency_checks.locate_densify_plugin", return_value=plugin), \
                patch("scripts.dependency_checks.should_use_bundled_densify_runner", return_value=True), \
                patch("scripts.dependency_checks.sys.executable", str(xpano_exe)), \
                patch("scripts.dependency_checks.sys.frozen", True, create=True), \
                patch("scripts.dependency_checks.check_lfs_densify_imports") as import_check, \
                patch("scripts.dependency_checks.check_lfs_densify_runner") as runner_check:
                import_check.return_value = ExecutableCheck(
                    name="LichtFeld densification dependencies",
                    requested=str(xpano_exe),
                    required=True,
                    ok=True,
                    resolved=str(xpano_exe),
                )
                runner_check.return_value = ExecutableCheck(
                    name="LichtFeld densification runner",
                    requested=str(plugin),
                    required=True,
                    ok=True,
                    resolved=str(plugin),
                )

                checks = check_pipeline_dependencies(
                    backend="colmap",
                    colmap_exe="colmap",
                    run_lfs_densify=True,
                )

        by_name = {check.name: check for check in checks}
        self.assertTrue(by_name["LichtFeld densification Python"].ok)
        self.assertIn("--run-lfs-densify-standalone", by_name["LichtFeld densification Python"].resolved)
        import_check.assert_called_once_with(None)
        runner_check.assert_called_once_with(None, plugin)

    def test_lfs_densification_ignores_stale_python_path_in_frozen_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = root / "plugin"
            xpano_exe = root / "xPano.exe"
            plugin.mkdir()
            (plugin / "densify.py").write_text("print('ok')", encoding="utf-8")
            xpano_exe.write_bytes(b"")

            with patch("scripts.dependency_checks.shutil.which", return_value="C:/Tools/tool.exe"), \
                patch("scripts.dependency_checks.locate_densify_plugin", return_value=plugin), \
                patch("scripts.dependency_checks.should_use_bundled_densify_runner", return_value=True), \
                patch("scripts.dependency_checks.sys.executable", str(xpano_exe)), \
                patch("scripts.dependency_checks.check_lfs_densify_imports") as import_check, \
                patch("scripts.dependency_checks.check_lfs_densify_runner") as runner_check:
                import_check.return_value = ExecutableCheck(
                    name="LichtFeld densification dependencies",
                    requested=str(xpano_exe),
                    required=True,
                    ok=True,
                    resolved=str(xpano_exe),
                )
                runner_check.return_value = ExecutableCheck(
                    name="LichtFeld densification runner",
                    requested=str(plugin),
                    required=True,
                    ok=True,
                    resolved=str(plugin),
                )

                checks = check_pipeline_dependencies(
                    backend="colmap",
                    colmap_exe="colmap",
                    run_lfs_densify=True,
                    lfs_densify_python=r"Z:\old-xpano-build\.venv-densify\Scripts\python.exe",
                )

        by_name = {check.name: check for check in checks}
        self.assertEqual(by_name["LichtFeld densification Python"].requested, str(xpano_exe))
        self.assertIn("--run-lfs-densify-standalone", by_name["LichtFeld densification Python"].resolved)
        import_check.assert_called_once_with(None)
        runner_check.assert_called_once_with(None, plugin)

    def test_lfs_import_check_reports_missing_dependency(self):
        result = type("Result", (), {"returncode": 1, "stdout": "", "stderr": "No module named pycolmap"})()

        with patch("scripts.dependency_checks.subprocess.run", return_value=result):
            check = check_lfs_densify_imports("python.exe")

        self.assertFalse(check.ok)
        self.assertIn("pycolmap", check.message)

    def test_lfs_runner_check_requires_plugin_cli_help(self):
        result = type("Result", (), {"returncode": 0, "stdout": "--scene_root\n--roma_setting\n", "stderr": ""})()

        with patch("scripts.dependency_checks.subprocess.run", return_value=result):
            check = check_lfs_densify_runner("python.exe", Path("plugin"))

        self.assertTrue(check.ok)

    def test_formats_failure_report(self):
        with patch("scripts.dependency_checks.shutil.which", return_value=None):
            checks = check_pipeline_dependencies(backend="metashape", metashape_exe="metashape.exe")

        report = format_dependency_report(checks)
        self.assertIn("MISSING", report)
        self.assertIn("ffmpeg", report)


if __name__ == "__main__":
    unittest.main()
