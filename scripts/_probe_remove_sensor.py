import sys
from pathlib import Path

import Metashape


def main():
    paths = [str(Path(p)) for p in sys.argv[1:]]
    doc = Metashape.app.document
    chunk = doc.addChunk()
    chunk.addPhotos(paths)
    extra = chunk.addSensor()
    extra.label = "unused"
    print(f"before sensors={len(chunk.sensors)} has_remove={hasattr(chunk, 'remove')}", flush=True)
    for expr in ["chunk.remove(extra)", "chunk.remove([extra])"]:
        try:
            eval(expr)
            print(f"{expr} ok sensors={len(chunk.sensors)}", flush=True)
            break
        except Exception as exc:
            print(f"{expr} failed: {type(exc).__name__}: {exc}", flush=True)


if __name__ == "__main__":
    main()
