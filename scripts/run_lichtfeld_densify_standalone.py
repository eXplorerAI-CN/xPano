import argparse
import ast
import importlib.util
import os
import sys
import types
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.densify_runtime_env import activate_densify_runtime


def _runtime_roots():
    try:
        from scripts.runtime_paths import candidate_roots

        return candidate_roots()
    except Exception:
        return [Path(__file__).resolve().parents[1]]


def locate_bundled_site_packages():
    explicit = os.environ.get("XPANO_DENSIFY_SITE_PACKAGES", "").strip()
    if explicit and Path(explicit).is_dir():
        return Path(explicit)
    candidates = []
    for root in _runtime_roots():
        root = Path(root)
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
    return None


def bootstrap_bundled_runtime():
    site_packages = locate_bundled_site_packages()
    if not site_packages:
        return None
    return activate_densify_runtime(site_packages)


def check_bundled_imports(profile="cpu"):
    import torch, torchvision, pycolmap, PIL, scipy, tqdm, einops, rich, open3d  # noqa: F401

    if profile == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA profile is installed but CUDA is unavailable")
        torch.ones(1, device="cuda").cpu()
    else:
        torch.ones(1)

    print("ok", flush=True)
    return 0


def _configure_console_output():
    for stream in [sys.stdout, sys.stderr]:
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


class _StdoutLogger:
    def info(self, text):
        print(text, flush=True)

    def warn(self, text):
        print(f"WARN: {text}", flush=True)

    def error(self, text):
        print(f"ERROR: {text}", flush=True)

    def debug(self, text):
        print(f"DEBUG: {text}", flush=True)


def _install_lichtfeld_stub():
    module = types.ModuleType("lichtfeld")
    module.log = _StdoutLogger()
    module.ui = types.SimpleNamespace(set_panel_enabled=lambda *args, **kwargs: None)
    module.register_class = lambda *args, **kwargs: None
    module.unregister_class = lambda *args, **kwargs: None
    sys.modules.setdefault("lichtfeld", module)


def _load_plugin_densify(plugin_dir):
    plugin_dir = Path(plugin_dir).resolve()
    densify_path = plugin_dir / "densify.py"
    if not densify_path.exists():
        raise FileNotFoundError(densify_path)
    project_root = Path(__file__).resolve().parents[1]
    torch_home = project_root / "tools" / "torch-cache"
    os.environ.setdefault("TORCH_HOME", str(torch_home))
    roma_src = plugin_dir / "RoMaV2" / "src"
    for path in [plugin_dir, roma_src]:
        if path.exists() and str(path) not in sys.path:
            sys.path.insert(0, str(path))
    package_name = "_xpano_lichtfeld_densification_plugin"
    package = types.ModuleType(package_name)
    package.__path__ = [str(plugin_dir)]
    sys.modules[package_name] = package
    spec = importlib.util.spec_from_file_location(
        f"{package_name}.densify",
        densify_path,
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"{package_name}.densify"] = module
    spec.loader.exec_module(module)
    return module


def _load_plugin_argparser(plugin_dir):
    plugin_dir = Path(plugin_dir).resolve()
    densify_path = plugin_dir / "densify.py"
    if not densify_path.exists():
        raise FileNotFoundError(densify_path)
    source = densify_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(densify_path))
    function = next(
        (node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "build_argparser"),
        None,
    )
    if function is None:
        raise RuntimeError(f"build_argparser() was not found in {densify_path}")
    namespace = {"argparse": argparse}
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    exec(compile(module, str(densify_path), "exec"), namespace)
    return namespace["build_argparser"]()


def main(argv=None):
    _configure_console_output()
    parser = argparse.ArgumentParser(
        description="Run LichtFeld densification plugin outside LichtFeld Studio.",
        add_help=False,
    )
    parser.add_argument("--plugin-dir")
    parser.add_argument("--xpano-site-packages")
    parser.add_argument("--self-test-imports", action="store_true")
    parser.add_argument("--profile", choices=["cpu", "cuda"], default="cpu")
    args, plugin_args = parser.parse_known_args(argv)

    os.environ["PYTHONNOUSERSITE"] = "1"
    if args.xpano_site_packages:
        activate_densify_runtime(args.xpano_site_packages)
    else:
        bootstrap_bundled_runtime()

    if args.self_test_imports:
        check_bundled_imports(args.profile)
        return 0

    if not args.plugin_dir:
        parser.error("--plugin-dir is required unless --self-test-imports is used")

    if not plugin_args or any(arg in {"-h", "--help"} for arg in plugin_args):
        # NOTE: Environment probes use --help; keep that path independent of Torch/Open3D imports.
        _load_plugin_argparser(args.plugin_dir).print_help()
        return 0

    print("PROGRESS:2.0:Loading LichtFeld densification components", flush=True)
    _install_lichtfeld_stub()
    densify = _load_plugin_densify(args.plugin_dir)
    plugin_parser = densify.build_argparser()
    print("PROGRESS:5.0:Starting LichtFeld densification", flush=True)

    def progress(percent, message):
        print(f"PROGRESS:{float(percent):.1f}:{message}", flush=True)

    return densify.dense_init(plugin_parser.parse_args(plugin_args), progress_callback=progress)


if __name__ == "__main__":
    raise SystemExit(main())
