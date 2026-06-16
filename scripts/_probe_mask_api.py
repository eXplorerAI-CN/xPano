import Metashape


def main():
    print("Mask doc:", getattr(Metashape.Mask, "__doc__", ""), flush=True)
    mask = Metashape.Mask()
    print("Mask attrs:", [name for name in dir(mask) if not name.startswith("_")], flush=True)
    print("Chunk mask methods:", [name for name in dir(Metashape.Chunk) if "mask" in name.lower()], flush=True)
    print("Camera attrs:", [name for name in dir(Metashape.Camera) if "mask" in name.lower()], flush=True)


if __name__ == "__main__":
    main()
