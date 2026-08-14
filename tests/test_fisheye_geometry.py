import unittest

from scripts.fisheye_geometry import (
    REFERENCE_FISHEYE_FOCAL_MM,
    effective_fisheye_pixel_size_mm,
    normalized_fisheye_focal_px,
)


class FisheyeGeometryTests(unittest.TestCase):
    def test_8k_reference_preserves_existing_pixel_focal_length(self):
        self.assertAlmostEqual(normalized_fisheye_focal_px(3840, 3840), 1041.6666666666667)
        self.assertAlmostEqual(effective_fisheye_pixel_size_mm(3840, 3840), 0.0024)

    def test_4k_frames_keep_the_same_angular_calibration(self):
        focal_8k = normalized_fisheye_focal_px(3840, 3840)
        focal_4k = normalized_fisheye_focal_px(1920, 1920)

        self.assertAlmostEqual(focal_4k, 520.8333333333334)
        self.assertAlmostEqual(focal_8k / 3840, focal_4k / 1920)
        self.assertAlmostEqual(
            effective_fisheye_pixel_size_mm(1920, 1920),
            REFERENCE_FISHEYE_FOCAL_MM / focal_4k,
        )

    def test_non_square_frames_scale_from_the_active_short_side(self):
        self.assertAlmostEqual(normalized_fisheye_focal_px(2560, 1920), 520.8333333333334)

    def test_invalid_dimensions_are_rejected(self):
        for width, height in ((0, 1920), (1920, 0), (-1, 1920)):
            with self.subTest(width=width, height=height):
                with self.assertRaisesRegex(ValueError, "positive"):
                    normalized_fisheye_focal_px(width, height)


if __name__ == "__main__":
    unittest.main()
