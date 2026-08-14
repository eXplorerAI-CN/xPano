import argparse
import json
import math
import os
import shutil
import sys
import time
import warnings
from contextlib import nullcontext
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from PIL import Image, ImageOps

from scripts import xpano_tracks
from scripts.xpano_extract import apply_style_lut_to_image, prepare_lut_chain
from scripts.xpano_lut_presets import resolve_lut_paths
from scripts.xpano_tracks import (
    build_ordinary_video_track,
    build_panorama_track,
    load_manifest,
    make_track_id,
    photo_sensor_key,
    sample_evenly,
    validate_manifest,
    write_manifest,
)


RESULT_RELATIVE_PATH = Path("work") / "media_prepare_result.json"
THUMBNAIL_EDGE = 320


def configure_console_output():
    for stream in [sys.stdout, sys.stderr]:
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


def emit_line(line):
    try:
        print(line, flush=True)
    except OSError as exc:
        if getattr(exc, "errno", None) in {9, 22, 32}:
            raise SystemExit(0) from None
        raise


def emit_event(percent, message, stage, track_id=None, current=None, total=None, eta_seconds=None):
    payload = {
        "phase": "extract",
        "stage": stage,
        "trackId": track_id,
        "percent": max(0.0, min(100.0, float(percent))),
        "phasePercent": max(0.0, min(100.0, float(percent))),
        "message": message,
        "current": current,
        "total": total,
        "etaSeconds": eta_seconds,
    }
    emit_line("PIPELINE_EVENT:" + json.dumps(payload, ensure_ascii=False))


def emit_media_item(track_id, item):
    emit_line("MEDIA_ITEM:" + json.dumps({"trackId": track_id, "item": item}, ensure_ascii=False))


def _relative_artifact(project_root, path):
    root = Path(project_root).resolve()
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError(f"Artifact is outside the project: {resolved}") from exc


def _write_thumbnail(source, destination):
    source = Path(source)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(destination.suffix + ".tmp")
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=r"Corrupt EXIF data\..*", category=UserWarning)
        with Image.open(source) as image:
            if image.format == "JPEG":
                image.draft("RGB", (THUMBNAIL_EDGE * 2, THUMBNAIL_EDGE * 2))
            image = ImageOps.exif_transpose(image)
            image.thumbnail((THUMBNAIL_EDGE, THUMBNAIL_EDGE), Image.Resampling.LANCZOS)
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            image.save(temp, format="JPEG", quality=82, optimize=True)
    os.replace(temp, destination)


def _frames_per_second(extraction):
    extraction = extraction or {}
    value = extraction.get("framesPerSecond")
    if value is None and extraction.get("secondsPerFrame") is not None:
        legacy_interval = float(extraction["secondsPerFrame"])
        value = 1.0 / legacy_interval if legacy_interval > 0 else 0.0
    fps = float(value if value is not None else 1.0)
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError("framesPerSecond must be a finite number greater than 0")
    return fps


def _stage_source(source, destination):
    source = Path(source).resolve()
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(destination.suffix + ".tmp")
    temp.unlink(missing_ok=True)
    try:
        os.link(source, temp)
    except OSError:
        shutil.copy2(source, temp)
    os.replace(temp, destination)


def stage_photo_item(project_root, track_id, source, index, selected=True):
    project_root = Path(project_root).resolve()
    source = Path(source).resolve()
    extension = source.suffix.lower() or ".jpg"
    item_id = f"photo_{index:05d}"
    staged = project_root / "work" / "media" / track_id / f"{item_id}{extension}"
    thumbnail = project_root / "work" / "thumbnails" / track_id / f"{item_id}.jpg"
    if source != staged:
        _stage_source(source, staged)
    _write_thumbnail(staged, thumbnail)
    return {
        "id": item_id,
        "timestamp": None,
        "selected": bool(selected),
        "image": _relative_artifact(project_root, staged),
        "thumbnail": _relative_artifact(project_root, thumbnail),
    }


def _stage_single_video_item(project_root, track, source, index, selected=True):
    item_id = f"frame_{index:05d}"
    thumbnail = Path(project_root) / "work" / "thumbnails" / track["id"] / f"{item_id}.jpg"
    _write_thumbnail(source, thumbnail)
    start = float((track.get("trim") or {}).get("start") or 0.0)
    step = 1.0 / _frames_per_second(track.get("extraction"))
    return {
        "id": item_id,
        "timestamp": start + (index - 1) * step,
        "selected": bool(selected),
        "image": _relative_artifact(project_root, source),
        "thumbnail": _relative_artifact(project_root, thumbnail),
    }


def _stage_panorama_item(project_root, track, frame, index, selected=True):
    item_id = f"frame_{index:05d}"
    thumb_root = Path(project_root) / "work" / "thumbnails" / track["id"] / item_id
    left_thumb = thumb_root / "left.jpg"
    right_thumb = thumb_root / "right.jpg"
    _write_thumbnail(frame["left"], left_thumb)
    _write_thumbnail(frame["right"], right_thumb)
    start = float((track.get("trim") or {}).get("start") or 0.0)
    step = 1.0 / _frames_per_second(track.get("extraction"))
    return {
        "id": item_id,
        "timestamp": start + (index - 1) * step,
        "selected": bool(selected),
        "left": _relative_artifact(project_root, frame["left"]),
        "right": _relative_artifact(project_root, frame["right"]),
        "thumbnailLeft": _relative_artifact(project_root, left_thumb),
        "thumbnailRight": _relative_artifact(project_root, right_thumb),
    }


def _source_key(value):
    return os.path.normcase(os.path.normpath(str(Path(value).resolve(strict=False))))


def merge_manifest_tracks(project_tracks, previous_manifest, replacements):
    previous_by_source = {}
    for track in (previous_manifest or {}).get("tracks", []):
        sources = track.get("source_paths") or []
        if sources:
            previous_by_source[_source_key(sources[0])] = track
    merged = []
    for project_track in project_tracks:
        replacement = replacements.get(project_track["id"])
        if replacement is not None:
            merged.append(replacement)
            continue
        if project_track.get("status") not in {"ready", "prepared"}:
            continue
        previous = previous_by_source.get(_source_key(project_track["sourcePath"]))
        if previous is not None:
            merged.append(previous)
    return merged


def _eta_seconds(started, completed_fraction):
    if completed_fraction <= 0.02:
        return None
    elapsed = max(0.001, time.monotonic() - started)
    return max(0, round(elapsed * (1.0 - completed_fraction) / completed_fraction))


def _append_photo_sensor(groups, base_label, photo, identity):
    key = photo_sensor_key(identity)
    if key not in groups:
        suffix = "" if not groups else f"_{len(groups) + 1:02d}"
        groups[key] = {
            "sensor_id": f"{base_label}{suffix}",
            "sensor_label": f"{base_label}{suffix}",
            "camera_identity": identity,
            "photos": [],
        }
    groups[key]["photos"].append(str(photo))


def _atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    json.loads(temp.read_text(encoding="utf-8"))
    os.replace(temp, path)


def prepare_project(project_root, expected_revision, target_track_ids=None):
    project_root = Path(project_root).resolve()
    project_path = project_root / "xpano_project.json"
    project = json.loads(project_path.read_text(encoding="utf-8-sig"))
    if int(project.get("revision", -1)) != int(expected_revision):
        raise RuntimeError(
            f"revision_conflict: expected {expected_revision}, actual {project.get('revision')}"
        )
    project_tracks = project.get("tracks") or []
    requested = set(target_track_ids or [])
    targets = [
        track
        for track in project_tracks
        if (not requested or track["id"] in requested)
        and track.get("status") in {"draft", "stale", "failed", "interrupted", "prepared", "ready", "running"}
    ]
    if requested - {track["id"] for track in targets}:
        raise ValueError("One or more requested tracks are unavailable")
    if not targets:
        raise ValueError("No media tracks require preparation")

    manifest_path = project_root / "work" / "manifests" / "media_full.json"
    previous_manifest = load_manifest(manifest_path) if manifest_path.exists() else {}
    replacements = {}
    result_tracks = []
    started = time.monotonic()
    total_tracks = len(targets)

    for target_index, track in enumerate(targets):
        track_id = track["id"]
        source = track["sourcePath"]
        track_type = track["type"]
        extraction = track.get("extraction") or {}
        trim = track.get("trim") or {}
        frames_per_second = _frames_per_second(extraction)
        frame_limit = int(extraction.get("frameLimit") or 0)
        lut_paths = resolve_lut_paths(APP_ROOT, extraction, track_type, source)
        start_time = float(trim.get("start") or 0.0)
        end_time = float(trim.get("end") or 0.0)
        base_fraction = target_index / total_tracks

        emit_event(base_fraction * 100, f"正在检查 {track['label']}", "media.probe", track_id)

        def progress(current, total):
            local = min(1.0, max(0.0, float(current or 0) / max(1, float(total or 1))))
            fraction = (target_index + local * 0.82) / total_tracks
            emit_event(
                fraction * 100,
                f"正在处理 {track['label']} {int(current or 0)}/{int(total or 0)}",
                "media.decode",
                track_id,
                int(current or 0),
                int(total or 0),
                _eta_seconds(started, fraction),
            )

        def preview_pair(left, right):
            emit_line(f"PREVIEW:{left}|{right}")

        def preview_single(image, _duplicate):
            emit_line(f"PREVIEW:{image}|")

        legacy_index = project_tracks.index(track) + 1
        if track_type == "panoramic_video":
            manifest_track = build_panorama_track(
                legacy_index,
                source,
                project_root / "work",
                frames_per_second,
                frame_limit,
                start_time_seconds=start_time,
                end_time_seconds=end_time,
                preview_cb=preview_pair,
                progress_cb=progress,
                log_cb=emit_line,
                restoration_lut_path=str(lut_paths.restoration) if lut_paths.restoration else None,
                style_lut_path=str(lut_paths.style) if lut_paths.style else None,
            )
            source_items = manifest_track["frames"]
            old_selection = {item["id"]: item.get("selected", True) for item in track.get("items", [])}
            items = []
            for index, frame in enumerate(source_items, 1):
                item_id = f"frame_{index:05d}"
                items.append(
                    _stage_panorama_item(
                        project_root,
                        track,
                        frame,
                        index,
                        old_selection.get(item_id, True),
                    )
                )
                emit_media_item(track_id, items[-1])
                fraction = (target_index + 0.82 + 0.18 * index / max(1, len(source_items))) / total_tracks
                emit_event(fraction * 100, f"正在生成 {track['label']} 缩略图", "media.finalize", track_id, index, len(source_items), _eta_seconds(started, fraction))
        elif track_type == "ordinary_video":
            manifest_track = build_ordinary_video_track(
                legacy_index,
                source,
                project_root / "work",
                frames_per_second,
                frame_limit,
                start_time_seconds=start_time,
                end_time_seconds=end_time,
                preview_cb=preview_single,
                progress_cb=progress,
                log_cb=emit_line,
                camera_profile=track.get("cameraProfile") or "wide",
                style_lut_path=str(lut_paths.style) if lut_paths.style else None,
            )
            source_items = manifest_track["photos"]
            old_selection = {item["id"]: item.get("selected", True) for item in track.get("items", [])}
            items = []
            for index, image_path in enumerate(source_items, 1):
                item_id = f"frame_{index:05d}"
                items.append(_stage_single_video_item(project_root, track, image_path, index, old_selection.get(item_id, True)))
                emit_media_item(track_id, items[-1])
                fraction = (target_index + 0.82 + 0.18 * index / max(1, len(source_items))) / total_tracks
                emit_event(fraction * 100, f"正在生成 {track['label']} 缩略图", "media.finalize", track_id, index, len(source_items), _eta_seconds(started, fraction))
        elif track_type in {"standard_photos", "aerial_photos"}:
            emit_event(base_fraction * 100, f"正在扫描 {track['label']}", "media.scan", track_id)
            all_photos = xpano_tracks.iter_photo_paths([source], excluded_paths=[project_root])
            if not all_photos:
                raise ValueError(f"No photos found for track {track['label']}")
            source_items = sample_evenly(all_photos, frame_limit)
            emit_event(
                base_fraction * 100,
                f"发现 {len(source_items)} 张照片",
                "media.scan",
                track_id,
                0,
                len(source_items),
            )
            old_selection = {item["id"]: item.get("selected", True) for item in track.get("items", [])}
            items = []
            manifest_photos = []
            sensor_label = f"{make_track_id(legacy_index, track['label'])}_frame"
            sensor_groups = {}
            photo_lut_context = (
                prepare_lut_chain(style_lut_path=str(lut_paths.style))
                if lut_paths.style
                else nullcontext(None)
            )
            with photo_lut_context as prepared_luts:
                for index, source_path in enumerate(source_items, 1):
                    item_id = f"photo_{index:05d}"
                    image_path = source_path
                    if prepared_luts:
                        image_path = project_root / "work" / "media" / track_id / f"{item_id}.jpg"
                        apply_style_lut_to_image(source_path, image_path, prepared_luts)
                    manifest_photos.append(image_path)
                    identity = xpano_tracks.read_photo_identity(image_path)
                    _append_photo_sensor(sensor_groups, sensor_label, image_path, identity)
                    items.append(
                        stage_photo_item(
                            project_root,
                            track_id,
                            image_path,
                            index,
                            old_selection.get(item_id, True),
                        )
                    )
                    emit_media_item(track_id, items[-1])
                    fraction = (target_index + index / max(1, len(source_items))) / total_tracks
                    emit_event(fraction * 100, f"正在索引 {track['label']} {index}/{len(source_items)}", "media.thumbnail", track_id, index, len(source_items), _eta_seconds(started, fraction))
            manifest_track_id = make_track_id(legacy_index, track["label"])
            manifest_track = {
                "track_id": manifest_track_id,
                "track_type": track_type,
                "device_label": track["label"],
                "source_paths": [str(Path(source).resolve())],
                "metashape_mode": "pinhole_frame",
                "export_mode": "undistorted_frame",
                "group_label": f"{manifest_track_id}_photos",
                "sensor_label": sensor_label,
                "photo_sensors": list(sensor_groups.values()),
                "photos": [str(path) for path in manifest_photos],
                "photo_count_total": len(all_photos),
                "photo_count_selected": len(source_items),
            }
        else:
            raise ValueError(f"Unsupported track type: {track_type}")

        replacements[track_id] = manifest_track
        result_tracks.append({"id": track_id, "status": "ready", "items": items})
        emit_event((target_index + 1) / total_tracks * 100, f"{track['label']} 已准备", "media.ready", track_id, len(items), len(items), _eta_seconds(started, (target_index + 1) / total_tracks))

    manifest = {
        "schema_version": 1,
        "workflow": "xpano_multi_track",
        "tracks": merge_manifest_tracks(project_tracks, previous_manifest, replacements),
    }
    validate_manifest(manifest)
    write_manifest(manifest, manifest_path)

    result = {
        "schemaVersion": 1,
        "projectId": project["projectId"],
        "inputRevision": int(expected_revision),
        "inputMediaRevision": int((project.get("revisions") or {}).get("media", 0)),
        "tracks": result_tracks,
        "manifestPath": "work/manifests/media_full.json",
    }
    _atomic_json(project_root / RESULT_RELATIVE_PATH, result)
    emit_event(100, "素材准备完成", "media.complete", None, len(result_tracks), len(result_tracks), 0)
    return result


def main():
    configure_console_output()
    parser = argparse.ArgumentParser(description="Prepare xPano v2 media tracks")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--expected-revision", type=int, required=True)
    parser.add_argument("--track-id", action="append", default=[])
    args = parser.parse_args()
    try:
        prepare_project(args.project_root, args.expected_revision, args.track_id)
    except Exception as exc:
        emit_line(f"ERROR:{exc}")
        raise


if __name__ == "__main__":
    main()
