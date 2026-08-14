use crate::contracts::{
    GeometryState, PointCloudVariant, PointVariantKind, PointVariantStatus, ProjectRevisions, ProjectWorkspace,
    ReconstructionBackend, ReconstructionState, ReconstructionStatus, TrainingState, WorldTransform,
    XpanoProjectV2, PROJECT_SCHEMA_VERSION,
};
use chrono::{SecondsFormat, Utc};
use serde::Serialize;
#[cfg(not(windows))]
use std::fs::File;
use std::fs::OpenOptions;
use std::io::Write;
use std::path::{Path, PathBuf};
use uuid::Uuid;

pub const PROJECT_FILE_NAME: &str = "xpano_project.json";
const PROJECT_TEMP_FILE_NAME: &str = "xpano_project.json.tmp";

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProjectCommandError {
    pub code: String,
    pub message: String,
    pub details: serde_json::Value,
}

impl ProjectCommandError {
    pub(crate) fn new(code: &str, message: impl Into<String>) -> Self {
        Self {
            code: code.to_string(),
            message: message.into(),
            details: serde_json::json!({}),
        }
    }

    pub(crate) fn revision_conflict(expected: u64, actual: u64) -> Self {
        Self {
            code: "revision_conflict".to_string(),
            message: format!("project revision changed from {} to {}", expected, actual),
            details: serde_json::json!({ "expectedRevision": expected, "actualRevision": actual }),
        }
    }

    fn project_exists(project_root: &Path) -> Self {
        Self {
            code: "project_exists".to_string(),
            message: "an xPano project already exists at the requested location".to_string(),
            details: serde_json::json!({ "projectRoot": project_root }),
        }
    }
}

#[derive(Clone, Debug, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProjectValidationReport {
    pub missing_source_track_ids: Vec<String>,
    pub missing_artifact_paths: Vec<String>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProjectOpenResult {
    pub project_root: PathBuf,
    pub project: XpanoProjectV2,
    pub validation: ProjectValidationReport,
}

fn project_error(code: &str, context: &str, error: impl std::fmt::Display) -> ProjectCommandError {
    let disk_full =
        error.to_string().contains("disk full") || error.to_string().contains("空间不足");
    ProjectCommandError::new(
        if disk_full { "disk_full" } else { code },
        format!("{}: {}", context, error),
    )
}

fn now_iso8601() -> String {
    Utc::now().to_rfc3339_opts(SecondsFormat::Millis, true)
}

pub(crate) fn touch_project(project: &mut XpanoProjectV2) {
    project.updated_at = now_iso8601();
}

fn default_project_root(first_source: &Path) -> Result<PathBuf, ProjectCommandError> {
    if first_source.is_dir() {
        return Ok(first_source.join("xPano"));
    }
    first_source
        .parent()
        .map(|parent| parent.join("xPano"))
        .ok_or_else(|| {
            ProjectCommandError::new("invalid_project", "first source has no parent directory")
        })
}

fn ensure_project_root_available(project_root: &Path) -> Result<(), ProjectCommandError> {
    if project_root.join(PROJECT_FILE_NAME).exists() {
        return Err(ProjectCommandError::project_exists(project_root));
    }
    if !project_root.exists() {
        return Ok(());
    }
    if !project_root.is_dir() {
        return Err(ProjectCommandError::new(
            "invalid_project",
            "requested project root is not a directory",
        ));
    }
    let mut entries = std::fs::read_dir(project_root).map_err(|error| {
        project_error(
            "invalid_project",
            "failed to inspect project directory",
            error,
        )
    })?;
    if entries.next().is_some() {
        return Err(ProjectCommandError::new(
            "invalid_project",
            "requested project directory is not empty",
        ));
    }
    Ok(())
}

fn empty_project(name: &str) -> XpanoProjectV2 {
    let timestamp = now_iso8601();
    XpanoProjectV2 {
        schema_version: PROJECT_SCHEMA_VERSION,
        project_id: Uuid::new_v4().to_string(),
        name: name.to_string(),
        created_at: timestamp.clone(),
        updated_at: timestamp.clone(),
        active_workspace: ProjectWorkspace::Media,
        revision: 0,
        revisions: ProjectRevisions {
            media: 0,
            alignment_input: 0,
            alignment: 0,
            geometry: 0,
        },
        tracks: Vec::new(),
        reconstruction: ReconstructionState {
            status: ReconstructionStatus::Idle,
            input_revision: 0,
            backend: ReconstructionBackend::Metashape,
            config: serde_json::json!({}),
            project_path: None,
            colmap_path: None,
        },
        training: TrainingState::default(),
        geometry: GeometryState {
            transform: WorldTransform {
                world_from_canonical: [
                    1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0,
                ],
                revision: 0,
            },
            active_variant_id: "standard".to_string(),
            variants: vec![PointCloudVariant {
                id: "standard".to_string(),
                label: "Standard".to_string(),
                kind: PointVariantKind::Standard,
                canonical_path: "work/geometry/variants/standard/points3D.bin".to_string(),
                point_count: 0,
                created_at: timestamp,
                source_job_id: None,
                protected: true,
                checksum_sha256: String::new(),
                transform_revision: 0,
                status: PointVariantStatus::Stale,
            }],
        },
        jobs: Vec::new(),
    }
}

pub fn create_project_impl(
    name: &str,
    first_source: &Path,
    optional_root: Option<&Path>,
) -> Result<ProjectOpenResult, ProjectCommandError> {
    let trimmed = name.trim();
    if trimmed.is_empty() {
        return Err(ProjectCommandError::new(
            "invalid_project",
            "project name must not be empty",
        ));
    }
    if !first_source.exists() {
        return Err(ProjectCommandError::new(
            "missing_source",
            "first source does not exist",
        ));
    }
    let project_root = match optional_root {
        Some(root) => root.to_path_buf(),
        None => default_project_root(first_source)?,
    };
    ensure_project_root_available(&project_root)?;
    let project = empty_project(trimmed);
    write_project_atomic(&project_root, &project)?;
    let project_root = project_root.canonicalize().map_err(|error| {
        project_error(
            "invalid_project",
            "failed to resolve created project directory",
            error,
        )
    })?;
    let validation = validate_project_files(&project_root, &project);
    Ok(ProjectOpenResult {
        project_root,
        project,
        validation,
    })
}

pub fn open_or_create_project_impl(
    name: &str,
    first_source: &Path,
    optional_root: Option<&Path>,
) -> Result<ProjectOpenResult, ProjectCommandError> {
    let project_root = match optional_root {
        Some(root) => root.to_path_buf(),
        None => default_project_root(first_source)?,
    };
    if project_root.join(PROJECT_FILE_NAME).is_file() {
        return open_project_from_path(&project_root)?.ok_or_else(|| {
            ProjectCommandError::new("invalid_project", "existing xPano project could not be opened")
        });
    }

    match create_project_impl(name, first_source, optional_root) {
        Ok(result) => Ok(result),
        Err(error) if error.code == "project_exists" => open_project_from_path(&project_root)?
            .ok_or_else(|| {
                ProjectCommandError::new(
                    "invalid_project",
                    "xPano project appeared during creation but could not be opened",
                )
            }),
        Err(error) => Err(error),
    }
}

fn parse_project(text: &str) -> Result<(XpanoProjectV2, bool), ProjectCommandError> {
    // WARN: Windows JSON writers may prepend a UTF-8 BOM; project loading must remain compatible.
    let text = text.strip_prefix('\u{feff}').unwrap_or(text);
    let mut value: serde_json::Value = serde_json::from_str(text).map_err(|error| {
        project_error(
            "invalid_project",
            "failed to parse xpano_project.json",
            error,
        )
    })?;
    let schema_version = value
        .get("schemaVersion")
        .and_then(serde_json::Value::as_u64)
        .ok_or_else(|| ProjectCommandError::new("invalid_project", "project schemaVersion is missing"))?;
    let migrated = if schema_version == 2 {
        let tracks = value
            .get_mut("tracks")
            .and_then(serde_json::Value::as_array_mut)
            .ok_or_else(|| ProjectCommandError::new("invalid_project", "project tracks are missing"))?;
        for track in tracks {
            let extraction = track
                .get_mut("extraction")
                .and_then(serde_json::Value::as_object_mut)
                .ok_or_else(|| ProjectCommandError::new("invalid_project", "track extraction settings are missing"))?;
            let interval = extraction
                .remove("secondsPerFrame")
                .and_then(|value| value.as_f64())
                .ok_or_else(|| ProjectCommandError::new("invalid_project", "legacy secondsPerFrame is missing"))?;
            if !interval.is_finite() || interval <= 0.0 {
                return Err(ProjectCommandError::new(
                    "invalid_project",
                    "legacy secondsPerFrame must be greater than 0",
                ));
            }
            let fps = serde_json::Number::from_f64(1.0 / interval).ok_or_else(|| {
                ProjectCommandError::new("invalid_project", "legacy extraction rate is invalid")
            })?;
            extraction.insert("framesPerSecond".to_string(), serde_json::Value::Number(fps));
        }
        value["schemaVersion"] = serde_json::json!(PROJECT_SCHEMA_VERSION);
        true
    } else {
        false
    };
    let project: XpanoProjectV2 = serde_json::from_value(value).map_err(|error| {
        project_error(
            "invalid_project",
            "failed to parse xpano_project.json",
            error,
        )
    })?;
    project
        .validate()
        .map_err(|error| ProjectCommandError::new("invalid_project", error))?;
    Ok((project, migrated))
}

pub fn read_project(project_root: &Path) -> Result<XpanoProjectV2, ProjectCommandError> {
    let path = project_root.join(PROJECT_FILE_NAME);
    let text = std::fs::read_to_string(&path).map_err(|error| {
        project_error(
            "invalid_project",
            "failed to read xpano_project.json",
            error,
        )
    })?;
    parse_project(&text).map(|(project, _)| project)
}

pub fn write_project_atomic(
    project_root: &Path,
    project: &XpanoProjectV2,
) -> Result<(), ProjectCommandError> {
    project
        .validate()
        .map_err(|error| ProjectCommandError::new("invalid_project", error))?;
    std::fs::create_dir_all(project_root).map_err(|error| {
        project_error(
            "invalid_project",
            "failed to create project directory",
            error,
        )
    })?;

    let target_path = project_root.join(PROJECT_FILE_NAME);
    let temp_path = project_root.join(PROJECT_TEMP_FILE_NAME);
    let payload = serde_json::to_vec_pretty(project)
        .map_err(|error| project_error("invalid_project", "failed to serialize project", error))?;
    let mut file = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open(&temp_path)
        .map_err(|error| {
            project_error(
                "invalid_project",
                "failed to create project temp file",
                error,
            )
        })?;
    file.write_all(&payload)
        .and_then(|_| file.write_all(b"\n"))
        .and_then(|_| file.sync_all())
        .map_err(|error| project_error("disk_full", "failed to flush project temp file", error))?;
    drop(file);

    let parsed_back: XpanoProjectV2 =
        serde_json::from_slice(&std::fs::read(&temp_path).map_err(|error| {
            project_error("invalid_project", "failed to read project temp file", error)
        })?)
        .map_err(|error| {
            project_error(
                "invalid_project",
                "failed to parse project temp file",
                error,
            )
        })?;
    parsed_back
        .validate()
        .map_err(|error| ProjectCommandError::new("invalid_project", error))?;

    atomic_replace(&temp_path, &target_path).map_err(|error| {
        project_error("invalid_project", "failed to replace project file", error)
    })?;
    sync_parent_directory(project_root);
    Ok(())
}

pub(crate) fn write_json_value_atomic(
    target_path: &Path,
    value: &serde_json::Value,
) -> Result<(), ProjectCommandError> {
    let parent = target_path.parent().ok_or_else(|| {
        ProjectCommandError::new("invalid_project", "JSON artifact has no parent directory")
    })?;
    std::fs::create_dir_all(parent).map_err(|error| {
        project_error(
            "invalid_project",
            "failed to create JSON artifact directory",
            error,
        )
    })?;
    let temp_path = target_path.with_extension("json.tmp");
    let payload = serde_json::to_vec_pretty(value).map_err(|error| {
        project_error(
            "invalid_project",
            "failed to serialize JSON artifact",
            error,
        )
    })?;
    let mut file = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open(&temp_path)
        .map_err(|error| {
            project_error(
                "invalid_project",
                "failed to create JSON artifact temp file",
                error,
            )
        })?;
    file.write_all(&payload)
        .and_then(|_| file.write_all(b"\n"))
        .and_then(|_| file.sync_all())
        .map_err(|error| {
            project_error(
                "disk_full",
                "failed to flush JSON artifact temp file",
                error,
            )
        })?;
    drop(file);
    let parsed: serde_json::Value =
        serde_json::from_slice(&std::fs::read(&temp_path).map_err(|error| {
            project_error(
                "invalid_project",
                "failed to read JSON artifact temp file",
                error,
            )
        })?)
        .map_err(|error| {
            project_error(
                "invalid_project",
                "failed to parse JSON artifact temp file",
                error,
            )
        })?;
    if &parsed != value {
        return Err(ProjectCommandError::new(
            "artifact_corrupt",
            "JSON artifact changed during serialization",
        ));
    }
    atomic_replace(&temp_path, target_path).map_err(|error| {
        project_error("invalid_project", "failed to replace JSON artifact", error)
    })?;
    Ok(())
}

#[cfg(windows)]
pub(crate) fn atomic_replace(temp_path: &Path, target_path: &Path) -> std::io::Result<()> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{
        MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
    };

    let mut temp_wide: Vec<u16> = temp_path.as_os_str().encode_wide().collect();
    temp_wide.push(0);
    let mut target_wide: Vec<u16> = target_path.as_os_str().encode_wide().collect();
    target_wide.push(0);
    let result = unsafe {
        MoveFileExW(
            temp_wide.as_ptr(),
            target_wide.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    };
    if result == 0 {
        Err(std::io::Error::last_os_error())
    } else {
        Ok(())
    }
}

#[cfg(not(windows))]
pub(crate) fn atomic_replace(temp_path: &Path, target_path: &Path) -> std::io::Result<()> {
    std::fs::rename(temp_path, target_path)
}

#[cfg(not(windows))]
fn sync_parent_directory(project_root: &Path) {
    if let Ok(directory) = File::open(project_root) {
        let _ = directory.sync_all();
    }
}

#[cfg(windows)]
fn sync_parent_directory(_project_root: &Path) {}

pub(crate) fn find_project_root(start: &Path) -> Option<PathBuf> {
    let mut current = if start.is_file() {
        start.parent()?.to_path_buf()
    } else {
        start.to_path_buf()
    };
    for _ in 0..=6 {
        if current.join(PROJECT_FILE_NAME).is_file() {
            return current.canonicalize().ok().or(Some(current));
        }
        if !current.pop() {
            break;
        }
    }
    None
}

fn validate_project_files(
    project_root: &Path,
    project: &XpanoProjectV2,
) -> ProjectValidationReport {
    let missing_source_track_ids = project
        .tracks
        .iter()
        .filter(|track| !Path::new(&track.source_path).exists())
        .map(|track| track.id.clone())
        .collect();

    let mut artifact_paths = Vec::new();
    if let Some(path) = &project.reconstruction.project_path {
        artifact_paths.push(path.clone());
    }
    if let Some(path) = &project.reconstruction.colmap_path {
        artifact_paths.push(path.clone());
    }
    artifact_paths.extend(
        project
            .geometry
            .variants
            .iter()
            .filter(|variant| variant.point_count > 0)
            .map(|variant| variant.canonical_path.clone()),
    );
    for track in &project.tracks {
        for item in &track.items {
            artifact_paths.extend(
                [
                    item.left.clone(),
                    item.right.clone(),
                    item.thumbnail_left.clone(),
                    item.thumbnail_right.clone(),
                    item.image.clone(),
                    item.thumbnail.clone(),
                ]
                .into_iter()
                .flatten(),
            );
        }
    }
    artifact_paths.sort();
    artifact_paths.dedup();
    let missing_artifact_paths = artifact_paths
        .into_iter()
        .filter(|path| !project_root.join(path).exists())
        .collect();

    ProjectValidationReport {
        missing_source_track_ids,
        missing_artifact_paths,
    }
}

pub fn open_project_from_path(
    path: &Path,
) -> Result<Option<ProjectOpenResult>, ProjectCommandError> {
    let Some(project_root) = find_project_root(path) else {
        return Ok(None);
    };
    let text = std::fs::read_to_string(project_root.join(PROJECT_FILE_NAME)).map_err(|error| {
        project_error("invalid_project", "failed to read xpano_project.json", error)
    })?;
    let (project, migrated) = parse_project(&text)?;
    if migrated {
        write_project_atomic(&project_root, &project)?;
    }
    let validation = validate_project_files(&project_root, &project);
    Ok(Some(ProjectOpenResult {
        project_root,
        project,
        validation,
    }))
}

pub fn rename_project_impl(
    project_root: &Path,
    expected_revision: u64,
    name: &str,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    let trimmed = name.trim();
    if trimmed.is_empty() {
        return Err(ProjectCommandError::new(
            "invalid_project",
            "project name must not be empty",
        ));
    }
    let mut project = read_project(project_root)?;
    if project.revision != expected_revision {
        return Err(ProjectCommandError::revision_conflict(
            expected_revision,
            project.revision,
        ));
    }
    project.name = trimmed.to_string();
    project.revision += 1;
    touch_project(&mut project);
    write_project_atomic(project_root, &project)?;
    Ok(project)
}

pub fn set_active_workspace_impl(
    project_root: &Path,
    expected_revision: u64,
    workspace: ProjectWorkspace,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    let mut project = read_project(project_root)?;
    if project.revision != expected_revision {
        return Err(ProjectCommandError::revision_conflict(
            expected_revision,
            project.revision,
        ));
    }
    if project.active_workspace != workspace {
        project.active_workspace = workspace;
        project.revision += 1;
        touch_project(&mut project);
        write_project_atomic(project_root, &project)?;
    }
    Ok(project)
}

#[tauri::command]
pub fn create_project(
    name: String,
    first_source: String,
    optional_root: Option<String>,
) -> Result<ProjectOpenResult, ProjectCommandError> {
    create_project_impl(
        &name,
        Path::new(&first_source),
        optional_root.as_deref().map(Path::new),
    )
}

#[tauri::command]
pub fn open_or_create_project(
    name: String,
    first_source: String,
    optional_root: Option<String>,
) -> Result<ProjectOpenResult, ProjectCommandError> {
    open_or_create_project_impl(
        &name,
        Path::new(&first_source),
        optional_root.as_deref().map(Path::new),
    )
}

#[tauri::command]
pub fn open_project(path: String) -> Result<Option<ProjectOpenResult>, ProjectCommandError> {
    open_project_from_path(Path::new(&path))
}

#[tauri::command]
pub fn rename_project(
    project_root: String,
    expected_revision: u64,
    name: String,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    rename_project_impl(Path::new(&project_root), expected_revision, &name)
}

#[tauri::command]
pub fn set_project_workspace(
    project_root: String,
    expected_revision: u64,
    workspace: ProjectWorkspace,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    set_active_workspace_impl(Path::new(&project_root), expected_revision, workspace)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::contracts::XpanoProjectV2;
    use std::path::PathBuf;

    fn temp_case(name: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!(
            "xpano-project-v2-{}-{}-{}",
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

    #[test]
    fn opens_and_persists_legacy_seconds_per_frame_as_frames_per_second() {
        let root = temp_case("fps-migration");
        let mut legacy: serde_json::Value = serde_json::from_str(include_str!(
            "../../../schemas/fixtures/xpano_project_v3.example.json"
        ))
        .unwrap();
        legacy["schemaVersion"] = serde_json::json!(2);
        let extraction = legacy["tracks"][0]["extraction"].as_object_mut().unwrap();
        let fps = extraction.remove("framesPerSecond").unwrap().as_f64().unwrap();
        extraction.insert("secondsPerFrame".to_string(), serde_json::json!(1.0 / fps));
        std::fs::write(
            root.join(PROJECT_FILE_NAME),
            serde_json::to_vec_pretty(&legacy).unwrap(),
        )
        .unwrap();

        let opened = open_project_from_path(&root).unwrap().unwrap();

        assert_eq!(opened.project.schema_version, PROJECT_SCHEMA_VERSION);
        assert_eq!(opened.project.tracks[0].extraction.frames_per_second, 1.0);
        let persisted: serde_json::Value = serde_json::from_slice(
            &std::fs::read(root.join(PROJECT_FILE_NAME)).unwrap(),
        )
        .unwrap();
        assert_eq!(persisted["schemaVersion"], PROJECT_SCHEMA_VERSION);
        assert_eq!(persisted["tracks"][0]["extraction"]["framesPerSecond"], 1.0);
        assert!(persisted["tracks"][0]["extraction"].get("secondsPerFrame").is_none());
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn project_parser_accepts_utf8_bom() {
        let text = format!(
            "\u{feff}{}",
            include_str!("../../../schemas/fixtures/xpano_project_v3.example.json")
        );
        let (project, migrated) = parse_project(&text).unwrap();
        assert_eq!(project.schema_version, PROJECT_SCHEMA_VERSION);
        assert!(!migrated);
    }

    #[test]
    fn atomically_writes_and_opens_project_from_nested_path() {
        let root = temp_case("atomic-open");
        let nested = root.join("colmap").join("sparse").join("0");
        std::fs::create_dir_all(&nested).unwrap();
        let project = fixture_project();

        write_project_atomic(&root, &project).unwrap();
        let opened = open_project_from_path(&nested).unwrap().unwrap();

        assert_eq!(opened.project.project_id, project.project_id);
        assert_eq!(opened.project_root, root.canonicalize().unwrap());
        assert!(!root.join("xpano_project.json.tmp").exists());
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn rename_increments_revision_and_rejects_stale_writer() {
        let root = temp_case("revision");
        let project = fixture_project();
        write_project_atomic(&root, &project).unwrap();

        let renamed = rename_project_impl(&root, project.revision, "Renamed project").unwrap();
        assert_eq!(renamed.name, "Renamed project");
        assert_eq!(renamed.revision, project.revision + 1);
        assert_ne!(renamed.updated_at, project.updated_at);

        let error = rename_project_impl(&root, project.revision, "Stale writer").unwrap_err();
        assert_eq!(error.code, "revision_conflict");
        let persisted = read_project(&root).unwrap();
        assert_eq!(persisted.name, "Renamed project");
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn creates_a_valid_empty_project_next_to_the_first_source() {
        let source_root = temp_case("create");
        let source = source_root.join("capture.osv");
        std::fs::write(&source, b"source").unwrap();

        let created = create_project_impl("  Capture project  ", &source, None).unwrap();

        assert_eq!(
            created.project_root,
            source_root.join("xPano").canonicalize().unwrap()
        );
        assert_eq!(created.project.name, "Capture project");
        assert_eq!(created.project.revision, 0);
        assert_eq!(created.project.active_workspace, ProjectWorkspace::Media);
        assert_eq!(created.project.geometry.active_variant_id, "standard");
        assert_eq!(created.project.geometry.variants.len(), 1);
        assert_eq!(created.project.geometry.variants[0].point_count, 0);
        assert!(created.validation.missing_artifact_paths.is_empty());
        assert_eq!(
            read_project(&created.project_root).unwrap(),
            created.project
        );
        let _ = std::fs::remove_dir_all(source_root);
    }

    #[test]
    fn create_project_rejects_an_existing_project_without_overwriting_it() {
        let source_root = temp_case("create-existing");
        let source = source_root.join("capture.osv");
        std::fs::write(&source, b"source").unwrap();
        let first = create_project_impl("First", &source, None).unwrap();
        let before = std::fs::read(first.project_root.join(PROJECT_FILE_NAME)).unwrap();

        let error = create_project_impl("Second", &source, None).unwrap_err();

        assert_eq!(error.code, "project_exists");
        assert_eq!(
            std::fs::read(first.project_root.join(PROJECT_FILE_NAME)).unwrap(),
            before
        );
        let _ = std::fs::remove_dir_all(source_root);
    }

    #[test]
    fn open_or_create_project_reuses_an_existing_project_without_mutating_it() {
        let source_root = temp_case("open-or-create-existing");
        let photos = source_root.join("photos");
        std::fs::create_dir_all(&photos).unwrap();
        std::fs::write(photos.join("capture.jpg"), b"source").unwrap();
        let first = create_project_impl("First", &photos, None).unwrap();
        let before = std::fs::read(first.project_root.join(PROJECT_FILE_NAME)).unwrap();

        let opened = open_or_create_project_impl("Second", &photos, None).unwrap();

        assert_eq!(opened.project_root, first.project_root);
        assert_eq!(opened.project.project_id, first.project.project_id);
        assert_eq!(opened.project.name, "First");
        assert_eq!(
            std::fs::read(first.project_root.join(PROJECT_FILE_NAME)).unwrap(),
            before
        );
        let _ = std::fs::remove_dir_all(source_root);
    }

    #[test]
    fn open_or_create_project_creates_a_project_when_none_exists() {
        let source_root = temp_case("open-or-create-new");
        let source = source_root.join("capture.osv");
        std::fs::write(&source, b"source").unwrap();

        let created = open_or_create_project_impl("Capture", &source, None).unwrap();

        assert_eq!(created.project.name, "Capture");
        assert!(created.project_root.join(PROJECT_FILE_NAME).is_file());
        let _ = std::fs::remove_dir_all(source_root);
    }

    #[test]
    fn changing_workspace_updates_revision_and_timestamp_once() {
        let root = temp_case("workspace-revision");
        let project = fixture_project();
        write_project_atomic(&root, &project).unwrap();

        let changed =
            set_active_workspace_impl(&root, project.revision, ProjectWorkspace::Results).unwrap();

        assert_eq!(changed.active_workspace, ProjectWorkspace::Results);
        assert_eq!(changed.revision, project.revision + 1);
        assert_ne!(changed.updated_at, project.updated_at);

        let unchanged =
            set_active_workspace_impl(&root, changed.revision, ProjectWorkspace::Results).unwrap();
        assert_eq!(unchanged.revision, changed.revision);
        assert_eq!(unchanged.updated_at, changed.updated_at);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn opening_project_reports_missing_sources_without_mutating_file() {
        let root = temp_case("validation");
        let project = fixture_project();
        write_project_atomic(&root, &project).unwrap();
        let before = std::fs::read(root.join(PROJECT_FILE_NAME)).unwrap();

        let opened = open_project_from_path(&root).unwrap().unwrap();

        assert_eq!(
            opened.validation.missing_source_track_ids,
            vec!["track-pano-001"]
        );
        assert!(!opened.validation.missing_artifact_paths.is_empty());
        assert_eq!(std::fs::read(root.join(PROJECT_FILE_NAME)).unwrap(), before);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn non_project_path_returns_none() {
        let root = temp_case("none");
        assert!(open_project_from_path(&root).unwrap().is_none());
        let _ = std::fs::remove_dir_all(root);
    }
}
