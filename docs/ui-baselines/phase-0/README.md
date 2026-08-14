# Phase 0 UI Baseline

Captured from the existing reconstruction page before the workspace refactor.

| Viewport | File | SHA-256 | Result |
|---|---|---|---|
| 1024x768 | `baseline-1024x768.png` | `25398a8b6da216bc0e12e03d6acf084bc7d7758e54b5f86bd8578c545ad24d53` | Baseline captured; right status area is unavailable |
| 1366x768 | `baseline-1366x768.png` | `5dfca8b03a4d80a6e4a11eface3f0aec1917513c9b93a529372d9b00ee16556e` | No overlap or clipped primary controls |
| 1920x1080 | `baseline-1920x1080.png` | `8f0edf110b9d28c8a2d63f9f379dbc5aa7abcc7bd5a3fcf0951d179773f7cf56` | No overlap or clipped primary controls |

Runtime check:

- Route: `/#/`
- Theme: light
- Browser: Microsoft Edge through Playwright CLI
- Console: 0 errors, 0 warnings

The 1024px image records a known pre-refactor gap, not an accepted final layout. Phase 1 must keep the global job bar visible and expose status/log content through a drawer at this width.
