import hashlib
import http.client
import json
import os
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from scripts.runtime_bootstrap import (
    BootstrapError,
    bootstrap_runtime,
    build_download_plan,
    download_artifact,
    ensure_disk_budget,
    install_verified_wheels,
    load_runtime_manifest,
    probe_installed_runtime,
    seed_bundled_cache,
)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class RuntimeBootstrapTests(unittest.TestCase):
    def write_manifest(self, root, artifacts, profiles=None, runtime_version="densify-test-1"):
        path = root / "runtime-manifest.json"
        path.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "runtimeVersion": runtime_version,
                    "platform": "windows-x86_64",
                    "pythonAbi": "cp312",
                    "profiles": profiles or {"cpu": {"artifacts": [item["id"] for item in artifacts]}},
                    "artifacts": artifacts,
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_plan_selects_profile_and_excludes_verified_cache_hits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cached = b"cached-wheel"
            missing = b"missing-wheel"
            artifacts = [
                {
                    "id": "common",
                    "filename": "common-1-py3-none-any.whl",
                    "size": len(cached),
                    "sha256": sha256(cached),
                    "urls": ["https://invalid/common.whl"],
                },
                {
                    "id": "cuda",
                    "filename": "cuda-1-cp312-cp312-win_amd64.whl",
                    "size": len(missing),
                    "sha256": sha256(missing),
                    "urls": ["https://invalid/cuda.whl"],
                },
            ]
            manifest = load_runtime_manifest(
                self.write_manifest(
                    root,
                    artifacts,
                    profiles={
                        "cpu": {"artifacts": ["common"]},
                        "cuda": {"artifacts": ["common", "cuda"]},
                    },
                )
            )
            cache = root / "cache"
            cache.mkdir()
            (cache / sha256(cached)).write_bytes(cached)

            plan = build_download_plan(manifest, "cuda", cache)

            self.assertEqual([item["id"] for item in plan["missing"]], ["cuda"])
            self.assertEqual(plan["downloadBytes"], len(missing))
            self.assertEqual(plan["cachedBytes"], len(cached))

    def test_bundled_cache_seeds_selected_profiles_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            common = b"common-wheel"
            cuda = b"cuda-wheel"
            artifacts = [
                {
                    "id": "common",
                    "filename": "common-1-py3-none-any.whl",
                    "size": len(common),
                    "sha256": sha256(common),
                    "urls": ["https://invalid/common.whl"],
                },
                {
                    "id": "cuda",
                    "filename": "cuda-1-cp312-cp312-win_amd64.whl",
                    "size": len(cuda),
                    "sha256": sha256(cuda),
                    "urls": ["https://invalid/cuda.whl"],
                },
            ]
            manifest = load_runtime_manifest(
                self.write_manifest(
                    root,
                    artifacts,
                    profiles={
                        "cpu": {"artifacts": ["common"]},
                        "cuda": {"artifacts": ["common", "cuda"]},
                    },
                )
            )
            bundled = root / "bundled"
            bundled.mkdir()
            (bundled / sha256(common)).write_bytes(common)
            (bundled / sha256(cuda)).write_bytes(cuda)
            cache = root / "state" / "cache"

            seeded = seed_bundled_cache(manifest, "cuda", bundled, cache)
            plan = build_download_plan(manifest, "cuda", cache)

            self.assertEqual(seeded, 2)
            self.assertEqual(plan["missing"], [])
            self.assertEqual((cache / sha256(common)).read_bytes(), common)
            self.assertEqual((cache / sha256(cuda)).read_bytes(), cuda)

    def test_corrupt_bundled_cache_fails_instead_of_downloading(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = b"expected-wheel"
            artifact = {
                "id": "common",
                "filename": "common-1-py3-none-any.whl",
                "size": len(payload),
                "sha256": sha256(payload),
                "urls": ["https://invalid/common.whl"],
            }
            manifest = load_runtime_manifest(self.write_manifest(root, [artifact]))
            bundled = root / "bundled"
            bundled.mkdir()
            (bundled / sha256(payload)).write_bytes(b"tampered")

            with self.assertRaises(BootstrapError) as raised:
                seed_bundled_cache(manifest, "cpu", bundled, root / "cache")

            self.assertEqual(raised.exception.code, "HASH_MISMATCH")

    def test_download_uses_next_mirror_and_commits_only_verified_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.whl"
            payload = b"small fake wheel"
            source.write_bytes(payload)
            artifact = {
                "id": "demo",
                "filename": "demo-1-py3-none-any.whl",
                "size": len(payload),
                "sha256": sha256(payload),
                "urls": [(root / "missing.whl").as_uri(), source.as_uri()],
            }

            cached = download_artifact(artifact, root / "cache", root / "downloads")

            self.assertEqual(cached.read_bytes(), payload)
            self.assertEqual(cached.name, sha256(payload))
            self.assertFalse(any((root / "downloads").glob("*.partial")))

    def test_hash_mismatch_never_enters_shared_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.whl"
            source.write_bytes(b"tampered")
            artifact = {
                "id": "demo",
                "filename": "demo-1-py3-none-any.whl",
                "size": 8,
                "sha256": sha256(b"expected"),
                "urls": [source.as_uri()],
            }

            with self.assertRaises(BootstrapError) as raised:
                download_artifact(artifact, root / "cache", root / "downloads")

            self.assertEqual(raised.exception.code, "HASH_MISMATCH")
            self.assertFalse((root / "cache" / artifact["sha256"]).exists())

    def test_successful_bootstrap_switches_active_only_after_probe_and_reuses_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = b"small fake wheel"
            source = root / "source.whl"
            source.write_bytes(payload)
            manifest_path = self.write_manifest(
                root,
                [
                    {
                        "id": "demo",
                        "filename": "demo-1-py3-none-any.whl",
                        "size": len(payload),
                        "sha256": sha256(payload),
                        "urls": [source.as_uri()],
                    }
                ],
            )
            installs = []

            def install(wheels, site_packages):
                installs.append([path.name for path in wheels])
                site_packages.mkdir(parents=True)
                (site_packages / "installed.txt").write_text("ok", encoding="utf-8")

            result = bootstrap_runtime(manifest_path, "cpu", root / "state-root", install, lambda path: (path / "installed.txt").is_file())
            source.unlink()
            repeated = bootstrap_runtime(manifest_path, "cpu", root / "state-root", install, lambda path: (path / "installed.txt").is_file())

            active = json.loads((root / "state-root" / "state" / "active-densify.json").read_text(encoding="utf-8"))
            self.assertEqual(active["runtimeId"], result["runtimeId"])
            self.assertTrue((Path(result["runtimePath"]) / "complete.marker").is_file())
            self.assertTrue(repeated["reused"])
            self.assertEqual(len(installs), 1)

    def test_verified_cache_rebuilds_missing_runtime_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = b"small fake wheel"
            source = root / "source.whl"
            source.write_bytes(payload)
            manifest_path = self.write_manifest(
                root,
                [{
                    "id": "demo",
                    "filename": "demo-1-py3-none-any.whl",
                    "size": len(payload),
                    "sha256": sha256(payload),
                    "urls": [source.as_uri()],
                }],
            )
            state_root = root / "state-root"

            def install(_wheels, site_packages):
                site_packages.mkdir(parents=True)
                (site_packages / "ok").write_text("ok", encoding="utf-8")

            first = bootstrap_runtime(manifest_path, "cpu", state_root, install, lambda path: (path / "ok").is_file())
            runtime = Path(first["runtimePath"])
            import shutil
            shutil.rmtree(runtime)
            source.unlink()

            with mock.patch(
                "scripts.runtime_bootstrap.urllib.request.urlopen",
                side_effect=AssertionError("verified cache must avoid network"),
            ):
                rebuilt = bootstrap_runtime(
                    manifest_path,
                    "cpu",
                    state_root,
                    install,
                    lambda path: (path / "ok").is_file(),
                )

            self.assertFalse(rebuilt["reused"])
            self.assertTrue(Path(rebuilt["runtimePath"]).is_dir())

    def test_failed_probe_preserves_previous_active_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = b"small fake wheel"
            source = root / "source.whl"
            source.write_bytes(payload)
            manifest_path = self.write_manifest(
                root,
                [
                    {
                        "id": "demo",
                        "filename": "demo-1-py3-none-any.whl",
                        "size": len(payload),
                        "sha256": sha256(payload),
                        "urls": [source.as_uri()],
                    }
                ],
            )
            state_root = root / "state-root"
            (state_root / "state").mkdir(parents=True)
            (state_root / "state" / "active-densify.json").write_text('{"runtimeId":"old"}', encoding="utf-8")

            def install(_wheels, site_packages):
                site_packages.mkdir(parents=True)

            with self.assertRaises(BootstrapError) as raised:
                bootstrap_runtime(manifest_path, "cpu", state_root, install, lambda _path: False)

            active = json.loads((state_root / "state" / "active-densify.json").read_text(encoding="utf-8"))
            self.assertEqual(raised.exception.code, "PROBE_FAILED")
            self.assertEqual(active["runtimeId"], "old")
            self.assertFalse(any((state_root / "runtimes" / "densify").glob("*.staging-*")))

    def test_model_artifact_is_materialized_but_not_passed_to_pip_installer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wheel_payload = b"small fake wheel"
            model_payload = b"small fake model"
            wheel_source = root / "demo.whl"
            model_source = root / "romav2.pt"
            wheel_source.write_bytes(wheel_payload)
            model_source.write_bytes(model_payload)
            manifest_path = self.write_manifest(
                root,
                [
                    {
                        "id": "demo",
                        "kind": "wheel",
                        "filename": "demo-1-py3-none-any.whl",
                        "size": len(wheel_payload),
                        "sha256": sha256(wheel_payload),
                        "urls": [wheel_source.as_uri()],
                    },
                    {
                        "id": "romav2-model",
                        "kind": "model",
                        "filename": "romav2.pt",
                        "destination": "model-cache/hub/checkpoints/romav2.pt",
                        "size": len(model_payload),
                        "sha256": sha256(model_payload),
                        "urls": [model_source.as_uri()],
                    },
                ],
            )
            installed = []

            def install(wheels, site_packages):
                installed.extend(path.name for path in wheels)
                site_packages.mkdir(parents=True)

            result = bootstrap_runtime(manifest_path, "cpu", root / "state-root", install, lambda _path: True)

            runtime = Path(result["runtimePath"])
            self.assertEqual(installed, ["demo-1-py3-none-any.whl"])
            self.assertEqual((runtime / "model-cache/hub/checkpoints/romav2.pt").read_bytes(), model_payload)

    def test_manifest_rejects_source_distributions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = self.write_manifest(
                root,
                [
                    {
                        "id": "bad",
                        "filename": "bad-1.tar.gz",
                        "size": 1,
                        "sha256": "0" * 64,
                        "urls": ["https://invalid/bad.tar.gz"],
                    }
                ],
            )

            with self.assertRaises(BootstrapError) as raised:
                load_runtime_manifest(manifest_path)

            self.assertEqual(raised.exception.code, "MANIFEST_INVALID")

    def test_manifest_distinguishes_unsupported_platform(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = self.write_manifest(
                root,
                [{
                    "id": "demo",
                    "filename": "demo-1-py3-none-any.whl",
                    "size": 1,
                    "sha256": "0" * 64,
                    "urls": ["https://example.invalid/demo.whl"],
                }],
            )
            value = json.loads(manifest_path.read_text(encoding="utf-8"))
            value["platform"] = "linux-x86_64"
            manifest_path.write_text(json.dumps(value), encoding="utf-8")

            with self.assertRaises(BootstrapError) as raised:
                load_runtime_manifest(manifest_path)

            self.assertEqual(raised.exception.code, "UNSUPPORTED_PLATFORM")

    def test_http_error_is_distinct_from_network_offline_and_keeps_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = b"partial"
            artifact = {
                "id": "demo",
                "filename": "demo-1-py3-none-any.whl",
                "size": 20,
                "sha256": sha256(b"expected payload here"),
                "urls": ["https://example.invalid/demo.whl"],
            }
            partial = root / "downloads" / f"{artifact['sha256']}.partial"
            partial.parent.mkdir(parents=True)
            partial.write_bytes(payload)
            error = urllib.error.HTTPError(artifact["urls"][0], 503, "unavailable", {}, None)

            with mock.patch("scripts.runtime_bootstrap.urllib.request.urlopen", side_effect=error):
                with self.assertRaises(BootstrapError) as raised:
                    download_artifact(artifact, root / "cache", root / "downloads")

            self.assertEqual(raised.exception.code, "HTTP_ERROR")
            self.assertIn("503", str(raised.exception))
            self.assertEqual(partial.read_bytes(), payload)

    def test_network_offline_keeps_partial_for_next_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = {
                "id": "demo",
                "filename": "demo-1-py3-none-any.whl",
                "size": 20,
                "sha256": sha256(b"expected payload here"),
                "urls": ["https://example.invalid/demo.whl"],
            }
            partial = root / "downloads" / f"{artifact['sha256']}.partial"
            partial.parent.mkdir(parents=True)
            partial.write_bytes(b"partial")

            with mock.patch(
                "scripts.runtime_bootstrap.urllib.request.urlopen",
                side_effect=urllib.error.URLError("offline"),
            ):
                with self.assertRaises(BootstrapError) as raised:
                    download_artifact(artifact, root / "cache", root / "downloads")

            self.assertEqual(raised.exception.code, "NETWORK_OFFLINE")
            self.assertEqual(partial.read_bytes(), b"partial")

    def test_midstream_disconnect_is_network_error_and_preserves_downloaded_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = {
                "id": "demo",
                "filename": "demo-1-py3-none-any.whl",
                "size": 20,
                "sha256": sha256(b"expected payload here"),
                "urls": ["https://example.invalid/demo.whl"],
            }

            class InterruptedResponse:
                status = 200

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def read(self, _size=-1):
                    if not hasattr(self, "sent"):
                        self.sent = True
                        return b"downloaded"
                    raise http.client.IncompleteRead(b"", 10)

            with mock.patch(
                "scripts.runtime_bootstrap.urllib.request.urlopen",
                return_value=InterruptedResponse(),
            ):
                with self.assertRaises(BootstrapError) as raised:
                    download_artifact(artifact, root / "cache", root / "downloads")

            partial = root / "downloads" / f"{artifact['sha256']}.partial"
            self.assertEqual(raised.exception.code, "NETWORK_OFFLINE")
            self.assertEqual(partial.read_bytes(), b"downloaded")

    def test_range_resume_appends_206_response_to_existing_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = b"resume-this-small-artifact"
            artifact = {
                "id": "demo",
                "filename": "demo-1-py3-none-any.whl",
                "size": len(payload),
                "sha256": sha256(payload),
                "urls": ["https://local.test/demo.whl"],
            }
            partial = root / "downloads" / f"{artifact['sha256']}.partial"
            partial.parent.mkdir(parents=True)
            partial.write_bytes(payload[:7])
            observed_ranges = []

            class Response:
                status = 206

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def read(self, _size=-1):
                    if hasattr(self, "sent"):
                        return b""
                    self.sent = True
                    return payload[7:]

            def open_request(request, timeout):
                self.assertEqual(timeout, 30)
                observed_ranges.append(request.get_header("Range"))
                return Response()

            with mock.patch("scripts.runtime_bootstrap.urllib.request.urlopen", side_effect=open_request):
                cached = download_artifact(artifact, root / "cache", root / "downloads")

            self.assertEqual(observed_ranges, ["bytes=7-"])
            self.assertEqual(cached.read_bytes(), payload)

    def test_server_ignoring_range_restarts_from_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = b"server-returned-the-whole-file"
            source = root / "source.whl"
            source.write_bytes(payload)
            artifact = {
                "id": "demo",
                "filename": "demo-1-py3-none-any.whl",
                "size": len(payload),
                "sha256": sha256(payload),
                "urls": [source.as_uri()],
            }
            partial = root / "downloads" / f"{artifact['sha256']}.partial"
            partial.parent.mkdir(parents=True)
            partial.write_bytes(b"stale-prefix")

            cached = download_artifact(artifact, root / "cache", root / "downloads")

            self.assertEqual(cached.read_bytes(), payload)

    def test_cancellation_preserves_partial_and_never_commits_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = os.urandom(192 * 1024)
            source = root / "source.whl"
            source.write_bytes(payload)
            artifact = {
                "id": "demo",
                "filename": "demo-1-py3-none-any.whl",
                "size": len(payload),
                "sha256": sha256(payload),
                "urls": [source.as_uri()],
            }
            checks = 0

            def cancelled():
                nonlocal checks
                checks += 1
                return checks >= 3

            with self.assertRaises(BootstrapError) as raised:
                download_artifact(
                    artifact,
                    root / "cache",
                    root / "downloads",
                    cancelled=cancelled,
                )

            partial = root / "downloads" / f"{artifact['sha256']}.partial"
            self.assertEqual(raised.exception.code, "CANCELLED")
            self.assertTrue(partial.is_file())
            self.assertGreater(partial.stat().st_size, 0)
            self.assertLess(partial.stat().st_size, len(payload))
            self.assertFalse((root / "cache" / artifact["sha256"]).exists())

    def test_disk_budget_fails_before_download(self):
        plan = {
            "missing": [{"size": 100}],
            "artifacts": [{"size": 100}],
        }

        with self.assertRaises(BootstrapError) as raised:
            ensure_disk_budget(plan, available_bytes=399, safety_margin=100)

        self.assertEqual(raised.exception.code, "DISK_FULL")
        self.assertIn("400", str(raised.exception))

    def test_bootstrap_disk_preflight_runs_before_any_network_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = b"small fake wheel"
            manifest_path = self.write_manifest(
                root,
                [{
                    "id": "demo",
                    "filename": "demo-1-py3-none-any.whl",
                    "size": len(payload),
                    "sha256": sha256(payload),
                    "urls": ["https://example.invalid/demo.whl"],
                }],
            )

            with mock.patch("scripts.runtime_bootstrap.download_artifact") as download:
                with self.assertRaises(BootstrapError) as raised:
                    bootstrap_runtime(
                        manifest_path,
                        "cpu",
                        root / "state-root",
                        lambda *_: None,
                        lambda _: True,
                        available_disk_bytes=0,
                    )

            self.assertEqual(raised.exception.code, "DISK_FULL")
            download.assert_not_called()

    def test_existing_lock_returns_busy_without_touching_active_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = b"small fake wheel"
            source = root / "source.whl"
            source.write_bytes(payload)
            manifest_path = self.write_manifest(
                root,
                [{
                    "id": "demo",
                    "filename": "demo-1-py3-none-any.whl",
                    "size": len(payload),
                    "sha256": sha256(payload),
                    "urls": [source.as_uri()],
                }],
            )
            state_root = root / "state-root"
            (state_root / "state").mkdir(parents=True)
            active = state_root / "state" / "active-densify.json"
            active.write_text('{"runtimeId":"old"}', encoding="utf-8")
            (state_root / "state" / "bootstrap.lock").write_text(str(os.getpid()), encoding="utf-8")

            with self.assertRaises(BootstrapError) as raised:
                bootstrap_runtime(manifest_path, "cpu", state_root, lambda *_: None, lambda _: True)

            self.assertEqual(raised.exception.code, "BUSY")
            self.assertEqual(json.loads(active.read_text(encoding="utf-8"))["runtimeId"], "old")

    def test_existing_lock_wins_even_when_requested_runtime_is_already_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = b"small fake wheel"
            source = root / "source.whl"
            source.write_bytes(payload)
            manifest_path = self.write_manifest(
                root,
                [{
                    "id": "demo",
                    "filename": "demo-1-py3-none-any.whl",
                    "size": len(payload),
                    "sha256": sha256(payload),
                    "urls": [source.as_uri()],
                }],
            )
            state_root = root / "state-root"
            runtime = state_root / "runtimes" / "densify" / "densify-test-1-cp312-cpu"
            runtime.mkdir(parents=True)
            (runtime / "complete.marker").write_text("complete", encoding="ascii")
            (state_root / "state").mkdir(parents=True)
            active = state_root / "state" / "active-densify.json"
            active.write_text('{"runtimeId":"other"}', encoding="utf-8")
            (state_root / "state" / "bootstrap.lock").write_text(str(os.getpid()), encoding="utf-8")

            with self.assertRaises(BootstrapError) as raised:
                bootstrap_runtime(manifest_path, "cpu", state_root, lambda *_: None, lambda _: True)

            self.assertEqual(raised.exception.code, "BUSY")
            self.assertEqual(json.loads(active.read_text(encoding="utf-8"))["runtimeId"], "other")

    def test_stale_lock_from_force_killed_bootstrap_is_recovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = b"small fake wheel"
            source = root / "source.whl"
            source.write_bytes(payload)
            manifest_path = self.write_manifest(
                root,
                [{
                    "id": "demo",
                    "filename": "demo-1-py3-none-any.whl",
                    "size": len(payload),
                    "sha256": sha256(payload),
                    "urls": [source.as_uri()],
                }],
            )
            state_root = root / "state-root"
            (state_root / "state").mkdir(parents=True)
            lock = state_root / "state" / "bootstrap.lock"
            lock.write_text("2147483647", encoding="utf-8")

            def install(_wheels, site_packages):
                site_packages.mkdir(parents=True)

            result = bootstrap_runtime(
                manifest_path,
                "cpu",
                state_root,
                install,
                lambda _: True,
            )

            self.assertTrue(Path(result["runtimePath"]).is_dir())
            self.assertFalse(lock.exists())

    def test_install_failure_preserves_old_active_and_removes_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = b"small fake wheel"
            source = root / "source.whl"
            source.write_bytes(payload)
            manifest_path = self.write_manifest(
                root,
                [{
                    "id": "demo",
                    "filename": "demo-1-py3-none-any.whl",
                    "size": len(payload),
                    "sha256": sha256(payload),
                    "urls": [source.as_uri()],
                }],
            )
            state_root = root / "state-root"
            (state_root / "state").mkdir(parents=True)
            active = state_root / "state" / "active-densify.json"
            active.write_text('{"runtimeId":"old"}', encoding="utf-8")

            def fail_install(_wheels, _site_packages):
                raise RuntimeError("fake pip exit 17")

            with self.assertRaises(BootstrapError) as raised:
                bootstrap_runtime(manifest_path, "cpu", state_root, fail_install, lambda _: True)

            self.assertEqual(raised.exception.code, "INSTALL_FAILED")
            self.assertIn("fake pip exit 17", str(raised.exception))
            self.assertEqual(json.loads(active.read_text(encoding="utf-8"))["runtimeId"], "old")
            self.assertFalse(any((state_root / "runtimes" / "densify").glob("*.staging-*")))

    def test_successful_upgrade_switches_active_and_keeps_old_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = b"small fake wheel"
            source = root / "source.whl"
            source.write_bytes(payload)
            artifact = {
                "id": "demo",
                "filename": "demo-1-py3-none-any.whl",
                "size": len(payload),
                "sha256": sha256(payload),
                "urls": [source.as_uri()],
            }
            state_root = root / "state-root"

            def install(_wheels, site_packages):
                site_packages.mkdir(parents=True)
                (site_packages / "ok").write_text("ok", encoding="utf-8")

            first = bootstrap_runtime(
                self.write_manifest(root, [artifact], runtime_version="densify-old"),
                "cpu",
                state_root,
                install,
                lambda path: (path / "ok").is_file(),
            )
            second = bootstrap_runtime(
                self.write_manifest(root, [artifact], runtime_version="densify-new"),
                "cpu",
                state_root,
                install,
                lambda path: (path / "ok").is_file(),
            )

            active = json.loads((state_root / "state" / "active-densify.json").read_text(encoding="utf-8"))
            self.assertEqual(active["runtimeId"], second["runtimeId"])
            self.assertNotEqual(first["runtimeId"], second["runtimeId"])
            self.assertTrue(Path(first["runtimePath"]).is_dir())
            self.assertTrue(Path(second["runtimePath"]).is_dir())

    def test_installed_runtime_probe_uses_explicit_script_activation_without_user_site(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            python = root / "python.exe"
            runtime = root / "runtime"
            site_packages = runtime / "site-packages"
            python.write_bytes(b"python")
            site_packages.mkdir(parents=True)
            completed = subprocess.CompletedProcess([], 0, "ok\n", "")

            with mock.patch("scripts.runtime_bootstrap.subprocess.run", return_value=completed) as run:
                self.assertTrue(probe_installed_runtime(python, runtime, "cpu"))

            command, kwargs = run.call_args
            runner = Path(__file__).resolve().parents[1] / "scripts/run_lichtfeld_densify_standalone.py"
            self.assertEqual(
                command[0],
                [
                    str(python),
                    str(runner),
                    "--xpano-site-packages",
                    str(site_packages),
                    "--self-test-imports",
                    "--profile",
                    "cpu",
                ],
            )
            self.assertEqual(kwargs["env"]["PYTHONNOUSERSITE"], "1")
            self.assertNotIn("PYTHONPATH", kwargs["env"])

    def test_cpu_and_cuda_profiles_install_only_their_locked_artifacts_in_unicode_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "中文 runtime 空格"
            root.mkdir()
            common = b"common"
            cpu = b"cpu"
            cuda = b"cuda"
            artifacts = []
            for artifact_id, payload in [("common", common), ("cpu", cpu), ("cuda", cuda)]:
                source = root / f"{artifact_id}.whl"
                source.write_bytes(payload)
                artifacts.append({
                    "id": artifact_id,
                    "filename": f"{artifact_id}-1-py3-none-any.whl",
                    "size": len(payload),
                    "sha256": sha256(payload),
                    "urls": [source.as_uri()],
                })
            manifest = self.write_manifest(
                root,
                artifacts,
                profiles={
                    "cpu": {"artifacts": ["common", "cpu"]},
                    "cuda": {"artifacts": ["common", "cuda"]},
                },
            )
            installed = {}

            def installer(profile):
                def install(wheels, site_packages):
                    installed[profile] = [path.name for path in wheels]
                    site_packages.mkdir(parents=True)
                return install

            cpu_result = bootstrap_runtime(manifest, "cpu", root / "状态 CPU", installer("cpu"), lambda _: True)
            cuda_result = bootstrap_runtime(manifest, "cuda", root / "状态 CUDA", installer("cuda"), lambda _: True)

            self.assertEqual(installed["cpu"], ["common-1-py3-none-any.whl", "cpu-1-py3-none-any.whl"])
            self.assertEqual(installed["cuda"], ["common-1-py3-none-any.whl", "cuda-1-py3-none-any.whl"])
            self.assertIn("中文 runtime 空格", cpu_result["runtimePath"])
            self.assertIn("中文 runtime 空格", cuda_result["runtimePath"])

    def test_runtime_probe_executes_cuda_tensor_only_for_cuda_profile(self):
        completed = mock.Mock(returncode=0, stdout="ok", stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch("scripts.runtime_bootstrap.subprocess.run", return_value=completed) as run:
                self.assertTrue(probe_installed_runtime("python.exe", root, "cpu"))
                cpu_command = run.call_args.args[0]
                self.assertTrue(probe_installed_runtime("python.exe", root, "cuda"))
                cuda_command = run.call_args.args[0]

        self.assertEqual(cpu_command[-2:], ["--profile", "cpu"])
        self.assertEqual(cuda_command[-2:], ["--profile", "cuda"])

    def test_runtime_probe_failure_preserves_process_diagnostics(self):
        completed = mock.Mock(returncode=7, stdout="", stderr="CUDA driver too old")
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("scripts.runtime_bootstrap.subprocess.run", return_value=completed):
                with self.assertRaises(BootstrapError) as raised:
                    probe_installed_runtime("python.exe", Path(tmp), "cuda")

        self.assertEqual(raised.exception.code, "PROBE_FAILED")
        self.assertIn("CUDA driver too old", str(raised.exception))

    def test_runtime_probe_start_or_timeout_failure_is_classified_as_probe_failure(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "scripts.runtime_bootstrap.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["python.exe"], 120),
        ):
            with self.assertRaises(BootstrapError) as raised:
                probe_installed_runtime("python.exe", Path(tmp), "cpu")

        self.assertEqual(raised.exception.code, "PROBE_FAILED")
        self.assertIn("densification import probe", str(raised.exception))

    def test_pip_install_uses_communicate_timeouts_to_drain_output_while_polling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            python = root / "python.exe"
            pip_pyz = root / "pip.pyz"
            python.write_bytes(b"fake")
            pip_pyz.write_bytes(b"fake")
            process = mock.Mock(returncode=0)
            process.communicate.side_effect = [
                subprocess.TimeoutExpired("pip", 0.1),
                ("large output drained", ""),
            ]

            with mock.patch("scripts.runtime_bootstrap.subprocess.Popen", return_value=process):
                install_verified_wheels(python, pip_pyz, [], root / "site packages")

            self.assertEqual(process.communicate.call_count, 2)
            self.assertEqual(process.communicate.call_args_list[0].kwargs, {"timeout": 0.1})


if __name__ == "__main__":
    unittest.main()
