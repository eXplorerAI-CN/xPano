import tempfile
import unittest
from pathlib import Path

import piexif
from PIL import Image

from scripts.xpano_tracks import build_photo_track
from scripts.xpano_tracks import validate_manifest


def write_jpeg(path, size, make, model, lens, focal_num):
    path = Path(path)
    image = Image.new("RGB", size, (32, 64, 96))
    image.save(path, "JPEG")
    exif = {
        "0th": {
            piexif.ImageIFD.Make: make.encode("utf-8"),
            piexif.ImageIFD.Model: model.encode("utf-8"),
        },
        "Exif": {
            piexif.ExifIFD.LensModel: lens.encode("utf-8"),
            piexif.ExifIFD.FocalLength: (focal_num, 10),
        },
        "GPS": {},
        "1st": {},
        "thumbnail": None,
    }
    piexif.insert(piexif.dump(exif), str(path))
    return path


class PhotoTrackTests(unittest.TestCase):
    def test_splits_same_size_photos_by_exif_camera_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            phone = write_jpeg(root / "phone.jpg", (100, 80), "PhoneCo", "Pocket 1", "Wide", 240)
            drone = write_jpeg(root / "drone.jpg", (100, 80), "DroneCo", "Air 3", "Main", 240)

            track = build_photo_track(1, "mixed", [phone, drone], "standard_photos")

            self.assertEqual(len(track["photos"]), 2)
            self.assertEqual(len(track["photo_sensors"]), 2)
            grouped = [sensor["photos"] for sensor in track["photo_sensors"]]
            self.assertEqual(sorted(len(paths) for paths in grouped), [1, 1])
            labels = {sensor["sensor_label"] for sensor in track["photo_sensors"]}
            self.assertEqual(len(labels), 2)

    def test_groups_matching_exif_photos_into_one_sensor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = write_jpeg(root / "first.jpg", (100, 80), "PhoneCo", "Pocket 1", "Wide", 240)
            second = write_jpeg(root / "second.jpg", (100, 80), "PhoneCo", "Pocket 1", "Wide", 240)

            track = build_photo_track(1, "phone", [first, second], "standard_photos")

            self.assertEqual(len(track["photos"]), 2)
            self.assertEqual(len(track["photo_sensors"]), 1)
            self.assertEqual(len(track["photo_sensors"][0]["photos"]), 2)

    def test_validates_photo_sensor_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = write_jpeg(root / "first.jpg", (100, 80), "PhoneCo", "Pocket 1", "Wide", 240)
            second = write_jpeg(root / "second.jpg", (100, 80), "PhoneCo", "Pocket 1", "Wide", 240)
            track = build_photo_track(1, "phone", [first, second], "standard_photos")
            track["photo_sensors"][0]["photos"] = [str(first)]
            manifest = {"schema_version": 1, "workflow": "xpano_multi_track", "tracks": [track]}

            with self.assertRaisesRegex(ValueError, "photo_sensors must cover exactly"):
                validate_manifest(manifest)

    def test_rejects_duplicate_track_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = write_jpeg(root / "first.jpg", (100, 80), "PhoneCo", "Pocket 1", "Wide", 240)
            track = build_photo_track(1, "phone", [first], "standard_photos")
            manifest = {"schema_version": 1, "workflow": "xpano_multi_track", "tracks": [track, dict(track)]}

            with self.assertRaisesRegex(ValueError, "Duplicate track_id"):
                validate_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
