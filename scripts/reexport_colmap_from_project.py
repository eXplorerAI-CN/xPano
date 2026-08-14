import argparse
import json
from pathlib import Path

try:
    from scripts.metashape_runtime_env import (
        METASHAPE_SITE_PACKAGES_FLAG,
        activate_metashape_runtime,
        metashape_site_packages_from_argv,
    )
except ImportError:
    from metashape_runtime_env import (
        METASHAPE_SITE_PACKAGES_FLAG,
        activate_metashape_runtime,
        metashape_site_packages_from_argv,
    )

activate_metashape_runtime(metashape_site_packages_from_argv())

import Metashape

import export_colmap

try:
    from scripts.component_selection import activated_component, inspect_components, resolve_component_key
except ImportError:
    from component_selection import activated_component, inspect_components, resolve_component_key


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(METASHAPE_SITE_PACKAGES_FLAG, help=argparse.SUPPRESS)
    parser.add_argument("--project", required=True)
    parser.add_argument("--export-dir", required=True)
    parser.add_argument("--reuse-images-dir")
    parser.add_argument("--image-cache-path")
    parser.add_argument("--image-cache-output")
    parser.add_argument("--component-key")
    return parser.parse_args()


def main():
    args = parse_args()
    doc = Metashape.app.document
    doc.open(str(Path(args.project)))
    chunk = doc.chunk
    inspection = inspect_components(chunk)
    try:
        selected_component_key = resolve_component_key(
            inspection,
            args.component_key,
            strict=args.component_key is not None,
        )
    except ValueError as exc:
        raise RuntimeError("PSX 中的 Component 已变化，请重新读取后再导出") from exc
    print("PIPELINE_EVENT:" + json.dumps({
        "phase": "export",
        "stage": "metashape.component.validate",
        "percent": 87,
        "phasePercent": 5,
        "message": "正在确认导出 Component",
    }, ensure_ascii=True), flush=True)
    selected = next(item for item in inspection.components if item.component_key == selected_component_key)
    with activated_component(chunk, selected_component_key):
        export_colmap.run_mixed_export(
            str(Path(args.export_dir)),
            show_dialog=False,
            reuse_images_dir=args.reuse_images_dir,
            image_cache_path=args.image_cache_path,
            image_cache_output=args.image_cache_output,
            selected_component_key=selected_component_key,
        )
    warnings = list(inspection.warnings)
    if inspection.aligned_camera_count < len(chunk.cameras):
        warnings.append("Some cameras were not aligned; the completed partial result remains exportable.")
    report = {
        "schemaVersion": 2,
        "processSucceeded": True,
        "state": "complete",
        "projectPath": str(Path(args.project)),
        "totalCameras": len(chunk.cameras),
        "alignedCameras": inspection.aligned_camera_count,
        "unalignedCameras": inspection.unaligned_camera_count,
        "alignmentRate": (
            inspection.aligned_camera_count / len(chunk.cameras) * 100.0
            if chunk.cameras else 0.0
        ),
        "inventoryComplete": inspection.inventory_complete,
        "components": [item.as_dict() for item in inspection.components],
        "selectedComponentKey": selected_component_key,
        "selectedComponentAlignedCameras": selected.aligned_camera_count,
        "warnings": warnings,
    }
    (Path(args.export_dir) / "xpano_alignment_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
