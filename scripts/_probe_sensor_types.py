import Metashape


def main():
    for name in dir(Metashape.Sensor.Type):
        if not name.startswith("_"):
            try:
                print(name, getattr(Metashape.Sensor.Type, name), flush=True)
            except Exception:
                pass


if __name__ == "__main__":
    main()
