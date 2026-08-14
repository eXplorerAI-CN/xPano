from __future__ import annotations

import os
import sys
from pathlib import Path


DENSIFY_SITE_PACKAGES_FLAG = "--xpano-site-packages"
_DLL_DIRECTORY_HANDLES = []
_REGISTERED_DLL_DIRECTORIES = set()


def densify_site_packages_from_argv(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if DENSIFY_SITE_PACKAGES_FLAG not in argv:
        return None
    index = argv.index(DENSIFY_SITE_PACKAGES_FLAG)
    if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
        raise RuntimeError(f"{DENSIFY_SITE_PACKAGES_FLAG} requires a directory")
    return argv[index + 1]


def _dll_directories(site_packages):
    fixed = [
        site_packages / "torch" / "lib",
        site_packages / "open3d",
        site_packages / "numpy.libs",
        site_packages / "pycolmap.libs",
    ]
    discovered = sorted(site_packages.glob("*.libs"))
    return [path.resolve() for path in [*fixed, *discovered] if path.is_dir()]


def activate_densify_runtime(site_packages=None):
    requested = site_packages or densify_site_packages_from_argv()
    if not requested:
        requested = os.environ.get("XPANO_DENSIFY_SITE_PACKAGES", "").strip()
    if not requested:
        return None
    site_packages = Path(requested).resolve()
    if not site_packages.is_dir():
        raise RuntimeError(f"xPano densification site-packages directory not found: {site_packages}")

    os.environ["PYTHONNOUSERSITE"] = "1"
    site_text = str(site_packages)
    if site_text in sys.path:
        sys.path.remove(site_text)
    sys.path.insert(0, site_text)

    dll_directories = _dll_directories(site_packages)
    add_dll_directory = getattr(os, "add_dll_directory", None)
    for directory in dll_directories:
        key = str(directory).casefold()
        if key in _REGISTERED_DLL_DIRECTORIES:
            continue
        if add_dll_directory:
            _DLL_DIRECTORY_HANDLES.append(add_dll_directory(str(directory)))
        _REGISTERED_DLL_DIRECTORIES.add(key)

    if dll_directories:
        existing = os.environ.get("PATH", "")
        os.environ["PATH"] = os.pathsep.join([*(str(path) for path in dll_directories), existing])
    return site_packages


def densify_runtime_cli_args(site_packages):
    return [DENSIFY_SITE_PACKAGES_FLAG, str(Path(site_packages).resolve())] if site_packages else []
