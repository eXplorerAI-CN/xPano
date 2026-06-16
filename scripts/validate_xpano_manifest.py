import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.xpano_tracks import load_manifest, validate_manifest


def parse_args():
    parser = argparse.ArgumentParser(description="Validate an xPano multi-track manifest")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--skip-file-checks", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    manifest_path = Path(args.manifest)
    manifest = validate_manifest(load_manifest(manifest_path), check_files=not args.skip_file_checks)
    summary = {
        "manifest": str(manifest_path.resolve()),
        "track_count": len(manifest["tracks"]),
        "tracks": [
            {
                "track_id": track["track_id"],
                "track_type": track["track_type"],
                "frames": len(track.get("frames", [])),
                "photos": len(track.get("photos", [])),
                "photo_sensors": len(track.get("photo_sensors", [])),
            }
            for track in manifest["tracks"]
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
