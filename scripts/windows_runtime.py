from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


WINDOWS_RUNTIME_NAMES = (
    "msvcp140.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "vcomp140.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
)


class WindowsRuntimeError(RuntimeError):
    pass


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_windows_runtime(root):
    root = Path(root).resolve()
    manifest_path = root / "runtime" / "windows-runtime-manifest.json"
    payload_root = root / "runtime" / "windows-x64"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise WindowsRuntimeError(f"Windows runtime manifest is invalid: {exc}") from exc
    if manifest.get("schemaVersion") != 1 or manifest.get("platform") != "windows-x86_64":
        raise WindowsRuntimeError("Windows runtime manifest schema/platform is unsupported")
    records = manifest.get("files")
    if not isinstance(records, list):
        raise WindowsRuntimeError("Windows runtime manifest has no files")
    by_name = {
        str(record.get("name", "")).lower(): record
        for record in records
        if isinstance(record, dict)
    }
    if set(by_name) != set(WINDOWS_RUNTIME_NAMES):
        raise WindowsRuntimeError("Windows runtime manifest must contain the complete fixed DLL set")
    verified = []
    for name in WINDOWS_RUNTIME_NAMES:
        record = by_name[name]
        source = payload_root / name
        expected_size = record.get("size")
        expected_hash = str(record.get("sha256", "")).lower()
        if (
            not source.is_file()
            or not isinstance(expected_size, int)
            or expected_size <= 0
            or source.stat().st_size != expected_size
            or _sha256(source) != expected_hash
        ):
            raise WindowsRuntimeError(f"Windows runtime file is missing or corrupt: {source}")
        verified.append(source)
    return verified


def deploy_windows_runtime(root, destinations):
    sources = load_windows_runtime(root)
    deployed = []
    for destination in map(Path, destinations):
        destination.mkdir(parents=True, exist_ok=True)
        for source in sources:
            target = destination / source.name
            shutil.copy2(source, target)
            if target.stat().st_size != source.stat().st_size or _sha256(target) != _sha256(source):
                raise WindowsRuntimeError(f"Windows runtime deployment verification failed: {target}")
            deployed.append(target)
    return deployed


def main(argv=None):
    parser = argparse.ArgumentParser(description="Verify or deploy xPano's fixed Windows x64 runtime DLL set.")
    parser.add_argument("command", choices=["verify", "deploy"])
    parser.add_argument("--root", required=True)
    parser.add_argument("--destination", action="append", default=[])
    args = parser.parse_args(argv)
    if args.command == "verify":
        files = load_windows_runtime(args.root)
    else:
        if not args.destination:
            parser.error("deploy requires at least one --destination")
        files = deploy_windows_runtime(args.root, args.destination)
    print("Windows runtime ready: " + ", ".join(str(path) for path in files), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
