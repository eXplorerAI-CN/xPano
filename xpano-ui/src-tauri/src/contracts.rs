use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::path::{Component, Path};

pub const PROJECT_SCHEMA_VERSION: u32 = 3;
pub const JOB_EVENT_SCHEMA_VERSION: u32 = 1;
pub const EXECUTION_PLAN_SCHEMA_VERSION: u32 = 1;
pub const DJI_OSMO_360_DLOGM_REC709_PRESET: &str = "builtin:dji-osmo360-dlogm-rec709";

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ProjectWorkspace {
    Media,
    Reconstruction,
    Results,
    Training,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ProjectTrackType {
    PanoramicVideo,
    OrdinaryVideo,
    StandardPhotos,
    AerialPhotos,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ProjectTrackStatus {
    Draft,
    Prepared,
    Running,
    Ready,
    Stale,
    Missing,
    Failed,
    Interrupted,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ReconstructionBackend {
    Metashape,
    Colmap,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ReconstructionStatus {
    Idle,
    Ready,
    Running,
    Complete,
    Stale,
    Failed,
    Interrupted,
    Repair,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TrainingStatus {
    #[default]
    Idle,
    Ready,
    Running,
    Complete,
    Stale,
    Failed,
    Interrupted,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PointVariantKind {
    Standard,
    Densified,
    Imported,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PointVariantStatus {
    Ready,
    Missing,
    Corrupt,
    #[default]
    Stale,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum JobState {
    Queued,
    Running,
    Cancelling,
    Completed,
    Failed,
    Cancelled,
    Skipped,
    Interrupted,
}

pub const BATCH_QUEUE_SCHEMA_VERSION: u32 = 1;

#[derive(Clone, Copy, Debug, Default, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum BatchQueueState {
    #[default]
    Idle,
    Running,
    Stopping,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum BatchTaskState {
    #[default]
    Draft,
    Queued,
    Running,
    Completed,
    Failed,
    Cancelled,
    Interrupted,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum BatchStageStatus {
    #[default]
    Disabled,
    Pending,
    Running,
    Completed,
    Failed,
    Skipped,
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BatchStages {
    pub media: bool,
    pub reconstruction: bool,
    pub training: bool,
}

impl BatchStages {
    pub fn validate_prefix(&self) -> Result<(), String> {
        if !self.media && !self.reconstruction && !self.training {
            return Err("batch task must enable at least the media stage".to_string());
        }
        if self.reconstruction && !self.media {
            return Err("batch stages must enable media before reconstruction".to_string());
        }
        if self.training && !self.reconstruction {
            return Err("batch stages must enable reconstruction before training".to_string());
        }
        if !self.media && (self.reconstruction || self.training) {
            return Err("batch stages must form a contiguous prefix".to_string());
        }
        Ok(())
    }

}

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BatchStageStatuses {
    pub media: BatchStageStatus,
    pub reconstruction: BatchStageStatus,
    pub training: BatchStageStatus,
}

impl BatchStageStatuses {
    pub fn for_stages(stages: &BatchStages) -> Self {
        Self {
            media: if stages.media { BatchStageStatus::Pending } else { BatchStageStatus::Disabled },
            reconstruction: if stages.reconstruction { BatchStageStatus::Pending } else { BatchStageStatus::Disabled },
            training: if stages.training { BatchStageStatus::Pending } else { BatchStageStatus::Disabled },
        }
    }
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BatchProgress {
    pub percent: f64,
    pub message: String,
    pub current: Option<u64>,
    pub total: Option<u64>,
    pub eta_seconds: Option<u64>,
    pub elapsed_seconds: u64,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BatchError {
    pub code: String,
    pub stage: Option<String>,
    pub message: String,
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BatchPipelineInput {
    #[serde(default)]
    pub media_track_ids: Vec<String>,
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BatchTask {
    pub task_id: String,
    pub project_id: String,
    pub project_root: String,
    pub label: String,
    pub order: u64,
    pub configured_revision: u64,
    pub stages: BatchStages,
    pub stage_status: BatchStageStatuses,
    pub state: BatchTaskState,
    pub current_stage: Option<String>,
    #[serde(default)]
    pub stage_job_ids: serde_json::Map<String, serde_json::Value>,
    pub progress: BatchProgress,
    pub last_error: Option<BatchError>,
    pub pipeline: BatchPipelineInput,
    pub created_at: String,
    pub started_at: Option<String>,
    pub finished_at: Option<String>,
    pub updated_at: String,
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BatchQueueFile {
    pub schema_version: u32,
    pub revision: u64,
    pub state: BatchQueueState,
    pub active_task_id: Option<String>,
    pub tasks: Vec<BatchTask>,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ProgressMode {
    Counted,
    Indeterminate,
    ExternalPercent,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProjectRevisions {
    pub media: u64,
    pub alignment_input: u64,
    pub alignment: u64,
    pub geometry: u64,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SourceFingerprint {
    pub size: u64,
    pub mtime_ns: u64,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProjectTrim {
    pub start: f64,
    pub end: f64,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ExtractionSettings {
    pub frames_per_second: f64,
    pub frame_limit: u64,
    #[serde(
        default,
        alias = "colorLutPath",
        skip_serializing_if = "Option::is_none"
    )]
    pub style_lut_path: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub color_lut_preset: Option<String>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProjectMediaItem {
    pub id: String,
    pub timestamp: Option<f64>,
    pub selected: bool,
    pub left: Option<String>,
    pub right: Option<String>,
    pub thumbnail_left: Option<String>,
    pub thumbnail_right: Option<String>,
    pub image: Option<String>,
    pub thumbnail: Option<String>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProjectTrack {
    pub id: String,
    #[serde(rename = "type")]
    pub track_type: ProjectTrackType,
    pub label: String,
    pub source_path: String,
    pub source_fingerprint: SourceFingerprint,
    pub camera_profile: Option<String>,
    pub trim: Option<ProjectTrim>,
    pub extraction: ExtractionSettings,
    pub status: ProjectTrackStatus,
    pub items: Vec<ProjectMediaItem>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReconstructionState {
    pub status: ReconstructionStatus,
    pub input_revision: u64,
    pub backend: ReconstructionBackend,
    pub config: serde_json::Value,
    pub project_path: Option<String>,
    pub colmap_path: Option<String>,
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TrainingState {
    pub status: TrainingStatus,
    pub input_revision: u64,
    pub config: serde_json::Value,
    pub output_path: Option<String>,
    pub artifact_path: Option<String>,
    pub source_job_id: Option<String>,
    pub last_iteration: u64,
    pub total_iterations: u64,
    pub last_loss: Option<f64>,
    pub splat_count: u64,
    pub error: Option<String>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WorldTransform {
    pub world_from_canonical: [f64; 16],
    pub revision: u64,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PointCloudVariant {
    pub id: String,
    pub label: String,
    pub kind: PointVariantKind,
    pub canonical_path: String,
    pub point_count: u64,
    pub created_at: String,
    pub source_job_id: Option<String>,
    pub protected: bool,
    #[serde(default)]
    pub checksum_sha256: String,
    #[serde(default)]
    pub transform_revision: u64,
    #[serde(default)]
    pub status: PointVariantStatus,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GeometryState {
    pub transform: WorldTransform,
    pub active_variant_id: String,
    pub variants: Vec<PointCloudVariant>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct JobSnapshot {
    pub job_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub project_root: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub task_id: Option<String>,
    pub workspace: ProjectWorkspace,
    pub state: JobState,
    pub stage_id: Option<String>,
    pub sequence: u64,
    pub started_at: String,
    pub updated_at: String,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct XpanoProjectV2 {
    pub schema_version: u32,
    pub project_id: String,
    pub name: String,
    pub created_at: String,
    pub updated_at: String,
    pub active_workspace: ProjectWorkspace,
    pub revision: u64,
    pub revisions: ProjectRevisions,
    pub tracks: Vec<ProjectTrack>,
    pub reconstruction: ReconstructionState,
    #[serde(default)]
    pub training: TrainingState,
    pub geometry: GeometryState,
    pub jobs: Vec<JobSnapshot>,
}

impl XpanoProjectV2 {
    pub fn validate(&self) -> Result<(), String> {
        if self.schema_version != PROJECT_SCHEMA_VERSION {
            return Err(format!("unsupported project schema version: {}", self.schema_version));
        }
        require_non_empty("projectId", &self.project_id)?;
        require_non_empty("name", &self.name)?;

        let mut track_ids = HashSet::new();
        for track in &self.tracks {
            require_non_empty("track.id", &track.id)?;
            require_non_empty("track.label", &track.label)?;
            require_non_empty("track.sourcePath", &track.source_path)?;
            if !track_ids.insert(track.id.as_str()) {
                return Err(format!("duplicate track id: {}", track.id));
            }
            if track.extraction.frames_per_second <= 0.0 || !track.extraction.frames_per_second.is_finite() {
                return Err(format!("invalid extraction frame rate for track {}", track.id));
            }
            if let Some(style_lut_path) = track.extraction.style_lut_path.as_deref() {
                let path = Path::new(style_lut_path.trim());
                if style_lut_path.trim().is_empty()
                    || !path
                        .extension()
                        .and_then(|value| value.to_str())
                        .is_some_and(|value| value.eq_ignore_ascii_case("cube"))
                {
                    return Err(format!("invalid style LUT path for track {}", track.id));
                }
            }
            if let Some(color_lut_preset) = track.extraction.color_lut_preset.as_deref() {
                let is_osv_panorama = track.track_type == ProjectTrackType::PanoramicVideo
                    && Path::new(&track.source_path)
                        .extension()
                        .and_then(|value| value.to_str())
                        .is_some_and(|value| value.eq_ignore_ascii_case("osv"));
                if color_lut_preset != DJI_OSMO_360_DLOGM_REC709_PRESET || !is_osv_panorama {
                    return Err(format!(
                        "color LUT preset is only valid for .osv panorama tracks: {}",
                        track.id
                    ));
                }
            }
            if let Some(trim) = &track.trim {
                if trim.start < 0.0 || trim.end <= trim.start || !trim.start.is_finite() || !trim.end.is_finite() {
                    return Err(format!("invalid trim range for track {}", track.id));
                }
            }
            let mut item_ids = HashSet::new();
            for item in &track.items {
                require_non_empty("track.item.id", &item.id)?;
                if !item_ids.insert(item.id.as_str()) {
                    return Err(format!("duplicate item id in track {}: {}", track.id, item.id));
                }
                let paths = [
                    item.left.as_deref(),
                    item.right.as_deref(),
                    item.thumbnail_left.as_deref(),
                    item.thumbnail_right.as_deref(),
                    item.image.as_deref(),
                    item.thumbnail.as_deref(),
                ];
                for path in paths.into_iter().flatten() {
                    require_relative_artifact_path(path)?;
                }
            }
        }

        for path in [
            self.reconstruction.project_path.as_deref(),
            self.reconstruction.colmap_path.as_deref(),
            self.training.output_path.as_deref(),
            self.training.artifact_path.as_deref(),
        ]
        .into_iter()
        .flatten()
        {
            require_relative_artifact_path(path)?;
        }
        if self.training.last_loss.is_some_and(|value| !value.is_finite()) {
            return Err("training loss contains a non-finite value".to_string());
        }

        if self.geometry.variants.is_empty() {
            return Err("geometry must contain at least one point variant".to_string());
        }
        if !self
            .geometry
            .transform
            .world_from_canonical
            .iter()
            .all(|value| value.is_finite())
        {
            return Err("worldFromCanonical contains a non-finite value".to_string());
        }
        let mut variant_ids = HashSet::new();
        for variant in &self.geometry.variants {
            require_non_empty("variant.id", &variant.id)?;
            require_relative_artifact_path(&variant.canonical_path)?;
            if !variant_ids.insert(variant.id.as_str()) {
                return Err(format!("duplicate point variant id: {}", variant.id));
            }
        }
        if !variant_ids.contains(self.geometry.active_variant_id.as_str()) {
            return Err("active point variant does not exist".to_string());
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq, Serialize)]
pub enum JobEventKind {
    #[serde(rename = "job.started")]
    JobStarted,
    #[serde(rename = "job.completed")]
    JobCompleted,
    #[serde(rename = "job.failed")]
    JobFailed,
    #[serde(rename = "job.cancelled")]
    JobCancelled,
    #[serde(rename = "stage.started")]
    StageStarted,
    #[serde(rename = "stage.progress")]
    StageProgress,
    #[serde(rename = "stage.heartbeat")]
    StageHeartbeat,
    #[serde(rename = "stage.completed")]
    StageCompleted,
    #[serde(rename = "stage.skipped")]
    StageSkipped,
    #[serde(rename = "stage.failed")]
    StageFailed,
    #[serde(rename = "artifact.created")]
    ArtifactCreated,
    #[serde(rename = "preview.item")]
    PreviewItem,
    #[serde(rename = "log.line")]
    LogLine,
}

impl JobEventKind {
    fn is_stage_event(self) -> bool {
        matches!(
            self,
            Self::StageStarted
                | Self::StageProgress
                | Self::StageHeartbeat
                | Self::StageCompleted
                | Self::StageSkipped
                | Self::StageFailed
        )
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct JobEvent {
    pub schema_version: u32,
    pub sequence: u64,
    pub timestamp: String,
    pub project_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub project_root: Option<String>,
    pub job_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub task_id: Option<String>,
    pub workspace: ProjectWorkspace,
    pub kind: JobEventKind,
    pub stage_id: Option<String>,
    pub track_id: Option<String>,
    pub state: JobState,
    pub current: Option<u64>,
    pub total: Option<u64>,
    pub unit: Option<String>,
    pub percent: Option<f64>,
    pub eta_seconds: Option<u64>,
    pub message: String,
    pub payload: serde_json::Value,
}

impl JobEvent {
    pub fn validate(&self) -> Result<(), String> {
        if self.schema_version != JOB_EVENT_SCHEMA_VERSION {
            return Err(format!("unsupported job event schema version: {}", self.schema_version));
        }
        if self.sequence == 0 {
            return Err("job event sequence must start at 1".to_string());
        }
        require_non_empty("projectId", &self.project_id)?;
        require_non_empty("jobId", &self.job_id)?;
        if self.kind.is_stage_event() && self.stage_id.as_deref().unwrap_or_default().is_empty() {
            return Err("stage event requires stageId".to_string());
        }
        if let Some(percent) = self.percent {
            if !percent.is_finite() || !(0.0..=100.0).contains(&percent) {
                return Err("job event percent must be between 0 and 100".to_string());
            }
        }
        if let (Some(current), Some(total)) = (self.current, self.total) {
            if current > total {
                return Err("job event current exceeds total".to_string());
            }
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ExecutionPlanNode {
    pub stage_id: String,
    pub label: String,
    pub depends_on: Vec<String>,
    pub weight: f64,
    pub progress_mode: ProgressMode,
    pub slow_hint: bool,
    pub skip_reason: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub estimated_seconds: Option<u64>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ExecutionPlan {
    pub schema_version: u32,
    pub plan_id: String,
    pub project_id: String,
    pub input_revision: u64,
    pub backend: ReconstructionBackend,
    pub created_at: String,
    pub nodes: Vec<ExecutionPlanNode>,
}

impl ExecutionPlan {
    pub fn validate(&self) -> Result<(), String> {
        if self.schema_version != EXECUTION_PLAN_SCHEMA_VERSION {
            return Err(format!("unsupported execution plan schema version: {}", self.schema_version));
        }
        require_non_empty("planId", &self.plan_id)?;
        require_non_empty("projectId", &self.project_id)?;
        if self.nodes.is_empty() {
            return Err("execution plan must contain at least one node".to_string());
        }
        let mut stage_ids = HashSet::new();
        for node in &self.nodes {
            require_non_empty("stageId", &node.stage_id)?;
            require_non_empty("stage label", &node.label)?;
            if !node.weight.is_finite() || node.weight < 0.0 {
                return Err(format!("invalid weight for stage {}", node.stage_id));
            }
            if !stage_ids.insert(node.stage_id.as_str()) {
                return Err(format!("duplicate stage id: {}", node.stage_id));
            }
        }
        for node in &self.nodes {
            for dependency in &node.depends_on {
                if dependency == &node.stage_id {
                    return Err(format!("stage {} depends on itself", node.stage_id));
                }
                if !stage_ids.contains(dependency.as_str()) {
                    return Err(format!("stage {} has unknown dependency {}", node.stage_id, dependency));
                }
            }
        }
        Ok(())
    }
}

fn require_non_empty(name: &str, value: &str) -> Result<(), String> {
    if value.trim().is_empty() {
        Err(format!("{} must not be empty", name))
    } else {
        Ok(())
    }
}

pub fn is_relative_artifact_path(value: &str) -> bool {
    let trimmed = value.trim();
    if trimmed.is_empty() || trimmed.starts_with('/') || trimmed.starts_with('\\') {
        return false;
    }
    let bytes = trimmed.as_bytes();
    if bytes.len() >= 2 && bytes[1] == b':' && bytes[0].is_ascii_alphabetic() {
        return false;
    }
    !Path::new(trimmed)
        .components()
        .any(|component| matches!(component, Component::ParentDir | Component::RootDir | Component::Prefix(_)))
}

fn require_relative_artifact_path(value: &str) -> Result<(), String> {
    if is_relative_artifact_path(value) {
        Ok(())
    } else {
        Err(format!("generated artifact path must be project-relative: {}", value))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn project_fixture_deserializes_and_validates() {
        let mut project: XpanoProjectV2 = serde_json::from_str(include_str!(
            "../../../schemas/fixtures/xpano_project_v3.example.json"
        ))
        .unwrap();
        assert_eq!(project.tracks[0].extraction.style_lut_path, None);
        project.validate().unwrap();

        project.geometry.variants[0].canonical_path = "D:/machine/points3D.bin".to_string();
        assert!(project.validate().unwrap_err().contains("project-relative"));
    }

    #[test]
    fn style_lut_is_optional_for_every_imported_track_and_migrates_legacy_paths() {
        let mut project: XpanoProjectV2 = serde_json::from_str(include_str!(
            "../../../schemas/fixtures/xpano_project_v3.example.json"
        ))
        .unwrap();
        project.tracks[0].extraction.style_lut_path = Some("camera.CUBE".to_string());
        project.validate().unwrap();

        project.tracks[0].track_type = ProjectTrackType::StandardPhotos;
        project.validate().unwrap();

        let mut legacy: serde_json::Value = serde_json::from_str(include_str!(
            "../../../schemas/fixtures/xpano_project_v3.example.json"
        ))
        .unwrap();
        legacy["tracks"][0]["extraction"]["colorLutPath"] = serde_json::json!("legacy.cube");
        let migrated: XpanoProjectV2 = serde_json::from_value(legacy).unwrap();
        assert_eq!(migrated.tracks[0].extraction.style_lut_path.as_deref(), Some("legacy.cube"));
        let serialized = serde_json::to_value(migrated).unwrap();
        assert_eq!(serialized["tracks"][0]["extraction"]["styleLutPath"], "legacy.cube");
        assert!(serialized["tracks"][0]["extraction"].get("colorLutPath").is_none());
    }

    #[test]
    fn bundled_dji_lut_preset_is_valid_only_for_osv_panorama_tracks() {
        let mut project: XpanoProjectV2 = serde_json::from_str(include_str!(
            "../../../schemas/fixtures/xpano_project_v3.example.json"
        ))
        .unwrap();
        project.tracks[0].source_path = "D:/captures/DJI_0001.OSV".to_string();
        project.tracks[0].extraction.color_lut_preset =
            Some("builtin:dji-osmo360-dlogm-rec709".to_string());
        project.validate().unwrap();

        project.tracks[0].source_path = "D:/captures/VID_0001_00_0.insv".to_string();
        assert!(project
            .validate()
            .unwrap_err()
            .contains("only valid for .osv panorama tracks"));
    }

    #[test]
    fn job_event_fixture_deserializes_and_validates() {
        let event: JobEvent = serde_json::from_str(include_str!(
            "../../../schemas/fixtures/xpano_job_event_v1.example.json"
        ))
        .unwrap();
        event.validate().unwrap();
    }

    #[test]
    fn execution_plan_fixture_deserializes_and_validates() {
        let mut plan: ExecutionPlan = serde_json::from_str(include_str!(
            "../../../schemas/fixtures/xpano_execution_plan_v1.example.json"
        ))
        .unwrap();
        plan.validate().unwrap();

        plan.nodes[1].depends_on = vec!["missing.stage".to_string()];
        assert!(plan.validate().unwrap_err().contains("unknown dependency"));
    }

    #[test]
    fn relative_artifact_path_rejects_windows_and_parent_paths() {
        assert!(is_relative_artifact_path("work/geometry/points3D.bin"));
        assert!(!is_relative_artifact_path("D:/geometry/points3D.bin"));
        assert!(!is_relative_artifact_path("../geometry/points3D.bin"));
        assert!(!is_relative_artifact_path("\\\\server\\share\\points3D.bin"));
    }
}
