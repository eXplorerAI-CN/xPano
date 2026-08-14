from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import uuid
import zipfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.windows_runtime import WindowsRuntimeError, deploy_windows_runtime, load_windows_runtime


class ReleaseStagingError(RuntimeError):
    pass


EXCLUDED_PARTS = {".git", "__pycache__", "_downloads"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".pdb"}
RUNTIME_POWERSHELL = {"configure_environment.ps1", "install_lfs_densify.ps1"}
SUPPORTED_METASHAPE_ABIS = {"cp39", "cp310", "cp311", "cp312"}
LICHTFELD_MANIFEST_RELATIVE_PATH = Path("runtime") / "lichtfeld-studio-manifest.json"


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


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_file(source, destination):
    source = Path(source)
    if not source.is_file():
        raise ReleaseStagingError(f"Required release file is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination, follow_symlinks=True)


def _portable_file(relative, source, kind):
    if any(part in EXCLUDED_PARTS for part in relative.parts):
        return False
    if source.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    if kind == "scripts":
        return source.suffix.lower() == ".py" or source.name in RUNTIME_POWERSHELL
    if kind == "colmap" and (source.name.lower().endswith("_test.exe") or source.name == "RUN_TESTS.bat"):
        return False
    return True


def _copy_tree(source, destination, kind, excluded_top_levels=()):
    source = Path(source)
    if not source.is_dir():
        raise ReleaseStagingError(f"Required release directory is missing: {source}")
    excluded_top_levels = set(excluded_top_levels)
    for item in source.rglob("*"):
        if not item.is_file():
            continue
        relative = item.relative_to(source)
        if relative.parts and relative.parts[0] in excluded_top_levels:
            continue
        if _portable_file(relative, item, kind):
            _copy_file(item, destination / relative)


def _validate_tool(path, name):
    path = Path(path)
    if not path.is_file():
        raise ReleaseStagingError(f"{name} was not found: {path}")
    resolved = path.resolve(strict=True)
    if resolved.stat().st_size <= 0:
        raise ReleaseStagingError(f"{name} resolved to an empty file: {resolved}")
    return resolved


def _validate_bundled_runtime_manifest(root):
    root = Path(root)
    manifest_path = root / "runtime" / "bundled-runtime-manifest.json"
    notices_path = root / "runtime" / "THIRD_PARTY_NOTICES.txt"
    if not notices_path.is_file() or notices_path.stat().st_size <= 0:
        raise ReleaseStagingError(f"Bundled runtime notices are missing: {notices_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ReleaseStagingError(f"Bundled runtime manifest is invalid: {exc}") from exc
    if manifest.get("schemaVersion") != 1 or manifest.get("platform") != "windows-x86_64":
        raise ReleaseStagingError("Bundled runtime manifest schema/platform is unsupported")
    metashape = manifest.get("metashape")
    profiles = metashape.get("profiles") if isinstance(metashape, dict) else None
    artifacts = metashape.get("artifacts") if isinstance(metashape, dict) else None
    if not isinstance(profiles, dict) or set(profiles) != SUPPORTED_METASHAPE_ABIS:
        raise ReleaseStagingError(
            "Bundled runtime manifest must contain exactly cp39/cp310/cp311/cp312 Metashape profiles"
        )
    if not isinstance(artifacts, list) or not artifacts:
        raise ReleaseStagingError("Bundled runtime manifest has no Metashape artifacts")
    by_id = {}
    for artifact in artifacts:
        artifact_id = artifact.get("id") if isinstance(artifact, dict) else None
        relative = artifact.get("path") if isinstance(artifact, dict) else None
        filename = artifact.get("filename") if isinstance(artifact, dict) else None
        if not artifact_id or artifact_id in by_id:
            raise ReleaseStagingError("Bundled runtime artifact ids must be unique")
        relative_path = Path(relative) if isinstance(relative, str) else Path()
        if not relative or relative_path.is_absolute() or ".." in relative_path.parts:
            raise ReleaseStagingError(f"Bundled runtime artifact path is invalid: {artifact_id}")
        if not isinstance(filename, str) or not filename.endswith(".whl"):
            raise ReleaseStagingError(f"Bundled runtime artifact is not a wheel: {artifact_id}")
        if not (filename.endswith("-win_amd64.whl") or filename.endswith("-any.whl")):
            raise ReleaseStagingError(f"Bundled runtime artifact is not Windows x64: {artifact_id}")
        if not str(artifact.get("license", "")).strip():
            raise ReleaseStagingError(f"Bundled runtime artifact license is missing: {artifact_id}")
        source = root / relative_path
        expected_size = artifact.get("size")
        expected_hash = str(artifact.get("sha256", "")).lower()
        if (
            not source.is_file()
            or not isinstance(expected_size, int)
            or source.stat().st_size != expected_size
            or _sha256(source) != expected_hash
        ):
            raise ReleaseStagingError(f"Bundled runtime artifact is missing or corrupt: {source}")
        by_id[artifact_id] = artifact
    for abi, ids in profiles.items():
        if not isinstance(ids, list) or not ids or not all(item in by_id for item in ids):
            raise ReleaseStagingError(f"Bundled runtime Metashape profile is incomplete: {abi}")
        incompatible = [by_id[item]["filename"] for item in ids if not _wheel_supports_abi(by_id[item]["filename"], abi)]
        if incompatible:
            raise ReleaseStagingError(
                f"Bundled runtime Metashape profile {abi} contains incompatible wheels: {', '.join(incompatible)}"
            )
    return manifest


def _manifest_relative_path(value, label):
    if not isinstance(value, str) or not value:
        raise ReleaseStagingError(f"LichtFeld runtime manifest {label} is invalid")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or relative.name in {"", "."}:
        raise ReleaseStagingError(f"LichtFeld runtime manifest {label} escapes the runtime root")
    return relative


def _load_lichtfeld_runtime_manifest(root):
    root = Path(root)
    manifest_path = root / LICHTFELD_MANIFEST_RELATIVE_PATH
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ReleaseStagingError(
            f"LichtFeld runtime manifest is missing or invalid: {manifest_path}"
        ) from exc
    if (
        manifest.get("schemaVersion") != 1
        or manifest.get("runtime") != "lichtfeld-studio"
        or not isinstance(manifest.get("version"), str)
        or not manifest["version"].strip()
        or not isinstance(manifest.get("upstreamCommit"), str)
        or not manifest["upstreamCommit"].strip()
    ):
        raise ReleaseStagingError("LichtFeld runtime manifest metadata is unsupported")
    archive = manifest.get("archive")
    if not isinstance(archive, dict):
        raise ReleaseStagingError("LichtFeld runtime manifest archive metadata is missing")
    archive_filename = archive.get("filename")
    archive_size = archive.get("size")
    archive_hash = str(archive.get("sha256", "")).lower()
    if (
        not isinstance(archive_filename, str)
        or not archive_filename.endswith(".zip")
        or not isinstance(archive_size, int)
        or archive_size <= 0
        or not _is_sha256(archive_hash)
    ):
        raise ReleaseStagingError("LichtFeld runtime manifest archive metadata is invalid")
    sentinels = manifest.get("sentinels")
    if not isinstance(sentinels, list) or not sentinels:
        raise ReleaseStagingError("LichtFeld runtime manifest sentinels are missing")
    sentinel_paths = [_manifest_relative_path(value, "sentinel") for value in sentinels]
    if len({path.as_posix() for path in sentinel_paths}) != len(sentinel_paths):
        raise ReleaseStagingError("LichtFeld runtime manifest sentinels must be unique")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ReleaseStagingError("LichtFeld runtime manifest file inventory is missing")
    records = {}
    for item in files:
        if not isinstance(item, dict):
            raise ReleaseStagingError("LichtFeld runtime manifest file entry is invalid")
        relative = _manifest_relative_path(item.get("path"), "file path")
        key = relative.as_posix()
        size = item.get("size")
        digest = str(item.get("sha256", "")).lower()
        if key in records or not isinstance(size, int) or size < 0 or not _is_sha256(digest):
            raise ReleaseStagingError("LichtFeld runtime manifest file entry is invalid")
        records[key] = {"path": relative, "size": size, "sha256": digest}
    missing_sentinels = [path.as_posix() for path in sentinel_paths if path.as_posix() not in records]
    if missing_sentinels:
        raise ReleaseStagingError(
            "LichtFeld runtime manifest sentinels are absent from the inventory: "
            + ", ".join(missing_sentinels)
        )
    return {"manifest": manifest, "records": records, "sentinels": sentinel_paths}


def _is_sha256(value):
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _validate_lichtfeld_runtime_tree(tree, contract, label, portable_only=False):
    tree = Path(tree)
    expected = contract["records"]
    if portable_only:
        expected = {
            key: record
            for key, record in expected.items()
            if _portable_file(record["path"], record["path"], "runtime")
        }
    actual = {
        path.relative_to(tree).as_posix(): path
        for path in tree.rglob("*")
        if path.is_file()
    }
    missing = sorted(set(expected) - set(actual))
    unexpected = sorted(set(actual) - set(expected))
    corrupt = []
    for key in sorted(set(expected) & set(actual)):
        record = expected[key]
        path = actual[key]
        if path.stat().st_size != record["size"] or _sha256(path) != record["sha256"]:
            corrupt.append(key)
    if missing or unexpected or corrupt:
        details = []
        for name, values in (("missing", missing), ("unexpected", unexpected), ("corrupt", corrupt)):
            if values:
                preview = ", ".join(values[:5])
                suffix = " ..." if len(values) > 5 else ""
                details.append(f"{name} ({len(values)}): {preview}{suffix}")
        raise ReleaseStagingError(
            f"LichtFeld runtime {label} differs from the pinned manifest: " + "; ".join(details)
        )
    sentinel_root = tree
    missing_sentinels = [
        path.as_posix() for path in contract["sentinels"] if not (sentinel_root / path).is_file()
    ]
    if missing_sentinels:
        raise ReleaseStagingError(
            "LichtFeld runtime "
            + label
            + " is missing required resources: "
            + ", ".join(missing_sentinels)
        )


def _validate_lichtfeld_archive(archive_path, contract):
    archive_path = Path(archive_path)
    metadata = contract["manifest"]["archive"]
    if not archive_path.is_file():
        raise ReleaseStagingError(f"Pinned LichtFeld archive is missing: {archive_path}")
    if archive_path.name != metadata["filename"]:
        raise ReleaseStagingError(
            "Pinned LichtFeld archive filename does not match the runtime manifest: "
            + archive_path.name
        )
    if archive_path.stat().st_size != metadata["size"] or _sha256(archive_path) != metadata["sha256"]:
        raise ReleaseStagingError(f"Pinned LichtFeld archive is corrupt: {archive_path}")
    try:
        with zipfile.ZipFile(archive_path) as package:
            entries = {}
            for info in package.infolist():
                if info.is_dir():
                    continue
                relative = _manifest_relative_path(info.filename.replace("\\", "/"), "archive entry")
                key = relative.as_posix()
                if key in entries:
                    raise ReleaseStagingError(
                        f"Pinned LichtFeld archive contains duplicate entry: {key}"
                    )
                entries[key] = info
    except ReleaseStagingError:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReleaseStagingError(f"Pinned LichtFeld archive is unreadable: {archive_path}") from exc
    expected = contract["records"]
    missing = sorted(set(expected) - set(entries))
    unexpected = sorted(set(entries) - set(expected))
    wrong_size = [
        key
        for key in sorted(set(expected) & set(entries))
        if entries[key].file_size != expected[key]["size"]
    ]
    if missing or unexpected or wrong_size:
        details = []
        for name, values in (("missing", missing), ("unexpected", unexpected), ("wrong size", wrong_size)):
            if values:
                preview = ", ".join(values[:5])
                suffix = " ..." if len(values) > 5 else ""
                details.append(f"{name} ({len(values)}): {preview}{suffix}")
        raise ReleaseStagingError(
            "Pinned LichtFeld archive differs from the runtime manifest: " + "; ".join(details)
        )
    return archive_path


def _stage_lichtfeld_archive(archive_path, destination, contract):
    archive_path = _validate_lichtfeld_archive(archive_path, contract)
    destination = Path(destination)
    with zipfile.ZipFile(archive_path) as package:
        for key, record in contract["records"].items():
            target = destination / record["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(key) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
    _validate_lichtfeld_runtime_tree(destination, contract, "extracted archive")
    for path in sorted(destination.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_file() and not _portable_file(path.relative_to(destination), path, "runtime"):
            path.unlink()
        elif path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass


def _release_manifest(stage, version):
    files = []
    for path in sorted((item for item in stage.rglob("*") if item.is_file()), key=lambda item: item.relative_to(stage).as_posix()):
        relative = path.relative_to(stage).as_posix()
        if relative == "release-manifest.json":
            continue
        files.append({"path": relative, "size": path.stat().st_size, "sha256": _sha256(path)})
    return {
        "schemaVersion": 1,
        "version": version,
        "platform": "windows-x86_64",
        "files": files,
    }


def _stage_full_offline_artifacts(root, destination, source):
    try:
        manifest = json.loads((root / "runtime" / "densify-runtime-manifest.json").read_text(encoding="utf-8"))
    except Exception as exc:
        raise ReleaseStagingError(f"Densification runtime manifest is invalid: {exc}") from exc
    artifacts = manifest.get("artifacts")
    profiles = manifest.get("profiles")
    if not isinstance(artifacts, list) or not isinstance(profiles, dict) or set(profiles) != {"cpu", "cuda"}:
        raise ReleaseStagingError("Full offline release requires CPU and CUDA densification profiles")
    by_id = {item.get("id"): item for item in artifacts if isinstance(item, dict) and item.get("id")}
    selected_ids = {
        artifact_id
        for profile in profiles.values()
        for artifact_id in profile.get("artifacts", [])
    }
    if not selected_ids or not selected_ids.issubset(by_id):
        raise ReleaseStagingError("Densification profiles reference invalid artifacts")
    source = Path(source)
    for artifact_id in sorted(selected_ids):
        artifact = by_id[artifact_id]
        digest = str(artifact.get("sha256", "")).lower()
        expected_size = artifact.get("size")
        artifact_source = source / digest
        if (
            len(digest) != 64
            or not artifact_source.is_file()
            or artifact_source.stat().st_size != expected_size
            or _sha256(artifact_source) != digest
        ):
            raise ReleaseStagingError(
                f"Full offline densification artifact is missing or corrupt: {artifact_id}"
            )
        _copy_file(artifact_source, destination / digest)


def stage_release_resources(
    root,
    destination,
    ffmpeg,
    ffprobe,
    webview2_loader,
    version,
    full_offline_artifacts=None,
    lichtfeld_archive=None,
):
    root = Path(root).resolve(strict=True)
    _validate_bundled_runtime_manifest(root)
    lichtfeld_contract = _load_lichtfeld_runtime_manifest(root)
    if lichtfeld_archive:
        _validate_lichtfeld_archive(lichtfeld_archive, lichtfeld_contract)
    else:
        _validate_lichtfeld_runtime_tree(
            root / "runtime" / "lichtfeld-studio",
            lichtfeld_contract,
            "source tree",
        )
    try:
        load_windows_runtime(root)
    except WindowsRuntimeError as exc:
        raise ReleaseStagingError(f"windows runtime payload is invalid: {exc}") from exc
    destination = Path(destination).resolve(strict=False)
    if destination.exists():
        raise ReleaseStagingError(f"Release staging destination already exists: {destination}")
    temporary = destination.with_name(f"{destination.name}.staging-{uuid.uuid4().hex}")
    temporary.mkdir(parents=True)
    try:
        _copy_tree(root / "binaries" / "python", temporary / "binaries" / "python", "python")
        _copy_tree(root / "scripts", temporary / "scripts", "scripts")
        _copy_tree(root / "tools" / "colmap", temporary / "tools" / "colmap", "colmap")
        _copy_tree(
            root / "tools" / "offline-wheels" / "app",
            temporary / "tools" / "offline-wheels" / "app",
            "wheels",
        )
        _copy_tree(
            root / "tools" / "offline-wheels" / "metashape",
            temporary / "tools" / "offline-wheels" / "metashape",
            "wheels",
        )
        _copy_tree(
            root / "tools" / "lichtfeld-densification-plugin",
            temporary / "tools" / "lichtfeld-densification-plugin",
            "plugin",
        )
        _copy_tree(
            root / "runtime",
            temporary / "runtime",
            "runtime",
            excluded_top_levels={"lichtfeld-studio"} if lichtfeld_archive else (),
        )
        if lichtfeld_archive:
            _stage_lichtfeld_archive(
                lichtfeld_archive,
                temporary / "runtime" / "lichtfeld-studio",
                lichtfeld_contract,
            )
        _validate_lichtfeld_runtime_tree(
            temporary / "runtime" / "lichtfeld-studio",
            lichtfeld_contract,
            "staged tree",
            portable_only=True,
        )
        _copy_tree(root / "luts", temporary / "luts", "luts")
        try:
            deploy_windows_runtime(
                root,
                [temporary / "tools" / "colmap" / "bin", temporary / "binaries" / "python"],
            )
        except WindowsRuntimeError as exc:
            raise ReleaseStagingError(f"windows runtime deployment failed: {exc}") from exc
        if full_offline_artifacts:
            _stage_full_offline_artifacts(
                root,
                temporary / "runtime" / "densify-artifacts" / "sha256",
                full_offline_artifacts,
            )
        _copy_file(
            root / "tools" / "webview2" / "MicrosoftEdgeWebView2RuntimeInstallerX64.exe",
            temporary / "tools" / "webview2" / "MicrosoftEdgeWebView2RuntimeInstallerX64.exe",
        )
        _copy_file(root / "requirements.txt", temporary / "requirements.txt")
        _copy_file(root / "metashape_requirements.txt", temporary / "metashape_requirements.txt")

        ffmpeg_source = _validate_tool(ffmpeg, "ffmpeg")
        ffprobe_source = _validate_tool(ffprobe, "ffprobe")
        webview2_loader_source = _validate_tool(webview2_loader, "WebView2Loader.dll")
        _copy_file(ffmpeg_source, temporary / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe")
        _copy_file(ffprobe_source, temporary / "tools" / "ffmpeg" / "bin" / "ffprobe.exe")
        _copy_file(webview2_loader_source, temporary / "WebView2Loader.dll")

        required = [
            temporary / "binaries" / "python" / "python.exe",
            temporary / "binaries" / "python" / "Lib" / "site-packages" / "tqdm" / "__init__.py",
            temporary / "scripts" / "run_xpano_tracks_job.py",
            temporary / "scripts" / "runtime_bootstrap.py",
            temporary / "scripts" / "lichtfeld_training.py",
            temporary / "scripts" / "export_image_cache.py",
            temporary / "scripts" / "export_remap.py",
            temporary / "scripts" / "fisheye_geometry.py",
            temporary / "scripts" / "metashape_runtime_env.py",
            temporary / "scripts" / "metashape_runtime_probe.py",
            temporary / "scripts" / "metashape_pipeline.py",
            temporary / "scripts" / "reexport_colmap_from_project.py",
            temporary / "scripts" / "inspect_metashape_components.py",
            temporary / "scripts" / "component_selection.py",
            temporary / "tools" / "colmap" / "bin" / "colmap.exe",
            temporary / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe",
            temporary / "tools" / "ffmpeg" / "bin" / "ffprobe.exe",
            temporary / "tools" / "lichtfeld-densification-plugin" / "densify.py",
            temporary / "tools" / "lichtfeld-densification-plugin" / "third_party" / "dinov3" / "hubconf.py",
            temporary / "tools" / "lichtfeld-densification-plugin" / "third_party" / "dinov3" / "LICENSE.md",
            temporary / "tools" / "offline-wheels" / "app" / "tqdm-4.68.3-py3-none-any.whl",
            temporary / "runtime" / "densify-runtime-manifest.json",
            temporary / "runtime" / "pip.pyz",
            temporary / "runtime" / "bundled-runtime-manifest.json",
            temporary / "runtime" / "windows-runtime-manifest.json",
            temporary / "runtime" / "lichtfeld-studio-manifest.json",
            temporary / "runtime" / "THIRD_PARTY_NOTICES.txt",
            temporary / "runtime" / "lichtfeld-studio" / "bin" / "LichtFeld-Studio.exe",
            temporary / "runtime" / "lichtfeld-studio" / "LICENSE",
            temporary / "luts" / "dji-osmo360-dlogm-rec709-v1.cube",
            temporary / "WebView2Loader.dll",
        ]
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise ReleaseStagingError("Required staged resources are missing: " + ", ".join(missing))
        if (temporary / "tools" / "torch-cache").exists() or (temporary / ".venv-densify").exists():
            raise ReleaseStagingError("Forbidden densification payload entered release staging")

        manifest_path = temporary / "release-manifest.json"
        manifest_path.write_text(
            json.dumps(_release_manifest(temporary, version), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, destination)
        return destination / "release-manifest.json"
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description="Create a clean, hashed xPano runtime resource stage.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--ffprobe", required=True)
    parser.add_argument("--webview2-loader", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--full-offline-artifacts")
    parser.add_argument("--lichtfeld-archive")
    args = parser.parse_args(argv)
    manifest = stage_release_resources(
        args.root,
        args.destination,
        args.ffmpeg,
        args.ffprobe,
        args.webview2_loader,
        args.version,
        args.full_offline_artifacts,
        args.lichtfeld_archive,
    )
    print(manifest, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
