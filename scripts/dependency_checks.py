import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from scripts.pipeline_backends import COLMAP_BACKEND, METASHAPE_BACKEND, normalize_backend
from scripts.lichtfeld_densify import (
    locate_densify_plugin,
    locate_densify_python,
    should_use_bundled_densify_runner,
)
from scripts.runtime_paths import app_root, first_existing, internal_root, locate_ffmpeg


@dataclass(frozen=True)
class ExecutableCheck:
    name: str
    requested: str
    required: bool
    ok: bool
    resolved: str = ""
    message: str = ""


def _project_root():
    return app_root()


def _bundled_colmap_candidates(project_root=None):
    roots = [Path(project_root)] if project_root else [_project_root()]
    internal = internal_root()
    if getattr(sys, "frozen", False) and internal not in roots:
        roots.append(internal)
    for root in list(roots):
        bundled_internal = root / "_internal"
        if bundled_internal not in roots:
            roots.append(bundled_internal)
    base_dirs = []
    for root in roots:
        base_dirs.extend([
            root / "tools" / "colmap",
            root / "third_party" / "colmap",
        ])
    candidates = []
    for base in base_dirs:
        candidates.extend(
            [
                base / "bin" / "colmap.exe",
                base / "colmap.exe",
                base / "COLMAP.bat",
                base / "colmap.bat",
            ]
        )
        if base.exists():
            for child in sorted(path for path in base.iterdir() if path.is_dir()):
                candidates.extend(
                    [
                        child / "bin" / "colmap.exe",
                        child / "colmap.exe",
                        child / "COLMAP.bat",
                        child / "colmap.bat",
                    ]
                )
    return candidates


def locate_executable(default_name, env_var=None, path_names=None, candidate_paths=None):
    if env_var:
        explicit = os.environ.get(env_var)
        if explicit and Path(explicit).exists():
            return explicit
    for name in path_names or [default_name]:
        resolved = shutil.which(name)
        if resolved:
            return resolved
    found = first_existing(candidate_paths or [])
    return found or default_name


def locate_colmap(project_root=None):
    explicit = os.environ.get("XPANO_COLMAP")
    if explicit and Path(explicit).exists():
        return explicit
    bundled = first_existing(_bundled_colmap_candidates(project_root=project_root))
    if bundled:
        return bundled
    return locate_executable(
        "colmap",
        path_names=["colmap.exe", "colmap", "COLMAP.bat"],
        candidate_paths=[
            r"C:\Program Files\COLMAP\colmap.exe",
            r"C:\Program Files\COLMAP\COLMAP.bat",
            r"C:\Program Files (x86)\COLMAP\colmap.exe",
            r"D:\Program Files\COLMAP\colmap.exe",
        ],
    )


def locate_lichtfield():
    return locate_executable(
        "lichtfield-studio",
        env_var="XPANO_LICHTFIELD",
        path_names=[
            "lichtfield-studio.exe",
            "lichtfield-studio",
            "LICHT Field Studio.exe",
            "LICHT.exe",
        ],
        candidate_paths=[
            r"C:\Program Files\LICHT Field Studio\lichtfield-studio.exe",
            r"C:\Program Files\LICHT Field Studio\LICHT Field Studio.exe",
            r"C:\Program Files\LICHT\lichtfield-studio.exe",
            r"D:\Program Files\LICHT Field Studio\lichtfield-studio.exe",
        ],
    )


def resolve_executable(executable, default_name):
    executable = (executable or "").strip() or default_name
    if len(executable) >= 2 and executable.startswith('"') and executable.endswith('"'):
        executable = executable[1:-1].strip()
    if Path(executable).is_absolute() or any(sep in executable for sep in ["\\", "/"]):
        if not Path(executable).exists():
            raise FileNotFoundError(executable)
        return str(Path(executable))
    if default_name.lower().startswith("colmap") and executable.lower() in {"colmap", "colmap.exe", "colmap.bat"}:
        bundled = first_existing(_bundled_colmap_candidates())
        if bundled:
            return bundled
    if default_name.lower().startswith("ffmpeg") and executable.lower() in {"ffmpeg", "ffmpeg.exe"}:
        bundled_or_path = locate_ffmpeg()
        if Path(bundled_or_path).exists():
            return bundled_or_path
    resolved = shutil.which(executable)
    if not resolved:
        raise RuntimeError(f"{executable} was not found in PATH")
    return resolved


def check_executable(name, executable, default_name, required=True):
    requested = (executable or "").strip() or default_name
    if not required:
        return ExecutableCheck(name=name, requested=requested, required=False, ok=True, message="Not required")
    try:
        resolved = resolve_executable(requested, default_name)
        return ExecutableCheck(name=name, requested=requested, required=True, ok=True, resolved=resolved)
    except Exception as exc:
        return ExecutableCheck(name=name, requested=requested, required=True, ok=False, message=str(exc))


def check_pipeline_dependencies(
    backend,
    metashape_exe="metashape.exe",
    colmap_exe="colmap",
    lichtfield_exe="lichtfield-studio",
    run_lichtfield=False,
    run_lfs_densify=False,
    lfs_densify_python=None,
    lfs_densify_plugin=None,
):
    backend = normalize_backend(backend)
    plugin_path = Path(lfs_densify_plugin) if lfs_densify_plugin else locate_densify_plugin()
    bundled_densify = should_use_bundled_densify_runner()
    densify_python = (sys.executable if bundled_densify else (lfs_densify_python or locate_densify_python())).strip()
    python_path = Path(densify_python)
    python_exists = True if bundled_densify else (
        python_path.exists()
        if python_path.is_absolute() or any(sep in densify_python for sep in ["\\", "/"])
        else bool(shutil.which(densify_python))
    )
    checks = [
        check_executable("ffmpeg", "ffmpeg", "ffmpeg", required=True),
        check_executable("Metashape", metashape_exe, "metashape.exe", required=backend == METASHAPE_BACKEND),
        check_executable("COLMAP", colmap_exe, "colmap", required=backend == COLMAP_BACKEND),
        check_executable(
            "LICHT Field Studio",
            lichtfield_exe,
            "lichtfield-studio",
            required=backend == COLMAP_BACKEND and run_lichtfield,
        ),
        ExecutableCheck(
            name="LichtFeld densification plugin",
            requested=str(plugin_path),
            required=run_lfs_densify,
            ok=(plugin_path / "densify.py").exists() if run_lfs_densify else True,
            resolved=str(plugin_path) if (plugin_path / "densify.py").exists() else "",
            message="Not required" if not run_lfs_densify else "Run INSTALL_LFS_DENSIFY.bat first",
        ),
        ExecutableCheck(
            name="LichtFeld densification Python",
            requested=densify_python,
            required=run_lfs_densify,
            ok=python_exists if run_lfs_densify else True,
            resolved=(
                f"{sys.executable} --run-lfs-densify-standalone"
                if bundled_densify
                else str(python_path) if python_path.exists() else shutil.which(densify_python) or ""
            ),
            message="Not required" if not run_lfs_densify else "Run INSTALL_LFS_DENSIFY.bat to create .venv-densify",
        ),
    ]
    if run_lfs_densify and python_exists:
        checks.append(check_lfs_densify_imports(None if bundled_densify else densify_python))
        if (plugin_path / "densify.py").exists():
            checks.append(check_lfs_densify_runner(None if bundled_densify else densify_python, plugin_path))
    return checks


def check_lfs_densify_imports(python_exe=None):
    code = "import torch, pycolmap, PIL, scipy, tqdm, einops, rich, open3d; print('ok')"
    command = [python_exe, "-c", code] if python_exe else [sys.executable, "--self-test-lfs-imports"]
    requested = python_exe or f"{sys.executable} --self-test-lfs-imports"
    try:
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["PYTHONNOUSERSITE"] = "1"
        result = subprocess.run(
            command,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        return ExecutableCheck(
            name="LichtFeld densification dependencies",
            requested=requested,
            required=True,
            ok=False,
            message=str(exc),
        )
    return ExecutableCheck(
        name="LichtFeld densification dependencies",
        requested=requested,
        required=True,
        ok=result.returncode == 0,
        resolved=requested if result.returncode == 0 else "",
        message=(result.stderr or result.stdout or "Import check failed").strip() if result.returncode != 0 else "",
    )


def check_lfs_densify_runner(python_exe, plugin_path):
    runner = first_existing([
        internal_root() / "scripts" / "run_lichtfeld_densify_standalone.py",
        _project_root() / "scripts" / "run_lichtfeld_densify_standalone.py",
    ])
    bundled_runner = not python_exe and getattr(sys, "frozen", False)
    if not runner and not bundled_runner:
        return ExecutableCheck(
            name="LichtFeld densification runner",
            requested=str(plugin_path),
            required=True,
            ok=False,
            message="run_lichtfeld_densify_standalone.py was not found",
        )
    command = (
        [sys.executable, "--run-lfs-densify-standalone", "--plugin-dir", str(plugin_path), "--help"]
        if bundled_runner
        else [python_exe, runner, "--plugin-dir", str(plugin_path), "--help"]
    )
    try:
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["PYTHONNOUSERSITE"] = "1"
        result = subprocess.run(
            command,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        return ExecutableCheck(
            name="LichtFeld densification runner",
            requested=str(plugin_path),
            required=True,
            ok=False,
            message=str(exc),
        )
    ok = result.returncode == 0 and "--scene_root" in result.stdout and "--roma_setting" in result.stdout
    return ExecutableCheck(
        name="LichtFeld densification runner",
        requested=str(plugin_path),
        required=True,
        ok=ok,
        resolved=" ".join(command[:2]) if bundled_runner and ok else runner if ok else "",
        message=(result.stderr or result.stdout or "Runner check failed").strip() if not ok else "",
    )


def format_dependency_report(checks):
    lines = []
    for check in checks:
        if not check.required:
            status = "SKIP"
            detail = check.message
        elif check.ok:
            status = "OK"
            detail = check.resolved
        else:
            status = "MISSING"
            detail = check.message
        lines.append(f"{status}: {check.name} ({check.requested}) {detail}".rstrip())
    return "\n".join(lines)


def require_dependency_checks(checks):
    failures = [check for check in checks if check.required and not check.ok]
    if failures:
        raise RuntimeError(format_dependency_report(failures))
    return checks
