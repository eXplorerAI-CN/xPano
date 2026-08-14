import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path

from scripts.capture_regression_baseline import capture_baseline


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"


class ProjectContractTests(unittest.TestCase):
    def test_schema_documents_and_examples_define_the_same_contract_versions(self):
        project_schema = json.loads((SCHEMAS / "xpano_project_v3.schema.json").read_text(encoding="utf-8"))
        event_schema = json.loads((SCHEMAS / "xpano_job_event_v1.schema.json").read_text(encoding="utf-8"))
        plan_schema = json.loads((SCHEMAS / "xpano_execution_plan_v1.schema.json").read_text(encoding="utf-8"))
        project = json.loads((SCHEMAS / "fixtures" / "xpano_project_v3.example.json").read_text(encoding="utf-8"))
        event = json.loads((SCHEMAS / "fixtures" / "xpano_job_event_v1.example.json").read_text(encoding="utf-8"))
        plan = json.loads((SCHEMAS / "fixtures" / "xpano_execution_plan_v1.example.json").read_text(encoding="utf-8"))

        self.assertEqual(project_schema["properties"]["schemaVersion"]["const"], project["schemaVersion"])
        self.assertEqual(event_schema["properties"]["schemaVersion"]["const"], event["schemaVersion"])
        self.assertEqual(plan_schema["properties"]["schemaVersion"]["const"], plan["schemaVersion"])
        self.assertEqual(project["geometry"]["transform"]["worldFromCanonical"], [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])
        self.assertEqual(plan["nodes"][1]["dependsOn"], [plan["nodes"][0]["stageId"]])
        self.assertTrue(event["trackId"])

    def test_project_example_keeps_generated_artifacts_relative(self):
        project = json.loads((SCHEMAS / "fixtures" / "xpano_project_v3.example.json").read_text(encoding="utf-8"))
        track = project["tracks"][0]
        item = track["items"][0]
        generated_paths = [
            item["left"],
            item["right"],
            item["thumbnailLeft"],
            item["thumbnailRight"],
            project["reconstruction"]["projectPath"],
            project["geometry"]["variants"][0]["canonicalPath"],
        ]
        self.assertTrue(all(not Path(path).is_absolute() for path in generated_paths))
        self.assertTrue(all(":" not in path[:3] for path in generated_paths))

    def test_capture_rejects_a_non_twenty_frame_project(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_manifest(root, frame_count=19)
            self._write_colmap_model(root, image_count=38, point_count=123)

            with self.assertRaisesRegex(ValueError, "expected 20"):
                capture_baseline(root, "metashape-20", root / "baseline.json")

    def test_capture_records_binary_counts_and_hashes_for_twenty_frames(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "baseline.json"
            self._write_manifest(root, frame_count=20)
            self._write_colmap_model(root, image_count=40, point_count=321)
            (root / "xpano_run_summary.json").write_text('{"backend":"colmap"}', encoding="utf-8")

            baseline = capture_baseline(root, "colmap-20", output)

            self.assertEqual(baseline["frameCount"], 20)
            self.assertEqual(baseline["backend"], "colmap")
            self.assertEqual(baseline["model"]["imageCount"], 40)
            self.assertEqual(baseline["model"]["pointCount"], 321)
            self.assertEqual(baseline["artifacts"]["xpano_manifest.json"], self._sha256(root / "xpano_manifest.json"))
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), baseline)

    @staticmethod
    def _write_manifest(root: Path, frame_count: int):
        frames = [
            {"frame_id": f"frame_{index:05d}", "left": f"work/frames/{index:05d}_left.jpg", "right": f"work/frames/{index:05d}_right.jpg"}
            for index in range(frame_count)
        ]
        manifest = {"schema_version": 1, "workflow": "xpano_multi_track", "tracks": [{"track_type": "panorama_video", "frames": frames}]}
        (root / "xpano_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    @staticmethod
    def _write_colmap_model(root: Path, image_count: int, point_count: int):
        model = root / "sparse" / "0"
        model.mkdir(parents=True)
        (model / "cameras.bin").write_bytes(struct.pack("<Q", 2))
        (model / "images.bin").write_bytes(struct.pack("<Q", image_count))
        (model / "points3D.bin").write_bytes(struct.pack("<Q", point_count))

    @staticmethod
    def _sha256(path: Path):
        return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    unittest.main()
