import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.xpano_extract import _run_ffmpeg


class FakeProgressProcess:
    def __init__(self, lines, return_code=0):
        self.stdout = lines
        self.return_code = return_code

    def wait(self):
        return self.return_code

    def poll(self):
        return self.return_code


class FakeRunningProcess:
    def __init__(self, out_root, base_name):
        self.stdout = []
        self.out_root = Path(out_root)
        self.base_name = base_name
        self.poll_count = 0

    def poll(self):
        self.poll_count += 1
        if self.poll_count == 2:
            (self.out_root / f"{self.base_name}_L_00001.jpg").write_bytes(b"left")
            (self.out_root / f"{self.base_name}_R_00001.jpg").write_bytes(b"right")
        if self.poll_count >= 3:
            return 0
        return None

    def wait(self):
        return 0


class XpanoExtractProgressTests(unittest.TestCase):
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

            with patch("scripts.xpano_extract.subprocess.Popen", return_value=process):
                _run_ffmpeg(
                    ["ffmpeg", "-progress", "pipe:1"],
                    Path("camera.osv"),
                    fps=1.0,
                    max_frames=5,
                    out_root=out_root,
                    base_name="camera",
                    progress_cb=lambda cur, total: progress_events.append((cur, total)),
                )

        self.assertIn((1, 5), progress_events)


if __name__ == "__main__":
    unittest.main()
