import inspect
import Metashape


def main():
    for name in ["matchPhotos", "alignCameras", "optimizeCameras", "addPhotos"]:
        fn = getattr(Metashape.Chunk, name)
        print(f"--- {name} ---", flush=True)
        print(getattr(fn, "__doc__", ""), flush=True)
        try:
            print(inspect.signature(fn), flush=True)
        except Exception as exc:
            print(f"signature unavailable: {exc}", flush=True)


if __name__ == "__main__":
    main()
