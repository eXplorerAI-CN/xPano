import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import piexif

from scripts.runtime_paths import locate_ffmpeg, locate_ffprobe


SUPPORTED_EXTENSIONS = {".insv", ".osv", ".mp4"}


def _hardware_acceleration_candidates(platform=None, mode=None):
    platform = platform or sys.platform
    mode = (mode or os.environ.get("XPANO_HWACCEL", "auto")).strip().lower()
    software = [("software", [])]
    if mode in {"none", "off", "software", "cpu", "0"}:
        return software
    if mode == "cuda":
        return [("cuda", ["-hwaccel", "cuda"]), *software]
    if mode in {"d3d11", "d3d11va"}:
        return [("d3d11va", ["-hwaccel", "d3d11va"]), *software]
    candidates = [("cuda", ["-hwaccel", "cuda"])]
    if platform == "win32":
        candidates.append(("d3d11va", ["-hwaccel", "d3d11va"]))
    candidates.extend(software)
    return candidates


def _ffmpeg_input_args(input_path, time_args, hardware_args):
    return [*hardware_args, *time_args, "-i", str(input_path)]


def _is_non_decoder_failure(error):
    output = str(getattr(error, "output", "") or "").lower()
    return any(
        marker in output
        for marker in (
            "error opening input",
            "no such file or directory",
            "permission denied",
            "no space left on device",
            "invalid data found when processing input",
            "could not open file",
        )
    )


def _run_ffmpeg_with_hardware_fallback(
    command_factory,
    input_path,
    fps,
    max_frames,
    candidates=None,
    cleanup_cb=None,
    log_cb=None,
    **run_kwargs,
):
    candidates = candidates or _hardware_acceleration_candidates()
    for index, (name, hardware_args) in enumerate(candidates):
        if cleanup_cb:
            cleanup_cb()
        if log_cb:
            log_cb(f"ffmpeg decoder attempt: {name}")
        try:
            _run_ffmpeg(
                command_factory(name, hardware_args),
                input_path,
                fps,
                max_frames,
                log_cb=log_cb,
                **run_kwargs,
            )
        except subprocess.CalledProcessError as error:
            if name == "software" or _is_non_decoder_failure(error) or index + 1 >= len(candidates):
                raise
            next_name = candidates[index + 1][0]
            if log_cb:
                log_cb(f"{name.upper()} 硬件解码不可用，回退到 {next_name}")
            continue
        if log_cb:
            log_cb(f"ffmpeg decoder selected: {name}")
        return name
    raise RuntimeError("no FFmpeg decoder candidate was attempted")


def _remove_generated_files(out_root, patterns):
    for pattern in patterns:
        for path in Path(out_root).glob(pattern):
            if path.is_file():
                path.unlink()


def _apply_exif(img_path: Path, model: str, make: str):
    try:
        exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
        exif_dict["0th"][piexif.ImageIFD.Make] = make.encode()
        exif_dict["0th"][piexif.ImageIFD.Model] = model.encode()
        piexif.insert(piexif.dump(exif_dict), str(img_path))
    except Exception:
        pass


def _copy_photo_exif(source: Path, destination: Path):
    try:
        exif = piexif.load(str(source))
        exif["0th"][piexif.ImageIFD.Orientation] = 1
        piexif.insert(piexif.dump(exif), str(destination))
    except Exception:
        # NOTE: Unsupported or malformed source metadata must not discard a valid styled image.
        return


def _frame_preview(left_path: Path, right_path: Path, preview_cb):
    if preview_cb is None:
        return
    preview_cb(str(left_path), str(right_path))


def _append_frame_limit(cmd, max_frames):
    if max_frames and max_frames > 0:
        cmd.extend(["-frames:v", str(max_frames)])


def _input_time_args(start_time_seconds=0.0, end_time_seconds=0.0):
    start = float(start_time_seconds or 0.0)
    end = float(end_time_seconds or 0.0)
    args = []
    if start > 0:
        args.extend(["-ss", f"{start:.6f}"])
    if end > 0 and end > start:
        args.extend(["-t", f"{end - start:.6f}"])
    return args


def _probe_duration_seconds(input_path: Path, log_cb=None):
    try:
        result = subprocess.run(
            [
                locate_ffprobe(),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(input_path),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        duration = float(result.stdout.strip())
        return duration if duration > 0 else None
    except Exception as exc:
        if log_cb:
            log_cb(f"ffprobe duration unavailable for {input_path.name}: {exc}")
        return None


def _expected_frame_count(input_path: Path, fps, max_frames, log_cb=None, start_time_seconds=0.0, end_time_seconds=0.0):
    if max_frames and max_frames > 0:
        return max_frames
    duration = _probe_duration_seconds(input_path, log_cb=log_cb)
    if not duration:
        return None
    start = min(float(start_time_seconds or 0.0), duration)
    end = float(end_time_seconds or 0.0)
    effective_end = min(end, duration) if end > 0 else duration
    return max(1, int(max(0.0, effective_end - start) * fps + 0.999999))


def _popen_creationflags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class PreparedLutChain:
    directory: Path
    restoration: Path | None
    style: Path | None


def _lut_filters(prepared_luts):
    if prepared_luts is None:
        return []
    filters = []
    if prepared_luts.restoration is not None:
        filters.append("lut3d=file=restore.cube:interp=tetrahedral")
    if prepared_luts.style is not None:
        filters.append("lut3d=file=style.cube:interp=tetrahedral")
    return filters


def _video_filter(fps, prepared_luts):
    filter_graph = f"fps={fps}"
    lut_filters = _lut_filters(prepared_luts)
    if lut_filters:
        filter_graph += "," + ",".join([*lut_filters, "format=yuvj420p"])
    return filter_graph


def _style_filter(prepared_luts):
    filters = _lut_filters(prepared_luts)
    if not filters:
        raise ValueError("style LUT is required for image transformation")
    return ",".join([*filters, "format=yuvj420p"])


def _validate_prepared_luts(prepared_luts):
    command = [
        locate_ffmpeg(),
        "-hide_banner",
        "-nostdin",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=2x2:d=0.04",
        "-vf",
        _style_filter(prepared_luts),
        "-frames:v",
        "1",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(
        command,
        cwd=str(prepared_luts.directory),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_popen_creationflags(),
    )
    if result.returncode != 0:
        tail = "\n".join((result.stdout or "").splitlines()[-12:])
        raise ValueError(f"invalid color LUT: {tail or 'FFmpeg rejected the .cube file'}")


@contextmanager
def prepare_lut_chain(restoration_lut_path=None, style_lut_path=None):
    if not restoration_lut_path and not style_lut_path:
        yield None
        return
    with tempfile.TemporaryDirectory(prefix="xpano-lut-") as temporary_directory:
        directory = Path(temporary_directory)
        prepared = {}
        for name, source_path in (("restore.cube", restoration_lut_path), ("style.cube", style_lut_path)):
            if not source_path:
                continue
            source = Path(source_path)
            if source.suffix.lower() != ".cube":
                raise ValueError(f"color LUT must be a .cube file: {source}")
            if not source.is_file():
                raise FileNotFoundError(f"color LUT does not exist or is not a file: {source}")
            destination = directory / name
            shutil.copyfile(source, destination)
            prepared[name] = destination
        chain = PreparedLutChain(
            directory=directory,
            restoration=prepared.get("restore.cube"),
            style=prepared.get("style.cube"),
        )
        try:
            _validate_prepared_luts(chain)
        except ValueError as error:
            raise ValueError(f"invalid color LUT: {error}") from error
        yield chain


def apply_style_lut_to_image(source, destination, prepared_luts):
    source = Path(source)
    destination = Path(destination)
    if prepared_luts is None or prepared_luts.style is None:
        raise ValueError("style LUT is required for image transformation")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"{destination.stem}.tmp{destination.suffix}")
    temporary.unlink(missing_ok=True)
    command = [
        locate_ffmpeg(),
        "-hide_banner",
        "-y",
        "-nostdin",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-vf",
        _style_filter(prepared_luts),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(temporary),
    ]
    result = subprocess.run(
        command,
        cwd=str(prepared_luts.directory),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_popen_creationflags(),
    )
    if result.returncode != 0:
        temporary.unlink(missing_ok=True)
        tail = "\n".join((result.stdout or "").splitlines()[-12:])
        raise ValueError(f"failed to apply style LUT to {source.name}: {tail or 'FFmpeg failed'}")
    _copy_photo_exif(source, temporary)
    os.replace(temporary, destination)


def _count_generated_pairs(out_root: Path, base_name: str):
    if not out_root or not base_name:
        return 0
    left_count = len(list(out_root.glob(f"{base_name}_L_*.jpg")))
    right_count = len(list(out_root.glob(f"{base_name}_R_*.jpg")))
    return min(left_count, right_count)


def _count_generated_single_frames(out_root: Path, base_name: str):
    if not out_root or not base_name:
        return 0
    return len(list(out_root.glob(f"{base_name}_*.jpg")))


def _emit_generated_pair_previews(out_root: Path, base_name: str, last_previewed: int, preview_cb):
    if not out_root or not base_name or preview_cb is None:
        return last_previewed
    count = _count_generated_pairs(out_root, base_name)
    for frame_idx in range(last_previewed + 1, count + 1):
        left = out_root / f"{base_name}_L_{frame_idx:05d}.jpg"
        right = out_root / f"{base_name}_R_{frame_idx:05d}.jpg"
        if left.exists() and right.exists():
            _frame_preview(left, right, preview_cb)
            last_previewed = frame_idx
    return last_previewed


def _emit_generated_single_previews(out_root: Path, base_name: str, last_previewed: int, preview_cb):
    if not out_root or not base_name or preview_cb is None:
        return last_previewed
    count = _count_generated_single_frames(out_root, base_name)
    for frame_idx in range(last_previewed + 1, count + 1):
        frame = out_root / f"{base_name}_{frame_idx:05d}.jpg"
        if frame.exists():
            _frame_preview(frame, frame, preview_cb)
            last_previewed = frame_idx
    return last_previewed


def _run_ffmpeg(
    cmd,
    input_path: Path,
    fps,
    max_frames,
    progress_cb=None,
    log_cb=None,
    out_root=None,
    base_name=None,
    preview_cb=None,
    preview_mode="pair",
    start_time_seconds=0.0,
    end_time_seconds=0.0,
    cwd=None,
):
    expected_frames = _expected_frame_count(
        input_path,
        fps,
        max_frames,
        log_cb=log_cb,
        start_time_seconds=start_time_seconds,
        end_time_seconds=end_time_seconds,
    )
    if log_cb:
        if expected_frames:
            log_cb(f"ffmpeg extracting {input_path.name}, expected frames: {expected_frames}")
        else:
            log_cb(f"ffmpeg extracting {input_path.name}, expected frames unknown")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_popen_creationflags(),
        cwd=str(cwd) if cwd else None,
    )
    output_lines = []
    last_frame = 0
    last_logged_frame = 0
    last_log_time = 0.0
    reader_done = threading.Event()
    frame_lock = threading.Lock()
    log_step = max(1, (expected_frames or 100) // 20)

    def emit_progress(current, final=False):
        if not progress_cb:
            return
        total = expected_frames or 100
        if final:
            current = total
        elif expected_frames:
            current = max(0, min(int(current), total))
        else:
            current = max(0, min(int(current), total - 1))
        progress_cb(current, total)

    def set_last_frame(value):
        nonlocal last_frame
        with frame_lock:
            last_frame = max(last_frame, int(value))
            return last_frame

    def get_last_frame():
        with frame_lock:
            return last_frame

    def read_output():
        try:
            for raw_line in proc.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                output_lines.append(line)
                key, sep, value = line.partition("=")
                if sep and key == "frame":
                    try:
                        emit_progress(set_last_frame(int(value.strip())))
                    except ValueError:
                        pass
                elif sep and key == "out_time_ms":
                    try:
                        seconds = int(value.strip()) / 1000000.0
                        emit_progress(set_last_frame(seconds * fps))
                    except ValueError:
                        pass
                elif sep and key == "progress":
                    if value == "end":
                        emit_progress(expected_frames or get_last_frame(), final=True)
                elif log_cb and not sep:
                    log_cb(line)
        finally:
            reader_done.set()

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()

    out_root = Path(out_root) if out_root else None
    last_previewed = 0
    while proc.poll() is None:
        generated = _count_generated_single_frames(out_root, base_name) if preview_mode == "single" else _count_generated_pairs(out_root, base_name)
        if generated:
            emit_progress(set_last_frame(generated))
            if preview_mode == "single":
                last_previewed = _emit_generated_single_previews(out_root, base_name, last_previewed, preview_cb)
            else:
                last_previewed = _emit_generated_pair_previews(out_root, base_name, last_previewed, preview_cb)

        now = time.monotonic()
        current_frame = get_last_frame()
        if log_cb and expected_frames and (
            current_frame - last_logged_frame >= log_step or now - last_log_time >= 5
        ):
            last_logged_frame = current_frame
            last_log_time = now
            log_cb(f"extract progress {min(current_frame, expected_frames)}/{expected_frames}")
        time.sleep(0.25)

    rc = proc.wait()
    reader_done.wait(timeout=2)
    reader.join(timeout=2)
    generated = _count_generated_single_frames(out_root, base_name) if preview_mode == "single" else _count_generated_pairs(out_root, base_name)
    if generated:
        emit_progress(set_last_frame(generated))
        if preview_mode == "single":
            _emit_generated_single_previews(out_root, base_name, last_previewed, preview_cb)
        else:
            _emit_generated_pair_previews(out_root, base_name, last_previewed, preview_cb)
    if rc != 0:
        tail = "\n".join(output_lines[-20:])
        raise subprocess.CalledProcessError(rc, cmd, output=tail)


def _extract_one(args):
    task, fps, out_root, max_frames, preview_cb, progress_cb, log_cb, model_prefix, start_time_seconds, end_time_seconds, prepared_luts = args
    left = task["left_file"]
    right = task["right_file"]
    base_name = task["clean_name"]
    input_time_args = _input_time_args(start_time_seconds, end_time_seconds)
    def command_factory(_name, hardware_args):
        if task["type"] == "insta_split":
            cmd = [
                locate_ffmpeg(), "-hide_banner", "-y", "-nostdin", "-progress", "pipe:1", "-nostats",
                *_ffmpeg_input_args(left, input_time_args, hardware_args),
                *_ffmpeg_input_args(right, input_time_args, hardware_args),
                "-map", "0:0", "-vf", _video_filter(fps, prepared_luts),
            ]
            _append_frame_limit(cmd, max_frames)
            cmd.extend([
                "-q:v", "2",
                str(out_root / f"{base_name}_L_%05d.jpg"),
                "-map", "1:0", "-vf", _video_filter(fps, prepared_luts),
            ])
            _append_frame_limit(cmd, max_frames)
            cmd.extend([
                "-q:v", "2",
                str(out_root / f"{base_name}_R_%05d.jpg"),
            ])
            return cmd
        cmd = [
            locate_ffmpeg(), "-hide_banner", "-y", "-nostdin", "-progress", "pipe:1", "-nostats",
            *_ffmpeg_input_args(left, input_time_args, hardware_args),
            "-map", "0:0", "-vf", _video_filter(fps, prepared_luts),
        ]
        _append_frame_limit(cmd, max_frames)
        cmd.extend([
            "-q:v", "2",
            str(out_root / f"{base_name}_L_%05d.jpg"),
            "-map", "0:1", "-vf", _video_filter(fps, prepared_luts),
        ])
        _append_frame_limit(cmd, max_frames)
        cmd.extend([
            "-q:v", "2",
            str(out_root / f"{base_name}_R_%05d.jpg"),
        ])
        return cmd

    _run_ffmpeg_with_hardware_fallback(
        command_factory,
        left,
        fps,
        max_frames,
        cleanup_cb=lambda: _remove_generated_files(
            out_root,
            [f"{base_name}_L_*.jpg", f"{base_name}_R_*.jpg"],
        ),
        progress_cb=progress_cb,
        out_root=out_root,
        base_name=base_name,
        preview_cb=preview_cb,
        start_time_seconds=start_time_seconds,
        end_time_seconds=end_time_seconds,
        log_cb=log_cb,
        cwd=prepared_luts.directory if prepared_luts else None,
    )

    left_files = sorted(out_root.glob(f"{base_name}_L_*.jpg"))
    right_files = sorted(out_root.glob(f"{base_name}_R_*.jpg"))
    count = min(len(left_files), len(right_files))
    if max_frames and max_frames > 0:
        count = min(count, max_frames)
    extracted = []
    for idx in range(count):
        frame_idx = idx + 1
        frame_dir = out_root / f"{base_name}_frame_{frame_idx:05d}"
        frame_dir.mkdir(exist_ok=True)
        ldst = frame_dir / f"{base_name}_frame_{frame_idx:05d}_left.jpg"
        rdst = frame_dir / f"{base_name}_frame_{frame_idx:05d}_right.jpg"
        shutil.move(str(left_files[idx]), str(ldst))
        shutil.move(str(right_files[idx]), str(rdst))
        make = "Insta360" if left.suffix.lower() == ".insv" else "DJI"
        model_root = model_prefix or make.lower()
        _apply_exif(ldst, f"{model_root}_left", make)
        _apply_exif(rdst, f"{model_root}_right", make)
        extracted.append((ldst, rdst))
        _frame_preview(ldst, rdst, preview_cb)
        if progress_cb:
            progress_cb(frame_idx, count)
    return extracted


def extract_frames(
    input_path,
    out_root,
    fps,
    max_frames=0,
    start_time_seconds=0.0,
    end_time_seconds=0.0,
    preview_cb=None,
    progress_cb=None,
    log_cb=None,
    model_prefix=None,
    restoration_lut_path=None,
    style_lut_path=None,
):
    input_path = Path(input_path)
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    if restoration_lut_path or style_lut_path:
        input_path = input_path.resolve()
        out_root = out_root.resolve()
    files = [input_path]
    pair_map = {}
    if input_path.suffix.lower() == ".insv":
        m = re.search(r"(VID_\d+_\d+)_(00|10)_(\d+)", input_path.name)
        if m:
            prefix, side, suffix = m.groups()
            other = "10" if side == "00" else "00"
            partner = input_path.parent / f"{prefix}_{other}_{suffix}.insv"
            if partner.exists():
                files = [input_path, partner]
                pair_map[input_path] = partner
    task = {
        "clean_name": input_path.stem,
        "left_file": input_path,
        "right_file": pair_map.get(input_path, input_path),
        "type": "insta_split" if input_path.suffix.lower() == ".insv" and pair_map.get(input_path) else "dji_dual",
    }
    if progress_cb:
        progress_cb(0, max_frames if max_frames and max_frames > 0 else 1)
    with prepare_lut_chain(restoration_lut_path, style_lut_path) as prepared_luts:
        extracted = _extract_one(
            (
                task,
                fps,
                out_root,
                max_frames,
                preview_cb,
                progress_cb,
                log_cb,
                model_prefix,
                start_time_seconds,
                end_time_seconds,
                prepared_luts,
            )
        )
    if progress_cb:
        progress_cb(1, 1)
    return extracted


def extract_single_video_frames(
    input_path,
    out_root,
    fps,
    max_frames=0,
    start_time_seconds=0.0,
    end_time_seconds=0.0,
    preview_cb=None,
    progress_cb=None,
    log_cb=None,
    model_prefix=None,
    restoration_lut_path=None,
    style_lut_path=None,
):
    input_path = Path(input_path)
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    if restoration_lut_path or style_lut_path:
        input_path = input_path.resolve()
        out_root = out_root.resolve()
    base_name = model_prefix or input_path.stem
    input_time_args = _input_time_args(start_time_seconds, end_time_seconds)
    def command_factory(_name, hardware_args):
        cmd = [
            locate_ffmpeg(), "-hide_banner", "-y", "-nostdin", "-progress", "pipe:1", "-nostats",
            *_ffmpeg_input_args(input_path, input_time_args, hardware_args),
            "-map", "0:v:0", "-vf", _video_filter(fps, prepared_luts),
        ]
        _append_frame_limit(cmd, max_frames)
        cmd.extend(["-q:v", "2", str(out_root / f"{base_name}_%05d.jpg")])
        return cmd
    if progress_cb:
        progress_cb(0, max_frames if max_frames and max_frames > 0 else 1)
    with prepare_lut_chain(restoration_lut_path, style_lut_path) as prepared_luts:
        _run_ffmpeg_with_hardware_fallback(
            command_factory,
            input_path,
            fps,
            max_frames,
            cleanup_cb=lambda: _remove_generated_files(out_root, [f"{base_name}_*.jpg"]),
            progress_cb=progress_cb,
            out_root=out_root,
            base_name=base_name,
            preview_cb=preview_cb,
            preview_mode="single",
            start_time_seconds=start_time_seconds,
            end_time_seconds=end_time_seconds,
            log_cb=log_cb,
            cwd=prepared_luts.directory if prepared_luts else None,
        )

    frame_files = sorted(out_root.glob(f"{base_name}_*.jpg"))
    if max_frames and max_frames > 0:
        frame_files = frame_files[:max_frames]
    extracted = []
    for idx, source in enumerate(frame_files, 1):
        dst = out_root / f"{base_name}_frame_{idx:05d}.jpg"
        if source != dst:
            shutil.move(str(source), str(dst))
        _apply_exif(dst, f"{base_name}_frame", "xPano")
        extracted.append(dst)
        if preview_cb:
            preview_cb(str(dst), str(dst))
        if progress_cb:
            progress_cb(idx, len(frame_files))
    return extracted
