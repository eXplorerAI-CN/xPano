import unittest
from pathlib import Path

from scripts.windows_dll_closure import is_windows_inbox_dll, resolve_dependency_closure


class WindowsPackagingTests(unittest.TestCase):
    def test_webview2_probe_checks_both_registry_views_and_rechecks_after_install(self):
        hook = (
            Path(__file__).parents[1]
            / "xpano-ui"
            / "src-tauri"
            / "windows"
            / "nsis-hooks.nsh"
        ).read_text(encoding="utf-8")

        self.assertIn("SetRegView 64", hook)
        self.assertIn("SetRegView 32", hook)
        self.assertIn("webview_recheck:", hook)
        self.assertGreaterEqual(
            hook.count(
                'ReadRegStr $0 HKLM "Software\\Microsoft\\EdgeUpdate\\Clients\\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"'
            ),
            4,
        )
        self.assertIn('!insertmacro XPANO_REMOVE_DIR "$INSTDIR"', hook)
        self.assertIn("IfSilent preserve_densify_runtime", hook)
        self.assertIn("MessageBox MB_YESNO|MB_ICONQUESTION", hook)
        self.assertIn('!insertmacro XPANO_REMOVE_DIR "$LOCALAPPDATA\\com.xpano.app\\EBWebView"', hook)
        self.assertIn("preserve_densify_runtime:", hook)
        self.assertNotIn("NSIS_HOOK_PREINSTALL", hook)
        self.assertNotIn("StrCpy $INSTDIR", hook)

    def test_release_bundle_installs_the_webview_loader_next_to_the_application(self):
        root = Path(__file__).parents[1]
        config = (root / "xpano-ui" / "src-tauri" / "tauri.release.conf.json").read_text(
            encoding="utf-8"
        )
        build_script = (root / "scripts" / "build_installer.ps1").read_text(encoding="utf-8")

        self.assertIn('"../../build/release-stage/WebView2Loader.dll": "WebView2Loader.dll"', config)
        self.assertIn('cargo build --release', build_script)
        self.assertIn('--webview2-loader', build_script)

    def test_gnullvm_release_statically_links_the_unwind_runtime(self):
        root = Path(__file__).parents[1]
        cargo_config = (root / "xpano-ui" / ".cargo" / "config.toml").read_text(
            encoding="utf-8"
        )

        self.assertIn("[target.x86_64-pc-windows-gnullvm]", cargo_config)
        self.assertIn('rustflags = ["-C", "target-feature=+crt-static"]', cargo_config)

    def test_formal_installer_runs_recursive_dll_closure_gate(self):
        root = Path(__file__).parents[1]
        build_script = (root / "scripts" / "build_installer.ps1").read_text(encoding="utf-8")

        self.assertIn("scripts\\windows_dll_closure.py", build_script)
        self.assertIn("--entry", build_script)
        self.assertIn("--app-local", build_script)
        self.assertIn("--tree-root", build_script)
        self.assertIn('"binaries\\python"', build_script)
        self.assertIn('"tools\\colmap"', build_script)
        self.assertIn('"runtime\\lichtfeld-studio"', build_script)
        self.assertGreaterEqual(build_script.count("Assert-ReleaseDllClosure"), 3)

    def test_formal_installer_requires_the_pinned_lichtfeld_archive(self):
        root = Path(__file__).parents[1]
        build_script = (root / "scripts" / "build_installer.ps1").read_text(encoding="utf-8")

        self.assertIn("[string]$LichtfeldArchive", build_script)
        self.assertIn("XPANO_LICHTFELD_ARCHIVE", build_script)
        self.assertIn("-LichtfeldArchive is required", build_script)
        self.assertIn("--lichtfeld-archive", build_script)

    def test_production_installer_requires_and_verifies_authenticode_signing(self):
        root = Path(__file__).parents[1]
        build_script = (root / "scripts" / "build_installer.ps1").read_text(encoding="utf-8")

        self.assertIn("[string]$SigningCertificateThumbprint", build_script)
        self.assertIn("[string]$TimestampUrl", build_script)
        self.assertIn("Resolve-ReleaseSigning", build_script)
        self.assertIn("certificateThumbprint", build_script)
        self.assertIn("timestampUrl", build_script)
        self.assertIn("Get-AuthenticodeSignature", build_script)
        self.assertIn("UNSIGNED DEVELOPMENT BUILD", build_script)

    def test_legacy_portable_builder_is_retired_in_favor_of_the_installer(self):
        root = Path(__file__).parents[1]
        legacy = (root / "scripts" / "build_release.ps1").read_text(encoding="utf-8")

        self.assertIn("build_installer.ps1", legacy)
        self.assertIn("retired", legacy)
        self.assertNotIn("Copy-PortableDirectory", legacy)

    def test_light_installer_delegates_all_release_contract_arguments(self):
        root = Path(__file__).parents[1]
        light = (root / "scripts" / "build_light_installer.ps1").read_text(encoding="utf-8")

        self.assertIn("[string]$LichtfeldArchive", light)
        self.assertIn("[string]$SigningCertificateThumbprint", light)
        self.assertIn("[string]$TimestampUrl", light)
        self.assertIn("[switch]$FullOffline", light)
        self.assertIn("@PSBoundParameters", light)

    def test_vc_redistributables_are_never_treated_as_windows_inbox_dlls(self):
        self.assertTrue(is_windows_inbox_dll("kernel32.dll"))
        self.assertTrue(is_windows_inbox_dll("api-ms-win-crt-runtime-l1-1-0.dll"))
        self.assertFalse(is_windows_inbox_dll("MSVCP140.dll"))
        self.assertFalse(is_windows_inbox_dll("VCRUNTIME140_1.dll"))
        self.assertFalse(is_windows_inbox_dll("VCOMP140.DLL"))

    def test_dependency_closure_rejects_unresolved_toolchain_dll(self):
        entry = Path("xpano-ui.exe")
        loader = Path("WebView2Loader.dll")
        imports = {
            entry: ["kernel32.dll", "WebView2Loader.dll", "libunwind.dll"],
            loader: ["kernel32.dll"],
        }

        with self.assertRaisesRegex(RuntimeError, "libunwind.dll"):
            resolve_dependency_closure(
                entry,
                [loader],
                lambda path: imports[path],
                lambda name: name.lower() == "kernel32.dll",
            )

    def test_dependency_closure_recurses_through_app_local_dlls(self):
        entry = Path("xpano-ui.exe")
        loader = Path("WebView2Loader.dll")
        imports = {
            entry: ["kernel32.dll", "WebView2Loader.dll"],
            loader: ["kernel32.dll", "ole32.dll"],
        }

        checked = resolve_dependency_closure(
            entry,
            [loader],
            lambda path: imports[path],
            lambda name: name.lower() in {"kernel32.dll", "ole32.dll"},
        )

        self.assertEqual(checked, [entry, loader])

    def test_dependency_closure_rejects_import_names_with_paths(self):
        entry = Path("xpano-ui.exe")

        with self.assertRaisesRegex(RuntimeError, r"\.\.\\libunwind.dll"):
            resolve_dependency_closure(
                entry,
                [],
                lambda _path: [r"..\libunwind.dll"],
                lambda _name: True,
            )


if __name__ == "__main__":
    unittest.main()
