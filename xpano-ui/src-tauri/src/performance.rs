use crate::contracts::{
    ExecutionPlan, JobEventKind, JobState, ReconstructionBackend, XpanoProjectV2,
};
use crate::job::{get_job_snapshots_impl, read_job_events_impl};
use crate::project::{read_project, write_json_value_atomic, ProjectCommandError};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::Path;
use std::path::PathBuf;
use tauri::Manager;

const PROFILE_SCHEMA_VERSION: u32 = 1;
const MAX_SAMPLES_PER_BUCKET: usize = 21;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct StageTimingBucket {
    backend: String,
    stage_id: String,
    input_size: String,
    pixel_scale: String,
    gpu_mode: String,
    samples_seconds: Vec<u64>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct StageTimingProfile {
    schema_version: u32,
    buckets: Vec<StageTimingBucket>,
}

impl Default for StageTimingProfile {
    fn default() -> Self {
        Self {
            schema_version: PROFILE_SCHEMA_VERSION,
            buckets: Vec::new(),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct TimingKey {
    backend: String,
    input_size: String,
    pixel_scale: String,
    gpu_mode: String,
}

fn performance_error(context: &str, error: impl std::fmt::Display) -> ProjectCommandError {
    ProjectCommandError::new("artifact_corrupt", format!("{}: {}", context, error))
}

fn backend_name(backend: ReconstructionBackend) -> &'static str {
    match backend {
        ReconstructionBackend::Metashape => "metashape",
        ReconstructionBackend::Colmap => "colmap",
    }
}

fn selected_input_count(project: &XpanoProjectV2) -> usize {
    project
        .tracks
        .iter()
        .flat_map(|track| &track.items)
        .filter(|item| item.selected)
        .count()
}

fn input_size_bucket(count: usize) -> &'static str {
    match count {
        0..=100 => "small",
        101..=500 => "medium",
        501..=2_000 => "large",
        _ => "very_large",
    }
}

fn timing_key(project: &XpanoProjectV2) -> TimingKey {
    let gpu_mode = match project.reconstruction.backend {
        ReconstructionBackend::Metashape => "auto".to_string(),
        ReconstructionBackend::Colmap => project
            .reconstruction
            .config
            .get("colmapUseGpu")
            .and_then(serde_json::Value::as_bool)
            .map_or("auto", |enabled| if enabled { "gpu" } else { "cpu" })
            .to_string(),
    };
    TimingKey {
        backend: backend_name(project.reconstruction.backend).to_string(),
        input_size: input_size_bucket(selected_input_count(project)).to_string(),
        // Pixel dimensions are not persisted in v2 media items yet; keep an explicit bucket so the profile format can refine it without migration.
        pixel_scale: project
            .reconstruction
            .config
            .get("inputPixelScale")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("unknown")
            .to_string(),
        gpu_mode,
    }
}

fn read_profile(path: &Path) -> Result<StageTimingProfile, ProjectCommandError> {
    if !path.is_file() {
        return Ok(StageTimingProfile::default());
    }
    let profile: StageTimingProfile = serde_json::from_slice(&std::fs::read(path).map_err(
        |error| performance_error("failed to read performance profile", error),
    )?)
    .map_err(|error| performance_error("failed to parse performance profile", error))?;
    if profile.schema_version != PROFILE_SCHEMA_VERSION {
        return Err(ProjectCommandError::new(
            "artifact_corrupt",
            "unsupported performance profile version",
        ));
    }
    Ok(profile)
}

fn write_profile(path: &Path, profile: &StageTimingProfile) -> Result<(), ProjectCommandError> {
    write_json_value_atomic(
        path,
        &serde_json::to_value(profile).map_err(|error| {
            performance_error("failed to serialize performance profile", error)
        })?,
    )
}

pub(crate) fn rolling_median(samples: &[u64]) -> Option<u64> {
    if samples.is_empty() {
        return None;
    }
    let mut ordered = samples.to_vec();
    ordered.sort_unstable();
    Some(ordered[ordered.len() / 2])
}

pub(crate) fn record_completed_job_impl(
    profile_path: &Path,
    project_root: &Path,
    job_id: &str,
) -> Result<(), ProjectCommandError> {
    let project = read_project(project_root)?;
    let snapshot = get_job_snapshots_impl(project_root)?
        .into_iter()
        .find(|snapshot| snapshot.job_id == job_id)
        .ok_or_else(|| ProjectCommandError::new("job_conflict", "completed job snapshot is missing"))?;
    if snapshot.state != JobState::Completed {
        return Ok(());
    }
    let events = read_job_events_impl(project_root, job_id, 0, 5_000)?;
    let mut stage_starts = HashMap::<String, chrono::DateTime<chrono::FixedOffset>>::new();
    let mut durations = Vec::<(String, u64)>::new();
    for event in events {
        let Some(stage_id) = event.stage_id.clone() else { continue };
        let timestamp = chrono::DateTime::parse_from_rfc3339(&event.timestamp).map_err(|error| {
            performance_error("job event has invalid timestamp", error)
        })?;
        match event.kind {
            JobEventKind::StageStarted => {
                stage_starts.insert(stage_id, timestamp);
            }
            JobEventKind::StageCompleted => {
                if let Some(started) = stage_starts.remove(&stage_id) {
                    durations.push((
                        stage_id,
                        (timestamp - started).num_seconds().max(1) as u64,
                    ));
                }
            }
            _ => {}
        }
    }
    if durations.is_empty() {
        return Ok(());
    }
    let key = timing_key(&project);
    let mut profile = read_profile(profile_path)?;
    for (stage_id, seconds) in durations {
        let bucket = profile.buckets.iter_mut().find(|bucket| {
            bucket.backend == key.backend
                && bucket.stage_id == stage_id
                && bucket.input_size == key.input_size
                && bucket.pixel_scale == key.pixel_scale
                && bucket.gpu_mode == key.gpu_mode
        });
        let bucket = match bucket {
            Some(bucket) => bucket,
            None => {
                profile.buckets.push(StageTimingBucket {
                    backend: key.backend.clone(),
                    stage_id: stage_id.clone(),
                    input_size: key.input_size.clone(),
                    pixel_scale: key.pixel_scale.clone(),
                    gpu_mode: key.gpu_mode.clone(),
                    samples_seconds: Vec::new(),
                });
                profile.buckets.last_mut().unwrap()
            }
        };
        bucket.samples_seconds.push(seconds);
        if bucket.samples_seconds.len() > MAX_SAMPLES_PER_BUCKET {
            let excess = bucket.samples_seconds.len() - MAX_SAMPLES_PER_BUCKET;
            bucket.samples_seconds.drain(0..excess);
        }
    }
    write_profile(profile_path, &profile)
}

pub(crate) fn apply_estimates_impl(
    profile_path: &Path,
    project_root: &Path,
    plan: &mut ExecutionPlan,
) -> Result<(), ProjectCommandError> {
    let project = read_project(project_root)?;
    let key = timing_key(&project);
    let profile = read_profile(profile_path)?;
    for node in &mut plan.nodes {
        if node.skip_reason.is_some() {
            node.estimated_seconds = None;
            continue;
        }
        let exact = profile.buckets.iter().find(|bucket| {
            bucket.backend == key.backend
                && bucket.stage_id == node.stage_id
                && bucket.input_size == key.input_size
                && bucket.pixel_scale == key.pixel_scale
                && bucket.gpu_mode == key.gpu_mode
        });
        node.estimated_seconds = exact
            .and_then(|bucket| rolling_median(&bucket.samples_seconds))
            .or_else(|| {
                let fallback = profile
                    .buckets
                    .iter()
                    .filter(|bucket| {
                        bucket.backend == key.backend
                            && bucket.stage_id == node.stage_id
                            && bucket.gpu_mode == key.gpu_mode
                    })
                    .flat_map(|bucket| bucket.samples_seconds.iter().copied())
                    .collect::<Vec<_>>();
                rolling_median(&fallback)
            });
    }
    Ok(())
}

fn profile_path(app: &tauri::AppHandle) -> Result<PathBuf, ProjectCommandError> {
    app.path()
        .app_local_data_dir()
        .map(|root| root.join("performance").join("stage_timings_v1.json"))
        .map_err(|error| performance_error("failed to resolve performance profile path", error))
}

pub(crate) fn apply_estimates_for_app(
    app: &tauri::AppHandle,
    project_root: &Path,
    plan: &mut ExecutionPlan,
) -> Result<(), ProjectCommandError> {
    apply_estimates_impl(&profile_path(app)?, project_root, plan)
}

pub(crate) fn record_completed_job_for_app(
    app: &tauri::AppHandle,
    context: &crate::job::JobContext,
) -> Result<(), ProjectCommandError> {
    record_completed_job_impl(&profile_path(app)?, context.project_root(), &context.job_id)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::contracts::{
        ExecutionPlan, ExecutionPlanNode, ProgressMode, ProjectWorkspace, ReconstructionBackend,
        XpanoProjectV2, EXECUTION_PLAN_SCHEMA_VERSION,
    };
    use crate::job::{begin_job_impl, finish_job_impl, record_progress_impl, JobProgressUpdate};
    use crate::project::write_project_atomic;
    use std::path::PathBuf;

    fn temp_case(name: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!(
            "xpano-performance-{}-{}-{}",
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
    fn completed_job_updates_profile_and_estimates_future_plan() {
        let root = temp_case("profile");
        let profile_path = root.join("user-data").join("stage_timings_v1.json");
        let project_root = root.join("project");
        write_project_atomic(&project_root, &fixture_project()).unwrap();
        let (context, started) = begin_job_impl(&project_root, ProjectWorkspace::Reconstruction).unwrap();
        record_progress_impl(
            &context,
            JobProgressUpdate {
                stage_id: Some("input.validate".to_string()),
                percent: Some(10.0),
                message: "正在校验".to_string(),
                ..Default::default()
            },
        )
        .unwrap();
        finish_job_impl(&context, crate::contracts::JobState::Completed, "完成").unwrap();

        record_completed_job_impl(&profile_path, &project_root, &started.job_id).unwrap();
        let mut plan = ExecutionPlan {
            schema_version: EXECUTION_PLAN_SCHEMA_VERSION,
            plan_id: "plan-1".to_string(),
            project_id: fixture_project().project_id,
            input_revision: 1,
            backend: ReconstructionBackend::Metashape,
            created_at: "2026-07-11T00:00:00.000Z".to_string(),
            nodes: vec![ExecutionPlanNode {
                stage_id: "input.validate".to_string(),
                label: "校验输入".to_string(),
                depends_on: vec![],
                weight: 1.0,
                progress_mode: ProgressMode::Counted,
                slow_hint: false,
                skip_reason: None,
                estimated_seconds: None,
            }],
        };

        apply_estimates_impl(&profile_path, &project_root, &mut plan).unwrap();

        assert!(plan.nodes[0].estimated_seconds.is_some_and(|seconds| seconds >= 1));
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn rolling_median_resists_a_single_slow_outlier() {
        assert_eq!(rolling_median(&[10, 11, 12, 13, 200]), Some(12));
    }
}
