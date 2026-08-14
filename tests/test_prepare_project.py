import tempfile
import unittest
import json
import shutil
import warnings
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

import scripts.run_xpano_prepare_project as prepare_module
from scripts.run_xpano_prepare_project import (
    _stage_single_video_item,
    emit_line,
    merge_manifest_tracks,
    prepare_project,
    stage_photo_item,
)
from scripts.xpano_tracks import read_photo_identity


class PrepareProjectTests(unittest.TestCase):
    def test_project_json_with_utf8_bom_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            photos = Path(tmp) / "photos"
            root.mkdir()
            photos.mkdir()
            Image.new("RGB", (80, 60), (30, 60, 90)).save(photos / "a.jpg")
            project = json.loads(
                (Path(__file__).parents[1] / "schemas" / "fixtures" / "xpano_project_v3.example.json").read_text(encoding="utf-8")
            )
            project["tracks"] = [{
                "id": "photo-track",
                "type": "standard_photos",
                "label": "BOM photos",
                "sourcePath": str(photos),
                "sourceFingerprint": {"size": 0, "mtimeNs": 0},
                "cameraProfile": None,
                "trim": None,
                "extraction": {"framesPerSecond": 1.0, "frameLimit": 0},
                "status": "draft",
                "items": [],
            }]
            (root / "xpano_project.json").write_text(json.dumps(project), encoding="utf-8-sig")

            result = prepare_project(root, project["revision"], ["photo-track"])

            self.assertEqual(len(result["tracks"][0]["items"]), 1)

    def test_video_item_timestamp_uses_frames_per_second(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "frame.jpg"
            Image.new("RGB", (100, 80), (32, 64, 96)).save(source, "JPEG")
            track = {
                "id": "video-track",
                "trim": {"start": 3.0, "end": 8.0},
                "extraction": {"framesPerSecond": 2.0, "frameLimit": 0},
            }

            item = _stage_single_video_item(root, track, source, 3)

            self.assertEqual(item["timestamp"], 4.0)

    def test_console_output_is_forced_to_utf8_with_replacement(self):
        class FakeStream:
            def __init__(self):
                self.calls = []

            def reconfigure(self, **kwargs):
                self.calls.append(kwargs)

        stdout = FakeStream()
        stderr = FakeStream()
        with patch.object(prepare_module.sys, "stdout", stdout), patch.object(prepare_module.sys, "stderr", stderr):
            prepare_module.configure_console_output()

        self.assertEqual(stdout.calls, [{"encoding": "utf-8", "errors": "replace"}])
        self.assertEqual(stderr.calls, [{"encoding": "utf-8", "errors": "replace"}])

    def test_stage_photo_item_creates_relative_preview_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.jpg"
            Image.new("RGB", (1200, 800), (60, 120, 180)).save(source)

            item = stage_photo_item(root, "track-1", source, 1, selected=False)

            self.assertEqual(item["id"], "photo_00001")
            self.assertFalse(item["selected"])
            self.assertFalse(Path(item["image"]).is_absolute())
            self.assertFalse(Path(item["thumbnail"]).is_absolute())
            staged = root / item["image"]
            thumb = root / item["thumbnail"]
            self.assertTrue(staged.is_file())
            self.assertTrue(thumb.is_file())
            with Image.open(thumb) as preview:
                self.assertLessEqual(max(preview.size), 320)

    def test_merge_manifest_tracks_replaces_targets_and_keeps_ready_tracks(self):
        previous = {
            "schema_version": 1,
            "workflow": "xpano_multi_track",
            "tracks": [
                {"track_id": "legacy-a", "source_paths": ["A.osv"]},
                {"track_id": "legacy-b", "source_paths": ["B.mp4"]},
            ],
        }
        replacements = {"a": {"track_id": "new-a", "source_paths": ["A.osv"]}}
        project_tracks = [
            {"id": "a", "sourcePath": "A.osv", "status": "draft"},
            {"id": "b", "sourcePath": "B.mp4", "status": "ready"},
        ]

        merged = merge_manifest_tracks(project_tracks, previous, replacements)

        self.assertEqual([track["track_id"] for track in merged], ["new-a", "legacy-b"])

    def test_prepare_project_builds_photo_items_manifest_and_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            photos = Path(tmp) / "photos"
            root.mkdir()
            photos.mkdir()
            Image.new("RGB", (640, 480), (180, 60, 80)).save(photos / "a.jpg")
            Image.new("RGB", (800, 600), (40, 160, 100)).save(photos / "b.jpg")
            project = json.loads(
                (Path(__file__).parents[1] / "schemas" / "fixtures" / "xpano_project_v3.example.json").read_text(encoding="utf-8")
            )
            project["tracks"] = [{
                "id": "photo-track",
                "type": "standard_photos",
                "label": "Reference photos",
                "sourcePath": str(photos),
                "sourceFingerprint": {"size": 0, "mtimeNs": 0},
                "cameraProfile": None,
                "trim": None,
                "extraction": {"framesPerSecond": 1.0, "frameLimit": 0},
                "status": "draft",
                "items": [],
            }]
            (root / "xpano_project.json").write_text(json.dumps(project), encoding="utf-8")

            result = prepare_project(root, project["revision"], ["photo-track"])

            self.assertEqual(len(result["tracks"]), 1)
            self.assertEqual(len(result["tracks"][0]["items"]), 2)
            self.assertEqual(result["inputMediaRevision"], project["revisions"]["media"])
            self.assertTrue((root / "work" / "manifests" / "media_full.json").is_file())
            self.assertTrue((root / "work" / "media_prepare_result.json").is_file())
            for item in result["tracks"][0]["items"]:
                self.assertTrue((root / item["image"]).is_file())
                self.assertTrue((root / item["thumbnail"]).is_file())

    def test_prepare_project_applies_style_lut_to_photos_and_exports_staged_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            photos = Path(tmp) / "photos"
            style = Path(tmp) / "style.cube"
            root.mkdir()
            photos.mkdir()
            Image.new("RGB", (640, 480), (180, 60, 80)).save(photos / "a.jpg")
            Image.new("RGB", (640, 480), (80, 120, 180)).save(photos / "b.jpg")
            style.write_text("LUT_3D_SIZE 2\n0 0 0\n1 1 1\n", encoding="utf-8")
            project = json.loads(
                (Path(__file__).parents[1] / "schemas" / "fixtures" / "xpano_project_v3.example.json").read_text(encoding="utf-8")
            )
            project["tracks"] = [{
                "id": "photo-track",
                "type": "standard_photos",
                "label": "Styled photos",
                "sourcePath": str(photos),
                "sourceFingerprint": {"size": 0, "mtimeNs": 0},
                "cameraProfile": None,
                "trim": None,
                "extraction": {"framesPerSecond": 1.0, "frameLimit": 0, "styleLutPath": str(style)},
                "status": "draft",
                "items": [],
            }]
            (root / "xpano_project.json").write_text(json.dumps(project), encoding="utf-8")

            emitted = []
            transformed = []

            def transform(source, destination, _prepared_luts):
                if transformed:
                    self.assertTrue(
                        any(line.startswith("MEDIA_ITEM:") for line in emitted),
                        "the first styled photo must be visible before transforming the second photo",
                    )
                Path(destination).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                transformed.append(destination)

            with patch(
                "scripts.run_xpano_prepare_project.prepare_lut_chain",
                return_value=nullcontext(SimpleNamespace(style=object())),
            ), patch(
                "scripts.run_xpano_prepare_project.apply_style_lut_to_image", side_effect=transform
            ) as apply, patch(
                "builtins.print", side_effect=lambda *args, **_kwargs: emitted.append(str(args[0]))
            ):
                prepare_project(root, project["revision"], ["photo-track"])

            manifest = json.loads((root / "work" / "manifests" / "media_full.json").read_text(encoding="utf-8"))
            staged = [
                root / "work" / "media" / "photo-track" / "photo_00001.jpg",
                root / "work" / "media" / "photo-track" / "photo_00002.jpg",
            ]
            self.assertEqual(manifest["tracks"][0]["photos"], [str(path) for path in staged])
            self.assertEqual(manifest["tracks"][0]["photo_sensors"][0]["photos"], [str(path) for path in staged])
            self.assertEqual(apply.call_count, 2)

    def test_prepare_project_propagates_video_color_lut(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            video = Path(tmp) / "clip.mp4"
            lut = Path(tmp) / "restore.cube"
            frame = root / "work" / "frames" / "video-track" / "frame.jpg"
            video.write_bytes(b"video")
            lut.write_text("LUT_3D_SIZE 2\n", encoding="utf-8")
            frame.parent.mkdir(parents=True)
            Image.new("RGB", (100, 80), (32, 64, 96)).save(frame, "JPEG")
            project = json.loads(
                (Path(__file__).parents[1] / "schemas" / "fixtures" / "xpano_project_v3.example.json").read_text(encoding="utf-8")
            )
            project["tracks"] = [{
                "id": "video-track",
                "type": "ordinary_video",
                "label": "Color video",
                "sourcePath": str(video),
                "sourceFingerprint": {"size": 5, "mtimeNs": 0},
                "cameraProfile": "wide",
                "trim": None,
                "extraction": {
                    "framesPerSecond": 1.0,
                    "frameLimit": 0,
                    "styleLutPath": str(lut),
                },
                "status": "draft",
                "items": [],
            }]
            (root / "xpano_project.json").write_text(json.dumps(project), encoding="utf-8")
            manifest_track = {
                "track_id": "track_001_clip",
                "track_type": "ordinary_video",
                "device_label": "clip",
                "source_paths": [str(video.resolve())],
                "frames_per_second": 1.0,
                "max_frames": 0,
                "camera_profile": "wide",
                "metashape_mode": "pinhole_video_frames",
                "export_mode": "undistorted_frame",
                "group_label": "track_001_clip_frames",
                "sensor_label": "track_001_clip_frame",
                "photo_sensors": [{
                    "sensor_id": "track_001_clip_frame",
                    "sensor_label": "track_001_clip_frame",
                    "camera_profile": "wide",
                    "camera_identity": {},
                    "photos": [str(frame.resolve())],
                }],
                "photos": [str(frame.resolve())],
            }

            with patch(
                "scripts.run_xpano_prepare_project.build_ordinary_video_track",
                return_value=manifest_track,
            ) as build:
                prepare_project(root, project["revision"], ["video-track"])

            self.assertEqual(build.call_args.kwargs["style_lut_path"], str(lut))

    def test_prepare_project_resolves_the_bundled_dji_lut_for_osv_preset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            video = Path(tmp) / "DJI_0001.osv"
            frame = root / "work" / "frames" / "pano-track" / "left.jpg"
            video.write_bytes(b"video")
            frame.parent.mkdir(parents=True)
            Image.new("RGB", (100, 80), (32, 64, 96)).save(frame, "JPEG")
            project = json.loads(
                (Path(__file__).parents[1] / "schemas" / "fixtures" / "xpano_project_v3.example.json").read_text(encoding="utf-8")
            )
            project["tracks"] = [{
                "id": "pano-track",
                "type": "panoramic_video",
                "label": "DJI panorama",
                "sourcePath": str(video),
                "sourceFingerprint": {"size": 5, "mtimeNs": 0},
                "cameraProfile": None,
                "trim": None,
                "extraction": {
                    "framesPerSecond": 1.0,
                    "frameLimit": 0,
                    "colorLutPreset": "builtin:dji-osmo360-dlogm-rec709",
                },
                "status": "draft",
                "items": [],
            }]
            (root / "xpano_project.json").write_text(json.dumps(project), encoding="utf-8")
            manifest_track = {
                "track_id": "track_001_dji",
                "track_type": "panorama_video",
                "device_label": "dji",
                "source_paths": [str(video.resolve())],
                "frames_per_second": 1.0,
                "max_frames": 0,
                "start_time_seconds": 0.0,
                "end_time_seconds": 0.0,
                "metashape_mode": "dual_fisheye_station",
                "export_mode": "cubemap",
                "left_sensor_label": "track_001_dji_left",
                "right_sensor_label": "track_001_dji_right",
                "frames": [{"left": str(frame.resolve()), "right": str(frame.resolve())}],
            }

            with patch(
                "scripts.run_xpano_prepare_project.build_panorama_track",
                return_value=manifest_track,
            ) as build:
                prepare_project(root, project["revision"], ["pano-track"])

            self.assertEqual(
                build.call_args.kwargs["restoration_lut_path"],
                str(Path(__file__).parents[1] / "luts" / "dji-osmo360-dlogm-rec709-v1.cube"),
            )

    def test_photo_preview_is_emitted_before_second_identity_is_scanned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            photos = Path(tmp) / "photos"
            root.mkdir()
            photos.mkdir()
            for index in range(3):
                Image.new("RGB", (640, 480), (index * 30, 80, 120)).save(photos / f"{index}.jpg")
            project = json.loads(
                (Path(__file__).parents[1] / "schemas" / "fixtures" / "xpano_project_v3.example.json").read_text(encoding="utf-8")
            )
            project["tracks"] = [{
                "id": "photo-track",
                "type": "standard_photos",
                "label": "Reference photos",
                "sourcePath": str(photos),
                "sourceFingerprint": {"size": 0, "mtimeNs": 0},
                "cameraProfile": None,
                "trim": None,
                "extraction": {"framesPerSecond": 1.0, "frameLimit": 0},
                "status": "draft",
                "items": [],
            }]
            (root / "xpano_project.json").write_text(json.dumps(project), encoding="utf-8")
            emitted = []
            scanned = []

            def read_identity(path):
                if scanned:
                    self.assertTrue(
                        any(line.startswith("MEDIA_ITEM:") for line in emitted),
                        "the first thumbnail must be visible before scanning metadata for the second photo",
                    )
                scanned.append(str(path))
                return {
                    "width": 640,
                    "height": 480,
                    "make": "",
                    "model": "",
                    "lens_make": "",
                    "lens_model": "",
                    "focal_length": "",
                    "focal_length_35mm": "",
                }

            with patch("scripts.xpano_tracks.read_photo_identity", side_effect=read_identity), patch(
                "builtins.print", side_effect=lambda *args, **_kwargs: emitted.append(str(args[0]))
            ):
                prepare_project(root, project["revision"], ["photo-track"])

            self.assertEqual(len(scanned), 3)
            self.assertEqual(sum(line.startswith("MEDIA_ITEM:") for line in emitted), 3)

    def test_corrupt_exif_warning_does_not_escape_thumbnail_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.jpg"
            Image.new("RGB", (1200, 800), (60, 120, 180)).save(source)

            def noisy_transpose(image):
                warnings.warn("Corrupt EXIF data. Expecting to read 2 bytes but only got 0.", UserWarning)
                return image

            with warnings.catch_warnings(record=True) as captured, patch(
                "scripts.run_xpano_prepare_project.ImageOps.exif_transpose", side_effect=noisy_transpose
            ):
                warnings.simplefilter("always")
                stage_photo_item(root, "track-1", source, 1)

            self.assertEqual(captured, [])

    def test_corrupt_exif_warning_does_not_escape_photo_identity_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.jpg"
            Image.new("RGB", (1200, 800), (60, 120, 180)).save(source)
            real_open = Image.open

            def noisy_open(path):
                warnings.warn("Corrupt EXIF data. Expecting to read 2 bytes but only got 0.", UserWarning)
                return real_open(path)

            with warnings.catch_warnings(record=True) as captured, patch(
                "scripts.xpano_tracks.Image.open", side_effect=noisy_open
            ):
                warnings.simplefilter("always")
                identity = read_photo_identity(source)

            self.assertEqual(identity["width"], 1200)
            self.assertEqual(captured, [])

    def test_photo_source_does_not_reimport_generated_project_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            photos = Path(tmp) / "photos"
            root = photos / "xPano"
            root.mkdir(parents=True)
            Image.new("RGB", (640, 480), (40, 80, 120)).save(photos / "original.jpg")
            generated = root / "work" / "thumbnails" / "old-track"
            generated.mkdir(parents=True)
            Image.new("RGB", (320, 240), (120, 80, 40)).save(generated / "generated.jpg")
            project = json.loads(
                (Path(__file__).parents[1] / "schemas" / "fixtures" / "xpano_project_v3.example.json").read_text(encoding="utf-8")
            )
            project["tracks"] = [{
                "id": "photo-track",
                "type": "standard_photos",
                "label": "Reference photos",
                "sourcePath": str(photos),
                "sourceFingerprint": {"size": 0, "mtimeNs": 0},
                "cameraProfile": None,
                "trim": None,
                "extraction": {"framesPerSecond": 1.0, "frameLimit": 0},
                "status": "draft",
                "items": [],
            }]
            (root / "xpano_project.json").write_text(json.dumps(project), encoding="utf-8")

            result = prepare_project(root, project["revision"], ["photo-track"])

            self.assertEqual(len(result["tracks"][0]["items"]), 1)

    def test_closed_progress_pipe_stops_quietly(self):
        with patch("builtins.print", side_effect=OSError(22, "Invalid argument")):
            with self.assertRaises(SystemExit) as stopped:
                emit_line("PIPELINE_EVENT:{}")

        self.assertEqual(stopped.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
