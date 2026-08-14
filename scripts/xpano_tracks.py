import json
import math
import os
import re
import shutil
import warnings
from pathlib import Path

import piexif
from PIL import Image

from scripts.xpano_extract import _expected_frame_count, extract_frames, extract_single_video_frames


PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".tif", ".tiff", ".png", ".bmp"}
PANO_EXTENSIONS = {".osv", ".insv"}
ORDINARY_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv"}
TRACK_TYPES = {"panorama_video", "ordinary_video", "standard_photos", "aerial_photos"}
CAMERA_PROFILES = {"standard", "wide"}
DEFAULT_ORDINARY_CAMERA_PROFILE = "wide"


def validate_frames_per_second(value):
    fps = float(value)
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError("frames_per_second must be a finite number greater than 0")
    return fps


def normalize_camera_profile(value, default=DEFAULT_ORDINARY_CAMERA_PROFILE):
    profile = str(value or default).strip().lower().replace("-", "_")
    aliases = {
        "normal": "standard",
        "default": "standard",
        "std": "standard",
        "wide_angle": "wide",
        "wideangle": "wide",
    }
    profile = aliases.get(profile, profile)
    if profile not in CAMERA_PROFILES:
        supported = ", ".join(sorted(CAMERA_PROFILES))
        raise ValueError(f"Unsupported camera profile: {value}. Supported: {supported}")
    return profile


def sample_evenly(items, limit):
    items = list(items)
    limit = int(limit or 0)
    if limit <= 0 or limit >= len(items):
        return items
    if limit == 1:
        return [items[0]]
    last = len(items) - 1
    return [items[round(index * last / (limit - 1))] for index in range(limit)]


def safe_id(text):
    value = re.sub(r"[^A-Za-z0-9_]+", "_", text.strip())
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "track"


def make_track_id(index, label):
    return f"track_{index:03d}_{safe_id(label).lower()}"


def _is_path_within(path, parent):
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False


def iter_photo_paths(paths, excluded_paths=None):
    excluded = [Path(path).resolve() for path in (excluded_paths or [])]
    result = []
    for item in paths:
        path = Path(item).resolve()
        if any(_is_path_within(path, root) for root in excluded):
            continue
        if path.is_dir():
            for current, directories, filenames in os.walk(path):
                current_path = Path(current)
                directories[:] = [
                    name
                    for name in directories
                    if not any(_is_path_within(current_path / name, root) for root in excluded)
                ]
                result.extend(
                    current_path / name
                    for name in filenames
                    if (current_path / name).suffix.lower() in PHOTO_EXTENSIONS
                )
        elif path.suffix.lower() in PHOTO_EXTENSIONS:
            result.append(path)
    return sorted(dict.fromkeys(p.resolve() for p in result))


def _decode_exif_text(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore").strip("\x00 ")
    return str(value).strip()


def _decode_exif_rational(value):
    if value is None:
        return ""
    if isinstance(value, tuple) and len(value) == 2:
        num, den = value
        return f"{num}/{den}" if den else str(num)
    return str(value)


def read_photo_identity(path):
    path = Path(path)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=r"Corrupt EXIF data\..*", category=UserWarning)
        with Image.open(path) as image:
            width, height = image.size

    identity = {
        "width": width,
        "height": height,
        "make": "",
        "model": "",
        "lens_make": "",
        "lens_model": "",
        "focal_length": "",
        "focal_length_35mm": "",
    }
    try:
        exif = piexif.load(str(path))
    except Exception:
        return identity

    zeroth = exif.get("0th", {})
    exif_ifd = exif.get("Exif", {})
    identity.update(
        {
            "make": _decode_exif_text(zeroth.get(piexif.ImageIFD.Make)),
            "model": _decode_exif_text(zeroth.get(piexif.ImageIFD.Model)),
            "lens_make": _decode_exif_text(exif_ifd.get(piexif.ExifIFD.LensMake)),
            "lens_model": _decode_exif_text(exif_ifd.get(piexif.ExifIFD.LensModel)),
            "focal_length": _decode_exif_rational(exif_ifd.get(piexif.ExifIFD.FocalLength)),
            "focal_length_35mm": _decode_exif_rational(exif_ifd.get(piexif.ExifIFD.FocalLengthIn35mmFilm)),
        }
    )
    return identity


def photo_sensor_key(identity):
    return (
        identity["width"],
        identity["height"],
        identity["make"].casefold(),
        identity["model"].casefold(),
        identity["lens_make"].casefold(),
        identity["lens_model"].casefold(),
        identity["focal_length"],
        identity["focal_length_35mm"],
    )


def build_photo_sensor_groups(base_label, photos):
    groups = {}
    for photo in photos:
        identity = read_photo_identity(photo)
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
    return list(groups.values())


def build_panorama_track(
    index,
    video_path,
    work_dir,
    frames_per_second,
    max_frames,
    start_time_seconds=0.0,
    end_time_seconds=0.0,
    preview_cb=None,
    progress_cb=None,
    log_cb=None,
    restoration_lut_path=None,
    style_lut_path=None,
):
    frames_per_second = validate_frames_per_second(frames_per_second)
    video = Path(video_path).resolve()
    if video.suffix.lower() not in PANO_EXTENSIONS:
        raise ValueError(f"Unsupported panorama video: {video}")
    if not video.exists():
        raise FileNotFoundError(video)

    label = video.stem
    track_id = make_track_id(index, label)
    track_root = Path(work_dir) / "frames" / track_id
    extracted = extract_frames(
        input_path=video,
        out_root=track_root,
        fps=frames_per_second,
        max_frames=max_frames,
        start_time_seconds=start_time_seconds,
        end_time_seconds=end_time_seconds,
        preview_cb=preview_cb,
        progress_cb=progress_cb,
        log_cb=log_cb,
        model_prefix=track_id,
        restoration_lut_path=restoration_lut_path,
        style_lut_path=style_lut_path,
    )
    frames = []
    for frame_idx, (left_path, right_path) in enumerate(extracted, 1):
        frames.append(
            {
                "frame_id": f"{track_id}_frame_{frame_idx:05d}",
                "group_label": f"{track_id}_frame_{frame_idx:05d}",
                "left": str(Path(left_path).resolve()),
                "right": str(Path(right_path).resolve()),
            }
        )

    return {
        "track_id": track_id,
        "track_type": "panorama_video",
        "device_label": label,
        "source_paths": [str(video)],
        "frames_per_second": frames_per_second,
        "max_frames": max_frames,
        "start_time_seconds": start_time_seconds,
        "end_time_seconds": end_time_seconds,
        "metashape_mode": "dual_fisheye_station",
        "export_mode": "cubemap",
        "left_sensor_label": f"{track_id}_left",
        "right_sensor_label": f"{track_id}_right",
        "frames": frames,
    }


def build_ordinary_video_track(
    index,
    video_path,
    work_dir,
    frames_per_second,
    max_frames,
    start_time_seconds=0.0,
    end_time_seconds=0.0,
    preview_cb=None,
    progress_cb=None,
    log_cb=None,
    camera_profile=DEFAULT_ORDINARY_CAMERA_PROFILE,
    style_lut_path=None,
):
    frames_per_second = validate_frames_per_second(frames_per_second)
    video = Path(video_path).resolve()
    if video.suffix.lower() not in ORDINARY_VIDEO_EXTENSIONS:
        raise ValueError(f"Unsupported ordinary video: {video}")
    if not video.exists():
        raise FileNotFoundError(video)

    label = video.stem
    track_id = make_track_id(index, label)
    track_root = Path(work_dir) / "frames" / track_id
    extracted = extract_single_video_frames(
        input_path=video,
        out_root=track_root,
        fps=frames_per_second,
        max_frames=max_frames,
        start_time_seconds=start_time_seconds,
        end_time_seconds=end_time_seconds,
        preview_cb=preview_cb,
        progress_cb=progress_cb,
        log_cb=log_cb,
        model_prefix=track_id,
        style_lut_path=style_lut_path,
    )
    photos = [str(Path(path).resolve()) for path in extracted]
    sensor_label = f"{track_id}_frame"
    camera_profile = normalize_camera_profile(camera_profile)
    return {
        "track_id": track_id,
        "track_type": "ordinary_video",
        "device_label": label,
        "source_paths": [str(video)],
        "frames_per_second": frames_per_second,
        "max_frames": max_frames,
        "start_time_seconds": start_time_seconds,
        "end_time_seconds": end_time_seconds,
        "camera_profile": camera_profile,
        "metashape_mode": "pinhole_video_frames",
        "export_mode": "undistorted_frame",
        "group_label": f"{track_id}_frames",
        "sensor_label": sensor_label,
        "photo_sensors": [
            {
                "sensor_id": sensor_label,
                "sensor_label": sensor_label,
                "camera_profile": camera_profile,
                "camera_identity": {},
                "photos": photos,
            }
        ],
        "photos": photos,
    }


def build_photo_track(index, label, paths, track_type, max_photos=0):
    if track_type not in {"standard_photos", "aerial_photos"}:
        raise ValueError(f"Unsupported photo track type: {track_type}")
    track_id = make_track_id(index, label)
    all_photos = iter_photo_paths(paths)
    if not all_photos:
        raise ValueError(f"No photos found for track {label}")
    photos = sample_evenly(all_photos, max_photos)
    sensor_label = f"{track_id}_frame"
    photo_sensors = build_photo_sensor_groups(sensor_label, photos)
    return {
        "track_id": track_id,
        "track_type": track_type,
        "device_label": label,
        "source_paths": [str(Path(p).resolve()) for p in paths],
        "metashape_mode": "pinhole_frame",
        "export_mode": "undistorted_frame",
        "group_label": f"{track_id}_photos",
        "sensor_label": sensor_label,
        "photo_sensors": photo_sensors,
        "photos": [str(p) for p in photos],
        "photo_count_total": len(all_photos),
        "photo_count_selected": len(photos),
    }


def _photo_track_parts(track):
    if len(track) == 2:
        label, paths = track
        return label, paths, 0
    if len(track) == 3:
        return track
    raise ValueError("Photo track entries must be (label, paths) or (label, paths, max_photos)")


def write_manifest(manifest, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_manifest(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _norm_paths(paths):
    return sorted(str(Path(path).resolve()) for path in paths)


def validate_manifest(manifest, check_files=True):
    _require(isinstance(manifest, dict), "Manifest must be a JSON object")
    _require(manifest.get("schema_version") == 1, "Manifest schema_version must be 1")
    _require(manifest.get("workflow") == "xpano_multi_track", "Manifest workflow must be xpano_multi_track")
    tracks = manifest.get("tracks")
    _require(isinstance(tracks, list) and tracks, "Manifest must contain at least one track")

    seen_track_ids = set()
    for index, track in enumerate(tracks, 1):
        _require(isinstance(track, dict), f"Track {index} must be an object")
        track_id = track.get("track_id")
        track_type = track.get("track_type")
        _require(track_id, f"Track {index} is missing track_id")
        _require(track_id not in seen_track_ids, f"Duplicate track_id: {track_id}")
        seen_track_ids.add(track_id)
        _require(track_type in TRACK_TYPES, f"Unsupported track_type for {track_id}: {track_type}")

        if track_type == "panorama_video":
            frames = track.get("frames")
            _require(isinstance(frames, list) and frames, f"Panorama track {track_id} must contain frames")
            _require(track.get("metashape_mode") == "dual_fisheye_station", f"Panorama track {track_id} has wrong metashape_mode")
            _require(track.get("export_mode") == "cubemap", f"Panorama track {track_id} has wrong export_mode")
            for frame_index, frame in enumerate(frames, 1):
                left = frame.get("left")
                right = frame.get("right")
                _require(left and right, f"Panorama track {track_id} frame {frame_index} must contain left and right")
                if check_files:
                    _require(Path(left).exists(), f"Missing left image for {track_id} frame {frame_index}: {left}")
                    _require(Path(right).exists(), f"Missing right image for {track_id} frame {frame_index}: {right}")
        elif track_type == "ordinary_video":
            photos = track.get("photos")
            photo_sensors = track.get("photo_sensors")
            _require(isinstance(photos, list) and photos, f"Ordinary video track {track_id} must contain extracted frames")
            _require(isinstance(photo_sensors, list) and photo_sensors, f"Ordinary video track {track_id} must contain photo_sensors")
            _require(track.get("metashape_mode") == "pinhole_video_frames", f"Ordinary video track {track_id} has wrong metashape_mode")
            _require(track.get("export_mode") == "undistorted_frame", f"Ordinary video track {track_id} has wrong export_mode")
            normalize_camera_profile(track.get("camera_profile"), default=DEFAULT_ORDINARY_CAMERA_PROFILE)
            if check_files:
                for photo in photos:
                    _require(Path(photo).exists(), f"Missing frame for {track_id}: {photo}")
            covered = []
            for sensor in photo_sensors:
                sensor_photos = sensor.get("photos")
                _require(isinstance(sensor_photos, list) and sensor_photos, f"Ordinary video track {track_id} sensor must contain photos")
                normalize_camera_profile(sensor.get("camera_profile") or track.get("camera_profile"), default=DEFAULT_ORDINARY_CAMERA_PROFILE)
                covered.extend(sensor_photos)
            _require(
                _norm_paths(covered) == _norm_paths(photos),
                f"Ordinary video track {track_id} photo_sensors must cover exactly the track photos",
            )
        else:
            photos = track.get("photos")
            photo_sensors = track.get("photo_sensors")
            _require(isinstance(photos, list) and photos, f"Photo track {track_id} must contain photos")
            _require(isinstance(photo_sensors, list) and photo_sensors, f"Photo track {track_id} must contain photo_sensors")
            _require(track.get("metashape_mode") == "pinhole_frame", f"Photo track {track_id} has wrong metashape_mode")
            _require(track.get("export_mode") == "undistorted_frame", f"Photo track {track_id} has wrong export_mode")
            if check_files:
                for photo in photos:
                    _require(Path(photo).exists(), f"Missing photo for {track_id}: {photo}")
            covered = []
            seen_sensor_labels = set()
            for sensor_index, sensor in enumerate(photo_sensors, 1):
                label = sensor.get("sensor_label")
                sensor_photos = sensor.get("photos")
                _require(label, f"Photo track {track_id} sensor {sensor_index} is missing sensor_label")
                _require(label not in seen_sensor_labels, f"Duplicate sensor_label in {track_id}: {label}")
                seen_sensor_labels.add(label)
                _require(isinstance(sensor_photos, list) and sensor_photos, f"Photo track {track_id} sensor {label} must contain photos")
                covered.extend(sensor_photos)
            _require(
                _norm_paths(covered) == _norm_paths(photos),
                f"Photo track {track_id} photo_sensors must cover exactly the track photos",
            )
    return manifest


def _track_extraction_for(path, track_extraction_settings, frames_per_second, max_frames):
    settings = track_extraction_settings or {}
    key = str(Path(path).resolve())
    value = settings.get(key) or settings.get(str(path)) or {}
    return (
        validate_frames_per_second(value.get("frames_per_second", frames_per_second)),
        int(value.get("max_frames", max_frames)),
        float(value.get("start_time_seconds", 0.0)),
        float(value.get("end_time_seconds", 0.0)),
    )


def _track_camera_profile_for(path, track_camera_profiles, default=DEFAULT_ORDINARY_CAMERA_PROFILE):
    profiles = track_camera_profiles or {}
    key = str(Path(path).resolve())
    return normalize_camera_profile(profiles.get(key) or profiles.get(str(path)) or default, default=default)


def _estimate_video_frames(path, frames_per_second, max_frames, start_time_seconds, end_time_seconds, log_cb=None):
    if max_frames and max_frames > 0:
        return int(max_frames)
    try:
        fps = validate_frames_per_second(frames_per_second)
    except Exception:
        fps = 1.0
    expected = _expected_frame_count(
        Path(path),
        fps,
        max_frames,
        log_cb=log_cb,
        start_time_seconds=start_time_seconds,
        end_time_seconds=end_time_seconds,
    )
    if expected:
        return int(expected)
    if log_cb:
        log_cb(f"extract frame count unknown for {Path(path).name}; using estimated progress")
    return 1


class ExtractionProgressAggregator:
    def __init__(self, plans, progress_cb, log_cb=None):
        self.progress_cb = progress_cb
        self.log_cb = log_cb
        self.totals = {
            plan["key"]: max(1, int(plan.get("expected_frames") or 1))
            for plan in plans
        }
        self.done = {key: 0 for key in self.totals}
        self.total = max(1, sum(self.totals.values()))
        self.last_current = 0
        self.last_logged = -1
        self.log_step = max(1, self.total // 20)

    def callback_for(self, key):
        def callback(current, total):
            self.update(key, current, total)

        return callback

    def update(self, key, current, total):
        if not self.progress_cb or key not in self.totals:
            return
        old_total = self.totals[key]
        reported_total = max(1, int(total or old_total))
        if reported_total > old_total:
            self.totals[key] = reported_total
            self.total += reported_total - old_total
        track_total = self.totals[key]
        track_current = max(0, min(int(current or 0), track_total))
        if reported_total and int(current or 0) >= reported_total:
            track_current = track_total
        self.done[key] = max(self.done[key], track_current)
        global_current = min(sum(self.done.values()), self.total)
        global_current = max(self.last_current, global_current)
        self.last_current = global_current
        self.progress_cb(global_current, self.total)
        if self.log_cb and (global_current >= self.total or global_current - self.last_logged >= self.log_step):
            self.last_logged = global_current
            self.log_cb(f"extract progress {global_current}/{self.total}")


def _without_track_extract_progress(log_cb):
    if not log_cb:
        return None

    def filtered(text):
        if str(text).lower().startswith("extract progress "):
            return
        log_cb(text)

    return filtered


def build_manifest(output_dir, panorama_videos=None, ordinary_videos=None, standard_photo_tracks=None, aerial_photo_tracks=None,
                   frames_per_second=1.0, max_frames=0, track_extraction_settings=None, track_camera_profiles=None,
                   preview_cb=None, progress_cb=None, log_cb=None):
    output_dir = Path(output_dir)
    work_dir = output_dir / "work"
    tracks = []
    index = 1
    video_plans = []

    for video in panorama_videos or []:
        track_fps, track_max_frames, track_start, track_end = _track_extraction_for(
            video,
            track_extraction_settings,
            frames_per_second,
            max_frames,
        )
        key = str(Path(video).resolve())
        video_plans.append(
            {
                "kind": "panorama",
                "key": key,
                "index": index,
                "video": video,
                "frames_per_second": track_fps,
                "max_frames": track_max_frames,
                "start_time_seconds": track_start,
                "end_time_seconds": track_end,
                "expected_frames": _estimate_video_frames(video, track_fps, track_max_frames, track_start, track_end, log_cb=log_cb),
            }
        )
        index += 1

    for video in ordinary_videos or []:
        track_fps, track_max_frames, track_start, track_end = _track_extraction_for(
            video,
            track_extraction_settings,
            frames_per_second,
            max_frames,
        )
        key = str(Path(video).resolve())
        video_plans.append(
            {
                "kind": "ordinary",
                "key": key,
                "index": index,
                "video": video,
                "frames_per_second": track_fps,
                "max_frames": track_max_frames,
                "start_time_seconds": track_start,
                "end_time_seconds": track_end,
                "expected_frames": _estimate_video_frames(video, track_fps, track_max_frames, track_start, track_end, log_cb=log_cb),
            }
        )
        index += 1

    progress_aggregator = ExtractionProgressAggregator(video_plans, progress_cb, log_cb=log_cb) if video_plans and progress_cb else None
    track_log_cb = _without_track_extract_progress(log_cb) if progress_aggregator else log_cb

    for plan in video_plans:
        if plan["kind"] == "panorama":
            tracks.append(
                build_panorama_track(
                    index=plan["index"],
                    video_path=plan["video"],
                    work_dir=work_dir,
                    frames_per_second=plan["frames_per_second"],
                    max_frames=plan["max_frames"],
                    start_time_seconds=plan["start_time_seconds"],
                    end_time_seconds=plan["end_time_seconds"],
                    preview_cb=preview_cb,
                    progress_cb=progress_aggregator.callback_for(plan["key"]) if progress_aggregator else progress_cb,
                    log_cb=track_log_cb,
                )
            )
            continue
        tracks.append(
            build_ordinary_video_track(
                index=plan["index"],
                video_path=plan["video"],
                work_dir=work_dir,
                frames_per_second=plan["frames_per_second"],
                max_frames=plan["max_frames"],
                camera_profile=_track_camera_profile_for(plan["video"], track_camera_profiles),
                start_time_seconds=plan["start_time_seconds"],
                end_time_seconds=plan["end_time_seconds"],
                preview_cb=preview_cb,
                progress_cb=progress_aggregator.callback_for(plan["key"]) if progress_aggregator else progress_cb,
                log_cb=track_log_cb,
            )
        )

    for item in standard_photo_tracks or []:
        label, paths, max_photos = _photo_track_parts(item)
        tracks.append(build_photo_track(index, label, paths, "standard_photos", max_photos=max_photos))
        index += 1

    for item in aerial_photo_tracks or []:
        label, paths, max_photos = _photo_track_parts(item)
        tracks.append(build_photo_track(index, label, paths, "aerial_photos", max_photos=max_photos))
        index += 1

    if not tracks:
        raise ValueError("No material tracks were provided")

    manifest = {
        "schema_version": 1,
        "workflow": "xpano_multi_track",
        "tracks": tracks,
    }
    validate_manifest(manifest)
    manifest_path = write_manifest(manifest, work_dir / "xpano_manifest.json")
    shutil.copy2(manifest_path, output_dir / "xpano_manifest.json")
    return manifest, manifest_path
