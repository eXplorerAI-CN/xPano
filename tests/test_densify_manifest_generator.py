import unittest

from scripts.generate_densify_manifest import select_compatible_wheel, select_simple_index_wheel


class DensifyManifestGeneratorTests(unittest.TestCase):
    def test_prefers_native_cp312_wheel_over_pure_python_fallback(self):
        files = [
            {
                "filename": "demo-1.0-py3-none-any.whl",
                "url": "https://example/pure.whl",
                "size": 10,
                "digests": {"sha256": "1" * 64},
                "packagetype": "bdist_wheel",
            },
            {
                "filename": "demo-1.0-cp312-cp312-win_amd64.whl",
                "url": "https://example/native.whl",
                "size": 20,
                "digests": {"sha256": "2" * 64},
                "packagetype": "bdist_wheel",
            },
        ]

        selected = select_compatible_wheel(files, "cp312")

        self.assertEqual(selected["filename"], "demo-1.0-cp312-cp312-win_amd64.whl")

    def test_rejects_wheel_for_different_python_abi(self):
        files = [
            {
                "filename": "demo-1.0-cp311-cp311-win_amd64.whl",
                "url": "https://example/wrong.whl",
                "size": 20,
                "digests": {"sha256": "2" * 64},
                "packagetype": "bdist_wheel",
            }
        ]

        self.assertIsNone(select_compatible_wheel(files, "cp312"))

    def test_selects_exact_pytorch_profile_wheel_and_hash_from_simple_index(self):
        html = """
        <a href=\"https://download.pytorch.org/whl/cu128/torch-2.8.0%2Bcu128-cp312-cp312-win_amd64.whl#sha256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\">torch</a>
        <a href=\"https://download.pytorch.org/whl/cu128/torch-2.8.0%2Bcu128-cp311-cp311-win_amd64.whl#sha256=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\">torch</a>
        """

        selected = select_simple_index_wheel(html, "torch", "2.8.0+cu128", "cp312")

        self.assertEqual(selected["filename"], "torch-2.8.0+cu128-cp312-cp312-win_amd64.whl")
        self.assertEqual(selected["sha256"], "a" * 64)


if __name__ == "__main__":
    unittest.main()
