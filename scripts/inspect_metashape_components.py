import argparse
import json
import os
from pathlib import Path

import Metashape

try:
    from scripts.component_selection import inspect_components
except ImportError:
    from component_selection import inspect_components


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def write_json_atomic(path, payload):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except Exception:
            pass


def main():
    args = parse_args()
    project_path = Path(args.project)
    document = Metashape.app.document
    document.open(str(project_path))
    chunk = document.chunk
    if chunk is None:
        raise RuntimeError("Metashape project has no active chunk")

    payload = inspect_components(chunk).as_dict()
    payload["projectPath"] = str(project_path)
    write_json_atomic(args.output, payload)


if __name__ == "__main__":
    main()
