import Metashape


def main():
    calib = Metashape.Calibration()
    print("Calibration attrs:", [name for name in dir(calib) if not name.startswith("_")], flush=True)
    for owner in [Metashape.Calibration, Metashape.Sensor]:
        print(owner, [name for name in dir(owner) if "Type" in name or "type" in name], flush=True)


if __name__ == "__main__":
    main()
