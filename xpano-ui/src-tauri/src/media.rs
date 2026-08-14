use crate::contracts::{
    ExtractionSettings, JobState, ProjectMediaItem, ProjectTrack, ProjectTrackStatus,
    ProjectTrackType, ProjectTrim, ProjectWorkspace, ReconstructionStatus, SourceFingerprint,
    XpanoProjectV2, DJI_OSMO_360_DLOGM_REC709_PRESET,
};
use crate::project::{
    read_project, touch_project, write_json_value_atomic, write_project_atomic, ProjectCommandError,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;
use tauri::{AppHandle, Emitter, State};
use uuid::Uuid;

const MEDIA_RESULT_RELATIVE_PATH: &str = "work/media_prepare_result.json";
const MEDIA_JOB_RELATIVE_PATH: &str = "work/media_job.json";
const DJI_OSMO_360_DLOGM_REC709_RELATIVE_PATH: &str = "luts/dji-osmo360-dlogm-rec709-v1.cube";
const DJI_OSMO_360_DLOGM_REC709_SHA256: &str =
    "b18162854ab47702068410c33afa98a8cb6eef159fc5a04ce0e65fad0fd8947e";

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MediaImportDraft {
    pub track_type: ProjectTrackType,
    pub label: String,
    pub source_path: String,
    pub camera_profile: Option<String>,
    pub trim: Option<ProjectTrim>,
    pub extraction: ExtractionSettings,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TrackSettingsPatch {
    pub trim: Option<ProjectTrim>,
    pub extraction: Option<ExtractionSettings>,
    pub camera_profile: Option<String>,
}

#[derive(Clone, Copy, Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MediaItemFilter {
    All,
    Selected,
    Unselected,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MediaItemPage {
    pub items: Vec<ProjectMediaItem>,
    pub total: usize,
    pub next_cursor: Option<usize>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct PreparedTrackResult {
    id: String,
    status: ProjectTrackStatus,
    items: Vec<ProjectMediaItem>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct MediaPrepareResult {
    schema_version: u32,
    project_id: String,
    input_revision: u64,
    #[serde(default)]
    input_media_revision: Option<u64>,
    tracks: Vec<PreparedTrackResult>,
    manifest_path: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct MediaJobMarker {
    schema_version: u32,
    input_revision: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    input_media_revision: Option<u64>,
    target_track_ids: Vec<String>,
}

fn read_media_job_marker(
    project_root: &Path,
) -> Result<Option<MediaJobMarker>, ProjectCommandError> {
    let marker_path = project_root.join(MEDIA_JOB_RELATIVE_PATH);
    if !marker_path.is_file() {
        return Ok(None);
    }
    let marker: MediaJobMarker = serde_json::from_slice(&std::fs::read(&marker_path).map_err(
        |error| {
            ProjectCommandError::new(
                "artifact_corrupt",
                format!("failed to read media job marker: {}", error),
            )
        },
    )?)
    .map_err(|error| {
        ProjectCommandError::new(
            "artifact_corrupt",
            format!("failed to parse media job marker: {}", error),
        )
    })?;
    if marker.schema_version != 1 {
        return Err(ProjectCommandError::new(
            "artifact_corrupt",
            "unsupported media job marker version",
        ));
    }
    Ok(Some(marker))
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ProjectUpdatedEvent {
    pub(crate) project_root: String,
    pub(crate) project: XpanoProjectV2,
}

fn file_extension(path: &Path) -> String {
    path.extension()
        .and_then(|value| value.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase()
}

fn is_ordinary_video_extension(extension: &str) -> bool {
    matches!(extension, "mp4" | "mov" | "avi" | "mkv" | "m4v" | "webm")
}

fn is_photo_extension(extension: &str) -> bool {
    matches!(
        extension,
        "jpg" | "jpeg" | "png" | "tif" | "tiff" | "bmp" | "webp"
    )
}

fn normalize_color_lut_settings(extraction: &mut ExtractionSettings) {
    extraction.style_lut_path = extraction
        .style_lut_path
        .take()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty());
    extraction.color_lut_preset = extraction
        .color_lut_preset
        .take()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty());
}

fn validate_style_lut_file(extraction: &ExtractionSettings) -> Result<(), ProjectCommandError> {
    let Some(value) = extraction.style_lut_path.as_deref() else {
        return Ok(());
    };
    let path = Path::new(value);
    if file_extension(path) != "cube" {
        return Err(ProjectCommandError::new(
            "invalid_media_type",
            "style LUT must be a .cube file",
        ));
    }
    if !path.is_file() {
        return Err(ProjectCommandError::new(
            "missing_source",
            format!("style LUT does not exist or is not a file: {}", path.display()),
        ));
    }
    Ok(())
}

fn resolve_builtin_color_lut_preset(preset: &str) -> Result<PathBuf, ProjectCommandError> {
    if preset != DJI_OSMO_360_DLOGM_REC709_PRESET {
        return Err(ProjectCommandError::new("invalid_media_type", "unknown color LUT preset"));
    }
    let path = crate::tool_resolver::resolve_resource_path(DJI_OSMO_360_DLOGM_REC709_RELATIVE_PATH);
    let bytes = std::fs::read(&path).map_err(|error| {
        ProjectCommandError::new(
            "missing_source",
            format!("bundled DJI color LUT is missing: {}", error),
        )
    })?;
    let digest = format!("{:x}", Sha256::digest(bytes));
    if digest != DJI_OSMO_360_DLOGM_REC709_SHA256 {
        return Err(ProjectCommandError::new(
            "artifact_corrupt",
            "bundled DJI color LUT checksum does not match",
        ));
    }
    Ok(path)
}

fn validate_color_lut_settings(
    track_type: ProjectTrackType,
    source_path: &str,
    extraction: &ExtractionSettings,
) -> Result<(), ProjectCommandError> {
    if let Some(preset) = extraction.color_lut_preset.as_deref() {
        let is_osv_panorama = track_type == ProjectTrackType::PanoramicVideo
            && file_extension(Path::new(source_path)) == "osv";
        if !is_osv_panorama {
            return Err(ProjectCommandError::new(
                "invalid_media_type",
                "bundled DJI color LUT is only valid for .osv panorama tracks",
            ));
        }
        resolve_builtin_color_lut_preset(preset)?;
    }
    validate_style_lut_file(extraction)
}

fn source_fingerprint(path: &Path) -> Result<SourceFingerprint, ProjectCommandError> {
    let metadata = std::fs::metadata(path).map_err(|error| {
        ProjectCommandError::new(
            "missing_source",
            format!("failed to read source metadata: {}", error),
        )
    })?;
    let mtime_ns = metadata
        .modified()
        .ok()
        .and_then(|modified| modified.duration_since(UNIX_EPOCH).ok())
        .map(|duration| duration.as_nanos().min(u64::MAX as u128) as u64)
        .unwrap_or(0);
    Ok(SourceFingerprint {
        size: if metadata.is_file() {
            metadata.len()
        } else {
            0
        },
        mtime_ns,
    })
}

fn validate_import_draft(draft: &MediaImportDraft) -> Result<(), ProjectCommandError> {
    if draft.label.trim().is_empty() {
        return Err(ProjectCommandError::new(
            "invalid_project",
            "media track label must not be empty",
        ));
    }
    let source = Path::new(&draft.source_path);
    if !source.exists() {
        return Err(ProjectCommandError::new(
            "missing_source",
            format!("media source does not exist: {}", source.display()),
        ));
    }
    let extension = file_extension(source);
    let valid = match draft.track_type {
        ProjectTrackType::PanoramicVideo => {
            source.is_file() && matches!(extension.as_str(), "osv" | "insv")
        }
        ProjectTrackType::OrdinaryVideo => {
            source.is_file() && is_ordinary_video_extension(&extension)
        }
        ProjectTrackType::StandardPhotos | ProjectTrackType::AerialPhotos => {
            source.is_dir() || (source.is_file() && is_photo_extension(&extension))
        }
    };
    if !valid {
        return Err(ProjectCommandError::new(
            "invalid_media_type",
            format!(
                "source {} is not valid for {:?}",
                source.display(),
                draft.track_type
            ),
        ));
    }
    if draft.track_type == ProjectTrackType::OrdinaryVideo {
        if !matches!(draft.camera_profile.as_deref(), Some("wide" | "standard")) {
            return Err(ProjectCommandError::new(
                "invalid_media_type",
                "ordinary video camera profile must be wide or standard",
            ));
        }
    }
    if !draft.extraction.frames_per_second.is_finite() || draft.extraction.frames_per_second <= 0.0
    {
        return Err(ProjectCommandError::new(
            "invalid_project",
            "frames per second must be greater than zero",
        ));
    }
    validate_color_lut_settings(draft.track_type, &draft.source_path, &draft.extraction)?;
    Ok(())
}

fn ensure_project_writable(project: &XpanoProjectV2) -> Result<(), ProjectCommandError> {
    let job_running = project.jobs.iter().any(|job| {
        matches!(
            job.state,
            JobState::Queued | JobState::Running | JobState::Cancelling
        )
    });
    let media_running = project
        .tracks
        .iter()
        .any(|track| track.status == ProjectTrackStatus::Running);
    if job_running || media_running || project.reconstruction.status == ReconstructionStatus::Running {
        return Err(ProjectCommandError::new(
            "job_conflict",
            "project media cannot change while a job is running",
        ));
    }
    Ok(())
}

fn ensure_media_result_committable(
    project: &XpanoProjectV2,
) -> Result<(), ProjectCommandError> {
    let other_job_running = project.jobs.iter().any(|job| {
        job.workspace != ProjectWorkspace::Media
            && matches!(
                job.state,
                JobState::Queued | JobState::Running | JobState::Cancelling
            )
    }) || project.reconstruction.status == ReconstructionStatus::Running;
    if other_job_running {
        return Err(ProjectCommandError::new(
            "job_conflict",
            "media result cannot be committed while another project job is running",
        ));
    }
    Ok(())
}

fn mark_reconstruction_stale(project: &mut XpanoProjectV2) {
    if project.reconstruction.status != ReconstructionStatus::Idle {
        project.reconstruction.status = ReconstructionStatus::Stale;
    }
}

fn normalized_path_key(value: &str) -> String {
    let path = std::fs::canonicalize(value).unwrap_or_else(|_| PathBuf::from(value));
    let normalized = path.to_string_lossy().replace('\\', "/");
    if cfg!(windows) {
        normalized.to_ascii_lowercase()
    } else {
        normalized
    }
}

fn project_relative_config_path(
    project: &XpanoProjectV2,
    key: &str,
) -> Result<Option<String>, ProjectCommandError> {
    let Some(value) = project.reconstruction.config.get(key) else {
        return Ok(None);
    };
    let Some(value) = value.as_str() else {
        return Err(ProjectCommandError::new(
            "artifact_corrupt",
            format!("reconstruction config {} must be a path string", key),
        ));
    };
    if Path::new(value).is_absolute()
        || value.replace('\\', "/").split('/').any(|part| part == "..")
    {
        return Err(ProjectCommandError::new(
            "artifact_corrupt",
            format!("reconstruction config {} is not project-relative", key),
        ));
    }
    Ok(Some(value.to_string()))
}

fn reconstruction_config_mut(
    project: &mut XpanoProjectV2,
) -> &mut serde_json::Map<String, serde_json::Value> {
    if !project.reconstruction.config.is_object() {
        project.reconstruction.config = serde_json::json!({});
    }
    project.reconstruction.config.as_object_mut().unwrap()
}

fn filter_manifest_track(
    manifest_track: &serde_json::Value,
    project_track: &ProjectTrack,
) -> Result<Option<serde_json::Value>, ProjectCommandError> {
    let mut filtered = manifest_track.clone();
    let object = filtered.as_object_mut().ok_or_else(|| {
        ProjectCommandError::new("artifact_corrupt", "media manifest track must be an object")
    })?;

    match project_track.track_type {
        ProjectTrackType::PanoramicVideo => {
            let frames = object
                .get("frames")
                .and_then(serde_json::Value::as_array)
                .ok_or_else(|| {
                    ProjectCommandError::new(
                        "artifact_corrupt",
                        format!("panorama track {} has no frame list", project_track.label),
                    )
                })?;
            if frames.len() != project_track.items.len() {
                return Err(ProjectCommandError::new(
                    "artifact_corrupt",
                    format!(
                        "panorama track {} item count does not match its prepared manifest",
                        project_track.label
                    ),
                ));
            }
            let selected = frames
                .iter()
                .zip(&project_track.items)
                .filter(|(_, item)| item.selected)
                .map(|(frame, _)| frame.clone())
                .collect::<Vec<_>>();
            if selected.is_empty() {
                return Ok(None);
            }
            object.insert("frames".to_string(), serde_json::Value::Array(selected));
        }
        ProjectTrackType::OrdinaryVideo
        | ProjectTrackType::StandardPhotos
        | ProjectTrackType::AerialPhotos => {
            let photos = object
                .get("photos")
                .and_then(serde_json::Value::as_array)
                .ok_or_else(|| {
                    ProjectCommandError::new(
                        "artifact_corrupt",
                        format!("photo track {} has no photo list", project_track.label),
                    )
                })?;
            if photos.len() != project_track.items.len() {
                return Err(ProjectCommandError::new(
                    "artifact_corrupt",
                    format!(
                        "photo track {} item count does not match its prepared manifest",
                        project_track.label
                    ),
                ));
            }
            let selected = photos
                .iter()
                .zip(&project_track.items)
                .filter(|(_, item)| item.selected)
                .map(|(photo, _)| photo.clone())
                .collect::<Vec<_>>();
            if selected.is_empty() {
                return Ok(None);
            }
            let selected_paths = selected
                .iter()
                .filter_map(serde_json::Value::as_str)
                .map(normalized_path_key)
                .collect::<HashSet<_>>();
            object.insert("photos".to_string(), serde_json::Value::Array(selected));

            if let Some(sensors) = object
                .get_mut("photo_sensors")
                .and_then(serde_json::Value::as_array_mut)
            {
                for sensor in sensors.iter_mut() {
                    if let Some(sensor_object) = sensor.as_object_mut() {
                        if let Some(sensor_photos) = sensor_object
                            .get_mut("photos")
                            .and_then(serde_json::Value::as_array_mut)
                        {
                            sensor_photos.retain(|photo| {
                                photo
                                    .as_str()
                                    .map(normalized_path_key)
                                    .is_some_and(|path| selected_paths.contains(&path))
                            });
                        }
                    }
                }
                sensors.retain(|sensor| {
                    sensor
                        .get("photos")
                        .and_then(serde_json::Value::as_array)
                        .is_some_and(|photos| !photos.is_empty())
                });
            }

            if project_track.track_type == ProjectTrackType::OrdinaryVideo {
                let camera_profile = project_track
                    .camera_profile
                    .as_deref()
                    .unwrap_or("wide")
                    .to_string();
                object.insert(
                    "camera_profile".to_string(),
                    serde_json::Value::String(camera_profile.clone()),
                );
                if let Some(sensors) = object
                    .get_mut("photo_sensors")
                    .and_then(serde_json::Value::as_array_mut)
                {
                    for sensor in sensors {
                        if let Some(sensor_object) = sensor.as_object_mut() {
                            sensor_object.insert(
                                "camera_profile".to_string(),
                                serde_json::Value::String(camera_profile.clone()),
                            );
                        }
                    }
                }
            }
        }
    }
    Ok(Some(filtered))
}

fn refresh_alignment_manifest(
    project_root: &Path,
    project: &mut XpanoProjectV2,
) -> Result<(), ProjectCommandError> {
    let Some(media_manifest_relative) = project_relative_config_path(project, "mediaManifestPath")?
    else {
        reconstruction_config_mut(project).remove("alignmentManifestPath");
        return Ok(());
    };
    let media_manifest_path = project_root.join(&media_manifest_relative);
    let payload = std::fs::read(&media_manifest_path).map_err(|error| {
        ProjectCommandError::new(
            "artifact_corrupt",
            format!("failed to read complete media manifest: {}", error),
        )
    })?;
    let mut full_manifest: serde_json::Value =
        serde_json::from_slice(&payload).map_err(|error| {
            ProjectCommandError::new(
                "artifact_corrupt",
                format!("failed to parse complete media manifest: {}", error),
            )
        })?;
    let manifest_tracks = full_manifest
        .get("tracks")
        .and_then(serde_json::Value::as_array)
        .ok_or_else(|| {
            ProjectCommandError::new(
                "artifact_corrupt",
                "complete media manifest has no track list",
            )
        })?;
    let mut tracks_by_source = HashMap::new();
    for track in manifest_tracks {
        let source = track
            .get("source_paths")
            .and_then(serde_json::Value::as_array)
            .and_then(|paths| paths.first())
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| {
                ProjectCommandError::new(
                    "artifact_corrupt",
                    "complete media manifest track has no source path",
                )
            })?;
        let key = normalized_path_key(source);
        if tracks_by_source.insert(key, track).is_some() {
            return Err(ProjectCommandError::new(
                "artifact_corrupt",
                "complete media manifest contains duplicate source tracks",
            ));
        }
    }

    let mut active_tracks = Vec::new();
    for project_track in &project.tracks {
        if !matches!(
            project_track.status,
            ProjectTrackStatus::Prepared | ProjectTrackStatus::Ready
        ) {
            continue;
        }
        let source_key = normalized_path_key(&project_track.source_path);
        let manifest_track = tracks_by_source.get(&source_key).ok_or_else(|| {
            ProjectCommandError::new(
                "artifact_corrupt",
                format!(
                    "prepared track {} is missing from the complete media manifest",
                    project_track.label
                ),
            )
        })?;
        if let Some(filtered) = filter_manifest_track(manifest_track, project_track)? {
            active_tracks.push(filtered);
        }
    }
    full_manifest["tracks"] = serde_json::Value::Array(active_tracks);

    let alignment_manifest_relative = format!(
        "work/manifests/alignment_{:08}.json",
        project.revisions.alignment_input
    );
    write_json_value_atomic(
        &project_root.join(&alignment_manifest_relative),
        &full_manifest,
    )?;
    let config = reconstruction_config_mut(project);
    config.insert(
        "mediaManifestPath".to_string(),
        serde_json::Value::String(media_manifest_relative),
    );
    config.insert(
        "alignmentManifestPath".to_string(),
        serde_json::Value::String(alignment_manifest_relative),
    );
    Ok(())
}

pub fn commit_import_impl(
    project_root: &Path,
    expected_revision: u64,
    mut drafts: Vec<MediaImportDraft>,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    if drafts.is_empty() {
        return Err(ProjectCommandError::new(
            "invalid_project",
            "at least one media draft is required",
        ));
    }
    for draft in &mut drafts {
        normalize_color_lut_settings(&mut draft.extraction);
        validate_import_draft(draft)?;
    }
    let mut project = read_project(project_root)?;
    if project.revision != expected_revision {
        return Err(ProjectCommandError::revision_conflict(
            expected_revision,
            project.revision,
        ));
    }
    ensure_project_writable(&project)?;

    for draft in drafts {
        let source = Path::new(&draft.source_path);
        let fingerprint = source_fingerprint(source)?;
        project.tracks.push(ProjectTrack {
            id: Uuid::new_v4().to_string(),
            track_type: draft.track_type,
            label: draft.label.trim().to_string(),
            source_path: draft.source_path,
            source_fingerprint: fingerprint,
            camera_profile: if draft.track_type == ProjectTrackType::OrdinaryVideo {
                draft.camera_profile
            } else {
                None
            },
            trim: draft.trim,
            extraction: draft.extraction,
            status: ProjectTrackStatus::Draft,
            items: Vec::new(),
        });
    }
    project.revision += 1;
    project.revisions.media += 1;
    project.revisions.alignment_input += 1;
    mark_reconstruction_stale(&mut project);
    refresh_alignment_manifest(project_root, &mut project)?;
    touch_project(&mut project);
    write_project_atomic(project_root, &project)?;
    Ok(project)
}

pub fn update_track_settings_impl(
    project_root: &Path,
    expected_revision: u64,
    track_id: &str,
    patch: TrackSettingsPatch,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    let mut project = read_project(project_root)?;
    if project.revision != expected_revision {
        return Err(ProjectCommandError::revision_conflict(
            expected_revision,
            project.revision,
        ));
    }
    ensure_project_writable(&project)?;
    let track = project
        .tracks
        .iter_mut()
        .find(|track| track.id == track_id)
        .ok_or_else(|| ProjectCommandError::new("invalid_project", "media track was not found"))?;

    let mut media_changed = false;
    let mut alignment_changed = false;
    if let Some(trim) = patch.trim {
        if track.trim.as_ref() != Some(&trim) {
            track.trim = Some(trim);
            media_changed = true;
        }
    }
    if let Some(mut extraction) = patch.extraction {
        normalize_color_lut_settings(&mut extraction);
        validate_color_lut_settings(track.track_type, &track.source_path, &extraction)?;
        if track.extraction != extraction {
            track.extraction = extraction;
            media_changed = true;
        }
    }
    if let Some(camera_profile) = patch.camera_profile {
        if track.track_type != ProjectTrackType::OrdinaryVideo
            || !matches!(camera_profile.as_str(), "wide" | "standard")
        {
            return Err(ProjectCommandError::new(
                "invalid_media_type",
                "camera profile is only valid for ordinary video",
            ));
        }
        if track.camera_profile.as_deref() != Some(camera_profile.as_str()) {
            track.camera_profile = Some(camera_profile);
            alignment_changed = true;
        }
    }
    if media_changed {
        track.status = ProjectTrackStatus::Stale;
        project.revisions.media += 1;
        alignment_changed = true;
    }
    if !alignment_changed {
        return Ok(project);
    }
    project.revision += 1;
    project.revisions.alignment_input += 1;
    mark_reconstruction_stale(&mut project);
    refresh_alignment_manifest(project_root, &mut project)?;
    touch_project(&mut project);
    write_project_atomic(project_root, &project)?;
    Ok(project)
}

pub fn set_track_item_selection_impl(
    project_root: &Path,
    expected_revision: u64,
    track_id: &str,
    item_ids: &[String],
    selected: bool,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    let mut project = read_project(project_root)?;
    if project.revision != expected_revision {
        return Err(ProjectCommandError::revision_conflict(
            expected_revision,
            project.revision,
        ));
    }
    ensure_project_writable(&project)?;
    let requested: HashSet<&str> = item_ids.iter().map(String::as_str).collect();
    let track = project
        .tracks
        .iter_mut()
        .find(|track| track.id == track_id)
        .ok_or_else(|| ProjectCommandError::new("invalid_project", "media track was not found"))?;
    let existing: HashSet<&str> = track.items.iter().map(|item| item.id.as_str()).collect();
    if !requested.iter().all(|id| existing.contains(id)) {
        return Err(ProjectCommandError::new(
            "invalid_project",
            "one or more media items were not found",
        ));
    }
    let mut changed = false;
    for item in &mut track.items {
        if requested.contains(item.id.as_str()) && item.selected != selected {
            item.selected = selected;
            changed = true;
        }
    }
    if !changed {
        return Ok(project);
    }
    project.revision += 1;
    project.revisions.alignment_input += 1;
    mark_reconstruction_stale(&mut project);
    refresh_alignment_manifest(project_root, &mut project)?;
    touch_project(&mut project);
    write_project_atomic(project_root, &project)?;
    Ok(project)
}

pub fn list_track_items_impl(
    project_root: &Path,
    track_id: &str,
    cursor: usize,
    limit: usize,
    filter: MediaItemFilter,
) -> Result<MediaItemPage, ProjectCommandError> {
    let project = read_project(project_root)?;
    let track = project
        .tracks
        .iter()
        .find(|track| track.id == track_id)
        .ok_or_else(|| ProjectCommandError::new("invalid_project", "media track was not found"))?;
    let page_limit = limit.clamp(1, 250);
    let mut total = 0usize;
    let mut items = Vec::with_capacity(page_limit);
    for item in &track.items {
        let matches = match filter {
            MediaItemFilter::All => true,
            MediaItemFilter::Selected => item.selected,
            MediaItemFilter::Unselected => !item.selected,
        };
        if !matches {
            continue;
        }
        if total >= cursor && items.len() < page_limit {
            items.push(item.clone());
        }
        total += 1;
    }
    let consumed = cursor.saturating_add(items.len());
    Ok(MediaItemPage {
        items,
        total,
        next_cursor: (consumed < total).then_some(consumed),
    })
}

pub fn remove_project_track_impl(
    project_root: &Path,
    expected_revision: u64,
    track_id: &str,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    let mut project = read_project(project_root)?;
    if project.revision != expected_revision {
        return Err(ProjectCommandError::revision_conflict(
            expected_revision,
            project.revision,
        ));
    }
    ensure_project_writable(&project)?;
    let original_len = project.tracks.len();
    project.tracks.retain(|track| track.id != track_id);
    if project.tracks.len() == original_len {
        return Err(ProjectCommandError::new(
            "invalid_project",
            "media track was not found",
        ));
    }
    project.revision += 1;
    project.revisions.media += 1;
    project.revisions.alignment_input += 1;
    mark_reconstruction_stale(&mut project);
    refresh_alignment_manifest(project_root, &mut project)?;
    touch_project(&mut project);
    write_project_atomic(project_root, &project)?;
    Ok(project)
}

pub fn finalize_media_job_impl(project_root: &Path) -> Result<XpanoProjectV2, ProjectCommandError> {
    let result_path = project_root.join(MEDIA_RESULT_RELATIVE_PATH);
    let payload = std::fs::read(&result_path).map_err(|error| {
        ProjectCommandError::new(
            "artifact_corrupt",
            format!("failed to read media preparation result: {}", error),
        )
    })?;
    let result: MediaPrepareResult = serde_json::from_slice(&payload).map_err(|error| {
        ProjectCommandError::new(
            "artifact_corrupt",
            format!("failed to parse media preparation result: {}", error),
        )
    })?;
    if result.schema_version != 1 {
        return Err(ProjectCommandError::new(
            "artifact_corrupt",
            "unsupported media preparation result version",
        ));
    }
    if Path::new(&result.manifest_path).is_absolute()
        || result
            .manifest_path
            .replace('\\', "/")
            .split('/')
            .any(|part| part == "..")
    {
        return Err(ProjectCommandError::new(
            "artifact_corrupt",
            "media manifest path is not project-relative",
        ));
    }
    if !project_root.join(&result.manifest_path).is_file() {
        return Err(ProjectCommandError::new(
            "artifact_corrupt",
            "media manifest was not created",
        ));
    }

    let mut project = read_project(project_root)?;
    if project.project_id != result.project_id {
        return Err(ProjectCommandError::new(
            "artifact_corrupt",
            "media result belongs to a different project",
        ));
    }
    let mut result_ids = HashSet::new();
    for prepared in &result.tracks {
        if !result_ids.insert(prepared.id.clone()) {
            return Err(ProjectCommandError::new(
                "artifact_corrupt",
                "media result contains duplicate track ids",
            ));
        }
    }
    if result_ids.is_empty() {
        return Err(ProjectCommandError::new(
            "artifact_corrupt",
            "media result did not contain any tracks",
        ));
    }
    if let Some(marker) = read_media_job_marker(project_root)? {
        if marker.input_revision != result.input_revision {
            return Err(ProjectCommandError::new(
                "artifact_corrupt",
                "media result does not match the active media job revision",
            ));
        }
        let target_ids: HashSet<String> = marker.target_track_ids.iter().cloned().collect();
        if target_ids != result_ids {
            return Err(ProjectCommandError::new(
                "artifact_corrupt",
                "media result tracks do not match the active media job",
            ));
        }
        if !marker.target_track_ids.iter().all(|target_id| {
            project.tracks.iter().any(|track| {
                track.id == *target_id && track.status == ProjectTrackStatus::Running
            })
        }) {
            return Err(ProjectCommandError::new(
                "job_conflict",
                "media result targets are no longer owned by the active media job",
            ));
        }
        // WARN: The global revision also changes for workspace/name updates; media results must key off the media revision.
        if let Some(input_media_revision) = marker
            .input_media_revision
            .or(result.input_media_revision)
        {
            if project.revisions.media != input_media_revision {
                return Err(ProjectCommandError::new(
                    "revision_conflict",
                    format!(
                        "project media revision changed from {} to {}",
                        input_media_revision, project.revisions.media
                    ),
                ));
            }
        }
    } else {
        let recoverable_targets = result_ids.iter().all(|target_id| {
            project.tracks.iter().any(|track| {
                track.id == *target_id
                    && matches!(
                        track.status,
                        ProjectTrackStatus::Running
                            | ProjectTrackStatus::Failed
                            | ProjectTrackStatus::Interrupted
                    )
                    && track.items.is_empty()
            })
        });
        if let Some(input_media_revision) = result.input_media_revision {
            if project.revisions.media != input_media_revision {
                return Err(ProjectCommandError::new(
                    "revision_conflict",
                    format!(
                        "project media revision changed from {} to {}",
                        input_media_revision, project.revisions.media
                    ),
                ));
            }
            if !recoverable_targets {
                return Err(ProjectCommandError::new(
                    "job_conflict",
                    "orphaned media result targets are no longer recoverable",
                ));
            }
        } else {
            // WARN: Markerless legacy results are recoverable only while revisions stay monotonic and targets remain untouched.
            let legacy_recovery_safe = recoverable_targets
                && project.revisions.media <= result.input_revision
                && result.input_revision <= project.revision;
            if !legacy_recovery_safe {
                return Err(ProjectCommandError::revision_conflict(
                    result.input_revision,
                    project.revision,
                ));
            }
        }
    }
    // WARN: Result submission must allow the media job's running tracks to become ready.
    ensure_media_result_committable(&project)?;
    for prepared in result.tracks {
        let track = project
            .tracks
            .iter_mut()
            .find(|track| track.id == prepared.id)
            .ok_or_else(|| {
                ProjectCommandError::new(
                    "artifact_corrupt",
                    "media result contains an unknown track",
                )
            })?;
        track.status = prepared.status;
        track.items = prepared.items;
    }
    project.revision += 1;
    project.revisions.media += 1;
    project.revisions.alignment_input += 1;
    reconstruction_config_mut(&mut project).insert(
        "mediaManifestPath".to_string(),
        serde_json::Value::String(result.manifest_path),
    );
    mark_reconstruction_stale(&mut project);
    refresh_alignment_manifest(project_root, &mut project)?;
    touch_project(&mut project);
    write_project_atomic(project_root, &project)?;
    let _ = std::fs::remove_file(result_path);
    let _ = std::fs::remove_file(project_root.join(MEDIA_JOB_RELATIVE_PATH));
    Ok(project)
}

pub fn sync_media_job_result_impl(
    project_root: &Path,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    if project_root.join(MEDIA_RESULT_RELATIVE_PATH).is_file() {
        finalize_media_job_impl(project_root)
    } else {
        read_project(project_root)
    }
}

fn begin_media_job_impl(
    project_root: &Path,
    expected_revision: u64,
    target_track_ids: &[String],
) -> Result<XpanoProjectV2, ProjectCommandError> {
    let mut project = read_project(project_root)?;
    if project.revision != expected_revision {
        return Err(ProjectCommandError::revision_conflict(
            expected_revision,
            project.revision,
        ));
    }
    ensure_project_writable(&project)?;
    if target_track_ids.is_empty() {
        return Err(ProjectCommandError::new(
            "invalid_project",
            "media preparation requires at least one target track",
        ));
    }
    let requested: HashSet<&str> = target_track_ids.iter().map(String::as_str).collect();
    if requested.len() != target_track_ids.len()
        || !requested
            .iter()
            .all(|id| project.tracks.iter().any(|track| track.id == *id))
    {
        return Err(ProjectCommandError::new(
            "invalid_project",
            "one or more requested media tracks were not found",
        ));
    }
    for track in project
        .tracks
        .iter()
        .filter(|track| requested.contains(track.id.as_str()))
    {
        validate_color_lut_settings(track.track_type, &track.source_path, &track.extraction)?;
    }

    let marker = MediaJobMarker {
        schema_version: 1,
        input_revision: expected_revision,
        input_media_revision: Some(project.revisions.media),
        target_track_ids: target_track_ids.to_vec(),
    };
    write_json_value_atomic(
        &project_root.join(MEDIA_JOB_RELATIVE_PATH),
        &serde_json::to_value(marker).map_err(|error| {
            ProjectCommandError::new(
                "artifact_corrupt",
                format!("failed to serialize media job marker: {}", error),
            )
        })?,
    )?;
    for track in &mut project.tracks {
        if requested.contains(track.id.as_str()) {
            track.status = ProjectTrackStatus::Running;
        }
    }
    touch_project(&mut project);
    if let Err(error) = write_project_atomic(project_root, &project) {
        let _ = std::fs::remove_file(project_root.join(MEDIA_JOB_RELATIVE_PATH));
        return Err(error);
    }
    Ok(project)
}

pub(crate) fn fail_media_job_impl(
    project_root: &Path,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    let marker_path = project_root.join(MEDIA_JOB_RELATIVE_PATH);
    let Some(marker) = read_media_job_marker(project_root)? else {
        return read_project(project_root);
    };
    let mut project = read_project(project_root)?;
    let requested: HashSet<&str> = marker.target_track_ids.iter().map(String::as_str).collect();
    for track in &mut project.tracks {
        if requested.contains(track.id.as_str()) && track.status == ProjectTrackStatus::Running {
            track.status = ProjectTrackStatus::Failed;
        }
    }
    touch_project(&mut project);
    write_project_atomic(project_root, &project)?;
    std::fs::remove_file(marker_path).map_err(|error| {
        ProjectCommandError::new(
            "artifact_corrupt",
            format!("failed to clear media job marker: {}", error),
        )
    })?;
    Ok(project)
}

#[tauri::command]
pub fn commit_import(
    project_root: String,
    expected_revision: u64,
    drafts: Vec<MediaImportDraft>,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    commit_import_impl(Path::new(&project_root), expected_revision, drafts)
}

#[tauri::command]
pub fn update_track_settings(
    project_root: String,
    expected_revision: u64,
    track_id: String,
    patch: TrackSettingsPatch,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    update_track_settings_impl(
        Path::new(&project_root),
        expected_revision,
        &track_id,
        patch,
    )
}

#[tauri::command]
pub fn set_track_item_selection(
    project_root: String,
    expected_revision: u64,
    track_id: String,
    item_ids: Vec<String>,
    selected: bool,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    set_track_item_selection_impl(
        Path::new(&project_root),
        expected_revision,
        &track_id,
        &item_ids,
        selected,
    )
}

#[tauri::command]
pub fn list_track_items(
    project_root: String,
    track_id: String,
    cursor: usize,
    limit: usize,
    filter: MediaItemFilter,
) -> Result<MediaItemPage, ProjectCommandError> {
    list_track_items_impl(Path::new(&project_root), &track_id, cursor, limit, filter)
}

#[tauri::command]
pub fn remove_project_track(
    project_root: String,
    expected_revision: u64,
    track_id: String,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    remove_project_track_impl(Path::new(&project_root), expected_revision, &track_id)
}

#[tauri::command]
pub fn start_media_job(
    state: State<'_, crate::AppState>,
    app: AppHandle,
    project_root: String,
    expected_revision: u64,
    target_track_ids: Vec<String>,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    crate::batch::ensure_manual_startable(&app, state.inner())?;
    start_media_job_blocking(&app, state.inner(), project_root, expected_revision, target_track_ids, None)
}

pub(crate) fn start_media_job_blocking(
    app: &AppHandle,
    state: &crate::AppState,
    project_root: String,
    expected_revision: u64,
    target_track_ids: Vec<String>,
    task_id: Option<String>,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    let root = Path::new(&project_root);
    let stale_result = root.join(MEDIA_RESULT_RELATIVE_PATH);
    if stale_result.exists() {
        std::fs::remove_file(&stale_result).map_err(|error| {
            ProjectCommandError::new(
                "artifact_corrupt",
                format!("failed to clear stale media result: {}", error),
            )
        })?;
    }
    begin_media_job_impl(root, expected_revision, &target_track_ids)?;
    let (job_context, _) = match crate::job::begin_job_with_task_impl(root, ProjectWorkspace::Media, task_id) {
        Ok(started) => started,
        Err(error) => {
            let _ = fail_media_job_impl(root);
            return Err(error);
        }
    };
    let project = read_project(root)?;
    let _ = app.emit(
        "project:updated",
        ProjectUpdatedEvent {
            project_root: project_root.clone(),
            project: project.clone(),
        },
    );
    let mut args = vec![
        "--project-root".to_string(),
        project_root.clone(),
        "--expected-revision".to_string(),
        expected_revision.to_string(),
    ];
    for track_id in target_track_ids {
        args.push("--track-id".to_string());
        args.push(track_id);
    }
    let mut pipeline = match state.pipeline.lock() {
        Ok(pipeline) => pipeline,
        Err(error) => {
            let failed = fail_media_job_impl(root)?;
            let _ = app.emit(
                "project:updated",
                ProjectUpdatedEvent {
                    project_root: project_root.clone(),
                    project: failed,
                },
            );
            return Err(ProjectCommandError::new(
                "job_conflict",
                format!("pipeline lock failed: {}", error),
            ));
        }
    };
    if let Err(error) = pipeline.start_registered_job(
        app.clone(),
        "",
        "scripts/run_xpano_prepare_project.py",
        &args,
        job_context.clone(),
    ) {
        let failed = fail_media_job_impl(root)?;
        let _ = crate::job::finish_job_impl(&job_context, JobState::Failed, &error);
        let _ = app.emit(
            "project:updated",
            ProjectUpdatedEvent {
                project_root: project_root.clone(),
                project: failed,
            },
        );
        return Err(ProjectCommandError::new("backend_unavailable", error));
    }
    Ok(project)
}

#[tauri::command]
pub fn finalize_media_job(project_root: String) -> Result<XpanoProjectV2, ProjectCommandError> {
    finalize_media_job_impl(Path::new(&project_root))
}

#[tauri::command]
pub fn sync_media_job_result(
    project_root: String,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    sync_media_job_result_impl(Path::new(&project_root))
}

#[tauri::command]
pub fn fail_media_job(project_root: String) -> Result<XpanoProjectV2, ProjectCommandError> {
    fail_media_job_impl(Path::new(&project_root))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::contracts::{
        ExtractionSettings, ProjectTrackStatus, ProjectTrackType, ProjectTrim,
        ReconstructionStatus, XpanoProjectV2,
    };
    use crate::project::{read_project, write_project_atomic};
    use std::path::PathBuf;

    fn temp_case(name: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!(
            "xpano-media-v2-{}-{}-{}",
            name,
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&root).unwrap();
        root
    }

    fn fixture_project() -> XpanoProjectV2 {
        serde_json::from_str(include_str!(
            "../../../schemas/fixtures/xpano_project_v3.example.json"
        ))
        .unwrap()
    }

    fn write_single_frame_media_result(
        root: &Path,
        project: &XpanoProjectV2,
        target_id: &str,
    ) -> PathBuf {
        let result_path = root.join("work").join("media_prepare_result.json");
        std::fs::create_dir_all(result_path.parent().unwrap()).unwrap();
        let full_manifest = root.join("work").join("manifests").join("media_full.json");
        std::fs::create_dir_all(full_manifest.parent().unwrap()).unwrap();
        std::fs::write(
            &full_manifest,
            serde_json::to_vec_pretty(&serde_json::json!({
                "schema_version": 1,
                "workflow": "xpano_multi_track",
                "tracks": [{
                    "track_id": "prepared-pano",
                    "track_type": "panorama_video",
                    "source_paths": [project.tracks[0].source_path],
                    "frames": [{
                        "frame_id": "prepared-frame",
                        "left": "D:/left.jpg",
                        "right": "D:/right.jpg"
                    }]
                }]
            }))
            .unwrap(),
        )
        .unwrap();
        std::fs::write(
            &result_path,
            serde_json::to_vec_pretty(&serde_json::json!({
                "schemaVersion": 1,
                "projectId": project.project_id,
                "inputRevision": project.revision,
                "inputMediaRevision": project.revisions.media,
                "tracks": [{
                    "id": target_id,
                    "status": "ready",
                    "items": [{
                        "id": "frame_00001",
                        "timestamp": 0.0,
                        "selected": true,
                        "left": "work/frames/track/frame_00001/left.jpg",
                        "right": "work/frames/track/frame_00001/right.jpg",
                        "thumbnailLeft": "work/thumbnails/track/frame_00001/left.jpg",
                        "thumbnailRight": "work/thumbnails/track/frame_00001/right.jpg"
                    }]
                }],
                "manifestPath": "work/manifests/media_full.json"
            }))
            .unwrap(),
        )
        .unwrap();
        result_path
    }

    #[test]
    fn commit_import_persists_tracks_and_invalidates_alignment_input() {
        let root = temp_case("commit");
        let source = root.join("capture.osv");
        let lut = root.join("camera.CUBE");
        std::fs::write(&source, b"capture").unwrap();
        std::fs::write(&lut, b"LUT_3D_SIZE 2").unwrap();
        let mut project = fixture_project();
        project.tracks.clear();
        project.reconstruction.status = ReconstructionStatus::Complete;
        write_project_atomic(&root, &project).unwrap();

        let updated = commit_import_impl(
            &root,
            project.revision,
            vec![MediaImportDraft {
                track_type: ProjectTrackType::PanoramicVideo,
                label: "  Main panorama  ".to_string(),
                source_path: source.to_string_lossy().to_string(),
                camera_profile: None,
                trim: Some(ProjectTrim {
                    start: 0.0,
                    end: 20.0,
                }),
                extraction: ExtractionSettings {
                    frames_per_second: 1.0,
                    frame_limit: 20,
                    style_lut_path: Some(lut.to_string_lossy().to_string()),
                    color_lut_preset: None,
                },
            }],
        )
        .unwrap();

        assert_eq!(updated.tracks.len(), 1);
        assert_eq!(updated.tracks[0].label, "Main panorama");
        assert_eq!(updated.tracks[0].status, ProjectTrackStatus::Draft);
        assert_eq!(updated.tracks[0].source_fingerprint.size, 7);
        assert_eq!(
            updated.tracks[0].extraction.style_lut_path.as_deref(),
            Some(lut.to_string_lossy().as_ref())
        );
        assert_eq!(updated.revisions.media, project.revisions.media + 1);
        assert_eq!(
            updated.revisions.alignment_input,
            project.revisions.alignment_input + 1
        );
        assert_eq!(updated.reconstruction.status, ReconstructionStatus::Stale);
        assert_eq!(read_project(&root).unwrap(), updated);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn panorama_import_rejects_non_osv_sources() {
        let root = temp_case("type-guard");
        let source = root.join("capture.mp4");
        std::fs::write(&source, b"capture").unwrap();
        let project = fixture_project();
        write_project_atomic(&root, &project).unwrap();

        let error = commit_import_impl(
            &root,
            project.revision,
            vec![MediaImportDraft {
                track_type: ProjectTrackType::PanoramicVideo,
                label: "Invalid panorama".to_string(),
                source_path: source.to_string_lossy().to_string(),
                camera_profile: None,
                trim: None,
                extraction: ExtractionSettings {
                    frames_per_second: 1.0,
                    frame_limit: 0,
                    style_lut_path: None,
                    color_lut_preset: None,
                },
            }],
        )
        .unwrap_err();

        assert_eq!(error.code, "invalid_media_type");
        assert_eq!(read_project(&root).unwrap(), project);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn changing_only_camera_profile_keeps_extracted_media_valid() {
        let root = temp_case("camera-profile");
        let mut project = fixture_project();
        project.tracks[0].track_type = ProjectTrackType::OrdinaryVideo;
        project.tracks[0].camera_profile = Some("wide".to_string());
        project.tracks[0].status = ProjectTrackStatus::Ready;
        write_project_atomic(&root, &project).unwrap();

        let updated = update_track_settings_impl(
            &root,
            project.revision,
            &project.tracks[0].id,
            TrackSettingsPatch {
                trim: None,
                extraction: None,
                camera_profile: Some("standard".to_string()),
            },
        )
        .unwrap();

        assert_eq!(updated.tracks[0].status, ProjectTrackStatus::Ready);
        assert_eq!(updated.revisions.media, project.revisions.media);
        assert_eq!(
            updated.revisions.alignment_input,
            project.revisions.alignment_input + 1
        );
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn changing_extraction_marks_only_the_track_stale() {
        let root = temp_case("extraction");
        let project = fixture_project();
        write_project_atomic(&root, &project).unwrap();

        let updated = update_track_settings_impl(
            &root,
            project.revision,
            &project.tracks[0].id,
            TrackSettingsPatch {
                trim: None,
                extraction: Some(ExtractionSettings {
                    frames_per_second: 2.0,
                    frame_limit: 10,
                    style_lut_path: None,
                    color_lut_preset: None,
                }),
                camera_profile: None,
            },
        )
        .unwrap();

        assert_eq!(updated.tracks[0].status, ProjectTrackStatus::Stale);
        assert_eq!(updated.revisions.media, project.revisions.media + 1);
        assert_eq!(
            updated.revisions.alignment_input,
            project.revisions.alignment_input + 1
        );
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn photo_import_accepts_style_lut_and_marks_project_stale() {
        let root = temp_case("photo-color-lut");
        let photos = root.join("photos");
        let lut = root.join("restore.cube");
        std::fs::create_dir_all(&photos).unwrap();
        std::fs::write(photos.join("capture.jpg"), b"photo").unwrap();
        std::fs::write(&lut, b"LUT_3D_SIZE 2").unwrap();
        let mut project = fixture_project();
        project.reconstruction.status = ReconstructionStatus::Complete;
        write_project_atomic(&root, &project).unwrap();

        let updated = commit_import_impl(
            &root,
            project.revision,
            vec![MediaImportDraft {
                track_type: ProjectTrackType::StandardPhotos,
                label: "Photos".to_string(),
                source_path: photos.to_string_lossy().to_string(),
                camera_profile: None,
                trim: None,
                extraction: ExtractionSettings {
                    frames_per_second: 1.0,
                    frame_limit: 0,
                    style_lut_path: Some(lut.to_string_lossy().to_string()),
                    color_lut_preset: None,
                },
            }],
        )
        .unwrap();

        assert_eq!(updated.tracks.len(), project.tracks.len() + 1);
        let imported = updated.tracks.last().unwrap();
        assert_eq!(imported.track_type, ProjectTrackType::StandardPhotos);
        assert_eq!(
            imported.extraction.style_lut_path.as_deref(),
            Some(lut.to_string_lossy().as_ref())
        );
        assert_eq!(updated.revisions.media, project.revisions.media + 1);
        assert_eq!(
            updated.revisions.alignment_input,
            project.revisions.alignment_input + 1
        );
        assert_eq!(updated.reconstruction.status, ReconstructionStatus::Stale);
        assert_eq!(read_project(&root).unwrap(), updated);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn changing_style_lut_marks_only_the_target_track_stale() {
        let root = temp_case("color-lut-change");
        let lut = root.join("restore.cube");
        std::fs::write(&lut, b"LUT_3D_SIZE 2").unwrap();
        let mut project = fixture_project();
        project.tracks[0].status = ProjectTrackStatus::Ready;
        let mut second = project.tracks[0].clone();
        second.id = "second-track".to_string();
        project.tracks.push(second);
        write_project_atomic(&root, &project).unwrap();

        let updated = update_track_settings_impl(
            &root,
            project.revision,
            &project.tracks[0].id,
            TrackSettingsPatch {
                trim: None,
                extraction: Some(ExtractionSettings {
                    frames_per_second: project.tracks[0].extraction.frames_per_second,
                    frame_limit: project.tracks[0].extraction.frame_limit,
                    style_lut_path: Some(lut.to_string_lossy().to_string()),
                    color_lut_preset: None,
                }),
                camera_profile: None,
            },
        )
        .unwrap();

        assert_eq!(updated.tracks[0].status, ProjectTrackStatus::Stale);
        assert_eq!(updated.tracks[1].status, ProjectTrackStatus::Ready);
        assert_eq!(updated.revisions.media, project.revisions.media + 1);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn missing_style_lut_rejects_media_job_before_state_mutation() {
        let root = temp_case("missing-color-lut");
        let mut project = fixture_project();
        project.tracks[0].status = ProjectTrackStatus::Draft;
        project.tracks[0].extraction.style_lut_path = Some(
            root.join("removed.cube").to_string_lossy().to_string(),
        );
        write_project_atomic(&root, &project).unwrap();
        let target_id = project.tracks[0].id.clone();

        let error = begin_media_job_impl(
            &root,
            project.revision,
            std::slice::from_ref(&target_id),
        )
        .unwrap_err();

        assert_eq!(error.code, "missing_source");
        assert_eq!(read_project(&root).unwrap(), project);
        assert!(!root.join(MEDIA_JOB_RELATIVE_PATH).exists());
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn bundled_osv_lut_preset_is_verified_before_media_job_starts() {
        let root = temp_case("bundled-osv-lut");
        let mut project = fixture_project();
        project.tracks[0].status = ProjectTrackStatus::Draft;
        project.tracks[0].source_path = root.join("DJI_0001.osv").to_string_lossy().to_string();
        project.tracks[0].extraction.color_lut_preset =
            Some(DJI_OSMO_360_DLOGM_REC709_PRESET.to_string());
        write_project_atomic(&root, &project).unwrap();
        let target_id = project.tracks[0].id.clone();

        let updated = begin_media_job_impl(
            &root,
            project.revision,
            std::slice::from_ref(&target_id),
        )
        .unwrap();

        assert_eq!(updated.tracks[0].status, ProjectTrackStatus::Running);
        assert!(root.join(MEDIA_JOB_RELATIVE_PATH).is_file());
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn item_selection_is_paged_and_persisted_without_deleting_files() {
        let root = temp_case("selection");
        let project = fixture_project();
        write_project_atomic(&root, &project).unwrap();
        let item_id = project.tracks[0].items[0].id.clone();

        let updated = set_track_item_selection_impl(
            &root,
            project.revision,
            &project.tracks[0].id,
            &[item_id.clone()],
            false,
        )
        .unwrap();
        let page = list_track_items_impl(
            &root,
            &project.tracks[0].id,
            0,
            100,
            MediaItemFilter::Unselected,
        )
        .unwrap();

        assert!(!updated.tracks[0].items[0].selected);
        assert_eq!(updated.revisions.media, project.revisions.media);
        assert_eq!(page.total, 1);
        assert_eq!(page.items[0].id, item_id);
        assert!(root
            .join(updated.tracks[0].items[0].left.as_ref().unwrap())
            .to_string_lossy()
            .contains("frame_00001"));
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn selection_writes_revisioned_manifest_and_can_restore_items() {
        let root = temp_case("selection-manifest");
        let mut project = fixture_project();
        project.reconstruction.config = serde_json::json!({
            "mediaManifestPath": "work/manifests/media_full.json"
        });
        write_project_atomic(&root, &project).unwrap();
        let full_manifest = root.join("work").join("manifests").join("media_full.json");
        std::fs::create_dir_all(full_manifest.parent().unwrap()).unwrap();
        std::fs::write(
            &full_manifest,
            serde_json::to_vec_pretty(&serde_json::json!({
                "schema_version": 1,
                "workflow": "xpano_multi_track",
                "tracks": [{
                    "track_id": "legacy-pano",
                    "track_type": "panorama_video",
                    "source_paths": [project.tracks[0].source_path],
                    "frames": [{
                        "frame_id": "legacy-frame",
                        "group_label": "legacy-frame",
                        "left": "D:/left.jpg",
                        "right": "D:/right.jpg"
                    }]
                }]
            }))
            .unwrap(),
        )
        .unwrap();
        let item_id = project.tracks[0].items[0].id.clone();

        let disabled = set_track_item_selection_impl(
            &root,
            project.revision,
            &project.tracks[0].id,
            &[item_id.clone()],
            false,
        )
        .unwrap();
        let disabled_manifest = disabled.reconstruction.config["alignmentManifestPath"]
            .as_str()
            .unwrap();
        let disabled_json: serde_json::Value =
            serde_json::from_slice(&std::fs::read(root.join(disabled_manifest)).unwrap()).unwrap();
        assert_eq!(disabled_json["tracks"].as_array().unwrap().len(), 0);

        let restored = set_track_item_selection_impl(
            &root,
            disabled.revision,
            &project.tracks[0].id,
            &[item_id],
            true,
        )
        .unwrap();
        let restored_manifest = restored.reconstruction.config["alignmentManifestPath"]
            .as_str()
            .unwrap();
        let restored_json: serde_json::Value =
            serde_json::from_slice(&std::fs::read(root.join(restored_manifest)).unwrap()).unwrap();
        assert_eq!(
            restored_json["tracks"][0]["frames"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn ordinary_video_selection_filters_sensor_photos_and_updates_camera_profile() {
        let root = temp_case("ordinary-manifest");
        let mut project = fixture_project();
        project.tracks[0].track_type = ProjectTrackType::OrdinaryVideo;
        project.tracks[0].camera_profile = Some("wide".to_string());
        project.tracks[0].status = ProjectTrackStatus::Ready;
        project.tracks[0].items[0].id = "frame_00001".to_string();
        let mut second = project.tracks[0].items[0].clone();
        second.id = "frame_00002".to_string();
        project.tracks[0].items.push(second);
        project.reconstruction.config = serde_json::json!({
            "mediaManifestPath": "work/manifests/media_full.json"
        });
        write_project_atomic(&root, &project).unwrap();
        let full_manifest = root.join("work").join("manifests").join("media_full.json");
        std::fs::create_dir_all(full_manifest.parent().unwrap()).unwrap();
        std::fs::write(
            &full_manifest,
            serde_json::to_vec_pretty(&serde_json::json!({
                "schema_version": 1,
                "workflow": "xpano_multi_track",
                "tracks": [{
                    "track_id": "ordinary",
                    "track_type": "ordinary_video",
                    "source_paths": [project.tracks[0].source_path],
                    "camera_profile": "wide",
                    "photos": ["D:/frame_1.jpg", "D:/frame_2.jpg"],
                    "photo_sensors": [{
                        "sensor_id": "ordinary-frame",
                        "sensor_label": "ordinary-frame",
                        "camera_profile": "wide",
                        "photos": ["D:/frame_1.jpg", "D:/frame_2.jpg"]
                    }]
                }]
            }))
            .unwrap(),
        )
        .unwrap();

        let profile_updated = update_track_settings_impl(
            &root,
            project.revision,
            &project.tracks[0].id,
            TrackSettingsPatch {
                trim: None,
                extraction: None,
                camera_profile: Some("standard".to_string()),
            },
        )
        .unwrap();
        let filtered = set_track_item_selection_impl(
            &root,
            profile_updated.revision,
            &project.tracks[0].id,
            &["frame_00002".to_string()],
            false,
        )
        .unwrap();
        let manifest_path = filtered.reconstruction.config["alignmentManifestPath"]
            .as_str()
            .unwrap();
        let manifest: serde_json::Value =
            serde_json::from_slice(&std::fs::read(root.join(manifest_path)).unwrap()).unwrap();

        assert_eq!(manifest["tracks"][0]["camera_profile"], "standard");
        assert_eq!(manifest["tracks"][0]["photos"].as_array().unwrap().len(), 1);
        assert_eq!(
            manifest["tracks"][0]["photo_sensors"][0]["camera_profile"],
            "standard"
        );
        assert_eq!(
            manifest["tracks"][0]["photo_sensors"][0]["photos"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn finalize_media_job_atomically_applies_prepared_items() {
        let root = temp_case("finalize");
        let mut project = fixture_project();
        project.tracks[0].status = ProjectTrackStatus::Running;
        project.tracks[0].items.clear();
        write_project_atomic(&root, &project).unwrap();
        let result_path = root.join("work").join("media_prepare_result.json");
        std::fs::create_dir_all(result_path.parent().unwrap()).unwrap();
        let full_manifest = root.join("work").join("manifests").join("media_full.json");
        std::fs::create_dir_all(full_manifest.parent().unwrap()).unwrap();
        std::fs::write(
            &full_manifest,
            serde_json::to_vec_pretty(&serde_json::json!({
                "schema_version": 1,
                "workflow": "xpano_multi_track",
                "tracks": [{
                    "track_id": "prepared-pano",
                    "track_type": "panorama_video",
                    "source_paths": [project.tracks[0].source_path],
                    "frames": [{
                        "frame_id": "prepared-frame",
                        "left": "D:/left.jpg",
                        "right": "D:/right.jpg"
                    }]
                }]
            }))
            .unwrap(),
        )
        .unwrap();
        std::fs::write(
            &result_path,
            serde_json::to_vec_pretty(&serde_json::json!({
                "schemaVersion": 1,
                "projectId": project.project_id,
                "inputRevision": project.revision,
                "tracks": [{
                    "id": project.tracks[0].id,
                    "status": "ready",
                    "items": [{
                        "id": "frame_00001",
                        "timestamp": 0.0,
                        "selected": true,
                        "left": "work/frames/track/frame_00001/left.jpg",
                        "right": "work/frames/track/frame_00001/right.jpg",
                        "thumbnailLeft": "work/thumbnails/track/frame_00001/left.jpg",
                        "thumbnailRight": "work/thumbnails/track/frame_00001/right.jpg"
                    }]
                }],
                "manifestPath": "work/manifests/media_full.json"
            }))
            .unwrap(),
        )
        .unwrap();

        let updated = sync_media_job_result_impl(&root).unwrap();

        assert_eq!(updated.tracks[0].status, ProjectTrackStatus::Ready);
        assert_eq!(updated.tracks[0].items.len(), 1);
        assert_eq!(updated.revision, project.revision + 1);
        assert_eq!(updated.revisions.media, project.revisions.media + 1);
        assert_eq!(
            updated.revisions.alignment_input,
            project.revisions.alignment_input + 1
        );
        assert!(!result_path.exists());
        assert_eq!(
            updated.reconstruction.config["mediaManifestPath"],
            "work/manifests/media_full.json"
        );
        assert!(updated.reconstruction.config["alignmentManifestPath"]
            .as_str()
            .is_some_and(|path| path.starts_with("work/manifests/alignment_")));
        assert_eq!(read_project(&root).unwrap(), updated);

        let synced_again = sync_media_job_result_impl(&root).unwrap();
        assert_eq!(synced_again, updated);
        assert_eq!(synced_again.revision, updated.revision);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn finalize_media_job_allows_non_media_revision_changes() {
        let root = temp_case("finalize-non-media-revision");
        let mut project = fixture_project();
        project.tracks[0].status = ProjectTrackStatus::Draft;
        project.tracks[0].items.clear();
        write_project_atomic(&root, &project).unwrap();
        let target_id = project.tracks[0].id.clone();
        begin_media_job_impl(&root, project.revision, std::slice::from_ref(&target_id)).unwrap();
        let result_path = write_single_frame_media_result(&root, &project, &target_id);

        let mut navigated = read_project(&root).unwrap();
        navigated.active_workspace = ProjectWorkspace::Results;
        navigated.name = "Renamed while preparing".to_string();
        navigated.revision += 2;
        write_project_atomic(&root, &navigated).unwrap();

        let updated = sync_media_job_result_impl(&root).unwrap();

        assert_eq!(updated.tracks[0].status, ProjectTrackStatus::Ready);
        assert_eq!(updated.revision, navigated.revision + 1);
        assert_eq!(updated.revisions.media, navigated.revisions.media + 1);
        assert!(!result_path.exists());
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn finalize_media_job_recovers_legacy_failed_result_without_marker() {
        let root = temp_case("finalize-legacy-result");
        let mut project = fixture_project();
        project.tracks[0].status = ProjectTrackStatus::Failed;
        project.tracks[0].items.clear();
        project.revision = 2;
        project.revisions.media = 1;
        write_project_atomic(&root, &project).unwrap();
        let target_id = project.tracks[0].id.clone();
        let result_path = write_single_frame_media_result(&root, &project, &target_id);
        let mut legacy_result: serde_json::Value = serde_json::from_slice(
            &std::fs::read(&result_path).unwrap(),
        )
        .unwrap();
        legacy_result
            .as_object_mut()
            .unwrap()
            .remove("inputMediaRevision");
        std::fs::write(
            &result_path,
            serde_json::to_vec_pretty(&legacy_result).unwrap(),
        )
        .unwrap();

        let mut navigated = read_project(&root).unwrap();
        navigated.active_workspace = ProjectWorkspace::Media;
        navigated.revision += 2;
        write_project_atomic(&root, &navigated).unwrap();

        let updated = sync_media_job_result_impl(&root).unwrap();

        assert_eq!(updated.tracks[0].status, ProjectTrackStatus::Ready);
        assert_eq!(updated.revision, navigated.revision + 1);
        assert!(!result_path.exists());
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn finalize_media_job_rejects_legacy_result_after_media_revision_advanced() {
        let root = temp_case("finalize-stale-legacy-result");
        let mut project = fixture_project();
        project.tracks[0].status = ProjectTrackStatus::Failed;
        project.tracks[0].items.clear();
        project.revision = 2;
        project.revisions.media = 1;
        write_project_atomic(&root, &project).unwrap();
        let target_id = project.tracks[0].id.clone();
        let result_path = write_single_frame_media_result(&root, &project, &target_id);
        let mut legacy_result: serde_json::Value = serde_json::from_slice(
            &std::fs::read(&result_path).unwrap(),
        )
        .unwrap();
        legacy_result
            .as_object_mut()
            .unwrap()
            .remove("inputMediaRevision");
        std::fs::write(
            &result_path,
            serde_json::to_vec_pretty(&legacy_result).unwrap(),
        )
        .unwrap();

        let mut edited = read_project(&root).unwrap();
        edited.revision = 4;
        edited.revisions.media = 3;
        write_project_atomic(&root, &edited).unwrap();

        let error = sync_media_job_result_impl(&root).unwrap_err();

        assert_eq!(error.code, "revision_conflict");
        assert!(result_path.exists());
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn finalize_media_job_rejects_changed_media_revision() {
        let root = temp_case("finalize-media-revision-conflict");
        let mut project = fixture_project();
        project.tracks[0].status = ProjectTrackStatus::Draft;
        project.tracks[0].items.clear();
        write_project_atomic(&root, &project).unwrap();
        let target_id = project.tracks[0].id.clone();
        begin_media_job_impl(&root, project.revision, std::slice::from_ref(&target_id)).unwrap();
        write_single_frame_media_result(&root, &project, &target_id);

        let mut changed = read_project(&root).unwrap();
        changed.revision += 1;
        changed.revisions.media += 1;
        write_project_atomic(&root, &changed).unwrap();

        let error = sync_media_job_result_impl(&root).unwrap_err();

        assert_eq!(error.code, "revision_conflict");
        assert!(error.message.contains("project media revision changed"));
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn media_job_state_marks_only_targets_without_changing_revision() {
        let root = temp_case("job-state");
        let mut project = fixture_project();
        let mut second = project.tracks[0].clone();
        second.id = "second-track".to_string();
        second.status = ProjectTrackStatus::Ready;
        project.tracks[0].status = ProjectTrackStatus::Draft;
        project.tracks.push(second);
        write_project_atomic(&root, &project).unwrap();

        let running = begin_media_job_impl(
            &root,
            project.revision,
            &[project.tracks[0].id.clone()],
        )
        .unwrap();

        assert_eq!(running.revision, project.revision);
        assert_eq!(running.tracks[0].status, ProjectTrackStatus::Running);
        assert_eq!(running.tracks[1].status, ProjectTrackStatus::Ready);
        let marker = read_media_job_marker(&root).unwrap().unwrap();
        assert_eq!(marker.input_media_revision, Some(project.revisions.media));
        let edit_error = update_track_settings_impl(
            &root,
            running.revision,
            &running.tracks[0].id,
            TrackSettingsPatch {
                trim: None,
                extraction: Some(ExtractionSettings {
                    frames_per_second: 2.0,
                    frame_limit: 10,
                    style_lut_path: None,
                    color_lut_preset: None,
                }),
                camera_profile: None,
            },
        )
        .unwrap_err();
        assert_eq!(edit_error.code, "job_conflict");

        let failed = fail_media_job_impl(&root).unwrap();

        assert_eq!(failed.revision, project.revision);
        assert_eq!(failed.tracks[0].status, ProjectTrackStatus::Failed);
        assert_eq!(failed.tracks[1].status, ProjectTrackStatus::Ready);
        assert!(!root.join(MEDIA_JOB_RELATIVE_PATH).exists());
        let _ = std::fs::remove_dir_all(root);
    }
}
