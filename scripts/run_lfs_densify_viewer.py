import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.colmap_dense_merge import merge_dense_ply_into_colmap_points
from scripts.lichtfeld_densify import LichtfeldDensifyConfig, run_densify_command


def _positive_float(value, fallback):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _positive_int(value, fallback):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _choose_images_subdir(scene_root):
    for name in ["images", "colmap_images", "images_2"]:
        if (scene_root / name).is_dir():
            return name
    return "images"


def build_argparser():
    parser = argparse.ArgumentParser(description="Run bundled LichtFeld densification for xPano UI.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--python-exe", required=True)
    parser.add_argument("--site-packages")
    parser.add_argument("--plugin-dir", required=True)
    parser.add_argument("--roma", default="fast", choices=["turbo", "fast", "base", "high", "precise"])
    parser.add_argument("--max-points", type=int, default=0)
    parser.add_argument("--num-refs", type=float, default=0.75)
    parser.add_argument("--nns-per-ref", type=int, default=3)
    parser.add_argument("--matches-per-ref", type=int, default=10000)
    parser.add_argument("--steps", type=int, default=50)
    parser.add_argument("--certainty-thresh", type=float, default=0.2)
    parser.add_argument("--image-filter", default="front_plus_hd")
    parser.add_argument("--roi-start", type=float, default=0.0)
    parser.add_argument("--roi-end", type=float, default=1.0)
    return parser


def run(args, densify_runner=run_densify_command):
    scene_root = Path(args.output_dir).resolve()
    sparse_dir = scene_root / "sparse" / "0"
    points_path = sparse_dir / "points3D.bin"
    if not points_path.exists():
        raise FileNotFoundError(f"COLMAP sparse model not found: {points_path}")

    images_subdir = _choose_images_subdir(scene_root)
    dense_ply = sparse_dir / "points3D_dense.ply"
    dense_bin = sparse_dir / "points3D_dense.bin"

    if dense_ply.exists():
        dense_ply.unlink()
    if dense_bin.exists():
        dense_bin.unlink()

    print(f"Using images directory: {images_subdir}", flush=True)
    print(f"Image range preset: {args.image_filter}", flush=True)
    print(f"Steps: {_positive_int(args.steps, 50)} (reserved by current LichtFeld backend)", flush=True)
    print("PROGRESS:1.0:Preparing LichtFeld densification", flush=True)

    def progress(percent):
        print(f"PROGRESS:{float(percent):.1f}:Running LichtFeld densification", flush=True)

    densify_runner(
        LichtfeldDensifyConfig(
            python_exe=str(Path(args.python_exe)),
            site_packages=Path(args.site_packages) if args.site_packages else None,
            plugin_dir=Path(args.plugin_dir),
            scene_root=scene_root,
            images_subdir=images_subdir,
            out_name=dense_ply.name,
            roma_setting=args.roma,
            num_refs=_positive_float(args.num_refs, 0.75),
            nns_per_ref=_positive_int(args.nns_per_ref, 3),
            matches_per_ref=_positive_int(args.matches_per_ref, 10000),
            certainty_thresh=max(0.0, min(1.0, float(args.certainty_thresh))),
            max_points=max(0, int(args.max_points)),
        ),
        progress_cb=progress,
        log_cb=lambda text: print(text, flush=True),
    )

    if not dense_ply.exists():
        raise FileNotFoundError(f"LichtFeld output was not created: {dense_ply}")

    print("PROGRESS:96.0:Merging dense points into COLMAP preview", flush=True)
    merge_result = merge_dense_ply_into_colmap_points(
        sparse_model_dir=sparse_dir,
        dense_ply_path=dense_ply,
        output_points_path=dense_bin,
        replace_points_bin=False,
    )
    result = {
        **merge_result,
        "dense_ply_path": str(dense_ply),
        "backup_points_path": str(sparse_dir / "points3D_sparse_original.bin"),
        "roma": args.roma,
        "max_points": max(0, int(args.max_points)),
        "steps": _positive_int(args.steps, 50),
    }
    print("PROGRESS:100.0:Densification preview is ready", flush=True)
    print("DENSIFY_RESULT:" + json.dumps(result, ensure_ascii=False), flush=True)
    return result


def main(argv=None):
    args = build_argparser().parse_args(argv)
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
