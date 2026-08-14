from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path


class BootstrapError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _fail(message):
    raise BootstrapError("MANIFEST_INVALID", message)


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified(path, artifact):
    path = Path(path)
    return (
        path.is_file()
        and path.stat().st_size == artifact["size"]
        and _sha256_file(path) == artifact["sha256"]
    )


def load_runtime_manifest(path):
    path = Path(path)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise BootstrapError("MANIFEST_INVALID", f"Failed to read runtime manifest: {exc}") from exc
    if manifest.get("schemaVersion") != 1:
        _fail("Unsupported runtime manifest schema")
    if manifest.get("platform") != "windows-x86_64":
        raise BootstrapError("UNSUPPORTED_PLATFORM", "Runtime manifest is not for Windows x64")
    if not re.fullmatch(r"cp\d{2,3}", str(manifest.get("pythonAbi", ""))):
        _fail("Runtime manifest has an invalid Python ABI")
    if not isinstance(manifest.get("runtimeVersion"), str) or not manifest["runtimeVersion"].strip():
        _fail("Runtime manifest is missing runtimeVersion")
    profiles = manifest.get("profiles")
    artifacts = manifest.get("artifacts")
    if not isinstance(profiles, dict) or not profiles:
        _fail("Runtime manifest has no profiles")
    if not isinstance(artifacts, list) or not artifacts:
        _fail("Runtime manifest has no artifacts")

    by_id = {}
    for item in artifacts:
        if not isinstance(item, dict):
            _fail("Runtime artifact must be an object")
        artifact_id = item.get("id")
        kind = item.get("kind", "wheel")
        filename = item.get("filename")
        digest = str(item.get("sha256", "")).lower()
        urls = item.get("urls")
        if not isinstance(artifact_id, str) or not artifact_id or artifact_id in by_id:
            _fail("Runtime artifact ids must be unique non-empty strings")
        if kind not in {"wheel", "model"}:
            _fail(f"Runtime artifact {artifact_id} has an unsupported kind")
        if not isinstance(filename, str) or not filename:
            _fail(f"Runtime artifact {artifact_id} has an invalid filename")
        if kind == "wheel":
            if not filename.endswith(".whl"):
                _fail(f"Runtime artifact {artifact_id} is not a wheel")
            if not (filename.endswith("-win_amd64.whl") or filename.endswith("-any.whl")):
                _fail(f"Runtime artifact {artifact_id} is not compatible with Windows x64")
        else:
            destination = item.get("destination")
            destination_path = Path(destination) if isinstance(destination, str) else Path()
            if (
                not destination
                or destination_path.is_absolute()
                or ".." in destination_path.parts
            ):
                _fail(f"Runtime model {artifact_id} has an invalid destination")
        if not isinstance(item.get("size"), int) or item["size"] <= 0:
            _fail(f"Runtime artifact {artifact_id} has an invalid size")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            _fail(f"Runtime artifact {artifact_id} has an invalid SHA-256")
        if not isinstance(urls, list) or not urls or not all(isinstance(url, str) and url for url in urls):
            _fail(f"Runtime artifact {artifact_id} has no download URLs")
        normalized = dict(item)
        normalized["kind"] = kind
        normalized["sha256"] = digest
        by_id[artifact_id] = normalized

    for profile_name, profile in profiles.items():
        ids = profile.get("artifacts") if isinstance(profile, dict) else None
        if not isinstance(ids, list) or not ids or not all(item in by_id for item in ids):
            _fail(f"Runtime profile {profile_name} references invalid artifacts")

    manifest["_path"] = str(path.resolve())
    manifest["_artifactsById"] = by_id
    return manifest


def build_download_plan(manifest, profile, cache_dir):
    if profile not in manifest["profiles"]:
        raise BootstrapError("MANIFEST_INVALID", f"Unknown runtime profile: {profile}")
    cache_dir = Path(cache_dir)
    selected = [manifest["_artifactsById"][item] for item in manifest["profiles"][profile]["artifacts"]]
    missing = []
    cached_bytes = 0
    for artifact in selected:
        cached_path = cache_dir / artifact["sha256"]
        if _verified(cached_path, artifact):
            cached_bytes += artifact["size"]
        else:
            if cached_path.exists():
                cached_path.unlink()
            missing.append(artifact)
    return {
        "profile": profile,
        "artifacts": selected,
        "missing": missing,
        "downloadBytes": sum(item["size"] for item in missing),
        "cachedBytes": cached_bytes,
    }


def seed_bundled_cache(manifest, profile, bundled_cache, cache_dir):
    if profile not in manifest["profiles"]:
        raise BootstrapError("MANIFEST_INVALID", f"Unknown runtime profile: {profile}")
    bundled_cache = Path(bundled_cache)
    if not bundled_cache.is_dir():
        return 0
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    seeded = 0
    for artifact_id in manifest["profiles"][profile]["artifacts"]:
        artifact = manifest["_artifactsById"][artifact_id]
        source = bundled_cache / artifact["sha256"]
        if not source.exists():
            continue
        if not _verified(source, artifact):
            raise BootstrapError(
                "HASH_MISMATCH",
                f"Bundled runtime artifact is corrupt: {artifact['filename']}",
            )
        destination = cache_dir / artifact["sha256"]
        if _verified(destination, artifact):
            continue
        destination.unlink(missing_ok=True)
        temporary = destination.with_name(f"{destination.name}.tmp-{uuid.uuid4().hex}")
        try:
            try:
                os.link(source, temporary)
            except OSError:
                shutil.copy2(source, temporary)
            if not _verified(temporary, artifact):
                raise BootstrapError(
                    "HASH_MISMATCH",
                    f"Failed to seed bundled runtime artifact: {artifact['filename']}",
                )
            os.replace(temporary, destination)
            seeded += 1
        finally:
            temporary.unlink(missing_ok=True)
    return seeded


def ensure_disk_budget(plan, available_bytes=None, safety_margin=512 * 1024 * 1024):
    required = (
        sum(item["size"] for item in plan["missing"])
        + sum(item["size"] for item in plan["artifacts"]) * 2
        + safety_margin
    )
    if available_bytes is not None and available_bytes < required:
        raise BootstrapError(
            "DISK_FULL",
            f"Runtime bootstrap requires {required} bytes but only {available_bytes} bytes are available",
        )
    return required


def _raise_if_cancelled(cancelled):
    if cancelled and cancelled():
        raise BootstrapError("CANCELLED", "Runtime bootstrap was cancelled")


def download_artifact(artifact, cache_dir, downloads_dir, cancelled=None):
    cache_dir = Path(cache_dir)
    downloads_dir = Path(downloads_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir.mkdir(parents=True, exist_ok=True)
    cached_path = cache_dir / artifact["sha256"]
    if _verified(cached_path, artifact):
        return cached_path
    if cached_path.exists():
        cached_path.unlink()

    partial = downloads_dir / f"{artifact['sha256']}.partial"
    if partial.exists() and partial.stat().st_size >= artifact["size"]:
        partial.unlink()
    mismatch = False
    errors = []
    saw_http_error = False
    for url in artifact["urls"]:
        try:
            _raise_if_cancelled(cancelled)
            existing = partial.stat().st_size if partial.exists() else 0
            request = urllib.request.Request(url)
            if existing:
                request.add_header("Range", f"bytes={existing}-")
            with urllib.request.urlopen(request, timeout=30) as response:
                status = getattr(response, "status", None)
                append = existing > 0 and status == 206
                mode = "ab" if append else "wb"
                with partial.open(mode) as output:
                    while True:
                        _raise_if_cancelled(cancelled)
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
            if not _verified(partial, artifact):
                mismatch = True
                partial.unlink(missing_ok=True)
                errors.append(f"{url}: size or SHA-256 mismatch")
                continue
            os.replace(partial, cached_path)
            return cached_path
        except urllib.error.HTTPError as exc:
            saw_http_error = True
            errors.append(f"{url}: HTTP {exc.code} {exc.reason}")
        except (OSError, urllib.error.URLError, http.client.HTTPException) as exc:
            errors.append(f"{url}: {exc}")
    code = "HASH_MISMATCH" if mismatch else ("HTTP_ERROR" if saw_http_error else "NETWORK_OFFLINE")
    raise BootstrapError(code, "; ".join(errors) or f"Failed to download {artifact['id']}")


def _write_json_atomic(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex}")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _runtime_id(manifest, profile):
    value = f"{manifest['runtimeVersion']}-{manifest['pythonAbi']}-{profile}"
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")


def _process_is_running(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        synchronize = 0x00100000
        wait_timeout = 0x00000102
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5
        try:
            return kernel32.WaitForSingleObject(handle, 0) == wait_timeout
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _acquire_bootstrap_lock(lock_path):
    for _attempt in range(3):
        try:
            lock_handle = lock_path.open("x", encoding="utf-8")
            lock_handle.write(str(os.getpid()))
            lock_handle.close()
            return
        except FileExistsError as exc:
            try:
                owner_text = lock_path.read_text(encoding="utf-8").strip()
                owner_pid = int(owner_text)
            except (OSError, ValueError):
                owner_text = ""
                owner_pid = 0
            if _process_is_running(owner_pid):
                raise BootstrapError(
                    "BUSY", "Another runtime bootstrap is already active"
                ) from exc
            try:
                # WARN: Reclaim a stale lock only while its recorded owner is unchanged.
                if lock_path.read_text(encoding="utf-8").strip() == owner_text:
                    lock_path.unlink()
            except FileNotFoundError:
                pass
    raise BootstrapError("BUSY", "Another runtime bootstrap is already active")


def bootstrap_runtime(
    manifest_path,
    profile,
    state_root,
    install,
    probe,
    event=None,
    cancelled=None,
    available_disk_bytes=None,
    bundled_cache=None,
):
    manifest = load_runtime_manifest(manifest_path)
    state_root = Path(state_root)
    cache_dir = state_root / "cache" / "artifacts" / "sha256"
    downloads_dir = state_root / "downloads"
    runtime_root = state_root / "runtimes" / "densify"
    runtime_id = _runtime_id(manifest, profile)
    final_runtime = runtime_root / runtime_id
    active_path = state_root / "state" / "active-densify.json"
    lock_path = state_root / "state" / "bootstrap.lock"
    runtime_root.mkdir(parents=True, exist_ok=True)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    _acquire_bootstrap_lock(lock_path)

    staging = runtime_root / f"{runtime_id}.staging-{uuid.uuid4().hex}"
    try:
        _raise_if_cancelled(cancelled)
        if (final_runtime / "complete.marker").is_file():
            _write_json_atomic(
                active_path,
                {"runtimeId": runtime_id, "runtimePath": str(final_runtime)},
            )
            return {
                "runtimeId": runtime_id,
                "runtimePath": str(final_runtime),
                "reused": True,
            }
        if bundled_cache:
            seeded = seed_bundled_cache(manifest, profile, bundled_cache, cache_dir)
            if event and seeded:
                event("planning", f"Seeded {seeded} bundled artifacts", 0)
        plan = build_download_plan(manifest, profile, cache_dir)
        if available_disk_bytes is None:
            available_disk_bytes = shutil.disk_usage(state_root).free
        ensure_disk_budget(plan, available_disk_bytes)
        if event:
            event("planning", f"Runtime plan contains {len(plan['artifacts'])} artifacts", 0)
        downloaded = {}
        for index, artifact in enumerate(plan["artifacts"], start=1):
            _raise_if_cancelled(cancelled)
            downloaded[artifact["id"]] = download_artifact(
                artifact,
                cache_dir,
                downloads_dir,
                cancelled=cancelled,
            )
            if event:
                event("downloading", artifact["filename"], index * 70 / len(plan["artifacts"]))

        site_packages = staging / "site-packages"
        staging.mkdir(parents=True)
        wheelhouse = staging / "wheelhouse"
        wheels = []
        for artifact in plan["artifacts"]:
            if artifact["kind"] != "wheel":
                continue
            wheel = wheelhouse / artifact["filename"]
            wheel.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(downloaded[artifact["id"]], wheel)
            except OSError:
                shutil.copy2(downloaded[artifact["id"]], wheel)
            wheels.append(wheel)
        for artifact in plan["artifacts"]:
            if artifact["kind"] != "model":
                continue
            destination = staging / artifact["destination"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(downloaded[artifact["id"]], destination)
            except OSError:
                shutil.copy2(downloaded[artifact["id"]], destination)
        if event:
            event("installing", "Installing verified wheels", 75)
        _raise_if_cancelled(cancelled)
        install(wheels, site_packages)
        _raise_if_cancelled(cancelled)
        if event:
            event("probing", "Validating runtime imports", 92)
        if not probe(site_packages):
            raise BootstrapError("PROBE_FAILED", "Runtime probe failed")
        shutil.rmtree(wheelhouse, ignore_errors=True)

        runtime_record = {
            "schemaVersion": 1,
            "runtimeId": runtime_id,
            "runtimeVersion": manifest["runtimeVersion"],
            "profile": profile,
            "pythonAbi": manifest["pythonAbi"],
            "manifestPath": manifest["_path"],
            "artifacts": [
                {
                    "id": item["id"],
                    "kind": item["kind"],
                    "filename": item["filename"],
                    "sha256": item["sha256"],
                    **({"destination": item["destination"]} if item["kind"] == "model" else {}),
                }
                for item in plan["artifacts"]
            ],
        }
        _write_json_atomic(staging / "runtime.json", runtime_record)
        (staging / "complete.marker").write_text(runtime_id, encoding="ascii")
        if final_runtime.exists():
            shutil.rmtree(final_runtime)
        os.replace(staging, final_runtime)
        _write_json_atomic(active_path, {"runtimeId": runtime_id, "runtimePath": str(final_runtime)})
        if event:
            event("ready", "Runtime ready", 100)
        return {"runtimeId": runtime_id, "runtimePath": str(final_runtime), "reused": False}
    except BootstrapError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise BootstrapError("INSTALL_FAILED", str(exc)) from exc
    finally:
        lock_path.unlink(missing_ok=True)


def bootstrap_local_runtime(
    runtime_name,
    runtime_id,
    runtime_version,
    state_root,
    artifacts,
    install,
    probe,
    event=None,
    cancelled=None,
    available_disk_bytes=None,
):
    if not re.fullmatch(r"[A-Za-z0-9._-]+", str(runtime_name)):
        raise BootstrapError("MANIFEST_INVALID", "Runtime name is invalid")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", str(runtime_id)):
        raise BootstrapError("MANIFEST_INVALID", "Runtime id is invalid")
    state_root = Path(state_root)
    runtime_root = state_root / "runtimes" / runtime_name
    final_runtime = runtime_root / runtime_id
    active_path = state_root / "state" / f"active-{runtime_name}.json"
    lock_path = state_root / "state" / "bootstrap.lock"
    runtime_root.mkdir(parents=True, exist_ok=True)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    _acquire_bootstrap_lock(lock_path)
    staging = runtime_root / f"{runtime_id}.staging-{uuid.uuid4().hex}"
    try:
        _raise_if_cancelled(cancelled)
        for stale in runtime_root.glob(f"{runtime_id}.staging-*"):
            shutil.rmtree(stale, ignore_errors=True)
        site_packages = final_runtime / "site-packages"
        if (final_runtime / "complete.marker").is_file() and probe(site_packages):
            _write_json_atomic(
                active_path,
                {"runtimeId": runtime_id, "runtimePath": str(final_runtime)},
            )
            return {
                "runtimeId": runtime_id,
                "runtimePath": str(final_runtime),
                "sitePackages": str(site_packages),
                "reused": True,
            }

        verified_wheels = []
        for artifact in artifacts:
            source = Path(artifact["source"])
            if not _verified(source, artifact):
                raise BootstrapError(
                    "HASH_MISMATCH",
                    f"Bundled artifact is missing or corrupt: {source}",
                )
            if source.suffix.lower() != ".whl":
                raise BootstrapError("MANIFEST_INVALID", f"Bundled artifact is not a wheel: {source}")
            verified_wheels.append(source)

        if available_disk_bytes is None:
            available_disk_bytes = shutil.disk_usage(state_root).free
        ensure_disk_budget(
            {"missing": [], "artifacts": artifacts},
            available_disk_bytes,
        )

        staging.mkdir(parents=True)
        staging_site_packages = staging / "site-packages"
        if event:
            event("installing", "Installing verified bundled wheels", 45)
        install(verified_wheels, staging_site_packages)
        _raise_if_cancelled(cancelled)
        if event:
            event("probing", "Validating installed runtime", 85)
        if not probe(staging_site_packages):
            raise BootstrapError("PROBE_FAILED", "Runtime probe failed")

        runtime_record = {
            "schemaVersion": 1,
            "runtimeId": runtime_id,
            "runtimeVersion": runtime_version,
            "runtimeName": runtime_name,
            "artifacts": [
                {
                    "id": item["id"],
                    "filename": item["filename"],
                    "size": item["size"],
                    "sha256": item["sha256"],
                }
                for item in artifacts
            ],
        }
        _write_json_atomic(staging / "runtime.json", runtime_record)
        (staging / "complete.marker").write_text(runtime_id, encoding="ascii")
        if final_runtime.exists():
            shutil.rmtree(final_runtime)
        os.replace(staging, final_runtime)
        _write_json_atomic(
            active_path,
            {"runtimeId": runtime_id, "runtimePath": str(final_runtime)},
        )
        if event:
            event("ready", "Runtime ready", 100)
        return {
            "runtimeId": runtime_id,
            "runtimePath": str(final_runtime),
            "sitePackages": str(final_runtime / "site-packages"),
            "reused": False,
        }
    except BootstrapError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise BootstrapError("INSTALL_FAILED", str(exc)) from exc
    finally:
        lock_path.unlink(missing_ok=True)


def _wheel_digest(path):
    return _sha256_file(path)


def install_verified_wheels(python, pip_pyz, wheels, site_packages, cancelled=None):
    python = Path(python)
    pip_pyz = Path(pip_pyz)
    if not python.is_file() or not pip_pyz.is_file():
        raise BootstrapError("INSTALL_FAILED", "Bundled Python or pip.pyz is missing")
    site_packages.mkdir(parents=True, exist_ok=True)
    requirements = site_packages.parent / "verified-requirements.txt"
    lines = [f"{path.resolve().as_uri()} --hash=sha256:{_wheel_digest(path)}" for path in wheels]
    requirements.write_text("\n".join(lines) + "\n", encoding="utf-8")
    command = [
        str(python),
        str(pip_pyz),
        "install",
        "--no-index",
        "--only-binary=:all:",
        "--require-hashes",
        "--no-compile",
        "--target",
        str(site_packages),
        "-r",
        str(requirements),
    ]
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    process = subprocess.Popen(
        command,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    while True:
        if cancelled and cancelled():
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
            raise BootstrapError("CANCELLED", "Runtime installation was cancelled")
        try:
            stdout, stderr = process.communicate(timeout=0.1)
            break
        except subprocess.TimeoutExpired:
            pass
    if process.returncode != 0:
        raise BootstrapError("INSTALL_FAILED", (stderr or stdout).strip())


def probe_installed_runtime(python, runtime_path, profile):
    runtime_path = Path(runtime_path)
    site_packages = runtime_path / "site-packages"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    env["TORCH_HOME"] = str(runtime_path / "model-cache")
    runner = Path(__file__).resolve().parent / "run_lichtfeld_densify_standalone.py"
    command = [
        str(python),
        str(runner),
        "--xpano-site-packages",
        str(site_packages),
        "--self-test-imports",
        "--profile",
        profile,
    ]
    try:
        completed = subprocess.run(
            command,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BootstrapError("PROBE_FAILED", f"Unable to run densification import probe: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise BootstrapError(
            "PROBE_FAILED",
            detail or f"Runtime probe exited with code {completed.returncode}",
        )
    return True


def _emit_cli_event(phase, message, progress):
    print(
        "BOOTSTRAP_EVENT:"
        + json.dumps({"phase": phase, "message": message, "progress": progress}, ensure_ascii=False),
        flush=True,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Plan or install the xPano densification runtime.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ["plan", "install"]:
        command = subparsers.add_parser(name)
        command.add_argument("--manifest", required=True)
        command.add_argument("--profile", choices=["cpu", "cuda"], required=True)
        command.add_argument("--state-root", required=True)
        if name == "install":
            command.add_argument("--python", required=True)
            command.add_argument("--pip-pyz", required=True)
            command.add_argument("--bundled-cache")
    args = parser.parse_args(argv)
    manifest = load_runtime_manifest(args.manifest)
    state_root = Path(args.state_root)
    if args.command == "plan":
        plan = build_download_plan(manifest, args.profile, state_root / "cache" / "artifacts" / "sha256")
        print(
            json.dumps(
                {
                    "profile": args.profile,
                    "artifactCount": len(plan["artifacts"]),
                    "missingCount": len(plan["missing"]),
                    "downloadBytes": plan["downloadBytes"],
                    "cachedBytes": plan["cachedBytes"],
                }
            )
        )
        return 0

    result = bootstrap_runtime(
        args.manifest,
        args.profile,
        state_root,
        lambda wheels, site_packages: install_verified_wheels(args.python, args.pip_pyz, wheels, site_packages),
        lambda site_packages: probe_installed_runtime(args.python, site_packages.parent, args.profile),
        event=_emit_cli_event,
        bundled_cache=args.bundled_cache,
    )
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BootstrapError as exc:
        print(json.dumps({"code": exc.code, "message": str(exc)}, ensure_ascii=False), file=sys.stderr, flush=True)
        raise SystemExit(1)
