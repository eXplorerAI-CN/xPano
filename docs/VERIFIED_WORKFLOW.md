# Verified xPano Workflow

This document locks the Metashape workflow that has been visually accepted as correct.

## Accepted Test Case

- Input: a local `.osv` dual-fisheye test clip.
- Sampling: `1.0` second/frame
- Regression frame limit: `50`
- Metashape executable used during validation: a local Metashape Pro install.
- Accepted output folder: `_acceptance_qinshi_1s_50`

Acceptance evidence:

- Metashape project: `_acceptance_qinshi_1s_50\work\xpano.psx`
- Aligned cameras: `100 / 100`
- Camera groups: `50`
- Sensors: `2`
- Sensor labels: `dji_left`, `dji_right`
- Exported COLMAP files:
  - `_acceptance_qinshi_1s_50\sparse\0\cameras.bin`
  - `_acceptance_qinshi_1s_50\sparse\0\images.bin`
  - `_acceptance_qinshi_1s_50\sparse\0\points3D.bin`
- Cubemap image count: `500`

## Locked Metashape Steps

The panorama backbone stage must match the README/screenshot workflow:

1. Extract each sampled video time into a folder containing the left and right fisheye JPEGs.
2. Import each frame folder as one Metashape camera group.
3. Set every group type to `Station` before matching and alignment.
4. Set every sensor to `Metashape.Sensor.Type.EquidistantFisheye` when supported, with `Fisheye` only as an older-version fallback.
   Copy the calibration imported from the source image before applying the projection type and fixed parameters.
5. Set sensor pixel size to `0.0024` mm and focal length to `2.5` mm.
6. Set initial `b1`, `b2`, and `k4` to `0`.
7. Fix exactly `["B1", "B2", "K4"]`.
   The parameter names must be uppercase.
8. Match photos with:
   - `downscale=1`
   - `generic_preselection=True`
   - `reference_preselection=False`
   - `filter_stationary_points=False`
   - `guided_matching=False`
   - `keep_keypoints=True`
   - `reset_matches=False`
   - `keypoint_limit=40000`
   - `tiepoint_limit=0`
9. Align cameras with `adaptive_fitting=True`.
10. After successful alignment, switch all groups back to `Folder`.
11. Optimize cameras with `fit_b1=False`, `fit_b2=False`, `fit_k4=False`.
12. Save `work\xpano.psx`.
13. Write `xpano_alignment_summary.txt`.
14. Run ground-plane alignment as a best-effort step.
15. Export COLMAP and cubemap images.

For mixed panorama + ordinary/video/photo projects, the GUI defaults to the
Metashape `backbone` strategy:

1. Import only panorama tracks, set their groups to `Station`, and visually match with retained keypoints.
2. Align the panorama cameras, restore their groups to `Folder`, and optimize the panorama solution.
3. Import ordinary video/photo/aerial tracks as `Frame` sensors.
4. Match again with retained keypoints, then call incremental alignment without resetting the solved panorama cameras.
5. Run a final conservative global optimization.

Legacy `mixed` configuration values are accepted for compatibility and normalized to this staged workflow.

## Do Not Regress

- Do not align already-cut cubemap or ERP images. Alignment must use raw dual-fisheye frames.
- Do not use `Frame` camera type for `.osv` / `.insv` dual-fisheye input.
- Do not use lowercase fixed parameter names.
- Do not enable `filter_stationary_points` in the verified workflow.
- Restore dual-fisheye panorama groups to `Folder` after the panorama solve, before optimization and Frame import.

## GUI Production Behavior

- GUI default sampling is `1.0` second/frame.
- Frame limit is optional. Blank means process all extracted frames.
- For regression testing, set frame limit to `50`.
- The GUI writes:
  - `work\xpano.psx`
  - `xpano_alignment_summary.txt`
  - `xpano_run_summary.json`
  - `images\*.jpg`
  - `sparse\0\cameras.bin`
  - `sparse\0\images.bin`
  - `sparse\0\points3D.bin`

## Regression Command

```powershell
python scripts\run_xpano_job.py `
  --input "D:\path\to\camera.osv" `
  --output ".\_acceptance_qinshi_1s_50" `
  --frames-per-second 1 `
  --max-frames 50 `
  --metashape "C:\Path\To\Metashape\metashape.exe"
```
