import sys
from pathlib import Path
import os
import shutil


def app_root():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def internal_root():
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS).resolve()
    return app_root()


def candidate_roots():
    roots = [app_root()]
    internal = internal_root()
    if internal not in roots:
        roots.append(internal)
    return roots


def first_existing(paths):
    for path in paths:
        path = Path(path)
        if path.exists():
            return str(path)
    return ""


def _bundled_tool_candidates(folder, tool_name, root=None):
    exe = tool_name if tool_name.lower().endswith(".exe") else f"{tool_name}.exe"
    roots = [Path(root)] if root else candidate_roots()
    candidates = []
    for item in roots:
        candidates.extend([
            item / "tools" / folder / "bin" / exe,
            item / "tools" / folder / exe,
            item / "third_party" / folder / "bin" / exe,
            item / "third_party" / folder / exe,
        ])
    return candidates


def locate_bundled_or_path(tool_name, folder=None, env_var=None, root=None):
    if env_var:
        explicit = os.environ.get(env_var)
        if explicit and Path(explicit).exists():
            return explicit
    if folder:
        bundled = first_existing(_bundled_tool_candidates(folder, tool_name, root=root))
        if bundled:
            return bundled
    names = [tool_name] if tool_name.lower().endswith(".exe") else [f"{tool_name}.exe", tool_name]
    for name in names:
        resolved = shutil.which(name)
        if resolved:
            return resolved
    return tool_name


def locate_ffmpeg(root=None):
    return locate_bundled_or_path("ffmpeg", folder="ffmpeg", env_var="XPANO_FFMPEG", root=root)


def locate_ffprobe(root=None):
    return locate_bundled_or_path("ffprobe", folder="ffmpeg", env_var="XPANO_FFPROBE", root=root)
