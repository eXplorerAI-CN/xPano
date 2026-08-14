from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


DJI_OSMO_360_DLOGM_REC709_PRESET = "builtin:dji-osmo360-dlogm-rec709"
DJI_OSMO_360_DLOGM_REC709_RELATIVE_PATH = Path("luts") / "dji-osmo360-dlogm-rec709-v1.cube"
DJI_OSMO_360_DLOGM_REC709_SHA256 = "b18162854ab47702068410c33afa98a8cb6eef159fc5a04ce0e65fad0fd8947e"


@dataclass(frozen=True)
class LutPaths:
    restoration: Path | None
    style: Path | None


def _style_lut_path(extraction):
    style_path = str(extraction.get("styleLutPath") or "").strip()
    legacy_path = str(extraction.get("colorLutPath") or "").strip()
    if style_path and legacy_path and Path(style_path) != Path(legacy_path):
        raise ValueError("style LUT conflicts with legacy color LUT path")
    value = style_path or legacy_path
    if not value:
        return None
    path = Path(value)
    if path.suffix.lower() != ".cube":
        raise ValueError(f"style LUT must be a .cube file: {path}")
    if not path.is_file():
        raise ValueError(f"style LUT does not exist or is not a file: {path}")
    return path


def resolve_lut_paths(
    app_root,
    extraction,
    track_type,
    source_path,
    expected_sha256=DJI_OSMO_360_DLOGM_REC709_SHA256,
):
    preset = str(extraction.get("colorLutPreset") or "").strip()
    style = _style_lut_path(extraction)
    if not preset:
        return LutPaths(restoration=None, style=style)
    if preset != DJI_OSMO_360_DLOGM_REC709_PRESET:
        raise ValueError(f"unknown color LUT preset: {preset}")
    if track_type != "panoramic_video" or Path(source_path).suffix.lower() != ".osv":
        raise ValueError("bundled DJI color LUT is only valid for .osv panorama tracks")
    path = Path(app_root) / DJI_OSMO_360_DLOGM_REC709_RELATIVE_PATH
    if not path.is_file():
        raise ValueError(f"bundled DJI color LUT is missing: {path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise ValueError("bundled DJI color LUT checksum does not match")
    return LutPaths(restoration=path, style=style)
