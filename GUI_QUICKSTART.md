# xPano New UI Quickstart

This project now uses the Tauri/React UI under `xpano-ui`.

## Start From Source

```bat
RUN_XPANO_UI.bat
```

The launcher enters `xpano-ui`, installs local UI dependencies when needed, and starts Tauri dev mode.

## Environment Check

The GUI now runs the same environment check automatically before starting an alignment task.
To run it manually:

```bat
CHECK_ENV.bat -Backend colmap
CHECK_ENV.bat -Backend metashape -MetashapeExe "C:\Path\To\Metashape\metashape.exe"
CHECK_ENV.bat -Backend colmap -IncludeDensify
```

The checker verifies app Python packages, ffmpeg/ffprobe, COLMAP or Metashape, Metashape's own Python packages, and optional LichtFeld densification. If `tools\offline-wheels` contains wheels, installation uses those local packages first.

Before building an offline release, refresh the bundled wheels:

```bat
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\download_offline_wheels.ps1 -Root .
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_embedded_python.ps1 -Root .
```

## Release Package

In a packaged release, run:

```bat
RUN_XPANO.bat
```

## Notes

- COLMAP mode uses the bundled `tools/colmap` when present.
- Metashape mode still requires a local licensed Metashape installation.
- LichtFeld densification is available from the point cloud viewer after a reconstruction is loaded.
