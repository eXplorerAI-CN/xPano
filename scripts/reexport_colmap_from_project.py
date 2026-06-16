import argparse
from pathlib import Path

import Metashape

import export_colmap


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--export-dir", required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    doc = Metashape.app.document
    doc.open(str(Path(args.project)))
    export_colmap.run_mixed_export(str(Path(args.export_dir)))


if __name__ == "__main__":
    main()
