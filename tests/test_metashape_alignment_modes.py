import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class FakeCalibration:
    def __init__(self):
        self.type = None
        self.width = 4000
        self.height = 3000
        self.f = 1000
        self.cx = 0
        self.cy = 0
        self.b1 = 1
        self.b2 = 1
        self.k1 = 0.1
        self.k2 = 0.2
        self.k3 = 0.3
        self.k4 = 0.4
        self.p1 = 0.01
        self.p2 = 0.02


class FakeSensor:
    _next_key = 1

    def __init__(self):
        self.key = FakeSensor._next_key
        FakeSensor._next_key += 1
        self.label = ""
        self.type = None
        self.width = 4000
        self.height = 3000
        self.pixel_width = 0.001
        self.pixel_height = 0.001
        self.focal_length = 4.0
        self.fixed_params = []
        self.calibration = FakeCalibration()
        self.user_calib = None


class FakePhoto:
    def __init__(self, path):
        self.path = str(path)


class FakeCamera:
    _next_key = 1

    def __init__(self, path, group):
        self.key = FakeCamera._next_key
        FakeCamera._next_key += 1
        self.label = Path(path).name
        self.photo = FakePhoto(path)
        self.group = group
        self.sensor = FakeSensor()
        if "portrait" in Path(path).name:
            self.sensor.width = 3000
            self.sensor.height = 4000
        self.transform = None
        self.enabled = "disabled" not in Path(path).name


class FakeGroup:
    def __init__(self):
        self.label = ""
        self.type = None


class FakeChunk:
    def __init__(self):
        self.cameras = []
        self.sensors = []
        self.camera_groups = []
        self.operations = []
        self.alignment_enabled_keys = []

    def addSensor(self):
        sensor = FakeSensor()
        self.sensors.append(sensor)
        return sensor

    def addCameraGroup(self):
        group = FakeGroup()
        self.camera_groups.append(group)
        return group

    def addPhotos(self, paths, **kwargs):
        if "group" in kwargs:
            raise AssertionError("This fake Metashape version does not expose CameraGroup.key")
        self.operations.append(("addPhotos", [Path(path).name for path in paths], None))
        for path in paths:
            self.cameras.append(FakeCamera(path, None))

    def remove(self, sensor):
        if sensor in self.sensors:
            self.sensors.remove(sensor)

    def matchPhotos(self, **kwargs):
        self.operations.append((
            "matchPhotos",
            len(kwargs.get("cameras") or self.cameras),
            len(kwargs.get("pairs") or []),
            [group.type for group in self.camera_groups],
            dict(kwargs),
        ))

    def alignCameras(self, **kwargs):
        self.alignment_enabled_keys.append([camera.key for camera in self.cameras if camera.enabled])
        cameras_arg = kwargs.get("cameras")
        if cameras_arg is None:
            cameras = self.cameras
        else:
            wanted = set(cameras_arg)
            cameras = [camera for camera in self.cameras if camera.key in wanted]
        self.operations.append(("alignCameras", [camera.key for camera in cameras]))
        for camera in cameras:
            camera.transform = object()

    def optimizeCameras(self, **kwargs):
        self.operations.append(("optimizeCameras", dict(kwargs), [group.type for group in self.camera_groups]))


class ReorderingFakeChunk(FakeChunk):
    def addPhotos(self, paths, **kwargs):
        existing = list(self.cameras)
        super().addPhotos(paths, **kwargs)
        imported = self.cameras[len(existing):]
        self.cameras[:] = imported + existing


def fake_metashape_module():
    return types.SimpleNamespace(
        Sensor=types.SimpleNamespace(Type=types.SimpleNamespace(
            Frame="Frame",
            Fisheye="Fisheye",
            EquidistantFisheye="EquidistantFisheye",
        )),
        CameraGroup=types.SimpleNamespace(Type=types.SimpleNamespace(Station="Station", Folder="Folder")),
        Calibration=FakeCalibration,
        Chunk=FakeChunk,
        app=types.SimpleNamespace(document=types.SimpleNamespace()),
    )


def import_pipeline_with_fake_metashape():
    fake_modules = {
        "Metashape": fake_metashape_module(),
        "align_ground_plane": types.SimpleNamespace(main=lambda: None),
        "export_colmap": types.SimpleNamespace(run_mixed_export=lambda _path, **_kwargs: None),
    }
    with patch.dict(sys.modules, fake_modules):
        sys.modules.pop("scripts.metashape_pipeline", None)
        return importlib.import_module("scripts.metashape_pipeline")


def import_alignment_variants_with_fake_metashape():
    with patch.dict(sys.modules, {"Metashape": fake_metashape_module()}):
        sys.modules.pop("scripts.run_alignment_variants", None)
        return importlib.import_module("scripts.run_alignment_variants")


class MetashapeAlignmentModeTests(unittest.TestCase):
    def test_4k_panorama_sensor_uses_resolution_normalized_initial_calibration(self):
        pipeline = import_pipeline_with_fake_metashape()
        chunk = FakeChunk()
        source_camera = FakeCamera("pano_left.jpg", None)
        source_camera.sensor.width = 1920
        source_camera.sensor.height = 1920
        source_camera.sensor.calibration.f = 6275.7

        sensor = pipeline.make_track_sensor(
            chunk,
            source_camera,
            "pano_left",
            pipeline.panorama_sensor_type(),
        )

        self.assertEqual(sensor.type, "EquidistantFisheye")
        self.assertIsNot(sensor.calibration, source_camera.sensor.calibration)
        self.assertIsNotNone(sensor.user_calib)
        self.assertEqual(sensor.user_calib.type, "EquidistantFisheye")
        self.assertEqual((sensor.user_calib.width, sensor.user_calib.height), (1920, 1920))
        self.assertAlmostEqual(sensor.user_calib.f, 520.8333333333334)
        self.assertAlmostEqual(sensor.pixel_width, 0.0048)
        self.assertAlmostEqual(sensor.pixel_height, 0.0048)
        self.assertEqual(sensor.focal_length, 2.5)

    def test_alignment_summary_reports_panorama_and_frame_quality_separately(self):
        pipeline = import_pipeline_with_fake_metashape()
        chunk = FakeChunk()
        chunk.addPhotos(["pano_left.jpg", "pano_right.jpg", "frame_one.jpg", "frame_two.jpg"])
        for camera in chunk.cameras[:2]:
            camera.sensor.type = "Fisheye"
            camera.transform = object()
        for camera in chunk.cameras[2:]:
            camera.sensor.type = "Frame"
        chunk.cameras[2].transform = object()

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            result = pipeline.write_alignment_summary(
                chunk,
                output,
                output / "project.psx",
                alignment_mode="backbone",
            )
            summary = (output / "xpano_alignment_summary.txt").read_text(encoding="utf-8")

        self.assertIn("panorama_cameras=2", summary)
        self.assertIn("panorama_aligned=2", summary)
        self.assertIn("frame_cameras=2", summary)
        self.assertIn("frame_aligned=1", summary)
        self.assertEqual(result["panorama_aligned"], 2)
        self.assertEqual(result["frame_aligned"], 1)

    def test_match_kwargs_converts_camera_objects_to_metashape_keys_and_preserves_keypoints(self):
        pipeline = import_pipeline_with_fake_metashape()
        chunk = FakeChunk()
        chunk.addPhotos(["a.jpg", "b.jpg"])
        args = types.SimpleNamespace(keypoint_limit=40000, tiepoint_limit=0)

        kwargs = pipeline._match_kwargs(args, cameras=chunk.cameras)

        self.assertEqual(kwargs["cameras"], [camera.key for camera in chunk.cameras])
        self.assertTrue(kwargs["keep_keypoints"])
        self.assertFalse(kwargs["reset_matches"])
        self.assertNotIn("pairs", kwargs)

    def test_incremental_alignment_preserves_the_existing_panorama_solution_when_supported(self):
        pipeline = import_pipeline_with_fake_metashape()
        chunk = types.SimpleNamespace(alignCameras=Mock())

        with patch.object(pipeline, "_supports_reset_alignment", return_value=True):
            pipeline._align_cameras(chunk, preserve_alignment=True)

        chunk.alignCameras.assert_called_once_with(adaptive_fitting=True, reset_alignment=False)

    def test_legacy_mixed_mode_normalizes_to_the_restored_staged_workflow(self):
        from scripts.metashape_alignment_modes import normalize_alignment_mode

        self.assertEqual(normalize_alignment_mode("mixed"), "backbone")
        self.assertEqual(normalize_alignment_mode("legacy"), "backbone")

    def test_add_photos_rejects_an_incomplete_metashape_import(self):
        pipeline = import_pipeline_with_fake_metashape()
        chunk = FakeChunk()
        original_add_photos = chunk.addPhotos

        def drop_last_camera(paths, **kwargs):
            original_add_photos(paths, **kwargs)
            chunk.cameras.pop()

        chunk.addPhotos = drop_last_camera

        with self.assertRaisesRegex(RuntimeError, "camera count"):
            pipeline.add_photos_get_new(chunk, ["one.jpg", "two.jpg"])

    def test_add_photos_rejects_imported_camera_path_mismatch(self):
        pipeline = import_pipeline_with_fake_metashape()
        chunk = FakeChunk()
        original_add_photos = chunk.addPhotos

        def replace_imported_path(paths, **kwargs):
            original_add_photos(paths, **kwargs)
            chunk.cameras[-1].photo.path = "unexpected.jpg"

        chunk.addPhotos = replace_imported_path

        with self.assertRaisesRegex(RuntimeError, "photo paths"):
            pipeline.add_photos_get_new(chunk, ["expected.jpg"])

    def test_add_photos_assigns_imported_cameras_to_group_without_group_key(self):
        pipeline = import_pipeline_with_fake_metashape()
        chunk = FakeChunk()
        group = chunk.addCameraGroup()

        cameras = pipeline.add_photos_get_new(chunk, ["grouped.jpg"], group=group)

        self.assertEqual(len(cameras), 1)
        self.assertIs(cameras[0].group, group)

    def test_panorama_import_supports_camera_groups_without_group_key(self):
        pipeline = import_pipeline_with_fake_metashape()
        chunk = FakeChunk()
        track = {
            "track_id": "pano",
            "frames": [
                {
                    "frame_id": "pano_0001",
                    "left": "pano_0001_left.jpg",
                    "right": "pano_0001_right.jpg",
                }
            ],
        }

        groups, cameras = pipeline.import_panorama_track(chunk, track)

        self.assertEqual(len(groups), 1)
        self.assertEqual(len(cameras), 2)
        self.assertTrue(all(camera.group is groups[0] for camera in cameras))

    def test_legacy_import_supports_camera_groups_without_group_key(self):
        pipeline = import_pipeline_with_fake_metashape()
        chunk = FakeChunk()
        with tempfile.TemporaryDirectory() as tmp:
            frame_dir = Path(tmp) / "frame_0001"
            frame_dir.mkdir()
            (frame_dir / "left.jpg").touch()
            (frame_dir / "right.jpg").touch()

            groups = pipeline.import_legacy_frames(chunk, Path(tmp), max_frames=0)

        self.assertEqual(len(groups), 1)
        self.assertEqual(len(chunk.cameras), 2)
        self.assertTrue(all(camera.group is groups[0] for camera in chunk.cameras))

    def test_variant_folder_group_import_supports_camera_groups_without_group_key(self):
        variants = import_alignment_variants_with_fake_metashape()
        chunk = FakeChunk()
        image_paths = [
            Path("station_0001") / "left.jpg",
            Path("station_0001") / "right.jpg",
        ]

        variants.import_images(chunk, image_paths, "folder_groups")

        self.assertEqual(len(chunk.camera_groups), 1)
        self.assertTrue(all(camera.group is chunk.camera_groups[0] for camera in chunk.cameras))

    def test_wide_frame_profile_uses_wider_initial_intrinsics(self):
        pipeline = import_pipeline_with_fake_metashape()
        standard = FakeSensor()
        wide = FakeSensor()
        for sensor in [standard, wide]:
            sensor.width = 5120
            sensor.height = 3840

        pipeline.configure_frame_sensor(standard, camera_profile="standard")
        pipeline.configure_frame_sensor(wide, camera_profile="wide")

        self.assertGreater(standard.calibration.f, wide.calibration.f)
        self.assertLess(wide.calibration.f, 2600)
        self.assertEqual(wide.calibration.cx, 0)
        self.assertEqual(wide.calibration.cy, 0)

    def test_backbone_alignment_restores_panorama_first_incremental_workflow(self):
        pipeline = import_pipeline_with_fake_metashape()
        chunk = FakeChunk()
        manifest = {
            "tracks": [
                {
                    "track_id": "pano",
                    "track_type": "panorama_video",
                    "frames": [
                        {"frame_id": "pano_1", "left": "pano_0001_left.jpg", "right": "pano_0001_right.jpg"},
                        {"frame_id": "pano_2", "left": "pano_0002_left.jpg", "right": "pano_0002_right.jpg"},
                    ],
                },
                {
                    "track_id": "phone",
                    "track_type": "ordinary_video",
                    "group_label": "phone_frames",
                    "sensor_label": "phone_frame",
                    "photos": ["phone_0001.jpg", "phone_0002.jpg"],
                },
            ]
        }
        args = types.SimpleNamespace(keypoint_limit=40000, tiepoint_limit=0)

        pipeline.run_backbone_alignment(chunk, manifest, args)

        operations = chunk.operations
        first_match_index = next(index for index, item in enumerate(operations) if item[0] == "matchPhotos")
        add_before_match = [item for item in operations[:first_match_index] if item[0] == "addPhotos"]
        match_calls = [item for item in operations if item[0] == "matchPhotos"]
        align_calls = [item for item in operations if item[0] == "alignCameras"]
        optimize_calls = [item for item in operations if item[0] == "optimizeCameras"]
        pano_keys = [camera.key for camera in chunk.cameras[:4]]
        frame_keys = [camera.key for camera in chunk.cameras[4:]]

        self.assertEqual([item[1] for item in add_before_match], [
            ["pano_0001_left.jpg", "pano_0001_right.jpg"],
            ["pano_0002_left.jpg", "pano_0002_right.jpg"],
        ])
        self.assertEqual(len(match_calls), 2)
        self.assertEqual(match_calls[0][1], 4)
        self.assertEqual(match_calls[0][2], 0)
        self.assertIn("Station", match_calls[0][3])
        self.assertNotIn("cameras", match_calls[0][4])
        self.assertTrue(match_calls[0][4]["keep_keypoints"])
        self.assertFalse(match_calls[0][4]["reset_matches"])
        self.assertNotIn("pairs", match_calls[0][4])
        self.assertEqual(match_calls[1][1], 6)
        self.assertTrue(match_calls[1][4]["keep_keypoints"])
        self.assertEqual(align_calls, [("alignCameras", pano_keys), ("alignCameras", pano_keys + frame_keys)])
        self.assertEqual(chunk.alignment_enabled_keys[0], pano_keys)
        self.assertEqual(set(chunk.alignment_enabled_keys[1]), set(pano_keys + frame_keys))
        self.assertEqual(len(optimize_calls), 2)
        self.assertTrue(all(group_type == "Folder" for group_type in optimize_calls[0][2]))
        self.assertTrue(all(group_type == "Folder" for group_type in optimize_calls[1][2]))

    def test_backbone_mixed_import_tracks_cameras_by_key_when_metashape_reorders(self):
        pipeline = import_pipeline_with_fake_metashape()
        chunk = ReorderingFakeChunk()
        manifest = {
            "tracks": [
                {
                    "track_id": "pano",
                    "track_type": "panorama_video",
                    "frames": [
                        {"frame_id": "pano_1", "left": "pano_0001_left.jpg", "right": "pano_0001_right.jpg"},
                        {"frame_id": "pano_2", "left": "pano_0002_left.jpg", "right": "pano_0002_right.jpg"},
                    ],
                },
                {
                    "track_id": "photo",
                    "track_type": "standard_photos",
                    "group_label": "photo_group",
                    "sensor_label": "photo_sensor",
                    "photos": ["photo_0001.jpg", "photo_0002.jpg"],
                },
            ]
        }
        args = types.SimpleNamespace(keypoint_limit=40000, tiepoint_limit=0)

        pipeline.run_backbone_alignment(chunk, manifest, args)

        cameras_by_name = {Path(camera.photo.path).name: camera for camera in chunk.cameras}
        self.assertEqual(set(cameras_by_name), {
            "pano_0001_left.jpg",
            "pano_0001_right.jpg",
            "pano_0002_left.jpg",
            "pano_0002_right.jpg",
            "photo_0001.jpg",
            "photo_0002.jpg",
        })
        self.assertTrue(
            all(cameras_by_name[name].sensor.type == "EquidistantFisheye" for name in cameras_by_name if "pano_" in name)
        )
        self.assertTrue(
            all(cameras_by_name[name].sensor.type == "Frame" for name in cameras_by_name if "photo_" in name)
        )

    def test_backbone_alignment_emits_stable_execution_plan_stage_ids(self):
        pipeline = import_pipeline_with_fake_metashape()
        chunk = FakeChunk()
        manifest = {
            "tracks": [
                {
                    "track_id": "pano",
                    "track_type": "panorama_video",
                    "frames": [{"frame_id": "pano_1", "left": "left.jpg", "right": "right.jpg"}],
                },
                {
                    "track_id": "phone",
                    "track_type": "ordinary_video",
                    "group_label": "phone_frames",
                    "sensor_label": "phone_frame",
                    "photos": ["phone.jpg"],
                },
            ]
        }
        args = types.SimpleNamespace(keypoint_limit=40000, tiepoint_limit=0)
        events = []

        with patch.object(pipeline, "emit_pipeline_event", side_effect=events.append):
            pipeline.run_backbone_alignment(chunk, manifest, args)

        stages = [event["stage"] for event in events]
        self.assertEqual(
            stages,
            [
                "metashape.pano.import",
                "metashape.pano.station",
                "metashape.pano.match",
                "metashape.pano.align",
                "metashape.pano.release",
                "metashape.pano.optimize",
                "metashape.frame.import",
                "metashape.frame.match",
                "metashape.frame.align",
                "metashape.all.optimize",
            ],
        )

    def test_backbone_panorama_only_matches_and_solves_the_panorama_once(self):
        pipeline = import_pipeline_with_fake_metashape()
        chunk = FakeChunk()
        manifest = {
            "tracks": [
                {
                    "track_id": "pano",
                    "track_type": "panorama_video",
                    "frames": [{"frame_id": "pano_1", "left": "left.jpg", "right": "right.jpg"}],
                }
            ]
        }
        args = types.SimpleNamespace(keypoint_limit=40000, tiepoint_limit=0)

        pipeline.run_backbone_alignment(chunk, manifest, args)

        self.assertEqual(len([item for item in chunk.operations if item[0] == "matchPhotos"]), 1)
        self.assertEqual(
            [item for item in chunk.operations if item[0] == "alignCameras"],
            [("alignCameras", [camera.key for camera in chunk.cameras])],
        )
        self.assertEqual(len([item for item in chunk.operations if item[0] == "optimizeCameras"]), 1)
        optimize = next(item for item in chunk.operations if item[0] == "optimizeCameras")
        self.assertTrue(all(group_type == "Folder" for group_type in optimize[2]))

    def test_backbone_panorama_only_reports_station_stage_before_matching(self):
        pipeline = import_pipeline_with_fake_metashape()
        chunk = FakeChunk()
        manifest = {
            "tracks": [
                {
                    "track_id": "pano",
                    "track_type": "panorama_video",
                    "frames": [{"frame_id": "pano_1", "left": "left.jpg", "right": "right.jpg"}],
                }
            ]
        }
        args = types.SimpleNamespace(keypoint_limit=40000, tiepoint_limit=0)
        events = []

        with patch.object(pipeline, "emit_pipeline_event", side_effect=events.append):
            pipeline.run_backbone_alignment(chunk, manifest, args)

        stages = [event["stage"] for event in events]
        self.assertLess(stages.index("metashape.pano.station"), stages.index("metashape.pano.match"))

    def test_backbone_flat_only_matches_and_solves_flat_cameras_once(self):
        pipeline = import_pipeline_with_fake_metashape()
        chunk = FakeChunk()
        manifest = {
            "tracks": [
                {
                    "track_id": "photo",
                    "track_type": "standard_photos",
                    "group_label": "photo_group",
                    "sensor_label": "photo_sensor",
                    "photos": ["one.jpg", "two.jpg"],
                }
            ]
        }
        args = types.SimpleNamespace(keypoint_limit=40000, tiepoint_limit=0)

        pipeline.run_backbone_alignment(chunk, manifest, args)

        match_calls = [item for item in chunk.operations if item[0] == "matchPhotos"]
        self.assertEqual(len(match_calls), 1)
        self.assertNotIn("cameras", match_calls[0][4])
        self.assertEqual(
            [item for item in chunk.operations if item[0] == "alignCameras"],
            [("alignCameras", [camera.key for camera in chunk.cameras])],
        )
        self.assertEqual(len([item for item in chunk.operations if item[0] == "optimizeCameras"]), 1)

    def test_backbone_does_not_import_flat_media_after_panorama_alignment_failure(self):
        pipeline = import_pipeline_with_fake_metashape()
        chunk = FakeChunk()
        manifest = {
            "tracks": [
                {
                    "track_id": "pano",
                    "track_type": "panorama_video",
                    "frames": [{"frame_id": "pano_1", "left": "left.jpg", "right": "right.jpg"}],
                },
                {
                    "track_id": "photo",
                    "track_type": "standard_photos",
                    "group_label": "photo_group",
                    "sensor_label": "photo_sensor",
                    "photos": ["enabled.jpg", "disabled.jpg"],
                },
            ]
        }
        args = types.SimpleNamespace(keypoint_limit=40000, tiepoint_limit=0)

        def fail_panorama_alignment(**kwargs):
            chunk.alignment_enabled_keys.append([camera.key for camera in chunk.cameras if camera.enabled])
            chunk.operations.append(("alignCameras", [camera.key for camera in chunk.cameras]))
            raise RuntimeError("native panorama alignment failure")

        chunk.alignCameras = fail_panorama_alignment
        with self.assertRaisesRegex(RuntimeError, "native panorama alignment failure"):
            pipeline.run_backbone_alignment(chunk, manifest, args)

        self.assertEqual(len(chunk.cameras), 2)
        self.assertEqual(chunk.alignment_enabled_keys, [[camera.key for camera in chunk.cameras[:2]]])
        self.assertEqual(len([item for item in chunk.operations if item[0] == "alignCameras"]), 1)
        self.assertEqual([item for item in chunk.operations if item[0] == "optimizeCameras"], [])

    def test_backbone_does_not_retry_partial_panorama_alignment_before_incremental_phase(self):
        pipeline = import_pipeline_with_fake_metashape()
        chunk = FakeChunk()
        manifest = {
            "tracks": [
                {
                    "track_id": "pano",
                    "track_type": "panorama_video",
                    "frames": [{"frame_id": "pano_1", "left": "left.jpg", "right": "right.jpg"}],
                },
                {
                    "track_id": "photo",
                    "track_type": "standard_photos",
                    "group_label": "photo_group",
                    "sensor_label": "photo_sensor",
                    "photos": ["one.jpg", "two.jpg"],
                },
            ]
        }
        args = types.SimpleNamespace(keypoint_limit=40000, tiepoint_limit=0)
        align_calls = []

        def partially_align_first_panorama_pass(**kwargs):
            wanted = set(kwargs.get("cameras") or [camera.key for camera in chunk.cameras])
            cameras = [camera for camera in chunk.cameras if camera.key in wanted]
            align_calls.append([camera.key for camera in cameras])
            if len(align_calls) == 1:
                cameras = cameras[:1]
            for camera in cameras:
                camera.transform = object()

        chunk.alignCameras = partially_align_first_panorama_pass
        pipeline.run_backbone_alignment(chunk, manifest, args)

        pano_keys = [camera.key for camera in chunk.cameras[:2]]
        frame_keys = [camera.key for camera in chunk.cameras[2:]]
        self.assertEqual(align_calls, [pano_keys, pano_keys + frame_keys])
        self.assertTrue(all(camera.transform is not None for camera in chunk.cameras))
        self.assertEqual(len([item for item in chunk.operations if item[0] == "matchPhotos"]), 2)

    def test_import_photo_track_splits_declared_sensor_group_by_actual_geometry(self):
        pipeline = import_pipeline_with_fake_metashape()
        chunk = FakeChunk()
        track = {
            "track_id": "photo",
            "track_type": "standard_photos",
            "group_label": "photo_group",
            "photo_sensors": [
                {
                    "sensor_label": "photo_sensor",
                    "photos": ["landscape.jpg", "portrait.jpg"],
                }
            ],
        }

        with patch("builtins.print") as print_mock:
            cameras = pipeline.import_photo_track(chunk, track)

        self.assertEqual(len(cameras), 2)
        self.assertIsNot(cameras[0].sensor, cameras[1].sensor)
        self.assertEqual(
            [(camera.sensor.width, camera.sensor.height) for camera in cameras],
            [(4000, 3000), (3000, 4000)],
        )
        self.assertEqual([camera.sensor.label for camera in cameras], ["photo_sensor", "photo_sensor_actual_02"])
        self.assertTrue(all(camera.sensor.type == "Frame" for camera in cameras))
        self.assertEqual(print_mock.call_count, 1)
        self.assertIn("partitions=2", print_mock.call_args.args[0])

    def test_backbone_validation_rejects_flat_camera_with_fisheye_sensor(self):
        pipeline = import_pipeline_with_fake_metashape()
        chunk = FakeChunk()
        chunk.addPhotos(["flat.jpg"])
        chunk.cameras[0].sensor.type = "Fisheye"

        with self.assertRaisesRegex(RuntimeError, "Flat camera has incompatible sensor type"):
            pipeline.validate_backbone_camera_sets(chunk, [], list(chunk.cameras))

    def test_backbone_validation_rejects_duplicate_camera_keys_in_the_chunk(self):
        pipeline = import_pipeline_with_fake_metashape()
        chunk = FakeChunk()
        chunk.addPhotos(["one.jpg", "two.jpg"])
        for camera in chunk.cameras:
            camera.sensor.type = "Frame"
        chunk.cameras[1].key = chunk.cameras[0].key

        with self.assertRaisesRegex(RuntimeError, "duplicate camera keys"):
            pipeline.validate_backbone_camera_sets(chunk, [], list(chunk.cameras))

    def test_backbone_validation_rejects_overlapping_panorama_and_frame_sets(self):
        pipeline = import_pipeline_with_fake_metashape()
        chunk = FakeChunk()
        chunk.addPhotos(["shared.jpg"])

        with self.assertRaisesRegex(RuntimeError, "sets overlap"):
            pipeline.validate_backbone_camera_sets(chunk, list(chunk.cameras), list(chunk.cameras))

    def test_backbone_validation_rejects_uncovered_chunk_camera(self):
        pipeline = import_pipeline_with_fake_metashape()
        chunk = FakeChunk()
        chunk.addPhotos(["included.jpg", "missing.jpg"])
        for camera in chunk.cameras:
            camera.sensor.type = "Frame"

        with self.assertRaisesRegex(RuntimeError, "do not cover the fresh Metashape chunk"):
            pipeline.validate_backbone_camera_sets(chunk, [], [chunk.cameras[0]])

    def test_backbone_validation_rejects_camera_without_sensor(self):
        pipeline = import_pipeline_with_fake_metashape()
        chunk = FakeChunk()
        chunk.addPhotos(["flat.jpg"])
        chunk.cameras[0].sensor = None

        with self.assertRaisesRegex(RuntimeError, "Flat camera has no sensor"):
            pipeline.validate_backbone_camera_sets(chunk, [], list(chunk.cameras))

    def test_backbone_validation_rejects_non_positive_sensor_dimensions(self):
        pipeline = import_pipeline_with_fake_metashape()
        chunk = FakeChunk()
        chunk.addPhotos(["flat.jpg"])
        chunk.cameras[0].sensor.type = "Frame"
        chunk.cameras[0].sensor.width = 0

        with self.assertRaisesRegex(RuntimeError, "invalid dimensions"):
            pipeline.validate_backbone_camera_sets(chunk, [], list(chunk.cameras))

    def test_export_emits_both_execution_plan_stage_ids(self):
        pipeline = import_pipeline_with_fake_metashape()
        events = []

        with patch.object(pipeline, "emit_stage", side_effect=lambda stage, message, percent, phase="align", **_kwargs: events.append((stage, phase, percent))):
            pipeline.export_project_outputs(Path("D:/project/export"))

        self.assertEqual(
            events,
            [
                ("export.images", "export", 97),
                ("export.colmap", "export", 99),
            ],
        )


if __name__ == "__main__":
    unittest.main()
