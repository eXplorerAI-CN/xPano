import tempfile
import unittest
from pathlib import Path

from scripts.colmap_backend import (
    PINHOLE_MODEL_ID,
    read_colmap_images,
    read_colmap_points3d,
    write_colmap_cameras,
    write_colmap_images,
    write_colmap_points3d,
)
from scripts.postprocess_colmap_axis import find_sparse_model, flip_sparse_model, qvec_to_rotmat


def camera_coordinates(image, xyz):
    rotation = qvec_to_rotmat(image["qvec"])
    return tuple(
        sum(rotation[row][col] * xyz[col] for col in range(3)) + image["tvec"][row]
        for row in range(3)
    )


class PostprocessColmapAxisTests(unittest.TestCase):
    def test_find_sparse_model_accepts_release_colmap_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            sparse = Path(tmp) / "sparse" / "0"
            sparse.mkdir(parents=True)
            for name in ["cameras.bin", "images.bin", "points3D.bin"]:
                (sparse / name).write_bytes(b"bin")

            self.assertEqual(find_sparse_model(tmp), sparse)

    def test_axis_preset_is_a_projection_preserving_180_degree_rotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            sparse = Path(tmp) / "sparse" / "0"
            sparse.mkdir(parents=True)
            write_colmap_cameras(
                sparse / "cameras.bin",
                [
                    {
                        "id": 1,
                        "model_id": PINHOLE_MODEL_ID,
                        "width": 100,
                        "height": 80,
                        "params": (50.0, 50.0, 50.0, 40.0),
                    }
                ],
            )
            write_colmap_images(
                sparse / "images.bin",
                [
                    {
                        "id": 1,
                        "qvec": (1.0, 0.0, 0.0, 0.0),
                        "tvec": (-1.0, -2.0, -3.0),
                        "camera_id": 1,
                        "name": "frame.jpg",
                        "points2d": [(10.0, 20.0, 1)],
                    }
                ],
            )
            write_colmap_points3d(
                sparse / "points3D.bin",
                [
                    {
                        "id": 1,
                        "xyz": (4.0, 5.0, 6.0),
                        "rgb": (1, 2, 3),
                        "error": 0.5,
                        "track": [(1, 0)],
                    }
                ],
            )

            before_image = read_colmap_images(sparse)[0]
            before_point = read_colmap_points3d(sparse)[0]
            before_camera_xyz = camera_coordinates(before_image, before_point["xyz"])

            result = flip_sparse_model(tmp, "y")

            after_image = read_colmap_images(sparse)[0]
            after_point = read_colmap_points3d(sparse)[0]
            after_camera_xyz = camera_coordinates(after_image, after_point["xyz"])

            self.assertEqual(result["axis"], "y")
            self.assertEqual(result["images"], 1)
            self.assertEqual(result["points"], 1)
            self.assertTrue((sparse / "images_before_axis_flip.bin").exists())
            self.assertTrue((sparse / "points3D_before_axis_flip.bin").exists())
            self.assertEqual(after_point["xyz"], (-4.0, 5.0, -6.0))
            self.assertEqual(after_camera_xyz, before_camera_xyz)
            self.assertEqual(
                qvec_to_rotmat(after_image["qvec"]),
                [[-1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, -1.0]],
            )


if __name__ == "__main__":
    unittest.main()
