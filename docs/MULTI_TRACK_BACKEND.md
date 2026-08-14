# xPano Multi-Track Backend

This document describes the current multi-track backend used by the GUI and CLI. A material pool is made of tracks, where each track represents one device/source.

## Track Types

- `panorama_video`: `.osv`, `.insv`, or compatible dual-fisheye video. Uses the verified Station-retained xPano workflow.
- `standard_photos`: pinhole/frame photos from a phone or standard camera.
- `aerial_photos`: pinhole/frame photos from a drone.

## Manifest

The backend writes a manifest at:

```text
output/xpano_manifest.json
output/work/xpano_manifest.json
```

Important fields:

- `track_id`: stable track identity.
- `track_type`: one of the supported track types.
- `device_label`: user/device-facing label.
- `metashape_mode`: `dual_fisheye_station` or `pinhole_frame`.
- `export_mode`: `cubemap` or `undistorted_frame`.
- `frames`: panorama frame pairs.
- `photos`: pinhole photo paths.
- `photo_sensors`: pinhole photos split by camera identity. The split key uses image size, EXIF make/model, lens make/model, focal length, and 35mm focal length when available.

## Metashape Mapping

One material track maps to one device source, but not directly to one CameraGroup.

Panorama track:

- Creates two sensors:
  - `<track_id>_left`
  - `<track_id>_right`
- Sensor type is `Metashape.Sensor.Type.EquidistantFisheye` when supported, matching the initial release. Older Metashape builds fall back to `Fisheye`.
- The imported source calibration is copied before the equidistant model and fixed parameters are applied.
- Pixel size is `0.0024`.
- Focal length is `2.5`.
- Fixed params are exactly `["B1", "B2", "K4"]`.
- Each sampled frame creates one CameraGroup with two cameras.
- These groups are switched to `Station` before panorama matching/alignment, then restored to `Folder` before panorama optimization.

Photo/aerial track:

- Creates Frame sensors, split by image size and EXIF camera/lens identity when available.
- Sensor type is `Metashape.Sensor.Type.Frame`.
- It is never switched to Fisheye.
- It is never assigned to Station groups.

Unused auto-created Metashape sensors are pruned after import so the project does not contain misleading empty sensors.
New cameras are identified by stable Metashape camera keys rather than `chunk.cameras` list position. Every import also verifies camera count and source-photo paths before sensor assignment.

Metashape uses the initial-release staged workflow: it imports, Station-matches, aligns, releases, and optimizes panorama cameras first with `keep_keypoints=True`. Only then are Frame cameras imported. A second visual match retains the panorama keypoints, and `alignCameras(reset_alignment=False)` incrementally attaches the new cameras before final optimization. Stored `mixed` configuration values remain accepted for project compatibility but are normalized to this one stable workflow.

## Export Rules

The exporter writes a single COLMAP model:

```text
output/images
output/sparse/0/cameras.bin
output/sparse/0/images.bin
output/sparse/0/points3D.bin
```

Rules:

- Fisheye sensors are exported as cubemap pinhole images.
- Frame sensors are exported as undistorted pinhole images.
- Used sensors only are exported.
- All cameras and points share one COLMAP sparse model.
- `scripts/verify_xpano_output.py` verifies the export structure: cube/frame image counts, single `sparse/0`, and COLMAP binary record counts.
- `scripts/run_xpano_tracks_job.py` records this verification under `xpano_run_summary.json -> export_verification`.

## CLI Examples

The Tauri UI launches `scripts/run_xpano_tracks_job.py`, which builds a manifest and routes into `scripts.pipeline_core.run_multi_track_pipeline(MultiTrackJobConfig)`. CLI runs and GUI runs therefore share the same backend path without depending on a legacy Python GUI module.

The backend input model is:

```text
MaterialTrack(track_type, label, paths)
```

where `track_type` is one of:

- `panorama_video`
- `ordinary_video`
- `standard_photos`
- `aerial_photos`

`material_tracks_to_job_config(...)` converts a material-track list into `MultiTrackJobConfig`. The Tauri UI serializes material tracks to CLI arguments, while the Python backend remains UI-neutral.
Single panorama track:

```powershell
python scripts\run_xpano_tracks_job.py `
  --output "_tracks_qinshi_1s_50" `
  --pano "D:\path\to\camera.osv" `
  --frames-per-second 1 `
  --max-frames 50 `
  --metashape "C:\Path\To\Metashape\metashape.exe"
```

Mixed panorama + phone photos:

```powershell
python scripts\run_xpano_tracks_job.py `
  --output "_tracks_mixed" `
  --pano "D:\path\camera.osv" `
  --standard-track phone "D:\path\phone_photos" `
  --frames-per-second 1 `
  --max-frames 50 `
  --metashape "C:\Path\To\Metashape\metashape.exe"
```

Mixed panorama + drone photos:

```powershell
python scripts\run_xpano_tracks_job.py `
  --output "_tracks_drone" `
  --pano "D:\path\camera.osv" `
  --aerial-track mavic "D:\path\drone_photos" `
  --frames-per-second 1 `
  --metashape "C:\Path\To\Metashape\metashape.exe"
```

Prepared manifest:

```powershell
python scripts\run_xpano_tracks_job.py `
  --output "_tracks_from_manifest" `
  --manifest "D:\path\xpano_manifest.json" `
  --metashape "C:\Path\To\Metashape\metashape.exe"
```

Validate a prepared manifest before starting Metashape:

```powershell
python scripts\validate_xpano_manifest.py `
  --manifest "_tracks_qinshi_1s_50\work\xpano_manifest.json"
```

Verify a finished Metashape project:

```powershell
& "C:\Path\To\Metashape\metashape.exe" -r scripts\diagnose_metashape_project.py `
  --project "_tracks_qinshi_1s_50\work\xpano.psx" `
  --expect-cameras 100 `
  --expect-aligned 100 `
  --expect-groups 50 `
  --expect-sensors 2 `
  --expect-fisheye-sensors 2 `
  --expect-frame-sensors 0 `
  --expect-folder-groups 50 `
  --expect-station-groups 0 `
  --expect-fixed-fisheye
```

Verify a finished COLMAP export:

```powershell
python scripts\verify_xpano_output.py `
  --output "_tracks_qinshi_1s_50" `
  --expect-cube-images 500 `
  --expect-frame-images 0 `
  --expect-colmap-images 500 `
  --expect-colmap-cameras 10 `
  --expect-single-sparse
```

Continue from a manually edited Metashape project:

```powershell
& "C:\Path\To\Metashape\metashape.exe" -r scripts\reexport_colmap_from_project.py `
  --project "D:\path\xpano.psx" `
  --export-dir "D:\path\colmap_output"
```

## Verified Smoke Runs

- `_tracks_qinshi_1s_10`: 20/20 aligned, 100 exported cubemap images.
- `_tracks_qinshi_1s_50`: 100/100 aligned, 2 used Fisheye sensors, 50 Folder groups, 500 exported cubemap images, one COLMAP sparse model.
- `_tmp_photo_sensor_split/xpano_manifest.json` probe: 2 same-size standard photos with different EXIF camera identity import as 2 Frame sensors and 0 Fisheye sensors.
- `_tmp_photo_sensor_split/xpano_mixed_manifest.json` probe: 2 panorama station frames + 2 standard photos import as 2 Fisheye sensors + 2 Frame sensors in one chunk; only panorama frame groups are returned as Station candidates.
- `_tmp_mixed_export_probe`: opens the aligned 10-frame qinshi project, temporarily adds 2 Frame photos, and verifies export structure as 100 `cube_` images + 2 `frame_` images + one `sparse/0` COLMAP model with 102 images and 12 camera models.

The mixed probes validate Metashape import structure and sensor typing, not full sparse alignment quality. A full panorama + phone/drone alignment run still needs same-scene photo or aerial data.
