# LichtFeld Studio Gaussian Training Integration

## Scope

xPano integrates one LichtFeld Studio v0.5.3 training stage. Multi-stage schedules, checkpoint chaining, and distributed training are intentionally out of scope for this phase.

## Runtime

- Bundled path: `runtime/lichtfeld-studio`
- Executable: `runtime/lichtfeld-studio/bin/LichtFeld-Studio.exe`
- Upstream version: v0.5.3, commit `d8c50c6a`
- Official source: https://github.com/MrNeRF/LichtFeld-Studio
- License: GPL-3.0; the upstream `LICENSE` and bundled third-party notices are retained unchanged.

The complete portable directory is kept because DLL, Python, USD, shader, locale, and asset lookup is relative to the distribution layout.

## Process model

1. xPano validates that the active project contains a COLMAP `images/` directory and `sparse/0` model.
2. xPano creates a durable `training` job and a project-local output directory under `work/training/runs/<job-id>`.
3. The existing bundled Python launches `scripts/lichtfeld_training.py`.
4. The supervisor starts LichtFeld Studio without `--headless`, so the native GUI is visible by default, but deliberately omits `--train`.
5. After the dataset is loaded, xPano connects to LichtFeld's local MCP service, re-applies the final runtime values through the integrated Python API, then calls the official `training.start` tool.
6. This post-load override is required because v0.5.3 automatically multiplies CLI training schedules by `images / 300` for datasets larger than 300 images. The GUI now runs the exact iteration count requested by xPano.
7. During training, xPano polls LichtFeld's official embedded Python trainer accessors through the local MCP `editor.run` tool once per second. These accessors provide the real optimizer iteration, total, current loss, and Gaussian count. The run-local `lichtfeld.log` remains the source for startup, checkpoints, success, and fatal errors.
8. After the native success record, the managed GUI is closed and the task is committed only if a deliverable PLY, SOG, or SPZ artifact exists. A resume checkpoint alone is not treated as a finished result.

Cancellation uses the same Windows Job Object as reconstruction, so the supervisor and LichtFeld child process are terminated together.

## Default parameter mapping

| xPano setting | LichtFeld v0.5.3 argument | Default |
|---|---|---|
| Iterations | `--iter` | 30000 |
| Strategy | `--strategy` | `mrnf` |
| SH degree | `--sh-degree` | 3 |
| Gaussian cap | `--max-cap` | 1000000 |
| Resize factor | `--resize_factor` | `auto` |
| Maximum image width | `--max-width` | 3840 |
| Test interval | `--test-every` | 0 / disabled |
| CPU cache | absence of `--no-cpu-cache` | enabled |
| Filesystem cache | absence of `--no-fs-cache` | enabled |
| Dataset centralization | `--centralize` | `off` |
| Mip filtering | `--enable-mip` | disabled |
| Bilateral grid | `--bilateral-grid` | enabled |
| Evaluation | `--eval` | disabled |
| GUI | absence of `--headless` | enabled |

The bilateral grid is enabled in xPano's default training configuration. CLI values are still supplied for dataset import, while iteration count, Gaussian cap/readiness checks, maximum width, and selected optimization flags are verified or re-applied after import through MCP before training starts.

## Progress contract

The v0.5.3 progress adapter reports:

- total iteration from `Training started - N iterations planned`
- exact live iteration, total, loss, and Gaussian count from `trainer_current_iteration()`, `trainer_total_iterations()`, `trainer_current_loss()`, and `trainer_num_splats()`
- checkpoint iteration and Gaussian count from `Checkpoint saved`
- completion only from `Training completed successfully`
- fatal errors from native error records such as out-of-memory or training failure
- elapsed time and ETA calculated by the supervisor from observed iteration cadence

The native `Loss updated (... buffer size: N)` record is not treated as an iteration counter because v0.5.3 emits it roughly once per ten optimizer steps. Live state is emitted as a low-noise heartbeat about once per second, while terminal states are always emitted immediately. Log reads are incremental, so long training runs do not repeatedly load the full log into memory. A transient MCP polling failure is surfaced as a warning but does not terminate healthy training; checkpoint and terminal log handling remain available as the degraded path.

LichtFeld v0.5.3's `--python-script` hook was tested but is not used: it did not execute in the visible GUI auto-training path, so it cannot meet xPano's default-GUI progress contract. The built-in MCP service and native log together provide the observable interface.
