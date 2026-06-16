import argparse
import shutil
from pathlib import Path

import Metashape

import export_colmap
import metashape_pipeline


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--export-dir", required=True)
    return parser.parse_args()


def first_aligned_transform(chunk):
    for camera in chunk.cameras:
        if camera.transform:
            return camera.transform
    raise RuntimeError("Project has no aligned camera transform to reuse for the export probe")


def copy_matrix(matrix):
    return Metashape.Matrix([[matrix[row, col] for col in range(4)] for row in range(4)])


def main():
    args = parse_args()
    export_dir = Path(args.export_dir)
    if export_dir.exists():
        shutil.rmtree(export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)

    doc = Metashape.app.document
    doc.open(str(Path(args.project)))
    chunk = doc.chunk
    if chunk is None:
        raise RuntimeError("Project did not open a valid chunk")

    transform = first_aligned_transform(chunk)
    manifest = metashape_pipeline.load_manifest(Path(args.manifest))
    added = []
    for track in manifest.get("tracks", []):
        if track.get("track_type") in {"standard_photos", "aerial_photos"}:
            added.extend(metashape_pipeline.import_photo_track(chunk, track))
    for camera in added:
        camera.transform = copy_matrix(transform)
        camera.enabled = True
    metashape_pipeline.prune_unused_sensors(chunk)

    print(
        f"mixed_export_probe cameras={len(chunk.cameras)} added_frame_cameras={len(added)} "
        f"sensors={len(metashape_pipeline.used_sensors(chunk))}",
        flush=True,
    )
    export_colmap.run_mixed_export(str(export_dir))


if __name__ == "__main__":
    main()
