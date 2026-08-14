# Bundled COLMAP

Place the portable Windows COLMAP release here so xPano can run the COLMAP backend without requiring PATH setup.

Recommended default package:

- https://github.com/colmap/colmap/releases/download/4.0.4/colmap-x64-windows-nocuda.zip

CUDA package, larger but GPU-enabled:

- https://github.com/colmap/colmap/releases/download/4.0.4/colmap-x64-windows-cuda.zip

Supported layouts after extraction:

```text
tools/colmap/COLMAP.bat
tools/colmap/colmap.exe
tools/colmap/bin/colmap.exe
tools/colmap/<release-folder>/COLMAP.bat
tools/colmap/<release-folder>/bin/colmap.exe
```

The GUI and CLI resolve COLMAP in this order:

1. `XPANO_COLMAP` environment variable
2. bundled executable under `tools/colmap`
3. system `PATH`
4. common system install locations

To install the recommended bundled copy from this repository root:

```powershell
INSTALL_COLMAP.bat
```

To install the larger CUDA build:

```powershell
INSTALL_COLMAP.bat -Variant cuda
```
