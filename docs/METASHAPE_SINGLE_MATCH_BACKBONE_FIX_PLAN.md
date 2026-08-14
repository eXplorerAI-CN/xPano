# Metashape mixed-resolution backbone stability plan

> Superseded on 2026-07-17. Runtime evidence showed that this proposed single-pass strategy regressed alignment quality versus xPano 0.1.0. The implemented contract is now the restored panorama-first, retained-keypoint, incremental Frame workflow documented in `VERIFIED_WORKFLOW.md`.

## 1. Purpose and delivery boundary

This plan repairs the Metashape native assertion reported on build 22170 when a project combines a panorama-video backbone with roughly 4,600 flat photos. The failure occurs after feature detection, inside the second `Chunk.matchPhotos` call, with an assertion containing both image sizes (`2880 2880 6000 4000`).

The implementation target is:

- preserve the current product behavior: panorama cameras establish the backbone first, and flat cameras are solved afterward;
- replace repeated stateful matching with one clean matching pass over all imported cameras;
- keep the existing advanced `mixed` mode semantically unchanged;
- keep panorama-only, flat-only, legacy `--input-root`, export, re-export, training, runtime provisioning, and packaging behavior unchanged;
- make the backend execution plan and UI accurately represent the new runtime stages;
- add fail-fast diagnostics for invalid camera/sensor state before entering native Metashape code;
- verify on a real Metashape runner, not only with Python mocks;
- do not build an installer or bump the release version unless the user separately requests a release.

This document is an implementation plan. It does not authorize silent fallbacks, quality reductions, image count limits, source-image rewriting, or suppression of Metashape errors.

## 2. Established evidence

The implementing agent must preserve these facts and must not restart diagnosis from guesses:

1. Panorama-only alignment succeeds.
2. All reported flat photos finish feature detection at 40,000 points per photo.
3. The exception occurs at `scripts/metashape_pipeline.py` in the second backbone `matchPhotos` call, before flat-camera `alignCameras`.
4. The current first call matches panorama cameras only.
5. The current second call uses `frame_cameras + pano_cameras` while `_match_kwargs` sets `keep_keypoints=True` and `reset_matches=False`.
6. Metashape build 22170 raises a native assertion containing the already-matched panorama size and the newly imported flat-photo size.
7. A real local project on Metashape 2.2.1 build 20221 successfully aligned 334 panorama cameras plus 138 flat cameras of different resolutions. Mixed resolution is supported in principle; the unsafe boundary is repeated stateful matching under the build/data-scale combination.
8. Local Metashape API help confirms:
   - `matchPhotos(cameras=...)` accepts `list[int]` camera keys;
   - `alignCameras(cameras=...)` accepts `list[int]` camera keys;
   - `keep_keypoints` means storing keypoints in the project;
   - `reset_matches` resets current matches.

The product-level root cause is therefore the backbone implementation's assumption that a second, differently ordered, mixed-resolution `matchPhotos` call can safely reuse the first call's state on every supported Metashape build and at large scale. The native assertion is inside Metashape, but xPano owns and must remove the unsafe call pattern.

## 3. Required target algorithm

### 3.1 Mixed panorama plus flat material

The new backbone algorithm must execute exactly this logical sequence:

1. Import panorama tracks and retain their camera objects/keys and Station groups.
2. Import flat tracks and retain their camera objects/keys.
3. Validate that the two camera sets are non-empty as expected, disjoint, sensor-valid, and together cover every camera in the fresh chunk.
4. Set panorama groups to `Station`.
5. Call `chunk.matchPhotos(...)` exactly once for the entire fresh chunk.
6. Call `chunk.alignCameras(cameras=camera_keys(pano_cameras), adaptive_fitting=True)` to solve only the panorama backbone.
7. Keep panorama groups as `Station`.
8. Run the existing conservative panorama optimization with the Station constraint intact.
9. Call `chunk.alignCameras(cameras=camera_keys(frame_cameras), adaptive_fitting=True)` to solve only flat cameras from the already-created cross-material matches.
10. Run the existing conservative global optimization.
11. Continue through save, auto-level, export, and output validation unchanged.

Matching all cameras once does not mean aligning all cameras at once. This distinction is the central correctness contract. The first alignment call must contain only panorama keys; the second must contain only flat-camera keys.

### 3.2 Panorama-only material

1. Import panorama cameras.
2. Set panorama groups to `Station`.
3. Match the fresh chunk once.
4. Align panorama cameras only.
5. Keep Station groups.
6. Run one conservative optimization with the Station constraint intact.
7. Do not emit or execute flat import/alignment work.

### 3.3 Flat-only material

1. Import flat cameras.
2. Match the fresh chunk once.
3. Align flat cameras only.
4. Run one conservative optimization.
5. Do not emit or execute panorama Station/alignment work.

### 3.4 Advanced `mixed` mode

Do not convert advanced `mixed` mode into backbone mode. Its contract remains one import, one combined match, one combined alignment, and one Station-retained optimization. Refactoring shared helpers is allowed only if tests prove its observable call sequence and parameters remain unchanged.

### 3.5 Legacy `--input-root` mode

Do not change it as part of this incident. It is panorama-only and does not contain the failing second matching call.

## 4. Detailed source changes

### 4.1 `scripts/metashape_pipeline.py`

#### A. Make matching policy explicit

Change `_match_kwargs` so callers can explicitly choose keypoint retention without changing existing callers accidentally. A suitable compatible signature is:

```python
def _match_kwargs(args, cameras=None, *, keep_keypoints=True, reset_matches=False):
```

Populate the returned dictionary from these explicit arguments. Preserve the existing camera-object-to-key conversion when `cameras` is provided.

For the new backbone unified match:

- call `_match_kwargs(args, keep_keypoints=False)`;
- do not pass `cameras`, because `main()` creates a new chunk and the pre-match validator must prove that the imported camera union equals `chunk.cameras`;
- keep `reset_matches=False`; this is a fresh chunk with no existing matches;
- do not pass `pairs`; generic visual preselection must continue choosing overlaps;
- preserve `downscale`, keypoint/tiepoint limits, GPU behavior, stationary-point filtering, guided matching, and reference preselection exactly as currently configured.

`keep_keypoints=False` is intentional only for the new one-pass backbone path: there is no later matching pass that needs stored raw keypoints. This reduces saved-project and memory pressure for thousands of photos. Do not change the default for advanced mixed or legacy paths in the same patch.

#### B. Rewrite `run_backbone_alignment`

Do not patch only line 484. Replace the two-match control flow with the target algorithm from section 3.

Recommended implementation shape:

```python
station_groups = []
pano_cameras = []
frame_cameras = []

if manifest contains panorama tracks:
    emit panorama import
    station_groups, pano_entries = import panorama tracks
    flatten pano_entries into pano_cameras

if manifest contains frame track types:
    emit flat import
    _, frame_entries = import frame tracks
    flatten frame_entries into frame_cameras

validate imported sets

if pano_cameras:
    set Station groups

emit unified match
chunk.matchPhotos(**_match_kwargs(args, keep_keypoints=False))

if pano_cameras:
    align only pano keys
    keep Station groups
    optimize panorama backbone with Station constraints

if frame_cameras:
    align only frame keys
    optimize globally
```

Do not call `matchPhotos` inside either alignment branch. The mixed-input path, panorama-only path, and flat-only path must each execute exactly one `matchPhotos` call.

Use a small helper to test whether the manifest contains a track type if that removes duplicated comprehensions. Do not add a generic workflow framework or new class hierarchy.

Add one short `NOTE:` comment immediately before the unified match explaining the non-obvious contract: all visual correspondences are generated once, while camera solving remains staged to preserve the panorama backbone. Do not add retrospective comments about this specific user incident.

#### C. Add a pre-native validation boundary

Add one focused helper, for example `validate_backbone_camera_sets(chunk, pano_cameras, frame_cameras)`. It must run after all imports and before Station assignment/matching.

It must fail with an actionable `RuntimeError` when any of these invariants is violated:

- the chunk contains zero cameras;
- a camera key occurs more than once;
- a camera appears in both panorama and flat sets;
- the union of panorama and flat keys differs from the keys in `chunk.cameras`;
- a camera has no sensor;
- a panorama camera is not assigned to a Fisheye sensor;
- a flat camera is not assigned to a Frame sensor;
- a sensor width or height is non-positive;
- the same sensor object/key is shared between panorama and flat camera sets.

Error messages must include the camera label/path, camera key, sensor label/key/type, and dimensions where applicable. Do not log thousands of normal cameras.

Before matching, emit one bounded diagnostic summary containing:

- total, panorama, and flat camera counts;
- total sensor count;
- each used sensor's label, type, dimensions, and camera count, capped to a reasonable number with a final omitted-count line;
- the effective matching policy (`single_pass`, `keep_keypoints=False`, `reset_matches=False`, keypoint limit, tiepoint limit).

Do not catch and suppress exceptions from `matchPhotos`, `alignCameras`, or validation. A native matching failure is high-impact and must terminate the task visibly; exporting a partial or stale result is forbidden.

#### D. Preserve optimization and alignment semantics

- Keep `adaptive_fitting=True` on both staged alignment calls.
- Keep `fit_b1=False`, `fit_b2=False`, and `fit_k4=False` on both optimizations.
- Never call the second `alignCameras` without a `cameras` argument.
- Never use `reset_alignment=True` for the flat-camera alignment.
- Keep Station groups after panorama alignment succeeds and through both optimizations.
- Do not change fisheye calibration constants, fixed parameters, camera profiles, or export code.

### 4.2 `scripts/xpano_tracks.py` and `scripts/run_xpano_prepare_project.py`

EXIF orientation is a real audit concern, but it is not proven to be the cause of the reported assertion. Treat it as an evidence-gated hardening item, not as a guessed mandatory rewrite.

#### Mandatory probe before changing grouping

Using temporary diagnostic images outside the repository, create:

- a normal landscape JPEG with stored dimensions `6000x4000`, Orientation 1;
- a JPEG with the same stored dimensions and Orientation 6;
- optionally Orientation 8 and a physically rotated `4000x6000` control.

Add them to a temporary Metashape chunk on build 20221 and, when available, build 22170. Record the source sensor width/height Metashape assigns before xPano reassigns sensors.

Decision rule:

- If Metashape reports logical, orientation-adjusted dimensions, update `read_photo_identity` to swap width/height for EXIF orientations 5, 6, 7, and 8 before computing `photo_sensor_key`.
- If Metashape reports stored/raw dimensions, retain raw-dimension grouping; do not change it merely because thumbnails are transposed.
- If builds differ, do not encode one build's assumption into preparation. Use the actual Metashape source-sensor geometry at import time to partition incompatible groups.

Never rotate or re-encode user source photos to solve this. Never use thumbnail dimensions for Metashape sensor grouping.

#### Import-boundary protection

Regardless of the probe outcome, harden `import_photo_track` so it does not blindly assign every camera in a declared `photo_sensors` group to a sensor created from the first camera without checking compatibility.

The safe implementation is:

1. Capture each new camera's Metashape-created source sensor before reassignment.
2. Partition cameras inside the declared camera/lens identity group by source sensor type, width, and height.
3. Create one xPano Frame sensor per actual-geometry partition and assign only compatible cameras to it.
4. Emit one bounded diagnostic when a declared group is split, while keeping missing source sensors and downstream invalid-dimension validation visible.

Do not create one sensor per image. Do not merge partitions across declared groups or discard camera/lens identity. The panorama-plus-photo incident is direct evidence that a declared group can legitimately contain multiple Metashape source geometries, so rejecting the whole project is no longer the correct boundary behavior.

### 4.3 `xpano-ui/src-tauri/src/reconstruction.rs`

Update `metashape_backbone_nodes` so the execution plan represents the new algorithm. Use this exact logical order:

1. `input.validate`
2. `metashape.project.create`
3. `metashape.pano.import` (skip when no panorama)
4. `metashape.frame.import` (skip when no flat material)
5. `metashape.pano.station` (skip when no panorama)
6. `metashape.all.match` (never skipped when input validation succeeded)
7. `metashape.pano.align` (skip when no panorama)
8. `metashape.pano.release` (skip when no panorama)
9. `metashape.pano.optimize` (skip when no panorama)
10. `metashape.frame.align` (skip when no flat material)
11. `metashape.all.optimize` (skip only for panorama-only input, because panorama optimization already ran)
12. save, coordinate processing, image export, COLMAP export, output validation as today.

Remove `metashape.pano.match` and `metashape.frame.match` from newly generated backbone plans. Reuse the already-supported `metashape.all.match` identifier; do not invent another synonymous stage ID.

Recommended weights, summing exactly to 1.00:

| Stage | Weight |
|---|---:|
| input.validate | 0.02 |
| metashape.project.create | 0.02 |
| metashape.pano.import | 0.07 |
| metashape.frame.import | 0.06 |
| metashape.pano.station | 0.02 |
| metashape.all.match | 0.28 |
| metashape.pano.align | 0.12 |
| metashape.pano.release | 0.02 |
| metashape.pano.optimize | 0.07 |
| metashape.frame.align | 0.10 |
| metashape.all.optimize | 0.06 |
| metashape.project.save | 0.02 |
| coordinate.auto_level | 0.02 |
| export.images | 0.05 |
| export.colmap | 0.04 |
| output.validate | 0.03 |

Update dependencies to follow the sequence above. Skipped nodes must still allow their dependent active node to proceed under the existing job engine. Add/adjust Rust tests for all three input combinations and assert total weight is 1.00 within floating-point tolerance.

Do not migrate or rewrite stored plans from old completed/interrupted jobs. New jobs receive the new graph; old jobs retain their recorded graph and event history.

### 4.4 Frontend development fixtures

Update `xpano-ui/src/features/reconstruction/ReconstructionWorkspace.tsx` development preview stages to match the new backbone graph. Update the running preview in `xpano-ui/src/hooks/usePipeline.ts` from `metashape.frame.match` to `metashape.all.match` with accurate text.

The production execution graph already renders backend-provided plan nodes; do not add a second hard-coded production graph. Do not rename the alignment mode, settings fields, or user-visible strategy selector.

### 4.5 Documentation

Update these documents after tests are green:

- `docs/MULTI_TRACK_BACKEND.md`: describe one unified visual matching pass followed by staged panorama and flat alignment.
- `docs/VERIFIED_WORKFLOW.md`: remove the requirement to retain panorama keypoints for a second matching pass; explicitly lock the one-match/two-align invariant.
- `docs/UI_WORKSPACE_REDESIGN_SPEC.md`: replace the old panorama-match then frame-match backbone graph with the new import-all/match-once/staged-align graph.
- `CHANGELOG.md`: add an unreleased entry only when implementation is complete. Do not bump version metadata in this source-fix turn.

Do not change historical release notes or claim build 22170 acceptance before it has actually run.

## 5. Test-first implementation sequence

### Phase A: Add RED Python call-sequence tests

Modify `tests/test_metashape_alignment_modes.py` before product code.

Enhance `FakeChunk` only as much as needed to record:

- exact camera keys passed to each alignment call;
- group types at match and alignment time;
- complete matching kwargs;
- operation ordering.

Add these tests:

1. Mixed backbone imports panorama and flat photos before the first match.
2. Mixed backbone calls `matchPhotos` exactly once.
3. Unified match has no explicit `cameras` or `pairs`, has `keep_keypoints=False`, and retains all existing quality parameters.
4. Panorama groups are Station at unified match time.
5. First alignment receives exactly panorama keys.
6. Station groups remain Station during panorama optimization.
7. Second alignment receives exactly flat keys and never panorama keys.
8. Mixed backbone performs two optimizations in the expected order.
9. Panorama-only performs one match, one panorama alignment, one optimization, and no flat stages.
10. Flat-only performs one match, one flat alignment, one optimization, and no panorama stages.
11. Advanced mixed mode keeps its call sequence and matching/alignment kwargs while retaining Station groups through optimization.
12. Camera-set validation rejects duplicate keys, cross-set overlap, missing sensors, wrong sensor types, non-positive dimensions, cross-type shared sensors, and uncovered chunk cameras.
13. Import partitions one declared photo sensor group by actual Metashape source-sensor geometry.

The RED state must demonstrate that the existing implementation makes two match calls for mixed backbone input and imports flat photos after the first match.

### Phase B: Implement the Python algorithm

Implement only enough code to make the RED tests green. Do not modify the advanced mixed workflow to share code unless duplication becomes materially harmful.

Run the focused suite after each meaningful change:

```powershell
python -m unittest tests.test_metashape_alignment_modes tests.test_xpano_tracks tests.test_prepare_project
```

Use the repository's active Python command if it differs; do not install new developer dependencies merely to run pytest.

### Phase C: Update backend plan tests

In `xpano-ui/src-tauri/src/reconstruction.rs`, add exact-order tests for:

- panorama plus flat backbone;
- panorama-only backbone, including skip reasons;
- flat-only backbone, including skip reasons;
- advanced mixed mode remaining unchanged;
- weight sum exactly 1.00 within tolerance;
- absence of `metashape.pano.match` and `metashape.frame.match` from new backbone plans;
- presence of exactly one `metashape.all.match` node.

Run focused Rust tests first, then the complete Rust suite.

### Phase D: Evidence-gated EXIF tests

Only after the Metashape orientation probe:

- add JPEG helpers that can write EXIF Orientation values;
- add `read_photo_identity` and grouping tests matching the observed Metashape behavior;
- add preparation-manifest tests proving landscape/portrait grouping is correct;
- add import validation tests for incompatible source sensor dimensions.

If the probe shows raw dimensions on all supported builds, do not add speculative logical-dimension swapping tests.

### Phase E: Full source verification

At minimum run:

```powershell
python -m unittest discover -s tests -p "test_*.py"
cargo test --manifest-path xpano-ui/src-tauri/Cargo.toml
npm --prefix xpano-ui test
npm --prefix xpano-ui run lint
npm --prefix xpano-ui run build
python -m compileall scripts
git diff --check
```

Review the diff and confirm no generated `target*`, runtime payload, binaries, installer, source media, project output, or unrelated dirty file was staged or edited.

## 6. Real Metashape acceptance matrix

Mock tests cannot validate a native Metashape assertion. A code agent must not report this incident fixed until real-runner evidence exists.

### 6.1 Local build 20221 regression acceptance

Use a copy or new output location. Never overwrite the known-good project.

Run:

- panorama-only real material;
- flat-only real material;
- panorama plus 138-photo known-good material;
- a small exact-dimension reproduction containing `2880x2880` panorama images and overlapping `6000x4000` flat photos if such material is available.

Required observations:

- log shows one matching invocation/stage;
- panorama alignment occurs before flat alignment;
- all expected cameras reach the saved PSX;
- panorama camera aligned count does not regress from the previous baseline;
- COLMAP/image export and output validation complete;
- the saved PSX opens in Metashape.

### 6.2 Build 22170 incident acceptance

This is the authoritative gate. Use the user's failing material or a faithful copy, with approximately 4,600 flat photos, on build 22170 and a 32 GB machine.

Record:

- Metashape version/build;
- panorama and flat camera counts;
- sensor types and dimensions;
- keypoint/tiepoint limits;
- total elapsed time and peak process memory;
- exact matching-stage log showing only one matching pass;
- panorama aligned count and flat aligned count separately;
- output validation result and PSX open result.

Acceptance requires:

- no native dimension assertion;
- no automatic quality reduction or image omission;
- no second `matchPhotos` call;
- no reset/re-alignment of already solved panorama cameras during the flat alignment call;
- no partial export after failure;
- successful save and downstream export;
- panorama backbone quality not worse than the panorama-only baseline. Do not require 100% flat alignment if some photos genuinely have no overlap; report the rate and investigate material-specific failures separately.

If build 22170 is unavailable to the implementing agent, the result must be reported as “source and build-20221 verified; build-22170 acceptance pending”, not “fully fixed”.

### 6.3 Repeatable acceptance evidence verifier

Use `scripts/verify_metashape_backbone_acceptance.py` after the real job and after reopening the saved project with the exact Metashape executable. The verifier is read-only: it checks the captured stdout log, non-empty PSX path, alignment summary, images, and COLMAP binaries. It does not open, mutate, or repair a project.

First reopen the PSX through the same native runner and record the expected total counts:

```powershell
& $metashapeExe -r scripts/diagnose_metashape_project.py `
  --project $projectPath `
  --expect-cameras $expectedCameras `
  --expect-aligned $expectedAligned `
  --expect-panorama-cameras $expectedPanoramaCameras `
  --expect-panorama-aligned $expectedPanoramaAligned `
  --expect-frame-cameras $expectedFrameCameras `
  --expect-frame-aligned $expectedFrameAligned `
  --expect-sensors $expectedSensors
```

Then run the evidence verifier with values from that run and the manifest. Do not copy the nearby-build values into a build-22170 acceptance command.

```powershell
python scripts/verify_metashape_backbone_acceptance.py `
  --log $stdoutLog `
  --project $projectPath `
  --output $exportDir `
  --expect-cameras $expectedCameras `
  --expect-aligned $expectedAligned `
  --expect-panorama-cameras $expectedPanoramaCameras `
  --expect-panorama-aligned $expectedPanoramaAligned `
  --expect-frame-cameras $expectedFrameCameras `
  --expect-frame-aligned $expectedFrameAligned `
  --expect-sensors $expectedSensors `
  --expect-cube-images $expectedCubeImages `
  --expect-frame-images $expectedFrameImages `
  --expect-colmap-images $expectedColmapImages `
  --expect-colmap-cameras $expectedColmapCameras `
  --expect-colmap-points $expectedColmapPoints
```

It fails when the log has a native assertion/traceback/exception marker, has anything other than one native `MatchPhotos:` line, lacks the required staged Backbone order, has no Backbone alignment summary, has unexpected aggregate or per-type counts, references a different PSX, lacks a valid PSX path, or has an incomplete export. Every expected count is mandatory so a stale or partially specified run cannot become authoritative evidence. It intentionally does not decide whether a lower flat-camera alignment rate is acceptable; choose and record the expected count from the material-overlap review.

## 7. Failure handling and rollback rules

- Do not catch the assertion and continue.
- Do not silently switch the user from backbone to advanced mixed mode.
- Do not retry matching in the same chunk after a native assertion; its in-memory matching state cannot be trusted.
- Do not write an incomplete PSX to the final successful artifact path as a fallback.
- Do not delete the user's source media, existing PSX, or prior export.
- Real acceptance must use a separate project/output directory.
- If unified matching still fails, preserve the complete log and diagnostic summary, then stop. A fresh-chunk retry strategy requires a separate design and explicit authorization.

## 8. Common mistakes a weaker implementation agent may make

The implementing agent must explicitly check its diff against every item below.

1. **Only reverse the list order.** Changing `frame + pano` to `pano + frame` may hide the assertion but still depends on unsafe repeated cache reuse.
2. **Keep two match calls and set `reset_matches=True`.** This duplicates expensive work, destroys the intended state boundary, and is not the selected architecture.
3. **Match only flat cameras in the second stage.** Flat cameras then have no matches to panorama anchors and may form an unrelated coordinate system.
4. **Align all cameras after unified matching.** Calling `alignCameras()` without camera keys converts backbone mode into advanced mixed mode and allows flat photos to influence the initial panorama solution.
5. **Reset panorama alignment.** `reset_alignment=True` or including panorama keys in the flat alignment call invalidates the backbone contract.
6. **Release Station groups before panorama alignment.** This removes the paired-fisheye constraint too early.
7. **Convert Station groups to Folder during final flat/global solve.** This removes the paired-fisheye constraint and can split the rig; do not do this.
8. **Generate every possible pair.** Explicit all-to-all `pairs` is quadratic and can be catastrophic for 4,600 photos. Keep generic visual preselection.
9. **Batch `matchPhotos` calls without a proven merge contract.** Repeated batches can recreate the same cache bug or discard earlier matches.
10. **Reduce keypoint limit, downscale images, cap photos, or disable GPU.** Those are quality/performance changes and do not fix the state bug.
11. **Physically rotate or recompress user photos.** This wastes time, can reduce quality, and mutates source semantics.
12. **Use thumbnail dimensions.** Thumbnails are EXIF-transposed presentation artifacts, not authoritative Metashape sensor geometry.
13. **Group sensors only by width and height.** Camera/lens/focal identity must remain part of grouping.
14. **Create one sensor per photo.** This fragments calibration and can materially degrade alignment.
15. **Keep assigning an incompatible group to the first camera's sensor.** Partition by actual geometry before reassignment; never force incompatible cameras onto the first sensor.
16. **Pass Camera objects to Metashape APIs that require keys.** Both `matchPhotos(cameras=...)` and `alignCameras(cameras=...)` use integer keys on the verified API.
17. **Change `_match_kwargs` defaults globally.** Legacy and advanced paths must not change accidentally.
18. **Emit the old stage graph.** Runtime, durable job events, backend plan, and UI must agree that matching is unified.
19. **Rewrite old persisted plans.** Historical jobs must retain their original stage graph.
20. **Edit generated copies.** Never edit `target`, `target-codex-test`, staged release trees, installed resources, or packaged script copies instead of source files.
21. **Treat fake-unit success as native acceptance.** The original error exists inside Metashape C++ and requires a real runner.
22. **Claim build 22170 compatibility using build 20221 only.** State the remaining gate honestly.
23. **Swallow validation/native errors and export anyway.** A partial camera solution is high-impact and must fail fast.
24. **Demand 100% flat-photo alignment.** Non-overlapping photos can legitimately remain unaligned; compare against dataset baselines and inspect failures.
25. **Build or publish an installer without authorization.** Finish source verification and real acceptance first.

## 9. Completion checklist for the code agent

- [ ] RED tests demonstrated the old two-match behavior.
- [ ] Mixed backbone imports all selected cameras before matching.
- [ ] Every backbone input combination calls `matchPhotos` exactly once.
- [ ] Unified matching uses existing quality parameters and no explicit all-to-all pairs.
- [ ] Panorama and flat alignment calls receive disjoint, exact integer key sets.
- [ ] Station and optimization ordering is correct.
- [ ] Pre-native validation produces actionable errors.
- [ ] Photo sensor reassignment cannot silently combine incompatible source geometry.
- [ ] EXIF behavior was probed before any orientation grouping change.
- [ ] Backend execution plan, runtime events, UI fixtures, and docs agree.
- [ ] New backbone plan has one `metashape.all.match` and weights sum to 1.00.
- [ ] Advanced mixed and legacy modes retain their behavior.
- [ ] Python, Rust, frontend, lint, build, compile, and diff checks pass.
- [ ] Real build-20221 regression acceptance passes.
- [ ] Real build-22170 failing-dataset acceptance passes, or is explicitly marked pending.
- [ ] No source media, generated output, release runtime, installer, or unrelated dirty file was modified.
- [ ] No release/version bump occurred without a separate user request.
