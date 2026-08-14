# Metashape Component inventory and export repair plan

## 1. Objective

Repair Component handling without changing alignment, camera calibration, image extraction or COLMAP format:

- enumerate every Component from the current PSX with truthful camera and tie-point counts;
- report the global aligned-camera rate as the union of cameras aligned in any Component;
- automatically choose the Component with the most aligned cameras after a new alignment;
- let the user inspect the current PSX and confirm a Component before re-export;
- activate the selected Component before ground leveling and export;
- never mix cameras or tie points from different Components;
- preserve the user's saved PSX active Component and reuse the existing transactional re-export publication.

This plan does not merge Components, retry alignment, repair bad matches or package a release.

## 2. Confirmed defect

Metashape 2.3 scopes `camera.transform` and `chunk.tie_points` to `chunk.component`.
The current helper reads all cameras and Components without switching the active Component. As a result:

1. inactive Components are reported with zero aligned cameras and zero tie points;
2. the largest-Component selector can choose from false counts;
3. export filters `camera.transform` before activating the requested Component;
4. ground leveling can read tie points from a different Component than the one exported;
5. the frontend only sees the stale report persisted before a user manually edits the PSX.

The known PSX evidence is three Components with 458, 221 and 177 aligned cameras, 24 unaligned cameras, and 132270, 94262 and 74148 tie points. Current xPano exposes only the active 458-camera result.

## 3. Design decision

Use one Metashape-aware activation boundary in Python, plus a read-only PSX inspection command for the UI.

```text
new alignment
  -> inspect every Component by temporary activation
  -> choose largest Component
  -> activate chosen Component
  -> level + export
  -> restore original active Component
  -> persist completed export report

PSX re-export
  -> read-only inspect current PSX
  -> if multiple: user confirms target
  -> start existing transactional re-export
  -> re-open PSX and strictly revalidate target key
  -> activate target, export, restore
  -> atomically publish outputs and report
```

Do not add a paused job waiting for user input. Inspection and export remain two explicit operations, which keeps cancellation, rollback and project revisions simple.

## 4. Python ownership boundary

### 4.1 `scripts/component_selection.py`

Replace the current camera-object-only inventory with helpers that accept a Metashape `chunk`:

- `component_key(component) -> str`
- `available_components(chunk) -> list`
- `activated_component(chunk, component_key)` context manager
- `inspect_components(chunk) -> ComponentInspection`
- `resolve_component_key(inspection, requested=None, strict=False) -> str`

`activated_component` must:

1. capture the original `chunk.component` object;
2. resolve keys by exact string equality without assuming numeric or contiguous keys;
3. assign the target object to `chunk.component`;
4. verify the active key after assignment;
5. yield only after successful activation;
6. restore the original object in `finally`;
7. fail the operation if restoration fails.

`inspect_components` must activate each native Component and capture:

- `componentKey`;
- optional `label` when exposed by Metashape;
- `alignedCameraCount`, from cameras with a transform while that Component is active;
- `tiePointCount`, from `chunk.tie_points.points` while active;
- aligned camera keys internally, used to compute a unique global union;
- `isInitiallyActive` for diagnostics only.

Top-level inspection fields:

```json
{
  "schemaVersion": 2,
  "inventoryComplete": true,
  "totalCameras": 880,
  "alignedCameras": 856,
  "unalignedCameras": 24,
  "defaultComponentKey": "12",
  "components": [],
  "warnings": []
}
```

Sort Components by:

1. descending `alignedCameraCount`;
2. descending `tiePointCount`;
3. ascending stable `componentKey`.

For Metashape versions that do not expose `chunk.components`, return the active Component as `__all__`, set `inventoryComplete=false`, and emit an observable compatibility warning. Do not pretend that multiple Components were fully inspected.

An explicit re-export request is strict: a missing key is an error. An initial alignment has no explicit request and chooses `defaultComponentKey`.

### 4.2 `scripts/metashape_pipeline.py`

After alignment and PSX save:

1. inspect Components once;
2. select the largest valid Component;
3. compute global alignment metrics from the inspection union rather than current transforms;
4. create the in-progress report with the full inventory;
5. enter `activated_component(selected_key)`;
6. run `align_ground_plane.main` while selected tie points are active;
7. run image/COLMAP export while selected camera transforms are active;
8. exit the context and verify restoration;
9. mark the report complete.

Update `emit_alignment_rate` and panorama/Frame aligned counts so they use the union of per-Component camera keys. The UI's overall rate must mean “aligned in any Component”, while the selected Component count describes what was exported.

Add report fields without removing existing fields:

- `inventoryComplete`;
- `unalignedCameras`;
- `selectedComponentAlignedCameras`;
- truthful `components`;
- warning when multiple Components exist and only one is exported.

### 4.3 `scripts/export_colmap.py`

Move Component activation ahead of every use of:

- `camera.transform`;
- `chunk.tie_points`;
- camera projections;
- point filtering;
- coordinate transforms derived from the selected reconstruction.

The export core should operate on the already active Component. The public `run_mixed_export` wrapper may activate a requested key for standalone use, but must not rebuild inventory from inactive transforms.

Remove `camera_belongs_to_component` as the primary export filter. Once the requested Component is active, transformed cameras are the authoritative export set. Membership metadata may be used only as a consistency assertion.

### 4.4 `scripts/reexport_colmap_from_project.py`

On every re-export:

1. open the PSX;
2. inspect current Components;
3. strictly validate `--component-key` when provided;
4. otherwise choose the largest Component;
5. activate it before export;
6. produce the same schema-v2 report as initial alignment;
7. restore the PSX's original active Component without saving the temporary activation.

If the Component changed between UI inspection and export, fail before writing publishable outputs. The existing staging transaction will then retain the previous valid export.

### 4.5 New read-only inspector

Add `scripts/inspect_metashape_components.py`:

- arguments: `--project`, `--output`;
- imports only Metashape, stdlib and `component_selection`;
- does not import `cv2`, NumPy, export code or alignment code;
- opens the PSX read-only in practice: it never calls `doc.save`;
- writes schema-v2 JSON atomically as UTF-8;
- restores the original active Component before exit;
- returns non-zero for corrupt PSX, no usable Component, activation failure or output failure.

Writing a JSON file avoids stdout encoding differences in embedded Metashape Python.

## 5. Rust/Tauri changes

### 5.1 Read-only command

Add an async Tauri command in `xpano-ui/src-tauri/src/reconstruction.rs`:

```text
inspect_metashape_components(
  projectRoot,
  expectedRevision,
  metashapePath
) -> ComponentInspection
```

The command must:

1. validate project revision and current PSX using the existing re-export validation;
2. validate the explicitly selected Metashape executable;
3. run `metashape.exe -r inspect_metashape_components.py` in `spawn_blocking`;
4. pass paths as `Command` arguments, never through a shell string;
5. suppress a Windows console window using the existing process pattern;
6. write to a UUID-named temporary JSON under `work`;
7. validate schema, unique keys, counts, default-key membership and `aligned <= total`;
8. delete the temporary file on success and best-effort on failure;
9. return structured `revision_conflict`, `backend_unavailable`, `artifact_corrupt` or `job_conflict` errors;
10. never mutate the project or current export report.

Register the command in `lib.rs`. Extract only the small shared hidden-process/runtime configuration needed by both pipeline and inspection; do not create a generic process framework.

### 5.2 Report persistence

Keep `validated_alignment_report` strict. Extend validation for schema v2 while accepting schema v1 reports from existing projects.

On successful initial export or re-export, persist:

- the full completed report;
- `selectedComponentKey` matching the exported result.

Read-only inspection must not overwrite `alignmentReport`, because that report describes the currently published COLMAP/images, not a proposed next export.

### 5.3 Execution-plan truthfulness

Add a real Component stage to generated plans:

- initial alignment: `metashape.component.select` between project save and coordinate leveling;
- re-export: `metashape.component.validate` between PSX open and image export.

Rebalance weights without changing total 1.0. The separate read-only UI inspection uses a local loading state rather than pretending to be part of the later export plan.

## 6. Frontend flow

### 6.1 Monitor state

In `ReconstructionMonitor`:

- show overall alignment as unique aligned cameras / total cameras;
- show `当前导出：Component #key · N 相机` separately;
- show the multiple-Component quality warning;
- stop treating the persisted selector as a live view of the PSX.

### 6.2 Re-export interaction

When the user clicks “从 PSX 重新导出”:

1. set a local `inspectingComponents` state and disable conflicting actions;
2. invoke `inspect_metashape_components` against the current PSX;
3. if one Component exists, continue with it after a concise confirmation/toast;
4. if several exist, open a compact selection dialog;
5. preselect `defaultComponentKey` (largest aligned camera count);
6. show each key, aligned-camera count and tie-point count;
7. explain that only the selected Component is exported and manual merging in Metashape is recommended when quality is poor;
8. on confirmation, build the existing re-export plan and pass the confirmed key;
9. on cancellation, make no project or output changes.

The dialog should use radio rows, not another generic settings card. The primary action is “导出所选 Component”; the secondary action is cancel. Do not expose raw Python/Metashape diagnostics in the default view.

If strict revalidation later says the key disappeared, show “PSX 中的 Component 已变化，请重新读取后再导出” and keep previous outputs intact.

## 7. Tests first

### 7.1 Python RED tests

Extend `tests/test_component_selection.py` with a fake chunk whose transforms and tie points change when `chunk.component` changes:

- inventories 458/221/177 rather than only the initially active Component;
- computes unique global aligned count and unaligned count;
- sorts by aligned count, then tie points, then key;
- restores the original Component after success;
- restores after an exception during inspection;
- fails when activation or restoration cannot be verified;
- marks fallback inventory incomplete when native Components are unavailable;
- rejects an invalid explicit key but defaults to the largest when no key is requested.

Add export/pipeline tests proving:

- selected inactive Component is activated before transform filtering;
- leveling runs with the selected Component active;
- export contains only that Component's cameras and points;
- original active Component is restored after success and failure;
- schema-v2 report counts all aligned Components but identifies one exported Component;
- re-export fails before publication when the requested key no longer exists.

### 7.2 Rust RED tests

Add tests for:

- schema-v2 inspection parsing and validation;
- duplicate keys, missing default, zero usable cameras and invalid totals;
- project revision conflict and missing PSX before process launch;
- exact Metashape command arguments with spaces and Chinese paths;
- schema-v1 completed reports remaining loadable;
- execution plans exposing the new select/validate nodes with weights totaling 1.0.

### 7.3 Frontend RED tests

Extract a small pure Component selection view model and test:

- largest Component is preselected;
- one Component bypasses the dialog;
- multiple Components require confirmation;
- current exported Component remains distinct from the proposed target;
- inspection failure and changed-key errors remain actionable;
- cancelling the dialog does not start re-export.

### 7.4 Release staging

Add `inspect_metashape_components.py` to the required staged resources and fixture assertions in `tests/test_release_staging.py`.

## 8. Static and native acceptance

Run, in order:

1. focused Component Python tests;
2. focused Rust inspection/report/plan tests;
3. frontend Component view-model tests;
4. full Python, Rust and frontend suites;
5. frontend lint and production build;
6. Python compile checks and `git diff --check`;
7. native read-only inspection of the known PSX.

Native acceptance for the known PSX must report:

- Components: 458, 221 and 177 aligned cameras;
- global aligned cameras: 856 / 880;
- unaligned cameras: 24;
- tie points: 132270, 94262 and 74148 for the corresponding active Components;
- original `chunk.component` restored after inspection.

Then perform one re-export of a non-initially-active Component and verify:

- report `selectedComponentKey` matches the request;
- COLMAP image count matches that Component's aligned/exportable camera count after cubemap expansion rules are applied;
- no camera from another Component appears;
- previous outputs survive a forced invalid-key failure;
- the UI lists all Components and displays the exported one separately.

Do not build an installer unless separately requested.

## 9. Common implementation mistakes to avoid

- Counting `camera.transform` without first activating each Component.
- Reading `component.tie_points` instead of active `chunk.tie_points`.
- Activating the Component only after cameras or points were collected.
- Filtering solely by `camera.component` and assuming it is globally reliable across Metashape versions.
- Summing per-Component counts without deduplicating camera keys for the global rate.
- Silently falling back when an explicit re-export key is missing.
- Saving the PSX after temporary Component activation and changing the user's active Component.
- Restoring only on success instead of in `finally`.
- Running ground leveling before activating the selected Component.
- Updating the completed export report during read-only inspection.
- Assuming Component keys are indices, numeric, stable across merge, or ordered by size.
- Trusting a stale frontend dropdown after manual PSX edits.
- Combining inspection and user selection into a paused background job.
- Launching Metashape through a shell command string or blocking the Tauri UI thread.
- Importing heavy optional dependencies into the inspection script.
- Publishing partial re-export outputs outside the existing transaction.
