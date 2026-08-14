# xPano color restoration LUT integration plan

## 1. Outcome

The updated upstream implementation is feasible to integrate, but its path handling and output format must not be copied directly.

The supported behavior will be:

- one optional `.cube` color-restoration LUT per panorama or ordinary-video track;
- the LUT is applied to selected frames during the existing FFmpeg extraction pass;
- panorama left/right streams always receive the same LUT and interpolation;
- prepared previews, thumbnails and Metashape/COLMAP inputs all derive from the same transformed JPEGs;
- no LUT remains the default and preserves the current FFmpeg command/output behavior.

No project schema-version bump or new runtime dependency is required.

## 2. Confirmed evidence

### Upstream behavior

`C:/Users/Beluga/Downloads/pano_extractor_GUI.py` accepts one `.cube` path and builds:

```text
fps=<rate>,lut3d=file='<path>'
```

It applies the same filter to each video stream before JPEG encoding. It validates path existence but does not pre-parse the LUT, persist it, stabilize the output pixel format, or handle the complete Windows path domain.

### Local runtime

- Bundled FFmpeg 8.0.1 includes the slice-threaded `lut3d` filter.
- Tetrahedral interpolation is FFmpeg's default and matches upstream behavior.
- There is no `lut3d_cuda` filter in the bundled build. Hardware acceleration remains decode-only.
- Direct filter interpolation failed for a path containing Chinese text, spaces, comma, brackets and an apostrophe.
- Copying the same LUT to a safe basename and running FFmpeg with that directory as `cwd` succeeded.
- `lut3d` alone negotiates MJPEG from current `yuvj420p` to `yuvj444p`. A trailing `format=yuvj420p` restores current storage/pixel-format semantics.
- A corrected identity LUT was geometrically neutral and changed only two RGB byte values by one level before JPEG encoding.

### Performance boundary

A synthetic six-frame 3840x3840 JPEG run measured:

| Mode | Time | Throughput | JPEG bytes |
|---|---:|---:|---:|
| Current `fps` | 0.649 s | 9.25 fps | 3,279,467 |
| `fps,lut3d,format=yuvj420p` | 1.079 s | 5.56 fps | 3,251,360 |

This is a filter/encode microbenchmark, not an end-to-end camera-video benchmark. It proves enabled LUT processing has a real CPU cost. Because `fps` runs first, the LUT processes extracted frames only, not every decoded source frame. The no-LUT branch must remain unchanged and have zero added processing.

## 3. Design decisions

### Per-track ownership

Add `colorLutPath` to video `ExtractionSettings` as an optional string/null field.

This is preferable to a global setting because xPano supports multiple camera/video tracks in one project. Different tracks may use different recording profiles or no LUT at all. Changing or clearing the field will reuse the existing extraction-settings comparison, marking only that track stale and invalidating downstream reconstruction.

Missing fields in older v3 projects deserialize as `None`; keep project schema version 3.

### Safe FFmpeg boundary

The shared Python extractor owns LUT preparation:

1. Resolve and verify the external source is a regular `.cube` file.
2. Create a temporary directory for one extraction call.
3. Copy the source bytes to the fixed ASCII basename `lut.cube`; never hardlink it.
4. Validate that snapshot once with bundled FFmpeg and `lut3d` before extracting frames.
5. Run extraction with the temporary directory as FFmpeg `cwd` and reference only `lut.cube` inside the filter graph.
6. Keep the snapshot alive across CUDA, D3D11VA and software decoder attempts, then remove it automatically.

This avoids filter-string injection/escaping bugs, supports Unicode paths, and prevents edits to the source LUT from changing later frames within the same job.

### Filter contract

Use exactly:

```text
fps=<rate>,lut3d=file=lut.cube:interp=tetrahedral,format=yuvj420p
```

For no LUT, continue using exactly:

```text
fps=<rate>
```

Do not add a second post-processing pass. That would duplicate disk I/O, recompress JPEGs and make extraction previews temporarily disagree with alignment inputs.

### Failure semantics

- Missing, non-file or non-`.cube` input: reject before starting the media process when possible.
- Invalid `.cube` content: fail before frame extraction with the useful FFmpeg diagnostic and LUT filename.
- LUT failure must never silently fall back to ungraded extraction; that would publish pixels different from the user's selected configuration.
- Decoder failure may continue through the existing CUDA -> D3D11VA -> software fallback. Every attempt uses the same validated LUT snapshot.
- If no LUT is selected, no LUT validation, copy, filter or working-directory change occurs.

## 4. Exact implementation changes

### A. Durable contracts and backend validation

Files:

- `xpano-ui/src-tauri/src/contracts.rs`
- `xpano-ui/src-tauri/src/media.rs`
- `xpano-ui/src/lib/contracts.ts`
- `xpano-ui/src/lib/types.ts`

Changes:

1. Extend Rust `ExtractionSettings` with `#[serde(default)] color_lut_path: Option<String>`.
2. Extend TypeScript extraction types with `colorLutPath?: string | null` so old fixtures/literals remain valid.
3. In project validation, allow the field only on `panoramic_video` and `ordinary_video`; a non-empty value must have a case-insensitive `.cube` suffix.
4. During import and track updates, normalize empty strings to `None`, require the selected path to be an existing file, and persist the path.
5. In `begin_media_job_impl`, recheck LUT paths for targeted tracks before writing the job marker or setting tracks to running. Use existing `missing_source`/`invalid_media_type` command errors.
6. Keep current equality-based invalidation: changing or clearing LUT marks the video track stale, increments media/alignment-input revisions and marks reconstruction stale.

Do not add LUT fields to photo tracks, reconstruction settings or global project settings.

### B. Python extraction core

Files:

- `scripts/xpano_extract.py`
- `scripts/xpano_tracks.py`
- `scripts/run_xpano_prepare_project.py`

Changes:

1. Add a small context-managed LUT preparation helper in `xpano_extract.py` that validates, copies to `lut.cube`, invokes one FFmpeg parse probe, and cleans up.
2. Add a pure `_video_filter(frames_per_second, prepared_lut)` helper. Assert exact no-LUT and LUT strings in tests.
3. Add optional `cwd` propagation to `_run_ffmpeg`; leave it `None` without LUT.
4. Add `color_lut_path=None` to `extract_frames` and `extract_single_video_frames` and keep the prepared snapshot alive around hardware fallback.
5. Apply the same filter string independently to both panorama outputs. Apply it once to ordinary-video output.
6. Extend `build_panorama_track` and `build_ordinary_video_track` with the optional path and pass it to the extractor.
7. In `run_xpano_prepare_project.py`, read `extraction.colorLutPath` and pass it only for video tracks.
8. Keep photo staging, EXIF writing, timestamps, frame naming, thumbnails and manifest geometry unchanged.

The primary GUI preparation path reads project JSON directly, so no new Tauri process argument is required. Legacy non-project CLI LUT flags are intentionally deferred unless a real caller requires them.

### C. Media UI

Files:

- `xpano-ui/src/features/media/MediaWorkspace.tsx`
- `xpano-ui/src/features/media/MaterialImportDialog.tsx`
- `xpano-ui/src/features/media/TrackEditor.tsx`
- `xpano-ui/src/features/media/mediaTypes.ts`

Changes:

1. Initialize new video drafts with `colorLutPath: null`.
2. Add a compact `色彩还原 LUT` file row for video tracks in the import dialog: current basename/`未使用`, a folder icon picker filtered to `.cube`, and an `X` clear action with tooltips.
3. Add the same control to video track settings; never show it for photo folders.
4. Wire the existing ready-track `修改抽帧范围` action to an actual settings mode so LUT/trim/FPS can be changed after first preparation.
5. Share the video extraction settings form between initial and edit states only if doing so removes actual duplicate controls; do not introduce a new settings framework.
6. Saving a changed LUT returns to the normal stale-track flow; re-preparation creates new transformed frames and thumbnails.

The trimmer's source-video preview remains ungraded. Live scrub-time LUT preview is deliberately excluded because it would add repeated FFmpeg work and a second preview pipeline. Live extraction preview and the prepared item grid will show the LUT output.

## 5. Test-first implementation order

### Step 1: Contracts and invalidation RED tests

Rust tests must first fail for:

- opening an old project with no LUT field and obtaining `None`;
- accepting a valid video `.cube` path;
- rejecting a missing file, wrong suffix and LUT on a photo track;
- changing/clearing LUT marks only the target track stale and invalidates reconstruction;
- same path is a no-op;
- a LUT removed after save is rejected before the job marker/status mutation.

Then implement the minimal Rust/TypeScript contract changes.

### Step 2: Extractor RED tests

Python tests must first fail for:

- exact no-LUT filter remains `fps=<rate>`;
- LUT filter order and explicit `yuvj420p` are exact;
- special-character/Unicode source is copied and referenced only as `lut.cube`;
- the snapshot is a copy, so later source edits do not alter it;
- invalid cube fails during preflight before `_run_ffmpeg` extraction;
- panorama commands apply the identical LUT filter to both eyes;
- ordinary-video command applies it once;
- CUDA/D3D11/software retries retain the same filter/cwd;
- no-LUT extraction retains current command arrays and fallback behavior.

Then implement the context helper, filter builder and `cwd` plumbing.

### Step 3: Project preparation RED tests

Extend `tests/test_prepare_project.py` and `tests/test_xpano_tracks.py` to prove:

- project `colorLutPath` reaches both video builders;
- photo tracks ignore/reject LUT state according to the contract;
- prepared timestamps, IDs and relative artifacts are unchanged;
- thumbnails are generated from the LUT-processed outputs;
- a failed LUT leaves no successful media result publication.

### Step 4: Frontend tests and UI

Add a small pure helper only if needed for testability, covering:

- LUT is available for both video types and unavailable for photo types;
- empty/cleared path serializes as null;
- `.cube` suffix matching is case-insensitive;
- the saved extraction patch preserves FPS/frame-limit while changing LUT.

Then implement the import/editor controls and ready-track settings mode. Run lint and production build.

### Step 5: Runtime acceptance

Using bundled FFmpeg on Windows:

1. Prepare a video with no LUT and confirm the no-LUT command/filter remains unchanged.
2. Prepare panorama and ordinary-video fixtures with an identity LUT stored under a Unicode/special-character source path.
3. Confirm output count, dimensions, eye pairing, EXIF/timestamps and preview events are unchanged.
4. Confirm output JPEG `pix_fmt=yuvj420p` and identity-LUT decoded pixels differ only by normal RGB/YUV round-trip noise.
5. Confirm an invalid cube produces an actionable visible error and no ready result.
6. Exercise CUDA or D3D11 failure and confirm software fallback still succeeds with LUT.
7. Compare no-LUT and LUT timing; report the measured cost but do not impose a brittle CI timing assertion.

Finally run full Python, Rust and frontend suites, Python compilation, frontend production build and `git diff --check`.

## 6. Non-goals

- No bundled camera-specific LUT preset or automatic camera/profile detection.
- No LUT intensity slider, interpolation selector or multi-stage LUT stack.
- No LUT application to imported still-photo folders.
- No live LUT rendering in the trimming scrubber.
- No GPU LUT implementation or new CUDA/OpenCL dependency.
- No in-place rewrite of already prepared JPEGs; configuration changes require re-preparation.
- No legacy standalone CLI flags until a real non-project caller needs them.

## 7. Acceptance definition

The feature is complete only when all of the following hold:

- old projects open without migration prompts;
- LUT selection is per video track, persisted and editable;
- no-LUT output/performance path is unchanged;
- special Windows paths work without embedding user paths in FFmpeg filter syntax;
- the LUT is applied once, after `fps`, to both panorama eyes or the ordinary-video stream;
- JPEG output remains `yuvj420p` and geometry/dimensions are unchanged;
- prepared previews/thumbnails and alignment input reference the same transformed files;
- invalid LUTs fail visibly and never degrade silently to no LUT;
- hardware decoder fallback, cancellation, progress and transactional media finalization remain intact.
