mod contracts;
mod batch;
mod geometry;
mod job;
mod media;
mod performance;
mod pipeline;
mod project;
mod reconstruction;
mod process_job;
mod thumbgen;
mod tool_resolver;
mod training;

use pipeline::PipelineState;
use serde::{Deserialize, Serialize};
use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};
use std::io::Write;
use std::io::{BufRead, BufReader, Read};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex, OnceLock};
use std::thread;
use std::time::{SystemTime, UNIX_EPOCH};
use tauri::ipc::Response;
use tauri::{AppHandle, Emitter, Manager, State};
use thumbgen::ThumbgenState;

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

/// Camera pose returned to the frontend.
#[derive(Serialize, Clone)]
struct ColmapCamera {
    id: u32,
    /// [x, y, z] in COLMAP world coordinates
    position: [f32; 3],
    /// [qw, qx, qy, qz] quaternion
    rotation: [f32; 4],
    /// FOV in radians (computed from camera intrinsics), or a sensible default
    fov: f32,
    /// Aspect ratio (width / height), or a sensible default
    aspect: f32,
    /// Near plane
    near: f32,
    /// Far plane
    far: f32,
}

/// COLMAP point cloud data returned to the frontend.
#[derive(Clone)]
struct ColmapPointCloud {
    /// Flat f32 array: [x0,y0,z0, x1,y1,z1, ...]
    points: Vec<f32>,
    /// Flat lossless source colors: [r0,g0,b0, r1,g1,b1, ...]
    colors: Vec<u8>,
    num_points: usize,
    total_points: usize,
    sampled: bool,
    cameras: Vec<ColmapCamera>,
}

type CameraIntrinsics = (f32, f32, f32, f32);
type CameraIntrinsicsMap = std::collections::HashMap<u32, CameraIntrinsics>;

#[derive(Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct DensifyEnvStatus {
    plugin_ok: bool,
    python_ok: bool,
    deps_ok: bool,
    runner_ok: bool,
    plugin_path: String,
    python_path: String,
    message: String,
}

#[derive(Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
struct DensifyRunResult {
    #[serde(alias = "original_points")]
    original_points: usize,
    #[serde(alias = "dense_points")]
    dense_points: usize,
    #[serde(alias = "merged_points")]
    merged_points: usize,
    #[serde(alias = "output_points_path")]
    output_points_path: String,
    #[serde(alias = "replaced_points_bin")]
    replaced_points_bin: bool,
    #[serde(alias = "dense_ply_path")]
    dense_ply_path: String,
    #[serde(alias = "backup_points_path")]
    backup_points_path: String,
    roma: String,
    #[serde(alias = "max_points")]
    max_points: usize,
}

#[derive(Serialize, Deserialize, Clone, Default)]
#[serde(rename_all = "camelCase")]
struct DensifyPersistedState {
    status: String,
    message: String,
    result: Option<DensifyRunResult>,
    log_path: String,
    updated_at: u64,
}

#[derive(Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct ImportPathInfo {
    path: String,
    label: String,
    name: String,
    is_dir: bool,
    extension: String,
    suggested_type: String,
    kind: String,
    valid_photo_folder: bool,
    valid: bool,
    photo_count: usize,
    preview_paths: Vec<String>,
    message: String,
}

#[derive(Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct PhotoPreviewResult {
    total: usize,
    paths: Vec<String>,
}

struct PhotoPathScan {
    total: usize,
    paths: Vec<std::path::PathBuf>,
}

#[derive(Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct LoadedProjectTrack {
    id: String,
    #[serde(rename = "type")]
    track_type: String,
    label: String,
    path: String,
    camera_profile: Option<String>,
    frame_count: usize,
    photo_count: usize,
}

#[derive(Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct LoadedProjectState {
    project_dir: String,
    manifest_path: String,
    metashape_project_path: String,
    backend: String,
    metashape_alignment_mode: String,
    frames_per_second: f64,
    max_frames: i64,
    tracks: Vec<LoadedProjectTrack>,
}

fn count_colmap_points_bin(path: &std::path::Path) -> Result<usize, String> {
    let file = std::fs::File::open(path).map_err(|e| format!("无法打开点云文件: {}", e))?;
    let file_size = file
        .metadata()
        .map_err(|e| format!("读取点云文件信息失败: {}", e))?
        .len();
    let mut reader = BufReader::new(file);
    let mut num_bytes = [0u8; 8];
    reader
        .read_exact(&mut num_bytes)
        .map_err(|e| format!("读取点数失败: {}", e))?;
    let count = u64::from_le_bytes(num_bytes);
    let min_size = 8u64.saturating_add(count.saturating_mul(51));
    if file_size < min_size {
        return Err(format!(
            "点云文件可能未写完整: {} ({} bytes < expected at least {})",
            path.display(),
            file_size,
            min_size
        ));
    }
    Ok(count as usize)
}

fn find_bin(dir: &str, names: &[&str]) -> Option<std::path::PathBuf> {
    for name in names {
        let p = std::path::Path::new(dir).join(name);
        if p.exists() {
            return Some(p);
        }
    }
    // Also try sparse/ subdirectories
    let sparse_dirs = &["sparse/0", "sparse", "0"];
    for sparse_dir in sparse_dirs {
        let base = std::path::Path::new(dir).join(sparse_dir);
        for name in names {
            let p = base.join(name);
            if p.exists() {
                return Some(p);
            }
        }
    }
    None
}

fn is_colmap_preview_dir(path: &std::path::Path) -> bool {
    path.is_dir() && find_bin(&path.to_string_lossy(), &["points3D.bin"]).is_some()
}

fn is_xpano_project_dir(path: &std::path::Path) -> bool {
    path.is_dir()
        && (path.join("xpano_manifest.json").is_file()
            || path.join("xpano_run_summary.json").is_file()
            || path.join("work").join("xpano_manifest.json").is_file()
            || path.join("work").join("xpano.psx").is_file())
}

fn resolve_xpano_project_dir_from_paths(paths: &[String]) -> Option<std::path::PathBuf> {
    paths.iter().find_map(|raw| {
        let path = std::path::PathBuf::from(raw);
        let candidate = path.canonicalize().ok()?;
        if is_xpano_project_dir(&candidate) {
            Some(candidate)
        } else {
            None
        }
    })
}

fn resolve_colmap_preview_dir_from_paths(paths: &[String]) -> Option<std::path::PathBuf> {
    paths.iter().find_map(|raw| {
        let path = std::path::PathBuf::from(raw);
        let candidate = path.canonicalize().ok()?;
        if is_colmap_preview_dir(&candidate) && !is_xpano_project_dir(&candidate) {
            Some(candidate)
        } else {
            None
        }
    })
}

#[tauri::command]
fn resolve_xpano_project_dir(paths: Vec<String>) -> Result<Option<String>, String> {
    Ok(resolve_xpano_project_dir_from_paths(&paths)
        .map(|path| tool_resolver::plain_windows_path(&path)))
}

fn json_str<'a>(value: &'a serde_json::Value, key: &str) -> Option<&'a str> {
    value.get(key).and_then(|item| item.as_str()).filter(|item| !item.is_empty())
}

fn json_f64(value: &serde_json::Value, key: &str, default: f64) -> f64 {
    value.get(key).and_then(|item| item.as_f64()).unwrap_or(default)
}

fn summary_frames_per_second(summary: &serde_json::Value) -> f64 {
    if summary.get("frames_per_second").is_some() {
        return json_f64(summary, "frames_per_second", 1.0);
    }
    let legacy_interval = json_f64(summary, "seconds_per_frame", 0.0);
    if legacy_interval.is_finite() && legacy_interval > 0.0 {
        1.0 / legacy_interval
    } else {
        1.0
    }
}

fn json_i64(value: &serde_json::Value, key: &str, default: i64) -> i64 {
    value.get(key).and_then(|item| item.as_i64()).unwrap_or(default)
}

fn first_existing_project_file(project_dir: &std::path::Path, names: &[&str]) -> Option<std::path::PathBuf> {
    names.iter().map(|name| project_dir.join(name)).find(|path| path.is_file())
}

fn common_parent(paths: &[std::path::PathBuf]) -> Option<std::path::PathBuf> {
    let mut parents = paths.iter().filter_map(|path| path.parent()).map(|path| path.to_path_buf());
    let mut common = parents.next()?;
    for parent in parents {
        while !parent.starts_with(&common) {
            if !common.pop() {
                return None;
            }
        }
    }
    Some(common)
}

fn map_manifest_track_type(track_type: &str) -> &str {
    match track_type {
        "panorama_video" => "panoramic_video",
        "ordinary_video" => "ordinary_video",
        "aerial_photos" => "aerial_photos",
        _ => "standard_photos",
    }
}

fn loaded_track_path(track: &serde_json::Value) -> String {
    let source_paths = track
        .get("source_paths")
        .and_then(|item| item.as_array())
        .map(|items| {
            items
                .iter()
                .filter_map(|item| item.as_str())
                .map(std::path::PathBuf::from)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    if source_paths.len() == 1 {
        return tool_resolver::plain_windows_path(&source_paths[0]);
    }
    if source_paths.len() > 1 {
        if let Some(parent) = common_parent(&source_paths) {
            return tool_resolver::plain_windows_path(&parent);
        }
    }

    let photos = track
        .get("photos")
        .and_then(|item| item.as_array())
        .map(|items| {
            items
                .iter()
                .filter_map(|item| item.as_str())
                .map(std::path::PathBuf::from)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    if photos.len() == 1 {
        tool_resolver::plain_windows_path(&photos[0])
    } else if let Some(parent) = common_parent(&photos) {
        tool_resolver::plain_windows_path(&parent)
    } else {
        String::new()
    }
}

#[tauri::command]
fn load_xpano_project(path: String) -> Result<LoadedProjectState, String> {
    let project_dir = std::path::PathBuf::from(path)
        .canonicalize()
        .map_err(|e| format!("无法打开 xPano 工程目录: {}", e))?;
    if !is_xpano_project_dir(&project_dir) {
        return Err("所选目录不是 xPano 工程目录".to_string());
    }

    let manifest_path = first_existing_project_file(
        &project_dir,
        &["work/xpano_manifest.json", "xpano_manifest.json"],
    )
    .ok_or_else(|| "工程目录中没有找到 xpano_manifest.json".to_string())?;
    let manifest_text = std::fs::read_to_string(&manifest_path)
        .map_err(|e| format!("读取 manifest 失败: {}", e))?;
    let manifest: serde_json::Value = serde_json::from_str(&manifest_text)
        .map_err(|e| format!("解析 manifest 失败: {}", e))?;

    let summary = first_existing_project_file(&project_dir, &["xpano_run_summary.json"])
        .and_then(|path| std::fs::read_to_string(path).ok())
        .and_then(|text| serde_json::from_str::<serde_json::Value>(&text).ok())
        .unwrap_or_else(|| serde_json::json!({}));

    let tracks = manifest
        .get("tracks")
        .and_then(|item| item.as_array())
        .ok_or_else(|| "manifest 中没有 tracks".to_string())?
        .iter()
        .enumerate()
        .map(|(index, track)| {
            let manifest_type = json_str(track, "track_type").unwrap_or("standard_photos");
            LoadedProjectTrack {
                id: json_str(track, "track_id")
                    .map(|value| value.to_string())
                    .unwrap_or_else(|| format!("loaded_track_{:03}", index + 1)),
                track_type: map_manifest_track_type(manifest_type).to_string(),
                label: json_str(track, "device_label")
                    .or_else(|| json_str(track, "track_id"))
                    .unwrap_or("xPano track")
                    .to_string(),
                path: loaded_track_path(track),
                camera_profile: json_str(track, "camera_profile").map(|value| value.to_string()),
                frame_count: track
                    .get("frames")
                    .and_then(|item| item.as_array())
                    .map(|items| items.len())
                    .unwrap_or(0),
                photo_count: track
                    .get("photos")
                    .and_then(|item| item.as_array())
                    .map(|items| items.len())
                    .unwrap_or(0),
            }
        })
        .collect::<Vec<_>>();

    let metashape_project_path = project_dir.join("work").join("xpano.psx");

    Ok(LoadedProjectState {
        project_dir: tool_resolver::plain_windows_path(&project_dir),
        manifest_path: tool_resolver::plain_windows_path(&manifest_path),
        metashape_project_path: if metashape_project_path.is_file() {
            tool_resolver::plain_windows_path(&metashape_project_path)
        } else {
            String::new()
        },
        backend: json_str(&summary, "backend").unwrap_or("metashape").to_string(),
        metashape_alignment_mode: json_str(&summary, "metashape_alignment_mode")
            .unwrap_or("backbone")
            .to_string(),
        frames_per_second: summary_frames_per_second(&summary),
        max_frames: json_i64(&summary, "max_frames", 0),
        tracks,
    })
}

#[tauri::command]
fn resolve_colmap_preview_dir(paths: Vec<String>) -> Result<Option<String>, String> {
    Ok(resolve_colmap_preview_dir_from_paths(&paths)
        .map(|path| tool_resolver::plain_windows_path(&path)))
}

fn read_cameras_bin(path: &std::path::Path) -> Result<CameraIntrinsicsMap, String> {
    let file = std::fs::File::open(path).map_err(|e| format!("无法打开 cameras.bin: {}", e))?;
    let mut reader = BufReader::new(file);
    let mut num_bytes = [0u8; 8];
    reader
        .read_exact(&mut num_bytes)
        .map_err(|e| format!("读取相机数失败: {}", e))?;
    let num_cameras = u64::from_le_bytes(num_bytes) as usize;
    let mut cameras = std::collections::HashMap::new();

    for _ in 0..num_cameras {
        let mut header = [0u8; 24]; // camera_id(u32) + model(u32) + width(u64) + height(u64)
        reader
            .read_exact(&mut header)
            .map_err(|e| format!("读取相机头失败: {}", e))?;
        let camera_id = u32::from_le_bytes(header[0..4].try_into().unwrap());
        let model = u32::from_le_bytes(header[4..8].try_into().unwrap());
        let width = u64::from_le_bytes(header[8..16].try_into().unwrap()) as f32;
        let height = u64::from_le_bytes(header[16..24].try_into().unwrap()) as f32;

        // Determine number of params based on model
        let num_params: usize = match model {
            0 => 3,   // SIMPLE_PINHOLE: f, cx, cy
            1 => 4,   // PINHOLE: fx, fy, cx, cy
            2 => 4,   // SIMPLE_RADIAL: f, cx, cy, k
            3 => 5,   // RADIAL: f, cx, cy, k1, k2
            4 => 8,   // OPENCV: fx, fy, cx, cy, k1, k2, p1, p2
            5 => 8,   // OPENCV_FISHEYE: fx, fy, cx, cy, k1, k2, k3, k4
            6 => 12,  // FULL_OPENCV: fx, fy, cx, cy, k1, k2, p1, p2, k3, k4, k5, k6
            7 => 5,   // FOV: fx, fy, cx, cy, omega
            8 => 4,   // SIMPLE_RADIAL_FISHEYE: f, cx, cy, k
            9 => 5,   // RADIAL_FISHEYE: f, cx, cy, k1, k2
            10 => 12, // THIN_PRISM_FISHEYE: fx, fy, cx, cy, k1, k2, p1, p2, k3, k4, sx1, sy1
            _ => 0,
        };
        let mut param_bytes = vec![0u8; num_params * 8];
        reader
            .read_exact(&mut param_bytes)
            .map_err(|e| format!("读取相机参数失败: {}", e))?;

        let aspect = if height > 0.0 { width / height } else { 1.55 };
        // Extract fx (or f) as the first parameter for most models
        let fx = if num_params >= 1 {
            f64::from_le_bytes(param_bytes[0..8].try_into().unwrap()) as f32
        } else {
            width
        };
        // FOV = 2 * atan(height / (2 * fy))
        // But for simplicity, use: fov = 2 * atan(height / (2 * fx))
        let fy = if model >= 4 && num_params >= 2 {
            f64::from_le_bytes(param_bytes[8..16].try_into().unwrap()) as f32
        } else {
            fx
        };
        let fov = if fy > 0.0 && height > 0.0 {
            2.0 * (height / (2.0 * fy)).atan()
        } else {
            std::f32::consts::PI / 3.0 // default ~60°
        };

        cameras.insert(camera_id, (fov, aspect, 0.25, 50.0));
    }
    Ok(cameras)
}

fn qvec_to_rotmat(qw: f32, qx: f32, qy: f32, qz: f32) -> [[f32; 3]; 3] {
    let norm = (qw * qw + qx * qx + qy * qy + qz * qz).sqrt();
    let (qw, qx, qy, qz) = if norm > 1e-8 {
        (qw / norm, qx / norm, qy / norm, qz / norm)
    } else {
        (1.0, 0.0, 0.0, 0.0)
    };

    [
        [
            1.0 - 2.0 * qy * qy - 2.0 * qz * qz,
            2.0 * qx * qy - 2.0 * qw * qz,
            2.0 * qx * qz + 2.0 * qw * qy,
        ],
        [
            2.0 * qx * qy + 2.0 * qw * qz,
            1.0 - 2.0 * qx * qx - 2.0 * qz * qz,
            2.0 * qy * qz - 2.0 * qw * qx,
        ],
        [
            2.0 * qx * qz - 2.0 * qw * qy,
            2.0 * qy * qz + 2.0 * qw * qx,
            1.0 - 2.0 * qx * qx - 2.0 * qy * qy,
        ],
    ]
}

fn colmap_camera_center(qw: f32, qx: f32, qy: f32, qz: f32, tx: f32, ty: f32, tz: f32) -> [f32; 3] {
    let rot = qvec_to_rotmat(qw, qx, qy, qz);
    let t = [tx, ty, tz];

    [
        -(rot[0][0] * t[0] + rot[1][0] * t[1] + rot[2][0] * t[2]),
        -(rot[0][1] * t[0] + rot[1][1] * t[1] + rot[2][1] * t[2]),
        -(rot[0][2] * t[0] + rot[1][2] * t[1] + rot[2][2] * t[2]),
    ]
}

fn read_images_bin(
    path: &std::path::Path,
    camera_params: &std::collections::HashMap<u32, (f32, f32, f32, f32)>,
) -> Result<Vec<ColmapCamera>, String> {
    let file = std::fs::File::open(path).map_err(|e| format!("无法打开 images.bin: {}", e))?;
    let mut reader = BufReader::new(file);
    let mut num_bytes = [0u8; 8];
    reader
        .read_exact(&mut num_bytes)
        .map_err(|e| format!("读取图片数失败: {}", e))?;
    let num_images = u64::from_le_bytes(num_bytes) as usize;
    let mut cameras = Vec::with_capacity(num_images);

    for _ in 0..num_images {
        let mut header = [0u8; 64]; // image_id(u32=4) + qw/qx/qy/qz(f64*4=32) + tx/ty/tz(f64*3=24) + camera_id(u32=4) = 64
        reader
            .read_exact(&mut header)
            .map_err(|e| format!("读取图片位姿失败: {}", e))?;
        let image_id = u32::from_le_bytes(header[0..4].try_into().unwrap());
        let qw = f64::from_le_bytes(header[4..12].try_into().unwrap()) as f32;
        let qx = f64::from_le_bytes(header[12..20].try_into().unwrap()) as f32;
        let qy = f64::from_le_bytes(header[20..28].try_into().unwrap()) as f32;
        let qz = f64::from_le_bytes(header[28..36].try_into().unwrap()) as f32;
        let tx = f64::from_le_bytes(header[36..44].try_into().unwrap()) as f32;
        let ty = f64::from_le_bytes(header[44..52].try_into().unwrap()) as f32;
        let tz = f64::from_le_bytes(header[52..60].try_into().unwrap()) as f32;
        let camera_id = u32::from_le_bytes(header[60..64].try_into().unwrap());

        // Skip image name (null-terminated string)
        loop {
            let mut byte = [0u8; 1];
            reader
                .read_exact(&mut byte)
                .map_err(|e| format!("读取图片名失败: {}", e))?;
            if byte[0] == 0 {
                break;
            }
        }

        // Skip points2D
        let mut pts2d_len_bytes = [0u8; 8];
        reader
            .read_exact(&mut pts2d_len_bytes)
            .map_err(|e| format!("读取2D点数失败: {}", e))?;
        let num_pts2d = u64::from_le_bytes(pts2d_len_bytes) as usize;
        // Each point2D: x(f64) + y(f64) + point3D_id(u64) = 24 bytes
        let skip = num_pts2d * 24;
        let mut skip_buf = vec![0u8; skip];
        if skip > 0 {
            reader
                .read_exact(&mut skip_buf)
                .map_err(|e| format!("跳过2D点失败: {}", e))?;
        }

        let default_params = (std::f32::consts::PI / 3.0, 1.55, 0.25, 50.0);
        let (fov, aspect, near, far) = camera_params.get(&camera_id).unwrap_or(&default_params);
        let center = colmap_camera_center(qw, qx, qy, qz, tx, ty, tz);

        cameras.push(ColmapCamera {
            id: image_id,
            position: center,
            rotation: [qw, qx, qy, qz],
            fov: *fov,
            aspect: *aspect,
            near: *near,
            far: *far,
        });
    }

    Ok(cameras)
}

fn read_colmap_points_impl(
    dir: String,
    points_path: Option<String>,
    max_points: Option<usize>,
) -> Result<ColmapPointCloud, String> {
    let points_path = points_path
        .and_then(|path| {
            let trimmed = path.trim();
            if trimmed.is_empty() {
                None
            } else {
                Some(std::path::PathBuf::from(trimmed))
            }
        })
        .or_else(|| find_bin(&dir, &["points3D.bin"]))
        .ok_or_else(|| format!("在 {} 中未找到 points3D.bin", dir))?;

    let file = std::fs::File::open(&points_path).map_err(|e| format!("无法打开文件: {}", e))?;
    let mut reader = BufReader::with_capacity(1024 * 1024, file);

    let mut num_bytes = [0u8; 8];
    reader
        .read_exact(&mut num_bytes)
        .map_err(|e| format!("读取点数失败: {}", e))?;
    let total_points = u64::from_le_bytes(num_bytes) as usize;
    let point_budget = max_points.unwrap_or(0);
    let sample_stride = if point_budget > 0 && total_points > point_budget {
        total_points.div_ceil(point_budget).max(1)
    } else {
        1
    };
    let expected_points = if sample_stride > 1 {
        total_points.div_ceil(sample_stride).min(point_budget)
    } else {
        total_points
    };

    let mut points = Vec::with_capacity(expected_points * 3);
    let mut colors = Vec::with_capacity(expected_points * 3);

    for index in 0..total_points {
        let mut record = [0u8; 51];
        reader
            .read_exact(&mut record)
            .map_err(|e| format!("读取点记录失败: {}", e))?;

        let x = f64::from_le_bytes(record[8..16].try_into().unwrap()) as f32;
        let y = f64::from_le_bytes(record[16..24].try_into().unwrap()) as f32;
        let z = f64::from_le_bytes(record[24..32].try_into().unwrap()) as f32;

        let keep_point = index % sample_stride == 0 && points.len() / 3 < expected_points;
        if keep_point {
            points.push(x);
            points.push(y);
            points.push(z);
            colors.extend_from_slice(&record[32..35]);
        }

        let track_len = u64::from_le_bytes(record[43..51].try_into().unwrap()) as usize;
        if track_len > 0 {
            let skip_bytes = track_len
                .checked_mul(8)
                .ok_or_else(|| "点轨迹长度溢出".to_string())?;
            skip_buffered_bytes(&mut reader, skip_bytes)
                .map_err(|e| format!("跳过轨迹失败: {}", e))?;
        }
    }

    let camera_params = find_bin(&dir, &["cameras.bin"])
        .and_then(|p| read_cameras_bin(&p).ok())
        .unwrap_or_default();

    let cameras = find_bin(&dir, &["images.bin"])
        .and_then(|p| read_images_bin(&p, &camera_params).ok())
        .unwrap_or_default();

    Ok(ColmapPointCloud {
        points,
        colors,
        num_points: expected_points,
        total_points,
        sampled: sample_stride > 1,
        cameras,
    })
}

fn skip_buffered_bytes<R: Read>(reader: &mut BufReader<R>, mut remaining: usize) -> std::io::Result<()> {
    while remaining > 0 {
        let available = reader.fill_buf()?;
        if available.is_empty() {
            return Err(std::io::Error::new(
                std::io::ErrorKind::UnexpectedEof,
                "point track is truncated",
            ));
        }
        let consumed = remaining.min(available.len());
        reader.consume(consumed);
        remaining -= consumed;
    }
    Ok(())
}

const POINT_CLOUD_PACKET_HEADER_BYTES: usize = 64;
const POINT_CLOUD_CAMERA_BYTES: usize = 48;

fn push_u32(buffer: &mut Vec<u8>, value: u32) {
    buffer.extend_from_slice(&value.to_le_bytes());
}

fn push_u64(buffer: &mut Vec<u8>, value: u64) {
    buffer.extend_from_slice(&value.to_le_bytes());
}

fn push_f32(buffer: &mut Vec<u8>, value: f32) {
    buffer.extend_from_slice(&value.to_le_bytes());
}

fn encode_point_cloud_packet(cloud: &ColmapPointCloud) -> Vec<u8> {
    let point_bytes = cloud.points.len() * std::mem::size_of::<f32>();
    let color_bytes = cloud.colors.len();
    let camera_bytes = cloud.cameras.len() * POINT_CLOUD_CAMERA_BYTES;
    let payload_bytes = point_bytes + color_bytes + camera_bytes;
    let mut packet = Vec::with_capacity(POINT_CLOUD_PACKET_HEADER_BYTES + payload_bytes);

    packet.extend_from_slice(b"XPCLD001");
    push_u32(&mut packet, 1);
    push_u32(&mut packet, u32::from(cloud.sampled));
    push_u32(&mut packet, cloud.num_points as u32);
    push_u32(&mut packet, cloud.cameras.len() as u32);
    push_u64(&mut packet, cloud.total_points as u64);
    push_u32(&mut packet, cloud.points.len() as u32);
    push_u32(&mut packet, cloud.colors.len() as u32);
    push_u32(&mut packet, POINT_CLOUD_CAMERA_BYTES as u32);
    push_u32(&mut packet, 0);
    push_u64(&mut packet, payload_bytes as u64);
    push_u64(&mut packet, 0);
    debug_assert_eq!(packet.len(), POINT_CLOUD_PACKET_HEADER_BYTES);

    for value in &cloud.points {
        push_f32(&mut packet, *value);
    }
    packet.extend_from_slice(&cloud.colors);
    for camera in &cloud.cameras {
        push_u32(&mut packet, camera.id);
        for value in camera.position {
            push_f32(&mut packet, value);
        }
        for value in camera.rotation {
            push_f32(&mut packet, value);
        }
        for value in [camera.fov, camera.aspect, camera.near, camera.far] {
            push_f32(&mut packet, value);
        }
    }
    debug_assert_eq!(packet.len(), POINT_CLOUD_PACKET_HEADER_BYTES + payload_bytes);
    packet
}

/// Read COLMAP geometry on the blocking executor and return a compact raw IPC packet.
#[tauri::command]
async fn read_colmap_points(
    dir: String,
    points_path: Option<String>,
    max_points: Option<usize>,
) -> Result<Response, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let cloud = read_colmap_points_impl(dir, points_path, max_points)?;
        Ok(Response::new(encode_point_cloud_packet(&cloud)))
    })
    .await
    .map_err(|error| format!("点云后台读取任务失败: {error}"))?
}
#[tauri::command]
fn apply_lfs_densify_result(
    app: AppHandle,
    output_dir: String,
    dense_points_path: String,
) -> Result<crate::contracts::PointCloudVariant, String> {
    let root = std::path::PathBuf::from(&output_dir)
        .canonicalize()
        .map_err(|e| format!("无法读取输出目录: {}", e))?;
    let dense_path = std::path::PathBuf::from(&dense_points_path)
        .canonicalize()
        .map_err(|e| format!("无法读取致密化点云: {}", e))?;
    if !dense_path.starts_with(&root) {
        return Err("致密化结果不在当前项目目录内".to_string());
    }

    let project_root = crate::project::find_project_root(&root)
        .ok_or_else(|| "无法从致密化输出定位 xPano 工程".to_string())?;
    let variant = crate::geometry::register_densified_variant_impl(
        &project_root,
        &dense_path,
        None,
    )
    .map_err(|error| error.message)?;
    if let Ok(project) = crate::project::read_project(&project_root) {
        let _ = app.emit(
            "project:updated",
            crate::media::ProjectUpdatedEvent {
                project_root: project_root.to_string_lossy().to_string(),
                project,
            },
        );
    }
    let previous = read_densify_state(&root);
    let _ = write_densify_state(
        &root,
        &DensifyPersistedState {
            status: "registered".to_string(),
            message: "致密化结果已注册为永久点云版本".to_string(),
            result: previous.and_then(|state| state.result),
            log_path: read_densify_state(&root)
                .map(|state| state.log_path)
                .unwrap_or_default(),
            updated_at: now_millis(),
        },
    );
    Ok(variant)
}

#[tauri::command]
fn discard_lfs_densify_result(output_dir: String, dense_points_path: String) -> Result<(), String> {
    let root = std::path::PathBuf::from(&output_dir)
        .canonicalize()
        .map_err(|e| format!("无法读取输出目录: {}", e))?;
    let dense_path = std::path::PathBuf::from(&dense_points_path)
        .canonicalize()
        .map_err(|e| format!("无法读取致密化点云: {}", e))?;
    if !dense_path.starts_with(&root) {
        return Err("致密化结果不在当前项目目录内".to_string());
    }
    // NOTE: Closing a preview must not destroy a recoverable densification result.
    let previous = read_densify_state(&root);
    let _ = write_densify_state(
        &root,
        &DensifyPersistedState {
            status: "preview_closed".to_string(),
            message: "已关闭致密化预览，候选文件仍保留".to_string(),
            result: previous.as_ref().and_then(|state| state.result.clone()),
            log_path: previous.map(|state| state.log_path).unwrap_or_default(),
            updated_at: now_millis(),
        },
    );
    Ok(())
}

fn remove_file_if_exists(path: &std::path::Path) {
    if path.exists() {
        let _ = std::fs::remove_file(path);
    }
}

fn now_millis() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_millis() as u64)
        .unwrap_or(0)
}

fn densify_workspace(output_dir: &std::path::Path) -> std::path::PathBuf {
    output_dir.join("workspace")
}

fn densify_state_path(output_dir: &std::path::Path) -> std::path::PathBuf {
    densify_workspace(output_dir).join("lfs_densify_state.json")
}

fn densify_logs_dir(output_dir: &std::path::Path) -> std::path::PathBuf {
    densify_workspace(output_dir).join("logs")
}

fn read_densify_state(output_dir: &std::path::Path) -> Option<DensifyPersistedState> {
    let path = densify_state_path(output_dir);
    let text = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&text).ok()
}

fn write_densify_state(
    output_dir: &std::path::Path,
    state: &DensifyPersistedState,
) -> Result<(), String> {
    let workspace = densify_workspace(output_dir);
    std::fs::create_dir_all(&workspace).map_err(|e| format!("创建工作目录失败: {}", e))?;
    let path = densify_state_path(output_dir);
    let tmp = path.with_extension("json.tmp");
    let text =
        serde_json::to_string_pretty(state).map_err(|e| format!("序列化致密化状态失败: {}", e))?;
    std::fs::write(&tmp, text).map_err(|e| format!("写入致密化状态失败: {}", e))?;
    if path.exists() {
        std::fs::remove_file(&path).map_err(|e| format!("更新致密化状态失败: {}", e))?;
    }
    std::fs::rename(&tmp, &path).map_err(|e| format!("保存致密化状态失败: {}", e))?;
    Ok(())
}

fn clean_old_densify_logs(log_dir: &std::path::Path, keep: usize) {
    let Ok(entries) = std::fs::read_dir(log_dir) else {
        return;
    };
    let mut files: Vec<_> = entries
        .flatten()
        .filter_map(|entry| {
            let path = entry.path();
            if path.extension().and_then(|value| value.to_str()) != Some("log") {
                return None;
            }
            let modified = entry.metadata().and_then(|value| value.modified()).ok()?;
            Some((modified, path))
        })
        .collect();
    files.sort_by_key(|(modified, _)| *modified);
    let remove_count = files.len().saturating_sub(keep);
    for (_, path) in files.into_iter().take(remove_count) {
        let _ = std::fs::remove_file(path);
    }
}

fn create_densify_log_file(
    output_dir: &std::path::Path,
    task: &str,
) -> Result<std::path::PathBuf, String> {
    let log_dir = densify_logs_dir(output_dir);
    std::fs::create_dir_all(&log_dir).map_err(|e| format!("创建日志目录失败: {}", e))?;
    clean_old_densify_logs(&log_dir, 12);
    Ok(log_dir.join(format!("densify-{}-{}.log", task, now_millis())))
}

fn create_densify_env_log_file(
    root: &std::path::Path,
    task: &str,
) -> Result<std::path::PathBuf, String> {
    let log_dir = root.join("logs");
    std::fs::create_dir_all(&log_dir)
        .map_err(|e| format!("创建致密化环境日志目录失败: {}", e))?;
    clean_old_densify_logs(&log_dir, 12);
    Ok(log_dir.join(format!("densify-{}-{}.log", task, now_millis())))
}

#[tauri::command]
fn get_lfs_densify_state(output_dir: String) -> Result<Option<DensifyPersistedState>, String> {
    let root = std::path::PathBuf::from(&output_dir)
        .canonicalize()
        .map_err(|e| format!("无法读取输出目录: {}", e))?;
    if let Some(mut state) = read_densify_state(&root) {
        if state.status == "running" {
            state.status = "stopped".to_string();
            state.message = "上次致密化任务未正常结束".to_string();
            state.updated_at = now_millis();
            let _ = write_densify_state(&root, &state);
        }
        Ok(Some(state))
    } else {
        Ok(None)
    }
}

#[tauri::command]
fn read_lfs_densify_log_tail(
    output_dir: String,
    log_path: String,
    max_lines: Option<usize>,
) -> Result<Vec<String>, String> {
    let root = std::path::PathBuf::from(&output_dir)
        .canonicalize()
        .map_err(|e| format!("无法读取输出目录: {}", e))?;
    let path = std::path::PathBuf::from(log_path)
        .canonicalize()
        .map_err(|e| format!("无法读取致密化日志: {}", e))?;
    if !path.starts_with(&root) {
        return Err("日志文件不在当前项目目录内".to_string());
    }
    let text = std::fs::read_to_string(&path).map_err(|e| format!("读取致密化日志失败: {}", e))?;
    let lines: Vec<String> = text.lines().map(|line| line.to_string()).collect();
    let keep = max_lines.unwrap_or(220).max(1);
    let start = lines.len().saturating_sub(keep);
    Ok(lines[start..].to_vec())
}

#[tauri::command]
fn get_lfs_densify_pending_result(output_dir: String) -> Result<Option<DensifyRunResult>, String> {
    let root = std::path::PathBuf::from(&output_dir)
        .canonicalize()
        .map_err(|e| format!("无法读取输出目录: {}", e))?;
    let points_path = find_bin(&output_dir, &["points3D.bin"])
        .ok_or_else(|| format!("在 {} 中未找到 points3D.bin", output_dir))?;
    let sparse_dir = points_path
        .parent()
        .ok_or_else(|| "无法定位 sparse 目录".to_string())?;
    remove_file_if_exists(&sparse_dir.join("points3D_dense.bin.tmp"));
    remove_file_if_exists(&sparse_dir.join("points3D.bin.tmp"));

    let dense_path = sparse_dir.join("points3D_dense.bin");
    if !dense_path.exists() {
        return Ok(None);
    }
    let dense_path = dense_path
        .canonicalize()
        .map_err(|e| format!("无法读取致密化结果: {}", e))?;
    if !dense_path.starts_with(&root) {
        return Ok(None);
    }
    let previous = read_densify_state(&root);
    if previous.as_ref().is_some_and(|state| state.status == "registered") {
        return Ok(None);
    }

    let backup_path = sparse_dir.join("points3D_sparse_original.bin");
    let original_points = if backup_path.exists() {
        count_colmap_points_bin(&backup_path)?
    } else {
        count_colmap_points_bin(&points_path)?
    };
    let current_points = count_colmap_points_bin(&points_path)?;
    let merged_points = count_colmap_points_bin(&dense_path)?;

    if backup_path.exists() && current_points == merged_points {
        return Ok(None);
    }
    if merged_points <= original_points {
        return Ok(None);
    }

    let result = DensifyRunResult {
        original_points,
        dense_points: merged_points - original_points,
        merged_points,
        output_points_path: dense_path.to_string_lossy().to_string(),
        replaced_points_bin: false,
        dense_ply_path: String::new(),
        backup_points_path: backup_path.to_string_lossy().to_string(),
        roma: String::new(),
        max_points: 0,
    };
    if previous
        .as_ref()
        .map(|state| state.status.as_str() != "completed_unconfirmed")
        .unwrap_or(true)
    {
        let _ = write_densify_state(
            &root,
            &DensifyPersistedState {
                status: "completed_unconfirmed".to_string(),
                message: "发现未确认的致密化结果".to_string(),
                result: Some(result.clone()),
                log_path: previous.map(|state| state.log_path).unwrap_or_default(),
                updated_at: now_millis(),
            },
        );
    }

    Ok(Some(result))
}

/// Normalize a path for the webview asset protocol.
///
/// Tauri's `convertFileSrc` mishandles Windows backslash paths (see
/// tauri-apps/tauri#8244), so we hand the frontend forward-slash paths instead.
fn web_path(p: &std::path::Path) -> String {
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

struct ChildProcessGuard {
    child: Option<Child>,
}

impl Drop for ChildProcessGuard {
    fn drop(&mut self) {
        let Some(child) = self.child.as_mut() else {
            return;
        };
        if child.try_wait().ok().flatten().is_none() {
            kill_process_tree(child.id());
            let _ = child.wait();
        }
    }
}

fn run_guarded_command(mut command: Command) -> bool {
    let child = command.stdout(Stdio::null()).stderr(Stdio::null()).spawn();
    let Ok(child) = child else {
        return false;
    };
    let mut guard = ChildProcessGuard { child: Some(child) };
    let status = guard.child.as_mut().and_then(|child| child.wait().ok());
    guard.child = None;
    status.map(|value| value.success()).unwrap_or(false)
}

fn locate_densify_plugin_path(root: &std::path::Path) -> std::path::PathBuf {
    let candidates = [
        root.join("tools").join("lichtfeld-densification-plugin"),
        root.join("third_party")
            .join("lichtfeld-densification-plugin"),
    ];
    candidates
        .into_iter()
        .find(|path| path.join("densify.py").exists())
        .unwrap_or_else(|| root.join("tools").join("lichtfeld-densification-plugin"))
}

fn densify_env_root(app: &AppHandle) -> std::path::PathBuf {
    let _ = app;
    tool_resolver::resolve_app_root()
}

fn densify_state_root(app: &AppHandle) -> Result<std::path::PathBuf, String> {
    app.path()
        .app_local_data_dir()
        .map_err(|error| format!("无法定位致密化运行时目录: {}", error))
}

fn active_densify_runtime(app: &AppHandle) -> Option<std::path::PathBuf> {
    let state_root = densify_state_root(app).ok()?;
    let value: serde_json::Value = serde_json::from_slice(
        &std::fs::read(state_root.join("state").join("active-densify.json")).ok()?,
    )
    .ok()?;
    let runtime = std::path::PathBuf::from(value.get("runtimePath")?.as_str()?);
    runtime.join("complete.marker").is_file().then_some(runtime)
}

fn densify_python_path(app: &AppHandle, resource_root: &std::path::Path) -> std::path::PathBuf {
    if active_densify_runtime(app).is_some() {
        let bundled = tool_resolver::resolve_resource_path("binaries/python/python.exe");
        if bundled.is_file() {
            return bundled;
        }
    }
    tool_resolver::locate_densify_python(resource_root)
}

fn configure_densify_runtime_env(command: &mut Command, runtime: Option<&std::path::Path>) {
    command.env("PYTHONNOUSERSITE", "1").env_remove("PYTHONPATH");
    let Some(runtime) = runtime else {
        return;
    };
    let site_packages = runtime.join("site-packages");
    command
        .env("XPANO_DENSIFY_SITE_PACKAGES", &site_packages)
        .env("TORCH_HOME", runtime.join("model-cache"));
    let mut paths = vec![
        site_packages.join("torch").join("lib"),
        site_packages.join("open3d"),
    ];
    if let Some(existing) = std::env::var_os("PATH") {
        paths.extend(std::env::split_paths(&existing));
    }
    if let Ok(joined) = std::env::join_paths(paths) {
        command.env("PATH", joined);
    }
}

fn run_output(mut cmd: Command) -> Result<std::process::Output, String> {
    #[cfg(target_os = "windows")]
    cmd.creation_flags(0x08000000);
    cmd.output().map_err(|e| format!("启动命令失败: {}", e))
}

fn command_text(output: &std::process::Output) -> String {
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    if stderr.is_empty() {
        stdout
    } else if stdout.is_empty() {
        stderr
    } else {
        format!("{}\n{}", stdout, stderr)
    }
}

fn hash_path_state(path: &std::path::Path, hasher: &mut DefaultHasher) {
    path.to_string_lossy().hash(hasher);
    match std::fs::metadata(path) {
        Ok(metadata) => {
            metadata.len().hash(hasher);
            metadata
                .modified()
                .ok()
                .and_then(|value| value.duration_since(UNIX_EPOCH).ok())
                .map(|value| value.as_millis())
                .hash(hasher);
        }
        Err(_) => {
            0u8.hash(hasher);
        }
    }
}

fn densify_env_signature(
    root: &std::path::Path,
    python_path: &std::path::Path,
    plugin_path: &std::path::Path,
    active_runtime: Option<&std::path::Path>,
) -> String {
    let mut hasher = DefaultHasher::new();
    hash_path_state(python_path, &mut hasher);
    hash_path_state(&plugin_path.join("densify.py"), &mut hasher);
    hash_path_state(
        &tool_resolver::resolve_script_path("scripts/run_lichtfeld_densify_standalone.py"),
        &mut hasher,
    );
    hash_path_state(&root.join(".venv-densify").join("pyvenv.cfg"), &mut hasher);
    if let Some(runtime) = active_runtime {
        hash_path_state(&runtime.join("runtime.json"), &mut hasher);
        hash_path_state(&runtime.join("complete.marker"), &mut hasher);
    }
    format!("{:016x}", hasher.finish())
}

fn densify_env_cache() -> &'static Mutex<Option<(String, DensifyEnvStatus)>> {
    static CACHE: OnceLock<Mutex<Option<(String, DensifyEnvStatus)>>> = OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(None))
}

fn emit_densify_task(
    app: &AppHandle,
    task: &str,
    kind: &str,
    message: &str,
    progress: Option<f64>,
) {
    let _ = app.emit(
        "densify:task",
        serde_json::json!({
            "task": task,
            "kind": kind,
            "message": message,
            "progress": progress,
        }),
    );
}

fn parse_tqdm_percent(line: &str) -> Option<f64> {
    let before_bar = line.split("%|").next()?.trim();
    let token = before_bar.split_whitespace().last()?;
    token.parse::<f64>().ok()
}

fn parse_bootstrap_event(line: &str) -> Option<(String, String, Option<f64>)> {
    let payload = line.trim().strip_prefix("BOOTSTRAP_EVENT:")?;
    let value: serde_json::Value = serde_json::from_str(payload).ok()?;
    Some((
        value.get("phase")?.as_str()?.to_string(),
        value.get("message")?.as_str()?.to_string(),
        value.get("progress").and_then(serde_json::Value::as_f64),
    ))
}

fn select_densify_profile(
    use_cuda: bool,
    nvidia_probe_ok: bool,
) -> (&'static str, Option<&'static str>) {
    if !use_cuda {
        ("cpu", None)
    } else if nvidia_probe_ok {
        ("cuda", None)
    } else {
        (
            "cpu",
            Some("未检测到可用的 NVIDIA 驱动，已改用 CPU 配置"),
        )
    }
}

fn nvidia_driver_probe() -> bool {
    let mut candidates = Vec::new();
    if let Some(system_root) = std::env::var_os("SystemRoot") {
        candidates.push(
            std::path::PathBuf::from(system_root)
                .join("System32")
                .join("nvidia-smi.exe"),
        );
    }
    for variable in ["ProgramW6432", "ProgramFiles"] {
        if let Some(program_files) = std::env::var_os(variable) {
            candidates.push(
                std::path::PathBuf::from(program_files)
                    .join("NVIDIA Corporation")
                    .join("NVSMI")
                    .join("nvidia-smi.exe"),
            );
        }
    }
    let Some(nvidia_smi) = candidates.into_iter().find(|path| path.is_file()) else {
        return false;
    };
    let mut cmd = Command::new(nvidia_smi);
    cmd.args([
        "--query-gpu=name,driver_version",
        "--format=csv,noheader",
    ]);
    match run_output(cmd) {
        Ok(output) => {
            output.status.success()
                && !String::from_utf8_lossy(&output.stdout)
                    .trim()
                    .is_empty()
        }
        Err(_) => false,
    }
}

fn run_streaming_densify_command(
    mut cmd: Command,
    app: AppHandle,
    task: &'static str,
    log_path: Option<std::path::PathBuf>,
) -> Result<String, String> {
    let state = app.state::<AppState>();
    {
        let pid = state.densify_pid.lock().map_err(|e| e.to_string())?;
        if pid.is_some() {
            return Err("已有致密化相关任务正在运行".to_string());
        }
    }

    #[cfg(target_os = "windows")]
    cmd.creation_flags(0x08000000);
    cmd.env("PYTHONIOENCODING", "utf-8:replace")
        .env("PYTHONUTF8", "1")
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env("PIP_DISABLE_PIP_VERSION_CHECK", "1")
        .env("PIP_NO_INPUT", "1")
        .env("PIP_PROGRESS_BAR", "off");
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped());

    let mut child = cmd.spawn().map_err(|e| format!("启动命令失败: {}", e))?;
    let pid = child.id();
    {
        let mut active = state.densify_pid.lock().map_err(|e| e.to_string())?;
        *active = Some(pid);
    }
    emit_densify_task(&app, task, "start", "任务已启动", Some(0.0));

    let output_text = Arc::new(Mutex::new(String::new()));
    let log_file = if let Some(path) = &log_path {
        Some(Arc::new(Mutex::new(
            std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(path)
                .map_err(|e| format!("打开致密化日志失败: {}", e))?,
        )))
    } else {
        None
    };
    let mut readers = Vec::new();

    if let Some(stream) = child.stdout.take() {
        let app_for_thread = app.clone();
        let output_for_thread = Arc::clone(&output_text);
        let log_for_thread = log_file.clone();
        readers.push(thread::spawn(move || {
            let reader = BufReader::new(stream);
            for line in reader.lines().map_while(Result::ok) {
                if let Ok(mut text) = output_for_thread.lock() {
                    text.push_str(&line);
                    text.push('\n');
                }
                if let Some(file) = &log_for_thread {
                    if let Ok(mut file) = file.lock() {
                        let _ = writeln!(file, "{}", line);
                    }
                }
                if let Some((_phase, message, progress)) = parse_bootstrap_event(&line) {
                    emit_densify_task(&app_for_thread, task, "progress", &message, progress);
                } else if let Some(value) = line.trim().strip_prefix("PROGRESS:") {
                    let mut parts = value.trim().splitn(2, ':');
                    let progress_text = parts.next().unwrap_or("").trim();
                    let message = parts.next().unwrap_or(progress_text).trim();
                    let progress = progress_text.parse::<f64>().ok();
                    emit_densify_task(&app_for_thread, task, "progress", message, progress);
                } else if task == "run" && line.contains("%|") {
                    if let Some(download_progress) = parse_tqdm_percent(&line) {
                        let mapped_progress = 10.0 + download_progress.clamp(0.0, 100.0) * 0.2;
                        emit_densify_task(
                            &app_for_thread,
                            task,
                            "progress",
                            "正在下载 RoMa 权重",
                            Some(mapped_progress),
                        );
                    }
                    emit_densify_task(&app_for_thread, task, "stdout", &line, None);
                } else {
                    emit_densify_task(&app_for_thread, task, "stdout", &line, None);
                }
            }
        }));
    }

    if let Some(stream) = child.stderr.take() {
        let app_for_thread = app.clone();
        let output_for_thread = Arc::clone(&output_text);
        let log_for_thread = log_file.clone();
        readers.push(thread::spawn(move || {
            let reader = BufReader::new(stream);
            for line in reader.lines().map_while(Result::ok) {
                if let Ok(mut text) = output_for_thread.lock() {
                    text.push_str(&line);
                    text.push('\n');
                }
                if let Some(file) = &log_for_thread {
                    if let Ok(mut file) = file.lock() {
                        let _ = writeln!(file, "{}", line);
                    }
                }
                emit_densify_task(&app_for_thread, task, "stderr", &line, None);
            }
        }));
    }

    let status = child.wait().map_err(|e| format!("等待命令失败: {}", e));
    for reader in readers {
        let _ = reader.join();
    }

    let should_clear = state
        .densify_pid
        .lock()
        .map(|mut active| {
            if *active == Some(pid) {
                *active = None;
                true
            } else {
                false
            }
        })
        .unwrap_or(false);

    let text = output_text
        .lock()
        .map(|value| value.trim().to_string())
        .unwrap_or_default();

    match status {
        Ok(status) if status.success() => {
            emit_densify_task(&app, task, "done", "任务完成", Some(100.0));
            Ok(text)
        }
        Ok(status) => {
            let stopped = !should_clear;
            let message = if stopped {
                "任务已停止".to_string()
            } else if text.is_empty() {
                format!("命令退出码: {}", status)
            } else {
                text.clone()
            };
            emit_densify_task(
                &app,
                task,
                if stopped { "stopped" } else { "error" },
                &message,
                None,
            );
            Err(message)
        }
        Err(error) => {
            emit_densify_task(&app, task, "error", &error, None);
            Err(error)
        }
    }
}

pub(crate) struct AppState {
    pipeline: Mutex<PipelineState>,
    densify_pid: Mutex<Option<u32>>,
    lichtfeld_readiness: Mutex<LichtfeldReadinessCache>,
    pub(crate) batch: Mutex<batch::BatchCoordinator>,
}

fn cli_arg_value(args: &[String], name: &str) -> Option<String> {
    args.windows(2)
        .find_map(|pair| (pair[0] == name).then(|| pair[1].clone()))
}

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RuntimeLaunchEnvironment {
    #[serde(default)]
    site_packages: String,
    #[serde(default)]
    metashape: Option<RuntimeMetashapeProbe>,
}

#[derive(Clone, Debug, Deserialize)]
struct RuntimeMetashapeProbe {
    status: String,
}

#[derive(Debug, Deserialize)]
struct RuntimeReadinessFailure {
    code: String,
    message: String,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct LichtfeldDeviceReadiness {
    status: String,
    device_count: u32,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct LichtfeldInputReadiness {
    status: String,
    code: String,
    message: String,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct LichtfeldRuntimeReadiness {
    status: String,
    version: String,
    cuda: LichtfeldDeviceReadiness,
    vulkan: LichtfeldDeviceReadiness,
    #[serde(default)]
    dataset: LichtfeldInputReadiness,
    #[serde(default)]
    output: LichtfeldInputReadiness,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct TrainingReadinessStatus {
    runtime_available: bool,
    runtime_path: String,
    runtime_code: String,
    runtime_message: String,
    cuda_available: bool,
    vulkan_available: bool,
    dataset_available: bool,
    dataset_message: String,
    geometry_available: bool,
    output_available: bool,
    output_message: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct RuntimeReadinessStatus {
    bundled: String,
    metashape: String,
    densification: String,
    detail: String,
}

fn runtime_readiness_command(
    app: &tauri::AppHandle,
    args: &[String],
    command_name: &str,
) -> Result<Command, String> {
    let root = tool_resolver::resolve_app_root();
    let script = tool_resolver::resolve_script_path("scripts/runtime_readiness.py");
    if !script.exists() {
        return Err(format!("运行时就绪脚本不存在: {}", script.display()));
    }
    let state_root = app
        .path()
        .app_local_data_dir()
        .map_err(|error| format!("无法定位应用运行时目录: {}", error))?;
    let backend = cli_arg_value(args, "--backend").unwrap_or_else(|| "metashape".to_string());
    let metashape = cli_arg_value(args, "--metashape").unwrap_or_else(|| "metashape.exe".to_string());
    let mut cmd = Command::new(tool_resolver::resolve_python(""));
    cmd.env("PYTHONIOENCODING", "utf-8:replace")
        .env("PYTHONUTF8", "1")
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env("PYTHONNOUSERSITE", "1");
    #[cfg(target_os = "windows")]
    cmd.creation_flags(0x08000000);
    cmd.arg(script)
        .arg(command_name)
        .arg("--root")
        .arg(tool_resolver::plain_windows_path(&root))
        .arg("--state-root")
        .arg(tool_resolver::plain_windows_path(&state_root))
        .arg("--backend")
        .arg(backend)
        .arg("--metashape")
        .arg(metashape)
        .env("XPANO_FFMPEG", tool_resolver::locate_ffmpeg())
        .env("XPANO_FFPROBE", tool_resolver::locate_ffprobe())
        .env("XPANO_COLMAP", detect_colmap());
    Ok(cmd)
}

fn parse_runtime_result(text: &str) -> Result<RuntimeLaunchEnvironment, String> {
    let mut result = None;
    let mut failure = None;
    for line in text.lines() {
        if let Some(payload) = line.strip_prefix("RUNTIME_RESULT:") {
            result = serde_json::from_str(payload).ok();
        } else if let Some(payload) = line.strip_prefix("RUNTIME_ERROR:") {
            failure = serde_json::from_str::<RuntimeReadinessFailure>(payload).ok();
        }
    }
    if let Some(result) = result {
        return Ok(result);
    }
    if let Some(failure) = failure {
        return Err(format!("{}: {}", failure.code, failure.message));
    }
    Err("运行时就绪检查没有返回结构化结果".to_string())
}

fn runtime_failure(text: &str) -> Option<RuntimeReadinessFailure> {
    text.lines().find_map(|line| {
        line.strip_prefix("RUNTIME_ERROR:")
            .and_then(|payload| serde_json::from_str(payload).ok())
    })
}

#[tauri::command]
fn probe_runtime_readiness(app: tauri::AppHandle) -> RuntimeReadinessStatus {
    let metashape_path = detect_metashape();
    let base_args = vec![
        "--backend".to_string(),
        "colmap".to_string(),
        "--metashape".to_string(),
        metashape_path.clone(),
    ];
    let bundled_output = runtime_readiness_command(&app, &base_args, "probe")
        .and_then(|mut command| command.output().map_err(|error| error.to_string()));
    let bundled_text = bundled_output
        .as_ref()
        .map(|output| {
            format!(
                "{}\n{}",
                String::from_utf8_lossy(&output.stdout),
                String::from_utf8_lossy(&output.stderr)
            )
        })
        .unwrap_or_default();
    let bundled_ready = bundled_output
        .as_ref()
        .is_ok_and(|output| output.status.success() && parse_runtime_result(&bundled_text).is_ok());
    if !bundled_ready {
        let detail = runtime_failure(&bundled_text)
            .map(|failure| format!("{}: {}", failure.code, failure.message))
            .unwrap_or_else(|| bundled_output.err().unwrap_or_else(|| "Bundled runtime probe failed".to_string()));
        return RuntimeReadinessStatus {
            bundled: "corrupt".to_string(),
            metashape: "error".to_string(),
            densification: if active_densify_runtime(&app).is_some() {
                "ready".to_string()
            } else {
                "downloadable".to_string()
            },
            detail,
        };
    }

    let metashape_args = vec![
        "--backend".to_string(),
        "metashape".to_string(),
        "--metashape".to_string(),
        metashape_path,
    ];
    let metashape_output = runtime_readiness_command(&app, &metashape_args, "probe")
        .and_then(|mut command| command.output().map_err(|error| error.to_string()));
    let metashape_text = metashape_output
        .as_ref()
        .map(|output| {
            format!(
                "{}\n{}",
                String::from_utf8_lossy(&output.stdout),
                String::from_utf8_lossy(&output.stderr)
            )
        })
        .unwrap_or_default();
    let failure = runtime_failure(&metashape_text);
    let metashape = if metashape_output.as_ref().is_ok_and(|output| output.status.success()) {
        parse_runtime_result(&metashape_text)
            .ok()
            .and_then(|result| result.metashape.map(|value| value.status))
            .unwrap_or_else(|| "error".to_string())
    } else {
        match failure.as_ref().map(|value| value.code.as_str()) {
            Some("METASHAPE_MISSING") | Some("METASHAPE_PYTHON_MISSING") => "missing".to_string(),
            Some("UNSUPPORTED_ABI") => "unsupported".to_string(),
            _ => "error".to_string(),
        }
    };
    RuntimeReadinessStatus {
        bundled: "ready".to_string(),
        metashape,
        densification: if active_densify_runtime(&app).is_some() {
            "ready".to_string()
        } else {
            "downloadable".to_string()
        },
        detail: failure
            .map(|failure| format!("{}: {}", failure.code, failure.message))
            .unwrap_or_default(),
    }
}

fn run_preflight_environment_check(
    app: &tauri::AppHandle,
    args: &[String],
) -> Result<RuntimeLaunchEnvironment, String> {

    let _ = app.emit(
        "pipeline:progress",
        pipeline::PipelineProgressEvent {
            phase: String::new(),
            stage: None,
            track_id: None,
            percent: 0.0,
            message: "正在检查并配置运行环境".to_string(),
            elapsed: 0,
            phase_percents: pipeline::PhasePercents {
                extract: 0.0,
                align: 0.0,
                export: 0.0,
            },
            current: None,
            total: None,
            eta_seconds: None,
            aligned_cameras: None,
            total_cameras: None,
            alignment_rate: None,
            loss: None,
            splat_count: None,
            trainer_state: None,
            heartbeat: false,
        },
    );

    let mut cmd = runtime_readiness_command(app, args, "ensure")?;
    let output = cmd
        .output()
        .map_err(|e| format!("启动环境检查失败: {}", e))?;
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    for line in stdout.lines().filter(|line| !line.trim().is_empty()) {
        let _ = app.emit(
            "pipeline:progress",
            pipeline::PipelineProgressEvent {
                phase: String::new(),
                stage: None,
                track_id: None,
                percent: 0.0,
                message: line.trim().to_string(),
                elapsed: 0,
                phase_percents: pipeline::PhasePercents {
                    extract: 0.0,
                    align: 0.0,
                    export: 0.0,
                },
                current: None,
                total: None,
                eta_seconds: None,
                aligned_cameras: None,
                total_cameras: None,
                alignment_rate: None,
                loss: None,
                splat_count: None,
                trainer_state: None,
                heartbeat: false,
            },
        );
    }
    let combined = format!("{}\n{}", stdout, stderr);
    if output.status.success() {
        parse_runtime_result(&combined)
    } else {
        parse_runtime_result(&combined).and_then(|_| Err(combined.trim().to_string()))
    }
}

enum ReconstructionPreflightError {
    Cancelled,
    Failed(String),
}

fn emit_preflight_log(
    app: &tauri::AppHandle,
    context: &job::JobContext,
    message: &str,
    elapsed: u64,
) {
    if let Ok(event) = job::record_log_impl(context, message) {
        let _ = app.emit("job:event", event);
    }
    let _ = app.emit(
        "pipeline:progress",
        pipeline::PipelineProgressEvent {
            phase: String::new(),
            stage: None,
            track_id: None,
            percent: 0.0,
            message: message.to_string(),
            elapsed,
            phase_percents: pipeline::PhasePercents {
                extract: 0.0,
                align: 0.0,
                export: 0.0,
            },
            current: None,
            total: None,
            eta_seconds: None,
            aligned_cameras: None,
            total_cameras: None,
            alignment_rate: None,
            loss: None,
            splat_count: None,
            trainer_state: None,
            heartbeat: false,
        },
    );
}

fn emit_preflight_progress(
    app: &tauri::AppHandle,
    context: &job::JobContext,
    message: &str,
    elapsed: u64,
    percent: f64,
    heartbeat: bool,
) {
    if let Ok(events) = job::record_progress_impl(
        context,
        job::JobProgressUpdate {
            stage_id: Some("input.validate".to_string()),
            percent: Some(percent),
            message: message.to_string(),
            heartbeat,
            ..Default::default()
        },
    ) {
        for event in events {
            let _ = app.emit("job:event", event);
        }
    }
    let _ = app.emit(
        "pipeline:progress",
        pipeline::PipelineProgressEvent {
            phase: "align".to_string(),
            stage: Some("input.validate".to_string()),
            track_id: None,
            percent,
            message: message.to_string(),
            elapsed,
            phase_percents: pipeline::PhasePercents {
                extract: 100.0,
                align: 0.0,
                export: 0.0,
            },
            current: None,
            total: None,
            eta_seconds: None,
            aligned_cameras: None,
            total_cameras: None,
            alignment_rate: None,
            loss: None,
            splat_count: None,
            trainer_state: None,
            heartbeat,
        },
    );
}

fn run_reconstruction_preflight(
    state: &State<'_, AppState>,
    app: &tauri::AppHandle,
    args: &[String],
    context: &job::JobContext,
) -> Result<RuntimeLaunchEnvironment, ReconstructionPreflightError> {
    let mut cmd = runtime_readiness_command(app, args, "ensure")
        .map_err(ReconstructionPreflightError::Failed)?;
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
    let mut child = cmd
        .spawn()
        .map_err(|error| ReconstructionPreflightError::Failed(format!("启动环境检查失败: {}", error)))?;
    let pid = child.id();
    let cancelled = match state.pipeline.lock() {
        Ok(mut pipeline) => match pipeline.register_external_process(pid, context.clone()) {
            Ok(cancelled) => cancelled,
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(ReconstructionPreflightError::Failed(error));
            }
        },
        Err(error) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(ReconstructionPreflightError::Failed(error.to_string()));
        }
    };
    let started = std::time::Instant::now();
    emit_preflight_progress(app, context, "正在检查并配置运行环境", 0, 0.0, false);
    let captured = Arc::new(Mutex::new(String::new()));

    let stdout_thread = child.stdout.take().map(|stdout| {
        let app = app.clone();
        let context = context.clone();
        let captured = captured.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                if !line.trim().is_empty() {
                    if let Ok(mut output) = captured.lock() {
                        output.push_str(&line);
                        output.push('\n');
                    }
                    let message = line
                        .strip_prefix("RUNTIME_EVENT:")
                        .and_then(|payload| serde_json::from_str::<serde_json::Value>(payload).ok())
                        .and_then(|value| value.get("message").and_then(serde_json::Value::as_str).map(str::to_string));
                    if let Some(message) = message {
                        emit_preflight_log(&app, &context, &message, started.elapsed().as_secs());
                    }
                }
            }
        })
    });
    let stderr_thread = child.stderr.take().map(|stderr| {
        let app = app.clone();
        let context = context.clone();
        let captured = captured.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                if !line.trim().is_empty() {
                    if let Ok(mut output) = captured.lock() {
                        output.push_str(&line);
                        output.push('\n');
                    }
                    emit_preflight_log(&app, &context, line.trim(), started.elapsed().as_secs());
                }
            }
        })
    });

    let mut last_heartbeat = 0;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break Ok(status),
            Ok(None) => {
                let elapsed = started.elapsed().as_secs();
                if elapsed > last_heartbeat {
                    last_heartbeat = elapsed;
                    emit_preflight_progress(
                        app,
                        context,
                        "运行环境检查中",
                        elapsed,
                        0.0,
                        true,
                    );
                }
                std::thread::sleep(std::time::Duration::from_millis(100));
            }
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                break Err(error);
            }
        }
    };
    if let Some(thread) = stdout_thread {
        let _ = thread.join();
    }
    if let Some(thread) = stderr_thread {
        let _ = thread.join();
    }
    let was_cancelled = state
        .pipeline
        .lock()
        .map_err(|error| ReconstructionPreflightError::Failed(error.to_string()))?
        .finish_registered_process(pid)
        .map_err(ReconstructionPreflightError::Failed)?
        || cancelled.load(std::sync::atomic::Ordering::SeqCst);
    if was_cancelled {
        return Err(ReconstructionPreflightError::Cancelled);
    }
    let status = status
        .map_err(|error| ReconstructionPreflightError::Failed(error.to_string()))?;
    let captured = captured
        .lock()
        .map_err(|error| ReconstructionPreflightError::Failed(error.to_string()))?
        .clone();
    if !status.success() {
        let message = match parse_runtime_result(&captured) {
            Err(message) => message,
            Ok(_) => format!(
                "环境检查失败，退出码 {}",
                status.code().map_or_else(|| "unknown".to_string(), |code| code.to_string())
            ),
        };
        return Err(ReconstructionPreflightError::Failed(message));
    }
    let environment = parse_runtime_result(&captured).map_err(ReconstructionPreflightError::Failed)?;
    emit_preflight_progress(
        app,
        context,
        "运行环境检查完成",
        started.elapsed().as_secs(),
        1.0,
        false,
    );
    Ok(environment)
}

#[tauri::command]
fn start_pipeline(
    state: State<'_, AppState>,
    app: tauri::AppHandle,
    python_exe: String,
    script: String,
    args: Vec<String>,
) -> Result<String, String> {
    batch::ensure_manual_startable(&app, state.inner()).map_err(|error| error.message)?;
    let environment = run_preflight_environment_check(&app, &args)?;
    let mut pipeline = state.pipeline.lock().map_err(|e| e.to_string())?;
    pipeline.start_with_metashape_runtime(
        app,
        &python_exe,
        &script,
        &args,
        (!environment.site_packages.is_empty()).then_some(environment.site_packages.as_str()),
    )?;
    Ok("Pipeline started".into())
}

pub(crate) fn start_reconstruction_job_blocking(
    app: tauri::AppHandle,
    project_root: String,
    expected_revision: u64,
    plan_id: String,
    python_exe: String,
    script: String,
    args: Vec<String>,
    task_id: Option<String>,
) -> Result<contracts::XpanoProjectV2, project::ProjectCommandError> {
    let state = app.state::<AppState>();
    {
        let pipeline = state.pipeline.lock().map_err(|error| {
            project::ProjectCommandError::new("job_conflict", error.to_string())
        })?;
        pipeline.ensure_startable().map_err(|error| {
            project::ProjectCommandError::new("job_conflict", error)
        })?;
    }
    let root = std::path::Path::new(&project_root);
    let active_plan = reconstruction::active_execution_plan_impl(root)?;
    reconstruction::begin_reconstruction_job_from_plan_impl(
        root,
        expected_revision,
        &plan_id,
    )?;
    let (job_context, _) = match job::begin_job_with_task_impl(
        root,
        contracts::ProjectWorkspace::Reconstruction,
        task_id,
    ) {
        Ok(started) => started,
        Err(error) => {
            let _ = reconstruction::fail_reconstruction_job_impl(root);
            return Err(error);
        }
    };
    for node in active_plan.nodes.iter().filter(|node| node.skip_reason.is_some()) {
        match job::record_skipped_stage_impl(
            &job_context,
            &node.stage_id,
            node.skip_reason.as_deref().unwrap_or_default(),
        ) {
            Ok(event) => {
                let _ = app.emit("job:event", event);
            }
            Err(error) => {
                reconstruction::fail_reconstruction_job_impl(root)?;
                job::finish_job_impl(
                    &job_context,
                    contracts::JobState::Failed,
                    &error.message,
                )?;
                return Err(error);
            }
        }
    }
    let project = project::read_project(root)?;
    let _ = app.emit(
        "project:updated",
        media::ProjectUpdatedEvent {
            project_root: project_root.clone(),
            project: project.clone(),
        },
    );
    let environment = match run_reconstruction_preflight(&state, &app, &args, &job_context) {
        Ok(environment) => environment,
        Err(error) => {
        let (state_value, message, code) = match error {
            ReconstructionPreflightError::Cancelled => (
                contracts::JobState::Cancelled,
                "任务已取消".to_string(),
                "job_conflict",
            ),
            ReconstructionPreflightError::Failed(message) => (
                contracts::JobState::Failed,
                message,
                "backend_unavailable",
            ),
        };
        if state_value == contracts::JobState::Cancelled {
            reconstruction::interrupt_reconstruction_job_impl(root)?;
        } else {
            reconstruction::fail_reconstruction_job_impl(root)?;
        }
        job::finish_job_impl(&job_context, state_value, &message)?;
        let failed = project::read_project(root)?;
        let _ = app.emit(
            "project:updated",
            media::ProjectUpdatedEvent {
                project_root,
                project: failed,
            },
        );
        return Err(project::ProjectCommandError::new(code, message));
        }
    };

    let mut pipeline = state.pipeline.lock().map_err(|error| {
        project::ProjectCommandError::new("job_conflict", error.to_string())
    })?;
    pipeline.ensure_startable().map_err(|error| {
        project::ProjectCommandError::new("job_conflict", error)
    })?;
    if let Err(error) = pipeline.start_registered_reconstruction(
        app.clone(),
        &python_exe,
        &script,
        &args,
        job_context.clone(),
        (!environment.site_packages.is_empty()).then_some(environment.site_packages.as_str()),
    ) {
        reconstruction::fail_reconstruction_job_impl(root)?;
        job::finish_job_impl(
            &job_context,
            contracts::JobState::Failed,
            &format!("任务启动失败: {}", error),
        )?;
        let failed = project::read_project(root)?;
        let _ = app.emit(
            "project:updated",
            media::ProjectUpdatedEvent {
                project_root,
                project: failed,
            },
        );
        return Err(project::ProjectCommandError::new(
            "backend_unavailable",
            error,
        ));
    }
    Ok(project)
}

#[tauri::command]
async fn start_reconstruction_job(
    app: tauri::AppHandle,
    project_root: String,
    expected_revision: u64,
    plan_id: String,
    python_exe: String,
    script: String,
    args: Vec<String>,
) -> Result<contracts::XpanoProjectV2, project::ProjectCommandError> {
    batch::ensure_manual_startable(&app, app.state::<AppState>().inner())?;
    tauri::async_runtime::spawn_blocking(move || {
        start_reconstruction_job_blocking(
            app,
            project_root,
            expected_revision,
            plan_id,
            python_exe,
            script,
            args,
            None,
        )
    })
    .await
    .map_err(|error| {
        project::ProjectCommandError::new(
            "backend_unavailable",
            format!("reconstruction supervisor stopped unexpectedly: {}", error),
        )
    })?
}

fn append_training_flag(args: &mut Vec<String>, enabled: bool, flag: &str) {
    if enabled {
        args.push(flag.to_string());
    }
}

fn parse_lichtfeld_readiness_result(
    text: &str,
) -> Result<LichtfeldRuntimeReadiness, project::ProjectCommandError> {
    let mut result = None;
    let mut failure = None;
    for line in text.lines() {
        if let Some(payload) = line.strip_prefix("LFS_READINESS_RESULT:") {
            result = serde_json::from_str(payload).ok();
        } else if let Some(payload) = line.strip_prefix("LFS_READINESS_ERROR:") {
            failure = serde_json::from_str::<RuntimeReadinessFailure>(payload).ok();
        }
    }
    if let Some(result) = result {
        return Ok(result);
    }
    if let Some(failure) = failure {
        return Err(project::ProjectCommandError::new(failure.code.as_str(), failure.message));
    }
    Err(project::ProjectCommandError::new(
        "LFS_READINESS_FAILED",
        "LichtFeld readiness probe returned no structured result",
    ))
}

fn ensure_lichtfeld_training_ready(
    readiness: &LichtfeldRuntimeReadiness,
) -> Result<(), project::ProjectCommandError> {
    if readiness.status == "ready" {
        return Ok(());
    }
    for check in [&readiness.dataset, &readiness.output] {
        if check.status != "ready" {
            return Err(project::ProjectCommandError::new(
                if check.code.is_empty() {
                    "LFS_READINESS_FAILED"
                } else {
                    check.code.as_str()
                },
                if check.message.is_empty() {
                    "LichtFeld training preflight did not complete"
                } else {
                    check.message.as_str()
                },
            ));
        }
    }
    Err(project::ProjectCommandError::new(
        "LFS_READINESS_FAILED",
        "LichtFeld training preflight did not complete",
    ))
}

#[derive(Clone, Debug)]
struct LichtfeldRuntime {
    resource_root: std::path::PathBuf,
    state_root: std::path::PathBuf,
    executable: std::path::PathBuf,
    readiness_script: std::path::PathBuf,
    training_script: std::path::PathBuf,
    python: std::path::PathBuf,
    profile_root: std::path::PathBuf,
}

fn lichtfeld_resource_root(
    packaged_root: &std::path::Path,
    development_fallback: Option<&std::path::Path>,
    allow_development_fallback: bool,
) -> Result<std::path::PathBuf, project::ProjectCommandError> {
    let manifest = |root: &std::path::Path| root.join("runtime/lichtfeld-studio-manifest.json");
    if manifest(packaged_root).is_file() {
        return Ok(packaged_root.to_path_buf());
    }
    if allow_development_fallback {
        if let Some(root) = development_fallback.filter(|root| manifest(root).is_file()) {
            return Ok(root.to_path_buf());
        }
    }
    Err(project::ProjectCommandError::new(
        "LFS_RUNTIME_CORRUPT",
        "Bundled LichtFeld runtime manifest is missing",
    ))
}

impl LichtfeldRuntime {
    fn resolve(app: &AppHandle) -> Result<Self, project::ProjectCommandError> {
        let packaged_root = app.path().resource_dir().map_err(|error| {
            project::ProjectCommandError::new(
                "LFS_RUNTIME_CORRUPT",
                format!("failed to resolve bundled LichtFeld resources: {error}"),
            )
        })?;
        let development_manifest = cfg!(debug_assertions).then(|| {
            tool_resolver::resolve_bundled_resource_path("runtime/lichtfeld-studio-manifest.json")
        });
        let development_root = development_manifest.as_deref().and_then(|manifest| {
            manifest.parent().and_then(std::path::Path::parent)
        });
        let resource_root = lichtfeld_resource_root(
            &packaged_root,
            development_root,
            cfg!(debug_assertions),
        )?;
        let state_root = app.path().app_local_data_dir().map_err(|error| {
            project::ProjectCommandError::new(
                "backend_unavailable",
                format!("failed to resolve LichtFeld state directory: {error}"),
            )
        })?;
        Self::from_roots(&resource_root, &state_root)
    }

    fn from_roots(
        resource_root: &std::path::Path,
        state_root: &std::path::Path,
    ) -> Result<Self, project::ProjectCommandError> {
        let resource_root = resource_root.to_path_buf();
        let state_root = state_root.to_path_buf();
        let manifest = resource_root.join("runtime/lichtfeld-studio-manifest.json");
        let content = std::fs::read_to_string(&manifest).map_err(|error| {
            project::ProjectCommandError::new(
                "LFS_RUNTIME_CORRUPT",
                format!("failed to read LichtFeld runtime manifest: {error}"),
            )
        })?;
        let version = serde_json::from_str::<serde_json::Value>(&content)
            .ok()
            .and_then(|value| value.get("version").and_then(serde_json::Value::as_str).map(str::to_owned))
            .filter(|value| {
                !value.is_empty()
                    && value.chars().all(|character| {
                        character.is_ascii_alphanumeric() || matches!(character, '.' | '-' | '_')
                    })
            })
            .ok_or_else(|| {
                project::ProjectCommandError::new(
                    "LFS_RUNTIME_CORRUPT",
                    "LichtFeld runtime manifest has an invalid version",
                )
            })?;
        let runtime = Self {
            executable: resource_root.join("runtime/lichtfeld-studio/bin/LichtFeld-Studio.exe"),
            readiness_script: resource_root.join("scripts/runtime_readiness.py"),
            training_script: resource_root.join("scripts/lichtfeld_training.py"),
            python: resource_root.join("binaries/python/python.exe"),
            profile_root: state_root.join("lichtfeld-studio").join(&version).join("profile"),
            resource_root,
            state_root,
        };
        for (path, label) in [
            (&runtime.executable, "LichtFeld Studio runtime"),
            (&runtime.readiness_script, "LichtFeld readiness supervisor"),
            (&runtime.training_script, "LichtFeld training supervisor"),
            (&runtime.python, "bundled xPano Python"),
        ] {
            if !path.is_file() {
                return Err(project::ProjectCommandError::new(
                    "LFS_RUNTIME_CORRUPT",
                    format!("{} is missing: {}", label, path.display()),
                ));
            }
        }
        Ok(runtime)
    }
}

const LFS_READINESS_CACHE_TTL: std::time::Duration = std::time::Duration::from_secs(10);

#[derive(Clone, Debug, PartialEq, Eq)]
struct LichtfeldReadinessCacheKey {
    project_root: std::path::PathBuf,
    project_revision: u64,
    resource_root: std::path::PathBuf,
    executable_size: u64,
    executable_modified_ns: u128,
    manifest_modified_ns: u128,
    sentinel_metadata: Vec<(String, u64, u128)>,
}

#[derive(Clone, Debug)]
struct CachedLichtfeldReadiness {
    key: LichtfeldReadinessCacheKey,
    checked_at: std::time::Instant,
    readiness: LichtfeldRuntimeReadiness,
}

#[derive(Default)]
struct LichtfeldReadinessCache {
    entry: Option<CachedLichtfeldReadiness>,
}

impl LichtfeldReadinessCache {
    fn get(
        &mut self,
        key: &LichtfeldReadinessCacheKey,
        now: std::time::Instant,
    ) -> Option<LichtfeldRuntimeReadiness> {
        let entry = self.entry.as_ref()?;
        if entry.key != *key || now.duration_since(entry.checked_at) > LFS_READINESS_CACHE_TTL {
            self.entry = None;
            return None;
        }
        Some(entry.readiness.clone())
    }

    fn store(
        &mut self,
        key: LichtfeldReadinessCacheKey,
        readiness: LichtfeldRuntimeReadiness,
        checked_at: std::time::Instant,
    ) {
        self.entry = Some(CachedLichtfeldReadiness {
            key,
            checked_at,
            readiness,
        });
    }
}

fn modified_ns(path: &std::path::Path) -> u128 {
    path.metadata()
        .and_then(|metadata| metadata.modified())
        .ok()
        .and_then(|value| value.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|value| value.as_nanos())
        .unwrap_or(0)
}

fn lichtfeld_readiness_cache_key(
    project_root: &std::path::Path,
    project_revision: u64,
    runtime: &LichtfeldRuntime,
) -> LichtfeldReadinessCacheKey {
    let executable_metadata = runtime.executable.metadata().ok();
    LichtfeldReadinessCacheKey {
        project_root: project_root.to_path_buf(),
        project_revision,
        resource_root: runtime.resource_root.clone(),
        executable_size: executable_metadata.as_ref().map(|metadata| metadata.len()).unwrap_or(0),
        executable_modified_ns: modified_ns(&runtime.executable),
        manifest_modified_ns: modified_ns(
            &runtime.resource_root.join("runtime/lichtfeld-studio-manifest.json"),
        ),
        sentinel_metadata: lichtfeld_sentinel_metadata(&runtime.resource_root),
    }
}

fn lichtfeld_sentinel_metadata(resource_root: &std::path::Path) -> Vec<(String, u64, u128)> {
    let manifest_path = resource_root.join("runtime/lichtfeld-studio-manifest.json");
    let sentinels = std::fs::read_to_string(manifest_path)
        .ok()
        .and_then(|content| serde_json::from_str::<serde_json::Value>(&content).ok())
        .and_then(|manifest| manifest.get("sentinels").and_then(serde_json::Value::as_array).cloned())
        .unwrap_or_default();
    let mut records = sentinels
        .into_iter()
        .filter_map(|value| value.as_str().map(str::to_owned))
        .filter(|relative| {
            let path = std::path::Path::new(relative);
            !path.is_absolute() && !path.components().any(|component| component.as_os_str() == "..")
        })
        .map(|relative| {
            let path = resource_root.join("runtime/lichtfeld-studio").join(&relative);
            let size = path.metadata().map(|metadata| metadata.len()).unwrap_or(0);
            (relative, size, modified_ns(&path))
        })
        .collect::<Vec<_>>();
    records.sort();
    records
}

fn run_lichtfeld_preflight(
    runtime: &LichtfeldRuntime,
    dataset: &std::path::Path,
    output: &std::path::Path,
) -> Result<LichtfeldRuntimeReadiness, project::ProjectCommandError> {
    let mut command = Command::new(tool_resolver::plain_windows_path(&runtime.python));
    command
        .env("PYTHONIOENCODING", "utf-8:replace")
        .env("PYTHONUTF8", "1")
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env("PYTHONNOUSERSITE", "1");
    pipeline::configure_training_supervisor_environment(&mut command, &runtime.python);
    #[cfg(target_os = "windows")]
    command.creation_flags(0x08000000);
    command
        .arg(&runtime.readiness_script)
        .arg("lichtfeld-probe")
        .arg("--root")
        .arg(&runtime.resource_root)
        .arg("--state-root")
        .arg(&runtime.state_root)
        .arg("--backend")
        .arg("colmap")
        .arg("--lfs-executable")
        .arg(tool_resolver::plain_windows_path(&runtime.executable))
        .arg("--profile-root")
        .arg(tool_resolver::plain_windows_path(&runtime.profile_root))
        .arg("--dataset")
        .arg(dataset)
        .arg("--output")
        .arg(output);
    let process = command.output().map_err(|error| {
        project::ProjectCommandError::new(
            "LFS_READINESS_FAILED",
            format!("failed to start LichtFeld readiness probe: {error}"),
        )
    })?;
    let text = format!(
        "{}\n{}",
        String::from_utf8_lossy(&process.stdout),
        String::from_utf8_lossy(&process.stderr)
    );
    if process.status.success() {
        parse_lichtfeld_readiness_result(&text)
    } else {
        match parse_lichtfeld_readiness_result(&text) {
            Err(error) => Err(error),
            Ok(_) => Err(project::ProjectCommandError::new(
                "LFS_READINESS_FAILED",
                format!("LichtFeld readiness probe exited with {}", process.status),
            )),
        }
    }
}

fn training_output_root(project_root: &std::path::Path) -> std::path::PathBuf {
    project_root.join("work").join("training").join("runs")
}

fn geometry_is_ready(project: &contracts::XpanoProjectV2) -> bool {
    matches!(
        project.reconstruction.status,
        contracts::ReconstructionStatus::Complete | contracts::ReconstructionStatus::Stale
    ) && project.geometry.variants.iter().any(|variant| {
        variant.id == project.geometry.active_variant_id
            && variant.status == contracts::PointVariantStatus::Ready
    })
}

fn training_readiness_blocking(
    app: &AppHandle,
    project_root: &std::path::Path,
) -> Result<TrainingReadinessStatus, project::ProjectCommandError> {
    let project = project::read_project(project_root)?;
    let dataset = training::resolve_training_dataset(project_root, &project);
    let dataset_path = dataset
        .as_ref()
        .cloned()
        .unwrap_or_else(|_| project_root.join(".xpano-missing-training-dataset"));
    let output = training_output_root(project_root);
    let geometry_available = geometry_is_ready(&project);
    let runtime = LichtfeldRuntime::resolve(app);
    let runtime_path = runtime
        .as_ref()
        .map(|runtime| tool_resolver::plain_windows_path(&runtime.executable))
        .unwrap_or_default();
    let readiness_result = runtime.and_then(|runtime| {
        let key = lichtfeld_readiness_cache_key(project_root, project.revision, &runtime);
        let now = std::time::Instant::now();
        if let Some(cached) = app
            .state::<AppState>()
            .lichtfeld_readiness
            .lock()
            .ok()
            .and_then(|mut cache| cache.get(&key, now))
        {
            return Ok(cached);
        }
        let readiness = run_lichtfeld_preflight(&runtime, &dataset_path, &output)?;
        if readiness.status == "ready" {
            if let Ok(mut cache) = app.state::<AppState>().lichtfeld_readiness.lock() {
                cache.store(key, readiness.clone(), now);
            }
        }
        Ok(readiness)
    });
    match readiness_result {
        Ok(runtime) => {
            let dataset_available = dataset.is_ok() && runtime.dataset.status == "ready";
            Ok(TrainingReadinessStatus {
                runtime_available: runtime.cuda.status == "ready" && runtime.vulkan.status == "ready",
                runtime_path,
                runtime_code: String::new(),
                runtime_message: String::new(),
                cuda_available: runtime.cuda.status == "ready",
                vulkan_available: runtime.vulkan.status == "ready",
                dataset_available,
                dataset_message: if dataset_available {
                    String::new()
                } else if !runtime.dataset.message.is_empty() {
                    runtime.dataset.message
                } else {
                    dataset.err().map(|error| error.message).unwrap_or_default()
                },
                geometry_available,
                output_available: runtime.output.status == "ready",
                output_message: runtime.output.message,
            })
        }
        Err(error) => Ok(TrainingReadinessStatus {
            runtime_available: false,
            runtime_path,
            runtime_code: error.code,
            runtime_message: error.message,
            cuda_available: false,
            vulkan_available: false,
            dataset_available: dataset.is_ok(),
            dataset_message: dataset.err().map(|value| value.message).unwrap_or_default(),
            geometry_available,
            output_available: false,
            output_message: String::new(),
        }),
    }
}

pub(crate) fn start_training_job_blocking(
    app: tauri::AppHandle,
    project_root: String,
    expected_revision: u64,
    config: training::TrainingConfig,
    task_id: Option<String>,
) -> Result<contracts::XpanoProjectV2, project::ProjectCommandError> {
    let state = app.state::<AppState>();
    {
        let pipeline = state.pipeline.lock().map_err(|error| {
            project::ProjectCommandError::new("job_conflict", error.to_string())
        })?;
        pipeline.ensure_startable().map_err(|error| {
            project::ProjectCommandError::new("job_conflict", error)
        })?;
    }
    let root = std::path::Path::new(&project_root);
    let current = project::read_project(root)?;
    if current.revision != expected_revision {
        return Err(project::ProjectCommandError::revision_conflict(
            expected_revision,
            current.revision,
        ));
    }
    let dataset = training::validate_training_start_inputs(root, &current, &config)?;
    let runtime = LichtfeldRuntime::resolve(&app)?;
    let preflight = run_lichtfeld_preflight(&runtime, &dataset, &training_output_root(root))?;
    ensure_lichtfeld_training_ready(&preflight)?;
    let (job_context, _) = job::begin_job_with_task_impl(root, contracts::ProjectWorkspace::Training, task_id)?;
    let project_after_job = project::read_project(root)?;
    let project = match training::begin_training_impl(
        root,
        project_after_job.revision,
        &job_context.job_id,
        &config,
    ) {
        Ok(project) => project,
        Err(error) => {
            let _ = job::finish_job_impl(
                &job_context,
                contracts::JobState::Failed,
                &error.message,
            );
            return Err(error);
        }
    };
    let output_relative = project.training.output_path.as_deref().ok_or_else(|| {
        project::ProjectCommandError::new("artifact_corrupt", "training output path is missing")
    })?;
    let output = root.join(output_relative);
    let mut args = vec![
        "--project-root".to_string(),
        project_root.clone(),
        "--executable".to_string(),
        // WARN: LichtFeld's MinGW resource lookup breaks when launched through a `\\?\` path.
        tool_resolver::plain_windows_path(&runtime.executable),
        "--data-path".to_string(),
        dataset.to_string_lossy().to_string(),
        "--output-path".to_string(),
        output.to_string_lossy().to_string(),
        "--profile-root".to_string(),
        tool_resolver::plain_windows_path(&runtime.profile_root),
        "--iterations".to_string(),
        config.iterations.to_string(),
        "--strategy".to_string(),
        config.strategy.clone(),
        "--sh-degree".to_string(),
        config.sh_degree.to_string(),
        "--max-gaussians".to_string(),
        config.max_gaussians.to_string(),
        "--resize-factor".to_string(),
        config.resize_factor.clone(),
        "--max-width".to_string(),
        config.max_width.to_string(),
        "--centralize".to_string(),
        config.centralize.clone(),
        "--background-mode".to_string(),
        config.background_mode.clone(),
        "--background-color".to_string(),
        config.background_color.clone(),
    ];
    if config.test_every > 0 {
        args.extend(["--test-every".to_string(), config.test_every.to_string()]);
    }
    append_training_flag(&mut args, !config.use_cpu_cache, "--no-cpu-cache");
    append_training_flag(&mut args, !config.use_fs_cache, "--no-fs-cache");
    append_training_flag(&mut args, config.undistort, "--undistort");
    append_training_flag(&mut args, config.enable_mip, "--enable-mip");
    append_training_flag(&mut args, config.bilateral_grid, "--bilateral-grid");
    append_training_flag(&mut args, config.enable_eval, "--eval");
    append_training_flag(&mut args, !config.gui, "--headless");

    let _ = app.emit(
        "project:updated",
        media::ProjectUpdatedEvent {
            project_root: project_root.clone(),
            project: project.clone(),
        },
    );
    let mut pipeline = state.pipeline.lock().map_err(|error| {
        project::ProjectCommandError::new("job_conflict", error.to_string())
    })?;
    if let Err(error) = pipeline.start_registered_job(
        app.clone(),
        tool_resolver::plain_windows_path(&runtime.python).as_str(),
        runtime.training_script.to_string_lossy().as_ref(),
        &args,
        job_context.clone(),
    ) {
        let _ = training::fail_training_job_impl(root, &error);
        let _ = job::finish_job_impl(
            &job_context,
            contracts::JobState::Failed,
            &format!("任务启动失败: {error}"),
        );
        return Err(project::ProjectCommandError::new("backend_unavailable", error));
    }
    Ok(project)
}

#[tauri::command]
async fn start_training_job(
    app: tauri::AppHandle,
    project_root: String,
    expected_revision: u64,
    config: training::TrainingConfig,
) -> Result<contracts::XpanoProjectV2, project::ProjectCommandError> {
    batch::ensure_manual_startable(&app, app.state::<AppState>().inner())?;
    tauri::async_runtime::spawn_blocking(move || {
        start_training_job_blocking(app, project_root, expected_revision, config, None)
    })
    .await
    .map_err(|error| {
        project::ProjectCommandError::new(
            "backend_unavailable",
            format!("training supervisor stopped unexpectedly: {error}"),
        )
    })?
}

#[tauri::command]
async fn get_training_readiness(
    app: tauri::AppHandle,
    project_root: String,
) -> Result<TrainingReadinessStatus, project::ProjectCommandError> {
    tauri::async_runtime::spawn_blocking(move || {
        training_readiness_blocking(&app, std::path::Path::new(&project_root))
    })
    .await
    .map_err(|error| {
        project::ProjectCommandError::new(
            "LFS_READINESS_FAILED",
            format!("LichtFeld readiness task stopped unexpectedly: {error}"),
        )
    })?
}

#[tauri::command]
fn save_training_config(
    project_root: String,
    expected_revision: u64,
    config: training::TrainingConfig,
) -> Result<contracts::XpanoProjectV2, project::ProjectCommandError> {
    training::save_training_config_impl(std::path::Path::new(&project_root), expected_revision, &config)
}

#[tauri::command]
fn get_job_snapshot(
    project_root: String,
) -> Result<Vec<contracts::JobSnapshot>, project::ProjectCommandError> {
    job::get_job_snapshots_impl(std::path::Path::new(&project_root))
}

#[tauri::command]
fn read_job_events(
    project_root: String,
    job_id: String,
    after_sequence: u64,
    limit: usize,
) -> Result<Vec<contracts::JobEvent>, project::ProjectCommandError> {
    job::read_job_events_impl(
        std::path::Path::new(&project_root),
        &job_id,
        after_sequence,
        limit,
    )
}

#[tauri::command]
fn get_job_recovery(
    project_root: String,
    after_sequence: u64,
) -> Result<job::JobRecovery, project::ProjectCommandError> {
    job::recovery_impl(std::path::Path::new(&project_root), after_sequence)
}

#[tauri::command]
fn recover_job_state(
    state: State<'_, AppState>,
    app: tauri::AppHandle,
    project_root: String,
    after_sequence: u64,
) -> Result<job::JobRecovery, project::ProjectCommandError> {
    let root = std::path::Path::new(&project_root);
    let active_job_id = state
        .pipeline
        .lock()
        .map_err(|error| project::ProjectCommandError::new("job_conflict", error.to_string()))?
        .active_job()
        .filter(|context| context.belongs_to(root))
        .map(|context| context.job_id.clone());
    job::recover_orphaned_jobs_impl(root, active_job_id.as_deref())?;
    let current = project::read_project(root)?;
    if current.reconstruction.status == contracts::ReconstructionStatus::Running
        && active_job_id.is_none()
    {
        let interrupted = reconstruction::interrupt_reconstruction_job_impl(root)?;
        let _ = app.emit(
            "project:updated",
            media::ProjectUpdatedEvent {
                project_root: project_root.clone(),
                project: interrupted,
            },
        );
    }
    job::recovery_impl(root, after_sequence)
}

#[tauri::command]
fn cancel_job(
    state: State<'_, AppState>,
    project_root: String,
    job_id: String,
) -> Result<contracts::JobSnapshot, project::ProjectCommandError> {
    let root = std::path::Path::new(&project_root);
    let mut pipeline = state.pipeline.lock().map_err(|error| {
        project::ProjectCommandError::new("job_conflict", error.to_string())
    })?;
    let context = pipeline
        .active_job()
        .filter(|context| context.matches(root, &job_id))
        .cloned()
        .ok_or_else(|| {
            project::ProjectCommandError::new(
                "job_conflict",
                "requested job is not the active pipeline task",
            )
        })?;
    let snapshot = job::mark_job_cancelling_impl(&context)?;
    pipeline
        .cancel()
        .map_err(|error| project::ProjectCommandError::new("job_conflict", error))?;
    Ok(snapshot)
}

#[tauri::command]
fn cancel_pipeline(state: State<'_, AppState>) -> Result<String, String> {
    let mut pipeline = state.pipeline.lock().map_err(|e| e.to_string())?;
    pipeline.cancel()?;
    Ok("Pipeline cancelled".into())
}

#[tauri::command]
fn is_pipeline_running(state: State<'_, AppState>) -> bool {
    state
        .pipeline
        .lock()
        .map(|pipeline| pipeline.is_running())
        .unwrap_or(false)
}

#[tauri::command]
fn open_output_folder(path: String) -> Result<(), String> {
    opener::open(&path).map_err(|e| format!("Failed to open folder: {}", e))
}

/// Apply a post-process axis correction to an existing COLMAP sparse model.
#[tauri::command]
async fn apply_colmap_axis_flip(
    python_exe: String,
    output_dir: String,
    axis: String,
) -> Result<String, String> {
    let axis = axis.trim().to_lowercase();
    if !matches!(axis.as_str(), "x" | "y" | "z") {
        return Err("axis must be x, y, or z".into());
    }
    let python = tool_resolver::resolve_python(&python_exe);
    tauri::async_runtime::spawn_blocking(move || {
        let script_path = tool_resolver::resolve_script_path("scripts/postprocess_colmap_axis.py");
        let mut cmd = Command::new(&python);
        cmd.env("PYTHONNOUSERSITE", "1").env_remove("PYTHONPATH");
        #[cfg(target_os = "windows")]
        cmd.creation_flags(0x08000000);
        let output = cmd
            .arg(script_path)
            .arg("--output-dir")
            .arg(output_dir)
            .arg("--flip-axis")
            .arg(axis)
            .output()
            .map_err(|e| format!("启动轴向后处理失败: {}", e))?;
        if output.status.success() {
            Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
        } else {
            let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
            let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
            Err(if stderr.is_empty() { stdout } else { stderr })
        }
    })
    .await
    .map_err(|e| format!("轴向后处理线程失败: {}", e))?
}

#[tauri::command]
async fn check_lfs_densify_env(
    app: AppHandle,
    python_exe: String,
    force: Option<bool>,
) -> Result<DensifyEnvStatus, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let root = densify_env_root(&app);
        let active_runtime = active_densify_runtime(&app);
        let plugin_path = locate_densify_plugin_path(&root);
        let python_path = if python_exe.trim().is_empty() {
            densify_python_path(&app, &root)
        } else {
            std::path::PathBuf::from(python_exe.trim())
        };
        let signature = densify_env_signature(
            &root,
            &python_path,
            &plugin_path,
            active_runtime.as_deref(),
        );
        if !force.unwrap_or(false) {
            if let Ok(cache) = densify_env_cache().lock() {
                if let Some((cached_signature, cached_status)) = cache.as_ref() {
                    if cached_signature == &signature {
                        return Ok(cached_status.clone());
                    }
                }
            }
        }
        let plugin_ok = plugin_path.join("densify.py").exists();
        let python_ok = python_path.exists();
        let mut messages = Vec::new();
        if !plugin_ok {
            messages.push("未找到 LichtFeld densification 插件".to_string());
        }
        if !python_ok {
            messages.push("未找到 .venv-densify Python".to_string());
        }

        let deps_ok = if python_ok {
            let mut deps = Command::new(&python_path);
            configure_densify_runtime_env(&mut deps, active_runtime.as_deref());
            deps.arg(tool_resolver::resolve_script_path(
                "scripts/run_lichtfeld_densify_standalone.py",
            ));
            if let Some(runtime) = active_runtime.as_deref() {
                deps.arg("--xpano-site-packages")
                    .arg(runtime.join("site-packages"));
            }
            deps.arg("--self-test-imports").arg("--profile").arg("cpu");
            match run_output(deps) {
                Ok(output) if output.status.success() => true,
                Ok(output) => {
                    messages.push(command_text(&output));
                    false
                }
                Err(error) => {
                    messages.push(error);
                    false
                }
            }
        } else {
            false
        };

        let runner_ok = if python_ok && plugin_ok {
            let mut runner = Command::new(&python_path);
            configure_densify_runtime_env(&mut runner, active_runtime.as_deref());
            runner
                .arg(tool_resolver::resolve_script_path(
                    "scripts/run_lichtfeld_densify_standalone.py",
                ));
            if let Some(runtime) = active_runtime.as_deref() {
                runner
                    .arg("--xpano-site-packages")
                    .arg(runtime.join("site-packages"));
            }
            runner.arg("--plugin-dir")
                .arg(&plugin_path)
                .arg("--help");
            match run_output(runner) {
                Ok(output) if output.status.success() => {
                    let text = command_text(&output);
                    text.contains("--scene_root") && text.contains("--roma_setting")
                }
                Ok(output) => {
                    messages.push(command_text(&output));
                    false
                }
                Err(error) => {
                    messages.push(error);
                    false
                }
            }
        } else {
            false
        };

        let message = if plugin_ok && python_ok && deps_ok && runner_ok {
            "致密化环境可用".to_string()
        } else if messages.is_empty() {
            "致密化环境未配置完整".to_string()
        } else {
            messages.join("\n")
        };

        let status = DensifyEnvStatus {
            plugin_ok,
            python_ok,
            deps_ok,
            runner_ok,
            plugin_path: plugin_path.to_string_lossy().into_owned(),
            python_path: python_path.to_string_lossy().into_owned(),
            message,
        };
        if let Ok(mut cache) = densify_env_cache().lock() {
            *cache = Some((signature, status.clone()));
        }
        Ok(status)
    })
    .await
    .map_err(|e| format!("环境检查线程失败: {}", e))?
}

#[tauri::command]
async fn install_lfs_densify_env(app: AppHandle, use_cuda: bool) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let state_root = densify_state_root(&app)?;
        let log_path = create_densify_env_log_file(&state_root, "install")?;
        let python = tool_resolver::resolve_resource_path("binaries/python/python.exe");
        let bootstrap = tool_resolver::resolve_resource_path("scripts/runtime_bootstrap.py");
        let manifest = tool_resolver::resolve_resource_path("runtime/densify-runtime-manifest.json");
        let pip_pyz = tool_resolver::resolve_resource_path("runtime/pip.pyz");
        for required in [&python, &bootstrap, &manifest, &pip_pyz] {
            if !required.is_file() {
                return Err(format!("致密化自举资源缺失: {}", required.display()));
            }
        }
        let (profile, downgrade_notice) =
            select_densify_profile(use_cuda, use_cuda && nvidia_driver_probe());
        let mut cmd = Command::new(&python);
        cmd.env("PYTHONNOUSERSITE", "1").env_remove("PYTHONPATH");
        cmd.arg(bootstrap)
            .arg("install")
            .arg("--manifest")
            .arg(manifest)
            .arg("--profile")
            .arg(profile)
            .arg("--state-root")
            .arg(&state_root)
            .arg("--python")
            .arg(&python)
            .arg("--pip-pyz")
            .arg(pip_pyz);
        let bundled_cache =
            tool_resolver::resolve_resource_path("runtime/densify-artifacts/sha256");
        if bundled_cache.is_dir() {
            cmd.arg("--bundled-cache").arg(bundled_cache);
        }
        if let Ok(mut cache) = densify_env_cache().lock() {
            *cache = None;
        }
        let text = run_streaming_densify_command(cmd, app.clone(), "install", Some(log_path))?;
        if let Some(message) = downgrade_notice {
            emit_densify_task(&app, "install", "stdout", message, None);
        }
        if let Ok(mut cache) = densify_env_cache().lock() {
            *cache = None;
        }
        Ok(text)
    })
    .await
    .map_err(|e| format!("安装线程失败: {}", e))?
}

#[tauri::command]
#[allow(clippy::too_many_arguments)]
async fn run_lfs_densify(
    app: AppHandle,
    output_dir: String,
    roma: String,
    max_points: i64,
    num_refs: f64,
    nns_per_ref: i64,
    matches_per_ref: i64,
    steps: i64,
    certainty_thresh: f64,
    image_filter: String,
    roi_start: f64,
    roi_end: f64,
) -> Result<DensifyRunResult, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let root = densify_env_root(&app);
        let active_runtime = active_densify_runtime(&app);
        let output_root = std::path::PathBuf::from(&output_dir)
            .canonicalize()
            .map_err(|e| format!("无法读取输出目录: {}", e))?;
        let log_path = create_densify_log_file(&output_root, "run")?;
        let log_path_text = log_path.to_string_lossy().to_string();
        let _ = write_densify_state(
            &output_root,
            &DensifyPersistedState {
                status: "running".to_string(),
                message: "正在运行 LichtFeld 致密化".to_string(),
                result: None,
                log_path: log_path_text.clone(),
                updated_at: now_millis(),
            },
        );
        let python = densify_python_path(&app, &root);
        if !python.exists() {
            let message = "未找到 .venv-densify Python，请先一键配置环境".to_string();
            let _ = write_densify_state(
                &output_root,
                &DensifyPersistedState {
                    status: "failed".to_string(),
                    message: message.clone(),
                    result: None,
                    log_path: log_path_text.clone(),
                    updated_at: now_millis(),
                },
            );
            return Err(message);
        }
        let plugin = locate_densify_plugin_path(&root);
        if !plugin.join("densify.py").exists() {
            let message = "未找到 LichtFeld densification 插件，请先一键配置环境".to_string();
            let _ = write_densify_state(
                &output_root,
                &DensifyPersistedState {
                    status: "failed".to_string(),
                    message: message.clone(),
                    result: None,
                    log_path: log_path_text.clone(),
                    updated_at: now_millis(),
                },
            );
            return Err(message);
        }
        let roma = if ["turbo", "fast", "base", "high", "precise"].contains(&roma.as_str()) {
            roma
        } else {
            "fast".to_string()
        };
        let max_points_text = max_points.max(0).to_string();
        let num_refs_text = if num_refs > 0.0 { num_refs } else { 0.75 }.to_string();
        let nns_per_ref_text = if nns_per_ref > 0 { nns_per_ref } else { 3 }.to_string();
        let matches_per_ref_text = matches_per_ref.max(100).to_string();
        let steps_text = steps.clamp(1, 500).to_string();
        let certainty_thresh_text = certainty_thresh.clamp(0.0, 1.0).to_string();
        let image_filter = if ["all", "cube_all", "front", "hd", "front_plus_hd"]
            .contains(&image_filter.as_str())
        {
            image_filter
        } else {
            "front_plus_hd".to_string()
        };
        let roi_start_text = roi_start.clamp(0.0, 1.0).to_string();
        let roi_end_text = roi_end.clamp(0.0, 1.0).to_string();

        let mut cmd = Command::new(tool_resolver::resolve_python(""));
        configure_densify_runtime_env(&mut cmd, active_runtime.as_deref());
        cmd.arg(tool_resolver::resolve_script_path(
            "scripts/run_lfs_densify_viewer.py",
        ))
        .arg("--output-dir")
        .arg(output_dir)
        .arg("--python-exe")
        .arg(python);
        if let Some(runtime) = active_runtime.as_deref() {
            cmd.arg("--site-packages")
                .arg(runtime.join("site-packages"));
        }
        cmd.arg("--plugin-dir")
        .arg(plugin)
        .arg("--roma")
        .arg(roma)
        .arg("--max-points")
        .arg(max_points_text)
        .arg("--num-refs")
        .arg(num_refs_text)
        .arg("--nns-per-ref")
        .arg(nns_per_ref_text)
        .arg("--matches-per-ref")
        .arg(matches_per_ref_text)
        .arg("--steps")
        .arg(steps_text)
        .arg("--certainty-thresh")
        .arg(certainty_thresh_text)
        .arg("--image-filter")
        .arg(image_filter)
        .arg("--roi-start")
        .arg(roi_start_text)
        .arg("--roi-end")
        .arg(roi_end_text);
        let text = match run_streaming_densify_command(cmd, app, "run", Some(log_path.clone())) {
            Ok(text) => text,
            Err(error) => {
                let status = if error.contains("任务已停止") {
                    "stopped"
                } else {
                    "failed"
                };
                let _ = write_densify_state(
                    &output_root,
                    &DensifyPersistedState {
                        status: status.to_string(),
                        message: error.clone(),
                        result: None,
                        log_path: log_path_text,
                        updated_at: now_millis(),
                    },
                );
                return Err(error);
            }
        };
        let result_line = match text
            .lines()
            .rev()
            .find_map(|line| line.trim().strip_prefix("DENSIFY_RESULT:"))
        {
            Some(line) => line,
            None => {
                let message = "致密化完成但未返回结果摘要".to_string();
                let _ = write_densify_state(
                    &output_root,
                    &DensifyPersistedState {
                        status: "failed".to_string(),
                        message: message.clone(),
                        result: None,
                        log_path: log_path_text,
                        updated_at: now_millis(),
                    },
                );
                return Err(message);
            }
        };
        let result = serde_json::from_str::<DensifyRunResult>(result_line)
            .map_err(|e| format!("解析致密化结果失败: {}\n{}", e, text))?;
        let _ = write_densify_state(
            &output_root,
            &DensifyPersistedState {
                status: "completed_unconfirmed".to_string(),
                message: "致密化完成，等待确认应用或丢弃".to_string(),
                result: Some(result.clone()),
                log_path: log_path_text,
                updated_at: now_millis(),
            },
        );
        Ok(result)
    })
    .await
    .map_err(|e| format!("致密化线程失败: {}", e))?
}

#[tauri::command]
fn stop_lfs_densify_task(state: State<'_, AppState>) -> Result<bool, String> {
    let pid = {
        let mut active = state.densify_pid.lock().map_err(|e| e.to_string())?;
        active.take()
    };
    if let Some(pid) = pid {
        kill_process_tree(pid);
        Ok(true)
    } else {
        Ok(false)
    }
}

#[tauri::command]
fn probe_video_duration(path: String) -> f64 {
    use std::process::Command;
    let src = match std::fs::canonicalize(&path) {
        Ok(a) => a,
        Err(_) => return 0.0,
    };
    let mut cmd = Command::new(tool_resolver::locate_ffprobe());
    #[cfg(target_os = "windows")]
    cmd.creation_flags(0x08000000);
    let out = cmd
        .args([
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
        ])
        .arg(&src)
        .output();
    match out {
        Ok(o) => String::from_utf8_lossy(&o.stdout)
            .trim()
            .parse::<f64>()
            .unwrap_or(0.0),
        Err(_) => 0.0,
    }
}

/// Extract a single frame from both lenses of a panoramic video at a given time.
///
/// Insta360 `.insv` files carry two video streams (front + back). HTML5 video
/// can't pick the second stream, and transcoding the whole 4K HEVC file is far
/// slower than realtime — so instead we extract one frame per lens on demand
/// when the user scrubs the timeline. Returns `[front_path, back_path]` as
/// browser-loadable temp jpgs (empty strings if a stream is missing).
#[tauri::command]
fn extract_pano_frame(path: String, time: f64) -> Vec<String> {
    use std::process::Command;

    let src = match std::fs::canonicalize(&path) {
        Ok(a) => a,
        Err(_) => return vec![String::new(), String::new()],
    };
    let key = src
        .to_string_lossy()
        .bytes()
        .fold(0u64, |acc, b| acc.wrapping_mul(31).wrapping_add(b as u64));
    let tmp_dir = std::env::temp_dir().join("xpano-frames");
    let _ = std::fs::create_dir_all(&tmp_dir);
    // Frame filenames include the time so concurrent scrubs don't clobber each other.
    let t_label = (time * 10.0).round() as i64;
    // Detect dual-lens sources. .insv (Insta360) and .osv (DJI 360) both carry
    // a second video stream, but they map front/back to different stream indices:
    //   Insta360 .insv:  stream 0 = front,  stream 1 = back
    //   DJI 360  .osv:   stream 0 = back,   stream 1 = front
    let is_osv = src
        .extension()
        .and_then(|e| e.to_str())
        .map(|e| e.eq_ignore_ascii_case("osv"))
        .unwrap_or(false);
    // Include format in cache key so OSV/INSV stream remapping doesn't reuse stale frames
    let fmt_tag = if is_osv { "osv" } else { "insv" };
    let front = tmp_dir.join(format!("{:x}_{}_{}_0.jpg", key, fmt_tag, t_label));
    let back = tmp_dir.join(format!("{:x}_{}_{}_1.jpg", key, fmt_tag, t_label));
    let has_dual_lens = if is_osv {
        true // DJI 360 always has dual lenses
    } else {
        let ffprobe = tool_resolver::locate_ffprobe();
        let mut probe_cmd = Command::new(&ffprobe);
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
    let ffmpeg = tool_resolver::locate_ffmpeg();
    let stamp = format!("{:.3}", time.max(0.0));
    // Swap stream mapping for OSV: front←stream 1, back←stream 0
    let (front_stream, back_stream): (&str, &str) = if is_osv {
        ("0:1", "0:0")
    } else {
        ("0:0", "0:1")
    };

    if !front.exists() {
        let mut command = Command::new(&ffmpeg);
        #[cfg(target_os = "windows")]
        command.creation_flags(0x08000000);
        command
            .args(["-hide_banner", "-y", "-nostdin", "-hwaccel", "d3d11va"])
            .args(["-ss", &stamp])
            .arg("-i")
            .arg(&src)
            .args([
                "-map",
                front_stream,
                "-frames:v",
                "1",
                "-vf",
                "scale=640:-2",
            ])
            .arg(&front);
        let _ = run_guarded_command(command);
    }
    if has_dual_lens && !back.exists() {
        let mut command = Command::new(&ffmpeg);
        #[cfg(target_os = "windows")]
        command.creation_flags(0x08000000);
        command
            .args(["-hide_banner", "-y", "-nostdin", "-hwaccel", "d3d11va"])
            .args(["-ss", &stamp])
            .arg("-i")
            .arg(&src)
            .args(["-map", back_stream, "-frames:v", "1", "-vf", "scale=640:-2"])
            .arg(&back);
        let _ = run_guarded_command(command);
    }

    vec![
        if front.exists() {
            web_path(&front)
        } else {
            String::new()
        },
        if back.exists() {
            web_path(&back)
        } else {
            String::new()
        },
    ]
}

/// Start incremental thumbnail generation for a panoramic video.
///
/// Spawns a background thread that extracts frame pairs (front +, for insv, back)
/// starting at `from`, every `interval` seconds, for `count` frames. Each pair is
/// emitted as a `thumbgen:frame` event so the frontend can fill the timeline
/// progressively. Switching videos calls `stop_thumbgen` to cancel.
#[tauri::command]
fn start_thumbgen(
    app: AppHandle,
    state: State<Mutex<ThumbgenState>>,
    path: String,
    from: f64,
    interval: f64,
    count: usize,
) {
    thumbgen::start_batch(app, state.inner(), path, from, interval, count);
}

/// Cancel any in-flight thumbnail batch (called when the user switches videos).
#[tauri::command]
fn stop_thumbgen(state: State<Mutex<ThumbgenState>>) {
    state.inner().lock().unwrap().reset();
}

fn is_photo_extension(ext: &str) -> bool {
    matches!(
        ext.to_ascii_lowercase().as_str(),
        "jpg" | "jpeg" | "png" | "tif" | "tiff" | "bmp" | "webp"
    )
}

fn scan_photo_paths(path: &std::path::Path) -> Result<PhotoPathScan, String> {
    if path.is_file() {
        let extension = path
            .extension()
            .and_then(|value| value.to_str())
            .unwrap_or_default();
        return if is_photo_extension(extension) {
            Ok(PhotoPathScan {
                total: 1,
                paths: vec![path.to_path_buf()],
            })
        } else {
            Err("所选文件不是支持的图片".to_string())
        };
    }
    if !path.is_dir() {
        return Err("照片路径不存在或不可读取".to_string());
    }

    let mut total = 0usize;
    let mut photos = Vec::new();
    let mut stack = vec![path.to_path_buf()];
    while let Some(dir) = stack.pop() {
        if dir != path && dir.join("xpano_project.json").is_file() {
            continue;
        }
        let entries = std::fs::read_dir(&dir)
            .map_err(|e| format!("无法读取文件夹 {}: {}", dir.display(), e))?;
        for entry in entries {
            let entry = entry.map_err(|e| format!("读取文件夹条目失败: {}", e))?;
            let entry_path = entry.path();
            if entry_path.is_dir() {
                stack.push(entry_path);
                continue;
            }
            let ext = entry_path
                .extension()
                .and_then(|value| value.to_str())
                .unwrap_or_default();
            if is_photo_extension(ext) {
                total += 1;
                photos.push(entry_path);
            }
        }
    }
    photos.sort();
    if total == 0 {
        Err("文件夹中未找到图片".to_string())
    } else {
        Ok(PhotoPathScan { total, paths: photos })
    }
}

fn preview_photo_folder_impl(path: &str) -> Result<PhotoPreviewResult, String> {
    let scan = scan_photo_paths(std::path::Path::new(path))?;
    Ok(PhotoPreviewResult {
        total: scan.total,
        paths: scan.paths.iter().map(|path| web_path(path)).collect(),
    })
}

#[tauri::command]
async fn preview_photo_folder(path: String) -> Result<PhotoPreviewResult, String> {
    // NOTE: Large directory walks are blocking I/O, so keep them off Tauri's async executor.
    tauri::async_runtime::spawn_blocking(move || preview_photo_folder_impl(&path))
        .await
        .map_err(|error| format!("照片预览任务失败: {}", error))?
}

fn analyze_import_paths_impl(paths: Vec<String>) -> Vec<ImportPathInfo> {
    paths
        .into_iter()
        .map(|raw| {
            let path = std::path::PathBuf::from(&raw);
            let label = path
                .file_stem()
                .or_else(|| path.file_name())
                .and_then(|value| value.to_str())
                .unwrap_or("素材")
                .to_string();
            let is_dir = path.is_dir();
            let extension = path
                .extension()
                .and_then(|value| value.to_str())
                .unwrap_or_default()
                .to_ascii_lowercase();

            let (suggested_type, valid_photo_folder, photo_count, preview_paths, message) = if is_dir {
                match scan_photo_paths(&path) {
                    Ok(scan) => (
                        "standard_photos".to_string(),
                        true,
                        scan.total,
                        scan.paths.iter().map(|path| web_path(path)).collect(),
                        format!("已识别 {} 张图片", scan.total),
                    ),
                    Err(error) => ("standard_photos".to_string(), false, 0, Vec::new(), error),
                }
            } else if matches!(extension.as_str(), "osv" | "insv") {
                ("panoramic_video".to_string(), false, 0, Vec::new(), "全景视频".to_string())
            } else if matches!(
                extension.as_str(),
                "mp4" | "mov" | "avi" | "mkv" | "m4v" | "webm"
            ) {
                ("ordinary_video".to_string(), false, 0, Vec::new(), "普通视频".to_string())
            } else if is_photo_extension(&extension) {
                (
                    "standard_photos".to_string(),
                    false,
                    1,
                    vec![web_path(&path)],
                    "单张图片".to_string(),
                )
            } else {
                (
                    "standard_photos".to_string(),
                    false,
                    0,
                    Vec::new(),
                    "暂不支持的素材格式".to_string(),
                )
            };

            let valid = if is_dir {
                valid_photo_folder
            } else {
                photo_count > 0
                    || matches!(
                        extension.as_str(),
                        "osv" | "insv" | "mp4" | "mov" | "avi" | "mkv" | "m4v" | "webm"
                    )
            };

            ImportPathInfo {
                path: raw,
                label: label.clone(),
                name: label,
                is_dir,
                extension,
                suggested_type: suggested_type.clone(),
                kind: suggested_type,
                valid_photo_folder,
                valid,
                photo_count,
                preview_paths,
                message,
            }
        })
        .collect()
}

#[tauri::command]
async fn analyze_import_paths(paths: Vec<String>) -> Result<Vec<ImportPathInfo>, String> {
    tauri::async_runtime::spawn_blocking(move || analyze_import_paths_impl(paths))
        .await
        .map_err(|error| format!("素材分析任务失败: {}", error))
}

#[tauri::command]
fn ensure_default_output_dir(path: String) -> Result<String, String> {
    let source = std::path::PathBuf::from(&path)
        .canonicalize()
        .map_err(|e| format!("无法读取素材路径: {}", e))?;
    let base = source
        .parent()
        .ok_or_else(|| "无法定位素材所在目录".to_string())?;
    let output = base.join("colmap");
    std::fs::create_dir_all(&output).map_err(|e| format!("创建输出目录失败: {}", e))?;
    Ok(tool_resolver::plain_windows_path(&output))
}

#[tauri::command]
fn detect_metashape() -> String {
    let candidates = [
        "C:\\Program Files\\Agisoft\\Metashape Pro\\metashape.exe",
        "C:\\Program Files\\Agisoft\\Metashape\\metashape.exe",
    ];
    // Check env var first
    if let Ok(val) = std::env::var("XPANO_METASHAPE") {
        let trimmed = val.trim();
        if !trimmed.is_empty() {
            return trimmed
                .strip_prefix('"')
                .and_then(|value| value.strip_suffix('"'))
                .map(str::trim)
                .unwrap_or(trimmed)
                .to_string();
        }
    }
    for path in &candidates {
        if std::path::Path::new(path).exists() {
            return path.to_string();
        }
    }
    tool_resolver::locate_tool("XPANO_METASHAPE", "metashape", "metashape")
}

#[tauri::command]
fn detect_colmap() -> String {
    let root = tool_resolver::resolve_app_root();
    let candidates = [
        root.join("tools").join("colmap").join("bin").join("colmap.exe"),
        root.join("tools").join("colmap").join("COLMAP.bat"),
        root.join("xpano-ui")
            .join("binaries")
            .join("colmap")
            .join("colmap.exe"),
    ];
    if let Ok(val) = std::env::var("XPANO_COLMAP") {
        if std::path::Path::new(&val).exists() {
            return val;
        }
    }
    for path in candidates {
        if path.exists() {
            return path.to_string_lossy().into_owned();
        }
    }
    tool_resolver::locate_tool("XPANO_COLMAP", "colmap", "colmap")
}

#[tauri::command]
fn window_minimize(window: tauri::Window) {
    let _ = window.minimize();
}

#[tauri::command]
fn window_toggle_maximize(window: tauri::Window) {
    if window.is_maximized().unwrap_or(false) {
        let _ = window.unmaximize();
    } else {
        let _ = window.maximize();
    }
}

#[tauri::command]
fn window_close(window: tauri::Window) {
    let _ = window.close();
}

/// Clear the temp directories used for cached thumbnails and frame extracts.
/// Called at startup (guaranteed) and on graceful shutdown (best-effort).
/// Retries with backoff if deletion fails (thumbgen / ffmpeg may still hold file handles).
fn clear_temp_cache() {
    for dir in &["xpano-thumbs", "xpano-frames"] {
        let path = std::env::temp_dir().join(dir);
        if !path.exists() {
            continue;
        }
        for attempt in 0..3 {
            match std::fs::remove_dir_all(&path) {
                Ok(()) => break,
                Err(_) if attempt < 2 => {
                    std::thread::sleep(std::time::Duration::from_millis(80 + attempt * 80));
                }
                Err(_) => {} // give up after 3 attempts — startup will catch it next time
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_case(name: &str) -> std::path::PathBuf {
        std::env::temp_dir().join(format!(
            "xpano-colmap-preview-{}-{}",
            name,
            std::process::id()
        ))
    }

    #[test]
    fn resolves_colmap_preview_dirs_at_common_levels() {
        let root = temp_case("common-levels");
        let sparse_zero = root.join("sparse").join("0");
        std::fs::create_dir_all(&sparse_zero).unwrap();
        std::fs::write(sparse_zero.join("points3D.bin"), b"").unwrap();

        let root_hit = resolve_colmap_preview_dir_from_paths(&[root.to_string_lossy().to_string()])
            .unwrap();
        let sparse_hit =
            resolve_colmap_preview_dir_from_paths(&[root.join("sparse").to_string_lossy().to_string()])
                .unwrap();
        let sparse_zero_hit =
            resolve_colmap_preview_dir_from_paths(&[sparse_zero.to_string_lossy().to_string()])
                .unwrap();

        assert_eq!(root_hit, root.canonicalize().unwrap());
        assert_eq!(sparse_hit, root.join("sparse").canonicalize().unwrap());
        assert_eq!(sparse_zero_hit, sparse_zero.canonicalize().unwrap());

        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn ignores_non_colmap_dirs() {
        let root = temp_case("empty");
        std::fs::create_dir_all(&root).unwrap();

        let hit = resolve_colmap_preview_dir_from_paths(&[root.to_string_lossy().to_string()]);

        assert!(hit.is_none());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn xpano_project_dirs_do_not_open_as_colmap_preview() {
        let root = temp_case("xpano-project");
        let sparse_zero = root.join("sparse").join("0");
        std::fs::create_dir_all(&sparse_zero).unwrap();
        std::fs::write(root.join("xpano_manifest.json"), b"{}").unwrap();
        std::fs::write(sparse_zero.join("points3D.bin"), b"").unwrap();

        let raw = root.to_string_lossy().to_string();
        let project_hit = resolve_xpano_project_dir_from_paths(&[raw.clone()]).unwrap();
        let preview_hit = resolve_colmap_preview_dir_from_paths(&[raw]);

        assert_eq!(project_hit, root.canonicalize().unwrap());
        assert!(preview_hit.is_none());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn loads_xpano_project_tracks_from_manifest() {
        let root = temp_case("load-project");
        let work = root.join("work");
        std::fs::create_dir_all(&work).unwrap();
        std::fs::write(work.join("xpano.psx"), b"").unwrap();
        std::fs::write(
            work.join("xpano_manifest.json"),
            r#"{
              "schema_version": 1,
              "workflow": "xpano_multi_track",
              "tracks": [
                {
                  "track_id": "track_001",
                  "track_type": "panorama_video",
                  "device_label": "CAM_A",
                  "source_paths": ["D:\\input\\CAM_A.OSV"],
                  "frames": [{"left": "l.jpg", "right": "r.jpg"}]
                },
                {
                  "track_id": "track_002",
                  "track_type": "standard_photos",
                  "device_label": "photos",
                  "source_paths": ["D:\\input\\photos\\a.jpg", "D:\\input\\photos\\b.jpg"],
                  "photos": ["D:\\input\\photos\\a.jpg", "D:\\input\\photos\\b.jpg"]
                }
              ]
            }"#,
        )
        .unwrap();
        std::fs::write(
            root.join("xpano_run_summary.json"),
            r#"{"backend":"metashape","metashape_alignment_mode":"backbone","frames_per_second":1.0,"max_frames":0}"#,
        )
        .unwrap();

        let loaded = load_xpano_project(root.to_string_lossy().to_string()).unwrap();

        assert_eq!(loaded.tracks.len(), 2);
        assert_eq!(loaded.tracks[0].track_type, "panoramic_video");
        assert_eq!(loaded.tracks[0].path, "D:\\input\\CAM_A.OSV");
        assert_eq!(loaded.tracks[1].track_type, "standard_photos");
        assert!(loaded.tracks[1].path.ends_with("photos"));
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn legacy_run_summary_interval_is_migrated_to_fps() {
        let summary = serde_json::json!({"seconds_per_frame": 2.0});
        assert_eq!(summary_frames_per_second(&summary), 0.5);
    }

    #[test]
    fn photo_scan_ignores_non_images_and_nested_xpano_projects() {
        let root = temp_case("photo-preview");
        std::fs::create_dir_all(&root).unwrap();
        std::fs::write(root.join("a.jpg"), b"jpg").unwrap();
        std::fs::write(root.join("notes.txt"), b"notes").unwrap();
        let nested = root.join("xPano");
        std::fs::create_dir_all(nested.join("work")).unwrap();
        std::fs::write(nested.join("xpano_project.json"), b"{}").unwrap();
        std::fs::write(nested.join("work").join("generated.jpg"), b"generated").unwrap();

        let scan = scan_photo_paths(&root).unwrap();

        assert_eq!(scan.total, 1);
        assert_eq!(scan.paths, vec![root.join("a.jpg")]);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn photo_preview_scan_returns_every_image_path() {
        let root = temp_case("complete-photo-preview");
        std::fs::create_dir_all(&root).unwrap();
        for index in 0..30 {
            std::fs::write(root.join(format!("{:03}.jpg", index)), b"jpg").unwrap();
        }

        let scan = scan_photo_paths(&root).unwrap();

        assert_eq!(scan.total, 30);
        assert_eq!(scan.paths.len(), 30);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn import_analysis_reuses_every_scanned_photo_path() {
        let root = temp_case("import-photo-preview");
        std::fs::create_dir_all(&root).unwrap();
        for index in 0..12 {
            std::fs::write(root.join(format!("{:03}.jpg", index)), b"jpg").unwrap();
        }

        let info = analyze_import_paths_impl(vec![root.to_string_lossy().to_string()]);

        assert_eq!(info.len(), 1);
        assert_eq!(info[0].photo_count, 12);
        assert_eq!(info[0].preview_paths.len(), 12);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn parses_runtime_bootstrap_progress_events() {
        let event = parse_bootstrap_event(
            r#"BOOTSTRAP_EVENT:{"phase":"downloading","message":"torch.whl","progress":42.5}"#,
        )
        .unwrap();

        assert_eq!(event.0, "downloading");
        assert_eq!(event.1, "torch.whl");
        assert_eq!(event.2, Some(42.5));
        assert!(parse_bootstrap_event("ordinary output").is_none());
    }

    #[test]
    fn parses_runtime_readiness_result_and_preserves_site_packages_path() {
        let parsed = parse_runtime_result(
            r#"RUNTIME_RESULT:{"status":"ready","sitePackages":"C:\\Users\\测试\\Runtime\\site-packages"}"#,
        )
        .unwrap();

        assert_eq!(
            parsed.site_packages,
            r"C:\Users\测试\Runtime\site-packages"
        );
    }

    #[test]
    fn runtime_readiness_errors_preserve_stable_error_codes() {
        let error = parse_runtime_result(
            r#"RUNTIME_ERROR:{"code":"UNSUPPORTED_ABI","message":"cp38 is unsupported"}"#,
        )
        .unwrap_err();

        assert_eq!(error, "UNSUPPORTED_ABI: cp38 is unsupported");
    }

    #[test]
    fn lichtfeld_readiness_result_keeps_gpu_status_and_stable_failures() {
        let result = parse_lichtfeld_readiness_result(
            r#"LFS_READINESS_RESULT:{"status":"ready","version":"0.5.3","cuda":{"status":"ready","deviceCount":1},"vulkan":{"status":"ready","deviceCount":1}}"#,
        )
        .unwrap();

        assert_eq!(result.version, "0.5.3");
        assert_eq!(result.cuda.device_count, 1);
        let error = parse_lichtfeld_readiness_result(
            r#"LFS_READINESS_ERROR:{"code":"LFS_VULKAN_NO_DEVICE","message":"No Vulkan device"}"#,
        )
        .unwrap_err();
        assert_eq!(error.code, "LFS_VULKAN_NO_DEVICE");
    }

    #[test]
    fn lichtfeld_not_ready_result_rejects_training_with_the_input_failure_code() {
        let result = parse_lichtfeld_readiness_result(
            r#"LFS_READINESS_RESULT:{"status":"not_ready","version":"0.5.3","cuda":{"status":"ready","deviceCount":1},"vulkan":{"status":"ready","deviceCount":1},"dataset":{"status":"unavailable","code":"TRAINING_DATASET_INVALID","message":"dataset is incomplete"},"output":{"status":"ready","code":"","message":""}}"#,
        )
        .unwrap();

        let error = ensure_lichtfeld_training_ready(&result).unwrap_err();

        assert_eq!(error.code, "TRAINING_DATASET_INVALID");
        assert_eq!(error.message, "dataset is incomplete");
    }

    #[test]
    fn lichtfeld_runtime_resolution_keeps_every_child_under_one_bundled_root() {
        let resource_root = temp_case("lichtfeld-runtime-root");
        let state_root = temp_case("lichtfeld-runtime-state");
        let executable = resource_root
            .join("runtime/lichtfeld-studio/bin/LichtFeld-Studio.exe");
        for path in [
            &executable,
            &resource_root.join("scripts/runtime_readiness.py"),
            &resource_root.join("scripts/lichtfeld_training.py"),
            &resource_root.join("binaries/python/python.exe"),
        ] {
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, b"fixture").unwrap();
        }
        let manifest = resource_root.join("runtime/lichtfeld-studio-manifest.json");
        std::fs::create_dir_all(manifest.parent().unwrap()).unwrap();
        std::fs::write(&manifest, r#"{"version":"0.5.3"}"#).unwrap();

        let runtime = LichtfeldRuntime::from_roots(&resource_root, &state_root).unwrap();

        assert_eq!(runtime.resource_root, resource_root);
        assert_eq!(runtime.executable, executable);
        assert_eq!(runtime.profile_root, state_root.join("lichtfeld-studio/0.5.3/profile"));
        assert_eq!(runtime.readiness_script, runtime.resource_root.join("scripts/runtime_readiness.py"));
        assert_eq!(runtime.training_script, runtime.resource_root.join("scripts/lichtfeld_training.py"));
        assert_eq!(runtime.python, runtime.resource_root.join("binaries/python/python.exe"));
        let _ = std::fs::remove_dir_all(&runtime.resource_root);
        let _ = std::fs::remove_dir_all(&state_root);
    }

    #[test]
    fn lichtfeld_runtime_resolution_rejects_a_missing_training_supervisor() {
        let resource_root = temp_case("lichtfeld-runtime-missing-script");
        let state_root = temp_case("lichtfeld-runtime-missing-script-state");
        for path in [
            resource_root.join("runtime/lichtfeld-studio/bin/LichtFeld-Studio.exe"),
            resource_root.join("scripts/runtime_readiness.py"),
            resource_root.join("binaries/python/python.exe"),
        ] {
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, b"fixture").unwrap();
        }
        let manifest = resource_root.join("runtime/lichtfeld-studio-manifest.json");
        std::fs::create_dir_all(manifest.parent().unwrap()).unwrap();
        std::fs::write(&manifest, r#"{"version":"0.5.3"}"#).unwrap();

        let error = LichtfeldRuntime::from_roots(&resource_root, &state_root).unwrap_err();

        assert_eq!(error.code, "LFS_RUNTIME_CORRUPT");
        assert!(error.message.contains("training supervisor"));
        let _ = std::fs::remove_dir_all(&resource_root);
        let _ = std::fs::remove_dir_all(&state_root);
    }

    #[test]
    fn installed_lichtfeld_runtime_never_falls_back_to_development_resources() {
        let installed_root = temp_case("lichtfeld-installed-runtime");
        let development_root = temp_case("lichtfeld-development-runtime");
        let manifest = development_root.join("runtime/lichtfeld-studio-manifest.json");
        std::fs::create_dir_all(manifest.parent().unwrap()).unwrap();
        std::fs::write(&manifest, r#"{"version":"0.5.3"}"#).unwrap();

        let installed_error = lichtfeld_resource_root(&installed_root, Some(&development_root), false)
            .unwrap_err();
        assert_eq!(installed_error.code, "LFS_RUNTIME_CORRUPT");
        assert_eq!(
            lichtfeld_resource_root(&installed_root, Some(&development_root), true).unwrap(),
            development_root
        );

        let _ = std::fs::remove_dir_all(&installed_root);
        let _ = std::fs::remove_dir_all(&development_root);
    }

    #[test]
    fn training_readiness_cache_requires_matching_runtime_and_fresh_project_state() {
        let key = LichtfeldReadinessCacheKey {
            project_root: std::path::PathBuf::from(r"C:\\Project"),
            project_revision: 7,
            resource_root: std::path::PathBuf::from(r"C:\\xPano"),
            executable_size: 123,
            executable_modified_ns: 456,
            manifest_modified_ns: 789,
            sentinel_metadata: vec![("bin/LichtFeld-Studio.exe".to_string(), 123, 456)],
        };
        let readiness = LichtfeldRuntimeReadiness {
            status: "ready".to_string(),
            version: "0.5.3".to_string(),
            ..Default::default()
        };
        let now = std::time::Instant::now();
        let mut cache = LichtfeldReadinessCache::default();
        cache.store(key.clone(), readiness.clone(), now);

        assert_eq!(
            cache
                .get(&key, now + std::time::Duration::from_secs(5))
                .unwrap()
                .status,
            readiness.status,
        );
        assert!(cache.get(&key, now + std::time::Duration::from_secs(11)).is_none());
        let changed_project = LichtfeldReadinessCacheKey {
            project_revision: 8,
            ..key
        };
        assert!(cache.get(&changed_project, now + std::time::Duration::from_secs(1)).is_none());
    }

    #[test]
    fn densify_profile_requires_both_user_opt_in_and_nvidia_probe() {
        assert_eq!(select_densify_profile(false, false), ("cpu", None));
        assert_eq!(select_densify_profile(false, true), ("cpu", None));
        assert_eq!(select_densify_profile(true, true), ("cuda", None));
        assert_eq!(
            select_densify_profile(true, false),
            (
                "cpu",
                Some("未检测到可用的 NVIDIA 驱动，已改用 CPU 配置")
            )
        );
    }

    #[test]
    fn resolves_runtime_bootstrap_resources_from_the_application_root() {
        assert!(tool_resolver::resolve_resource_path("scripts/runtime_bootstrap.py").is_file());
        assert!(tool_resolver::resolve_resource_path("runtime/densify-runtime-manifest.json").is_file());
        assert!(tool_resolver::resolve_resource_path("runtime/pip.pyz").is_file());
    }

    #[test]
    fn point_cloud_packet_preserves_all_points_with_compact_u8_colors() {
        let cloud = ColmapPointCloud {
            points: vec![1.0, 2.0, 3.0],
            colors: vec![4, 5, 6],
            num_points: 1,
            total_points: 1,
            sampled: false,
            cameras: vec![ColmapCamera {
                id: 7,
                position: [8.0, 9.0, 10.0],
                rotation: [1.0, 0.0, 0.0, 0.0],
                fov: 1.1,
                aspect: 1.5,
                near: 0.2,
                far: 50.0,
            }],
        };

        let packet = encode_point_cloud_packet(&cloud);

        assert_eq!(&packet[0..8], b"XPCLD001");
        assert_eq!(u32::from_le_bytes(packet[16..20].try_into().unwrap()), 1);
        assert_eq!(packet.len(), 64 + 12 + 3 + 48);
        assert_eq!(&packet[76..79], &[4, 5, 6]);
    }

    #[test]
    fn point_cloud_reader_skips_tracks_without_losing_record_boundaries() {
        fn append_point(
            bytes: &mut Vec<u8>,
            id: u64,
            position: [f64; 3],
            color: [u8; 3],
            track: &[(u32, u32)],
        ) {
            bytes.extend_from_slice(&id.to_le_bytes());
            for value in position {
                bytes.extend_from_slice(&value.to_le_bytes());
            }
            bytes.extend_from_slice(&color);
            bytes.extend_from_slice(&0.25f64.to_le_bytes());
            bytes.extend_from_slice(&(track.len() as u64).to_le_bytes());
            for (image_id, point_index) in track {
                bytes.extend_from_slice(&image_id.to_le_bytes());
                bytes.extend_from_slice(&point_index.to_le_bytes());
            }
        }

        let root = temp_case("point-tracks");
        std::fs::create_dir_all(&root).unwrap();
        let points_path = root.join("points3D.bin");
        let mut bytes = Vec::new();
        bytes.extend_from_slice(&2u64.to_le_bytes());
        append_point(
            &mut bytes,
            11,
            [1.25, -2.5, 3.75],
            [10, 20, 30],
            &[(7, 8), (9, 10)],
        );
        append_point(
            &mut bytes,
            12,
            [-4.5, 5.25, -6.75],
            [40, 50, 60],
            &[(11, 12)],
        );
        std::fs::write(&points_path, bytes).unwrap();

        let cloud = read_colmap_points_impl(
            root.to_string_lossy().to_string(),
            Some(points_path.to_string_lossy().to_string()),
            Some(0),
        )
        .unwrap();

        assert_eq!(cloud.num_points, 2);
        assert_eq!(cloud.total_points, 2);
        assert_eq!(cloud.points, vec![1.25, -2.5, 3.75, -4.5, 5.25, -6.75]);
        assert_eq!(cloud.colors, vec![10, 20, 30, 40, 50, 60]);
        let _ = std::fs::remove_dir_all(&root);
    }

}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    clear_temp_cache();

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(AppState {
            pipeline: Mutex::new(PipelineState::new()),
            densify_pid: Mutex::new(None),
            lichtfeld_readiness: Mutex::new(LichtfeldReadinessCache::default()),
            batch: Mutex::new(batch::BatchCoordinator::default()),
        })
        .manage(Mutex::new(ThumbgenState::new()))
        .setup(|app| {
            tool_resolver::init(app.handle());
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                if window.label() == "main" {
                    // Kill running pipeline and its entire process tree
                    if let Some(state) = window.app_handle().try_state::<AppState>() {
                        batch::interrupt_for_shutdown(window.app_handle(), state.inner());
                        let _ = state.pipeline.lock().map(|mut p| p.cancel());
                        if let Ok(mut pid) = state.densify_pid.lock() {
                            if let Some(pid) = pid.take() {
                                kill_process_tree(pid);
                            }
                        }
                    }
                    // Cancel any in-flight thumbnail generation to release file handles
                    if let Some(state) = window.app_handle().try_state::<Mutex<ThumbgenState>>() {
                        let _ = state.lock().map(|s| s.cancel());
                    }
                    clear_temp_cache();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            start_pipeline,
            start_reconstruction_job,
            start_training_job,
            save_training_config,
            get_training_readiness,
            get_job_snapshot,
            read_job_events,
            get_job_recovery,
            recover_job_state,
            cancel_job,
            cancel_pipeline,
            is_pipeline_running,
            open_output_folder,
            apply_colmap_axis_flip,
            check_lfs_densify_env,
            install_lfs_densify_env,
            run_lfs_densify,
            get_lfs_densify_state,
            read_lfs_densify_log_tail,
            get_lfs_densify_pending_result,
            apply_lfs_densify_result,
            discard_lfs_densify_result,
            stop_lfs_densify_task,
            geometry::list_point_variants,
            geometry::materialize_standard_variant,
            geometry::preview_point_variant,
            geometry::set_active_point_variant,
            geometry::delete_point_variant,
            geometry::register_densified_variant,
            geometry::apply_world_transform,
            probe_video_duration,
            extract_pano_frame,
            start_thumbgen,
            stop_thumbgen,
            resolve_xpano_project_dir,
            load_xpano_project,
            project::create_project,
            project::open_or_create_project,
            project::open_project,
            project::rename_project,
            project::set_project_workspace,
            media::commit_import,
            media::update_track_settings,
            media::set_track_item_selection,
            media::list_track_items,
            media::remove_project_track,
            media::start_media_job,
            media::finalize_media_job,
            media::sync_media_job_result,
            media::fail_media_job,
            batch::get_batch_queue,
            batch::save_and_enqueue_batch_task,
            batch::requeue_batch_task,
            batch::remove_batch_task,
            batch::reorder_batch_tasks,
            batch::delete_batch_queue,
            batch::start_batch_queue,
            batch::stop_batch_queue,
            reconstruction::build_execution_plan,
            reconstruction::build_reexport_plan,
            reconstruction::inspect_metashape_components,
            reconstruction::probe_reconstruction_backends,
            probe_runtime_readiness,
            reconstruction::update_reconstruction_config,
            reconstruction::sync_reconstruction_job_result,
            resolve_colmap_preview_dir,
            analyze_import_paths,
            preview_photo_folder,
            ensure_default_output_dir,
            detect_metashape,
            detect_colmap,
            read_colmap_points,
            window_minimize,
            window_toggle_maximize,
            window_close,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
