import unittest
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from scripts.run_xpano_tracks_job import main, resolve_frames_per_second, validate_run_args


class RunXpanoTracksJobTests(unittest.TestCase):
    def test_legacy_entrypoint_preserves_explicit_metashape_runtime(self):
        from scripts.run_xpano_job import main as legacy_main

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_video = root / "input.osv"
            output = root / "out"
            runtime_site = root / "runtime with spaces" / "site-packages"
            input_video.write_bytes(b"video")
            runtime_site.mkdir(parents=True)
            argv = [
                "run_xpano_job.py",
                "--input",
                str(input_video),
                "--output",
                str(output),
                "--metashape",
                sys.executable,
                "--metashape-site-packages",
                str(runtime_site),
            ]

            with patch.object(sys, "argv", argv), patch(
                "scripts.run_xpano_job.run_metashape_pipeline"
            ) as runner:
                legacy_main()

            job = runner.call_args.args[0]
            self.assertEqual(job.metashape_site_packages, runtime_site.resolve())

    def test_legacy_seconds_per_frame_is_migrated_to_fps(self):
        self.assertEqual(resolve_frames_per_second(legacy_seconds_per_frame=2.0), 0.5)

    def test_rejects_negative_max_frames(self):
        with self.assertRaisesRegex(ValueError, "--max-frames"):
            validate_run_args(frames_per_second=1.0, max_frames=-1)

    def test_rejects_non_positive_frames_per_second(self):
        with self.assertRaisesRegex(ValueError, "--frames-per-second"):
            validate_run_args(frames_per_second=0, max_frames=0)

    def test_check_env_does_not_require_output_or_run_pipeline(self):
        argv = [
            "run_xpano_tracks_job.py",
            "--check-env",
            "--backend",
            "colmap",
            "--run-lichtfield",
        ]

        with patch.object(sys, "argv", argv), \
            patch("scripts.run_xpano_tracks_job.run_multi_track_pipeline") as runner, \
            patch("builtins.print") as print_fn:
            main()

        runner.assert_not_called()
        output = print_fn.call_args.args[0]
        self.assertIn("COLMAP", output)
        self.assertIn("LICHT Field Studio", output)

    def test_check_env_reports_lfs_densification_plugin(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = Path(tmp) / "plugin"
            plugin.mkdir()
            (plugin / "densify.py").write_text("print('ok')", encoding="utf-8")
            argv = [
                "run_xpano_tracks_job.py",
                "--check-env",
                "--backend",
                "colmap",
                "--run-lfs-densify",
                "--lfs-densify-plugin",
                str(plugin),
            ]

            with patch.object(sys, "argv", argv), \
                patch("scripts.dependency_checks.check_lfs_densify_imports") as import_check, \
                patch("scripts.run_xpano_tracks_job.run_multi_track_pipeline") as runner, \
                patch("builtins.print") as print_fn:
                import_check.return_value = type(
                    "Check",
                    (),
                    {
                        "name": "LichtFeld densification dependencies",
                        "requested": sys.executable,
                        "required": True,
                        "ok": True,
                        "resolved": sys.executable,
                        "message": "",
                    },
                )()
                main()

            runner.assert_not_called()
            output = print_fn.call_args.args[0]
            self.assertIn("LichtFeld densification plugin", output)
            self.assertIn("OK:", output)

    def test_check_env_strict_fails_for_missing_dependencies(self):
        argv = [
            "run_xpano_tracks_job.py",
            "--check-env",
            "--strict",
            "--backend",
            "colmap",
        ]

        with patch.object(sys, "argv", argv), \
            patch("scripts.dependency_checks.shutil.which", return_value=None), \
            patch("builtins.print"):
            with self.assertRaisesRegex(RuntimeError, "MISSING"):
                main()

    def test_main_delegates_material_tracks_to_app_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pano_a = root / "a.osv"
            pano_b = root / "b.insv"
            phone = root / "phone"
            drone = root / "drone"
            output = root / "out"
            runtime_site = root / "xPano Runtime" / "site-packages"
            for path in [pano_a, pano_b]:
                path.write_bytes(b"video")
            phone.mkdir()
            drone.mkdir()
            runtime_site.mkdir(parents=True)

            argv = [
                "run_xpano_tracks_job.py",
                "--output",
                str(output),
                "--metashape",
                sys.executable,
                "--metashape-site-packages",
                str(runtime_site),
                "--frames-per-second",
                "1.5",
                "--max-frames",
                "7",
                "--pano",
                str(pano_a),
                "--pano",
                str(pano_b),
                "--standard-track",
                "phone",
                str(phone),
                "--aerial-track",
                "mavic",
                str(drone),
            ]

            with patch.object(sys, "argv", argv), patch("scripts.run_xpano_tracks_job.run_multi_track_pipeline") as runner:
                main()

            job = runner.call_args.args[0]
            self.assertEqual(job.panorama_videos, [pano_a.resolve(), pano_b.resolve()])
            self.assertEqual(job.standard_photo_tracks, [("phone", [phone.resolve()])])
            self.assertEqual(job.aerial_photo_tracks, [("mavic", [drone.resolve()])])
            self.assertEqual(job.output_dir, output.resolve())
            self.assertEqual(job.frames_per_second, 1.5)
            self.assertEqual(job.max_frames, 7)
            self.assertEqual(job.metashape_exe, sys.executable)
            self.assertEqual(job.metashape_site_packages, runtime_site.resolve())
            self.assertEqual(job.backend, "metashape")

    def test_main_accepts_backend_argument(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pano = root / "a.osv"
            output = root / "out"
            pano.write_bytes(b"video")

            argv = [
                "run_xpano_tracks_job.py",
                "--output",
                str(output),
                "--metashape",
                "missing-metashape.exe",
                "--backend",
                "colmap",
                "--colmap",
                sys.executable,
                "--colmap-density-preset",
                "high-density",
                "--colmap-use-gpu",
                "--frames-per-second",
                "1.0",
                "--max-frames",
                "1",
                "--pano",
                str(pano),
            ]

            with patch.object(sys, "argv", argv), patch("scripts.run_xpano_tracks_job.run_multi_track_pipeline") as runner:
                main()

            job = runner.call_args.args[0]
            self.assertEqual(job.backend, "colmap")
            self.assertEqual(job.colmap_exe, sys.executable)
            self.assertEqual(job.colmap_density_preset, "high-density")
            self.assertTrue(job.colmap_use_gpu)

    def test_main_accepts_tauri_ui_colmap_parameters(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pano = root / "a.osv"
            output = root / "out"
            pano.write_bytes(b"video")

            argv = [
                "run_xpano_tracks_job.py",
                "--output",
                str(output),
                "--backend",
                "colmap",
                "--colmap",
                sys.executable,
                "--colmap-density-preset",
                "stable",
                "--colmap-matcher",
                "exhaustive",
                "--colmap-max-image-size",
                "2200",
                "--colmap-max-num-features",
                "9000",
                "--metashape-keypoint-limit",
                "50000",
                "--metashape-tiepoint-limit",
                "1000",
                "--up-axis",
                "y-up",
                "--pano",
                str(pano),
                "--pano-start",
                "2.5",
                "--pano-end",
                "12.0",
                "--pano-frames-per-second",
                "1.25",
                "--pano-max-frames",
                "20",
            ]

            with patch.object(sys, "argv", argv), patch("scripts.run_xpano_tracks_job.run_multi_track_pipeline") as runner:
                main()

            job = runner.call_args.args[0]
            self.assertEqual(job.colmap_matcher, "exhaustive")
            self.assertEqual(job.colmap_max_image_size, 2200)
            self.assertEqual(job.colmap_max_num_features, 9000)
            self.assertEqual(job.metashape_keypoint_limit, 50000)
            self.assertEqual(job.metashape_tiepoint_limit, 1000)
            self.assertEqual(job.up_axis, "y-up")
            self.assertEqual(
                job.track_extraction_settings[str(pano.resolve())],
                {
                    "start_time_seconds": 2.5,
                    "end_time_seconds": 12.0,
                    "frames_per_second": 1.25,
                    "max_frames": 20,
                },
            )

    def test_main_defaults_metashape_to_backbone_alignment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pano = root / "a.osv"
            output = root / "out"
            pano.write_bytes(b"video")

            argv = [
                "run_xpano_tracks_job.py",
                "--output",
                str(output),
                "--metashape",
                sys.executable,
                "--pano",
                str(pano),
            ]

            with patch.object(sys, "argv", argv), patch("scripts.run_xpano_tracks_job.run_multi_track_pipeline") as runner:
                main()

            job = runner.call_args.args[0]
            self.assertEqual(job.metashape_alignment_mode, "backbone")

    def test_main_maps_legacy_metashape_mixed_alignment_to_backbone(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pano = root / "a.osv"
            output = root / "out"
            pano.write_bytes(b"video")

            argv = [
                "run_xpano_tracks_job.py",
                "--output",
                str(output),
                "--metashape",
                sys.executable,
                "--metashape-alignment-mode",
                "mixed",
                "--pano",
                str(pano),
            ]

            with patch.object(sys, "argv", argv), patch("scripts.run_xpano_tracks_job.run_multi_track_pipeline") as runner:
                main()

            job = runner.call_args.args[0]
            self.assertEqual(job.metashape_alignment_mode, "backbone")

    def test_main_accepts_ordinary_video_tracks_and_extract_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            phone_video = root / "phone.mp4"
            output = root / "out"
            phone_video.write_bytes(b"video")

            argv = [
                "run_xpano_tracks_job.py",
                "--output",
                str(output),
                "--metashape",
                sys.executable,
                "--ordinary-video",
                str(phone_video),
                "--ordinary-view",
                "standard",
                "--ordinary-start",
                "1.5",
                "--ordinary-end",
                "9.5",
                "--ordinary-frames-per-second",
                "0.8",
                "--ordinary-max-frames",
                "10",
            ]

            with patch.object(sys, "argv", argv), patch("scripts.run_xpano_tracks_job.run_multi_track_pipeline") as runner:
                main()

            job = runner.call_args.args[0]
            self.assertEqual(job.ordinary_video_tracks, [phone_video.resolve()])
            self.assertEqual(job.track_camera_profiles[str(phone_video.resolve())], "standard")
            self.assertEqual(
                job.track_extraction_settings[str(phone_video.resolve())],
                {
                    "start_time_seconds": 1.5,
                    "end_time_seconds": 9.5,
                    "frames_per_second": 0.8,
                    "max_frames": 10,
                },
            )

    def test_main_accepts_lichtfield_parameters(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pano = root / "a.osv"
            output = root / "out"
            pano.write_bytes(b"video")

            argv = [
                "run_xpano_tracks_job.py",
                "--output",
                str(output),
                "--backend",
                "colmap",
                "--colmap",
                sys.executable,
                "--run-lichtfield",
                "--lichtfield",
                sys.executable,
                "--lichtfield-point-count",
                "120000",
                "--lichtfield-bilateral-grid",
                "16",
                "--pano",
                str(pano),
            ]

            with patch.object(sys, "argv", argv), patch("scripts.run_xpano_tracks_job.run_multi_track_pipeline") as runner:
                main()

            job = runner.call_args.args[0]
            self.assertTrue(job.run_lichtfield)
            self.assertEqual(job.lichtfield_exe, sys.executable)
            self.assertEqual(job.lichtfield_point_count, 120000)
            self.assertEqual(job.lichtfield_bilateral_grid, 16)

    def test_main_accepts_lfs_densification_parameters(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pano = root / "a.osv"
            output = root / "out"
            plugin = root / "plugin"
            pano.write_bytes(b"video")
            plugin.mkdir()

            argv = [
                "run_xpano_tracks_job.py",
                "--output",
                str(output),
                "--backend",
                "colmap",
                "--colmap",
                sys.executable,
                "--run-lfs-densify",
                "--lfs-densify-plugin",
                str(plugin),
                "--lfs-densify-python",
                sys.executable,
                "--lfs-densify-roma",
                "base",
                "--lfs-densify-num-refs",
                "3",
                "--lfs-densify-max-points",
                "50000",
                "--pano",
                str(pano),
            ]

            with patch.object(sys, "argv", argv), patch("scripts.run_xpano_tracks_job.run_multi_track_pipeline") as runner:
                main()

            job = runner.call_args.args[0]
            self.assertTrue(job.run_lfs_densify)
            self.assertEqual(job.lfs_densify_plugin, plugin.resolve())
            self.assertEqual(job.lfs_densify_python, sys.executable)
            self.assertEqual(job.lfs_densify_roma, "base")
            self.assertEqual(job.lfs_densify_num_refs, 3)
            self.assertEqual(job.lfs_densify_max_points, 50000)

    def test_metashape_backend_ignores_lichtfield_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pano = root / "a.osv"
            output = root / "out"
            pano.write_bytes(b"video")

            argv = [
                "run_xpano_tracks_job.py",
                "--output",
                str(output),
                "--metashape",
                sys.executable,
                "--run-lichtfield",
                "--lichtfield",
                "missing-lichtfield.exe",
                "--pano",
                str(pano),
            ]

            with patch.object(sys, "argv", argv), patch("scripts.run_xpano_tracks_job.run_multi_track_pipeline") as runner:
                main()

            job = runner.call_args.args[0]
            self.assertEqual(job.backend, "metashape")
            self.assertFalse(job.run_lichtfield)


if __name__ == "__main__":
    unittest.main()
