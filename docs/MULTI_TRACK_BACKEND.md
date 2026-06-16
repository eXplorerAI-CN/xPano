# xPano Multi-Track Backend

This document describes the current multi-track backend used by the GUI and CLI. A material pool is made of tracks, where each track represents one device/source.

## Track Types

- `panorama_video`: `.osv`, `.insv`, or compatible dual-fisheye video. Uses the verified Station -> Folder xPano workflow.
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
- Sensor type is `Metashape.Sensor.Type.Fisheye`.
- Pixel size is `0.0024`.
- Focal length is `2.5`.
- Fixed params are exactly `["B1", "B2", "K4"]`.
- Each sampled frame creates one CameraGroup with two cameras.
- These groups are switched to `Station` before matching/alignment.
- These groups are switched back to `Folder` before optimization/export.

Photo/aerial track:

- Creates Frame sensors, split by image size and EXIF camera/lens identity when available.
- Sensor type is `Metashape.Sensor.Type.Frame`.
- It is never switched to Fisheye.
- It is never assigned to Station groups.

Unused auto-created Metashape sensors are pruned after import so the project does not contain misleading empty sensors.

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

The current `app.py` GUI builds a manifest internally and calls Metashape with `--manifest`. Internally, the legacy `run_metashape_pipeline(JobConfig)` wrapper also routes into `run_multi_track_pipeline(MultiTrackJobConfig)`, so single-video GUI runs, multi-track GUI runs, and CLI runs share the same backend path. `scripts/run_xpano_tracks_job.py` is a thin CLI wrapper around this app-level runner.

The app-level input model is:

```text
MaterialTrack(track_type, label, paths)
```

where `track_type` is one of:

- `panorama_video`
- `standard_photos`
- `aerial_photos`

`material_tracks_to_job_config(...)` converts a material-track list into `MultiTrackJobConfig`. The GUI material pool maintains this list directly, then calls the shared runner.

The Tkinter GUI keeps a `self.material_tracks` list and exposes a material-track table with:

- `+ 全景视频`
- `+ 普通照片`
- `+ 航拍照片`
- `删除选中`

This is the production bridge to the multi-track backend. Visual polish can continue independently from the Metashape workflow logic.

Single panorama track:

```powershell
python scripts\run_xpano_tracks_job.py `
  --output "_tracks_qinshi_1s_50" `
  --pano "F:\3Dregistration\360TEST\qinshi\CAM_20260615223741_0132_D.OSV" `
  --seconds-per-frame 1 `
  --max-frames 50 `
  --metashape "E:\FastProgram\Metashape\metashape.exe"
```

Mixed panorama + phone photos:

```powershell
python scripts\run_xpano_tracks_job.py `
  --output "_tracks_mixed" `
  --pano "F:\path\camera.osv" `
  --standard-track phone "F:\path\phone_photos" `
  --seconds-per-frame 1 `
  --max-frames 50 `
  --metashape "E:\FastProgram\Metashape\metashape.exe"
```

Mixed panorama + drone photos:

```powershell
python scripts\run_xpano_tracks_job.py `
  --output "_tracks_drone" `
  --pano "F:\path\camera.osv" `
  --aerial-track mavic "F:\path\drone_photos" `
  --seconds-per-frame 1 `
  --metashape "E:\FastProgram\Metashape\metashape.exe"
```

Prepared manifest:

```powershell
python scripts\run_xpano_tracks_job.py `
  --output "_tracks_from_manifest" `
  --manifest "F:\path\xpano_manifest.json" `
  --metashape "E:\FastProgram\Metashape\metashape.exe"
```

Validate a prepared manifest before starting Metashape:

```powershell
python scripts\validate_xpano_manifest.py `
  --manifest "_tracks_qinshi_1s_50\work\xpano_manifest.json"
```

Verify a finished Metashape project:

```powershell
& "E:\FastProgram\Metashape\metashape.exe" -r scripts\diagnose_metashape_project.py `
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
& "E:\FastProgram\Metashape\metashape.exe" -r scripts\reexport_colmap_from_project.py `
  --project "F:\path\xpano.psx" `
  --export-dir "F:\path\colmap_output"
```

## Verified Smoke Runs

- `_tracks_qinshi_1s_10`: 20/20 aligned, 100 exported cubemap images.
- `_tracks_qinshi_1s_50`: 100/100 aligned, 2 used Fisheye sensors, 50 Folder groups, 500 exported cubemap images, one COLMAP sparse model.
- `_tmp_photo_sensor_split/xpano_manifest.json` probe: 2 same-size standard photos with different EXIF camera identity import as 2 Frame sensors and 0 Fisheye sensors.
- `_tmp_photo_sensor_split/xpano_mixed_manifest.json` probe: 2 panorama station frames + 2 standard photos import as 2 Fisheye sensors + 2 Frame sensors in one chunk; only panorama frame groups are returned as Station candidates.
- `_tmp_mixed_export_probe`: opens the aligned 10-frame qinshi project, temporarily adds 2 Frame photos, and verifies export structure as 100 `cube_` images + 2 `frame_` images + one `sparse/0` COLMAP model with 102 images and 12 camera models.

The mixed probes validate Metashape import structure and sensor typing, not full sparse alignment quality. A full panorama + phone/drone alignment run still needs same-scene photo or aerial data.
