import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path


CACHE_SCHEMA_VERSION = 1


def _sha256_file(path, chunk_size=1024 * 1024):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def build_image_signature(source, sensor, strategy, contract_version):
    payload = {
        "contractVersion": str(contract_version),
        "source": source,
        "sensor": sensor,
        "strategy": strategy,
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def source_record(path, cached=None):
    path = Path(path)
    stat = path.stat()
    resolved = str(path.resolve())
    if (
        isinstance(cached, dict)
        and cached.get("path") == resolved
        and cached.get("size") == stat.st_size
        and cached.get("mtimeNs") == stat.st_mtime_ns
        and cached.get("sha256")
    ):
        return dict(cached)
    return {
        "path": resolved,
        "size": stat.st_size,
        "mtimeNs": stat.st_mtime_ns,
        "sha256": _sha256_file(path),
    }


def output_record(path, root):
    path = Path(path)
    root = Path(root)
    relative = path.relative_to(root).as_posix()
    stat = path.stat()
    return {
        "name": relative,
        "size": stat.st_size,
        "mtimeNs": stat.st_mtime_ns,
        "sha256": _sha256_file(path),
    }


def empty_image_cache():
    return {"schemaVersion": CACHE_SCHEMA_VERSION, "cameras": {}}


def load_image_cache(path):
    path = Path(path)
    if not path.is_file():
        return empty_image_cache()
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if payload.get("schemaVersion") != CACHE_SCHEMA_VERSION:
        raise ValueError(f"Unsupported export image cache schema: {payload.get('schemaVersion')}")
    if not isinstance(payload.get("cameras"), dict):
        raise ValueError("Invalid export image cache cameras payload")
    return payload


def write_image_cache(path, cameras):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    payload = {"schemaVersion": CACHE_SCHEMA_VERSION, "cameras": cameras}
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _safe_relative_path(name):
    relative = Path(str(name))
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        return None
    return relative


def _verified_sources(entry, source_dir):
    outputs = entry.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        return None
    verified = []
    for record in outputs:
        if not isinstance(record, dict):
            return None
        relative = _safe_relative_path(record.get("name", ""))
        if relative is None:
            return None
        source = Path(source_dir) / relative
        if not source.is_file():
            return None
        stat = source.stat()
        if stat.st_size != record.get("size"):
            return None
        if record.get("mtimeNs") != stat.st_mtime_ns and _sha256_file(source) != record.get("sha256"):
            return None
        verified.append((source, relative))
    return verified


def refresh_output_records(records, root):
    refreshed = []
    for record in records:
        relative = _safe_relative_path(record.get("name", ""))
        if relative is None:
            raise ValueError(f"Unsafe cached output name: {record.get('name')}")
        path = Path(root) / relative
        stat = path.stat()
        refreshed.append({
            "name": relative.as_posix(),
            "size": stat.st_size,
            "mtimeNs": stat.st_mtime_ns,
            "sha256": record["sha256"],
        })
    return refreshed


def reuse_cached_outputs(cache, camera_id, signature, source_dir, destination_dir):
    entry = cache.get("cameras", {}).get(str(camera_id))
    if not isinstance(entry, dict) or entry.get("signature") != signature:
        return False
    verified = _verified_sources(entry, source_dir)
    if not verified:
        return False

    destination_dir = Path(destination_dir)
    created = []
    try:
        for source, relative in verified:
            destination = destination_dir / relative
            if destination.exists():
                raise FileExistsError(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(source, destination)
            except OSError:
                shutil.copy2(source, destination)
            created.append(destination)
        return True
    except Exception:
        for path in reversed(created):
            if path.exists():
                path.unlink()
        for directory in sorted({path.parent for path in created}, key=lambda item: len(item.parts), reverse=True):
            if directory.exists() and directory != destination_dir:
                try:
                    directory.rmdir()
                except OSError:
                    pass
        if destination_dir.exists():
            try:
                destination_dir.rmdir()
            except OSError:
                pass
        return False
