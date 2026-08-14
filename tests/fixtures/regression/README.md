# Reconstruction Regression Baselines

Regression metadata is captured from verified, exact 20-frame projects with:

```powershell
python scripts/capture_regression_baseline.py `
  --project "D:\path\to\verified-project" `
  --case-id "metashape-20" `
  --backend metashape `
  --output "tests\fixtures\regression\metashape-20.json"
```

Only counts and SHA-256 values are committed. Source media paths, extracted images, PSX files, and point-cloud binaries stay outside Git.

Verified cases captured on 2026-07-10 from the same 20-frame, 1 second/frame OSV input:

- `metashape-20.json`: 40/40 source fisheye cameras aligned, exported as 200 cubemap images with 14,028 sparse points.
- `colmap-20.json`: 40/40 source fisheye images registered, exported as 200 cubemap images with 2,571 sparse points.
