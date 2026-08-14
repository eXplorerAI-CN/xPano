import tempfile
import unittest
from pathlib import Path

from scripts.export_image_cache import (
    build_image_signature,
    load_image_cache,
    output_record,
    reuse_cached_outputs,
    source_record,
    write_image_cache,
)
from unittest.mock import patch


class ExportImageCacheTests(unittest.TestCase):
    def test_signature_changes_with_source_sensor_strategy_or_contract(self):
        source = {"path": "a.jpg", "size": 12, "sha256": "abc"}
        sensor = {"f": 100.0, "k1": 0.01}
        strategy = {"type": "Cubemap", "width": 2048}

        baseline = build_image_signature(source, sensor, strategy, "contract-1")

        self.assertNotEqual(baseline, build_image_signature({**source, "sha256": "def"}, sensor, strategy, "contract-1"))
        self.assertNotEqual(baseline, build_image_signature(source, {**sensor, "f": 101.0}, strategy, "contract-1"))
        self.assertNotEqual(baseline, build_image_signature(source, sensor, {**strategy, "width": 1024}, "contract-1"))
        self.assertNotEqual(baseline, build_image_signature(source, sensor, strategy, "contract-2"))

    def test_matching_entry_reuses_verified_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            previous = root / "previous"
            staged = root / "staged"
            previous.mkdir()
            image = previous / "face.jpg"
            image.write_bytes(b"jpeg bytes")
            record = output_record(image, previous)
            cache = {"cameras": {"camera-1": {"signature": "sig", "outputs": [record]}}}

            reused = reuse_cached_outputs(cache, "camera-1", "sig", previous, staged)

            self.assertTrue(reused)
            self.assertEqual((staged / "face.jpg").read_bytes(), b"jpeg bytes")

    def test_unchanged_output_metadata_avoids_rehashing_cached_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            previous = root / "previous"
            previous.mkdir()
            image = previous / "face.jpg"
            image.write_bytes(b"jpeg bytes")
            record = output_record(image, previous)
            cache = {"cameras": {"camera-1": {"signature": "sig", "outputs": [record]}}}

            with patch("scripts.export_image_cache._sha256_file", side_effect=AssertionError("unexpected hash")):
                self.assertTrue(reuse_cached_outputs(cache, "camera-1", "sig", previous, root / "staged"))

    def test_source_record_reuses_verified_fingerprint_when_metadata_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.jpg"
            source.write_bytes(b"source bytes")
            cached = source_record(source)

            with patch("scripts.export_image_cache._sha256_file", side_effect=AssertionError("unexpected hash")):
                self.assertEqual(source_record(source, cached=cached), cached)

    def test_signature_mismatch_does_not_stage_old_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            previous = root / "previous"
            staged = root / "staged"
            previous.mkdir()
            image = previous / "face.jpg"
            image.write_bytes(b"jpeg bytes")
            cache = {"cameras": {"camera-1": {"signature": "old", "outputs": [output_record(image, previous)]}}}

            self.assertFalse(reuse_cached_outputs(cache, "camera-1", "new", previous, staged))
            self.assertFalse((staged / "face.jpg").exists())

    def test_corrupt_cached_output_is_rejected_without_partial_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            previous = root / "previous"
            staged = root / "staged"
            previous.mkdir()
            first = previous / "first.jpg"
            second = previous / "second.jpg"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            records = [output_record(first, previous), output_record(second, previous)]
            second.write_bytes(b"corrupt")
            cache = {"cameras": {"camera-1": {"signature": "sig", "outputs": records}}}

            self.assertFalse(reuse_cached_outputs(cache, "camera-1", "sig", previous, staged))
            self.assertFalse(staged.exists())

    def test_cache_write_is_atomic_and_schema_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "work" / "export_image_cache.json"
            cameras = {"camera-1": {"signature": "sig", "outputs": []}}

            write_image_cache(path, cameras)

            self.assertEqual(load_image_cache(path)["cameras"], cameras)
            path.write_text('{"schemaVersion": 999, "cameras": {}}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "schema"):
                load_image_cache(path)

    def test_unsafe_output_name_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            previous = root / "previous"
            previous.mkdir()
            cache = {
                "cameras": {
                    "camera-1": {
                        "signature": "sig",
                        "outputs": [{"name": "../outside.jpg", "size": 1, "sha256": "x"}],
                    }
                }
            }

            self.assertFalse(reuse_cached_outputs(cache, "camera-1", "sig", previous, root / "staged"))


if __name__ == "__main__":
    unittest.main()
