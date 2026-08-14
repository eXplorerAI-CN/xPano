use serde::Serialize;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;
use std::time::Duration;
use tauri::{AppHandle, Emitter};

/// One generated thumbnail pair, emitted as an event to the frontend.
#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ThumbEvent {
    pub time: f64,
    pub front: String,
    pub back: String,
}

/// Holds the cancel flag for the in-flight thumbgen batch so it can be stopped
/// when the user switches videos (or starts a new batch).
pub struct ThumbgenState {
    cancel: Arc<AtomicBool>,
}

impl ThumbgenState {
    pub fn new() -> Self {
        Self {
            cancel: Arc::new(AtomicBool::new(false)),
        }
    }

    /// Cancel the current batch (if any) and arm a fresh flag for the next one.
    /// Returns the new flag so the spawned thread can check `is_cancelled()`.
    pub fn reset(&mut self) -> Arc<AtomicBool> {
        self.cancel.store(true, Ordering::SeqCst);
        let flag = Arc::new(AtomicBool::new(false));
        self.cancel = flag.clone();
        flag
    }

    /// Signal cancellation without creating a new flag. Used on shutdown.
    pub fn cancel(&self) {
        self.cancel.store(true, Ordering::SeqCst);
    }
}

fn web_path(p: &Path) -> String {
    p.to_string_lossy().replace('\\', "/")
}

fn kill_process_tree(pid: u32) {
    #[cfg(target_os = "windows")]
    {
        let mut cmd = Command::new("taskkill");
        cmd.creation_flags(0x08000000);
        let _ = cmd
            .args(["/F", "/T", "/PID", &pid.to_string()])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    }

    #[cfg(not(target_os = "windows"))]
    {
        let _ = Command::new("kill")
            .args(["-TERM", &pid.to_string()])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    }
}

/// Extract a single frame at `time` from stream `stream_index`, writing to `out`.
/// Uses d3d11va hardware acceleration when available for faster HEVC seek+decode.
fn extract_one(src: &Path, time: f64, stream_index: u32, out: &Path, cancel: &AtomicBool) -> bool {
    if out.exists() {
        return true;
    }
    if cancel.load(Ordering::SeqCst) {
        return false;
    }
    let ffmpeg = crate::tool_resolver::locate_ffmpeg();
    let stamp = format!("{:.3}", time.max(0.0));
    let map = format!("0:{}", stream_index);
    let mut child_cmd = Command::new(&ffmpeg);
    #[cfg(target_os = "windows")]
    child_cmd.creation_flags(0x08000000);
    let child = child_cmd
        .args(["-hide_banner", "-y", "-nostdin", "-hwaccel", "d3d11va"])
        .args(["-ss", &stamp])
        .arg("-i")
        .arg(src)
        .args(["-map", &map, "-frames:v", "1", "-vf", "scale=480:-2"])
        .arg(out)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn();

    let Ok(mut child) = child else {
        return false;
    };
    let pid = child.id();

    loop {
        if cancel.load(Ordering::SeqCst) {
            kill_process_tree(pid);
            let _ = child.wait();
            return false;
        }

        match child.try_wait() {
            Ok(Some(status)) => return status.success() && out.exists(),
            Ok(None) => std::thread::sleep(Duration::from_millis(45)),
            Err(_) => return false,
        }
    }
}

/// Start an incremental thumbnail batch.
///
/// Generates `count` frame pairs starting at `from`, spaced `interval` seconds
/// apart, emitting a `thumbgen:frame` event per pair as each completes. The
/// caller passes a fresh cancel flag (from `state.reset()`) so switching videos
/// aborts the previous batch cleanly.
pub fn start_batch(
    app: AppHandle,
    state: &Mutex<ThumbgenState>,
    path: String,
    from: f64,
    interval: f64,
    count: usize,
) {
    let cancel = {
        let mut s = state.lock().unwrap();
        s.reset()
    };

    let src = match std::fs::canonicalize(&path) {
        Ok(a) => a,
        Err(_) => return,
    };
    // DJI 360 .osv files have front/back streams swapped vs Insta360 .insv
    let is_osv = src
        .extension()
        .and_then(|e| e.to_str())
        .map(|e| e.eq_ignore_ascii_case("osv"))
        .unwrap_or(false);
    let has_dual_lens = if is_osv {
        true
    } else {
        let ffprobe = crate::tool_resolver::locate_ffprobe();
        let mut probe_cmd = std::process::Command::new(&ffprobe);
        #[cfg(target_os = "windows")]
        probe_cmd.creation_flags(0x08000000);
        probe_cmd
            .args([
                "-v",
                "error",
                "-select_streams",
                "v:1",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
            ])
            .arg(&src)
            .output()
            .map(|o| !o.stdout.is_empty())
            .unwrap_or(false)
    };
    // OSV: front from stream 1, back from stream 0  (swapped vs insv)
    let (front_stream, back_stream): (u32, u32) = if is_osv { (1, 0) } else { (0, 1) };
    let fmt_tag = if is_osv { "osv" } else { "insv" };
    let key = src
        .to_string_lossy()
        .bytes()
        .fold(0u64, |acc, b| acc.wrapping_mul(31).wrapping_add(b as u64));
    let tmp_dir = std::env::temp_dir().join("xpano-thumbs");
    let _ = std::fs::create_dir_all(&tmp_dir);

    std::thread::spawn(move || {
        for i in 0..count {
            if cancel.load(Ordering::SeqCst) {
                return;
            }
            let time = from + (i as f64) * interval;
            let t_label = (time * 10.0).round() as i64;
            let front: PathBuf = tmp_dir.join(format!("{:x}_{}_{}_0.jpg", key, fmt_tag, t_label));
            let back: PathBuf = tmp_dir.join(format!("{:x}_{}_{}_1.jpg", key, fmt_tag, t_label));

            let ok_f = extract_one(&src, time, front_stream, &front, &cancel);
            if cancel.load(Ordering::SeqCst) {
                return;
            }
            let ok_b = if has_dual_lens {
                extract_one(&src, time, back_stream, &back, &cancel)
            } else {
                false
            };
            if cancel.load(Ordering::SeqCst) {
                return;
            }

            if ok_f {
                let _ = app.emit(
                    "thumbgen:frame",
                    ThumbEvent {
                        time,
                        front: web_path(&front),
                        back: if ok_b { web_path(&back) } else { String::new() },
                    },
                );
            }
        }
        let _ = app.emit("thumbgen:done", serde_json::json!({}));
    });
}
