import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts.pipeline_core import JobConfig, MaterialTrack, MultiTrackJobConfig, ProgressEtaEstimator, collect_runtime_import_versions, emit_pipeline_event, manifest_expected_camera_count, material_tracks_to_job_config, metashape_process_env, recover_reexport_transaction, report_alignment_rate, run_metashape_pipeline, run_metashape_reexport_from_existing_project, run_multi_track_pipeline, write_run_summary
from scripts.colmap_backend import read_colmap_points3d, write_colmap_points3d


class FakeProcess:
    def __init__(self):
        self.stdout = ["PROGRESS:100\n"]

    def wait(self):
        return 0


class AppPipelineTests(unittest.TestCase):
    def test_metashape_camera_export_progress_uses_planned_images_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            manifest_path = output / "work" / "xpano_manifest.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(
                json.dumps({
                    "schema_version": 1,
                    "workflow": "xpano_multi_track",
                    "tracks": [],
                }),
                encoding="utf-8",
            )
            job = MultiTrackJobConfig(
                panorama_videos=[],
                standard_photo_tracks=[],
                aerial_photo_tracks=[],
                output_dir=output,
                frames_per_second=1.0,
                max_frames=0,
                metashape_exe="metashape.exe",
            )
            process = FakeProcess()
            process.stdout = ["Exporting camera [1/2]\n", "Exporting camera [2/2]\n"]
            logs = []

            with patch("scripts.pipeline_core.build_manifest", return_value=({}, manifest_path)), \
                patch("scripts.pipeline_core.subprocess.Popen", return_value=process), \
                patch("scripts.pipeline_core.write_run_summary"):
                run_multi_track_pipeline(job, Mock(), Mock(), logs.append)

            counted_stages = [
                json.loads(line.split(":", 1)[1])["stage"]
                for line in logs
                if line.startswith("PIPELINE_EVENT:")
                and json.loads(line.split(":", 1)[1]).get("current") is not None
            ]
            self.assertEqual(counted_stages, ["export.images", "export.images"])

    def test_metashape_reexport_progress_uses_planned_images_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            project_path = output / "work" / "xpano.psx"
            project_path.parent.mkdir(parents=True)
            project_path.write_text("project", encoding="utf-8")
            job = MultiTrackJobConfig(
                panorama_videos=[],
                standard_photo_tracks=[],
                aerial_photo_tracks=[],
                output_dir=output,
                frames_per_second=1.0,
                max_frames=0,
                metashape_exe="metashape.exe",
            )
            process = FakeProcess()
            process.stdout = ["Exporting camera [1/2]\n", "Exporting camera [2/2]\n"]
            logs = []
            runtime_site = output / "runtime with spaces" / "site-packages"
            runtime_site.mkdir(parents=True)
            job.metashape_site_packages = runtime_site
            job.selected_component_key = "42"

            with patch.dict("scripts.pipeline_core.os.environ", {}, clear=True), \
                patch("scripts.pipeline_core.subprocess.Popen", return_value=process) as popen, \
                patch("scripts.pipeline_core.write_run_summary"):
                run_metashape_reexport_from_existing_project(job, project_path, Mock(), logs.append)

            command = popen.call_args.args[0]
            self.assertEqual(
                command[command.index("--xpano-site-packages") + 1],
                str(runtime_site),
            )
            self.assertEqual(command[command.index("--component-key") + 1], "42")

            counted_stages = [
                json.loads(line.split(":", 1)[1])["stage"]
                for line in logs
                if line.startswith("PIPELINE_EVENT:")
                and json.loads(line.split(":", 1)[1]).get("current") is not None
            ]
            self.assertEqual(counted_stages, ["export.images", "export.images"])
            stages = [
                json.loads(line.split(":", 1)[1])["stage"]
                for line in logs
                if line.startswith("PIPELINE_EVENT:")
            ]
            self.assertIn("output.validate", stages)

    def test_reexport_stages_before_publishing_and_keeps_live_images_available_for_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            project_path = output / "work" / "manual correction" / "fixed.psx"
            project_path.parent.mkdir(parents=True)
            project_path.write_text("manually corrected project", encoding="utf-8")
            previous_image = output / "images" / "previous.jpg"
            previous_image.parent.mkdir()
            previous_image.write_bytes(b"previous")
            cache_path = output / "work" / "export_image_cache.json"
            cache_path.write_text('{"schemaVersion": 1, "cameras": {}}', encoding="utf-8")
            job = MultiTrackJobConfig(
                panorama_videos=[],
                standard_photo_tracks=[],
                aerial_photo_tracks=[],
                output_dir=output,
                frames_per_second=1.0,
                max_frames=0,
                metashape_exe="metashape.exe",
                overwrite_generated=True,
                reexport_existing_project=True,
                existing_project_path=project_path,
            )

            def stage_export(_job, _project, _progress, _log, *, export_dir, reuse_images_dir, image_cache_path, image_cache_output):
                self.assertTrue(previous_image.is_file())
                self.assertEqual(reuse_images_dir, output / "images")
                self.assertEqual(image_cache_path, cache_path)
                staged_image = export_dir / "images" / "previous.jpg"
                staged_image.parent.mkdir(parents=True)
                staged_image.write_bytes(previous_image.read_bytes())
                sparse = export_dir / "sparse" / "0"
                sparse.mkdir(parents=True)
                for name in ("cameras.bin", "images.bin", "points3D.bin"):
                    (sparse / name).write_bytes(name.encode("ascii"))
                cache_output = Path(image_cache_output)
                cache_output.parent.mkdir(parents=True)
                cache_output.write_text('{"schemaVersion": 1, "cameras": {}}', encoding="utf-8")

            with patch("scripts.pipeline_core.run_metashape_reexport_from_existing_project", side_effect=stage_export) as reexport, \
                patch("scripts.pipeline_core.verify_output", return_value={"ok": True}), \
                patch("scripts.pipeline_core.write_run_summary"):
                run_multi_track_pipeline(job, Mock(), Mock(), Mock())

            self.assertTrue(project_path.is_file())
            self.assertEqual(previous_image.read_bytes(), b"previous")
            self.assertTrue((output / "sparse" / "0" / "images.bin").is_file())
            self.assertTrue(cache_path.is_file())
            self.assertFalse(list(output.glob(".xpano-reexport-stage-*")))
            self.assertFalse(list(output.glob(".xpano-reexport-backup-*")))
            self.assertFalse((output / "work" / "reexport_transaction.json").exists())
            reexport.assert_called_once()
            self.assertEqual(reexport.call_args.args[1], project_path)

    def test_failed_reexport_restores_previous_outputs_and_preserves_psx(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            project_path = output / "work" / "xpano.psx"
            project_path.parent.mkdir(parents=True)
            project_path.write_text("manually corrected project", encoding="utf-8")
            previous_image = output / "images" / "previous.jpg"
            previous_image.parent.mkdir()
            previous_image.write_bytes(b"previous")
            job = MultiTrackJobConfig(
                panorama_videos=[],
                standard_photo_tracks=[],
                aerial_photo_tracks=[],
                output_dir=output,
                frames_per_second=1.0,
                max_frames=0,
                metashape_exe="metashape.exe",
                overwrite_generated=True,
                reexport_existing_project=True,
                existing_project_path=project_path,
            )

            with patch(
                "scripts.pipeline_core.run_metashape_reexport_from_existing_project",
                side_effect=lambda *_args, export_dir, **_kwargs: (
                    (export_dir / "images").mkdir(parents=True),
                    (export_dir / "images" / "partial.jpg").write_bytes(b"partial"),
                    (_ for _ in ()).throw(RuntimeError("export failed")),
                )[-1],
            ):
                with self.assertRaisesRegex(RuntimeError, "export failed"):
                    run_multi_track_pipeline(job, Mock(), Mock(), Mock())

            self.assertEqual(project_path.read_text(encoding="utf-8"), "manually corrected project")
            self.assertEqual(previous_image.read_bytes(), b"previous")
            self.assertFalse(list(output.glob(".xpano-reexport-stage-*")))
            self.assertFalse((output / "work" / "reexport_transaction.json").exists())

    def test_reexport_recovery_rolls_back_an_interrupted_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            live = output / "images"
            backup = output / ".xpano-reexport-backup-test"
            stage = output / ".xpano-reexport-stage-test"
            live.mkdir()
            backup.mkdir()
            stage.mkdir()
            (live / "new.jpg").write_bytes(b"new")
            (backup / "images").mkdir()
            (backup / "images" / "old.jpg").write_bytes(b"old")
            marker = output / "work" / "reexport_transaction.json"
            marker.parent.mkdir()
            marker.write_text(json.dumps({
                "state": "publishing",
                "stageDir": str(stage),
                "backupDir": str(backup),
                "originalTargets": ["images"],
            }), encoding="utf-8")

            recover_reexport_transaction(output, Mock())

            self.assertEqual((live / "old.jpg").read_bytes(), b"old")
            self.assertFalse((live / "new.jpg").exists())
            self.assertFalse(backup.exists())
            self.assertFalse(stage.exists())
            self.assertFalse(marker.exists())

    def test_reexport_recovery_keeps_a_committed_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            live = output / "images"
            backup = output / ".xpano-reexport-backup-test"
            stage = output / ".xpano-reexport-stage-test"
            live.mkdir()
            backup.mkdir()
            stage.mkdir()
            (live / "new.jpg").write_bytes(b"new")
            (backup / "images").mkdir()
            (backup / "images" / "old.jpg").write_bytes(b"old")
            marker = output / "work" / "reexport_transaction.json"
            marker.parent.mkdir()
            marker.write_text(json.dumps({
                "state": "committed",
                "stageDir": str(stage),
                "backupDir": str(backup),
                "originalTargets": ["images"],
            }), encoding="utf-8")

            recover_reexport_transaction(output, Mock())

            self.assertEqual((live / "new.jpg").read_bytes(), b"new")
            self.assertFalse(backup.exists())
            self.assertFalse(stage.exists())
            self.assertFalse(marker.exists())

    def test_progress_eta_estimator_waits_for_stable_samples(self):
        ticks = iter([0.0, 0.2, 2.0])
        estimator = ProgressEtaEstimator(clock=lambda: next(ticks), min_elapsed=1.0)

        self.assertIsNone(estimator.update(0, 10))
        self.assertIsNone(estimator.update(1, 10))
        self.assertEqual(estimator.update(2, 10), 8)

    def test_emit_pipeline_event_includes_eta_when_known(self):
        logs = []

        emit_pipeline_event(
            logs.append,
            phase="export",
            stage="export.cameras",
            percent=98,
            phase_percent=50,
            message="Exporting 5/10 cameras",
            current=5,
            total=10,
            eta_seconds=42,
        )

        self.assertEqual(len(logs), 1)
        self.assertTrue(logs[0].startswith("PIPELINE_EVENT:"))
        payload = json.loads(logs[0].split(":", 1)[1])
        self.assertEqual(payload["phase"], "export")
        self.assertEqual(payload["stage"], "export.cameras")
        self.assertEqual(payload["current"], 5)
        self.assertEqual(payload["total"], 10)
        self.assertEqual(payload["etaSeconds"], 42)

    def test_report_alignment_rate_emits_structured_metric(self):
        logs = []

        report_alignment_rate(logs.append, aligned=7, total=10, percent=95)

        self.assertEqual(len(logs), 1)
        self.assertTrue(logs[0].startswith("PIPELINE_EVENT:"))
        payload = json.loads(logs[0].split(":", 1)[1])
        self.assertEqual(payload["phase"], "align")
        self.assertEqual(payload["stage"], "align.rate")
        self.assertEqual(payload["percent"], 95)
        self.assertEqual(payload["alignedCameras"], 7)
        self.assertEqual(payload["totalCameras"], 10)
        self.assertEqual(payload["alignmentRate"], 70.0)

    def test_manifest_expected_camera_count_counts_mixed_tracks(self):
        manifest = {
            "tracks": [
                {"track_type": "panorama_video", "frames": [{"left": "a", "right": "b"}, {"left": "c", "right": "d"}]},
                {"track_type": "ordinary_video", "photos": ["1.jpg", "2.jpg", "3.jpg"]},
                {"track_type": "standard_photos", "photos": ["p.jpg"]},
            ]
        }

        self.assertEqual(manifest_expected_camera_count(manifest), 8)

    def test_runtime_import_report_marks_missing_dependency_as_failure(self):
        def fake_import_module(name):
            if name == "cv2":
                raise ImportError("missing cv2")
            return SimpleNamespace(__version__="1.0", __file__=f"{name}.py")

        report = collect_runtime_import_versions(import_module=fake_import_module)

        self.assertFalse(report["ok"])
        self.assertTrue(report["modules"]["numpy"]["ok"])
        self.assertFalse(report["modules"]["cv2"]["ok"])
        self.assertIn("missing cv2", report["modules"]["cv2"]["error"])

    def test_metashape_process_env_isolates_gui_runtime_from_metashape(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as external_tmp:
            root = Path(tmp)
            (root / "cv2").mkdir()
            (root / "numpy.libs").mkdir()
            external = Path(external_tmp) / "external"
            metashape_site = Path(external_tmp) / "metashape-site"
            metashape_site.mkdir()
            (metashape_site / "numpy.libs").mkdir()
            (metashape_site / "cv2").mkdir()
            with patch("scripts.pipeline_core.internal_root", return_value=root), patch.dict(
                "scripts.pipeline_core.os.environ",
                {
                    "QT_PLUGIN_PATH": "bad-qt",
                    "QT_QPA_PLATFORM_PLUGIN_PATH": "bad-platforms",
                    "PYTHONHOME": "bad-python-home",
                    "PYTHONPATH": os.pathsep.join([str(root), str(root / "cv2"), str(external)]),
                    "PATH": os.pathsep.join([str(root), str(root / "numpy.libs"), "existing-path"]),
                    "XPANO_METASHAPE_SITE_PACKAGES": str(metashape_site),
                },
                clear=True,
            ):
                env = metashape_process_env()

        self.assertNotIn("QT_PLUGIN_PATH", env)
        self.assertNotIn("QT_QPA_PLATFORM_PLUGIN_PATH", env)
        self.assertNotIn("PYTHONHOME", env)
        self.assertNotIn(str(root), env.get("PYTHONPATH", ""))
        self.assertNotIn(str(root / "cv2"), env.get("PYTHONPATH", ""))
        self.assertIn(str(external), env["PYTHONPATH"])
        self.assertEqual(env["PYTHONPATH"].split(os.pathsep)[0], str(metashape_site))
        self.assertNotIn(str(root), env.get("PATH", ""))
        self.assertNotIn(str(root / "numpy.libs"), env.get("PATH", ""))
        self.assertEqual(
            env["PATH"].split(os.pathsep),
            [str(metashape_site / "numpy.libs"), str(metashape_site / "cv2"), str(metashape_site), "existing-path"],
        )

    def test_material_tracks_build_multi_track_job_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pano = root / "a.osv"
            ordinary = root / "clip.mp4"
            phone = root / "phone"
            drone = root / "drone"
            output = root / "out"
            pano.write_bytes(b"video")
            ordinary.write_bytes(b"video")
            phone.mkdir()
            drone.mkdir()

            job = material_tracks_to_job_config(
                tracks=[
                    MaterialTrack(track_type="panorama_video", label="insta", paths=[pano]),
                    MaterialTrack(track_type="ordinary_video", label="clip", paths=[ordinary], camera_profile="standard"),
                    MaterialTrack(track_type="standard_photos", label="phone", paths=[phone]),
                    MaterialTrack(track_type="aerial_photos", label="mavic", paths=[drone]),
                ],
                output_dir=output,
                frames_per_second=1.0,
                max_frames=5,
                metashape_exe="metashape.exe",
                metashape_site_packages=root / "runtime" / "site-packages",
            )

            self.assertEqual(job.panorama_videos, [pano.resolve()])
            self.assertEqual(job.ordinary_video_tracks, [ordinary.resolve()])
            self.assertEqual(job.track_camera_profiles[str(ordinary.resolve())], "standard")
            self.assertEqual(job.standard_photo_tracks, [("phone", [phone.resolve()])])
            self.assertEqual(job.aerial_photo_tracks, [("mavic", [drone.resolve()])])
            self.assertEqual(job.output_dir, output.resolve())
            self.assertEqual(job.metashape_site_packages, (root / "runtime" / "site-packages").resolve())
            self.assertEqual(job.backend, "metashape")

    def test_material_tracks_reject_empty_track(self):
        with self.assertRaisesRegex(ValueError, "must contain at least one path"):
            material_tracks_to_job_config(
                tracks=[MaterialTrack(track_type="panorama_video", label="empty", paths=[])],
                output_dir=Path("out"),
                frames_per_second=1.0,
                max_frames=0,
                metashape_exe="metashape.exe",
            )

    def test_single_video_gui_pipeline_uses_manifest_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            manifest_path = output / "work" / "xpano_manifest.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text("{}", encoding="utf-8")
            video = output / "input.osv"
            video.write_bytes(b"video")
            runtime_site = output / "runtime with spaces" / "site-packages"
            runtime_site.mkdir(parents=True)
            job = JobConfig(
                input_video=video,
                output_dir=output,
                frames_per_second=1.0,
                max_frames=10,
                metashape_exe="metashape.exe",
                metashape_site_packages=runtime_site,
            )

            popen_calls = []

            def fake_popen(cmd, **kwargs):
                popen_calls.append(cmd)
                return FakeProcess()

            with patch.dict("scripts.pipeline_core.os.environ", {}, clear=True), \
                patch("scripts.pipeline_core.build_manifest", return_value=({}, manifest_path)) as build_manifest, \
                patch("scripts.pipeline_core.subprocess.Popen", side_effect=fake_popen), \
                patch("scripts.pipeline_core.write_run_summary"):
                run_metashape_pipeline(job, Mock(), Mock(), Mock())

            build_manifest.assert_called_once()
            self.assertIn("log_cb", build_manifest.call_args.kwargs)
            command = popen_calls[0]
            self.assertIn("--manifest", command)
            self.assertIn(str(manifest_path), command)
            self.assertIn("--alignment-mode", command)
            self.assertEqual(command[command.index("--alignment-mode") + 1], "backbone")
            self.assertIn("--up-axis", command)
            self.assertEqual(command[command.index("--up-axis") + 1], "y-up")
            self.assertEqual(
                command[command.index("--xpano-site-packages") + 1],
                str(runtime_site),
            )
            self.assertNotIn("--input-root", command)

    def test_colmap_backend_builds_and_runs_colmap_plan_without_metashape(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            left = output / "left.jpg"
            right = output / "right.jpg"
            left.write_bytes(b"left")
            right.write_bytes(b"right")
            manifest_path = output / "work" / "xpano_manifest.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_text = """{
                  "schema_version": 1,
                  "workflow": "xpano_multi_track",
                  "tracks": [
                    {
                      "track_id": "track_001",
                      "track_type": "panorama_video",
                      "metashape_mode": "dual_fisheye_station",
                      "export_mode": "cubemap",
                      "frames": [
                        {
                          "left": "%s",
                          "right": "%s"
                        }
                      ]
                    }
                  ]
                }""" % (left.as_posix(), right.as_posix())
            manifest_path.write_text(manifest_text, encoding="utf-8")
            job = MultiTrackJobConfig(
                panorama_videos=[],
                standard_photo_tracks=[],
                aerial_photo_tracks=[],
                output_dir=output,
                frames_per_second=1.0,
                max_frames=0,
                metashape_exe="metashape.exe",
                backend="colmap",
                manifest_path=manifest_path,
                colmap_density_preset="high-density",
            )

            fake_plan = Mock()
            fake_plan.output_dir = output / "colmap"
            progress = Mock()
            log = Mock()
            with patch("scripts.pipeline_core.subprocess.Popen") as popen, \
                patch("scripts.pipeline_core.build_colmap_plan", return_value=fake_plan) as build_colmap_plan, \
                patch("scripts.pipeline_core.run_colmap_plan") as run_colmap_plan, \
                patch("scripts.pipeline_core.publish_colmap_output", return_value={"image_dir": str(output / "images"), "sparse_model_path": str(output / "sparse" / "0")}), \
                patch("scripts.pipeline_core.write_run_summary"):
                run_multi_track_pipeline(job, progress, Mock(), log)

            popen.assert_not_called()
            build_colmap_plan.assert_called_once()
            self.assertEqual(build_colmap_plan.call_args.kwargs["config"].max_num_features, 8192)
            self.assertTrue(build_colmap_plan.call_args.kwargs["config"].guided_matching)
            run_colmap_plan.assert_called_once()
            progress.assert_any_call(35)
            progress.assert_any_call(100)

    def test_colmap_alignment_rate_uses_mapper_model_before_cubemap_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            left = output / "left.jpg"
            right = output / "right.jpg"
            left.write_bytes(b"left")
            right.write_bytes(b"right")
            manifest_path = output / "work" / "xpano_manifest.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(
                json.dumps({
                    "schema_version": 1,
                    "workflow": "xpano_multi_track",
                    "tracks": [{
                        "track_id": "track_001",
                        "track_type": "panorama_video",
                        "metashape_mode": "dual_fisheye_station",
                        "export_mode": "cubemap",
                        "frames": [{"left": left.as_posix(), "right": right.as_posix()}],
                    }],
                }),
                encoding="utf-8",
            )
            job = MultiTrackJobConfig(
                panorama_videos=[],
                standard_photo_tracks=[],
                aerial_photo_tracks=[],
                output_dir=output,
                frames_per_second=1.0,
                max_frames=0,
                metashape_exe="metashape.exe",
                backend="colmap",
                manifest_path=manifest_path,
            )
            native_model = output / "colmap" / "sparse" / "0"
            published_model = output / "sparse" / "0"
            fake_plan = Mock()
            fake_plan.output_dir = output / "colmap"
            fake_plan.sparse_dir = output / "colmap" / "sparse"
            fake_plan.image_dir = output / "colmap" / "colmap_images"
            logs = []

            with patch("scripts.pipeline_core.build_colmap_plan", return_value=fake_plan), \
                patch("scripts.pipeline_core.run_colmap_plan", return_value={"sparse_model_path": str(native_model)}), \
                patch("scripts.pipeline_core.publish_colmap_output", return_value={"image_dir": str(output / "images"), "sparse_model_path": str(published_model)}), \
                patch("scripts.pipeline_core.read_colmap_images", side_effect=lambda path: [object()] * (2 if Path(path) == native_model else 10)) as read_images, \
                patch("scripts.pipeline_core.write_run_summary"):
                run_multi_track_pipeline(job, Mock(), Mock(), logs.append)

            alignment_event = next(
                json.loads(line.removeprefix("PIPELINE_EVENT:"))
                for line in logs
                if line.startswith("PIPELINE_EVENT:") and '"stage": "align.rate"' in line
            )
            self.assertEqual(alignment_event["alignedCameras"], 2)
            self.assertEqual(alignment_event["totalCameras"], 2)
            self.assertEqual(alignment_event["alignmentRate"], 100.0)
            read_images.assert_called_once_with(native_model)

    def test_colmap_backend_resolves_executable_before_building_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            left = output / "left.jpg"
            right = output / "right.jpg"
            left.write_bytes(b"left")
            right.write_bytes(b"right")
            manifest_path = output / "work" / "xpano_manifest.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(
                """{
                  "schema_version": 1,
                  "workflow": "xpano_multi_track",
                  "tracks": [
                    {
                      "track_id": "track_001",
                      "track_type": "panorama_video",
                      "metashape_mode": "dual_fisheye_station",
                      "export_mode": "cubemap",
                      "frames": [
                        {"left": "%s", "right": "%s"}
                      ]
                    }
                  ]
                }""" % (left.as_posix(), right.as_posix()),
                encoding="utf-8",
            )
            bundled = output / "tools" / "colmap" / "bin" / "colmap.exe"
            job = MultiTrackJobConfig(
                panorama_videos=[],
                standard_photo_tracks=[],
                aerial_photo_tracks=[],
                output_dir=output,
                frames_per_second=1.0,
                max_frames=0,
                metashape_exe="metashape.exe",
                backend="colmap",
                manifest_path=manifest_path,
                colmap_exe="colmap",
            )
            fake_plan = Mock()
            fake_plan.output_dir = output / "colmap"

            with patch("scripts.pipeline_core.resolve_executable", return_value=str(bundled)) as resolve_executable, \
                patch("scripts.pipeline_core.build_colmap_plan", return_value=fake_plan) as build_colmap_plan, \
                patch("scripts.pipeline_core.run_colmap_plan"), \
                patch("scripts.pipeline_core.publish_colmap_output", return_value={"image_dir": str(output / "images"), "sparse_model_path": str(output / "sparse" / "0")}), \
                patch("scripts.pipeline_core.write_run_summary"):
                run_multi_track_pipeline(job, Mock(), Mock(), Mock())

            resolve_executable.assert_called_once_with("colmap", "colmap")
            self.assertEqual(build_colmap_plan.call_args.kwargs["config"].colmap_exe, str(bundled))

    def test_colmap_backend_can_run_lichtfield_postprocess(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            left = output / "left.jpg"
            right = output / "right.jpg"
            left.write_bytes(b"left")
            right.write_bytes(b"right")
            manifest_path = output / "work" / "xpano_manifest.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(
                """{
                  "schema_version": 1,
                  "workflow": "xpano_multi_track",
                  "tracks": [
                    {
                      "track_id": "track_001",
                      "track_type": "panorama_video",
                      "metashape_mode": "dual_fisheye_station",
                      "export_mode": "cubemap",
                      "frames": [
                        {"left": "%s", "right": "%s"}
                      ]
                    }
                  ]
                }""" % (left.as_posix(), right.as_posix()),
                encoding="utf-8",
            )
            job = MultiTrackJobConfig(
                panorama_videos=[],
                standard_photo_tracks=[],
                aerial_photo_tracks=[],
                output_dir=output,
                frames_per_second=1.0,
                max_frames=0,
                metashape_exe="metashape.exe",
                backend="colmap",
                manifest_path=manifest_path,
                run_lichtfield=True,
                lichtfield_exe="lichtfield-studio.exe",
                lichtfield_point_count=120000,
                lichtfield_bilateral_grid=16,
            )

            sparse_model = output / "sparse" / "0"
            image_dir = output / "colmap" / "colmap_images"
            final_image_dir = output / "images"
            fake_plan = Mock()
            fake_plan.output_dir = output / "colmap"
            fake_plan.sparse_dir = output / "colmap" / "sparse"
            fake_plan.image_dir = image_dir
            with patch("scripts.pipeline_core.build_colmap_plan", return_value=fake_plan), \
                patch("scripts.pipeline_core.run_colmap_plan", return_value={"sparse_model_path": str(output / "colmap" / "sparse" / "0")}), \
                patch("scripts.pipeline_core.publish_colmap_output", return_value={"image_dir": str(final_image_dir), "sparse_model_path": str(sparse_model)}), \
                patch("scripts.pipeline_core.run_lichtfield_command") as run_lichtfield_command, \
                patch("scripts.pipeline_core.write_run_summary"):
                run_multi_track_pipeline(job, Mock(), Mock(), Mock())

            config = run_lichtfield_command.call_args.args[0]
            self.assertEqual(config.executable, "lichtfield-studio.exe")
            self.assertEqual(config.input_colmap, sparse_model)
            self.assertEqual(config.image_dir, final_image_dir)
            self.assertEqual(config.output_dir, output / "lichtfield")
            self.assertEqual(config.point_count, 120000)
            self.assertEqual(config.bilateral_grid, 16)

    def test_colmap_backend_can_run_lfs_densification(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            left = output / "left.jpg"
            right = output / "right.jpg"
            left.write_bytes(b"left")
            right.write_bytes(b"right")
            manifest_path = output / "work" / "xpano_manifest.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(
                """{
                  "schema_version": 1,
                  "workflow": "xpano_multi_track",
                  "tracks": [
                    {
                      "track_id": "track_001",
                      "track_type": "panorama_video",
                      "metashape_mode": "dual_fisheye_station",
                      "export_mode": "cubemap",
                      "frames": [
                        {"left": "%s", "right": "%s"}
                      ]
                    }
                  ]
                }""" % (left.as_posix(), right.as_posix()),
                encoding="utf-8",
            )
            job = MultiTrackJobConfig(
                panorama_videos=[],
                standard_photo_tracks=[],
                aerial_photo_tracks=[],
                output_dir=output,
                frames_per_second=1.0,
                max_frames=0,
                metashape_exe="metashape.exe",
                backend="colmap",
                manifest_path=manifest_path,
                run_lfs_densify=True,
                lfs_densify_python="python.exe",
                lfs_densify_plugin=Path("plugin"),
                lfs_densify_roma="fast",
                lfs_densify_max_points=50000,
            )

            fake_plan = Mock()
            fake_plan.output_dir = output / "colmap"
            fake_plan.sparse_dir = output / "colmap" / "sparse"
            fake_plan.image_dir = output / "colmap" / "colmap_images"
            sparse_zero = output / "sparse" / "0"

            def fake_densify(config, **kwargs):
                sparse_zero.mkdir(parents=True, exist_ok=True)
                write_colmap_points3d(sparse_zero / "points3D.bin", [])
                (sparse_zero / config.out_name).write_bytes(
                    b"ply\nformat binary_little_endian 1.0\nelement vertex 1\n"
                    b"property float x\nproperty float y\nproperty float z\n"
                    b"property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n"
                    + struct.pack("<fffBBB", 1.0, 2.0, 3.0, 4, 5, 6)
                )

            with patch("scripts.pipeline_core.build_colmap_plan", return_value=fake_plan), \
                patch("scripts.pipeline_core.run_colmap_plan", return_value={"sparse_model_path": str(output / "colmap" / "sparse" / "0")}), \
                patch("scripts.pipeline_core.publish_colmap_output", return_value={"image_dir": str(output / "images"), "sparse_model_path": str(output / "sparse" / "0")}), \
                patch("scripts.pipeline_core.run_densify_command", side_effect=fake_densify) as run_densify_command, \
                patch("scripts.pipeline_core.write_run_summary"):
                run_multi_track_pipeline(job, Mock(), Mock(), Mock())

            config = run_densify_command.call_args.args[0]
            self.assertEqual(config.scene_root, output)
            self.assertEqual(config.images_subdir, "images")
            self.assertEqual(config.out_name, "points3D_dense.ply")
            self.assertEqual(config.plugin_dir, Path("plugin"))
            self.assertEqual(config.python_exe, "python.exe")
            self.assertEqual(config.roma_setting, "fast")
            self.assertEqual(config.max_points, 50000)
            self.assertEqual(read_colmap_points3d(sparse_zero), [{"id": 1, "xyz": (1.0, 2.0, 3.0), "rgb": (4, 5, 6), "error": 1.0, "track": []}])

    def test_overwrite_keeps_current_manifest_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            manifest_path = output / "work" / "xpano_manifest.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(
                """{
                  "schema_version": 1,
                  "workflow": "xpano_multi_track",
                  "tracks": []
                }""",
                encoding="utf-8",
            )
            (output / "images").mkdir()
            (output / "sparse").mkdir()
            (output / "colmap").mkdir()
            (output / "lichtfield").mkdir()

            clear_log = []

            from scripts.pipeline_core import clear_generated_outputs

            clear_generated_outputs(output, clear_log.append, preserve_paths=[manifest_path])

            self.assertTrue(manifest_path.exists())
            self.assertFalse((output / "images").exists())
            self.assertFalse((output / "sparse").exists())
            self.assertFalse((output / "colmap").exists())
            self.assertFalse((output / "lichtfield").exists())

    def test_colmap_summary_uses_colmap_native_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            manifest_path = output / "work" / "xpano_manifest.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(
                """{
                  "schema_version": 1,
                  "workflow": "xpano_multi_track",
                  "tracks": []
                }""",
                encoding="utf-8",
            )
            image_dir = output / "images"
            sparse_dir = output / "sparse" / "0"
            image_dir.mkdir()
            sparse_dir.mkdir(parents=True)
            (image_dir / "000001_left.jpg").write_bytes(b"left")
            for name in ["cameras.bin", "images.bin", "points3D.bin"]:
                (sparse_dir / name).write_bytes(b"bin")
            job = MultiTrackJobConfig(
                panorama_videos=[],
                standard_photo_tracks=[],
                aerial_photo_tracks=[],
                output_dir=output,
                frames_per_second=1.0,
                max_frames=0,
                metashape_exe="metashape.exe",
                backend="colmap",
                manifest_path=manifest_path,
            )

            write_run_summary(job)

            summary = json.loads((output / "xpano_run_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["backend"], "colmap")
            self.assertEqual(summary["colmap_input_images"], 1)
            self.assertEqual(summary["export_verification"]["sparse_model_path"], str(sparse_dir))

    def test_multi_track_pipeline_passes_all_track_types_to_manifest_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            manifest_path = output / "work" / "xpano_manifest.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text("{}", encoding="utf-8")
            pano_a = output / "a.osv"
            pano_b = output / "b.insv"
            phone_dir = output / "phone"
            drone_dir = output / "drone"
            for path in [pano_a, pano_b]:
                path.write_bytes(b"video")
            phone_dir.mkdir()
            drone_dir.mkdir()
            job = MultiTrackJobConfig(
                panorama_videos=[pano_a, pano_b],
                standard_photo_tracks=[("phone", [phone_dir])],
                aerial_photo_tracks=[("mavic", [drone_dir])],
                output_dir=output,
                frames_per_second=1.0,
                max_frames=5,
                metashape_exe="metashape.exe",
            )

            popen_calls = []

            def fake_popen(cmd, **kwargs):
                popen_calls.append(cmd)
                return FakeProcess()

            with patch("scripts.pipeline_core.build_manifest", return_value=({}, manifest_path)) as build_manifest, \
                patch("scripts.pipeline_core.subprocess.Popen", side_effect=fake_popen), \
                patch("scripts.pipeline_core.write_run_summary"):
                run_multi_track_pipeline(job, Mock(), Mock(), Mock())

            kwargs = build_manifest.call_args.kwargs
            self.assertEqual(kwargs["panorama_videos"], [pano_a, pano_b])
            self.assertEqual(kwargs["standard_photo_tracks"], [("phone", [phone_dir])])
            self.assertEqual(kwargs["aerial_photo_tracks"], [("mavic", [drone_dir])])
            self.assertIn("log_cb", kwargs)
            command = popen_calls[0]
            self.assertIn("--manifest", command)
            self.assertIn(str(manifest_path), command)
            self.assertIn("--alignment-mode", command)
            self.assertEqual(command[command.index("--alignment-mode") + 1], "backbone")
            self.assertIn("--up-axis", command)
            self.assertEqual(command[command.index("--up-axis") + 1], "y-up")


if __name__ == "__main__":
    unittest.main()
