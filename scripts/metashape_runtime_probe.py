import json
from pathlib import Path

try:
    from scripts.metashape_runtime_env import activate_metashape_runtime, metashape_site_packages_from_argv
except ImportError:
    from metashape_runtime_env import activate_metashape_runtime, metashape_site_packages_from_argv

activate_metashape_runtime(metashape_site_packages_from_argv())

import Metashape
import cv2
import numpy


print(
    "XPANO_METASHAPE_RUNTIME_READY:"
    + json.dumps(
        {
            "metashape": getattr(Metashape.app, "version", "unknown"),
            "numpy": numpy.__version__,
            "opencv": cv2.__version__,
            "numpyPath": str(Path(numpy.__file__).resolve()),
            "opencvPath": str(Path(cv2.__file__).resolve()),
        },
        ensure_ascii=True,
    ),
    flush=True,
)
