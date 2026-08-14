from dataclasses import dataclass
from pathlib import Path
import os
import subprocess
import sys

from scripts.runtime_paths import app_root, candidate_roots, first_existing


@dataclass(frozen=True)
class LichtfeldDensifyConfig:
    python_exe: str = sys.executable
    site_packages: Path = None
    plugin_dir: Path = None
    scene_root: Path = None
    images_subdir: str = "images"
    out_name: str = "points3D_dense.ply"
    roma_setting: str = "fast"
    num_refs: float = 0.75
    nns_per_ref: int = 3
    matches_per_ref: int = 10000
    certainty_thresh: float = 0.20
    reproj_thresh: float = 1.5
    sampson_thresh: float = 5.0
    min_parallax_deg: float = 0.5
    max_points: int = 0
    seed: int = 0
    no_filter: bool = False
    publish_to_points3d: bool = True


def _project_root():
    return app_root()


def locate_densify_python(project_root=None):
    roots = [Path(project_root)] if project_root else candidate_roots()
    candidates = []
    for root in roots:
        active = root / "tools" / "lfs-densify-runtime" / "active_python.txt"
        if active.exists():
            try:
                selected = Path(active.read_text(encoding="ascii").strip())
                if selected.exists():
                    candidates.append(selected)
            except Exception:
                pass
        candidates.extend([
            root / ".venv-densify" / "Scripts" / "python.exe",
            root / ".venv-densify" / "bin" / "python",
        ])
    env_python = None
    try:
        import os
        env_value = os.environ.get("XPANO_LFS_DENSIFY_PYTHON", "").strip()
        env_python = Path(env_value) if env_value else None
    except Exception:
        env_python = None
    if env_python and env_python.exists():
        candidates.insert(0, env_python)
    fallback_root = roots[0]
    candidates.extend([
        fallback_root / ".venv-densify" / "Scripts" / "python.exe",
        fallback_root / ".venv-densify" / "bin" / "python",
    ]
    )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return str(candidates[0])


def locate_bundled_densify_site_packages(project_root=None):
    roots = [Path(project_root)] if project_root else candidate_roots()
    candidates = []
    for root in roots:
        candidates.extend([
            root / ".venv-densify" / "Lib" / "site-packages",
            root / ".venv-densify" / "lib" / "site-packages",
        ])
        venv_lib = root / ".venv-densify" / "lib"
        if venv_lib.exists():
            candidates.extend(sorted(venv_lib.glob("python*/site-packages")))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else Path(".venv-densify") / "Lib" / "site-packages"


def has_bundled_densify_runtime(project_root=None):
    site_packages = locate_bundled_densify_site_packages(project_root)
    return site_packages.exists()


def should_use_bundled_densify_runner(project_root=None):
    return getattr(sys, "frozen", False) and has_bundled_densify_runtime(project_root)


def locate_densify_plugin(project_root=None):
    roots = [Path(project_root)] if project_root else candidate_roots()
    candidates = []
    for root in roots:
        candidates.extend([
            root / "tools" / "lichtfeld-densification-plugin",
            root / "third_party" / "lichtfeld-densification-plugin",
        ])
    for candidate in candidates:
        if (candidate / "densify.py").exists():
            return candidate
    return candidates[0]


def _append_arg(command, name, value):
    command.extend([name, str(value)])


def _append_positive_int_arg(command, name, value):
    value = int(value)
    if value < 0:
        raise ValueError(f"{name} must be greater than or equal to 0")
    command.extend([name, str(value)])


def build_densify_command(config):
    if not config.scene_root:
        raise ValueError("LichtFeld densification scene_root is required")
    plugin_dir = Path(config.plugin_dir) if config.plugin_dir else locate_densify_plugin()
    if should_use_bundled_densify_runner():
        command = [sys.executable, "--run-lfs-densify-standalone", "--plugin-dir", str(plugin_dir)]
    else:
        script = first_existing([
            *(root / "scripts" / "run_lichtfeld_densify_standalone.py" for root in candidate_roots()),
        ])
        if not script:
            raise FileNotFoundError("run_lichtfeld_densify_standalone.py")
        command = [config.python_exe or locate_densify_python(), str(script), "--plugin-dir", str(plugin_dir)]
    if config.site_packages:
        command.extend(["--xpano-site-packages", str(Path(config.site_packages).resolve())])
    _append_arg(command, "--scene_root", Path(config.scene_root))
    _append_arg(command, "--images_subdir", config.images_subdir)
    _append_arg(command, "--out_name", config.out_name)
    _append_arg(command, "--roma_setting", config.roma_setting)
    _append_arg(command, "--num_refs", _plugin_num_refs(config.num_refs))
    _append_positive_int_arg(command, "--nns_per_ref", config.nns_per_ref)
    _append_positive_int_arg(command, "--matches_per_ref", config.matches_per_ref)
    _append_arg(command, "--certainty_thresh", config.certainty_thresh)
    _append_arg(command, "--reproj_thresh", config.reproj_thresh)
    _append_arg(command, "--sampson_thresh", config.sampson_thresh)
    _append_arg(command, "--min_parallax_deg", config.min_parallax_deg)
    _append_positive_int_arg(command, "--max_points", config.max_points)
    _append_arg(command, "--seed", config.seed)
    if config.no_filter:
        command.append("--no_filter")
    return command


def _plugin_num_refs(value):
    value = float(value)
    if value == 1.0:
        return 1.01
    return value


def _popen_creationflags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run_command_streaming(command, cwd, log_cb):
    proc = subprocess.Popen(
        command,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_popen_creationflags(),
        env={**os.environ, "PYTHONNOUSERSITE": "1"},
    )
    output_lines = []
    for raw_line in proc.stdout:
        line = raw_line.rstrip()
        if line:
            output_lines.append(line)
            log_cb(line)
    rc = proc.wait()
    return subprocess.CompletedProcess(command, rc, stdout="\n".join(output_lines), stderr="")


def run_densify_command(config, progress_cb=None, log_cb=None, runner=None):
    progress_cb = progress_cb or (lambda value: None)
    log_cb = log_cb or (lambda text: None)

    command = build_densify_command(config)
    plugin_dir = Path(config.plugin_dir) if config.plugin_dir else locate_densify_plugin()
    log_cb(f"LichtFeld densification: {' '.join(str(part) for part in command)}")
    if runner is None:
        result = _run_command_streaming(command, plugin_dir, log_cb)
    else:
        result = runner(
            command,
            cwd=str(plugin_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONNOUSERSITE": "1"},
        )
        for stream in [getattr(result, "stdout", ""), getattr(result, "stderr", "")]:
            for line in (stream or "").splitlines():
                if line:
                    log_cb(line)
    if getattr(result, "returncode", 0) != 0:
        raise RuntimeError(f"LichtFeld densification failed with return code {result.returncode}")
    progress_cb(100)
    return command
