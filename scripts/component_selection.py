"""Metashape Component inspection and activation helpers."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass


DEFAULT_COMPONENT_KEY = "__all__"


class ComponentActivationError(RuntimeError):
    pass


def component_key(component):
    key = getattr(component, "key", component)
    return str(key) if key is not None else DEFAULT_COMPONENT_KEY


def camera_key(camera):
    return int(getattr(camera, "key", id(camera)))


def available_components(chunk):
    try:
        return list(getattr(chunk, "components", None) or ())
    except TypeError:
        return []


def _component_by_key(chunk, requested_key):
    requested = str(requested_key)
    return next(
        (component for component in available_components(chunk) if component_key(component) == requested),
        None,
    )


@contextmanager
def activated_component(chunk, requested_key):
    requested = str(requested_key)
    if requested == DEFAULT_COMPONENT_KEY:
        yield getattr(chunk, "component", None)
        return

    target = _component_by_key(chunk, requested)
    if target is None:
        raise ComponentActivationError(f"Metashape Component {requested!r} does not exist")

    original = getattr(chunk, "component", None)
    original_key = component_key(original)
    if original is target or original_key == requested:
        yield target
        return

    try:
        chunk.component = target
    except Exception as exc:
        raise ComponentActivationError(f"Failed to activate Metashape Component {requested!r}: {exc}") from exc
    if component_key(getattr(chunk, "component", None)) != requested:
        raise ComponentActivationError(f"Metashape did not activate Component {requested!r}")

    try:
        yield target
    finally:
        try:
            chunk.component = original
        except Exception as exc:
            raise ComponentActivationError(
                f"Failed to restore original Metashape Component {original_key!r}: {exc}"
            ) from exc
        if component_key(getattr(chunk, "component", None)) != original_key:
            raise ComponentActivationError(
                f"Metashape did not restore original Component {original_key!r}"
            )


def _tie_point_count(chunk):
    tie_points = getattr(chunk, "tie_points", None)
    points = getattr(tie_points, "points", None)
    try:
        return len(points) if points is not None else 0
    except TypeError:
        return 0


@dataclass(frozen=True)
class ComponentSummary:
    component_key: str
    label: str
    aligned_camera_keys: tuple[int, ...]
    tie_point_count: int
    is_initially_active: bool = False

    @property
    def aligned_camera_count(self):
        return len(self.aligned_camera_keys)

    def as_dict(self):
        return {
            "componentKey": self.component_key,
            "label": self.label,
            "alignedCameraCount": self.aligned_camera_count,
            "totalCameraCount": self.aligned_camera_count,
            "tiePointCount": self.tie_point_count,
            "isInitiallyActive": self.is_initially_active,
        }


@dataclass(frozen=True)
class ComponentInspection:
    total_camera_count: int
    components: tuple[ComponentSummary, ...]
    inventory_complete: bool
    warnings: tuple[str, ...] = ()

    @property
    def aligned_camera_keys(self):
        return frozenset(
            key
            for component in self.components
            for key in component.aligned_camera_keys
        )

    @property
    def aligned_camera_count(self):
        return len(self.aligned_camera_keys)

    @property
    def unaligned_camera_count(self):
        return max(0, self.total_camera_count - self.aligned_camera_count)

    @property
    def default_component_key(self):
        return self.components[0].component_key if self.components else None

    def as_dict(self):
        return {
            "schemaVersion": 2,
            "inventoryComplete": self.inventory_complete,
            "totalCameras": self.total_camera_count,
            "alignedCameras": self.aligned_camera_count,
            "unalignedCameras": self.unaligned_camera_count,
            "defaultComponentKey": self.default_component_key,
            "components": [component.as_dict() for component in self.components],
            "warnings": list(self.warnings),
        }


def _summary_for_active_component(chunk, key, label, initial_key):
    aligned_keys = tuple(
        camera_key(camera)
        for camera in chunk.cameras
        if getattr(camera, "transform", None) is not None
    )
    return ComponentSummary(
        component_key=str(key),
        label=str(label or f"Component {key}"),
        aligned_camera_keys=aligned_keys,
        tie_point_count=_tie_point_count(chunk),
        is_initially_active=str(key) == initial_key,
    )


def inspect_components(chunk):
    components = available_components(chunk)
    initial_key = component_key(getattr(chunk, "component", None))
    warnings = []

    if not components:
        warnings.append(
            "This Metashape version does not expose the complete Component list; only the active result is available."
        )
        summary = _summary_for_active_component(
            chunk,
            DEFAULT_COMPONENT_KEY,
            "Active Component",
            initial_key,
        )
        return ComponentInspection(
            total_camera_count=len(chunk.cameras),
            components=(summary,),
            inventory_complete=False,
            warnings=tuple(warnings),
        )

    summaries = []
    for component in components:
        key = component_key(component)
        with activated_component(chunk, key):
            summaries.append(_summary_for_active_component(
                chunk,
                key,
                getattr(component, "label", ""),
                initial_key,
            ))

    summaries.sort(key=lambda item: (
        -item.aligned_camera_count,
        -item.tie_point_count,
        item.component_key,
    ))
    if len(summaries) > 1:
        warnings.append(
            "Multiple Metashape Components found; only the selected Component will be exported."
        )
    return ComponentInspection(
        total_camera_count=len(chunk.cameras),
        components=tuple(summaries),
        inventory_complete=True,
        warnings=tuple(warnings),
    )


def resolve_component_key(inspection, requested=None, *, strict=False):
    keys = {component.component_key for component in inspection.components}
    if requested is not None and str(requested) in keys:
        return str(requested)
    if requested is not None and strict:
        raise ValueError(f"Requested Metashape Component {requested!r} does not exist")
    if inspection.default_component_key is None:
        raise ValueError("Metashape project has no aligned Component")
    return inspection.default_component_key
