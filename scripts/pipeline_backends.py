from dataclasses import dataclass


METASHAPE_BACKEND = "metashape"
COLMAP_BACKEND = "colmap"
SUPPORTED_BACKENDS = {METASHAPE_BACKEND, COLMAP_BACKEND}


@dataclass(frozen=True)
class BackendStatus:
    name: str
    stable: bool
    description: str


BACKEND_STATUS = {
    METASHAPE_BACKEND: BackendStatus(
        name=METASHAPE_BACKEND,
        stable=True,
        description="Verified Metashape Station-to-Folder workflow.",
    ),
    COLMAP_BACKEND: BackendStatus(
        name=COLMAP_BACKEND,
        stable=False,
        description="Experimental backend planned for COLMAP rig/fisheye reconstruction.",
    ),
}


def normalize_backend(value):
    backend = (value or METASHAPE_BACKEND).strip().lower()
    if backend not in SUPPORTED_BACKENDS:
        supported = ", ".join(sorted(SUPPORTED_BACKENDS))
        raise ValueError(f"Unsupported backend: {value}. Supported backends: {supported}")
    return backend


def require_implemented_backend(backend):
    backend = normalize_backend(backend)
    return backend
