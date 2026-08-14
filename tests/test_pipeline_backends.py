import unittest

from scripts.pipeline_backends import (
    COLMAP_BACKEND,
    METASHAPE_BACKEND,
    normalize_backend,
    require_implemented_backend,
)


class PipelineBackendTests(unittest.TestCase):
    def test_normalizes_default_backend_to_metashape(self):
        self.assertEqual(normalize_backend(""), METASHAPE_BACKEND)
        self.assertEqual(normalize_backend(None), METASHAPE_BACKEND)

    def test_accepts_colmap_as_planned_backend(self):
        self.assertEqual(normalize_backend("COLMAP"), COLMAP_BACKEND)

    def test_rejects_unknown_backend(self):
        with self.assertRaisesRegex(ValueError, "Unsupported backend"):
            normalize_backend("unknown")

    def test_colmap_backend_is_implemented(self):
        self.assertEqual(require_implemented_backend(COLMAP_BACKEND), COLMAP_BACKEND)


if __name__ == "__main__":
    unittest.main()
