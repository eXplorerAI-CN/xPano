import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import piexif
from PIL import Image

from scripts.xpano_extract import (
    PreparedLutChain,
    _copy_photo_exif,
    _emit_generated_pair_previews,
    _emit_generated_single_previews,
    _ffmpeg_input_args,
    _hardware_acceleration_candidates,
    prepare_lut_chain,
    _run_ffmpeg,
    _run_ffmpeg_with_hardware_fallback,
    _video_filter,
    extract_frames,
    extract_single_video_frames,
)


class FakeProgressProcess:
    def __init__(self, lines, return_code=0):
        self.stdout = lines
        self.return_code = return_code

    def wait(self):
        return self.return_code

    def poll(self):
        return self.return_code


class FakeRunningProcess:
    def __init__(self, out_root, base_name, pattern="pair"):
        self.stdout = []
        self.out_root = Path(out_root)
        self.base_name = base_name
        self.pattern = pattern
        self.poll_count = 0

    def poll(self):
        self.poll_count += 1
        if self.poll_count == 2:
            if self.pattern == "single":
                (self.out_root / f"{self.base_name}_00001.jpg").write_bytes(b"frame")
            else:
                (self.out_root / f"{self.base_name}_L_00001.jpg").write_bytes(b"left")
                (self.out_root / f"{self.base_name}_R_00001.jpg").write_bytes(b"right")
        if self.poll_count >= 3:
            return 0
        return None

    def wait(self):
        return 0


class XpanoExtractProgressTests(unittest.TestCase):
    def test_style_image_output_preserves_camera_exif_with_normalized_orientation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.jpg"
            styled = root / "styled.jpg"
            Image.new("RGB", (64, 48), (32, 64, 96)).save(source, "JPEG")
            Image.new("RGB", (48, 64), (96, 64, 32)).save(styled, "JPEG")
            piexif.insert(
                piexif.dump({
                    "0th": {
                        piexif.ImageIFD.Make: b"CameraCo",
                        piexif.ImageIFD.Model: b"Model X",
                        piexif.ImageIFD.Orientation: 6,
                    },
                    "Exif": {piexif.ExifIFD.FocalLength: (35, 1)},
                    "GPS": {},
                    "1st": {},
                    "thumbnail": None,
                }),
                str(source),
            )

            _copy_photo_exif(source, styled)

            metadata = piexif.load(str(styled))
            self.assertEqual(metadata["0th"][piexif.ImageIFD.Make], b"CameraCo")
            self.assertEqual(metadata["0th"][piexif.ImageIFD.Model], b"Model X")
            self.assertEqual(metadata["Exif"][piexif.ExifIFD.FocalLength], (35, 1))
            self.assertEqual(metadata["0th"][piexif.ImageIFD.Orientation], 1)

    def test_video_filter_preserves_no_lut_command_and_stabilizes_lut_output(self):
        self.assertEqual(_video_filter(1.0, None), "fps=1.0")
        chain = PreparedLutChain(Path("safe"), Path("safe/restore.cube"), Path("safe/style.cube"))
        self.assertEqual(
            _video_filter(1.0, chain),
            "fps=1.0,lut3d=file=restore.cube:interp=tetrahedral,lut3d=file=style.cube:interp=tetrahedral,format=yuvj420p",
        )

    def test_lut_chain_is_copied_to_safe_temporary_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            restore = Path(tmp) / "restore, source.cube"
            style = Path(tmp) / "style, source.cube"
            restore.write_text("LUT_3D_SIZE 2\n", encoding="utf-8")
            style.write_text("LUT_3D_SIZE 2\n", encoding="utf-8")

            with patch("scripts.xpano_extract._validate_prepared_luts") as validate:
                with prepare_lut_chain(restore, style) as prepared:
                    self.assertEqual(prepared.restoration.name, "restore.cube")
                    self.assertEqual(prepared.style.name, "style.cube")
                    self.assertEqual(prepared.restoration.read_bytes(), restore.read_bytes())
                    self.assertEqual(prepared.style.read_bytes(), style.read_bytes())
                    validate.assert_called_once_with(prepared)
                    prepared_parent = prepared.directory

            self.assertFalse(prepared_parent.exists())

    def test_no_lut_chain_skips_snapshot_and_validation(self):
        with patch("scripts.xpano_extract._validate_prepared_luts") as validate:
            with prepare_lut_chain() as prepared:
                self.assertIsNone(prepared)

        validate.assert_not_called()

    def test_color_lut_rejects_non_cube_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "restore.png"
            source.write_bytes(b"not a LUT")

            with self.assertRaisesRegex(ValueError, "must be a .cube file"):
                with prepare_lut_chain(style_lut_path=source):
                    pass

    def test_invalid_color_lut_fails_before_frame_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "clip.mp4"
            lut = root / "invalid.cube"
            video.write_bytes(b"video")
            lut.write_text("invalid", encoding="utf-8")

            with patch(
                "scripts.xpano_extract._validate_prepared_luts",
                side_effect=ValueError("invalid color LUT"),
            ), patch("scripts.xpano_extract._run_ffmpeg") as run:
                with self.assertRaisesRegex(ValueError, r"invalid color LUT"):
                    extract_single_video_frames(video, root / "frames", 1.0, style_lut_path=lut)

            run.assert_not_called()

    def test_panorama_applies_the_same_lut_to_both_streams_with_safe_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "camera.osv"
            restore = root / "restore.cube"
            style = root / "style.cube"
            out_root = root / "frames"
            video.write_bytes(b"video")
            restore.write_text("LUT_3D_SIZE 2\n", encoding="utf-8")
            style.write_text("LUT_3D_SIZE 2\n", encoding="utf-8")
            captured = {}

            def fake_run(cmd, *_args, **kwargs):
                captured["cmd"] = cmd
                captured["cwd"] = kwargs.get("cwd")
                out_root.mkdir(parents=True, exist_ok=True)
                (out_root / "camera_L_00001.jpg").write_bytes(b"left")
                (out_root / "camera_R_00001.jpg").write_bytes(b"right")

            with patch("scripts.xpano_extract._validate_prepared_luts"), patch(
                "scripts.xpano_extract._run_ffmpeg", side_effect=fake_run
            ):
                extract_frames(video, out_root, 1.0, restoration_lut_path=restore, style_lut_path=style)

            filters = [
                captured["cmd"][index + 1]
                for index, value in enumerate(captured["cmd"])
                if value == "-vf"
            ]
            self.assertEqual(filters, [
                "fps=1.0,lut3d=file=restore.cube:interp=tetrahedral,lut3d=file=style.cube:interp=tetrahedral,format=yuvj420p",
                "fps=1.0,lut3d=file=restore.cube:interp=tetrahedral,lut3d=file=style.cube:interp=tetrahedral,format=yuvj420p",
            ])
            self.assertNotIn(str(restore), captured["cmd"])
            self.assertNotIn(str(style), captured["cmd"])
            self.assertEqual(Path(captured["cwd"]).name.startswith("xpano-lut-"), True)

    def test_no_lut_single_video_keeps_the_existing_filter_and_working_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "clip.mp4"
            out_root = root / "frames"
            video.write_bytes(b"video")
            captured = {}

            def fake_run(cmd, *_args, **kwargs):
                captured["cmd"] = cmd
                captured["cwd"] = kwargs.get("cwd")
                out_root.mkdir(parents=True, exist_ok=True)
                (out_root / "clip_00001.jpg").write_bytes(b"frame")

            with patch("scripts.xpano_extract._run_ffmpeg", side_effect=fake_run):
                extract_single_video_frames(video, out_root, 1.0)

            filter_index = captured["cmd"].index("-vf")
            self.assertEqual(captured["cmd"][filter_index + 1], "fps=1.0")
            self.assertIsNone(captured["cwd"])

    def test_hardware_acceleration_prefers_cuda_then_d3d11va_on_windows(self):
        candidates = _hardware_acceleration_candidates(platform="win32", mode="auto")

        self.assertEqual([name for name, _args in candidates], ["cuda", "d3d11va", "software"])
        self.assertEqual(candidates[0][1], ["-hwaccel", "cuda"])
        self.assertEqual(candidates[1][1], ["-hwaccel", "d3d11va"])
        self.assertEqual(candidates[2][1], [])

    def test_hardware_acceleration_is_attached_to_each_ffmpeg_input(self):
        args = _ffmpeg_input_args(
            Path("right.insv"),
            ["-ss", "2.000000", "-t", "5.000000"],
            ["-hwaccel", "cuda"],
        )

        self.assertEqual(
            args,
            ["-hwaccel", "cuda", "-ss", "2.000000", "-t", "5.000000", "-i", "right.insv"],
        )

    def test_hardware_acceleration_falls_back_and_reports_selected_decoder(self):
        attempts = []
        logs = []

        def command_factory(name, input_args):
            attempts.append((name, input_args))
            return ["ffmpeg", *input_args, "-i", "camera.osv"]

        with patch(
            "scripts.xpano_extract._run_ffmpeg",
            side_effect=[subprocess.CalledProcessError(1, ["ffmpeg"]), None],
        ):
            selected = _run_ffmpeg_with_hardware_fallback(
                command_factory,
                Path("camera.osv"),
                fps=1.0,
                max_frames=1,
                log_cb=logs.append,
                candidates=[("cuda", ["-hwaccel", "cuda"]), ("software", [])],
            )

        self.assertEqual(selected, "software")
        self.assertEqual([name for name, _args in attempts], ["cuda", "software"])
        self.assertTrue(any("CUDA" in line and "回退" in line for line in logs))
        self.assertTrue(any("software" in line.lower() for line in logs))

    def test_hardware_fallback_keeps_the_prepared_lut_working_directory(self):
        prepared_directory = Path("safe-lut-directory")

        with patch(
            "scripts.xpano_extract._run_ffmpeg",
            side_effect=[subprocess.CalledProcessError(1, ["ffmpeg"]), None],
        ) as run:
            _run_ffmpeg_with_hardware_fallback(
                lambda _name, args: ["ffmpeg", *args, "-i", "camera.osv"],
                Path("camera.osv"),
                fps=1.0,
                max_frames=1,
                candidates=[("cuda", ["-hwaccel", "cuda"]), ("software", [])],
                cwd=prepared_directory,
            )

        self.assertEqual(
            [call.kwargs["cwd"] for call in run.call_args_list],
            [prepared_directory, prepared_directory],
        )

    def test_hardware_fallback_does_not_retry_non_decoder_failures(self):
        attempts = []

        def command_factory(name, input_args):
            attempts.append(name)
            return ["ffmpeg", *input_args, "-i", "missing.osv"]

        failure = subprocess.CalledProcessError(
            1,
            ["ffmpeg"],
            output="Error opening input file missing.osv: No such file or directory",
        )
        with patch("scripts.xpano_extract._run_ffmpeg", side_effect=failure):
            with self.assertRaises(subprocess.CalledProcessError):
                _run_ffmpeg_with_hardware_fallback(
                    command_factory,
                    Path("missing.osv"),
                    fps=1.0,
                    max_frames=1,
                    candidates=[("cuda", ["-hwaccel", "cuda"]), ("software", [])],
                )

        self.assertEqual(attempts, ["cuda"])

    def test_emits_preview_for_newly_generated_frame_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "clip_L_00001.jpg").write_bytes(b"left")
            (root / "clip_R_00001.jpg").write_bytes(b"right")
            previews = []

            last = _emit_generated_pair_previews(root, "clip", 0, lambda left, right: previews.append((left, right)))

            self.assertEqual(last, 1)
            self.assertEqual(previews, [(str(root / "clip_L_00001.jpg"), str(root / "clip_R_00001.jpg"))])

    def test_emits_preview_for_newly_generated_single_video_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "clip_00001.jpg").write_bytes(b"frame")
            previews = []

            last = _emit_generated_single_previews(root, "clip", 0, lambda left, right: previews.append((left, right)))

            self.assertEqual(last, 1)
            self.assertEqual(previews, [(str(root / "clip_00001.jpg"), str(root / "clip_00001.jpg"))])

    def test_streams_ffmpeg_progress_and_logs(self):
        progress_events = []
        log_events = []
        process = FakeProgressProcess(
            [
                "frame=1\n",
                "out_time_ms=1000000\n",
                "progress=continue\n",
                "frame=5\n",
                "progress=continue\n",
                "progress=end\n",
            ]
        )

        with patch("scripts.xpano_extract.subprocess.Popen", return_value=process):
            _run_ffmpeg(
                ["ffmpeg", "-progress", "pipe:1"],
                Path("camera.osv"),
                fps=1.0,
                max_frames=5,
                progress_cb=lambda cur, total: progress_events.append((cur, total)),
                log_cb=log_events.append,
            )

        self.assertIn((1, 5), progress_events)
        self.assertIn((5, 5), progress_events)
        self.assertTrue(any("expected frames: 5" in item for item in log_events))

    def test_failed_ffmpeg_includes_progress_tail(self):
        process = FakeProgressProcess(["bad input\n", "progress=end\n"], return_code=1)

        with patch("scripts.xpano_extract.subprocess.Popen", return_value=process):
            with self.assertRaises(subprocess.CalledProcessError) as raised:
                _run_ffmpeg(
                    ["ffmpeg", "-progress", "pipe:1"],
                    Path("broken.osv"),
                    fps=1.0,
                    max_frames=5,
                )

        self.assertIn("bad input", raised.exception.output)

    def test_polls_generated_jpegs_when_ffmpeg_output_is_quiet(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_root = Path(tmp)
            process = FakeRunningProcess(out_root, "camera")
            progress_events = []
            preview_events = []

            with patch("scripts.xpano_extract.subprocess.Popen", return_value=process):
                _run_ffmpeg(
                    ["ffmpeg", "-progress", "pipe:1"],
                    Path("camera.osv"),
                    fps=1.0,
                    max_frames=5,
                    out_root=out_root,
                    base_name="camera",
                    progress_cb=lambda cur, total: progress_events.append((cur, total)),
                    preview_cb=lambda left, right: preview_events.append((left, right)),
                )

        self.assertIn((1, 5), progress_events)
        self.assertEqual(
            preview_events,
            [(str(out_root / "camera_L_00001.jpg"), str(out_root / "camera_R_00001.jpg"))],
        )

    def test_polls_generated_single_jpegs_when_ffmpeg_output_is_quiet(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_root = Path(tmp)
            process = FakeRunningProcess(out_root, "clip", pattern="single")
            progress_events = []
            preview_events = []

            with patch("scripts.xpano_extract.subprocess.Popen", return_value=process):
                _run_ffmpeg(
                    ["ffmpeg", "-progress", "pipe:1"],
                    Path("clip.mp4"),
                    fps=1.0,
                    max_frames=5,
                    out_root=out_root,
                    base_name="clip",
                    preview_mode="single",
                    progress_cb=lambda cur, total: progress_events.append((cur, total)),
                    preview_cb=lambda left, right: preview_events.append((left, right)),
                )

        self.assertIn((1, 5), progress_events)
        self.assertEqual(preview_events, [(str(out_root / "clip_00001.jpg"), str(out_root / "clip_00001.jpg"))])

    def test_single_video_frames_are_flat_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_root = Path(tmp)
            video = out_root / "clip.mp4"
            video.write_bytes(b"video")

            def fake_run_ffmpeg(*_args, **_kwargs):
                (out_root / "track_002_00001.jpg").write_bytes(b"frame")
                (out_root / "track_002_00002.jpg").write_bytes(b"frame")

            with patch("scripts.xpano_extract._run_ffmpeg", side_effect=fake_run_ffmpeg):
                frames = extract_single_video_frames(
                    video,
                    out_root,
                    fps=1.0,
                    model_prefix="track_002",
                )

            self.assertEqual([path.name for path in frames], ["track_002_frame_00001.jpg", "track_002_frame_00002.jpg"])
            self.assertTrue((out_root / "track_002_frame_00001.jpg").is_file())
            self.assertFalse((out_root / "track_002_frame_00001").exists())


if __name__ == "__main__":
    unittest.main()
