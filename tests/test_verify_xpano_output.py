import struct
import tempfile
import unittest
from pathlib import Path

from scripts.verify_xpano_output import verify_output


def write_count_header(path, count):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<Q", count))


class VerifyXpanoOutputTests(unittest.TestCase):
    def test_accepts_expected_single_sparse_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            images = root / "images"
            images.mkdir()
            (images / "cube_front_00001_left.jpg").write_bytes(b"x")
            (images / "frame_00002_phone.jpg").write_bytes(b"x")
            write_count_header(root / "sparse" / "0" / "cameras.bin", 2)
            write_count_header(root / "sparse" / "0" / "images.bin", 2)
            write_count_header(root / "sparse" / "0" / "points3D.bin", 0)

            result = verify_output(
                root,
                expect_cube_images=1,
                expect_frame_images=1,
                expect_colmap_images=2,
                expect_colmap_cameras=2,
                expect_single_sparse=True,
            )

            self.assertEqual(result["cube_images"], 1)
            self.assertEqual(result["frame_images"], 1)
            self.assertEqual(result["colmap_images"], 2)

    def test_rejects_missing_frame_images_when_expected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "images").mkdir()
            write_count_header(root / "sparse" / "0" / "cameras.bin", 1)
            write_count_header(root / "sparse" / "0" / "images.bin", 0)
            write_count_header(root / "sparse" / "0" / "points3D.bin", 0)

            with self.assertRaisesRegex(RuntimeError, "frame_images"):
                verify_output(root, expect_frame_images=1)

    def test_accepts_native_colmap_backend_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image_dir = root / "images"
            image_dir.mkdir()
            (image_dir / "000001_left.jpg").write_bytes(b"left")
            (image_dir / "000001_right.jpg").write_bytes(b"right")
            write_count_header(root / "sparse" / "0" / "cameras.bin", 2)
            write_count_header(root / "sparse" / "0" / "images.bin", 2)
            write_count_header(root / "sparse" / "0" / "points3D.bin", 10)

            result = verify_output(
                root,
                backend="colmap",
                expect_colmap_images=2,
                expect_colmap_cameras=2,
                expect_colmap_points=10,
                expect_single_sparse=True,
            )

            self.assertEqual(result["backend"], "colmap")
            self.assertEqual(result["colmap_input_images"], 2)
            self.assertEqual(result["colmap_points"], 10)

    def test_native_colmap_backend_uses_standard_sparse_zero_and_nested_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image_dir = root / "images"
            for side in ["left", "right"]:
                for index in range(1, 3):
                    path = image_dir / side / f"{index:06d}.jpg"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"jpg")
            write_count_header(root / "sparse" / "0" / "cameras.bin", 1)
            write_count_header(root / "sparse" / "0" / "images.bin", 2)
            write_count_header(root / "sparse" / "0" / "points3D.bin", 171)
            write_count_header(root / "sparse" / "1" / "cameras.bin", 2)
            write_count_header(root / "sparse" / "1" / "images.bin", 4)
            write_count_header(root / "sparse" / "1" / "points3D.bin", 1786)

            result = verify_output(
                root,
                backend="colmap",
                expect_colmap_images=2,
                expect_colmap_cameras=1,
                expect_colmap_points=171,
            )

            self.assertEqual(result["colmap_input_images"], 4)
            self.assertEqual(Path(result["sparse_model_path"]), root / "sparse" / "0")

    def test_rejects_native_colmap_missing_images_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_count_header(root / "sparse" / "0" / "cameras.bin", 1)
            write_count_header(root / "sparse" / "0" / "images.bin", 1)
            write_count_header(root / "sparse" / "0" / "points3D.bin", 0)

            with self.assertRaisesRegex(RuntimeError, "image directory"):
                verify_output(root, backend="colmap")


if __name__ == "__main__":
    unittest.main()
