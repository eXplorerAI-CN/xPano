import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.colmap_backend import read_colmap_cameras, read_colmap_images, read_colmap_points3d


def face_counts(images_dir):
    counts = {}
    for path in Path(images_dir).glob("cube_*.jpg"):
        parts = path.name.split("_")
        if len(parts) >= 2:
            counts[parts[1]] = counts.get(parts[1], 0) + 1
    return dict(sorted(counts.items()))


def summarize_output(output_dir):
    output_dir = Path(output_dir)
    images_dir = output_dir / "images"
    sparse_dir = output_dir / "sparse" / "0"
    cameras = read_colmap_cameras(sparse_dir)
    images = read_colmap_images(sparse_dir)
    points = read_colmap_points3d(sparse_dir)
    camera_models = {}
    camera_sizes = {}
    for camera in cameras.values():
        camera_models[str(camera["model_id"])] = camera_models.get(str(camera["model_id"]), 0) + 1
        size = f"{camera['width']}x{camera['height']}"
        camera_sizes[size] = camera_sizes.get(size, 0) + 1
    return {
        "output_dir": str(output_dir),
        "image_files": len([path for path in images_dir.rglob("*") if path.is_file()]),
        "cube_images": len(list(images_dir.glob("cube_*.jpg"))),
        "face_counts": face_counts(images_dir),
        "cameras": len(cameras),
        "camera_models": dict(sorted(camera_models.items())),
        "camera_sizes": dict(sorted(camera_sizes.items())),
        "registered_images": len(images),
        "points3D": len(points),
        "point_tracks": sum(len(point["track"]) for point in points),
        "image_points2D": sum(len(image["points2d"]) for image in images),
    }


def qvec_to_rotmat(qvec):
    qw, qx, qy, qz = qvec
    return [
        [1 - 2 * qy * qy - 2 * qz * qz, 2 * qx * qy - 2 * qw * qz, 2 * qx * qz + 2 * qw * qy],
        [2 * qx * qy + 2 * qw * qz, 1 - 2 * qx * qx - 2 * qz * qz, 2 * qy * qz - 2 * qw * qx],
        [2 * qx * qz - 2 * qw * qy, 2 * qy * qz + 2 * qw * qx, 1 - 2 * qx * qx - 2 * qy * qy],
    ]


def camera_center(image):
    rot = qvec_to_rotmat(image["qvec"])
    t = image["tvec"]
    return tuple(-sum(rot[row][col] * t[row] for row in range(3)) for col in range(3))


def image_key(name):
    side = None
    if "_left" in name or "left_" in name:
        side = "left"
    elif "_right" in name or "right_" in name:
        side = "right"
    match = re.search(r"frame_(\d+)", name)
    if not match:
        match = re.search(r"(?:^|_)(\d{6})(?:\.|_)", name)
    if not match or side is None:
        return None
    return f"{int(match.group(1)):06d}_{side}"


def centers_by_key(output_dir):
    images = read_colmap_images(Path(output_dir) / "sparse" / "0")
    grouped = {}
    for image in images:
        key = image_key(image["name"])
        if key is None:
            continue
        grouped.setdefault(key, []).append(camera_center(image))
    centers = {}
    for key, values in grouped.items():
        centers[key] = tuple(sum(value[idx] for value in values) / len(values) for idx in range(3))
    return centers


def umeyama_similarity(source, target):
    import numpy as np

    src = np.asarray(source, dtype=np.float64)
    dst = np.asarray(target, dtype=np.float64)
    src_mean = src.mean(axis=0)
    dst_mean = dst.mean(axis=0)
    src_centered = src - src_mean
    dst_centered = dst - dst_mean
    covariance = dst_centered.T @ src_centered / len(src)
    u, singular_values, vt = np.linalg.svd(covariance)
    correction = np.eye(3)
    if np.linalg.det(u @ vt) < 0:
        correction[-1, -1] = -1
    rotation = u @ correction @ vt
    variance = (src_centered * src_centered).sum() / len(src)
    scale = float(np.trace(np.diag(singular_values) @ correction) / variance) if variance else 1.0
    translation = dst_mean - scale * rotation @ src_mean
    aligned = (scale * (rotation @ src.T)).T + translation
    errors = np.linalg.norm(aligned - dst, axis=1)
    return {
        "count": int(len(errors)),
        "scale": scale,
        "rmse": float(np.sqrt((errors * errors).mean())),
        "median": float(np.median(errors)),
        "max": float(errors.max()),
    }


def compare_camera_centers(metashape_dir, colmap_dir):
    metashape_centers = centers_by_key(metashape_dir)
    colmap_centers = centers_by_key(colmap_dir)
    keys = sorted(set(metashape_centers) & set(colmap_centers))
    if len(keys) < 3:
        return {"count": len(keys), "error": "not enough shared camera keys"}
    return umeyama_similarity([colmap_centers[key] for key in keys], [metashape_centers[key] for key in keys])


def main():
    parser = argparse.ArgumentParser(description="Compare Metashape and COLMAP backend output summaries.")
    parser.add_argument("--metashape", required=True)
    parser.add_argument("--colmap", required=True)
    args = parser.parse_args()

    metashape = summarize_output(args.metashape)
    colmap = summarize_output(args.colmap)
    comparison = {
        "metashape": metashape,
        "colmap": colmap,
        "deltas": {
            key: colmap[key] - metashape[key]
            for key in ["image_files", "cube_images", "cameras", "registered_images", "points3D", "point_tracks", "image_points2D"]
        },
        "camera_center_alignment": compare_camera_centers(args.metashape, args.colmap),
    }
    print(json.dumps(comparison, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
