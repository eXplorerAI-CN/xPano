import argparse
import json
import struct
from pathlib import Path


def read_colmap_count(path):
    path = Path(path)
    if not path.exists():
        raise RuntimeError(f"Missing COLMAP file: {path}")
    data = path.read_bytes()[:8]
    if len(data) != 8:
        raise RuntimeError(f"COLMAP file is too small: {path}")
    return struct.unpack("<Q", data)[0]


def assert_expected(name, actual, expected, failures):
    if expected is not None and actual != expected:
        failures.append(f"{name}: expected {expected}, got {actual}")


def verify_output(
    output_dir,
    expect_cube_images=None,
    expect_frame_images=None,
    expect_colmap_images=None,
    expect_colmap_cameras=None,
    expect_colmap_points=None,
    expect_single_sparse=False,
):
    output_dir = Path(output_dir)
    images_dir = output_dir / "images"
    sparse_dir = output_dir / "sparse"
    sparse_zero = sparse_dir / "0"

    if not images_dir.exists():
        raise RuntimeError(f"Missing images directory: {images_dir}")
    if not sparse_zero.exists():
        raise RuntimeError(f"Missing sparse/0 directory: {sparse_zero}")

    image_files = [path for path in images_dir.iterdir() if path.is_file()]
    cube_images = [path for path in image_files if path.name.startswith("cube_")]
    frame_images = [path for path in image_files if path.name.startswith("frame_")]
    sparse_models = [path for path in sparse_dir.iterdir() if path.is_dir()] if sparse_dir.exists() else []

    result = {
        "output_dir": str(output_dir),
        "image_files": len(image_files),
        "cube_images": len(cube_images),
        "frame_images": len(frame_images),
        "sparse_models": [path.name for path in sparse_models],
        "colmap_cameras": read_colmap_count(sparse_zero / "cameras.bin"),
        "colmap_images": read_colmap_count(sparse_zero / "images.bin"),
        "colmap_points": read_colmap_count(sparse_zero / "points3D.bin"),
    }

    failures = []
    assert_expected("cube_images", result["cube_images"], expect_cube_images, failures)
    assert_expected("frame_images", result["frame_images"], expect_frame_images, failures)
    assert_expected("colmap_images", result["colmap_images"], expect_colmap_images, failures)
    assert_expected("colmap_cameras", result["colmap_cameras"], expect_colmap_cameras, failures)
    assert_expected("colmap_points", result["colmap_points"], expect_colmap_points, failures)
    if expect_single_sparse and result["sparse_models"] != ["0"]:
        failures.append(f"sparse_models: expected ['0'], got {result['sparse_models']}")
    if failures:
        raise RuntimeError("; ".join(failures))
    return result


def parse_args():
    parser = argparse.ArgumentParser(description="Verify xPano COLMAP export structure")
    parser.add_argument("--output", required=True)
    parser.add_argument("--expect-cube-images", type=int)
    parser.add_argument("--expect-frame-images", type=int)
    parser.add_argument("--expect-colmap-images", type=int)
    parser.add_argument("--expect-colmap-cameras", type=int)
    parser.add_argument("--expect-colmap-points", type=int)
    parser.add_argument("--expect-single-sparse", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    result = verify_output(
        args.output,
        expect_cube_images=args.expect_cube_images,
        expect_frame_images=args.expect_frame_images,
        expect_colmap_images=args.expect_colmap_images,
        expect_colmap_cameras=args.expect_colmap_cameras,
        expect_colmap_points=args.expect_colmap_points,
        expect_single_sparse=args.expect_single_sparse,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
