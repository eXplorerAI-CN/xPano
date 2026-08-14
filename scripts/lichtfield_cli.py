from dataclasses import dataclass, field
from pathlib import Path
import subprocess


@dataclass(frozen=True)
class LichtfieldStudioConfig:
    executable: str = "lichtfield-studio"
    input_colmap: Path = None
    image_dir: Path = None
    output_dir: Path = None
    point_count: int = 0
    bilateral_grid: int = 0
    extra_args: list = field(default_factory=list)


def _append_path_arg(command, name, value):
    if value is not None:
        command.extend([name, str(value)])


def _append_int_arg(command, name, value):
    if value:
        if value < 0:
            raise ValueError(f"{name} must be greater than or equal to 0")
        command.extend([name, str(value)])


def build_lichtfield_command(config):
    if not config.input_colmap:
        raise ValueError("LICHT Field Studio input_colmap is required")
    if not config.image_dir:
        raise ValueError("LICHT Field Studio image_dir is required")
    if not config.output_dir:
        raise ValueError("LICHT Field Studio output_dir is required")

    command = [config.executable]
    _append_path_arg(command, "--input-colmap", config.input_colmap)
    _append_path_arg(command, "--image-dir", config.image_dir)
    _append_path_arg(command, "--output", config.output_dir)
    _append_int_arg(command, "--point-count", config.point_count)
    _append_int_arg(command, "--bilateral-grid", config.bilateral_grid)
    command.extend(str(item) for item in config.extra_args)
    return command


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
    )
    output_lines = []
    for raw_line in proc.stdout:
        line = raw_line.rstrip()
        if line:
            output_lines.append(line)
            log_cb(line)
    rc = proc.wait()
    return subprocess.CompletedProcess(command, rc, stdout="\n".join(output_lines), stderr="")


def run_lichtfield_command(config, progress_cb=None, log_cb=None, runner=None):
    progress_cb = progress_cb or (lambda value: None)
    log_cb = log_cb or (lambda text: None)

    command = build_lichtfield_command(config)
    log_cb(f"LICHT Field Studio: {' '.join(str(part) for part in command)}")
    progress_cb(80)
    config.output_dir.parent.mkdir(parents=True, exist_ok=True)
    if runner is None:
        result = _run_command_streaming(command, config.output_dir.parent, log_cb)
    else:
        result = runner(
            command,
            cwd=str(config.output_dir.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for stream in [getattr(result, "stdout", ""), getattr(result, "stderr", "")]:
            for line in (stream or "").splitlines():
                if line:
                    log_cb(line)
    if getattr(result, "returncode", 0) != 0:
        raise RuntimeError(f"LICHT Field Studio failed with return code {result.returncode}")
    progress_cb(100)
    return command
