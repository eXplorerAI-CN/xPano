import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import piexif
from PIL import Image

from scripts.xpano_tracks import (
    _estimate_video_frames,
    build_manifest,
    build_ordinary_video_track,
    build_panorama_track,
    build_photo_track,
)
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
    def test_two_fps_over_five_seconds_estimates_ten_frames(self):
        with patch("scripts.xpano_tracks._expected_frame_count", return_value=10) as expected:
            count = _estimate_video_frames(Path("clip.mp4"), 2.0, 0, 0.0, 5.0)

        self.assertEqual(count, 10)
        self.assertEqual(expected.call_args.args[1], 2.0)
        self.assertEqual(expected.call_args.kwargs["end_time_seconds"], 5.0)

    def test_rejects_non_finite_frames_per_second(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "clip.mp4"
            video.write_bytes(b"video")
            with self.assertRaisesRegex(ValueError, "finite number"):
                build_ordinary_video_track(1, video, Path(tmp) / "work", float("inf"), 0)

    def test_ordinary_video_uses_frames_per_second_without_reciprocal_conversion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "clip.mp4"
            lut = root / "restore.cube"
            video.write_bytes(b"video")
            lut.write_text("LUT_3D_SIZE 2\n", encoding="utf-8")
            frame = root / "frame.jpg"
            Image.new("RGB", (100, 80), (32, 64, 96)).save(frame, "JPEG")

            with patch("scripts.xpano_tracks.extract_single_video_frames", return_value=[frame]) as extract:
                track = build_ordinary_video_track(
                    1,
                    video,
                    root / "work",
                    frames_per_second=2.0,
                    max_frames=0,
                    style_lut_path=lut,
                )

            self.assertEqual(extract.call_args.kwargs["fps"], 2.0)
            self.assertEqual(extract.call_args.kwargs["style_lut_path"], lut)
            self.assertEqual(track["frames_per_second"], 2.0)
            self.assertNotIn("seconds_per_frame", track)

    def test_rejects_mp4_as_panorama_track(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "clip.mp4"
            video.write_bytes(b"video")

            with self.assertRaisesRegex(ValueError, "Unsupported panorama video"):
                build_panorama_track(
                    1,
                    video,
                    root / "work",
                    frames_per_second=1.0,
                    max_frames=1,
                )

    def test_builds_ordinary_video_track_as_frame_photo_track(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "clip.mp4"
            video.write_bytes(b"video")
            frame = root / "frame.jpg"
            Image.new("RGB", (100, 80), (32, 64, 96)).save(frame, "JPEG")

            with patch("scripts.xpano_tracks.extract_single_video_frames", return_value=[frame]) as extract:
                track = build_ordinary_video_track(
                    1,
                    video,
                    root / "work",
                    frames_per_second=2.0,
                    max_frames=5,
                )

            extract.assert_called_once()
            self.assertEqual(track["track_type"], "ordinary_video")
            self.assertEqual(track["frames_per_second"], 2.0)
            self.assertEqual(track["max_frames"], 5)
            self.assertEqual(track["camera_profile"], "wide")
            self.assertEqual(track["photo_sensors"][0]["camera_profile"], "wide")
            self.assertEqual(track["metashape_mode"], "pinhole_video_frames")
            self.assertEqual(track["photos"], [str(frame.resolve())])
            validate_manifest({"schema_version": 1, "workflow": "xpano_multi_track", "tracks": [track]})

    def test_builds_ordinary_video_track_with_standard_camera_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "clip.mp4"
            video.write_bytes(b"video")
            frame = root / "frame.jpg"
            Image.new("RGB", (100, 80), (32, 64, 96)).save(frame, "JPEG")

            with patch("scripts.xpano_tracks.extract_single_video_frames", return_value=[frame]):
                track = build_ordinary_video_track(
                    1,
                    video,
                    root / "work",
                    frames_per_second=2.0,
                    max_frames=5,
                    camera_profile="standard",
                )

            self.assertEqual(track["camera_profile"], "standard")
            self.assertEqual(track["photo_sensors"][0]["camera_profile"], "standard")
            validate_manifest({"schema_version": 1, "workflow": "xpano_multi_track", "tracks": [track]})

    def test_manifest_applies_video_track_specific_extraction_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pano = root / "camera.osv"
            ordinary = root / "clip.mp4"
            pano.write_bytes(b"pano")
            ordinary.write_bytes(b"video")
            calls = []

            def fake_pano(**kwargs):
                calls.append(
                    (
                        "pano",
                        kwargs["frames_per_second"],
                        kwargs["max_frames"],
                        kwargs["start_time_seconds"],
                        kwargs["end_time_seconds"],
                    )
                )
                return {
                    "track_id": "pano",
                    "track_type": "panorama_video",
                    "metashape_mode": "dual_fisheye_station",
                    "export_mode": "cubemap",
                    "frames": [{"left": str(pano), "right": str(pano)}],
                }

            def fake_ordinary(**kwargs):
                calls.append(
                    (
                        "ordinary",
                        kwargs["frames_per_second"],
                        kwargs["max_frames"],
                        kwargs["start_time_seconds"],
                        kwargs["end_time_seconds"],
                        kwargs["camera_profile"],
                    )
                )
                return {
                    "track_id": "ordinary",
                    "track_type": "ordinary_video",
                    "metashape_mode": "pinhole_video_frames",
                    "export_mode": "undistorted_frame",
                    "photos": [str(ordinary)],
                    "photo_sensors": [{"sensor_label": "ordinary_frame", "photos": [str(ordinary)]}],
                }

            settings = {
                str(pano.resolve()): {
                    "frames_per_second": 1.0,
                    "max_frames": 10,
                    "start_time_seconds": 3.0,
                    "end_time_seconds": 8.0,
                },
                str(ordinary.resolve()): {
                    "frames_per_second": 2.0,
                    "max_frames": 20,
                    "start_time_seconds": 4.0,
                    "end_time_seconds": 12.0,
                },
            }
            profiles = {str(ordinary.resolve()): "standard"}
            with patch("scripts.xpano_tracks.build_panorama_track", side_effect=fake_pano), \
                patch("scripts.xpano_tracks.build_ordinary_video_track", side_effect=fake_ordinary):
                build_manifest(
                    root / "out",
                    panorama_videos=[pano],
                    ordinary_videos=[ordinary],
                    frames_per_second=9.0,
                    max_frames=99,
                    track_extraction_settings=settings,
                    track_camera_profiles=profiles,
                )

            self.assertEqual(calls, [("pano", 1.0, 10, 3.0, 8.0), ("ordinary", 2.0, 20, 4.0, 12.0, "standard")])

    def test_manifest_aggregates_video_extraction_progress_across_tracks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pano = root / "camera.osv"
            ordinary = root / "clip.mp4"
            pano.write_bytes(b"pano")
            ordinary.write_bytes(b"video")
            progress = []
            logs = []

            def fake_pano(**kwargs):
                cb = kwargs["progress_cb"]
                cb(0, 50)
                cb(25, 50)
                cb(50, 50)
                return {
                    "track_id": "pano",
                    "track_type": "panorama_video",
                    "metashape_mode": "dual_fisheye_station",
                    "export_mode": "cubemap",
                    "frames": [{"left": str(pano), "right": str(pano)}],
                }

            def fake_ordinary(**kwargs):
                cb = kwargs["progress_cb"]
                cb(0, 150)
                cb(75, 150)
                cb(150, 150)
                return {
                    "track_id": "ordinary",
                    "track_type": "ordinary_video",
                    "metashape_mode": "pinhole_video_frames",
                    "export_mode": "undistorted_frame",
                    "photos": [str(ordinary)],
                    "photo_sensors": [{"sensor_label": "ordinary_frame", "photos": [str(ordinary)]}],
                }

            settings = {
                str(pano.resolve()): {"frames_per_second": 1.0, "max_frames": 50},
                str(ordinary.resolve()): {"frames_per_second": 1.0, "max_frames": 150},
            }
            with patch("scripts.xpano_tracks.build_panorama_track", side_effect=fake_pano), \
                patch("scripts.xpano_tracks.build_ordinary_video_track", side_effect=fake_ordinary):
                build_manifest(
                    root / "out",
                    panorama_videos=[pano],
                    ordinary_videos=[ordinary],
                    track_extraction_settings=settings,
                    progress_cb=lambda cur, total: progress.append((cur, total)),
                    log_cb=logs.append,
                )

            self.assertEqual(progress[0], (0, 200))
            self.assertIn((50, 200), progress)
            self.assertIn((125, 200), progress)
            self.assertEqual(progress[-1], (200, 200))
            self.assertEqual(progress, sorted(progress))
            self.assertIn("extract progress 200/200", logs)

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

    def test_photo_track_uses_even_sampling_when_max_photos_is_below_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            photos = [
                write_jpeg(root / f"image_{index:02d}.jpg", (100, 80), "PhoneCo", "Pocket 1", "Wide", 240)
                for index in range(10)
            ]

            track = build_photo_track(1, "phone", photos, "standard_photos", max_photos=4)

            self.assertEqual(
                [Path(path).name for path in track["photos"]],
                ["image_00.jpg", "image_03.jpg", "image_06.jpg", "image_09.jpg"],
            )
            self.assertEqual(track["photo_count_total"], 10)
            self.assertEqual(track["photo_count_selected"], 4)

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
