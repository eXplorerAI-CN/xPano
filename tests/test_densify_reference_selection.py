import importlib.util
import sys
import types
import unittest
from pathlib import Path

import numpy as np


def _load_selection_module():
    source = (
        Path(__file__).resolve().parents[1]
        / "tools"
        / "lichtfeld-densification-plugin"
        / "core"
        / "selection.py"
    )
    spec = importlib.util.spec_from_file_location("xpano_densify_selection", source)
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get("pycolmap")
    sys.modules["pycolmap"] = types.SimpleNamespace(Reconstruction=object)
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            del sys.modules["pycolmap"]
        else:
            sys.modules["pycolmap"] = previous
    return module


class DensifyReferenceSelectionTests(unittest.TestCase):
    def test_large_reference_selection_is_bounded_and_evenly_distributed(self):
        selection = _load_selection_module()
        poses = np.zeros((6000, 12), dtype=np.float32)

        selected = selection.select_reference_indices(poses, 4500)

        self.assertEqual(len(selected), 4500)
        self.assertEqual(selected[0], 0)
        self.assertEqual(selected[-1], 5999)
        self.assertEqual(selected, sorted(set(selected)))
        gaps = np.diff(np.asarray(selected))
        self.assertLessEqual(int(gaps.max() - gaps.min()), 1)


if __name__ == "__main__":
    unittest.main()
