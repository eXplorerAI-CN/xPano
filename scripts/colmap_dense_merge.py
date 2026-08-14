import struct
from pathlib import Path

from scripts.colmap_backend import read_colmap_points3d, write_colmap_points3d


def read_binary_little_endian_ply_vertices(path):
    path = Path(path)
    data = path.read_bytes()
    marker = b"end_header\n"
    header_end = data.find(marker)
    if header_end < 0:
        raise RuntimeError(f"PLY header is missing end_header: {path}")
    header = data[:header_end].decode("ascii", errors="replace")
    if "format binary_little_endian 1.0" not in header:
        raise RuntimeError(f"Only binary_little_endian PLY is supported: {path}")
    vertex_count = None
    for line in header.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[:2] == ["element", "vertex"]:
            vertex_count = int(parts[2])
            break
    if vertex_count is None:
        raise RuntimeError(f"PLY vertex count is missing: {path}")
    offset = header_end + len(marker)
    stride = struct.calcsize("<fffBBB")
    expected = offset + vertex_count * stride
    if len(data) < expected:
        raise RuntimeError(f"PLY data is truncated: {path}")
    vertices = []
    for index in range(vertex_count):
        x, y, z, r, g, b = struct.unpack_from("<fffBBB", data, offset + index * stride)
        vertices.append({"xyz": (float(x), float(y), float(z)), "rgb": (int(r), int(g), int(b))})
    return vertices


def merge_dense_ply_into_colmap_points(
    sparse_model_dir,
    dense_ply_path,
    output_points_path=None,
    replace_points_bin=False,
    error=1.0,
):
    sparse_model_dir = Path(sparse_model_dir)
    dense_ply_path = Path(dense_ply_path)
    original_points_path = sparse_model_dir / "points3D.bin"
    output_points_path = Path(output_points_path) if output_points_path else sparse_model_dir / "points3D_dense.bin"

    original = read_colmap_points3d(sparse_model_dir)
    dense_vertices = read_binary_little_endian_ply_vertices(dense_ply_path)
    next_id = max((int(point["id"]) for point in original), default=0) + 1
    dense_points = [
        {
            "id": next_id + idx,
            "xyz": vertex["xyz"],
            "rgb": vertex["rgb"],
            "error": float(error),
            "track": [],
        }
        for idx, vertex in enumerate(dense_vertices)
    ]
    merged = original + dense_points
    write_colmap_points3d(output_points_path, merged)
    if replace_points_bin:
        backup = sparse_model_dir / "points3D_sparse_original.bin"
        if not backup.exists():
            backup.write_bytes(original_points_path.read_bytes())
        write_colmap_points3d(original_points_path, merged)
    return {
        "original_points": len(original),
        "dense_points": len(dense_points),
        "merged_points": len(merged),
        "output_points_path": str(output_points_path),
        "replaced_points_bin": bool(replace_points_bin),
    }
