import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.colmap_backend import read_colmap_images, read_colmap_points3d, write_colmap_images, write_colmap_points3d


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Flip one world axis in a COLMAP sparse model.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--flip-axis", required=True, choices=["x", "y", "z"])
    return parser.parse_args(argv)


def find_sparse_model(output_dir):
    root = Path(output_dir)
    candidates = [
        root,
        root / "sparse" / "0",
        root / "sparse",
        root / "0",
    ]
    for candidate in candidates:
        if all((candidate / name).exists() for name in ["cameras.bin", "images.bin", "points3D.bin"]):
            return candidate
    raise FileNotFoundError(f"No COLMAP sparse model found under {root}")


def backup_once(path):
    backup = path.with_name(path.stem + "_before_axis_flip" + path.suffix)
    if not backup.exists():
        shutil.copy2(path, backup)
    return backup


def rotate_180_vector(values, axis_index):
    return tuple(value if index == axis_index else -value for index, value in enumerate(values))


def quaternion_multiply(left, right):
    lw, lx, ly, lz = left
    rw, rx, ry, rz = right
    result = (
        lw * rw - lx * rx - ly * ry - lz * rz,
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
    )
    norm = sum(value * value for value in result) ** 0.5
    if norm <= 1e-12:
        raise ValueError("rotation produced an invalid quaternion")
    return tuple(value / norm for value in result)


def qvec_to_rotmat(qvec):
    qw, qx, qy, qz = qvec
    norm = (qw * qw + qx * qx + qy * qy + qz * qz) ** 0.5
    if norm <= 1e-12:
        qw, qx, qy, qz = 1.0, 0.0, 0.0, 0.0
    else:
        qw, qx, qy, qz = qw / norm, qx / norm, qy / norm, qz / norm
    return [
        [1 - 2 * qy * qy - 2 * qz * qz, 2 * qx * qy - 2 * qw * qz, 2 * qx * qz + 2 * qw * qy],
        [2 * qx * qy + 2 * qw * qz, 1 - 2 * qx * qx - 2 * qz * qz, 2 * qy * qz - 2 * qw * qx],
        [2 * qx * qz - 2 * qw * qy, 2 * qy * qz + 2 * qw * qx, 1 - 2 * qx * qx - 2 * qy * qy],
    ]


def camera_center(qvec, tvec):
    rotation = qvec_to_rotmat(qvec)
    return tuple(
        -sum(rotation[row][col] * tvec[row] for row in range(3))
        for col in range(3)
    )


def tvec_from_center(qvec, center):
    rotation = qvec_to_rotmat(qvec)
    return tuple(
        -sum(rotation[row][col] * center[col] for col in range(3))
        for row in range(3)
    )


def flip_sparse_model(output_dir, axis):
    axis_index = {"x": 0, "y": 1, "z": 2}[axis]
    axis_quaternion = {
        "x": (0.0, 1.0, 0.0, 0.0),
        "y": (0.0, 0.0, 1.0, 0.0),
        "z": (0.0, 0.0, 0.0, 1.0),
    }[axis]
    sparse_dir = find_sparse_model(output_dir)
    images_path = sparse_dir / "images.bin"
    points_path = sparse_dir / "points3D.bin"
    backup_once(images_path)
    backup_once(points_path)

    images = read_colmap_images(sparse_dir)
    for image in images:
        rotated_qvec = quaternion_multiply(image["qvec"], axis_quaternion)
        image["tvec"] = tvec_from_center(
            rotated_qvec,
            rotate_180_vector(camera_center(image["qvec"], image["tvec"]), axis_index),
        )
        image["qvec"] = rotated_qvec

    points = read_colmap_points3d(sparse_dir)
    for point in points:
        point["xyz"] = rotate_180_vector(point["xyz"], axis_index)

    write_colmap_images(images_path, images)
    write_colmap_points3d(points_path, points)
    return {
        "sparse_dir": str(sparse_dir),
        "axis": axis,
        "images": len(images),
        "points": len(points),
    }


def main(argv=None):
    args = parse_args(argv)
    print(json.dumps(flip_sparse_model(args.output_dir, args.flip_axis), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
