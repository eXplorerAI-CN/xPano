import json
import math
import os
import shutil
import subprocess
import struct
from dataclasses import dataclass, field
from pathlib import Path

from scripts.dependency_checks import resolve_executable


@dataclass(frozen=True)
class ColmapBackendConfig:
    colmap_exe: str = "colmap"
    camera_model: str = "OPENCV_FISHEYE"
    camera_params: str = "1041.6666666667,1041.6666666667,1920,1920,0,0,0,0"
    image_extension: str = ".jpg"
    max_image_size: int = 1600
    max_num_features: int = 4096
    num_threads: int = 4
    single_camera: bool = False
    single_camera_per_folder: bool = True
    use_gpu: bool = False
    matcher: str = "sequential"
    sequential_overlap: int = 6
    guided_matching: bool = False
    sift_peak_threshold: float = None
    mapper_tri_ignore_two_view_tracks: bool = None
    mapper_filter_max_reproj_error: float = None
    mapper_tri_min_angle: float = None
    mapper_snapshot_frames_freq: int = 0


COLMAP_DENSITY_PRESETS = ("stable", "high-density", "experimental-high-density")


def colmap_config_for_density_preset(preset, colmap_exe="colmap"):
    preset = (preset or "stable").strip().lower()
    if preset == "stable":
        return ColmapBackendConfig(colmap_exe=colmap_exe)
    if preset == "high-density":
        return ColmapBackendConfig(
            colmap_exe=colmap_exe,
            max_num_features=8192,
            sequential_overlap=10,
            guided_matching=True,
        )
    if preset == "experimental-high-density":
        return ColmapBackendConfig(
            colmap_exe=colmap_exe,
            max_image_size=2000,
            max_num_features=12000,
            sequential_overlap=12,
            guided_matching=True,
            sift_peak_threshold=0.004,
            mapper_filter_max_reproj_error=6.0,
            mapper_tri_min_angle=1.0,
            mapper_tri_ignore_two_view_tracks=False,
        )
    raise ValueError(f"Unsupported COLMAP density preset: {preset}")


@dataclass(frozen=True)
class ColmapCommandPlan:
    output_dir: Path
    database_path: Path
    image_dir: Path
    sparse_dir: Path
    commands: list = field(default_factory=list)
    image_manifest_path: Path = None
    manifest_path: Path = None


def _collect_panorama_frames(manifest):
    frames = []
    for track in manifest.get("tracks", []):
        if track.get("track_type") != "panorama_video":
            continue
        frames.extend(track.get("frames", []))
    return frames


def build_colmap_plan(manifest, output_dir, config=None):
    config = config or ColmapBackendConfig()
    config = ColmapBackendConfig(
        **{
            **config.__dict__,
            "colmap_exe": resolve_executable(config.colmap_exe, "colmap"),
        }
    )
    output_dir = Path(output_dir)
    image_dir = output_dir / "colmap_images"
    sparse_dir = output_dir / "sparse"
    database_path = output_dir / "database.db"

    frames = _collect_panorama_frames(manifest)
    if not frames:
        raise ValueError("COLMAP plan requires panorama frames in the manifest")

    frame_sources = []
    for index, frame in enumerate(frames, 1):
        left = Path(frame["left"])
        right = Path(frame["right"])
        if not left.exists() or not right.exists():
            raise FileNotFoundError(f"Missing panorama frame images for COLMAP plan: {frame}")
        frame_sources.append((index, frame, left, right))

    if image_dir.exists():
        shutil.rmtree(image_dir)
    if sparse_dir.exists():
        shutil.rmtree(sparse_dir)
    if database_path.exists():
        database_path.unlink()
    image_dir.mkdir(parents=True, exist_ok=True)
    sparse_dir.mkdir(parents=True, exist_ok=True)

    image_entries = []
    for index, frame, left, right in frame_sources:
        for side, source in [("left", left), ("right", right)]:
            target_name = f"{side}/{index:06d}{config.image_extension}"
            target = image_dir / target_name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            image_entries.append(
                {
                    "frame_id": frame.get("frame_id", f"frame_{index:06d}"),
                    "side": side,
                    "source": str(source),
                    "image": target_name,
                }
            )

    commands = [
        [
            config.colmap_exe,
            "feature_extractor",
            "--database_path",
            str(database_path),
            "--image_path",
            str(image_dir),
            "--ImageReader.camera_model",
            config.camera_model,
            "--ImageReader.camera_params",
            config.camera_params,
            "--ImageReader.single_camera",
            "1" if config.single_camera else "0",
            "--ImageReader.single_camera_per_folder",
            "1" if config.single_camera_per_folder else "0",
            "--FeatureExtraction.max_image_size",
            str(config.max_image_size),
            "--FeatureExtraction.num_threads",
            str(config.num_threads),
            "--FeatureExtraction.use_gpu",
            "1" if config.use_gpu else "0",
            "--SiftExtraction.max_num_features",
            str(config.max_num_features),
        ],
    ]
    if config.sift_peak_threshold is not None:
        commands[0].extend(["--SiftExtraction.peak_threshold", str(config.sift_peak_threshold)])
    if config.matcher == "sequential":
        commands.append(
            [
                config.colmap_exe,
                "sequential_matcher",
                "--database_path",
                str(database_path),
                "--FeatureMatching.num_threads",
                str(config.num_threads),
                "--FeatureMatching.use_gpu",
                "1" if config.use_gpu else "0",
                "--FeatureMatching.guided_matching",
                "1" if config.guided_matching else "0",
                "--SequentialMatching.overlap",
                str(config.sequential_overlap),
                "--SequentialMatching.expand_rig_images",
                "1",
            ]
        )
    elif config.matcher == "exhaustive":
        commands.append(
            [
                config.colmap_exe,
                "exhaustive_matcher",
                "--database_path",
                str(database_path),
                "--FeatureMatching.num_threads",
                str(config.num_threads),
                "--FeatureMatching.use_gpu",
                "1" if config.use_gpu else "0",
                "--FeatureMatching.guided_matching",
                "1" if config.guided_matching else "0",
            ]
        )
    else:
        raise ValueError(f"Unsupported COLMAP matcher: {config.matcher}")
    commands.append(
        [
            config.colmap_exe,
            "mapper",
            "--database_path",
            str(database_path),
            "--image_path",
            str(image_dir),
            "--output_path",
            str(sparse_dir),
        ]
    )
    mapper_command = commands[-1]
    if config.mapper_snapshot_frames_freq and config.mapper_snapshot_frames_freq > 0:
        mapper_command.extend([
            "--Mapper.snapshot_path",
            str(output_dir / "snapshots"),
            "--Mapper.snapshot_frames_freq",
            str(config.mapper_snapshot_frames_freq),
        ])
    if config.mapper_filter_max_reproj_error is not None:
        mapper_command.extend(["--Mapper.filter_max_reproj_error", str(config.mapper_filter_max_reproj_error)])
    if config.mapper_tri_min_angle is not None:
        mapper_command.extend(["--Mapper.tri_min_angle", str(config.mapper_tri_min_angle)])
    if config.mapper_tri_ignore_two_view_tracks is not None:
        mapper_command.extend([
            "--Mapper.tri_ignore_two_view_tracks",
            "1" if config.mapper_tri_ignore_two_view_tracks else "0",
        ])

    manifest_path = output_dir / "xpano_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    image_manifest_path = output_dir / "colmap_images.json"
    image_manifest_path.write_text(json.dumps(image_entries, ensure_ascii=False, indent=2), encoding="utf-8")

    return ColmapCommandPlan(
        output_dir=output_dir,
        database_path=database_path,
        image_dir=image_dir,
        sparse_dir=sparse_dir,
        commands=commands,
        image_manifest_path=image_manifest_path,
        manifest_path=manifest_path,
    )


def _command_name(command):
    if len(command) >= 2:
        return command[1]
    if command:
        return Path(command[0]).name
    return "COLMAP"


def _command_stage_label(name):
    return {
        "feature_extractor": "COLMAP_STAGE: feature_extraction",
        "sequential_matcher": "COLMAP_STAGE: feature_matching",
        "exhaustive_matcher": "COLMAP_STAGE: feature_matching",
        "mapper": "COLMAP_STAGE: mapping",
    }.get(name, f"COLMAP_STAGE: {name}")


def _popen_creationflags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _colmap_process_env(command):
    env = os.environ.copy()
    if not command:
        return env
    exe = Path(str(command[0]))
    if not exe.exists():
        return env
    bin_dir = exe.parent
    root_dir = bin_dir.parent if bin_dir.name.lower() == "bin" else exe.parent
    plugin_dir = root_dir / "plugins"
    platform_dir = plugin_dir / "platforms"
    path_parts = [str(bin_dir), str(root_dir)]
    existing_path = env.get("PATH") or env.get("Path") or ""
    if existing_path:
        path_parts.append(existing_path)
    env["PATH"] = os.pathsep.join(path_parts)
    if plugin_dir.exists():
        env["QT_PLUGIN_PATH"] = str(plugin_dir)
    if platform_dir.exists():
        env["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(platform_dir)
    return env


def _run_command_streaming(command, cwd, log_cb):
    proc = subprocess.Popen(
        command,
        cwd=str(cwd),
        env=_colmap_process_env(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_popen_creationflags(),
    )
    output_lines = []
    for raw_line in proc.stdout:
        line = raw_line.rstrip()
        if line:
            output_lines.append(line)
            log_cb(line)
    rc = proc.wait()
    return subprocess.CompletedProcess(command, rc, stdout="\n".join(output_lines), stderr="")


def _looks_like_cuda_unavailable(output):
    lowered = (output or "").lower()
    return (
        "without cuda" in lowered
        or "cuda is not available" in lowered
        or "not compiled with cuda" in lowered
        or "siftgpu" in lowered and "cuda" in lowered
    )


def _command_with_gpu_disabled(command):
    command = [str(part) for part in command]
    gpu_flags = {"--FeatureExtraction.use_gpu", "--FeatureMatching.use_gpu"}
    changed = False
    for index, part in enumerate(command[:-1]):
        if part in gpu_flags and command[index + 1] != "0":
            command[index + 1] = "0"
            changed = True
    return command if changed else None


def _colmap_help_text(command):
    if not command:
        return ""
    exe = str(command[0])
    try:
        result = subprocess.run(
            [exe, "-h"],
            env=_colmap_process_env([exe]),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=_popen_creationflags(),
        )
    except Exception:
        return ""
    return result.stdout or ""


def _disable_gpu_for_no_cuda_colmap(commands, log_cb):
    if not commands or not any(_command_with_gpu_disabled(command) for command in commands):
        return commands
    if not _looks_like_cuda_unavailable(_colmap_help_text(commands[0])):
        return commands

    updated = []
    changed = False
    for command in commands:
        cpu_command = _command_with_gpu_disabled(command)
        if cpu_command:
            updated.append(cpu_command)
            changed = True
        else:
            updated.append(command)
    if changed:
        log_cb("COLMAP build reports no CUDA support; running COLMAP GPU stages on CPU.")
    return updated


def _has_sparse_model(sparse_dir):
    sparse_dir = Path(sparse_dir)
    return bool(_sparse_model_candidates(sparse_dir))


def _sparse_model_candidates(sparse_dir):
    sparse_dir = Path(sparse_dir)
    required = ["cameras.bin", "images.bin", "points3D.bin"]
    candidates = []
    for candidate in [sparse_dir, *sorted((path for path in sparse_dir.iterdir()), key=lambda path: path.name)] if sparse_dir.exists() else []:
        if candidate.is_dir() and all((candidate / name).exists() for name in required):
            candidates.append(candidate)
    return candidates


def _read_colmap_count(path):
    data = Path(path).read_bytes()[:8]
    if len(data) != 8:
        return 0
    return struct.unpack("<Q", data)[0]


def _sparse_model_score(path):
    cameras = _read_colmap_count(path / "cameras.bin")
    images = _read_colmap_count(path / "images.bin")
    points = _read_colmap_count(path / "points3D.bin")
    return (images, points, cameras)


def find_sparse_model_path(sparse_dir):
    sparse_dir = Path(sparse_dir)
    candidates = _sparse_model_candidates(sparse_dir)
    if candidates:
        return max(candidates, key=_sparse_model_score)
    raise RuntimeError(f"COLMAP sparse model output is missing: {sparse_dir}")


CAMERA_MODEL_PARAM_COUNTS = {
    0: 3,   # SIMPLE_PINHOLE
    1: 4,   # PINHOLE
    2: 4,   # SIMPLE_RADIAL
    3: 5,   # RADIAL
    4: 8,   # OPENCV
    5: 8,   # OPENCV_FISHEYE
    6: 12,  # FULL_OPENCV
    7: 5,   # FOV
    8: 4,   # SIMPLE_RADIAL_FISHEYE
    9: 5,   # RADIAL_FISHEYE
    10: 12, # THIN_PRISM_FISHEYE
}

PINHOLE_MODEL_ID = 1
OPENCV_FISHEYE_MODEL_ID = 5

FACE_ROTATIONS = {
    "front": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    "left": ((0, 0, 1), (0, 1, 0), (-1, 0, 0)),
    "right": ((0, 0, -1), (0, 1, 0), (1, 0, 0)),
    "top": ((1, 0, 0), (0, 0, 1), (0, -1, 0)),
    "bottom": ((1, 0, 0), (0, 0, -1), (0, 1, 0)),
}


def _read_cstring(data, offset):
    end = data.index(b"\0", offset)
    return data[offset:end].decode("utf-8"), end + 1


def read_colmap_cameras(model_dir):
    path = Path(model_dir) / "cameras.bin"
    data = path.read_bytes()
    offset = 0
    count = struct.unpack_from("<Q", data, offset)[0]
    offset += 8
    cameras = {}
    for _ in range(count):
        camera_id, model_id = struct.unpack_from("<Ii", data, offset)
        offset += 8
        width, height = struct.unpack_from("<QQ", data, offset)
        offset += 16
        param_count = CAMERA_MODEL_PARAM_COUNTS.get(model_id)
        if param_count is None:
            raise RuntimeError(f"Unsupported COLMAP camera model id: {model_id}")
        params = struct.unpack_from("<" + "d" * param_count, data, offset)
        offset += 8 * param_count
        cameras[camera_id] = {
            "id": camera_id,
            "model_id": model_id,
            "width": width,
            "height": height,
            "params": params,
        }
    return cameras


def read_colmap_images(model_dir):
    path = Path(model_dir) / "images.bin"
    data = path.read_bytes()
    offset = 0
    count = struct.unpack_from("<Q", data, offset)[0]
    offset += 8
    images = []
    for _ in range(count):
        image_id = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        qvec = struct.unpack_from("<dddd", data, offset)
        offset += 32
        tvec = struct.unpack_from("<ddd", data, offset)
        offset += 24
        camera_id = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        name, offset = _read_cstring(data, offset)
        point_count = struct.unpack_from("<Q", data, offset)[0]
        offset += 8
        points2d = []
        for _point in range(point_count):
            x, y, point3d_id = struct.unpack_from("<ddq", data, offset)
            offset += 24
            points2d.append((x, y, point3d_id))
        images.append(
            {
                "id": image_id,
                "qvec": qvec,
                "tvec": tvec,
                "camera_id": camera_id,
                "name": name,
                "points2d": points2d,
            }
        )
    return images


def read_colmap_points3d_file(path):
    path = Path(path)
    data = path.read_bytes()
    offset = 0
    count = struct.unpack_from("<Q", data, offset)[0]
    offset += 8
    points = []
    for _ in range(count):
        point_id = struct.unpack_from("<Q", data, offset)[0]
        offset += 8
        xyz = struct.unpack_from("<ddd", data, offset)
        offset += 24
        rgb = struct.unpack_from("<BBB", data, offset)
        offset += 3
        error = struct.unpack_from("<d", data, offset)[0]
        offset += 8
        track_len = struct.unpack_from("<Q", data, offset)[0]
        offset += 8
        track = []
        for _track in range(track_len):
            image_id, point2d_idx = struct.unpack_from("<II", data, offset)
            offset += 8
            track.append((image_id, point2d_idx))
        points.append({"id": point_id, "xyz": xyz, "rgb": rgb, "error": error, "track": track})
    return points


def read_colmap_points3d(model_dir):
    return read_colmap_points3d_file(Path(model_dir) / "points3D.bin")


def _write_cstring(handle, text):
    handle.write(text.encode("utf-8") + b"\0")


def write_colmap_cameras(path, cameras):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(struct.pack("<Q", len(cameras)))
        for camera in cameras:
            handle.write(struct.pack("<IiQQ", camera["id"], camera["model_id"], camera["width"], camera["height"]))
            handle.write(struct.pack("<" + "d" * len(camera["params"]), *camera["params"]))


def write_colmap_images(path, images):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(struct.pack("<Q", len(images)))
        for image in images:
            handle.write(struct.pack("<I", image["id"]))
            handle.write(struct.pack("<dddd", *image["qvec"]))
            handle.write(struct.pack("<ddd", *image["tvec"]))
            handle.write(struct.pack("<I", image["camera_id"]))
            _write_cstring(handle, image["name"])
            handle.write(struct.pack("<Q", len(image["points2d"])))
            for x, y, point3d_id in image["points2d"]:
                handle.write(struct.pack("<ddq", x, y, int(point3d_id)))


def write_colmap_points3d(path, points):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(struct.pack("<Q", len(points)))
        for point in points:
            handle.write(struct.pack("<Q", int(point["id"])))
            handle.write(struct.pack("<ddd", *point["xyz"]))
            handle.write(struct.pack("<BBB", *point["rgb"]))
            handle.write(struct.pack("<d", float(point["error"])))
            handle.write(struct.pack("<Q", len(point["track"])))
            for image_id, point2d_idx in point["track"]:
                handle.write(struct.pack("<II", int(image_id), int(point2d_idx)))


def _face_configs(width):
    half = int(width / 2)
    return {
        "front": (width, width, half, half),
        "right": (half, width, half, half),
        "left": (half, width, 0, half),
        "top": (width, half, half, 0),
        "bottom": (width, half, half, half),
    }


def _qvec_to_rotmat(qvec):
    qw, qx, qy, qz = qvec
    return (
        (
            1 - 2 * qy * qy - 2 * qz * qz,
            2 * qx * qy - 2 * qw * qz,
            2 * qx * qz + 2 * qw * qy,
        ),
        (
            2 * qx * qy + 2 * qw * qz,
            1 - 2 * qx * qx - 2 * qz * qz,
            2 * qy * qz - 2 * qw * qx,
        ),
        (
            2 * qx * qz - 2 * qw * qy,
            2 * qy * qz + 2 * qw * qx,
            1 - 2 * qx * qx - 2 * qy * qy,
        ),
    )


def _rotmat_to_qvec(rot):
    m00, m01, m02 = rot[0]
    m10, m11, m12 = rot[1]
    m20, m21, m22 = rot[2]
    trace = m00 + m11 + m22
    if trace > 0:
        s = math.sqrt(trace + 1.0) * 2
        return (0.25 * s, (m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s)
    if m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2
        return ((m21 - m12) / s, 0.25 * s, (m01 + m10) / s, (m02 + m20) / s)
    if m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2
        return ((m02 - m20) / s, (m01 + m10) / s, 0.25 * s, (m12 + m21) / s)
    s = math.sqrt(1.0 + m22 - m00 - m11) * 2
    return ((m10 - m01) / s, (m02 + m20) / s, (m12 + m21) / s, 0.25 * s)


def _matmul3(a, b):
    return tuple(
        tuple(sum(a[row][idx] * b[idx][col] for idx in range(3)) for col in range(3))
        for row in range(3)
    )


def _matvec3(a, v):
    return tuple(sum(a[row][idx] * v[idx] for idx in range(3)) for row in range(3))


def _safe_flat_image_name(name):
    return Path(name.replace("\\", "/")).with_suffix(".jpg").as_posix().replace("/", "_")


def _cubemap_width_for_camera(camera):
    params = camera["params"]
    if camera["model_id"] != OPENCV_FISHEYE_MODEL_ID:
        raise RuntimeError(f"COLMAP cubemap publishing requires OPENCV_FISHEYE cameras, got model {camera['model_id']}")
    width = int(round((params[0] + params[1]) / 2.0 * 2.0))
    if width % 2:
        width += 1
    return max(512, width)


def _fisheye_remap_grid(camera, face, face_width):
    import numpy as np

    fx, fy, cx, cy, k1, k2, k3, k4 = camera["params"]
    fw, fh, vcx, vcy = _face_configs(face_width)[face]
    u, v = np.meshgrid(np.arange(fw, dtype=np.float32), np.arange(fh, dtype=np.float32))
    focal = face_width / 2.0
    x = (u + 0.5 - vcx) / focal
    y = (v + 0.5 - vcy) / focal
    z = np.ones_like(x)
    rot = np.array(FACE_ROTATIONS[face], dtype=np.float32).T
    xb = rot[0, 0] * x + rot[0, 1] * y + rot[0, 2] * z
    yb = rot[1, 0] * x + rot[1, 1] * y + rot[1, 2] * z
    zb = rot[2, 0] * x + rot[2, 1] * y + rot[2, 2] * z
    r = np.sqrt(xb * xb + yb * yb)
    theta = np.arctan2(r, zb)
    theta2 = theta * theta
    theta_distorted = theta * (
        1 + k1 * theta2 + k2 * theta2 * theta2 + k3 * theta2 * theta2 * theta2 + k4 * theta2 * theta2 * theta2 * theta2
    )
    scale = np.divide(theta_distorted, r, out=np.zeros_like(theta_distorted), where=r > 1e-10)
    mx = fx * xb * scale + cx
    my = fy * yb * scale + cy
    return mx.astype(np.float32), my.astype(np.float32)


def _project_pinhole(point_xyz, rot, tvec, face_width, face):
    fw, fh, cx, cy = _face_configs(face_width)[face]
    pc = _matvec3(rot, point_xyz)
    pc = (pc[0] + tvec[0], pc[1] + tvec[1], pc[2] + tvec[2])
    if pc[2] <= 1e-8:
        return None
    focal = face_width / 2.0
    u = focal * (pc[0] / pc[2]) + cx
    v = focal * (pc[1] / pc[2]) + cy
    if 0 <= u < fw and 0 <= v < fh:
        return (float(u), float(v))
    return None


def publish_colmap_output(plan, final_output_dir):
    import cv2

    final_output_dir = Path(final_output_dir)
    final_image_dir = final_output_dir / "images"
    final_sparse_model = final_output_dir / "sparse" / "0"
    source_sparse_model = find_sparse_model_path(plan.sparse_dir)

    if final_image_dir.exists():
        shutil.rmtree(final_image_dir)
    if final_sparse_model.parent.exists():
        shutil.rmtree(final_sparse_model.parent)

    final_image_dir.mkdir(parents=True, exist_ok=True)
    final_sparse_model.mkdir(parents=True, exist_ok=True)

    cameras = read_colmap_cameras(source_sparse_model)
    images = read_colmap_images(source_sparse_model)
    points3d = read_colmap_points3d(source_sparse_model)

    output_cameras = []
    output_images = []
    output_points_by_id = {}
    image_entries = {}
    next_camera_id = 1
    next_image_id = 1

    cubemap_cameras = {}
    for camera_id, camera in sorted(cameras.items()):
        face_width = _cubemap_width_for_camera(camera)
        cubemap_cameras[camera_id] = {}
        for face in ["front", "left", "right", "top", "bottom"]:
            fw, fh, cx, cy = _face_configs(face_width)[face]
            output_cameras.append(
                {
                    "id": next_camera_id,
                    "model_id": PINHOLE_MODEL_ID,
                    "width": fw,
                    "height": fh,
                    "params": (face_width / 2.0, face_width / 2.0, float(cx), float(cy)),
                }
            )
            cubemap_cameras[camera_id][face] = {"id": next_camera_id, "width": face_width}
            next_camera_id += 1

    for source_image in images:
        camera = cameras[source_image["camera_id"]]
        source_path = Path(plan.image_dir) / source_image["name"]
        if not source_path.exists():
            raise RuntimeError(f"COLMAP source image is missing for cubemap export: {source_path}")
        source_pixels = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
        if source_pixels is None:
            raise RuntimeError(f"COLMAP source image cannot be read for cubemap export: {source_path}")
        rot = _qvec_to_rotmat(source_image["qvec"])
        flat_name = _safe_flat_image_name(source_image["name"])
        for face in ["front", "left", "right", "top", "bottom"]:
            face_camera = cubemap_cameras[source_image["camera_id"]][face]
            face_width = face_camera["width"]
            mx, my = _fisheye_remap_grid(camera, face, face_width)
            face_pixels = cv2.remap(source_pixels, mx, my, cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_CONSTANT)
            output_name = f"cube_{face}_{source_image['id']:05d}_{flat_name}"
            cv2.imwrite(str(final_image_dir / output_name), face_pixels, [int(cv2.IMWRITE_JPEG_QUALITY), 100])
            face_rot = FACE_ROTATIONS[face]
            out_rot = _matmul3(face_rot, rot)
            out_tvec = _matvec3(face_rot, source_image["tvec"])
            output_image = {
                "id": next_image_id,
                "qvec": _rotmat_to_qvec(out_rot),
                "tvec": out_tvec,
                "camera_id": face_camera["id"],
                "name": output_name,
                "points2d": [],
                "rot": out_rot,
                "face_width": face_width,
                "face": face,
            }
            output_images.append(output_image)
            image_entries[next_image_id] = output_image
            next_image_id += 1

    for point in points3d:
        refs = []
        for output_image in output_images:
            projected = _project_pinhole(
                point["xyz"],
                output_image["rot"],
                output_image["tvec"],
                output_image["face_width"],
                output_image["face"],
            )
            if projected is None:
                continue
            point2d_idx = len(output_image["points2d"])
            output_image["points2d"].append((projected[0], projected[1], point["id"]))
            refs.append((output_image["id"], point2d_idx))
        if refs:
            output_points_by_id[point["id"]] = {
                "id": point["id"],
                "xyz": point["xyz"],
                "rgb": point["rgb"],
                "error": point["error"],
                "track": refs,
            }

    for output_image in output_images:
        output_image.pop("rot", None)
        output_image.pop("face_width", None)
        output_image.pop("face", None)

    write_colmap_cameras(final_sparse_model / "cameras.bin", output_cameras)
    write_colmap_images(final_sparse_model / "images.bin", output_images)
    write_colmap_points3d(final_sparse_model / "points3D.bin", list(output_points_by_id.values()))

    return {
        "image_dir": str(final_image_dir),
        "sparse_model_path": str(final_sparse_model),
        "native_sparse_model_path": str(source_sparse_model),
    }


def run_colmap_plan(plan, progress_cb=None, log_cb=None, runner=None, stage_cb=None):
    progress_cb = progress_cb or (lambda value: None)
    log_cb = log_cb or (lambda text: None)
    stage_cb = stage_cb or (lambda _name, _index, _total: None)

    total = len(plan.commands)
    if total == 0:
        raise ValueError("COLMAP plan has no commands to run")

    commands = plan.commands
    if runner is None:
        commands = _disable_gpu_for_no_cuda_colmap(commands, log_cb)

    for index, command in enumerate(commands, 1):
        name = _command_name(command)
        stage_cb(name, index, total)
        log_cb(_command_stage_label(name))
        log_cb(f"COLMAP {name}: {' '.join(str(part) for part in command)}")
        if runner is None:
            result = _run_command_streaming(command, plan.output_dir, log_cb)
        else:
            result = runner(
                command,
                cwd=str(plan.output_dir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            for stream in [getattr(result, "stdout", ""), getattr(result, "stderr", "")]:
                for line in (stream or "").splitlines():
                    if line:
                        log_cb(line)
        if getattr(result, "returncode", 0) != 0:
            retry_command = _command_with_gpu_disabled(command)
            if retry_command and _looks_like_cuda_unavailable(getattr(result, "stdout", "") + "\n" + getattr(result, "stderr", "")):
                log_cb(f"COLMAP {name}: CUDA is unavailable in this COLMAP build; retrying with CPU.")
                if runner is None:
                    result = _run_command_streaming(retry_command, plan.output_dir, log_cb)
                else:
                    result = runner(
                        retry_command,
                        cwd=str(plan.output_dir),
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                    )
                    for stream in [getattr(result, "stdout", ""), getattr(result, "stderr", "")]:
                        for line in (stream or "").splitlines():
                            if line:
                                log_cb(line)
                command = retry_command
        if getattr(result, "returncode", 0) != 0:
            raise RuntimeError(f"COLMAP {name} failed with return code {result.returncode}")
        progress_cb(35 + int(55 * index / total))
        log_cb(f"COLMAP_STAGE_DONE: {name}")

    if not Path(plan.database_path).exists():
        raise RuntimeError(f"COLMAP database output is missing: {plan.database_path}")
    sparse_model_path = find_sparse_model_path(plan.sparse_dir)

    return {
        "database_path": str(plan.database_path),
        "image_dir": str(plan.image_dir),
        "sparse_dir": str(plan.sparse_dir),
        "sparse_model_path": str(sparse_model_path),
    }
