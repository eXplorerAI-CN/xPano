import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class DensifyRuntimeEnvironmentTests(unittest.TestCase):
    def test_explicit_runtime_path_activates_without_pythonpath_and_retains_dll_handles(self):
        from scripts import densify_runtime_env

        original_path = list(sys.path)
        original_environment = os.environ.copy()
        densify_runtime_env._DLL_DIRECTORY_HANDLES.clear()
        densify_runtime_env._REGISTERED_DLL_DIRECTORIES.clear()
        with tempfile.TemporaryDirectory() as tmp:
            site_packages = Path(tmp) / "external runtime" / "site-packages"
            for relative in ["torch/lib", "open3d", "numpy.libs", "pycolmap.libs"]:
                (site_packages / relative).mkdir(parents=True)
            try:
                os.environ.pop("PYTHONPATH", None)
                with patch.object(
                    densify_runtime_env.os,
                    "add_dll_directory",
                    side_effect=[object(), object(), object(), object()],
                    create=True,
                ) as add_dll:
                    activated = densify_runtime_env.activate_densify_runtime(site_packages)
                    repeated = densify_runtime_env.activate_densify_runtime(site_packages)

                self.assertEqual(activated, site_packages.resolve())
                self.assertEqual(repeated, site_packages.resolve())
                self.assertEqual(sys.path[0], str(site_packages.resolve()))
                self.assertEqual(len(densify_runtime_env._DLL_DIRECTORY_HANDLES), 4)
                self.assertEqual(add_dll.call_count, 4)
                self.assertEqual(os.environ["PYTHONNOUSERSITE"], "1")
            finally:
                sys.path[:] = original_path
                os.environ.clear()
                os.environ.update(original_environment)
                densify_runtime_env._DLL_DIRECTORY_HANDLES.clear()
                densify_runtime_env._REGISTERED_DLL_DIRECTORIES.clear()

    def test_runtime_path_is_parsed_from_explicit_cli_argument(self):
        from scripts.densify_runtime_env import densify_site_packages_from_argv

        self.assertEqual(
            densify_site_packages_from_argv(
                ["--xpano-site-packages", r"C:\Users\Test User\xPano\site-packages"]
            ),
            r"C:\Users\Test User\xPano\site-packages",
        )
        with self.assertRaisesRegex(RuntimeError, "requires a directory"):
            densify_site_packages_from_argv(["--xpano-site-packages"])


if __name__ == "__main__":
    unittest.main()
