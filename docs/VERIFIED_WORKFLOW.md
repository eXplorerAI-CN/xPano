# Verified xPano Workflow

This document locks the Metashape workflow that has been visually accepted as correct.

## Accepted Test Case

- Input: `F:\3Dregistration\360TEST\qinshi\CAM_20260615223741_0132_D.OSV`
- Sampling: `1.0` second/frame
- Regression frame limit: `50`
- Metashape executable used during validation: `E:\FastProgram\Metashape\metashape.exe`
- Accepted output folder: `D:\CodeFiles\360gaussain\_acceptance_qinshi_1s_50`

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

The automated pipeline must match the README/screenshot workflow:

1. Extract each sampled video time into a folder containing the left and right fisheye JPEGs.
2. Import each frame folder as one Metashape camera group.
3. Set every group type to `Station` before matching and alignment.
4. Set every sensor to `Metashape.Sensor.Type.Fisheye`.
   In the Metashape UI this corresponds to the equidistant fisheye camera type used by the screenshots.
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
   - `keypoint_limit=40000`
   - `tiepoint_limit=0`
9. Align cameras with `adaptive_fitting=True`.
10. After successful alignment, switch all groups back to `Folder`.
11. Optimize cameras with `fit_b1=False`, `fit_b2=False`, `fit_k4=False`.
12. Save `work\xpano.psx`.
13. Write `xpano_alignment_summary.txt`.
14. Run ground-plane alignment as a best-effort step.
15. Export COLMAP and cubemap images.

## Do Not Regress

- Do not align already-cut cubemap or ERP images. Alignment must use raw dual-fisheye frames.
- Do not use `Frame` camera type for `.osv` / `.insv` dual-fisheye input.
- Do not use lowercase fixed parameter names.
- Do not enable `filter_stationary_points` in the verified workflow.
- Do not leave groups as `Station` after alignment; release them back to `Folder` before optimization/export.

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
  --input "F:\3Dregistration\360TEST\qinshi\CAM_20260615223741_0132_D.OSV" `
  --output "D:\CodeFiles\360gaussain\_acceptance_qinshi_1s_50" `
  --seconds-per-frame 1 `
  --max-frames 50 `
  --metashape "E:\FastProgram\Metashape\metashape.exe"
```
