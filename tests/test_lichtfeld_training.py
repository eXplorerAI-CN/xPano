import os
import json
import tempfile
import unittest
from pathlib import Path

from scripts.lichtfeld_training import (
    LichtfeldLogTracker,
    LichtfeldTrainingConfig,
    LichtfeldStartupWatchdog,
    build_lichtfeld_diagnostic,
    build_lichtfeld_environment,
    classify_lichtfeld_failure,
    parse_runtime_state_result,
    build_runtime_override_code,
    build_lichtfeld_training_command,
)


class LichtfeldTrainingCommandTests(unittest.TestCase):
    def test_builds_visible_single_stage_training_command_with_runtime_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = LichtfeldTrainingConfig(
                executable=root / "bin" / "LichtFeld-Studio.exe",
                data_path=root / "dataset",
                output_path=root / "output",
            )

            command = build_lichtfeld_training_command(config)

            self.assertEqual(command[0], str(config.executable))
            self.assertIn("--data-path", command)
            self.assertIn(str(config.data_path), command)
            self.assertIn("--output-path", command)
            self.assertIn(str(config.output_path), command)
            self.assertIn("--log-file", command)
            self.assertIn(str(config.output_path / "lichtfeld.log"), command)
            self.assertNotIn("--train", command)
            self.assertNotIn("--python-script", command)
            self.assertIn("--iter", command)
            self.assertIn("30000", command)
            self.assertIn("--strategy", command)
            self.assertIn("mrnf", command)
            self.assertIn("--steps-scaler", command)
            self.assertEqual(command[command.index("--steps-scaler") + 1], "1")
            self.assertIn("--bilateral-grid", command)
            self.assertNotIn("--headless", command)
            self.assertNotIn("--no-cpu-cache", command)
            self.assertNotIn("--no-fs-cache", command)

    def test_maps_advanced_parameters_to_supported_v053_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = LichtfeldTrainingConfig(
                executable=root / "LichtFeld-Studio.exe",
                data_path=root / "dataset",
                output_path=root / "output",
                iterations=12000,
                strategy="mcmc",
                sh_degree=2,
                max_gaussians=750000,
                resize_factor="4",
                max_width=2048,
                test_every=8,
                use_cpu_cache=False,
                use_fs_cache=False,
                centralize="by_cameras",
                undistort=True,
                enable_mip=True,
                bilateral_grid=False,
                enable_eval=True,
                background_mode="random",
                gui=False,
            )

            command = build_lichtfeld_training_command(config)

            for flag in [
                "--headless",
                "--no-cpu-cache",
                "--no-fs-cache",
                "--undistort",
                "--enable-mip",
                "--eval",
            ]:
                self.assertIn(flag, command)
            self.assertNotIn("--bilateral-grid", command)
            self.assertIn("--resize_factor", command)
            self.assertIn("by_cameras", command)
            self.assertIn("random", command)

    def test_rejects_invalid_single_stage_parameters(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = LichtfeldTrainingConfig(
                executable=root / "LichtFeld-Studio.exe",
                data_path=root / "dataset",
                output_path=root / "output",
                iterations=0,
            )

            with self.assertRaisesRegex(ValueError, "iterations"):
                build_lichtfeld_training_command(config)

    def test_resolves_paths_before_switching_to_the_runtime_working_directory(self):
        config = LichtfeldTrainingConfig(
            executable=Path("runtime/lichtfeld-studio/bin/LichtFeld-Studio.exe"),
            data_path=Path("dataset"),
            output_path=Path("output"),
        )

        command = build_lichtfeld_training_command(config)

        self.assertTrue(Path(command[0]).is_absolute())
        self.assertTrue(Path(command[command.index("--data-path") + 1]).is_absolute())
        self.assertTrue(Path(command[command.index("--output-path") + 1]).is_absolute())
        self.assertTrue(Path(command[command.index("--log-file") + 1]).is_absolute())

    def test_runtime_override_reapplies_exact_values_after_dataset_auto_scaling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = LichtfeldTrainingConfig(
                executable=root / "LichtFeld-Studio.exe",
                data_path=root / "dataset",
                output_path=root / "output",
                iterations=12345,
                strategy="mcmc",
                sh_degree=2,
                max_gaussians=765432,
                max_width=2048,
                undistort=True,
                enable_mip=True,
                bilateral_grid=True,
                enable_eval=True,
            )

            code = build_runtime_override_code(config)

            self.assertIn("p.iterations = 12345", code)
            self.assertIn("p.steps_scaler = 1.0", code)
            self.assertIn("('strategy', p.strategy, 'mcmc')", code)
            self.assertIn("('sh_degree', p.sh_degree, 2)", code)
            self.assertIn("('max_cap', p.max_cap, 765432)", code)
            self.assertIn("p.use_bilateral_grid = True", code)
            self.assertIn("('mip_filter', p.mip_filter, True)", code)
            self.assertIn("d.max_width = 2048", code)

    def test_launch_environment_uses_an_xpano_owned_home_and_removes_host_python_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / "LichtFeld/bin/LichtFeld-Studio.exe"
            executable.parent.mkdir(parents=True)
            profile = root / "xpano-local-data/lfs-profile"
            inherited = {
                "PATH": r"C:\\HostPython;C:\\HostQt",
                "SystemRoot": r"C:\\Windows",
                "PYTHONHOME": r"C:\\HostPython",
                "PYTHONPATH": r"C:\\HostPython\\Lib",
                "PYTHONUSERBASE": r"C:\\Users\\Person\\Python",
                "VIRTUAL_ENV": r"C:\\venv",
                "CONDA_PREFIX": r"C:\\conda",
                "XPANO_ROOT": r"C:\\old-xpano",
                "XPANO_PYTHON": r"C:\\old-xpano\\python.exe",
                "QT_PLUGIN_PATH": r"C:\\HostQt\\plugins",
                "CUDA_PATH": r"C:\\Program Files\\NVIDIA GPU Computing Toolkit\\CUDA\\v12.6",
                "CUDA_PATH_V13_0": r"C:\\Program Files\\NVIDIA GPU Computing Toolkit\\CUDA\\v13.0",
                "CUDA_HOME": r"C:\\Program Files\\NVIDIA GPU Computing Toolkit\\CUDA\\v11.8",
                "VK_ICD_FILENAMES": r"C:\\HostVulkan\\icd.json",
                "VK_LAYER_PATH": r"C:\\HostVulkan\\layers",
                "VK_INSTANCE_LAYERS": r"C:\\HostVulkan\\instance-layers",
                "USERPROFILE": r"C:\\Users\\Person",
                "HOMEDRIVE": "C:",
                "HOMEPATH": "\\Users\\Person",
            }

            environment = build_lichtfeld_environment(executable, profile, inherited)

            self.assertEqual(environment["USERPROFILE"], str(profile))
            self.assertEqual(environment["HOME"], str(profile))
            self.assertEqual(environment["APPDATA"], str(profile / "AppData" / "Roaming"))
            self.assertEqual(environment["LOCALAPPDATA"], str(profile / "AppData" / "Local"))
            self.assertTrue(environment["PATH"].startswith(str(executable.parent) + os.pathsep))
            for name in (
                "PYTHONHOME",
                "PYTHONPATH",
                "PYTHONUSERBASE",
                "VIRTUAL_ENV",
                "CONDA_PREFIX",
                "XPANO_ROOT",
                "XPANO_PYTHON",
                "QT_PLUGIN_PATH",
                "CUDA_PATH",
                "CUDA_PATH_V13_0",
                "CUDA_HOME",
                "VK_ICD_FILENAMES",
                "VK_LAYER_PATH",
                "VK_INSTANCE_LAYERS",
                "HOMEDRIVE",
                "HOMEPATH",
            ):
                self.assertNotIn(name, environment)


class LichtfeldTrainingProgressTests(unittest.TestCase):
    def test_startup_watchdog_uses_inactivity_instead_of_a_fixed_total_deadline(self):
        watchdog = LichtfeldStartupWatchdog(timeout_seconds=120, started_at=0)

        self.assertFalse(watchdog.expired(now=119))
        watchdog.touch("LichtFeld log is growing", now=119)
        self.assertFalse(watchdog.expired(now=238))
        self.assertTrue(watchdog.expired(now=239))
        self.assertIn("LichtFeld log is growing", watchdog.failure_message(now=239))

    def test_training_diagnostic_uses_stable_failure_codes_and_scrubs_user_paths(self):
        config = LichtfeldTrainingConfig(
            executable=Path(r"C:\xPano\runtime\lichtfeld-studio\bin\LichtFeld-Studio.exe"),
            data_path=Path(r"C:\Users\Alice\Project\images"),
            output_path=Path(r"C:\Users\Alice\Project\work\training\runs\job-1"),
            profile_root=Path(r"C:\Users\Alice\AppData\Local\xPano\lichtfeld-studio\0.5.3\profile"),
        )

        payload = build_lichtfeld_diagnostic(
            config,
            "LFS_PROCESS_EXITED:139: Vulkan loader failed at C:\\Users\\Alice\\Project\\images",
            ["[error] C:\\Users\\Alice\\Project\\images\\frame.jpg"],
        )

        self.assertEqual(classify_lichtfeld_failure("CUDA out of memory"), "LFS_GPU_OUT_OF_MEMORY")
        self.assertEqual(payload["failure"]["code"], "LFS_VULKAN_RUNTIME_FAILED")
        self.assertNotIn("Alice", json.dumps(payload))
        self.assertEqual(payload["launch"]["iterations"], 30000)

    def test_parses_authoritative_runtime_state_from_editor_output(self):
        result = {
            "structuredContent": {
                "success": True,
                "output": {
                    "text": "XPANO_TRAIN_STATE:running|253|30000|0.1797|632000"
                },
            }
        }

        state = parse_runtime_state_result(result)

        self.assertTrue(state["is_running"])
        self.assertFalse(state["is_paused"])
        self.assertEqual(state["iteration"], 253)
        self.assertEqual(state["max_iterations"], 30000)
        self.assertEqual(state["num_gaussians"], 632000)

    def test_rejects_empty_training_get_state_style_snapshot(self):
        result = {
            "structuredContent": {
                "success": True,
                "output": {"text": "unrelated output"},
            }
        }

        with self.assertRaisesRegex(RuntimeError, "runtime state"):
            parse_runtime_state_result(result)

    def test_loss_records_do_not_masquerade_as_optimizer_iterations(self):
        tracker = LichtfeldLogTracker(expected_iterations=30_000)

        started = tracker.parse_line(
            "[2026-07-11 13:31:31.184] [info] training_manager.cpp:429 "
            "Training started - 20 iterations planned"
        )
        first = tracker.parse_line(
            "[2026-07-11 13:31:31.798] [trace] training_manager.cpp:764 "
            "Loss updated: 0.271313 (buffer size: 1)"
        )
        second = tracker.parse_line(
            "[2026-07-11 13:31:31.899] [trace] training_manager.cpp:764 "
            "Loss updated: 0.190001 (buffer size: 2)"
        )

        self.assertEqual(started["stage"], "training.initialize")
        self.assertEqual(started["total"], 20)
        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(tracker.current, 0)
        self.assertEqual(tracker.loss, 0.190001)

    def test_mcp_state_reports_the_real_optimizer_iteration(self):
        tracker = LichtfeldLogTracker(expected_iterations=30_000)
        tracker.parse_line(
            "[2026-07-11 13:31:31.184] [info] training_manager.cpp:429 "
            "Training started - 30000 iterations planned"
        )

        event = tracker.update_from_mcp_state({
            "is_running": True,
            "is_paused": False,
            "iteration": 253,
            "max_iterations": 30000,
            "loss": 0.1797,
            "num_gaussians": 632000,
        })

        self.assertEqual(event["current"], 253)
        self.assertEqual(event["total"], 30000)
        self.assertEqual(event["loss"], 0.1797)
        self.assertEqual(event["splatCount"], 632000)
        self.assertAlmostEqual(event["percent"], 253 / 30000 * 100, places=4)
        self.assertEqual(event["trainerState"], "running")
        self.assertTrue(event["heartbeat"])

    def test_checkpoint_updates_iteration_and_gaussian_count(self):
        tracker = LichtfeldLogTracker(expected_iterations=20)

        event = tracker.parse_line(
            "[2026-07-11 13:31:31.875] [info] checkpoint.cpp:255 Checkpoint saved: "
            "D:\\run\\checkpoints\\checkpoint.resume (2571 Gaussians, iter 12)"
        )

        self.assertEqual(event["current"], 12)
        self.assertEqual(event["total"], 20)
        self.assertEqual(event["splatCount"], 2571)
        self.assertEqual(event["stage"], "training.optimize")

    def test_success_and_error_lines_are_terminal_and_observable(self):
        tracker = LichtfeldLogTracker(expected_iterations=20)

        completed = tracker.parse_line(
            "[2026-07-11 13:31:31.885] [info] trainer.cpp:4377 Training completed successfully"
        )
        duplicate = tracker.parse_line(
            "[2026-07-11 13:31:31.917] [info] training_manager.cpp:830 Training completed successfully"
        )
        failed = LichtfeldLogTracker(expected_iterations=20).parse_line(
            "[2026-07-11 13:31:31.885] [error] trainer.cpp:4377 CUDA out of memory"
        )

        self.assertTrue(tracker.completed)
        self.assertEqual(completed["stage"], "training.finalize")
        self.assertEqual(completed["percent"], 100.0)
        self.assertIsNone(duplicate)
        self.assertEqual(failed["stage"], "training.error")
        self.assertIn("CUDA out of memory", failed["message"])

    def test_ignores_unrelated_renderer_noise(self):
        tracker = LichtfeldLogTracker(expected_iterations=20)

        event = tracker.parse_line(
            "[2026-07-11 13:31:31.200] [trace] renderer.cpp:10 uploaded 9000 splats"
        )

        self.assertIsNone(event)


if __name__ == "__main__":
    unittest.main()
