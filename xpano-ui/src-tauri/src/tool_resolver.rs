use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use tauri::AppHandle;
use tauri::Manager;

// ---------------------------------------------------------------------------
// Lazy-initialised paths seeded once in `init()`.
// ---------------------------------------------------------------------------

static RESOURCE_BASE: OnceLock<PathBuf> = OnceLock::new();

/// Call once during startup from `tauri::Builder::setup()`.
pub fn init(app: &AppHandle) {
    if let Ok(dir) = app.path().resource_dir() {
        RESOURCE_BASE.set(dir).ok();
    }
}

fn resource_base() -> Option<&'static Path> {
    RESOURCE_BASE.get().map(|p| p.as_path())
}

pub fn plain_windows_path(path: &Path) -> String {
    let text = path.to_string_lossy();
    if let Some(stripped) = text.strip_prefix(r"\\?\UNC\") {
        format!(r"\\{}", stripped)
    } else if let Some(stripped) = text.strip_prefix(r"\\?\") {
        stripped.to_string()
    } else {
        text.into_owned()
    }
}

fn ancestors_with_script(base: &Path, script: &str, require_project_root: bool) -> Vec<PathBuf> {
    base.ancestors()
        .take(8)
        .filter_map(|root| {
            let candidate = root.join(script);
            if !candidate.exists() {
                return None;
            }
            if require_project_root
                && !root.join("xpano-ui").exists()
                && !root.join("scripts").join("xpano_tracks.py").exists()
            {
                return None;
            }
            Some(candidate)
        })
        .collect()
}

// ---------------------------------------------------------------------------
// Tool resolution: bundled > env var > PATH > hardcoded fallback
// ---------------------------------------------------------------------------

pub fn locate_tool(env_var: &str, name: &str, subdir: &str) -> String {
    let exe_name = if cfg!(windows) && !name.ends_with(".exe") {
        format!("{}.exe", name)
    } else {
        name.to_string()
    };

    // 1. Bundled in resources or portable release root.
    if let Some(base) = resource_base() {
        for bundled in [
            base.join("tools").join(subdir).join("bin").join(&exe_name),
            base.join("tools").join(subdir).join(&exe_name),
            base.join("binaries").join(subdir).join(&exe_name),
            base.join("_up_")
                .join("tools")
                .join(subdir)
                .join("bin")
                .join(&exe_name),
            base.join("_up_")
                .join("tools")
                .join(subdir)
                .join(&exe_name),
            base.join("_up_")
                .join("binaries")
                .join(subdir)
                .join(&exe_name),
            base.join("_up_")
                .join("_up_")
                .join("tools")
                .join(subdir)
                .join("bin")
                .join(&exe_name),
            base.join("_up_")
                .join("_up_")
                .join("tools")
                .join(subdir)
                .join(&exe_name),
            base.join("_up_")
                .join("_up_")
                .join("binaries")
                .join(subdir)
                .join(&exe_name),
        ] {
            if bundled.exists() {
                return bundled.to_string_lossy().into_owned();
            }
        }
    }

    // 2. Explicit env var.
    if let Ok(val) = std::env::var(env_var) {
        if Path::new(&val).exists() {
            return val;
        }
    }

    // 3. Portable release root resolved from scripts.
    let root = resolve_app_root();
    for bundled in [
        root.join("tools").join(subdir).join("bin").join(&exe_name),
        root.join("tools").join(subdir).join(&exe_name),
        root.join("binaries").join(subdir).join(&exe_name),
    ] {
        if bundled.exists() {
            return bundled.to_string_lossy().into_owned();
        }
    }

    // 4. Search PATH.
    if let Ok(path) = std::env::var("PATH") {
        for dir in path.split(if cfg!(windows) { ';' } else { ':' }) {
            if dir.is_empty() {
                continue;
            }
            let candidate = Path::new(dir).join(&exe_name);
            if candidate.exists() {
                return candidate.to_string_lossy().into_owned();
            }
        }
    }

    // 5. Windows hardcoded fallback paths.
    #[cfg(windows)]
    {
        for dir in [
            r"D:\ffmpeg\ffmpeg-master-latest-win64-gpl-shared\bin",
            r"D:\ffmpeg\bin",
            r"C:\ffmpeg\bin",
        ] {
            let candidate = Path::new(dir).join(&exe_name);
            if candidate.exists() {
                return candidate.to_string_lossy().into_owned();
            }
        }
    }

    exe_name
}

pub fn locate_ffmpeg() -> String {
    locate_tool("XPANO_FFMPEG", "ffmpeg", "ffmpeg")
}

pub fn locate_ffprobe() -> String {
    locate_tool("XPANO_FFPROBE", "ffprobe", "ffmpeg")
}

// ---------------------------------------------------------------------------
// Script path resolution: use current_exe() instead of current_dir().
// ---------------------------------------------------------------------------

pub fn resolve_script_path(script: &str) -> PathBuf {
    if Path::new(script).is_absolute() {
        return PathBuf::from(script);
    }

    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))
        .unwrap_or_else(|| std::env::current_dir().unwrap_or_default());

    let mut candidates = Vec::new();
    if let Ok(root) = std::env::var("XPANO_ROOT") {
        candidates.push(PathBuf::from(root).join(script));
    }
    if let Ok(cwd) = std::env::current_dir() {
        candidates.extend(ancestors_with_script(&cwd, script, true));
    }
    candidates.extend(ancestors_with_script(&exe_dir, script, true));
    if let Some(base) = resource_base() {
        candidates.push(base.join(script));
        candidates.push(base.join("_up_").join(script));
        candidates.push(base.join("_up_").join("_up_").join(script));
    }
    candidates.extend([
        exe_dir.join(script),                       // same dir as exe
        exe_dir.join("..").join(script),            // exe parent (src-tauri in dev)
        exe_dir.join("..").join("..").join(script), // exe grandparent
        std::env::current_dir()
            .unwrap_or_default()
            .join("..")
            .join(script),
        std::env::current_dir().unwrap_or_default().join(script),
    ]);

    candidates
        .into_iter()
        .find(|p| p.exists())
        .unwrap_or_else(|| PathBuf::from(script))
}

fn bundled_resource_candidates(
    relative: &Path,
    resource_base: Option<&Path>,
    executable_dir: Option<&Path>,
    current_dir: Option<&Path>,
) -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    let mut add = |path: PathBuf| {
        if !candidates.contains(&path) {
            candidates.push(path);
        }
    };
    if let Some(base) = resource_base {
        add(base.join(relative));
        add(base.join("_up_").join(relative));
        add(base.join("_up_").join("_up_").join(relative));
    }
    for base in executable_dir.into_iter().chain(current_dir) {
        for root in base.ancestors().take(8) {
            add(root.join(relative));
        }
    }
    candidates
}

/// Resolve an application-owned resource without development environment overrides.
pub fn resolve_bundled_resource_path(relative: &str) -> PathBuf {
    let relative = Path::new(relative);
    if relative.is_absolute() || relative.components().any(|component| component.as_os_str() == "..") {
        return relative.to_path_buf();
    }
    let executable_dir = std::env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(Path::to_path_buf));
    let current_dir = std::env::current_dir().ok();
    bundled_resource_candidates(
        relative,
        resource_base(),
        executable_dir.as_deref(),
        current_dir.as_deref(),
    )
    .into_iter()
    .find(|path| path.exists())
    .unwrap_or_else(|| relative.to_path_buf())
}

pub fn resolve_resource_path(relative: &str) -> PathBuf {
    let relative = Path::new(relative);
    if relative.is_absolute() {
        return relative.to_path_buf();
    }
    let mut candidates = Vec::new();
    if let Some(base) = resource_base() {
        candidates.extend([
            base.join(relative),
            base.join("_up_").join(relative),
            base.join("_up_").join("_up_").join(relative),
        ]);
    }
    let root = resolve_app_root();
    candidates.push(root.join(relative));
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            candidates.push(dir.join(relative));
        }
    }
    candidates
        .into_iter()
        .find(|path| path.exists())
        .unwrap_or_else(|| root.join(relative))
}

pub fn resolve_app_root() -> PathBuf {
    let marker = resolve_script_path("scripts/run_xpano_tracks_job.py");
    marker
        .parent()
        .and_then(|p| p.parent())
        .map(|p| p.to_path_buf())
        .or_else(|| std::env::current_dir().ok())
        .unwrap_or_default()
}

// ---------------------------------------------------------------------------
// Python resolution
// ---------------------------------------------------------------------------

pub fn locate_densify_python(root: &Path) -> PathBuf {
    let candidates = [
        root.join(".venv-densify")
            .join("Scripts")
            .join("python.exe"),
        root.join(".venv-densify").join("bin").join("python.exe"),
        root.join(".venv-densify").join("bin").join("python"),
    ];
    candidates
        .into_iter()
        .find(|p| p.exists())
        .unwrap_or_else(|| {
            root.join(".venv-densify")
                .join("Scripts")
                .join("python.exe")
        })
}

/// Resolve the best Python to use. Priority:
/// 1. Explicit non-empty path from caller.
/// 2. Bundled embedded Python.
/// 3. System `python` on PATH.
pub fn resolve_python(explicit: &str) -> String {
    let trimmed = explicit.trim();
    if !trimmed.is_empty() {
        return trimmed.to_string();
    }

    if let Ok(value) = std::env::var("XPANO_PYTHON") {
        let path = Path::new(value.trim());
        if path.exists() {
            return path.to_string_lossy().into_owned();
        }
    }

    let root = resolve_app_root();
    for candidate in [
        root.join(".venv").join("Scripts").join("python.exe"),
        root.join(".venv").join("bin").join("python.exe"),
        root.join(".venv").join("bin").join("python"),
        root.join("binaries").join("python").join("python.exe"),
    ] {
        if candidate.exists() {
            return candidate.to_string_lossy().into_owned();
        }
    }

    // Bundled embedded Python
    if let Some(base) = resource_base() {
        for bundled in [
            base.join(".venv").join("Scripts").join("python.exe"),
            base.join("binaries").join("python").join("python.exe"),
            base.join("_up_")
                .join(".venv")
                .join("Scripts")
                .join("python.exe"),
            base.join("_up_")
                .join("binaries")
                .join("python")
                .join("python.exe"),
            base.join("_up_")
                .join("_up_")
                .join("binaries")
                .join("python")
                .join("python.exe"),
        ] {
            if bundled.exists() {
                return bundled.to_string_lossy().into_owned();
            }
        }
    }

    "python".to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn plain_windows_path_removes_drive_verbatim_prefix() {
        assert_eq!(
            plain_windows_path(Path::new(r"\\?\E:\FastProgram\xPano\runtime\lichtfeld-studio\bin\LichtFeld-Studio.exe")),
            r"E:\FastProgram\xPano\runtime\lichtfeld-studio\bin\LichtFeld-Studio.exe"
        );
    }

    #[test]
    fn plain_windows_path_converts_verbatim_unc_prefix() {
        assert_eq!(
            plain_windows_path(Path::new(r"\\?\UNC\server\share\xPano\xpano-ui.exe")),
            r"\\server\share\xPano\xpano-ui.exe"
        );
    }

    #[test]
    fn plain_windows_path_preserves_normal_drive_path() {
        let path = r"E:\FastProgram\xPano\xpano-ui.exe";
        assert_eq!(plain_windows_path(Path::new(path)), path);
    }

    #[test]
    fn invalid_explicit_python_path_is_not_silently_replaced() {
        let missing = r"Z:\missing xpano python\python.exe";
        assert_eq!(resolve_python(missing), missing);
    }

    #[test]
    fn bundled_resource_candidates_start_with_tauri_resources_and_ignore_development_overrides() {
        let relative = Path::new("runtime/lichtfeld-studio/bin/LichtFeld-Studio.exe");
        let candidates = bundled_resource_candidates(
            relative,
            Some(Path::new(r"E:\xPano\resources")),
            None,
            None,
        );

        assert_eq!(
            candidates.first(),
            Some(&PathBuf::from(r"E:\xPano\resources\runtime\lichtfeld-studio\bin\LichtFeld-Studio.exe"))
        );
        assert!(!candidates.iter().any(|path| path.to_string_lossy().contains("XPANO_ROOT")));
    }
}
