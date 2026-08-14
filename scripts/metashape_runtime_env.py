from __future__ import annotations

import os
import sys
from pathlib import Path


_DLL_DIRECTORY_HANDLES = []
_REGISTERED_DLL_DIRECTORIES = set()
METASHAPE_SITE_PACKAGES_FLAG = "--xpano-site-packages"


def metashape_site_packages_from_argv(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    prefix = METASHAPE_SITE_PACKAGES_FLAG + "="
    for index, argument in enumerate(arguments):
        if argument.startswith(prefix):
            value = argument[len(prefix):].strip()
            if not value:
                raise RuntimeError(f"{METASHAPE_SITE_PACKAGES_FLAG} requires a directory")
            return value
        if argument == METASHAPE_SITE_PACKAGES_FLAG:
            if index + 1 >= len(arguments) or not arguments[index + 1].strip():
                raise RuntimeError(f"{METASHAPE_SITE_PACKAGES_FLAG} requires a directory")
            return arguments[index + 1].strip()
    return None


def metashape_runtime_cli_args(site_packages):
    if not site_packages:
        return []
    runtime_site = Path(site_packages).resolve()
    if not runtime_site.is_dir():
        raise RuntimeError(f"xPano Metashape runtime directory does not exist: {runtime_site}")
    return [METASHAPE_SITE_PACKAGES_FLAG, str(runtime_site)]

def _path_is_within(path, root):
    try:
        Path(path).resolve().relative_to(root)
        return True
    except ValueError:
        return False


def build_metashape_process_env(application_root, site_packages=None, base_env=None):
    env = dict(os.environ if base_env is None else base_env)
    env["PYTHONNOUSERSITE"] = "1"
    for name in ["QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "PYTHONHOME"]:
        env.pop(name, None)

    root = Path(application_root)
    if root.exists():
        root = root.resolve()

        def outside_application(item):
            if not item:
                return False
            try:
                return not _path_is_within(item, root)
            except Exception:
                return False

        for name in ["PYTHONPATH", "PATH"]:
            parts = [part for part in env.get(name, "").split(os.pathsep) if outside_application(part)]
            if parts:
                env[name] = os.pathsep.join(parts)
            else:
                env.pop(name, None)

    dependency_path = Path(site_packages).resolve() if site_packages else None
    if dependency_path and dependency_path.is_dir():
        dependency = str(dependency_path)
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = os.pathsep.join([dependency] + ([existing_pythonpath] if existing_pythonpath else []))

        dll_paths = [dependency_path / "numpy.libs", dependency_path / "cv2", dependency_path]
        existing_path = [part for part in env.get("PATH", "").split(os.pathsep) if part]
        env["PATH"] = os.pathsep.join([str(path) for path in dll_paths if path.is_dir()] + existing_path)
    return env


def activate_metashape_runtime(site_packages=None):
    os.environ["PYTHONNOUSERSITE"] = "1"
    requested = site_packages or os.environ.get("XPANO_METASHAPE_SITE_PACKAGES", "").strip()
    if not requested:
        return None
    runtime_site = Path(requested).resolve()
    if not runtime_site.is_dir():
        raise RuntimeError(f"xPano Metashape runtime directory does not exist: {runtime_site}")

    runtime_text = str(runtime_site)
    sys.path[:] = [entry for entry in sys.path if entry != runtime_text]
    sys.path.insert(0, runtime_text)

    dll_directories = [runtime_site / "numpy.libs", runtime_site / "cv2"]
    existing_path = [part for part in os.environ.get("PATH", "").split(os.pathsep) if part]
    os.environ["PATH"] = os.pathsep.join([str(path) for path in dll_directories if path.is_dir()] + existing_path)
    add_dll_directory = getattr(os, "add_dll_directory", None)
    if add_dll_directory:
        for directory in dll_directories:
            if not directory.is_dir():
                continue
            identity = str(directory.resolve()).casefold()
            if identity in _REGISTERED_DLL_DIRECTORIES:
                continue
            _DLL_DIRECTORY_HANDLES.append(add_dll_directory(str(directory)))
            _REGISTERED_DLL_DIRECTORIES.add(identity)
    return runtime_site
