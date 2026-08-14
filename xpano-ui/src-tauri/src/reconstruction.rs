use crate::contracts::{
    ExecutionPlan, ExecutionPlanNode, ProgressMode, ProjectTrackStatus,
    ProjectWorkspace, ReconstructionBackend, ReconstructionStatus, XpanoProjectV2,
    EXECUTION_PLAN_SCHEMA_VERSION,
};
use crate::project::{
    read_project, touch_project, write_json_value_atomic, write_project_atomic,
    ProjectCommandError,
};
use chrono::{SecondsFormat, Utc};
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::ffi::OsString;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::Command;
#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;
use tauri::Emitter;
use uuid::Uuid;

const ACTIVE_RECONSTRUCTION_PLAN_RELATIVE_PATH: &str = "work/plans/reconstruction_active.json";

#[derive(Clone, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReconstructionPlanConfig {
    pub backend: ReconstructionBackend,
    pub alignment_mode: Option<String>,
    #[serde(default)]
    pub metashape_path: Option<String>,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
enum ReconstructionOperation {
    #[default]
    Align,
    Reexport,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct StoredExecutionPlan {
    schema_version: u32,
    #[serde(default)]
    operation: ReconstructionOperation,
    config: ReconstructionPlanConfig,
    plan: ExecutionPlan,
}

fn active_reconstruction_plan_path(project_root: &Path) -> PathBuf {
    project_root.join(ACTIVE_RECONSTRUCTION_PLAN_RELATIVE_PATH)
}

fn read_stored_execution_plan(
    project_root: &Path,
) -> Result<StoredExecutionPlan, ProjectCommandError> {
    let path = active_reconstruction_plan_path(project_root);
    let payload = std::fs::read(&path).map_err(|error| {
        ProjectCommandError::new(
            "invalid_project",
            format!("execution plan is not available: {}", error),
        )
    })?;
    let stored: StoredExecutionPlan = serde_json::from_slice(&payload).map_err(|error| {
        ProjectCommandError::new(
            "artifact_corrupt",
            format!("failed to parse stored execution plan: {}", error),
        )
    })?;
    if stored.schema_version != 1 {
        return Err(ProjectCommandError::new(
            "artifact_corrupt",
            "unsupported stored execution plan version",
        ));
    }
    stored
        .plan
        .validate()
        .map_err(|error| ProjectCommandError::new("artifact_corrupt", error))?;
    Ok(stored)
}

pub(crate) fn active_execution_plan_impl(
    project_root: &Path,
) -> Result<ExecutionPlan, ProjectCommandError> {
    Ok(read_stored_execution_plan(project_root)?.plan)
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BackendProbe {
    pub backend: ReconstructionBackend,
    pub available: bool,
    pub path: String,
    pub cuda_available: Option<bool>,
    pub detail: String,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ComponentInspectionItem {
    pub component_key: String,
    #[serde(default)]
    pub label: String,
    pub aligned_camera_count: u64,
    #[serde(default)]
    pub total_camera_count: u64,
    #[serde(default)]
    pub tie_point_count: u64,
    #[serde(default)]
    pub is_initially_active: bool,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ComponentInspection {
    pub schema_version: u32,
    pub inventory_complete: bool,
    pub total_cameras: u64,
    pub aligned_cameras: u64,
    pub unaligned_cameras: u64,
    pub default_component_key: String,
    pub components: Vec<ComponentInspectionItem>,
    #[serde(default)]
    pub warnings: Vec<String>,
}

fn parse_component_inspection(payload: &[u8]) -> Result<ComponentInspection, ProjectCommandError> {
    let inspection: ComponentInspection = serde_json::from_slice(payload).map_err(|error| {
        ProjectCommandError::new(
            "artifact_corrupt",
            format!("failed to parse Metashape Component inventory: {error}"),
        )
    })?;
    if inspection.schema_version != 2
        || inspection.total_cameras == 0
        || inspection.aligned_cameras == 0
        || inspection.aligned_cameras > inspection.total_cameras
        || inspection.unaligned_cameras != inspection.total_cameras - inspection.aligned_cameras
        || inspection.components.is_empty()
        || inspection.default_component_key.trim().is_empty()
    {
        return Err(ProjectCommandError::new(
            "artifact_corrupt",
            "Metashape Component inventory has inconsistent totals",
        ));
    }

    let mut keys = HashSet::new();
    let mut default_is_usable = false;
    for component in &inspection.components {
        if component.component_key.trim().is_empty()
            || !keys.insert(component.component_key.as_str())
            || component.aligned_camera_count > inspection.total_cameras
            || component.total_camera_count > 0
                && component.aligned_camera_count > component.total_camera_count
        {
            return Err(ProjectCommandError::new(
                "artifact_corrupt",
                "Metashape Component inventory contains an invalid Component",
            ));
        }
        if component.component_key == inspection.default_component_key
            && component.aligned_camera_count > 0
        {
            default_is_usable = true;
        }
    }
    if !default_is_usable {
        return Err(ProjectCommandError::new(
            "artifact_corrupt",
            "Metashape Component inventory has no usable default Component",
        ));
    }
    Ok(inspection)
}

fn component_inspection_args(script: &Path, project: &Path, output: &Path) -> Vec<OsString> {
    vec![
        OsString::from("-r"),
        script.as_os_str().to_owned(),
        OsString::from("--project"),
        project.as_os_str().to_owned(),
        OsString::from("--output"),
        output.as_os_str().to_owned(),
    ]
}

fn normalize_executable_path(path: &str) -> &str {
    let trimmed = path.trim();
    trimmed
        .strip_prefix('"')
        .and_then(|value| value.strip_suffix('"'))
        .map(str::trim)
        .unwrap_or(trimmed)
}

fn command_available(command: &str) -> bool {
    let path = Path::new(command);
    if path.is_file() {
        return true;
    }
    if path.parent().is_some_and(|parent| !parent.as_os_str().is_empty()) {
        return false;
    }
    let locator = if cfg!(windows) { "where.exe" } else { "which" };
    Command::new(locator)
        .arg(command)
        .output()
        .is_ok_and(|output| output.status.success())
}

fn command_help(command: &str) -> String {
    let path = Path::new(command);
    let output = if cfg!(windows)
        && path
            .extension()
            .and_then(|value| value.to_str())
            .is_some_and(|value| value.eq_ignore_ascii_case("bat"))
    {
        Command::new("cmd.exe")
            .args(["/C", command, "-h"])
            .output()
    } else {
        Command::new(command).arg("-h").output()
    };
    output
        .map(|output| {
            format!(
                "{}\n{}",
                String::from_utf8_lossy(&output.stdout),
                String::from_utf8_lossy(&output.stderr)
            )
        })
        .unwrap_or_default()
}

pub fn probe_reconstruction_backends_impl(explicit_metashape_path: Option<&str>) -> Vec<BackendProbe> {
    let explicit_metashape_path = explicit_metashape_path
        .map(normalize_executable_path)
        .filter(|path| !path.is_empty());
    let metashape_path = explicit_metashape_path
        .map(str::to_string)
        .unwrap_or_else(crate::detect_metashape);
    let colmap_path = crate::detect_colmap();
    let metashape_available = command_available(&metashape_path);
    let colmap_available = command_available(&colmap_path);
    let colmap_help = colmap_available.then(|| command_help(&colmap_path));
    let cuda_available = colmap_help.as_ref().map(|text| {
        let normalized = text.to_ascii_lowercase();
        normalized.contains("cuda")
            && !normalized.contains("without cuda")
            && !normalized.contains("no cuda")
    });
    vec![
        BackendProbe {
            backend: ReconstructionBackend::Metashape,
            available: metashape_available,
            path: metashape_path,
            cuda_available: None,
            detail: if metashape_available {
                if explicit_metashape_path.is_some() {
                    "User-selected Metashape executable found; license is checked when the job starts".to_string()
                } else {
                    "Metashape executable found; license is checked when the job starts".to_string()
                }
            } else {
                if explicit_metashape_path.is_some() {
                    "The user-selected Metashape executable was not found".to_string()
                } else {
                    "Metashape executable was not found".to_string()
                }
            },
        },
        BackendProbe {
            backend: ReconstructionBackend::Colmap,
            available: colmap_available,
            path: colmap_path,
            cuda_available,
            detail: if colmap_available {
                "Bundled or system COLMAP executable found".to_string()
            } else {
                "COLMAP executable was not found".to_string()
            },
        },
    ]
}

fn validate_reconstruction_config(config: &serde_json::Value) -> Result<(), ProjectCommandError> {
    let object = config.as_object().ok_or_else(|| {
        ProjectCommandError::new("invalid_project", "reconstruction config must be an object")
    })?;
    if let Some(mode) = object.get("alignmentMode").and_then(serde_json::Value::as_str) {
        if !matches!(mode, "backbone" | "mixed") {
            return Err(ProjectCommandError::new(
                "invalid_project",
                "Metashape alignment mode must be backbone or mixed",
            ));
        }
    }
    if object
        .get("metashapePath")
        .is_some_and(|value| !value.is_string())
    {
        return Err(ProjectCommandError::new(
            "invalid_project",
            "Metashape executable path must be a string",
        ));
    }
    for key in ["metashapeKeypointLimit", "metashapeTiepointLimit"] {
        if object
            .get(key)
            .and_then(serde_json::Value::as_i64)
            .is_some_and(|value| value < 0)
        {
            return Err(ProjectCommandError::new(
                "invalid_project",
                format!("{} must be zero or greater", key),
            ));
        }
    }
    for key in ["colmapMaxImageSize", "colmapMaxNumFeatures"] {
        if object
            .get(key)
            .and_then(serde_json::Value::as_i64)
            .is_some_and(|value| value <= 0)
        {
            return Err(ProjectCommandError::new(
                "invalid_project",
                format!("{} must be greater than zero", key),
            ));
        }
    }
    Ok(())
}

pub fn update_reconstruction_config_impl(
    project_root: &Path,
    expected_revision: u64,
    backend: ReconstructionBackend,
    mut config: serde_json::Value,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    validate_reconstruction_config(&config)?;
    let mut project = read_project(project_root)?;
    if project.revision != expected_revision {
        return Err(ProjectCommandError::revision_conflict(
            expected_revision,
            project.revision,
        ));
    }
    if project.reconstruction.status == crate::contracts::ReconstructionStatus::Running {
        return Err(ProjectCommandError::new(
            "job_conflict",
            "reconstruction config cannot change while a job is running",
        ));
    }
    let old_config = project.reconstruction.config.as_object();
    let new_config = config.as_object_mut().unwrap();
    for key in ["mediaManifestPath", "alignmentManifestPath"] {
        if !new_config.contains_key(key) {
            if let Some(value) = old_config.and_then(|object| object.get(key)) {
                new_config.insert(key.to_string(), value.clone());
            }
        }
    }
    if project.reconstruction.backend == backend && project.reconstruction.config == config {
        return Ok(project);
    }
    project.revision += 1;
    project.reconstruction.backend = backend;
    project.reconstruction.config = config;
    project.reconstruction.status = if project
        .reconstruction
        .config
        .get("alignmentManifestPath")
        .and_then(serde_json::Value::as_str)
        .is_some()
    {
        crate::contracts::ReconstructionStatus::Stale
    } else {
        crate::contracts::ReconstructionStatus::Idle
    };
    touch_project(&mut project);
    write_project_atomic(project_root, &project)?;
    Ok(project)
}

pub(crate) fn begin_reconstruction_job_impl(
    project_root: &Path,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    let mut project = read_project(project_root)?;
    if project.reconstruction.status == ReconstructionStatus::Running {
        return Err(ProjectCommandError::new(
            "job_conflict",
            "a reconstruction job is already running",
        ));
    }
    project.reconstruction.status = ReconstructionStatus::Running;
    project.reconstruction.input_revision = project.revisions.alignment_input;
    project.active_workspace = ProjectWorkspace::Reconstruction;
    project.revision += 1;
    touch_project(&mut project);
    write_project_atomic(project_root, &project)?;
    Ok(project)
}

pub(crate) fn begin_reconstruction_job_from_plan_impl(
    project_root: &Path,
    expected_revision: u64,
    plan_id: &str,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    let mut project = read_project(project_root)?;
    if project.revision != expected_revision {
        return Err(ProjectCommandError::revision_conflict(
            expected_revision,
            project.revision,
        ));
    }
    let stored = read_stored_execution_plan(project_root)?;
    if stored.plan.plan_id != plan_id || stored.plan.project_id != project.project_id {
        return Err(ProjectCommandError::new(
            "invalid_project",
            "execution plan does not match the active project",
        ));
    }
    if stored.plan.input_revision != project.revisions.alignment_input {
        return Err(ProjectCommandError::revision_conflict(
            stored.plan.input_revision,
            project.revisions.alignment_input,
        ));
    }
    let active_mode = project
        .reconstruction
        .config
        .get("alignmentMode")
        .and_then(serde_json::Value::as_str);
    let configured_metashape_path = project
        .reconstruction
        .config
        .get("metashapePath")
        .and_then(serde_json::Value::as_str)
        .map(normalize_executable_path)
        .filter(|path| !path.is_empty());
    let planned_metashape_path = stored
        .config
        .metashape_path
        .as_deref()
        .map(normalize_executable_path)
        .filter(|path| !path.is_empty());
    let detected_metashape_path = (planned_metashape_path.is_some()
        && configured_metashape_path.is_none()
        && project.reconstruction.backend == ReconstructionBackend::Metashape)
        .then(crate::detect_metashape);
    let active_metashape_path = configured_metashape_path
        .or_else(|| detected_metashape_path.as_deref());
    // WARN: A plan must launch the same Metashape executable it validated.
    if project.reconstruction.backend != stored.config.backend
        || active_mode != stored.config.alignment_mode.as_deref()
        || planned_metashape_path.is_some() && active_metashape_path != planned_metashape_path
    {
        return Err(ProjectCommandError::new(
            "revision_conflict",
            "reconstruction configuration changed after the execution plan was built",
        ));
    }
    if stored.operation == ReconstructionOperation::Reexport {
        validate_reexport_project(project_root, &project)?;
    }
    if project.reconstruction.status == ReconstructionStatus::Running {
        let active_plan_id = project
            .reconstruction
            .config
            .get("activePlanId")
            .and_then(serde_json::Value::as_str);
        if active_plan_id == Some(plan_id)
            && project.reconstruction.input_revision == stored.plan.input_revision
        {
            return Ok(project);
        }
        return Err(ProjectCommandError::new(
            "job_conflict",
            "a different reconstruction job is already running",
        ));
    }
    let config = project
        .reconstruction
        .config
        .as_object_mut()
        .ok_or_else(|| {
            ProjectCommandError::new(
                "invalid_project",
                "reconstruction config must be an object",
            )
        })?;
    config.insert(
        "activePlanId".to_string(),
        serde_json::Value::String(plan_id.to_string()),
    );
    project.reconstruction.status = ReconstructionStatus::Running;
    project.reconstruction.input_revision = stored.plan.input_revision;
    project.active_workspace = ProjectWorkspace::Reconstruction;
    project.revision += 1;
    touch_project(&mut project);
    write_project_atomic(project_root, &project)?;
    Ok(project)
}

fn require_non_empty_artifact(path: &Path, label: &str) -> Result<(), ProjectCommandError> {
    let metadata = std::fs::metadata(path).map_err(|error| {
        ProjectCommandError::new(
            "artifact_corrupt",
            format!("missing reconstruction artifact {}: {}", label, error),
        )
    })?;
    if !metadata.is_file() || metadata.len() == 0 {
        return Err(ProjectCommandError::new(
            "artifact_corrupt",
            format!("reconstruction artifact {} is empty", label),
        ));
    }
    Ok(())
}

fn require_colmap_records(path: &Path, label: &str) -> Result<u64, ProjectCommandError> {
    let mut file = std::fs::File::open(path).map_err(|error| {
        ProjectCommandError::new(
            "artifact_corrupt",
            format!("missing reconstruction artifact {}: {}", label, error),
        )
    })?;
    let mut count_bytes = [0u8; 8];
    file.read_exact(&mut count_bytes).map_err(|error| {
        ProjectCommandError::new(
            "artifact_corrupt",
            format!("failed to read COLMAP record count from {}: {}", label, error),
        )
    })?;
    let count = u64::from_le_bytes(count_bytes);
    if count == 0 {
        return Err(ProjectCommandError::new(
            "artifact_corrupt",
            format!("COLMAP artifact {} contains no records", label),
        ));
    }
    Ok(count)
}

fn validated_colmap_root(project_root: &Path) -> Result<&'static str, ProjectCommandError> {
    for (relative, sparse) in [("colmap", "colmap/sparse/0"), (".", "sparse/0")] {
        let sparse_root = project_root.join(sparse);
        let files = ["cameras.bin", "images.bin", "points3D.bin"];
        if files.iter().all(|name| sparse_root.join(name).is_file()) {
            for name in files {
                require_colmap_records(&sparse_root.join(name), name)?;
            }
            return Ok(relative);
        }
    }
    Err(ProjectCommandError::new(
        "artifact_corrupt",
        "reconstruction completed without a readable COLMAP sparse/0 model",
    ))
}

fn validate_legacy_result_manifest(
    project_root: &Path,
    project: &XpanoProjectV2,
) -> Result<(), ProjectCommandError> {
    let active_manifest = project
        .reconstruction
        .config
        .get("alignmentManifestPath")
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| {
            ProjectCommandError::new(
                "artifact_corrupt",
                "cannot recover reconstruction without an active alignment manifest",
            )
        })?;
    let summary: serde_json::Value = serde_json::from_slice(
        &std::fs::read(project_root.join("xpano_run_summary.json")).map_err(|error| {
            ProjectCommandError::new(
                "artifact_corrupt",
                format!("cannot recover reconstruction without xpano_run_summary.json: {}", error),
            )
        })?,
    )
    .map_err(|error| {
        ProjectCommandError::new(
            "artifact_corrupt",
            format!("failed to parse xpano_run_summary.json: {}", error),
        )
    })?;
    let completed_manifest = summary
        .get("manifest")
        .and_then(serde_json::Value::as_str)
        .unwrap_or_default()
        .replace('\\', "/")
        .to_ascii_lowercase();
    let expected = active_manifest.replace('\\', "/").to_ascii_lowercase();
    if completed_manifest.is_empty() || !completed_manifest.ends_with(&expected) {
        return Err(ProjectCommandError::new(
            "revision_conflict",
            "existing reconstruction artifacts were produced from a different alignment manifest",
        ));
    }
    Ok(())
}

fn validated_alignment_report(project_root: &Path) -> Result<serde_json::Value, ProjectCommandError> {
    let path = project_root.join("xpano_alignment_report.json");
    let report: serde_json::Value = serde_json::from_slice(&std::fs::read(&path).map_err(|error| {
        ProjectCommandError::new(
            "artifact_corrupt",
            format!("missing Metashape alignment report: {}", error),
        )
    })?)
    .map_err(|error| {
        ProjectCommandError::new(
            "artifact_corrupt",
            format!("failed to parse Metashape alignment report: {}", error),
        )
    })?;
    let succeeded = report
        .get("processSucceeded")
        .and_then(serde_json::Value::as_bool)
        .unwrap_or(false);
    let state = report
        .get("state")
        .and_then(serde_json::Value::as_str)
        .unwrap_or_default();
    let aligned = report
        .get("alignedCameras")
        .and_then(serde_json::Value::as_u64)
        .unwrap_or(0);
    let total = report
        .get("totalCameras")
        .and_then(serde_json::Value::as_u64)
        .unwrap_or(0);
    let selected_component = report
        .get("selectedComponentKey")
        .and_then(serde_json::Value::as_str)
        .unwrap_or_default();
    let selected_component_is_valid = report
        .get("components")
        .and_then(serde_json::Value::as_array)
        .is_some_and(|components| {
            components.iter().any(|component| {
                component.get("componentKey").and_then(serde_json::Value::as_str)
                    == Some(selected_component)
                    && component
                        .get("alignedCameraCount")
                        .and_then(serde_json::Value::as_u64)
                        .unwrap_or(0)
                        > 0
            })
        });
    let schema_version = match report.get("schemaVersion") {
        None => 1,
        Some(value) => value.as_u64().unwrap_or(0),
    };
    let schema_v2_inventory_is_valid = if schema_version == 2 {
        let unaligned = report
            .get("unalignedCameras")
            .and_then(serde_json::Value::as_u64);
        let selected_aligned = report
            .get("selectedComponentAlignedCameras")
            .and_then(serde_json::Value::as_u64);
        let components = report
            .get("components")
            .and_then(serde_json::Value::as_array);
        let mut keys = HashSet::new();
        let mut selected_count = None;
        let components_are_valid = components.is_some_and(|components| {
            !components.is_empty()
                && components.iter().all(|component| {
                    let Some(key) = component
                        .get("componentKey")
                        .and_then(serde_json::Value::as_str)
                        .filter(|key| !key.trim().is_empty())
                    else {
                        return false;
                    };
                    let Some(component_aligned) = component
                        .get("alignedCameraCount")
                        .and_then(serde_json::Value::as_u64)
                    else {
                        return false;
                    };
                    let component_total = component
                        .get("totalCameraCount")
                        .and_then(serde_json::Value::as_u64)
                        .unwrap_or(component_aligned);
                    if key == selected_component {
                        selected_count = Some(component_aligned);
                    }
                    keys.insert(key) && component_aligned <= component_total && component_aligned <= total
                })
        });
        report
            .get("inventoryComplete")
            .and_then(serde_json::Value::as_bool)
            .is_some()
            && unaligned == total.checked_sub(aligned)
            && components_are_valid
            && selected_count == selected_aligned
            && selected_aligned.is_some_and(|count| count > 0)
    } else {
        schema_version == 1
    };
    if !succeeded
        || state != "complete"
        || aligned == 0
        || total < aligned
        || selected_component.is_empty()
        || !selected_component_is_valid
        || !schema_v2_inventory_is_valid
    {
        return Err(ProjectCommandError::new(
            "artifact_corrupt",
            "Metashape did not produce a successful alignment/export report",
        ));
    }
    Ok(report)
}

pub(crate) fn finalize_reconstruction_job_impl(
    project_root: &Path,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    let mut project = read_project(project_root)?;
    if matches!(
        project.reconstruction.status,
        ReconstructionStatus::Failed | ReconstructionStatus::Interrupted
    ) {
        return Err(ProjectCommandError::new(
            "job_conflict",
            "a failed or interrupted alignment cannot be finalized without an explicit PSX re-export",
        ));
    }
    let alignment_report = if project.reconstruction.backend == ReconstructionBackend::Metashape {
        Some(validated_alignment_report(project_root)?)
    } else {
        None
    };
    let colmap_path = validated_colmap_root(project_root)?;
    let project_path = if project.reconstruction.backend == ReconstructionBackend::Metashape {
        let relative = "work/xpano.psx";
        require_non_empty_artifact(&project_root.join(relative), "work/xpano.psx")?;
        Some(relative.to_string())
    } else {
        None
    };
    let completed_input_revision = if project.reconstruction.status == ReconstructionStatus::Running {
        if project.reconstruction.input_revision != project.revisions.alignment_input {
            return Err(ProjectCommandError::revision_conflict(
                project.reconstruction.input_revision,
                project.revisions.alignment_input,
            ));
        }
        project.reconstruction.input_revision
    } else {
        validate_legacy_result_manifest(project_root, &project)?;
        project.revisions.alignment_input
    };

    if project.reconstruction.status == ReconstructionStatus::Complete
        && project.reconstruction.input_revision == completed_input_revision
        && project.reconstruction.project_path == project_path
        && project.reconstruction.colmap_path.as_deref() == Some(colmap_path)
    {
        return Ok(project);
    }

    project.reconstruction.status = ReconstructionStatus::Complete;
    project.reconstruction.input_revision = completed_input_revision;
    project.reconstruction.project_path = project_path;
    project.reconstruction.colmap_path = Some(colmap_path.to_string());
    if let Some(report) = alignment_report {
        if let Some(config) = project.reconstruction.config.as_object_mut() {
            if let Some(component_key) = report.get("selectedComponentKey") {
                config.insert("selectedComponentKey".to_string(), component_key.clone());
            }
            config.insert("alignmentReport".to_string(), report);
        }
    }
    project.revisions.alignment += 1;
    project.active_workspace = ProjectWorkspace::Results;
    crate::geometry::reset_geometry_from_reconstruction_impl(project_root, &mut project)?;
    project.revision += 1;
    touch_project(&mut project);
    write_project_atomic(project_root, &project)?;
    Ok(project)
}

fn set_reconstruction_terminal_status(
    project_root: &Path,
    status: ReconstructionStatus,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    let mut project = read_project(project_root)?;
    if project.reconstruction.status == status {
        return Ok(project);
    }
    project.reconstruction.status = status;
    if status == ReconstructionStatus::Failed
        && project.reconstruction.backend == ReconstructionBackend::Metashape
        && project_root.join("work/xpano.psx").is_file()
    {
        project.reconstruction.project_path = Some("work/xpano.psx".to_string());
    }
    if status == ReconstructionStatus::Failed {
        if let Ok(payload) = std::fs::read(project_root.join("xpano_alignment_report.json")) {
            if let Ok(report) = serde_json::from_slice::<serde_json::Value>(&payload) {
                if let Some(config) = project.reconstruction.config.as_object_mut() {
                    if let Some(component_key) = report.get("selectedComponentKey") {
                        config.insert("selectedComponentKey".to_string(), component_key.clone());
                    }
                    config.insert("alignmentReport".to_string(), report);
                }
            }
        }
    }
    project.active_workspace = ProjectWorkspace::Reconstruction;
    project.revision += 1;
    touch_project(&mut project);
    write_project_atomic(project_root, &project)?;
    Ok(project)
}

pub(crate) fn fail_reconstruction_job_impl(
    project_root: &Path,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    set_reconstruction_terminal_status(project_root, ReconstructionStatus::Failed)
}

pub(crate) fn interrupt_reconstruction_job_impl(
    project_root: &Path,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    set_reconstruction_terminal_status(project_root, ReconstructionStatus::Interrupted)
}

fn node(
    stage_id: &str,
    label: &str,
    depends_on: &[&str],
    weight: f64,
    progress_mode: ProgressMode,
    slow_hint: bool,
    skip_reason: Option<&str>,
) -> ExecutionPlanNode {
    ExecutionPlanNode {
        stage_id: stage_id.to_string(),
        label: label.to_string(),
        depends_on: depends_on.iter().map(|value| value.to_string()).collect(),
        weight,
        progress_mode,
        slow_hint,
        skip_reason: skip_reason.map(str::to_string),
        estimated_seconds: None,
    }
}

fn active_track_types(
    project_root: &Path,
    project: &XpanoProjectV2,
) -> Result<(bool, bool), ProjectCommandError> {
    if project.tracks.is_empty() {
        return Err(ProjectCommandError::new(
            "invalid_project",
            "project does not contain media tracks",
        ));
    }
    let unavailable = project.tracks.iter().find(|track| {
        !matches!(
            track.status,
            ProjectTrackStatus::Prepared | ProjectTrackStatus::Ready
        )
    });
    if let Some(track) = unavailable {
        return Err(ProjectCommandError::new(
            "invalid_project",
            format!("media track {} is not prepared", track.label),
        ));
    }

    let manifest_relative = project
        .reconstruction
        .config
        .get("alignmentManifestPath")
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| {
            ProjectCommandError::new(
                "artifact_corrupt",
                "active alignment manifest is missing; prepare media first",
            )
        })?;
    if Path::new(manifest_relative).is_absolute()
        || manifest_relative
            .replace('\\', "/")
            .split('/')
            .any(|part| part == "..")
    {
        return Err(ProjectCommandError::new(
            "artifact_corrupt",
            "active alignment manifest is not project-relative",
        ));
    }
    let manifest_path = project_root.join(manifest_relative);
    let manifest: serde_json::Value = serde_json::from_slice(
        &std::fs::read(&manifest_path).map_err(|error| {
            ProjectCommandError::new(
                "artifact_corrupt",
                format!("failed to read active alignment manifest: {}", error),
            )
        })?,
    )
    .map_err(|error| {
        ProjectCommandError::new(
            "artifact_corrupt",
            format!("failed to parse active alignment manifest: {}", error),
        )
    })?;
    let tracks = manifest
        .get("tracks")
        .and_then(serde_json::Value::as_array)
        .ok_or_else(|| {
            ProjectCommandError::new(
                "artifact_corrupt",
                "active alignment manifest has no track list",
            )
        })?;
    if tracks.is_empty() {
        return Err(ProjectCommandError::new(
            "invalid_project",
            "no selected media items remain for reconstruction",
        ));
    }

    let has_panorama = tracks.iter().any(|track| {
        track.get("track_type").and_then(serde_json::Value::as_str)
            == Some("panorama_video")
    });
    let has_frames = tracks.iter().any(|track| {
        matches!(
            track.get("track_type").and_then(serde_json::Value::as_str),
            Some("ordinary_video" | "standard_photos" | "aerial_photos")
        )
    });
    Ok((has_panorama, has_frames))
}

fn metashape_backbone_nodes(has_panorama: bool, has_frames: bool) -> Vec<ExecutionPlanNode> {
    let no_panorama = (!has_panorama).then_some("No panorama media selected");
    let no_frames = (!has_frames).then_some("No flat-frame media selected");
    vec![
        node("input.validate", "校验输入", &[], 0.02, ProgressMode::Counted, false, None),
        node("metashape.project.create", "创建 Metashape 工程", &["input.validate"], 0.02, ProgressMode::Indeterminate, false, None),
        node("metashape.pano.import", "导入全景双鱼眼与站点", &["metashape.project.create"], 0.07, ProgressMode::Counted, false, no_panorama),
        node("metashape.pano.station", "设置全景站点", &["metashape.pano.import"], 0.02, ProgressMode::Counted, false, no_panorama),
        node("metashape.pano.match", "匹配全景素材", &["metashape.pano.station"], 0.20, ProgressMode::Indeterminate, true, no_panorama),
        node("metashape.pano.align", "求解全景骨架", &["metashape.pano.match"], 0.11, ProgressMode::Indeterminate, true, no_panorama),
        node("metashape.pano.release", "释放全景站点以优化外参", &["metashape.pano.align"], 0.02, ProgressMode::Counted, false, no_panorama),
        node("metashape.pano.optimize", "优化全景骨架", &["metashape.pano.release"], 0.06, ProgressMode::Indeterminate, true, no_panorama),
        node("metashape.frame.import", "导入普通帧与照片", &["metashape.pano.optimize"], 0.06, ProgressMode::Counted, false, no_frames),
        node("metashape.frame.match", "匹配新增普通素材", &["metashape.frame.import"], 0.17, ProgressMode::Indeterminate, true, no_frames),
        node("metashape.frame.align", "增量接入普通相机", &["metashape.frame.match"], 0.08, ProgressMode::Indeterminate, true, no_frames),
        node("metashape.all.optimize", "全局相机优化", &["metashape.frame.align"], 0.05, ProgressMode::Indeterminate, true, no_frames),
        node("metashape.project.save", "保存 Metashape 工程", &["metashape.all.optimize"], 0.01, ProgressMode::Indeterminate, false, None),
        node("metashape.component.select", "检查并选择主 Component", &["metashape.project.save"], 0.01, ProgressMode::Counted, false, None),
        node("coordinate.auto_level", "自动校正地面方向", &["metashape.component.select"], 0.02, ProgressMode::Indeterminate, false, None),
        node("export.images", "导出训练图像", &["coordinate.auto_level"], 0.04, ProgressMode::Counted, false, None),
        node("export.colmap", "写出 COLMAP 模型", &["export.images"], 0.03, ProgressMode::Counted, false, None),
        node("output.validate", "验证输出完整性", &["export.colmap"], 0.01, ProgressMode::Counted, false, None),
    ]
}

fn colmap_panorama_nodes() -> Vec<ExecutionPlanNode> {
    vec![
        node("input.validate", "校验输入与相机模型", &[], 0.04, ProgressMode::Counted, false, None),
        node("colmap.images.prepare", "准备双鱼眼图像与传感器组", &["input.validate"], 0.08, ProgressMode::Counted, false, None),
        node("colmap.features.extract", "提取 SIFT 特征", &["colmap.images.prepare"], 0.18, ProgressMode::ExternalPercent, true, None),
        node("colmap.features.match", "匹配图像特征", &["colmap.features.extract"], 0.20, ProgressMode::ExternalPercent, true, None),
        node("colmap.mapper", "增量重建相机与点云", &["colmap.features.match"], 0.28, ProgressMode::Indeterminate, true, None),
        node("colmap.model.select", "选择并检查最佳模型", &["colmap.mapper"], 0.05, ProgressMode::Counted, false, None),
        node("export.images", "导出训练图像", &["colmap.model.select"], 0.08, ProgressMode::Counted, false, None),
        node("export.colmap", "发布标准 COLMAP 目录", &["export.images"], 0.05, ProgressMode::Counted, false, None),
        node("output.validate", "验证输出完整性", &["export.colmap"], 0.04, ProgressMode::Counted, false, None),
    ]
}

fn metashape_reexport_nodes() -> Vec<ExecutionPlanNode> {
    vec![
        node("export.reuse_project", "读取已保存的 Metashape 工程", &[], 0.05, ProgressMode::Indeterminate, false, None),
        node("metashape.component.validate", "确认导出 Component", &["export.reuse_project"], 0.05, ProgressMode::Counted, false, None),
        node("export.images", "重新导出训练图像", &["metashape.component.validate"], 0.75, ProgressMode::Counted, true, None),
        node("export.colmap", "重新写出 COLMAP 模型", &["export.images"], 0.10, ProgressMode::Counted, false, None),
        node("output.validate", "验证重新导出结果", &["export.colmap"], 0.05, ProgressMode::Counted, false, None),
    ]
}

fn normalize_plan_config(
    mut config: ReconstructionPlanConfig,
) -> Result<ReconstructionPlanConfig, ProjectCommandError> {
    if config.backend == ReconstructionBackend::Metashape
        && config.alignment_mode.as_deref() == Some("mixed")
    {
        config.alignment_mode = Some("backbone".to_string());
    }
    config.metashape_path = config
        .metashape_path
        .as_deref()
        .map(normalize_executable_path)
        .filter(|path| !path.is_empty())
        .map(str::to_string);
    if config.backend == ReconstructionBackend::Metashape
        && config
            .metashape_path
            .as_deref()
            .is_some_and(|path| !command_available(path))
    {
        return Err(ProjectCommandError::new(
            "backend_unavailable",
            "The selected Metashape executable is not available",
        ));
    }
    Ok(config)
}

fn validate_reexport_project(
    project_root: &Path,
    project: &XpanoProjectV2,
) -> Result<String, ProjectCommandError> {
    if project.reconstruction.backend != ReconstructionBackend::Metashape {
        return Err(ProjectCommandError::new(
            "backend_unavailable",
            "PSX re-export is only available for Metashape projects",
        ));
    }
    if project.reconstruction.status == ReconstructionStatus::Running {
        return Err(ProjectCommandError::new(
            "job_conflict",
            "PSX inspection and re-export are unavailable while reconstruction is running",
        ));
    }
    if project.reconstruction.input_revision != project.revisions.alignment_input {
        return Err(ProjectCommandError::new(
            "invalid_project",
            "PSX re-export requires a current, non-running Metashape reconstruction",
        ));
    }
    let relative = project
        .reconstruction
        .project_path
        .as_deref()
        .ok_or_else(|| ProjectCommandError::new("artifact_corrupt", "Metashape project path is missing"))?;
    let relative_path = Path::new(relative);
    if relative_path.is_absolute() || relative_path.components().any(|part| matches!(part, std::path::Component::ParentDir)) {
        return Err(ProjectCommandError::new(
            "artifact_corrupt",
            "Metashape project path must be project-relative",
        ));
    }
    require_non_empty_artifact(&project_root.join(relative_path), "Metashape PSX")?;
    Ok(relative.to_string())
}

fn store_execution_plan(
    project_root: &Path,
    operation: ReconstructionOperation,
    config: ReconstructionPlanConfig,
    plan: &ExecutionPlan,
) -> Result<(), ProjectCommandError> {
    let stored = StoredExecutionPlan {
        schema_version: 1,
        operation,
        config,
        plan: plan.clone(),
    };
    write_json_value_atomic(
        &active_reconstruction_plan_path(project_root),
        &serde_json::to_value(stored).map_err(|error| {
            ProjectCommandError::new(
                "artifact_corrupt",
                format!("failed to serialize execution plan: {}", error),
            )
        })?,
    )
}

pub fn build_execution_plan_impl(
    project_root: &Path,
    expected_revision: u64,
    config: ReconstructionPlanConfig,
) -> Result<ExecutionPlan, ProjectCommandError> {
    let config = normalize_plan_config(config)?;
    let project = read_project(project_root)?;
    if project.revision != expected_revision {
        return Err(ProjectCommandError::revision_conflict(
            expected_revision,
            project.revision,
        ));
    }
    let (has_panorama, has_frames) = active_track_types(project_root, &project)?;
    let nodes = match config.backend {
        ReconstructionBackend::Metashape => metashape_backbone_nodes(has_panorama, has_frames),
        ReconstructionBackend::Colmap => {
            if has_frames {
                return Err(ProjectCommandError::new(
                    "backend_unavailable",
                    "COLMAP mixed or flat-frame reconstruction is disabled until multi-camera regression coverage is complete",
                ));
            }
            colmap_panorama_nodes()
        }
    };
    let plan = ExecutionPlan {
        schema_version: EXECUTION_PLAN_SCHEMA_VERSION,
        plan_id: Uuid::new_v4().to_string(),
        project_id: project.project_id,
        input_revision: project.revisions.alignment_input,
        backend: config.backend,
        created_at: Utc::now().to_rfc3339_opts(SecondsFormat::Millis, true),
        nodes,
    };
    plan.validate()
        .map_err(|error| ProjectCommandError::new("invalid_project", error))?;
    store_execution_plan(project_root, ReconstructionOperation::Align, config, &plan)?;
    Ok(plan)
}

pub fn build_reexport_plan_impl(
    project_root: &Path,
    expected_revision: u64,
    config: ReconstructionPlanConfig,
) -> Result<ExecutionPlan, ProjectCommandError> {
    let config = normalize_plan_config(config)?;
    if config.backend != ReconstructionBackend::Metashape {
        return Err(ProjectCommandError::new(
            "backend_unavailable",
            "PSX re-export is only available with Metashape",
        ));
    }
    let project = read_project(project_root)?;
    if project.revision != expected_revision {
        return Err(ProjectCommandError::revision_conflict(
            expected_revision,
            project.revision,
        ));
    }
    active_track_types(project_root, &project)?;
    validate_reexport_project(project_root, &project)?;
    let plan = ExecutionPlan {
        schema_version: EXECUTION_PLAN_SCHEMA_VERSION,
        plan_id: Uuid::new_v4().to_string(),
        project_id: project.project_id,
        input_revision: project.revisions.alignment_input,
        backend: ReconstructionBackend::Metashape,
        created_at: Utc::now().to_rfc3339_opts(SecondsFormat::Millis, true),
        nodes: metashape_reexport_nodes(),
    };
    plan.validate()
        .map_err(|error| ProjectCommandError::new("invalid_project", error))?;
    store_execution_plan(project_root, ReconstructionOperation::Reexport, config, &plan)?;
    Ok(plan)
}

fn inspect_metashape_components_impl(
    project_root: &Path,
    expected_revision: u64,
    metashape_path: &str,
    script_path: &Path,
) -> Result<ComponentInspection, ProjectCommandError> {
    let project = read_project(project_root)?;
    if project.revision != expected_revision {
        return Err(ProjectCommandError::revision_conflict(
            expected_revision,
            project.revision,
        ));
    }
    let project_relative = validate_reexport_project(project_root, &project)?;
    let executable = normalize_executable_path(metashape_path);
    if executable.is_empty() || !command_available(executable) {
        return Err(ProjectCommandError::new(
            "backend_unavailable",
            "The selected Metashape executable is not available",
        ));
    }
    if !script_path.is_file() {
        return Err(ProjectCommandError::new(
            "backend_unavailable",
            format!(
                "Metashape Component inspection script is missing: {}",
                script_path.display()
            ),
        ));
    }

    let work_dir = project_root.join("work");
    std::fs::create_dir_all(&work_dir).map_err(|error| {
        ProjectCommandError::new(
            "artifact_corrupt",
            format!("failed to prepare Component inspection output: {error}"),
        )
    })?;
    let output_path = work_dir.join(format!(
        "component-inspection-{}.json",
        Uuid::new_v4()
    ));
    let project_path = project_root.join(project_relative);
    let result = (|| {
        let mut command = Command::new(executable);
        #[cfg(target_os = "windows")]
        command.creation_flags(0x08000000);
        let output = command
            .args(component_inspection_args(
                script_path,
                &project_path,
                &output_path,
            ))
            .env("PYTHONIOENCODING", "utf-8:replace")
            .env("PYTHONUTF8", "1")
            .output()
            .map_err(|error| {
                ProjectCommandError::new(
                    "backend_unavailable",
                    format!("failed to launch Metashape Component inspection: {error}"),
                )
            })?;
        if !output.status.success() {
            let stdout = String::from_utf8_lossy(&output.stdout);
            let stderr = String::from_utf8_lossy(&output.stderr);
            return Err(ProjectCommandError::new(
                "artifact_corrupt",
                format!(
                    "Metashape could not inspect the current PSX (exit {:?}): {}{}",
                    output.status.code(),
                    stdout.trim(),
                    stderr.trim()
                ),
            ));
        }
        let payload = std::fs::read(&output_path).map_err(|error| {
            ProjectCommandError::new(
                "artifact_corrupt",
                format!("Metashape did not produce Component inventory: {error}"),
            )
        })?;
        parse_component_inspection(&payload)
    })();
    let _ = std::fs::remove_file(&output_path);
    result
}

#[tauri::command]
pub fn build_execution_plan(
    app: tauri::AppHandle,
    project_root: String,
    expected_revision: u64,
    config: ReconstructionPlanConfig,
) -> Result<ExecutionPlan, ProjectCommandError> {
    let mut plan = build_execution_plan_impl(Path::new(&project_root), expected_revision, config)?;
    if let Err(error) = crate::performance::apply_estimates_for_app(
        &app,
        Path::new(&project_root),
        &mut plan,
    ) {
        let _ = app.emit(
            "performance:warning",
            serde_json::json!({ "error": error.message }),
        );
    }
    Ok(plan)
}

#[tauri::command]
pub fn build_reexport_plan(
    app: tauri::AppHandle,
    project_root: String,
    expected_revision: u64,
    config: ReconstructionPlanConfig,
) -> Result<ExecutionPlan, ProjectCommandError> {
    let mut plan = build_reexport_plan_impl(Path::new(&project_root), expected_revision, config)?;
    if let Err(error) = crate::performance::apply_estimates_for_app(
        &app,
        Path::new(&project_root),
        &mut plan,
    ) {
        let _ = app.emit(
            "performance:warning",
            serde_json::json!({ "error": error.message }),
        );
    }
    Ok(plan)
}

#[tauri::command]
pub async fn inspect_metashape_components(
    project_root: String,
    expected_revision: u64,
    metashape_path: String,
) -> Result<ComponentInspection, ProjectCommandError> {
    let script_path = crate::tool_resolver::resolve_script_path(
        "scripts/inspect_metashape_components.py",
    );
    tauri::async_runtime::spawn_blocking(move || {
        inspect_metashape_components_impl(
            Path::new(&project_root),
            expected_revision,
            &metashape_path,
            &script_path,
        )
    })
    .await
    .map_err(|error| {
        ProjectCommandError::new(
            "backend_unavailable",
            format!("Metashape Component inspection worker failed: {error}"),
        )
    })?
}

#[tauri::command]
pub fn probe_reconstruction_backends(metashape_path: Option<String>) -> Vec<BackendProbe> {
    probe_reconstruction_backends_impl(metashape_path.as_deref())
}

#[tauri::command]
pub fn update_reconstruction_config(
    project_root: String,
    expected_revision: u64,
    backend: ReconstructionBackend,
    config: serde_json::Value,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    update_reconstruction_config_impl(
        Path::new(&project_root),
        expected_revision,
        backend,
        config,
    )
}

#[tauri::command]
pub fn sync_reconstruction_job_result(
    project_root: String,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    finalize_reconstruction_job_impl(Path::new(&project_root))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::contracts::{
        ProjectMediaItem, ProjectTrack, ProjectTrackType, SourceFingerprint,
    };
    use crate::project::write_project_atomic;
    use std::io::Write;
    use std::path::PathBuf;

    fn temp_case(name: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!(
            "xpano-reconstruction-v2-{}-{}-{}",
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

    #[test]
    fn explicit_metashape_probe_path_is_authoritative() {
        let root = temp_case("explicit-metashape");
        let executable = root.join("Metashape 工具").join("metashape.exe");
        std::fs::create_dir_all(executable.parent().unwrap()).unwrap();
        std::fs::write(&executable, b"metashape").unwrap();

        let quoted = format!("  \"{}\"  ", executable.display());
        let probes = probe_reconstruction_backends_impl(Some(&quoted));
        let metashape = probes
            .iter()
            .find(|probe| probe.backend == ReconstructionBackend::Metashape)
            .unwrap();

        assert!(metashape.available);
        assert_eq!(Path::new(&metashape.path), executable.as_path());
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn missing_explicit_metashape_path_does_not_fall_back() {
        let root = temp_case("missing-explicit-metashape");
        let missing = root.join("missing").join("metashape.exe");

        let probes = probe_reconstruction_backends_impl(Some(missing.to_string_lossy().as_ref()));
        let metashape = probes
            .iter()
            .find(|probe| probe.backend == ReconstructionBackend::Metashape)
            .unwrap();

        assert!(!metashape.available);
        assert_eq!(Path::new(&metashape.path), missing.as_path());
        let _ = std::fs::remove_dir_all(root);
    }

    fn fixture_project() -> XpanoProjectV2 {
        serde_json::from_str(include_str!(
            "../../../schemas/fixtures/xpano_project_v3.example.json"
        ))
        .unwrap()
    }

    fn write_valid_points(path: &Path, count: u64) {
        let mut file = std::fs::File::create(path).unwrap();
        file.write_all(&count.to_le_bytes()).unwrap();
        for index in 0..count {
            file.write_all(&(index + 1).to_le_bytes()).unwrap();
            for value in [index as f64, index as f64 + 1.0, index as f64 + 2.0] {
                file.write_all(&value.to_le_bytes()).unwrap();
            }
            file.write_all(&[10, 20, 30]).unwrap();
            file.write_all(&0.25f64.to_le_bytes()).unwrap();
            file.write_all(&0u64.to_le_bytes()).unwrap();
        }
        file.sync_all().unwrap();
    }

    fn write_successful_alignment_report(root: &Path) {
        std::fs::write(
            root.join("xpano_alignment_report.json"),
            br#"{"schemaVersion":1,"processSucceeded":true,"state":"complete","totalCameras":2,"alignedCameras":1,"alignmentRate":50.0,"components":[{"componentKey":"7","alignedCameraCount":1,"totalCameraCount":2}],"selectedComponentKey":"7","warnings":["partial alignment"]}"#,
        )
        .unwrap();
    }

    #[test]
    fn schema_v2_alignment_report_rejects_inconsistent_component_inventory() {
        let root = temp_case("invalid-v2-alignment-report");
        std::fs::write(
            root.join("xpano_alignment_report.json"),
            br#"{"schemaVersion":2,"processSucceeded":true,"state":"complete","inventoryComplete":true,"totalCameras":2,"alignedCameras":1,"unalignedCameras":0,"components":[{"componentKey":"7","alignedCameraCount":1,"totalCameraCount":1,"tiePointCount":10},{"componentKey":"7","alignedCameraCount":1,"totalCameraCount":1,"tiePointCount":10}],"selectedComponentKey":"7","selectedComponentAlignedCameras":1,"warnings":[]}"#,
        )
        .unwrap();

        assert_eq!(validated_alignment_report(&root).unwrap_err().code, "artifact_corrupt");
        let _ = std::fs::remove_dir_all(root);
    }

    fn write_active_manifest(root: &Path, project: &mut XpanoProjectV2, types: &[&str]) {
        let relative = "work/manifests/alignment_00000002.json";
        let path = root.join(relative);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        let tracks = types
            .iter()
            .enumerate()
            .map(|(index, track_type)| {
                serde_json::json!({
                    "track_id": format!("track-{index}"),
                    "track_type": track_type,
                    "source_paths": [format!("D:/source-{index}")],
                    "frames": if *track_type == "panorama_video" { serde_json::json!([{"left":"a","right":"b"}]) } else { serde_json::Value::Null },
                    "photos": if *track_type != "panorama_video" { serde_json::json!(["a.jpg"]) } else { serde_json::Value::Null }
                })
            })
            .collect::<Vec<_>>();
        std::fs::write(
            path,
            serde_json::to_vec_pretty(&serde_json::json!({
                "schema_version": 1,
                "workflow": "xpano_multi_track",
                "tracks": tracks
            }))
            .unwrap(),
        )
        .unwrap();
        project.reconstruction.config["alignmentManifestPath"] =
            serde_json::Value::String(relative.to_string());
    }

    #[test]
    fn component_inspection_accepts_truthful_schema_v2_inventory() {
        let inspection = parse_component_inspection(br#"{
            "schemaVersion": 2,
            "inventoryComplete": true,
            "totalCameras": 880,
            "alignedCameras": 856,
            "unalignedCameras": 24,
            "defaultComponentKey": "12",
            "components": [
                {"componentKey":"12","label":"Main","alignedCameraCount":458,"totalCameraCount":458,"tiePointCount":132270,"isInitiallyActive":false},
                {"componentKey":"27","label":"Secondary","alignedCameraCount":221,"totalCameraCount":221,"tiePointCount":94262,"isInitiallyActive":true}
            ],
            "warnings": ["Multiple Components"]
        }"#).unwrap();

        assert_eq!(inspection.default_component_key, "12");
        assert_eq!(inspection.components.len(), 2);
        assert_eq!(inspection.components[0].aligned_camera_count, 458);
    }

    #[test]
    fn component_inspection_rejects_inconsistent_or_ambiguous_inventory() {
        for payload in [
            br#"{"schemaVersion":2,"inventoryComplete":true,"totalCameras":2,"alignedCameras":3,"unalignedCameras":0,"defaultComponentKey":"1","components":[{"componentKey":"1","alignedCameraCount":1,"tiePointCount":1}],"warnings":[]}"#.as_slice(),
            br#"{"schemaVersion":2,"inventoryComplete":true,"totalCameras":2,"alignedCameras":1,"unalignedCameras":1,"defaultComponentKey":"missing","components":[{"componentKey":"1","alignedCameraCount":1,"tiePointCount":1}],"warnings":[]}"#.as_slice(),
            br#"{"schemaVersion":2,"inventoryComplete":true,"totalCameras":2,"alignedCameras":1,"unalignedCameras":1,"defaultComponentKey":"1","components":[{"componentKey":"1","alignedCameraCount":1,"tiePointCount":1},{"componentKey":"1","alignedCameraCount":1,"tiePointCount":2}],"warnings":[]}"#.as_slice(),
            br#"{"schemaVersion":2,"inventoryComplete":true,"totalCameras":2,"alignedCameras":0,"unalignedCameras":2,"defaultComponentKey":"1","components":[{"componentKey":"1","alignedCameraCount":0,"tiePointCount":0}],"warnings":[]}"#.as_slice(),
        ] {
            assert_eq!(parse_component_inspection(payload).unwrap_err().code, "artifact_corrupt");
        }
    }

    #[test]
    fn component_inspection_arguments_preserve_spaces_and_unicode_paths() {
        let script = Path::new("D:/xPano scripts/inspect_metashape_components.py");
        let project = Path::new("D:/项目 素材/work/xpano.psx");
        let output = Path::new("D:/项目 素材/work/component.json");

        assert_eq!(
            component_inspection_args(script, project, output),
            vec![
                std::ffi::OsString::from("-r"),
                script.as_os_str().to_owned(),
                std::ffi::OsString::from("--project"),
                project.as_os_str().to_owned(),
                std::ffi::OsString::from("--output"),
                output.as_os_str().to_owned(),
            ]
        );
    }

    #[test]
    fn component_inspection_rejects_revision_conflict_before_process_launch() {
        let root = temp_case("component-inspection-revision");
        let project = fixture_project();
        write_project_atomic(&root, &project).unwrap();

        let error = inspect_metashape_components_impl(
            &root,
            project.revision + 1,
            "missing-metashape.exe",
            Path::new("missing-inspector.py"),
        )
        .unwrap_err();

        assert_eq!(error.code, "revision_conflict");
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn component_inspection_rejects_missing_psx_before_process_launch() {
        let root = temp_case("component-inspection-missing-psx");
        let mut project = fixture_project();
        project.reconstruction.backend = ReconstructionBackend::Metashape;
        project.reconstruction.status = ReconstructionStatus::Complete;
        project.reconstruction.input_revision = project.revisions.alignment_input;
        project.reconstruction.project_path = Some("work/xpano.psx".to_string());
        write_project_atomic(&root, &project).unwrap();

        let error = inspect_metashape_components_impl(
            &root,
            project.revision,
            "missing-metashape.exe",
            Path::new("missing-inspector.py"),
        )
        .unwrap_err();

        assert_eq!(error.code, "artifact_corrupt");
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn backbone_plan_marks_absent_flat_branch_as_skipped() {
        let root = temp_case("pano");
        let mut project = fixture_project();
        write_active_manifest(&root, &mut project, &["panorama_video"]);
        write_project_atomic(&root, &project).unwrap();

        let plan = build_execution_plan_impl(
            &root,
            project.revision,
            ReconstructionPlanConfig {
                backend: ReconstructionBackend::Metashape,
                alignment_mode: Some("backbone".to_string()),
                metashape_path: None,
            },
        )
        .unwrap();

        assert_eq!(plan.input_revision, project.revisions.alignment_input);
        assert!(plan.nodes.iter().any(|node| node.stage_id == "metashape.pano.align" && node.skip_reason.is_none()));
        assert!(plan.nodes.iter().any(|node| node.stage_id == "metashape.frame.align" && node.skip_reason.is_some()));
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn backbone_plan_exposes_panorama_first_incremental_alignment() {
        let nodes = metashape_backbone_nodes(true, true);
        let stages = nodes
            .iter()
            .map(|node| node.stage_id.as_str())
            .collect::<Vec<_>>();

        assert_eq!(
            stages,
            vec![
                "input.validate",
                "metashape.project.create",
                "metashape.pano.import",
                "metashape.pano.station",
                "metashape.pano.match",
                "metashape.pano.align",
                "metashape.pano.release",
                "metashape.pano.optimize",
                "metashape.frame.import",
                "metashape.frame.match",
                "metashape.frame.align",
                "metashape.all.optimize",
                "metashape.project.save",
                "metashape.component.select",
                "coordinate.auto_level",
                "export.images",
                "export.colmap",
                "output.validate",
            ]
        );
        assert_eq!(
            nodes
                .iter()
                .find(|node| node.stage_id == "metashape.frame.import")
                .unwrap()
                .depends_on,
            vec!["metashape.pano.optimize"]
        );
        let total_weight = nodes.iter().map(|node| node.weight).sum::<f64>();
        assert!((total_weight - 1.0).abs() < 1e-9);
    }

    #[test]
    fn colmap_rejects_mixed_media_until_backend_support_is_verified() {
        let root = temp_case("colmap-mixed");
        let mut project = fixture_project();
        project.tracks.push(ProjectTrack {
            id: "flat-track".to_string(),
            track_type: ProjectTrackType::StandardPhotos,
            label: "Reference photos".to_string(),
            source_path: "D:/photos".to_string(),
            source_fingerprint: SourceFingerprint { size: 0, mtime_ns: 0 },
            camera_profile: None,
            trim: None,
            extraction: project.tracks[0].extraction.clone(),
            status: ProjectTrackStatus::Ready,
            items: vec![ProjectMediaItem {
                id: "photo_00001".to_string(),
                timestamp: None,
                selected: true,
                left: None,
                right: None,
                thumbnail_left: None,
                thumbnail_right: None,
                image: Some("work/media/flat/photo.jpg".to_string()),
                thumbnail: Some("work/thumbnails/flat/photo.jpg".to_string()),
            }],
        });
        write_active_manifest(
            &root,
            &mut project,
            &["panorama_video", "standard_photos"],
        );
        write_project_atomic(&root, &project).unwrap();

        let error = build_execution_plan_impl(
            &root,
            project.revision,
            ReconstructionPlanConfig {
                backend: ReconstructionBackend::Colmap,
                alignment_mode: None,
                metashape_path: None,
            },
        )
        .unwrap_err();

        assert_eq!(error.code, "backend_unavailable");
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn plan_rejects_projects_without_selected_alignment_tracks() {
        let root = temp_case("empty");
        let mut project = fixture_project();
        write_active_manifest(&root, &mut project, &[]);
        write_project_atomic(&root, &project).unwrap();

        let error = build_execution_plan_impl(
            &root,
            project.revision,
            ReconstructionPlanConfig {
                backend: ReconstructionBackend::Metashape,
                alignment_mode: Some("backbone".to_string()),
                metashape_path: None,
            },
        )
        .unwrap_err();

        assert_eq!(error.code, "invalid_project");
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn plan_rejects_missing_selected_metashape_executable() {
        let root = temp_case("plan-missing-metashape");
        let mut project = fixture_project();
        write_active_manifest(&root, &mut project, &["panorama_video"]);
        write_project_atomic(&root, &project).unwrap();
        let missing = root.join("missing").join("metashape.exe");

        let error = build_execution_plan_impl(
            &root,
            project.revision,
            ReconstructionPlanConfig {
                backend: ReconstructionBackend::Metashape,
                alignment_mode: Some("backbone".to_string()),
                metashape_path: Some(missing.to_string_lossy().into_owned()),
            },
        )
        .unwrap_err();

        assert_eq!(error.code, "backend_unavailable");
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn completed_metashape_project_builds_export_only_reexport_plan() {
        let root = temp_case("reexport-plan");
        let mut project = fixture_project();
        write_active_manifest(&root, &mut project, &["panorama_video"]);
        project.reconstruction.backend = ReconstructionBackend::Metashape;
        project.reconstruction.status = ReconstructionStatus::Complete;
        project.reconstruction.input_revision = project.revisions.alignment_input;
        project.reconstruction.project_path = Some("work/xpano.psx".to_string());
        let psx = root.join("work/xpano.psx");
        std::fs::create_dir_all(psx.parent().unwrap()).unwrap();
        std::fs::write(&psx, b"corrected psx").unwrap();
        write_project_atomic(&root, &project).unwrap();

        let plan = build_reexport_plan_impl(
            &root,
            project.revision,
            ReconstructionPlanConfig {
                backend: ReconstructionBackend::Metashape,
                alignment_mode: Some("backbone".to_string()),
                metashape_path: None,
            },
        )
        .unwrap();

        let stages = plan
            .nodes
            .iter()
            .map(|node| node.stage_id.as_str())
            .collect::<Vec<_>>();
        assert_eq!(
            stages,
            vec![
                "export.reuse_project",
                "metashape.component.validate",
                "export.images",
                "export.colmap",
                "output.validate"
            ]
        );
        assert!((plan.nodes.iter().map(|node| node.weight).sum::<f64>() - 1.0).abs() < 1e-9);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn reexport_plan_rejects_a_missing_psx_without_changing_project() {
        let root = temp_case("reexport-missing-psx");
        let mut project = fixture_project();
        write_active_manifest(&root, &mut project, &["panorama_video"]);
        project.reconstruction.backend = ReconstructionBackend::Metashape;
        project.reconstruction.status = ReconstructionStatus::Complete;
        project.reconstruction.input_revision = project.revisions.alignment_input;
        project.reconstruction.project_path = Some("work/xpano.psx".to_string());
        write_project_atomic(&root, &project).unwrap();

        let error = build_reexport_plan_impl(
            &root,
            project.revision,
            ReconstructionPlanConfig {
                backend: ReconstructionBackend::Metashape,
                alignment_mode: Some("backbone".to_string()),
                metashape_path: None,
            },
        )
        .unwrap_err();

        assert_eq!(error.code, "artifact_corrupt");
        assert_eq!(read_project(&root).unwrap(), project);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn saving_reconstruction_config_preserves_manifest_paths_and_marks_result_stale() {
        let root = temp_case("save-config");
        let mut project = fixture_project();
        project.reconstruction.config = serde_json::json!({
            "alignmentManifestPath": "work/manifests/alignment_00000002.json",
            "mediaManifestPath": "work/manifests/media_full.json",
            "alignmentMode": "backbone"
        });
        write_project_atomic(&root, &project).unwrap();

        let updated = update_reconstruction_config_impl(
            &root,
            project.revision,
            ReconstructionBackend::Metashape,
            serde_json::json!({
                "alignmentMode": "mixed",
                "metashapeKeypointLimit": 60000,
                "metashapeTiepointLimit": 0,
                "upAxis": "+Y"
            }),
        )
        .unwrap();

        assert_eq!(updated.revision, project.revision + 1);
        assert_eq!(updated.reconstruction.status, crate::contracts::ReconstructionStatus::Stale);
        assert_eq!(updated.reconstruction.config["alignmentMode"], "mixed");
        assert_eq!(
            updated.reconstruction.config["alignmentManifestPath"],
            "work/manifests/alignment_00000002.json"
        );
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn registered_execution_plan_starts_the_matching_reconstruction_revision() {
        let root = temp_case("start-registered-plan");
        let mut project = fixture_project();
        write_active_manifest(&root, &mut project, &["panorama_video"]);
        project.reconstruction.backend = ReconstructionBackend::Metashape;
        project.reconstruction.config["alignmentMode"] =
            serde_json::Value::String("backbone".to_string());
        write_project_atomic(&root, &project).unwrap();

        let plan = build_execution_plan_impl(
            &root,
            project.revision,
            ReconstructionPlanConfig {
                backend: ReconstructionBackend::Metashape,
                alignment_mode: Some("backbone".to_string()),
                metashape_path: None,
            },
        )
        .unwrap();

        let running = begin_reconstruction_job_from_plan_impl(
            &root,
            project.revision,
            &plan.plan_id,
        )
        .unwrap();

        assert_eq!(running.reconstruction.status, ReconstructionStatus::Running);
        assert_eq!(running.reconstruction.input_revision, plan.input_revision);
        assert_eq!(running.reconstruction.config["activePlanId"], plan.plan_id);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn registered_execution_plan_rejects_changed_metashape_path() {
        let root = temp_case("start-changed-metashape-path");
        let original_executable = root.join("Metashape A").join("metashape.exe");
        let changed_executable = root.join("Metashape B").join("metashape.exe");
        std::fs::create_dir_all(original_executable.parent().unwrap()).unwrap();
        std::fs::create_dir_all(changed_executable.parent().unwrap()).unwrap();
        std::fs::write(&original_executable, b"metashape").unwrap();
        std::fs::write(&changed_executable, b"metashape").unwrap();
        let mut project = fixture_project();
        write_active_manifest(&root, &mut project, &["panorama_video"]);
        project.reconstruction.backend = ReconstructionBackend::Metashape;
        project.reconstruction.config["alignmentMode"] =
            serde_json::Value::String("backbone".to_string());
        project.reconstruction.config["metashapePath"] =
            serde_json::Value::String(original_executable.to_string_lossy().into_owned());
        write_project_atomic(&root, &project).unwrap();

        let plan = build_execution_plan_impl(
            &root,
            project.revision,
            ReconstructionPlanConfig {
                backend: ReconstructionBackend::Metashape,
                alignment_mode: Some("backbone".to_string()),
                metashape_path: Some(original_executable.to_string_lossy().into_owned()),
            },
        )
        .unwrap();

        let mut changed = read_project(&root).unwrap();
        changed.reconstruction.config["metashapePath"] =
            serde_json::Value::String(changed_executable.to_string_lossy().into_owned());
        changed.revision += 1;
        write_project_atomic(&root, &changed).unwrap();

        let error = begin_reconstruction_job_from_plan_impl(
            &root,
            changed.revision,
            &plan.plan_id,
        )
        .unwrap_err();

        assert_eq!(error.code, "revision_conflict");
        assert_eq!(read_project(&root).unwrap(), changed);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn registered_execution_plan_rejects_changed_alignment_input() {
        let root = temp_case("start-stale-plan");
        let mut project = fixture_project();
        write_active_manifest(&root, &mut project, &["panorama_video"]);
        project.reconstruction.backend = ReconstructionBackend::Metashape;
        project.reconstruction.config["alignmentMode"] =
            serde_json::Value::String("backbone".to_string());
        write_project_atomic(&root, &project).unwrap();
        let plan = build_execution_plan_impl(
            &root,
            project.revision,
            ReconstructionPlanConfig {
                backend: ReconstructionBackend::Metashape,
                alignment_mode: Some("backbone".to_string()),
                metashape_path: None,
            },
        )
        .unwrap();

        let mut changed = read_project(&root).unwrap();
        changed.revision += 1;
        changed.revisions.alignment_input += 1;
        write_project_atomic(&root, &changed).unwrap();

        let error = begin_reconstruction_job_from_plan_impl(
            &root,
            changed.revision,
            &plan.plan_id,
        )
        .unwrap_err();

        assert_eq!(error.code, "revision_conflict");
        assert_eq!(read_project(&root).unwrap(), changed);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn flat_only_and_legacy_mixed_configs_expose_the_staged_graph() {
        let flat_root = temp_case("flat-plan");
        let mut flat_project = fixture_project();
        write_active_manifest(&flat_root, &mut flat_project, &["standard_photos"]);
        write_project_atomic(&flat_root, &flat_project).unwrap();
        let flat_plan = build_execution_plan_impl(
            &flat_root,
            flat_project.revision,
            ReconstructionPlanConfig {
                backend: ReconstructionBackend::Metashape,
                alignment_mode: Some("backbone".to_string()),
                metashape_path: None,
            },
        )
        .unwrap();
        assert!(flat_plan.nodes.iter().any(|node| {
            node.stage_id == "metashape.pano.import" && node.skip_reason.is_some()
        }));
        assert!(flat_plan.nodes.iter().any(|node| {
            node.stage_id == "metashape.frame.import" && node.skip_reason.is_none()
        }));

        let mixed_root = temp_case("mixed-plan");
        let mut mixed_project = fixture_project();
        write_active_manifest(
            &mixed_root,
            &mut mixed_project,
            &["panorama_video", "standard_photos"],
        );
        write_project_atomic(&mixed_root, &mixed_project).unwrap();
        let mixed_plan = build_execution_plan_impl(
            &mixed_root,
            mixed_project.revision,
            ReconstructionPlanConfig {
                backend: ReconstructionBackend::Metashape,
                alignment_mode: Some("mixed".to_string()),
                metashape_path: None,
            },
        )
        .unwrap();
        assert!(mixed_plan
            .nodes
            .iter()
            .any(|node| node.stage_id == "metashape.pano.match"));
        assert!(mixed_plan
            .nodes
            .iter()
            .any(|node| node.stage_id == "metashape.frame.match"));
        assert!(mixed_plan
            .nodes
            .iter()
            .any(|node| node.stage_id == "metashape.component.select"));
        assert!(!mixed_plan
            .nodes
            .iter()
            .any(|node| node.stage_id == "metashape.all.match"));

        let _ = std::fs::remove_dir_all(flat_root);
        let _ = std::fs::remove_dir_all(mixed_root);
    }

    #[test]
    fn successful_reconstruction_registers_artifacts_and_opens_results_workspace() {
        let root = temp_case("finalize-success");
        let mut project = fixture_project();
        project.revisions.alignment_input = 4;
        project.reconstruction.status = crate::contracts::ReconstructionStatus::Stale;
        project.reconstruction.config["alignmentManifestPath"] =
            serde_json::Value::String("work/manifests/alignment_00000002.json".to_string());
        write_project_atomic(&root, &project).unwrap();

        let sparse = root.join("sparse/0");
        std::fs::create_dir_all(&sparse).unwrap();
        std::fs::create_dir_all(root.join("images")).unwrap();
        std::fs::create_dir_all(root.join("work")).unwrap();
        std::fs::write(sparse.join("cameras.bin"), 1u64.to_le_bytes()).unwrap();
        std::fs::write(sparse.join("images.bin"), 1u64.to_le_bytes()).unwrap();
        write_valid_points(&sparse.join("points3D.bin"), 1);
        std::fs::write(root.join("work/xpano.psx"), b"project").unwrap();
        write_successful_alignment_report(&root);
        std::fs::write(
            root.join("xpano_run_summary.json"),
            br#"{"manifest":"D:/project/work/manifests/alignment_00000002.json"}"#,
        )
        .unwrap();

        let updated = finalize_reconstruction_job_impl(&root).unwrap();

        assert_eq!(updated.reconstruction.status, crate::contracts::ReconstructionStatus::Complete);
        assert_eq!(updated.reconstruction.input_revision, 4);
        assert_eq!(updated.revisions.alignment, project.revisions.alignment + 1);
        assert_eq!(updated.reconstruction.project_path.as_deref(), Some("work/xpano.psx"));
        assert_eq!(updated.reconstruction.colmap_path.as_deref(), Some("."));
        assert_eq!(updated.reconstruction.config["selectedComponentKey"], "7");
        assert_eq!(updated.reconstruction.config["alignmentReport"]["alignmentRate"], 50.0);
        assert_eq!(updated.active_workspace, crate::contracts::ProjectWorkspace::Results);
        assert_eq!(updated.revision, project.revision + 1);
        assert_eq!(updated.geometry.active_variant_id, "standard");
        assert_eq!(updated.geometry.variants[0].point_count, 1);
        assert!(!updated.geometry.variants[0].checksum_sha256.is_empty());
        assert!(root.join("work/geometry/base_images.bin").is_file());
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn reconstruction_lifecycle_captures_input_revision_and_persists_failure() {
        let root = temp_case("lifecycle");
        let mut project = fixture_project();
        project.revisions.alignment_input = 6;
        project.reconstruction.status = crate::contracts::ReconstructionStatus::Stale;
        write_project_atomic(&root, &project).unwrap();

        let running = begin_reconstruction_job_impl(&root).unwrap();
        assert_eq!(running.reconstruction.status, crate::contracts::ReconstructionStatus::Running);
        assert_eq!(running.reconstruction.input_revision, 6);

        std::fs::create_dir_all(root.join("work")).unwrap();
        std::fs::write(root.join("work/xpano.psx"), b"partial project").unwrap();
        let failed = fail_reconstruction_job_impl(&root).unwrap();
        assert_eq!(failed.reconstruction.status, crate::contracts::ReconstructionStatus::Failed);
        assert_eq!(failed.active_workspace, crate::contracts::ProjectWorkspace::Reconstruction);
        assert_eq!(failed.reconstruction.colmap_path, project.reconstruction.colmap_path);
        assert_eq!(failed.reconstruction.project_path.as_deref(), Some("work/xpano.psx"));
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn failed_alignment_cannot_be_finalized_from_leftover_exports() {
        let root = temp_case("failed-finalize");
        let mut project = fixture_project();
        project.reconstruction.status = ReconstructionStatus::Failed;
        project.reconstruction.input_revision = project.revisions.alignment_input;
        project.reconstruction.project_path = Some("work/xpano.psx".to_string());
        write_project_atomic(&root, &project).unwrap();

        let sparse = root.join("sparse/0");
        std::fs::create_dir_all(&sparse).unwrap();
        std::fs::create_dir_all(root.join("work")).unwrap();
        std::fs::write(sparse.join("cameras.bin"), 1u64.to_le_bytes()).unwrap();
        std::fs::write(sparse.join("images.bin"), 1u64.to_le_bytes()).unwrap();
        write_valid_points(&sparse.join("points3D.bin"), 1);
        std::fs::write(root.join("work/xpano.psx"), b"partial project").unwrap();
        write_successful_alignment_report(&root);

        let error = finalize_reconstruction_job_impl(&root).unwrap_err();
        let unchanged = crate::project::read_project(&root).unwrap();

        assert_eq!(error.code, "job_conflict");
        assert_eq!(unchanged.reconstruction.status, ReconstructionStatus::Failed);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn failed_current_psx_can_build_an_explicit_reexport_plan() {
        let root = temp_case("failed-reexport");
        let mut project = fixture_project();
        project.reconstruction.backend = ReconstructionBackend::Metashape;
        project.reconstruction.status = ReconstructionStatus::Failed;
        project.reconstruction.input_revision = project.revisions.alignment_input;
        project.reconstruction.project_path = Some("work/xpano.psx".to_string());
        write_active_manifest(&root, &mut project, &["panorama_video"]);
        std::fs::create_dir_all(root.join("work")).unwrap();
        std::fs::write(root.join("work/xpano.psx"), b"manually fixed project").unwrap();
        write_project_atomic(&root, &project).unwrap();

        let plan = build_reexport_plan_impl(
            &root,
            project.revision,
            ReconstructionPlanConfig {
                backend: ReconstructionBackend::Metashape,
                alignment_mode: Some("backbone".to_string()),
                metashape_path: None,
            },
        )
        .unwrap();

        assert_eq!(plan.nodes.first().unwrap().stage_id, "export.reuse_project");
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn reconstruction_recovery_marks_an_orphaned_run_as_interrupted() {
        let root = temp_case("interrupted");
        let mut project = fixture_project();
        project.reconstruction.status = crate::contracts::ReconstructionStatus::Stale;
        write_project_atomic(&root, &project).unwrap();
        begin_reconstruction_job_impl(&root).unwrap();

        let interrupted = interrupt_reconstruction_job_impl(&root).unwrap();

        assert_eq!(
            interrupted.reconstruction.status,
            crate::contracts::ReconstructionStatus::Interrupted
        );
        assert_eq!(
            interrupted.active_workspace,
            crate::contracts::ProjectWorkspace::Reconstruction
        );
        assert_eq!(interrupted.reconstruction.colmap_path, project.reconstruction.colmap_path);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn reconstruction_completion_rejects_missing_sparse_model_without_mutating_project() {
        let root = temp_case("finalize-missing-model");
        let mut project = fixture_project();
        project.reconstruction.status = crate::contracts::ReconstructionStatus::Stale;
        write_project_atomic(&root, &project).unwrap();

        let error = finalize_reconstruction_job_impl(&root).unwrap_err();
        let unchanged = crate::project::read_project(&root).unwrap();

        assert_eq!(error.code, "artifact_corrupt");
        assert_eq!(unchanged.reconstruction.status, crate::contracts::ReconstructionStatus::Stale);
        assert_eq!(unchanged.revision, project.revision);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn reconstruction_completion_rejects_a_zero_point_model() {
        let root = temp_case("finalize-zero-points");
        let mut project = fixture_project();
        project.reconstruction.status = crate::contracts::ReconstructionStatus::Stale;
        project.reconstruction.config["alignmentManifestPath"] =
            serde_json::Value::String("work/manifests/alignment_00000002.json".to_string());
        write_project_atomic(&root, &project).unwrap();

        let sparse = root.join("sparse/0");
        std::fs::create_dir_all(&sparse).unwrap();
        std::fs::create_dir_all(root.join("work")).unwrap();
        std::fs::write(sparse.join("cameras.bin"), 1u64.to_le_bytes()).unwrap();
        std::fs::write(sparse.join("images.bin"), 1u64.to_le_bytes()).unwrap();
        std::fs::write(sparse.join("points3D.bin"), 0u64.to_le_bytes()).unwrap();
        std::fs::write(root.join("work/xpano.psx"), b"project").unwrap();
        write_successful_alignment_report(&root);
        std::fs::write(
            root.join("xpano_run_summary.json"),
            br#"{"manifest":"D:/project/work/manifests/alignment_00000002.json"}"#,
        )
        .unwrap();

        let error = finalize_reconstruction_job_impl(&root).unwrap_err();

        assert_eq!(error.code, "artifact_corrupt");
        let _ = std::fs::remove_dir_all(root);
    }
}
