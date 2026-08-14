# Phase 0 Acceptance: Contracts and Baselines

Status: passed on 2026-07-10

## Scope

- Freeze project v2, job event v1, execution plan v1, and point-cloud variant contracts.
- Record exact 20-frame Metashape and COLMAP regression baselines without committing user media or reconstruction binaries.
- Capture the pre-refactor UI at the required desktop widths.
- Establish a truthful structured task-event baseline.

## Evidence

### Contract files

- `schemas/xpano_project_v3.schema.json`
- `schemas/xpano_job_event_v1.schema.json`
- `schemas/xpano_execution_plan_v1.schema.json`
- TypeScript mirror: `xpano-ui/src/lib/contracts.ts`
- Rust mirror and runtime validation: `xpano-ui/src-tauri/src/contracts.rs`

Generated artifact paths are rejected when absolute or parent-relative. External source media paths remain allowed and carry size/mtime fingerprints.

### Reconstruction baselines

Both cases used the same OSV input at 1 second/frame with an exact 20-frame limit.

| Backend | Source registration | Published cubemap images | Sparse points | Baseline |
|---|---:|---:|---:|---|
| Metashape | 40/40 | 200 | 14,028 | `tests/fixtures/regression/metashape-20.json` |
| COLMAP | 40/40 | 200 | 2,571 | `tests/fixtures/regression/colmap-20.json` |

The committed files contain counts and SHA-256 values only. Media paths, extracted frames, PSX data, and COLMAP binaries are not committed.

### Task-event correctness

A real COLMAP rerun using the prepared 20-frame manifest completed successfully and emitted:

```text
Alignment rate 40/40 (100.0%)
```

The previous implementation counted the 200 published cubemap faces as aligned source cameras and reported 500%. A regression test now locks the mapper-model boundary before publication.

### Automated verification

- Python: 119 passed.
- Rust: 8 passed.
- Frontend production build: passed.
- Frontend lint: passed.
- `git diff --check`: passed.

### UI baseline

Screenshots are stored in `docs/ui-baselines/phase-0/` for 1024x768, 1366x768, and 1920x1080. Browser console: 0 errors, 0 warnings.

The 1024px baseline exposes a known pre-refactor gap: the right status/log area is unavailable. This is an explicit Phase 1 acceptance target, where the global job bar must remain visible and status/log content must be available in a drawer.

## Decision

Phase 0 is accepted. Phase 1 may change application ownership and routing while keeping the current reconstruction workflow mounted and usable.
