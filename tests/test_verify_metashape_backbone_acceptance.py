import json
import struct
import tempfile
import unittest
from pathlib import Path

from scripts.verify_metashape_backbone_acceptance import verify_backbone_acceptance


MIXED_BACKBONE_STAGES = [
    "metashape.pano.match",
    "metashape.pano.align",
    "metashape.pano.release",
    "metashape.pano.optimize",
    "metashape.frame.import",
    "metashape.frame.match",
    "metashape.frame.align",
    "metashape.all.optimize",
    "output.validate",
]


def write_count_header(path, count):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<Q", count))


def write_backbone_log(path, stages=MIXED_BACKBONE_STAGES, match_calls=2, error_line=None):
    lines = []
    for stage in stages:
        if stage == "output.validate":
            lines.append("PIPELINE_EVENT:" + json.dumps({"stage": "export.images"}))
        lines.append("PIPELINE_EVENT:" + json.dumps({"stage": stage}))
        if stage in {"metashape.pano.match", "metashape.frame.match"}:
            lines.append("MatchPhotos: test policy")
    if match_calls != 2:
        lines = [line for line in lines if not line.startswith("MatchPhotos:")]
        lines.extend("MatchPhotos: test policy" for _ in range(match_calls))
    if error_line:
        lines.append(error_line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_success_artifacts(root, summary_overrides=None):
    project = root / "xpano.psx"
    project.write_bytes(b"PSX")
    export_dir = root / "export"
    images = export_dir / "images"
    images.mkdir(parents=True)
    (images / "cube_front_00001_left.jpg").write_bytes(b"cube")
    (images / "cube_back_00001_right.jpg").write_bytes(b"cube")
    (images / "frame_00001_phone.jpg").write_bytes(b"frame")
    write_count_header(export_dir / "sparse" / "0" / "cameras.bin", 2)
    write_count_header(export_dir / "sparse" / "0" / "images.bin", 3)
    write_count_header(export_dir / "sparse" / "0" / "points3D.bin", 7)
    summary = {
        "project": str(project),
        "cameras": 4,
        "aligned": 4,
        "panorama_cameras": 2,
        "panorama_aligned": 2,
        "frame_cameras": 2,
        "frame_aligned": 2,
        "groups": 2,
        "sensors": 3,
        "alignment_mode": "backbone",
    }
    summary.update(summary_overrides or {})
    (export_dir / "xpano_alignment_summary.txt").write_text(
        "\n".join(
            ["xPano Metashape alignment summary"]
            + [f"{key}={value}" for key, value in summary.items()]
        )
        + "\n",
        encoding="utf-8",
    )
    return project, export_dir


class VerifyMetashapeBackboneAcceptanceTests(unittest.TestCase):
    def verify_success_fixture(self, root, **overrides):
        project, export_dir = write_success_artifacts(root, overrides.pop("summary", None))
        log_path = root / "metashape.stdout.log"
        write_backbone_log(log_path, **overrides.pop("log", {}))
        expected = {
            "log_path": log_path,
            "project_path": project,
            "export_dir": export_dir,
            "expect_cameras": 4,
            "expect_aligned": 4,
            "expect_panorama_cameras": 2,
            "expect_panorama_aligned": 2,
            "expect_frame_cameras": 2,
            "expect_frame_aligned": 2,
            "expect_sensors": 3,
            "expect_cube_images": 2,
            "expect_frame_images": 1,
            "expect_colmap_images": 3,
            "expect_colmap_cameras": 2,
            "expect_colmap_points": 7,
        }
        expected.update(overrides)
        return verify_backbone_acceptance(**expected)

    def test_accepts_complete_incremental_mixed_backbone_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.verify_success_fixture(Path(tmp))

        self.assertEqual(result["match_calls"], 2)
        self.assertEqual(result["stages"], list(MIXED_BACKBONE_STAGES))
        self.assertEqual(result["alignment_summary"]["aligned"], 4)
        self.assertEqual(result["alignment_summary"]["panorama_aligned"], 2)
        self.assertEqual(result["alignment_summary"]["frame_aligned"], 2)
        self.assertEqual(result["output"]["colmap_points"], 7)

    def test_rejects_missing_incremental_match_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "exactly two native MatchPhotos"):
                self.verify_success_fixture(Path(tmp), log={"match_calls": 1})

    def test_rejects_native_failure_marker_even_when_export_files_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "native failure marker"):
                self.verify_success_fixture(Path(tmp), log={"error_line": "Assertion failed in Metashape"})

    def test_rejects_out_of_order_staged_alignment(self):
        stages = [
            "metashape.pano.match",
            "metashape.pano.align",
            "metashape.pano.release",
            "metashape.pano.optimize",
            "metashape.all.optimize",
            "metashape.frame.import",
            "metashape.frame.match",
            "metashape.frame.align",
            "output.validate",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "invalid Backbone stage order"):
                self.verify_success_fixture(Path(tmp), log={"stages": stages})

    def test_rejects_alignment_summary_count_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "aligned: expected 3, got 4"):
                self.verify_success_fixture(Path(tmp), expect_aligned=3)

    def test_rejects_panorama_quality_loss_even_when_aggregate_expectation_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "panorama_aligned: expected 2, got 1"):
                self.verify_success_fixture(
                    Path(tmp),
                    summary={
                        "cameras": 5,
                        "aligned": 4,
                        "panorama_cameras": 2,
                        "panorama_aligned": 1,
                        "frame_cameras": 3,
                        "frame_aligned": 3,
                    },
                    expect_cameras=5,
                    expect_aligned=4,
                    expect_frame_cameras=3,
                    expect_frame_aligned=3,
                )

    def test_rejects_summary_from_a_different_nonempty_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unrelated_project = root / "unrelated.psx"
            unrelated_project.write_bytes(b"OTHER PSX")
            with self.assertRaisesRegex(RuntimeError, "summary project does not match"):
                self.verify_success_fixture(root, project_path=unrelated_project)

    def test_rejects_authoritative_acceptance_without_expected_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, export_dir = write_success_artifacts(root)
            log_path = root / "metashape.stdout.log"
            write_backbone_log(log_path)
            with self.assertRaisesRegex(RuntimeError, "expected counts are required"):
                verify_backbone_acceptance(log_path, project, export_dir)


if __name__ == "__main__":
    unittest.main()
