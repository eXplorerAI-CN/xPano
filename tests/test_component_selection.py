import unittest
from types import SimpleNamespace

from scripts.component_selection import (
    ComponentActivationError,
    activated_component,
    inspect_components,
    resolve_component_key,
)


class ActiveScopedCamera:
    def __init__(self, chunk, key, component_keys):
        self.chunk = chunk
        self.key = key
        self.component_keys = {str(value) for value in component_keys}

    @property
    def transform(self):
        active = getattr(self.chunk.component, "key", None)
        return object() if str(active) in self.component_keys else None


class ActiveScopedChunk:
    def __init__(self, component_camera_counts, tie_point_counts, active_key):
        self.components = [SimpleNamespace(key=key, label=f"Component {key}") for key in component_camera_counts]
        self._component = next(item for item in self.components if str(item.key) == str(active_key))
        self.tie_point_counts = {str(key): count for key, count in tie_point_counts.items()}
        self.cameras = []
        camera_key = 1
        for component_key, count in component_camera_counts.items():
            for _ in range(count):
                self.cameras.append(ActiveScopedCamera(self, camera_key, [component_key]))
                camera_key += 1
        self.cameras.append(ActiveScopedCamera(self, camera_key, []))
        self.fail_on_key = None

    @property
    def component(self):
        return self._component

    @component.setter
    def component(self, value):
        if str(getattr(value, "key", value)) == str(self.fail_on_key):
            raise RuntimeError("activation failed")
        self._component = value

    @property
    def tie_points(self):
        count = self.tie_point_counts[str(self.component.key)]
        return SimpleNamespace(points=list(range(count)))


class ComponentSelectionTests(unittest.TestCase):
    def test_inventory_activates_every_component_and_restores_the_original(self):
        chunk = ActiveScopedChunk({11: 3, 22: 2}, {11: 9, 22: 20}, active_key=22)

        inspection = inspect_components(chunk)

        self.assertEqual([item.component_key for item in inspection.components], ["11", "22"])
        self.assertEqual([item.aligned_camera_count for item in inspection.components], [3, 2])
        self.assertEqual([item.tie_point_count for item in inspection.components], [9, 20])
        self.assertEqual(inspection.aligned_camera_count, 5)
        self.assertEqual(inspection.unaligned_camera_count, 1)
        self.assertEqual(inspection.default_component_key, "11")
        self.assertEqual(str(chunk.component.key), "22")

    def test_tie_points_break_equal_camera_count_ties(self):
        chunk = ActiveScopedChunk({1: 2, 2: 2}, {1: 4, 2: 8}, active_key=1)

        inspection = inspect_components(chunk)

        self.assertEqual([item.component_key for item in inspection.components], ["2", "1"])
        self.assertEqual(inspection.default_component_key, "2")

    def test_strict_selection_rejects_a_component_removed_after_inspection(self):
        chunk = ActiveScopedChunk({1: 2, 2: 1}, {1: 4, 2: 2}, active_key=1)
        inspection = inspect_components(chunk)

        with self.assertRaisesRegex(ValueError, "does not exist"):
            resolve_component_key(inspection, "missing", strict=True)

        self.assertEqual(resolve_component_key(inspection, "missing", strict=False), "1")

    def test_activation_restores_the_original_component_after_an_error(self):
        chunk = ActiveScopedChunk({1: 2, 2: 1}, {1: 4, 2: 2}, active_key=1)

        with self.assertRaisesRegex(RuntimeError, "export failed"):
            with activated_component(chunk, "2"):
                self.assertEqual(str(chunk.component.key), "2")
                raise RuntimeError("export failed")

        self.assertEqual(str(chunk.component.key), "1")

    def test_inventory_fails_when_a_component_cannot_be_activated(self):
        chunk = ActiveScopedChunk({1: 2, 2: 1}, {1: 4, 2: 2}, active_key=1)
        chunk.fail_on_key = "2"

        with self.assertRaises(ComponentActivationError):
            inspect_components(chunk)

        self.assertEqual(str(chunk.component.key), "1")

    def test_missing_native_component_api_is_visible_as_incomplete_inventory(self):
        chunk = SimpleNamespace(
            cameras=[SimpleNamespace(key=1, transform=object()), SimpleNamespace(key=2, transform=None)],
            component=None,
            tie_points=SimpleNamespace(points=[1, 2, 3]),
        )

        inspection = inspect_components(chunk)

        self.assertFalse(inspection.inventory_complete)
        self.assertEqual(inspection.default_component_key, "__all__")
        self.assertEqual(inspection.aligned_camera_count, 1)
        self.assertTrue(inspection.warnings)


if __name__ == "__main__":
    unittest.main()
