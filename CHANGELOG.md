# Changelog

All notable xPano release changes are recorded here. Versions follow Semantic Versioning.

## [2.0.1-4kfix] - 2026-07-28

### Fixed

- Normalized dual-fisheye focal calibration to the actual per-lens resolution so 4K sources no longer inherit the 8K pixel focal length.
- Corrected Metashape fisheye tangential-distortion projection during cubemap export.
- Rejected resolution-incompatible legacy fisheye calibration before export instead of producing images with fisheye distortion or large black borders.

### Verification

- Added 4K/8K calibration, Metashape compatibility, remap coverage, legacy-project rejection and release-staging regression coverage.
- Accepted the source with 325 Python tests, 117 Rust tests, 54 frontend tests, lint and a production frontend build.

## [2.0.0-preview] - 2026-07-17

### Changed

- Restored the initial-release panorama calibration bootstrap and the panorama-first, flat-camera incremental Metashape workflow, with execution plans and UI progress matching the native stages.
- Replaced the stale persisted Component selector with live, read-only PSX inspection and an explicit multi-Component confirmation flow before re-export.

### Fixed

- Enumerated every Metashape Component under its active scope, computed the global aligned-camera union, and kept the selected Component active through ground leveling and COLMAP/image export.
- Strictly revalidated Component keys during transactional PSX re-export so a changed project cannot silently export another Component or overwrite the previous valid result.
- Added schema-v2 inventory validation, Unicode-safe asynchronous inspection, and release-staging protection for the new Component inspection entrypoint.

### Verification

- Accepted the source with 288 Python tests (one intentional skip), 100 Rust tests, 50 frontend tests, lint, production build, Python compilation and release hygiene checks.
- Reproduced the known 458/221/177 Component inventory and 856/880 global aligned count under both Metashape 2.2.1 and 2.3.0 without modifying the PSX.

## [1.0.0-preview] - 2026-07-15

### Changed

- Reworked the Gaussian training workspace into clear setup, running, result and recovery states while preserving the existing LichtFeld parameter protocol.
- Consolidated the accepted mixed-material camera identity, Station retention, Component selection and PSX re-export workflow for preview release testing.

### Fixed

- Normalized bundled LichtFeld Studio's Windows executable argument before launch so Tauri verbatim paths cannot break MinGW resource lookup and leave the LFS UI incomplete.
- Kept drive and UNC path conversion in the shared tool resolver, covering the existing project/runtime path callers without adding a dependency.

## [0.2.9-preview-cameraidentityfix] - 2026-07-14

### Fixed

- Identified newly imported Metashape cameras by stable Camera keys instead of relying on `chunk.cameras` list position after native reordering.
- Propagated exact panorama and flat-camera objects through both Backbone and `mixed` imports, with fail-fast camera-count and source-path validation before sensor assignment.

### Verification

- Reproduced the previous panorama/flat overlap failure with a reordering regression, then passed the corrected mixed-material flow in native Metashape 2.2.1, including a Unicode-path probe.
- Accepted the release source with 283 Python, 91 Rust, and 44 frontend tests plus lint, compilation, production build and DLL-closure gates.

## [0.2.9-preview-7142057camerafix] - 2026-07-14

### Fixed

- Allowed a manifest-declared ordinary-photo sensor group to contain multiple actual Metashape source geometries by creating one compatible Frame calibration sensor per geometry partition.
- Preserved manifest camera/lens identity boundaries, missing-sensor errors, and downstream sensor validation without changing matching, Station, alignment, optimization, Component selection, or export behavior.

### Verification

- Added a landscape/portrait regression and accepted the release source with 280 Python, 91 Rust, and 44 frontend tests plus lint, compilation, and production build checks.

## [0.2.9-preview] - 2026-07-14

### Fixed

- Kept dual-fisheye panorama groups as Metashape `Station` constraints through mixed-material optimization and export; ordinary photo groups remain `Folder`.
- Prevented failed or partial Metashape runs from being finalized or exported as successful reconstructions, while preserving an explicit PSX re-export path.
- Added Component-aware export selection and truthful alignment-rate/warning reporting for multi-Component projects.
- Hardened bundled runtime activation and Windows dependency closure checks across Metashape, Python, COLMAP, FFmpeg, and LichtFeld entrypoints.

### Packaging

- Published the reviewed source and bundled-runtime pipeline as the `0.2.9-preview` Windows x64 NSIS installer.

## [0.2.8] - 2026-07-14

### Fixed

- Replaced the mixed-resolution Backbone's repeated stateful Metashape matching with one unrestricted all-camera match followed by isolated panorama and incremental flat-camera alignment.
- Added one bounded retry for only the panorama cameras left unaligned by Metashape's first non-deterministic solve, reusing the existing matches without reopening the mixed-resolution crash path.
- Preserved every flat camera's enabled state when the panorama solve succeeds or fails, and stopped downstream alignment after a native panorama failure.
- Added per-type panorama and flat-camera alignment metrics so aggregate totals cannot hide a degraded panorama backbone.

### Verification

- Bound authoritative acceptance evidence to the exact PSX and made all expected alignment and export counts mandatory.
- Verified the available 472-camera mixed project on Metashape build 21778 with 472/472 cameras aligned, one native `MatchPhotos` call and complete exports. Exact build 22170 incident-scale verification remains pending because that runner and dataset are unavailable locally.

## [0.2.7] - 2026-07-13

### Fixed

- Removed clean-machine dependence on an installed Visual C++ Redistributable by deploying a hash-locked six-DLL runtime beside bundled COLMAP and Python.
- Replaced densification `PYTHONPATH` dependence with explicit script-local package and DLL activation, while disabling user site-packages for bundled Python children.
- Made Runtime Readiness start FFmpeg, ffprobe, COLMAP and LichtFeld Studio and report loader diagnostics before a job begins.
- Added the previously ambient `tqdm` dependency to the embedded application runtime and offline wheel set.

### Packaging

- Added recursive PE dependency gates for embedded Python, COLMAP and LichtFeld Studio using a fixed Windows system allowlist.
- Added an explicit Windows N/KN Media Feature Pack diagnostic for OpenCV's non-redistributable OS dependency.

## [0.2.6] - 2026-07-13

### Fixed

- Replaced the final environment-only Metashape runtime handoff with explicit, validated CLI arguments from Tauri through the xPano Python job and into every production `metashape.exe -r` entrypoint.
- Preserved the verified site-packages directory in reconstruction job configuration, so Metashape builds that filter custom environment variables can still load the bundled NumPy/OpenCV wheels.
- Applied the same explicit runtime path to normal alignment, legacy single-video jobs and PSX re-export.

### Packaging

- Made release staging reject packages missing either production Metashape entrypoint in addition to the runtime activator and probe.

## [0.2.5] - 2026-07-13

### Fixed

- Fixed Metashape builds that ignore `PYTHONPATH` for `-r` scripts: xPano now activates its verified external NumPy/OpenCV runtime inside the probe and alignment scripts before importing native dependencies.
- Registered the external NumPy and OpenCV DLL directories through the running Metashape Python process, so Windows native-module loading no longer depends on launcher environment propagation.

## [0.2.4] - 2026-07-13

### Fixed

- Replaced the standalone Metashape Python dependency check with an actual `metashape.exe -r` import probe, matching the process that performs panorama alignment and export.
- Provisioned the locked offline NumPy/OpenCV wheels until the real Metashape runner imports both successfully; failures now stop before alignment with the captured runner diagnostic.
- Supplied verified NumPy and OpenCV DLL directories only to the isolated Metashape child process, preventing Windows extension-loading failures without leaking xPano GUI libraries into Metashape.
- Made release staging reject an installer missing either Metashape runtime probe component.

### Packaging

- Updated the Windows installer to 0.2.4 with the complete normal application runtime and offline Metashape NumPy/OpenCV ABI wheel set.

## [0.2.3] - 2026-07-12

### Added

- Added a stable export-only action that regenerates training images and COLMAP data directly from an existing completed Metashape PSX while preserving manual camera corrections.
- Added source/sensor/export-contract image caching with selective invalidation, corruption detection and transaction-safe publication.

### Changed

- Accelerated fisheye-to-Cubemap export through measured OpenCL/OpenCV remapping with observable OpenCV CPU and NumPy compatibility fallbacks.
- Reduced first full export time on the 1,013-camera acceptance project from about 21 minutes to about 6 minutes, with hot PSX re-export reduced to about one minute.
- Added per-stage export metrics and removed redundant source copies and repeated projection reads.

### Fixed

- Preserved existing reconstruction results through interrupted PSX re-export using staged validation, persistent transaction markers and automatic rollback recovery.
- Made manually selected quoted or PATH-resolved Metashape executables authoritative throughout readiness checks, planning and execution.
- Required the new export acceleration/cache modules in formal release staging so incomplete installers fail before publication.

### Packaging

- Bundled all normal application dependencies and supported Metashape ABI wheels for offline operation; the optional multi-gigabyte densification runtime remains separately provisioned.

## [0.2.2] - 2026-07-11

### Fixed

- Fixed installed frame extraction failing because isolated bundled Python could not import the top-level `scripts` package outside the source repository.
- Added installed-style entrypoint regression coverage for every Python command launched by the application.
- Accepted UTF-8 BOM project files at both Rust and Python project-loading boundaries.

### Changed

- Replaced extraction interval configuration with canonical frames-per-second semantics from the UI and project JSON through Rust, Python, FFmpeg, frame estimates and timestamps.
- Upgraded the project contract to schema v3; schema v2 `secondsPerFrame` values are migrated to `framesPerSecond` and persisted atomically.
- Retained hidden legacy CLI interval options with explicit reciprocal conversion for existing command-line integrations.

## [0.2.1] - 2026-07-11

### Fixed

- Removed the gnullvm `libunwind.dll` startup dependency by statically linking the Rust CRT/unwind runtime.
- Added a recursive PE import-closure release gate so unresolved non-system DLL dependencies block packaging before an installer is produced.
- Removed an NSIS preinstall hook that changed `$INSTDIR` mid-install and could roll back bundled resources while leaving only the launcher files.
- Corrected locked PyTorch artifact URLs to the official `download.pytorch.org` endpoints while preserving exact sizes and SHA-256 hashes.

### Changed

- Added verified bundled-artifact cache support for the forthcoming full-offline densification package variant; the standard installer remains unchanged in size and behavior.

## [0.2.0] - 2026-07-11

### Added

- Single-stage Gaussian training through bundled LichtFeld Studio v0.5.3 with visible GUI, parameter control and live iteration/loss/Gaussian progress.
- Offline Runtime Readiness with exact, hashed Metashape dependency profiles for Python cp39, cp310, cp311 and cp312.
- Automatic non-admin Metashape dependency provisioning under LocalAppData using bundled wheels and a Python 3.9-compatible pip zipapp.
- Persistent point-cloud preview sessions and retained result workspaces.
- Per-feature environment readiness states for bundled tools, Metashape and optional densification.

### Changed

- Consolidated the title bar and bottom workspace navigation.
- Made the installed application runtime immutable; only versioned LocalAppData runtimes are writable.
- Formal release staging now includes offline wheelhouses, runtime manifests, notices and the complete LichtFeld Studio distribution.
- Point-cloud parsing, transfer and preparation remain off the UI thread and are reused across workspace navigation.

### Fixed

- Metashape environments without NumPy, OpenCV or pip can now be prepared fully offline.
- Repeated point-cloud loads and UI freezes when leaving and returning to results.
- LichtFeld parameter application, restart behavior and authoritative training progress tracking.

### Release notes

- Densification remains the only optional downloadable runtime.
- Windows x64 is the supported release platform.
- The installer and application are not Authenticode-signed unless a publisher certificate is supplied during release production.
