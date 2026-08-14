import tempfile
import unittest
from pathlib import Path

from scripts.colmap_backend import write_colmap_cameras, write_colmap_images, write_colmap_points3d
from scripts.compare_backend_outputs import compare_camera_centers, summarize_output


class CompareBackendOutputsTests(unittest.TestCase):
    def test_summarizes_standard_colmap_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            images = root / "images"
            images.mkdir()
            (images / "cube_front_000001_left.jpg").write_bytes(b"jpg")
            write_colmap_cameras(
                root / "sparse" / "0" / "cameras.bin",
                [{"id": 1, "model_id": 1, "width": 10, "height": 10, "params": (5, 5, 5, 5)}],
            )
            write_colmap_images(
                root / "sparse" / "0" / "images.bin",
                [
                    {
                        "id": 1,
                        "qvec": (1, 0, 0, 0),
                        "tvec": (0, 0, 0),
                        "camera_id": 1,
                        "name": "cube_front_000001_left.jpg",
                        "points2d": [(1.0, 2.0, 7)],
                    }
                ],
            )
            write_colmap_points3d(
                root / "sparse" / "0" / "points3D.bin",
                [{"id": 7, "xyz": (0, 0, 1), "rgb": (255, 255, 255), "error": 0, "track": [(1, 0)]}],
            )

            result = summarize_output(root)

            self.assertEqual(result["cube_images"], 1)
            self.assertEqual(result["camera_models"], {"1": 1})
            self.assertEqual(result["points3D"], 1)
            self.assertEqual(result["image_points2D"], 1)

    def test_compares_camera_centers_by_frame_and_side(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metashape = root / "metashape"
            colmap = root / "colmap"
            for output in [metashape, colmap]:
                (output / "images").mkdir(parents=True)
                write_colmap_cameras(
                    output / "sparse" / "0" / "cameras.bin",
                    [{"id": 1, "model_id": 1, "width": 10, "height": 10, "params": (5, 5, 5, 5)}],
                )
                images = []
                for idx in range(1, 4):
                    images.append(
                        {
                            "id": idx,
                            "qvec": (1, 0, 0, 0),
                            "tvec": (-idx, 0, 0),
                            "camera_id": 1,
                            "name": f"cube_front_00000_CAM_frame_{idx:05d}_left.jpg",
                            "points2d": [],
                        }
                    )
                write_colmap_images(output / "sparse" / "0" / "images.bin", images)
                write_colmap_points3d(output / "sparse" / "0" / "points3D.bin", [])

            result = compare_camera_centers(metashape, colmap)

            self.assertEqual(result["count"], 3)
            self.assertAlmostEqual(result["rmse"], 0.0)


if __name__ == "__main__":
    unittest.main()
