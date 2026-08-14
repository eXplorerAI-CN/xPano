from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.runtime_bootstrap import (
    BootstrapError,
    bootstrap_local_runtime,
    install_verified_wheels,
)
from scripts.metashape_runtime_env import build_metashape_process_env, metashape_runtime_cli_args
from scripts.lichtfeld_training import build_lichtfeld_environment


class RuntimeReadinessError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _fail(code, message):
    raise RuntimeReadinessError(code, message)


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_runtime_relative_path(value, label):
    if not isinstance(value, str) or not value:
        _fail("LFS_RUNTIME_CORRUPT", f"LichtFeld runtime {label} is invalid")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path.name in {"", "."}:
        _fail("LFS_RUNTIME_CORRUPT", f"LichtFeld runtime {label} escapes the bundled runtime")
    return path


def _load_lichtfeld_manifest(resource_root):
    resource_root = Path(resource_root).resolve()
    manifest_path = resource_root / "runtime" / "lichtfeld-studio-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        _fail("LFS_RUNTIME_CORRUPT", f"Failed to read LichtFeld runtime manifest: {exc}")
    if (
        manifest.get("schemaVersion") != 1
        or manifest.get("runtime") != "lichtfeld-studio"
        or not isinstance(manifest.get("version"), str)
        or not manifest["version"].strip()
    ):
        _fail("LFS_RUNTIME_CORRUPT", "LichtFeld runtime manifest schema is unsupported")
    records = {}
    for record in manifest.get("files", []):
        if not isinstance(record, dict):
            _fail("LFS_RUNTIME_CORRUPT", "LichtFeld runtime manifest contains an invalid file record")
        relative = _safe_runtime_relative_path(record.get("path"), "file path")
        key = relative.as_posix()
        digest = str(record.get("sha256", "")).lower()
        if (
            key in records
            or not isinstance(record.get("size"), int)
            or record["size"] < 0
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            _fail("LFS_RUNTIME_CORRUPT", f"LichtFeld runtime manifest record is invalid: {key}")
        records[key] = {"path": relative, "size": record["size"], "sha256": digest}
    sentinels = []
    for value in manifest.get("sentinels", []):
        relative = _safe_runtime_relative_path(value, "sentinel")
        key = relative.as_posix()
        if key not in records:
            _fail("LFS_RUNTIME_CORRUPT", f"LichtFeld runtime sentinel is absent from the manifest: {key}")
        sentinels.append(key)
    if not records or not sentinels:
        _fail("LFS_RUNTIME_CORRUPT", "LichtFeld runtime manifest has no files or sentinels")
    return manifest, records, sentinels


def _validate_lichtfeld_sentinels(resource_root, executable):
    resource_root = Path(resource_root).resolve()
    runtime = resource_root / "runtime" / "lichtfeld-studio"
    manifest, records, sentinels = _load_lichtfeld_manifest(resource_root)
    expected_executable = runtime / "bin" / "LichtFeld-Studio.exe"
    if Path(executable).resolve(strict=False) != expected_executable.resolve(strict=False):
        _fail("LFS_RUNTIME_CORRUPT", "LichtFeld executable is not the bundled runtime")
    for key in sentinels:
        record = records[key]
        path = runtime / record["path"]
        if not path.is_file() or path.stat().st_size != record["size"] or _sha256(path) != record["sha256"]:
            _fail("LFS_RUNTIME_CORRUPT", f"Bundled LichtFeld resource is missing or corrupt: {key}")
    return manifest


def _probe_lfs_version(executable, profile_root):
    environment = build_lichtfeld_environment(executable, profile_root)
    try:
        completed = subprocess.run(
            [str(executable), "--version"],
            cwd=str(Path(executable).resolve().parent),
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _fail("LFS_VERSION_PROBE_FAILED", f"Unable to start LichtFeld Studio: {exc}")
    output = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()
    if completed.returncode != 0 or "LichtFeld Studio v" not in output:
        _fail("LFS_VERSION_PROBE_FAILED", output or f"LichtFeld Studio exited with code {completed.returncode}")
    return output


def _windows_library(name):
    if os.name != "nt" or not hasattr(ctypes, "WinDLL"):
        _fail("LFS_UNSUPPORTED_PLATFORM", "LichtFeld GUI training is supported only on Windows x64")
    try:
        return ctypes.WinDLL(str(name))
    except OSError as exc:
        _fail("LFS_DRIVER_LOADER_MISSING", f"Failed to load {name}: {exc}")


def probe_cuda_device():
    try:
        library = _windows_library("nvcuda.dll")
    except RuntimeReadinessError as exc:
        if exc.code == "LFS_DRIVER_LOADER_MISSING":
            _fail("LFS_CUDA_DRIVER_MISSING", "NVIDIA display driver is unavailable; install a compatible NVIDIA driver")
        raise
    initialize = library.cuInit
    initialize.argtypes = [ctypes.c_uint]
    initialize.restype = ctypes.c_int
    if initialize(0) != 0:
        _fail("LFS_CUDA_DRIVER_UNAVAILABLE", "NVIDIA CUDA driver initialization failed")
    get_count = library.cuDeviceGetCount
    get_count.argtypes = [ctypes.POINTER(ctypes.c_int)]
    get_count.restype = ctypes.c_int
    count = ctypes.c_int()
    if get_count(ctypes.byref(count)) != 0:
        _fail("LFS_CUDA_DRIVER_UNAVAILABLE", "NVIDIA CUDA device enumeration failed")
    if count.value < 1:
        _fail("LFS_CUDA_NO_DEVICE", "No CUDA-capable NVIDIA device was detected")
    return {"status": "ready", "deviceCount": count.value}


class _VkApplicationInfo(ctypes.Structure):
    _fields_ = [
        ("sType", ctypes.c_uint32),
        ("pNext", ctypes.c_void_p),
        ("pApplicationName", ctypes.c_char_p),
        ("applicationVersion", ctypes.c_uint32),
        ("pEngineName", ctypes.c_char_p),
        ("engineVersion", ctypes.c_uint32),
        ("apiVersion", ctypes.c_uint32),
    ]


class _VkInstanceCreateInfo(ctypes.Structure):
    _fields_ = [
        ("sType", ctypes.c_uint32),
        ("pNext", ctypes.c_void_p),
        ("flags", ctypes.c_uint32),
        ("pApplicationInfo", ctypes.POINTER(_VkApplicationInfo)),
        ("enabledLayerCount", ctypes.c_uint32),
        ("ppEnabledLayerNames", ctypes.c_void_p),
        ("enabledExtensionCount", ctypes.c_uint32),
        ("ppEnabledExtensionNames", ctypes.c_void_p),
    ]


def probe_vulkan_device(loader_path=None):
    try:
        library = _windows_library(loader_path or "vulkan-1.dll")
    except RuntimeReadinessError as exc:
        if exc.code == "LFS_DRIVER_LOADER_MISSING":
            _fail("LFS_VULKAN_LOADER_MISSING", "Vulkan loader is unavailable; reinstall the graphics driver")
        raise
    application = _VkApplicationInfo(
        0,
        None,
        b"xPano readiness",
        1,
        b"xPano",
        1,
        1 << 22,
    )
    create_info = _VkInstanceCreateInfo(1, None, 0, ctypes.pointer(application), 0, None, 0, None)
    instance = ctypes.c_void_p()
    create_instance = library.vkCreateInstance
    create_instance.argtypes = [ctypes.POINTER(_VkInstanceCreateInfo), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    create_instance.restype = ctypes.c_int32
    if create_instance(ctypes.byref(create_info), None, ctypes.byref(instance)) != 0 or not instance.value:
        _fail("LFS_VULKAN_DEVICE_UNAVAILABLE", "Vulkan could not create an instance for the active graphics driver")
    try:
        enumerate_devices = library.vkEnumeratePhysicalDevices
        enumerate_devices.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
        enumerate_devices.restype = ctypes.c_int32
        count = ctypes.c_uint32()
        if enumerate_devices(instance, ctypes.byref(count), None) != 0:
            _fail("LFS_VULKAN_DEVICE_UNAVAILABLE", "Vulkan physical-device enumeration failed")
        if count.value < 1:
            _fail("LFS_VULKAN_NO_DEVICE", "No Vulkan-capable graphics device was detected")
        return {"status": "ready", "deviceCount": count.value}
    finally:
        destroy_instance = library.vkDestroyInstance
        destroy_instance.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        destroy_instance.restype = None
        destroy_instance(instance, None)


def _validate_lfs_dataset(dataset):
    dataset = Path(dataset)
    required = [
        dataset / "images",
        dataset / "sparse" / "0" / "cameras.bin",
        dataset / "sparse" / "0" / "images.bin",
        dataset / "sparse" / "0" / "points3D.bin",
    ]
    if not required[0].is_dir() or not all(path.is_file() for path in required[1:]):
        _fail("TRAINING_DATASET_INVALID", "Training data requires images and sparse/0 COLMAP model files")


def _verify_lfs_output_writable(output):
    output = Path(output)
    try:
        output.mkdir(parents=True, exist_ok=True)
        probe = output / f".xpano-lfs-write-{uuid.uuid4().hex}.tmp"
        with probe.open("xb") as stream:
            stream.write(b"xpano")
        probe.unlink()
    except OSError as exc:
        _fail("TRAINING_OUTPUT_NOT_WRITABLE", f"Training output directory is not writable: {exc}")


def _input_readiness(check):
    try:
        check()
        return {"status": "ready", "code": "", "message": ""}
    except RuntimeReadinessError as exc:
        return {"status": "unavailable", "code": exc.code, "message": str(exc)}


def probe_lichtfeld_training(resource_root, executable, profile_root, dataset, output):
    manifest = _validate_lichtfeld_sentinels(resource_root, executable)
    version_output = _probe_lfs_version(executable, profile_root)
    if f"v{manifest['version']}" not in version_output:
        _fail(
            "LFS_VERSION_MISMATCH",
            f"Bundled LichtFeld version {manifest['version']} does not match the executable version output",
        )
    cuda = probe_cuda_device()
    vulkan = probe_vulkan_device(Path(executable).resolve(strict=False).parent / "vulkan-1.dll")
    dataset_status = _input_readiness(lambda: _validate_lfs_dataset(dataset))
    output_status = _input_readiness(lambda: _verify_lfs_output_writable(output))
    return {
        "status": "ready" if dataset_status["status"] == "ready" and output_status["status"] == "ready" else "not_ready",
        "version": manifest["version"],
        "cuda": cuda,
        "vulkan": vulkan,
        "dataset": dataset_status,
        "output": output_status,
    }


def _wheel_supports_abi(filename, target_abi):
    try:
        _prefix, python_tag, abi_tag, platform_tag = filename[:-4].rsplit("-", 3)
    except ValueError:
        return False
    if platform_tag == "any" and abi_tag == "none":
        return True
    python_tags = python_tag.split(".")
    if target_abi in python_tags and abi_tag in {target_abi, "abi3"}:
        return True
    if abi_tag == "abi3":
        target_version = int(target_abi[2:])
        return any(
            tag.startswith("cp") and tag[2:].isdigit() and int(tag[2:]) <= target_version
            for tag in python_tags
        )
    return False


def load_bundled_runtime_manifest(path, resource_root):
    path = Path(path)
    resource_root = Path(resource_root).resolve()
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        _fail("MANIFEST_INVALID", f"Failed to read bundled runtime manifest: {exc}")
    if manifest.get("schemaVersion") != 1:
        _fail("MANIFEST_INVALID", "Unsupported bundled runtime manifest schema")
    if manifest.get("platform") != "windows-x86_64":
        _fail("UNSUPPORTED_PLATFORM", "Bundled runtime manifest is not for Windows x64")
    if not isinstance(manifest.get("runtimeVersion"), str) or not manifest["runtimeVersion"]:
        _fail("MANIFEST_INVALID", "Bundled runtime version is missing")
    metashape = manifest.get("metashape")
    profiles = metashape.get("profiles") if isinstance(metashape, dict) else None
    artifacts = metashape.get("artifacts") if isinstance(metashape, dict) else None
    if not isinstance(profiles, dict) or not profiles:
        _fail("MANIFEST_INVALID", "Metashape profiles are missing")
    if not isinstance(artifacts, list) or not artifacts:
        _fail("MANIFEST_INVALID", "Metashape artifacts are missing")
    by_id = {}
    for item in artifacts:
        if not isinstance(item, dict):
            _fail("MANIFEST_INVALID", "Metashape artifact must be an object")
        artifact_id = item.get("id")
        filename = item.get("filename")
        relative = item.get("path")
        digest = str(item.get("sha256", "")).lower()
        if not isinstance(artifact_id, str) or not artifact_id or artifact_id in by_id:
            _fail("MANIFEST_INVALID", "Metashape artifact ids must be unique")
        if not isinstance(filename, str) or not filename.endswith(".whl"):
            _fail("MANIFEST_INVALID", f"Metashape artifact {artifact_id} is not a wheel")
        if not (filename.endswith("-win_amd64.whl") or filename.endswith("-any.whl")):
            _fail("MANIFEST_INVALID", f"Metashape artifact {artifact_id} is not Windows x64 compatible")
        relative_path = Path(relative) if isinstance(relative, str) else Path()
        if not relative or relative_path.is_absolute() or ".." in relative_path.parts:
            _fail("MANIFEST_INVALID", f"Metashape artifact {artifact_id} path is invalid")
        source = (resource_root / relative_path).resolve()
        try:
            source.relative_to(resource_root)
        except ValueError:
            _fail("MANIFEST_INVALID", f"Metashape artifact {artifact_id} escapes resources")
        if not isinstance(item.get("size"), int) or item["size"] <= 0:
            _fail("MANIFEST_INVALID", f"Metashape artifact {artifact_id} size is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            _fail("MANIFEST_INVALID", f"Metashape artifact {artifact_id} SHA-256 is invalid")
        if not isinstance(item.get("license"), str) or not item["license"].strip():
            _fail("MANIFEST_INVALID", f"Metashape artifact {artifact_id} license is missing")
        normalized = dict(item)
        normalized["sha256"] = digest
        normalized["source"] = source
        by_id[artifact_id] = normalized
    for abi, ids in profiles.items():
        if not re.fullmatch(r"cp\d{2,3}", str(abi)):
            _fail("MANIFEST_INVALID", f"Invalid Metashape ABI profile: {abi}")
        if not isinstance(ids, list) or not ids or not all(item in by_id for item in ids):
            _fail("MANIFEST_INVALID", f"Metashape ABI profile {abi} references invalid artifacts")
        incompatible = [by_id[item]["filename"] for item in ids if not _wheel_supports_abi(by_id[item]["filename"], abi)]
        if incompatible:
            _fail(
                "MANIFEST_INVALID",
                f"Metashape ABI profile {abi} contains incompatible wheels: {', '.join(incompatible)}",
            )
    manifest["_metashapeArtifactsById"] = by_id
    manifest["_resourceRoot"] = resource_root
    return manifest


def metashape_profile(manifest, abi):
    profiles = manifest["metashape"]["profiles"]
    if abi not in profiles:
        raise RuntimeReadinessError(
            "UNSUPPORTED_ABI",
            f"Metashape Python {abi} is unsupported; supported ABIs: {', '.join(sorted(profiles))}",
        )
    by_id = manifest["_metashapeArtifactsById"]
    return [by_id[item] for item in profiles[abi]]


def _run_python(python, code):
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    env["PYTHONNOUSERSITE"] = "1"
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        [str(python), "-c", code],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _probe_tool(executable, arguments, expected_marker):
    env = os.environ.copy()
    env["PYTHONNOUSERSITE"] = "1"
    try:
        completed = subprocess.run(
            [str(executable), *arguments],
            cwd=str(Path(executable).resolve().parent),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    output = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()
    if completed.returncode != 0:
        return False, output or f"process exited with code {completed.returncode}"
    if expected_marker.casefold() not in output.casefold():
        return False, output or f"expected marker was not found: {expected_marker}"
    return True, output


def _missing_media_foundation_dlls():
    if os.name != "nt":
        return []
    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    return [name for name in ("MFPlat.dll", "MF.dll", "MFReadWrite.dll") if not (system32 / name).is_file()]


def metashape_python(metashape_exe):
    requested = str(metashape_exe).strip()
    if len(requested) >= 2 and requested.startswith('"') and requested.endswith('"'):
        requested = requested[1:-1].strip()
    resolved = shutil.which(requested) if requested and Path(requested).name == requested else None
    metashape_exe = Path(resolved or requested).resolve()
    if not metashape_exe.is_file():
        raise RuntimeReadinessError("METASHAPE_MISSING", f"Metashape executable was not found: {metashape_exe}")
    python = metashape_exe.parent / "python" / "python.exe"
    if not python.is_file():
        raise RuntimeReadinessError("METASHAPE_PYTHON_MISSING", f"Metashape Python was not found: {python}")
    return metashape_exe, python


def python_abi(python):
    completed = _run_python(python, "import sys; print(f'cp{sys.version_info[0]}{sys.version_info[1]}')")
    value = completed.stdout.strip()
    if completed.returncode != 0 or not re.fullmatch(r"cp\d{2,3}", value):
        raise RuntimeReadinessError(
            "ABI_PROBE_FAILED",
            (completed.stderr or completed.stdout or "Failed to detect Metashape Python ABI").strip(),
        )
    return value


def _probe_metashape_runtime(metashape_exe, resource_root, site_packages=None):
    probe_script = Path(resource_root) / "scripts" / "metashape_runtime_probe.py"
    command = [str(metashape_exe), "-r", str(probe_script)]
    command.extend(metashape_runtime_cli_args(site_packages))
    try:
        completed = subprocess.run(
            command,
            env=build_metashape_process_env(resource_root, site_packages),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Unable to start Metashape runtime probe: {exc}"
    output = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()
    return completed.returncode == 0 and "XPANO_METASHAPE_RUNTIME_READY:" in completed.stdout, output


def probe_metashape_runtime(metashape_exe, resource_root, site_packages=None):
    ready, _detail = _probe_metashape_runtime(metashape_exe, resource_root, site_packages)
    return ready


def _metashape_runtime_id(metashape_exe, python, abi, bundle_version):
    exe_stat = metashape_exe.stat()
    python_stat = python.stat()
    identity = json.dumps(
        {
            "exe": str(metashape_exe).casefold(),
            "exeSize": exe_stat.st_size,
            "exeMtimeNs": exe_stat.st_mtime_ns,
            "pythonSize": python_stat.st_size,
            "pythonMtimeNs": python_stat.st_mtime_ns,
            "abi": abi,
            "bundleVersion": bundle_version,
        },
        sort_keys=True,
    )
    fingerprint = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"{fingerprint}-{abi}-{bundle_version}"


def ensure_metashape_runtime(resource_root, state_root, metashape_exe, event=None, cancelled=None):
    resource_root = Path(resource_root).resolve()
    manifest = load_bundled_runtime_manifest(
        resource_root / "runtime" / "bundled-runtime-manifest.json",
        resource_root,
    )
    metashape_exe, python = metashape_python(metashape_exe)
    abi = python_abi(python)
    artifacts = metashape_profile(manifest, abi)
    native_ready, _native_detail = _probe_metashape_runtime(metashape_exe, resource_root)
    if native_ready:
        return {
            "status": "ready",
            "source": "metashape",
            "metashapePath": str(metashape_exe),
            "pythonAbi": abi,
            "sitePackages": "",
            "reused": True,
        }
    runtime_id = _metashape_runtime_id(
        metashape_exe, python, abi, manifest["runtimeVersion"]
    )
    pip_pyz = resource_root / "runtime" / "pip.pyz"
    last_probe_detail = ""

    def probe(site):
        nonlocal last_probe_detail
        ready, last_probe_detail = _probe_metashape_runtime(metashape_exe, resource_root, site)
        return ready

    try:
        result = bootstrap_local_runtime(
            runtime_name="metashape",
            runtime_id=runtime_id,
            runtime_version=manifest["runtimeVersion"],
            state_root=state_root,
            artifacts=artifacts,
            install=lambda wheels, site: install_verified_wheels(
                python, pip_pyz, wheels, site, cancelled=cancelled
            ),
            probe=probe,
            event=event,
            cancelled=cancelled,
        )
    except BootstrapError as exc:
        if exc.code == "PROBE_FAILED" and last_probe_detail:
            raise BootstrapError(
                "METASHAPE_RUNNER_IMPORT_FAILED",
                "Metashape could not import xPano's verified NumPy/OpenCV runtime: " + last_probe_detail,
            ) from exc
        raise
    return {
        "status": "ready",
        "source": "xpano",
        "metashapePath": str(metashape_exe),
        "pythonAbi": abi,
        "sitePackages": result["sitePackages"],
        "reused": result["reused"],
    }


def probe_bundled_resources(resource_root):
    root = Path(resource_root)
    def configured_path(name, fallback):
        value = os.environ.get(name, "").strip()
        return Path(value) if value else fallback

    required = {
        "python": root / "binaries/python/python.exe",
        "ffmpeg": configured_path("XPANO_FFMPEG", root / "tools/ffmpeg/bin/ffmpeg.exe"),
        "ffprobe": configured_path("XPANO_FFPROBE", root / "tools/ffmpeg/bin/ffprobe.exe"),
        "colmap": configured_path("XPANO_COLMAP", root / "tools/colmap/bin/colmap.exe"),
        "lichtfeld": root / "runtime/lichtfeld-studio/bin/LichtFeld-Studio.exe",
        "pip": root / "runtime/pip.pyz",
        "manifest": root / "runtime/bundled-runtime-manifest.json",
    }
    resources = {
        name: {"status": "ready" if path.is_file() and path.stat().st_size > 0 else "corrupt", "path": str(path)}
        for name, path in required.items()
    }
    if resources["python"]["status"] == "ready":
        missing_media = _missing_media_foundation_dlls()
        if missing_media:
            resources["python"] = {
                "status": "corrupt",
                "path": str(required["python"]),
                "detail": (
                    "Windows Media Foundation is missing ("
                    + ", ".join(missing_media)
                    + "). Install the Media Feature Pack on Windows N/KN."
                ),
            }
        else:
            imports = _run_python(required["python"], "import cv2, numpy, PIL, piexif, tqdm")
        if not missing_media and imports.returncode != 0:
            detail = (imports.stderr or imports.stdout).strip()
            if any(name in detail.casefold() for name in ("mfplat.dll", "mf.dll", "mfreadwrite.dll")):
                detail += "\nWindows Media Feature Pack is required on Windows N/KN editions."
            resources["python"] = {
                "status": "corrupt",
                "path": str(required["python"]),
                "detail": detail,
            }
    probes = {
        "ffmpeg": (["-version"], "ffmpeg version"),
        "ffprobe": (["-version"], "ffprobe version"),
        "colmap": (["-h"], "COLMAP"),
        "lichtfeld": (["--version"], "LichtFeld Studio v"),
    }
    for name, (arguments, marker) in probes.items():
        if resources[name]["status"] != "ready":
            continue
        ready, detail = _probe_tool(required[name], arguments, marker)
        if not ready:
            resources[name] = {
                "status": "corrupt",
                "path": str(required[name]),
                "detail": detail,
            }
    return resources


def _emit(prefix, payload):
    print(prefix + json.dumps(payload, ensure_ascii=False), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Probe and prepare xPano runtime dependencies")
    parser.add_argument("command", choices=["probe", "ensure", "lichtfeld-probe"])
    parser.add_argument("--root", required=True)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--backend", choices=["metashape", "colmap"], required=True)
    parser.add_argument("--metashape", default="metashape.exe")
    parser.add_argument("--lfs-executable")
    parser.add_argument("--profile-root")
    parser.add_argument("--dataset")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        if args.command == "lichtfeld-probe":
            root = Path(args.root)
            executable = Path(args.lfs_executable) if args.lfs_executable else root / "runtime" / "lichtfeld-studio" / "bin" / "LichtFeld-Studio.exe"
            profile_root = Path(args.profile_root) if args.profile_root else Path(args.state_root) / "lichtfeld-studio" / "profile"
            if not args.dataset or not args.output:
                _fail("TRAINING_DATASET_INVALID", "LichtFeld training probe requires dataset and output paths")
            result = probe_lichtfeld_training(root, executable, profile_root, Path(args.dataset), Path(args.output))
            _emit("LFS_READINESS_RESULT:", result)
            return 0
        bundled = probe_bundled_resources(args.root)
        corrupt = [name for name, value in bundled.items() if value["status"] != "ready"]
        if corrupt:
            details = [
                f"{name}: {bundled[name].get('detail', 'missing or empty')}"
                for name in corrupt
            ]
            raise RuntimeReadinessError(
                "BUNDLED_RUNTIME_CORRUPT",
                "Bundled runtime is missing or corrupt: " + "; ".join(details),
            )
        result = {"status": "ready", "backend": args.backend, "resources": bundled, "sitePackages": ""}
        if args.backend == "metashape":
            if args.command == "ensure":
                result["metashape"] = ensure_metashape_runtime(
                    args.root,
                    args.state_root,
                    args.metashape,
                    event=lambda phase, message, progress: _emit(
                        "RUNTIME_EVENT:",
                        {"phase": phase, "message": message, "progress": progress},
                    ),
                )
                result["sitePackages"] = result["metashape"]["sitePackages"]
            else:
                metashape_exe, python = metashape_python(args.metashape)
                abi = python_abi(python)
                manifest = load_bundled_runtime_manifest(
                    Path(args.root) / "runtime/bundled-runtime-manifest.json", args.root
                )
                metashape_profile(manifest, abi)
                result["metashape"] = {
                    "status": "ready" if probe_metashape_runtime(metashape_exe, Path(args.root)) else "dependencies_missing",
                    "metashapePath": str(metashape_exe),
                    "pythonAbi": abi,
                }
        _emit("RUNTIME_RESULT:", result)
        return 0
    except (RuntimeReadinessError, BootstrapError) as exc:
        prefix = "LFS_READINESS_ERROR:" if args.command == "lichtfeld-probe" else "RUNTIME_ERROR:"
        _emit(prefix, {"code": exc.code, "message": str(exc)})
        return 2
    except Exception as exc:
        if args.command == "lichtfeld-probe":
            _emit("LFS_READINESS_ERROR:", {"code": "LFS_READINESS_FAILED", "message": str(exc)})
        else:
            _emit("RUNTIME_ERROR:", {"code": "READINESS_FAILED", "message": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
