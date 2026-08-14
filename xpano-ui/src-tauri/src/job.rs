use crate::contracts::{
    JobEvent, JobEventKind, JobSnapshot, JobState, ProjectWorkspace, JOB_EVENT_SCHEMA_VERSION,
};
use crate::project::{
    read_project, touch_project, write_json_value_atomic, write_project_atomic, ProjectCommandError,
};
use chrono::{SecondsFormat, Utc};
use serde::{Deserialize, Serialize};
use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use uuid::Uuid;

const JOBS_RELATIVE_PATH: &str = "work/jobs";
const MAX_EVENT_READ_LIMIT: usize = 5_000;
static JOB_IO_LOCK: Mutex<()> = Mutex::new(());

#[derive(Clone, Debug)]
pub(crate) struct JobContext {
    project_root: PathBuf,
    project_id: String,
    pub(crate) job_id: String,
    workspace: ProjectWorkspace,
    pub(crate) task_id: Option<String>,
}

impl JobContext {
    pub(crate) fn belongs_to(&self, project_root: &Path) -> bool {
        self.project_root == project_root
    }

    pub(crate) fn matches(&self, project_root: &Path, job_id: &str) -> bool {
        self.job_id == job_id && self.belongs_to(project_root)
    }

    pub(crate) fn project_root(&self) -> &Path {
        &self.project_root
    }
}

#[derive(Clone, Debug, Default)]
pub(crate) struct JobProgressUpdate {
    pub stage_id: Option<String>,
    pub current: Option<u64>,
    pub total: Option<u64>,
    pub percent: Option<f64>,
    pub eta_seconds: Option<u64>,
    pub message: String,
    pub heartbeat: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct JobRecovery {
    pub snapshots: Vec<JobSnapshot>,
    pub events: Vec<JobEvent>,
}

fn now_iso8601() -> String {
    Utc::now().to_rfc3339_opts(SecondsFormat::Millis, true)
}

fn job_error(code: &str, context: &str, error: impl std::fmt::Display) -> ProjectCommandError {
    ProjectCommandError::new(code, format!("{}: {}", context, error))
}

fn validate_job_id(job_id: &str) -> Result<(), ProjectCommandError> {
    if job_id.trim().is_empty()
        || job_id.contains('/')
        || job_id.contains('\\')
        || job_id == "."
        || job_id == ".."
    {
        return Err(ProjectCommandError::new("invalid_project", "invalid job id"));
    }
    Ok(())
}

fn job_dir(project_root: &Path, job_id: &str) -> PathBuf {
    project_root.join(JOBS_RELATIVE_PATH).join(job_id)
}

fn snapshot_path(project_root: &Path, job_id: &str) -> PathBuf {
    job_dir(project_root, job_id).join("snapshot.json")
}

fn events_path(project_root: &Path, job_id: &str) -> PathBuf {
    job_dir(project_root, job_id).join("events.ndjson")
}

fn log_path(project_root: &Path, job_id: &str) -> PathBuf {
    job_dir(project_root, job_id).join("job.log")
}

fn read_snapshot(project_root: &Path, job_id: &str) -> Result<JobSnapshot, ProjectCommandError> {
    validate_job_id(job_id)?;
    let path = snapshot_path(project_root, job_id);
    serde_json::from_slice(&std::fs::read(&path).map_err(|error| {
        job_error("artifact_corrupt", "failed to read job snapshot", error)
    })?)
    .map_err(|error| job_error("artifact_corrupt", "failed to parse job snapshot", error))
}

fn write_snapshot(project_root: &Path, snapshot: &JobSnapshot) -> Result<(), ProjectCommandError> {
    write_json_value_atomic(
        &snapshot_path(project_root, &snapshot.job_id),
        &serde_json::to_value(snapshot).map_err(|error| {
            job_error("artifact_corrupt", "failed to serialize job snapshot", error)
        })?,
    )
}

fn append_event(project_root: &Path, event: &JobEvent) -> Result<(), ProjectCommandError> {
    event
        .validate()
        .map_err(|error| ProjectCommandError::new("artifact_corrupt", error))?;
    let path = events_path(project_root, &event.job_id);
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .map_err(|error| job_error("artifact_corrupt", "failed to open job event log", error))?;
    let payload = serde_json::to_vec(event).map_err(|error| {
        job_error("artifact_corrupt", "failed to serialize job event", error)
    })?;
    file.write_all(&payload)
        .and_then(|_| file.write_all(b"\n"))
        .and_then(|_| file.sync_data())
        .map_err(|error| job_error("disk_full", "failed to persist job event", error))
}

fn append_log(project_root: &Path, job_id: &str, message: &str) -> Result<(), ProjectCommandError> {
    if message.trim().is_empty() {
        return Ok(());
    }
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(log_path(project_root, job_id))
        .map_err(|error| job_error("artifact_corrupt", "failed to open job log", error))?;
    file.write_all(format!("{} {}\n", now_iso8601(), message.trim()).as_bytes())
        .and_then(|_| file.sync_data())
        .map_err(|error| job_error("disk_full", "failed to persist job log", error))
}

fn event_for(
    context: &JobContext,
    sequence: u64,
    kind: JobEventKind,
    state: JobState,
    stage_id: Option<String>,
    update: Option<&JobProgressUpdate>,
    message: &str,
) -> JobEvent {
    JobEvent {
        schema_version: JOB_EVENT_SCHEMA_VERSION,
        sequence,
        timestamp: now_iso8601(),
        project_id: context.project_id.clone(),
        job_id: context.job_id.clone(),
        project_root: Some(context.project_root.to_string_lossy().to_string()),
        task_id: context.task_id.clone(),
        workspace: context.workspace,
        kind,
        stage_id,
        track_id: None,
        state,
        current: update.and_then(|value| value.current),
        total: update.and_then(|value| value.total),
        unit: None,
        percent: update.and_then(|value| value.percent),
        eta_seconds: update.and_then(|value| value.eta_seconds),
        message: message.to_string(),
        payload: serde_json::json!({}),
    }
}

fn replace_project_snapshot(
    project_root: &Path,
    snapshot: &JobSnapshot,
) -> Result<(), ProjectCommandError> {
    let mut project = read_project(project_root)?;
    let stored = project
        .jobs
        .iter_mut()
        .find(|job| job.job_id == snapshot.job_id)
        .ok_or_else(|| ProjectCommandError::new("job_conflict", "job is not registered in project"))?;
    if stored == snapshot {
        return Ok(());
    }
    *stored = snapshot.clone();
    project.revision += 1;
    touch_project(&mut project);
    write_project_atomic(project_root, &project)
}

#[cfg(test)]
pub(crate) fn begin_job_impl(
    project_root: &Path,
    workspace: ProjectWorkspace,
) -> Result<(JobContext, JobSnapshot), ProjectCommandError> {
    begin_job_with_task_impl(project_root, workspace, None)
}

pub(crate) fn begin_job_with_task_impl(
    project_root: &Path,
    workspace: ProjectWorkspace,
    task_id: Option<String>,
) -> Result<(JobContext, JobSnapshot), ProjectCommandError> {
    let _guard = JOB_IO_LOCK
        .lock()
        .map_err(|error| ProjectCommandError::new("job_conflict", error.to_string()))?;
    let mut project = read_project(project_root)?;
    if project.jobs.iter().any(|job| {
        matches!(job.state, JobState::Queued | JobState::Running | JobState::Cancelling)
    }) {
        return Err(ProjectCommandError::new(
            "job_conflict",
            "another project job is already active",
        ));
    }
    let timestamp = now_iso8601();
    let context = JobContext {
        project_root: project_root.to_path_buf(),
        project_id: project.project_id.clone(),
        job_id: Uuid::new_v4().to_string(),
        workspace,
        task_id,
    };
    let snapshot = JobSnapshot {
        job_id: context.job_id.clone(),
        project_root: Some(context.project_root.to_string_lossy().to_string()),
        task_id: context.task_id.clone(),
        workspace,
        state: JobState::Running,
        stage_id: None,
        sequence: 1,
        started_at: timestamp.clone(),
        updated_at: timestamp,
    };
    let directory = job_dir(project_root, &snapshot.job_id);
    std::fs::create_dir_all(&directory).map_err(|error| {
        job_error("artifact_corrupt", "failed to create job directory", error)
    })?;
    let started = event_for(
        &context,
        1,
        JobEventKind::JobStarted,
        JobState::Running,
        None,
        None,
        "任务已启动",
    );
    let persisted = (|| {
        append_event(project_root, &started)?;
        append_log(project_root, &snapshot.job_id, &started.message)?;
        write_snapshot(project_root, &snapshot)?;
        project.jobs.push(snapshot.clone());
        project.revision += 1;
        touch_project(&mut project);
        write_project_atomic(project_root, &project)
    })();
    if persisted.is_err() {
        let _ = std::fs::remove_dir_all(directory);
    }
    persisted?;
    Ok((context, snapshot))
}

pub(crate) fn record_progress_impl(
    context: &JobContext,
    update: JobProgressUpdate,
) -> Result<Vec<JobEvent>, ProjectCommandError> {
    let _guard = JOB_IO_LOCK
        .lock()
        .map_err(|error| ProjectCommandError::new("job_conflict", error.to_string()))?;
    let mut snapshot = read_snapshot(&context.project_root, &context.job_id)?;
    if !matches!(snapshot.state, JobState::Running | JobState::Cancelling) {
        return Err(ProjectCommandError::new("job_conflict", "job is no longer active"));
    }
    let stage_id = update.stage_id.clone().or_else(|| snapshot.stage_id.clone());
    let mut events = Vec::new();
    if stage_id != snapshot.stage_id {
        if let Some(previous_stage) = snapshot.stage_id.clone() {
            snapshot.sequence += 1;
            events.push(event_for(
                context,
                snapshot.sequence,
                JobEventKind::StageCompleted,
                snapshot.state,
                Some(previous_stage),
                None,
                "阶段完成",
            ));
        }
        if let Some(next_stage) = stage_id.clone() {
            snapshot.sequence += 1;
            events.push(event_for(
                context,
                snapshot.sequence,
                JobEventKind::StageStarted,
                snapshot.state,
                Some(next_stage),
                None,
                &update.message,
            ));
        }
        snapshot.stage_id = stage_id.clone();
    }
    snapshot.sequence += 1;
    let kind = if update.heartbeat && stage_id.is_some() {
        JobEventKind::StageHeartbeat
    } else if stage_id.is_some() {
        JobEventKind::StageProgress
    } else {
        JobEventKind::LogLine
    };
    events.push(event_for(
        context,
        snapshot.sequence,
        kind,
        snapshot.state,
        stage_id,
        Some(&update),
        &update.message,
    ));
    snapshot.updated_at = now_iso8601();
    for event in &events {
        append_event(&context.project_root, event)?;
    }
    if !update.heartbeat {
        append_log(&context.project_root, &context.job_id, &update.message)?;
    }
    write_snapshot(&context.project_root, &snapshot)?;
    Ok(events)
}

pub(crate) fn record_log_impl(
    context: &JobContext,
    message: &str,
) -> Result<JobEvent, ProjectCommandError> {
    let _guard = JOB_IO_LOCK
        .lock()
        .map_err(|error| ProjectCommandError::new("job_conflict", error.to_string()))?;
    let mut snapshot = read_snapshot(&context.project_root, &context.job_id)?;
    if !matches!(snapshot.state, JobState::Running | JobState::Cancelling) {
        return Err(ProjectCommandError::new("job_conflict", "job is no longer active"));
    }
    snapshot.sequence += 1;
    snapshot.updated_at = now_iso8601();
    let event = event_for(
        context,
        snapshot.sequence,
        JobEventKind::LogLine,
        snapshot.state,
        snapshot.stage_id.clone(),
        None,
        message,
    );
    append_event(&context.project_root, &event)?;
    append_log(&context.project_root, &context.job_id, message)?;
    write_snapshot(&context.project_root, &snapshot)?;
    Ok(event)
}

pub(crate) fn record_skipped_stage_impl(
    context: &JobContext,
    stage_id: &str,
    reason: &str,
) -> Result<JobEvent, ProjectCommandError> {
    if stage_id.trim().is_empty() {
        return Err(ProjectCommandError::new("invalid_project", "skipped stage id is empty"));
    }
    let _guard = JOB_IO_LOCK
        .lock()
        .map_err(|error| ProjectCommandError::new("job_conflict", error.to_string()))?;
    let mut snapshot = read_snapshot(&context.project_root, &context.job_id)?;
    if snapshot.state != JobState::Running {
        return Err(ProjectCommandError::new("job_conflict", "job is not running"));
    }
    snapshot.sequence += 1;
    snapshot.updated_at = now_iso8601();
    let event = event_for(
        context,
        snapshot.sequence,
        JobEventKind::StageSkipped,
        JobState::Skipped,
        Some(stage_id.to_string()),
        None,
        reason,
    );
    append_event(&context.project_root, &event)?;
    append_log(&context.project_root, &context.job_id, reason)?;
    write_snapshot(&context.project_root, &snapshot)?;
    Ok(event)
}

pub(crate) fn mark_job_cancelling_impl(
    context: &JobContext,
) -> Result<JobSnapshot, ProjectCommandError> {
    let _guard = JOB_IO_LOCK
        .lock()
        .map_err(|error| ProjectCommandError::new("job_conflict", error.to_string()))?;
    let mut snapshot = read_snapshot(&context.project_root, &context.job_id)?;
    if snapshot.state != JobState::Running {
        return Err(ProjectCommandError::new("job_conflict", "job is not running"));
    }
    snapshot.state = JobState::Cancelling;
    snapshot.sequence += 1;
    snapshot.updated_at = now_iso8601();
    let event = event_for(
        context,
        snapshot.sequence,
        JobEventKind::LogLine,
        JobState::Cancelling,
        snapshot.stage_id.clone(),
        None,
        "正在取消任务",
    );
    append_event(&context.project_root, &event)?;
    append_log(&context.project_root, &context.job_id, &event.message)?;
    write_snapshot(&context.project_root, &snapshot)?;
    replace_project_snapshot(&context.project_root, &snapshot)?;
    Ok(snapshot)
}

pub(crate) fn finish_job_impl(
    context: &JobContext,
    state: JobState,
    message: &str,
) -> Result<JobSnapshot, ProjectCommandError> {
    if !matches!(
        state,
        JobState::Completed | JobState::Failed | JobState::Cancelled | JobState::Interrupted
    ) {
        return Err(ProjectCommandError::new("invalid_project", "invalid terminal job state"));
    }
    let _guard = JOB_IO_LOCK
        .lock()
        .map_err(|error| ProjectCommandError::new("job_conflict", error.to_string()))?;
    let mut snapshot = read_snapshot(&context.project_root, &context.job_id)?;
    if matches!(
        snapshot.state,
        JobState::Completed | JobState::Failed | JobState::Cancelled | JobState::Interrupted
    ) {
        replace_project_snapshot(&context.project_root, &snapshot)?;
        return Ok(snapshot);
    }
    if let Some(stage_id) = snapshot.stage_id.clone() {
        snapshot.sequence += 1;
        let stage_kind = if state == JobState::Completed {
            JobEventKind::StageCompleted
        } else {
            JobEventKind::StageFailed
        };
        append_event(
            &context.project_root,
            &event_for(
                context,
                snapshot.sequence,
                stage_kind,
                state,
                Some(stage_id),
                None,
                message,
            ),
        )?;
    }
    snapshot.state = state;
    snapshot.sequence += 1;
    snapshot.updated_at = now_iso8601();
    let job_kind = match state {
        JobState::Completed => JobEventKind::JobCompleted,
        JobState::Cancelled => JobEventKind::JobCancelled,
        JobState::Failed | JobState::Interrupted => JobEventKind::JobFailed,
        _ => unreachable!(),
    };
    append_event(
        &context.project_root,
        &event_for(
            context,
            snapshot.sequence,
            job_kind,
            state,
            snapshot.stage_id.clone(),
            None,
            message,
        ),
    )?;
    append_log(&context.project_root, &context.job_id, message)?;
    write_snapshot(&context.project_root, &snapshot)?;
    replace_project_snapshot(&context.project_root, &snapshot)?;
    Ok(snapshot)
}

pub(crate) fn get_job_snapshots_impl(
    project_root: &Path,
) -> Result<Vec<JobSnapshot>, ProjectCommandError> {
    let _guard = JOB_IO_LOCK
        .lock()
        .map_err(|error| ProjectCommandError::new("job_conflict", error.to_string()))?;
    let project = read_project(project_root)?;
    project
        .jobs
        .iter()
        .map(|stored| {
            let path = snapshot_path(project_root, &stored.job_id);
            if path.is_file() {
                read_snapshot(project_root, &stored.job_id)
            } else {
                Ok(stored.clone())
            }
        })
        .collect()
}

pub(crate) fn read_job_events_impl(
    project_root: &Path,
    job_id: &str,
    after_sequence: u64,
    limit: usize,
) -> Result<Vec<JobEvent>, ProjectCommandError> {
    validate_job_id(job_id)?;
    let project = read_project(project_root)?;
    if !project.jobs.iter().any(|job| job.job_id == job_id) {
        return Err(ProjectCommandError::new("job_conflict", "job is not registered in project"));
    }
    let path = events_path(project_root, job_id);
    let file = File::open(&path).map_err(|error| {
        job_error("artifact_corrupt", "failed to open job event log", error)
    })?;
    let mut events = Vec::new();
    for line in BufReader::new(file).lines() {
        let line = line.map_err(|error| {
            job_error("artifact_corrupt", "failed to read job event log", error)
        })?;
        if line.trim().is_empty() {
            continue;
        }
        let event: JobEvent = serde_json::from_str(&line).map_err(|error| {
            job_error("artifact_corrupt", "failed to parse job event", error)
        })?;
        event
            .validate()
            .map_err(|error| ProjectCommandError::new("artifact_corrupt", error))?;
        if event.project_id != project.project_id || event.job_id != job_id {
            return Err(ProjectCommandError::new(
                "artifact_corrupt",
                "job event belongs to a different project or job",
            ));
        }
        if event.sequence > after_sequence {
            events.push(event);
            if events.len() >= limit.clamp(1, MAX_EVENT_READ_LIMIT) {
                break;
            }
        }
    }
    Ok(events)
}

pub(crate) fn recovery_impl(
    project_root: &Path,
    after_sequence: u64,
) -> Result<JobRecovery, ProjectCommandError> {
    let snapshots = get_job_snapshots_impl(project_root)?;
    let mut events = Vec::new();
    if let Some(latest) = snapshots.last() {
        events = read_job_events_impl(
            project_root,
            &latest.job_id,
            after_sequence,
            MAX_EVENT_READ_LIMIT,
        )?;
    }
    Ok(JobRecovery { snapshots, events })
}

pub(crate) fn recover_orphaned_jobs_impl(
    project_root: &Path,
    active_job_id: Option<&str>,
) -> Result<Vec<JobSnapshot>, ProjectCommandError> {
    let project = read_project(project_root)?;
    let orphaned = project
        .jobs
        .iter()
        .filter(|snapshot| {
            matches!(snapshot.state, JobState::Queued | JobState::Running | JobState::Cancelling)
                && active_job_id != Some(snapshot.job_id.as_str())
        })
        .cloned()
        .collect::<Vec<_>>();
    for snapshot in orphaned {
        let context = JobContext {
            project_root: project_root.to_path_buf(),
            project_id: project.project_id.clone(),
            job_id: snapshot.job_id,
            workspace: snapshot.workspace,
            task_id: snapshot.task_id.clone(),
        };
        finish_job_impl(&context, JobState::Interrupted, "应用退出后任务已中断")?;
    }
    get_job_snapshots_impl(project_root)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::contracts::{JobEventKind, JobState, ProjectWorkspace, XpanoProjectV2};
    use crate::project::{read_project, write_project_atomic};
    use std::path::PathBuf;

    fn temp_case(name: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!(
            "xpano-job-{}-{}-{}",
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
    fn durable_job_records_ordered_events_and_survives_reload() {
        let root = temp_case("durable");
        let project = fixture_project();
        write_project_atomic(&root, &project).unwrap();

        let (context, started) = begin_job_impl(&root, ProjectWorkspace::Reconstruction).unwrap();
        record_progress_impl(
            &context,
            JobProgressUpdate {
                stage_id: Some("input.validate".to_string()),
                current: Some(1),
                total: Some(4),
                percent: Some(25.0),
                eta_seconds: Some(3),
                message: "正在校验输入".to_string(),
                heartbeat: false,
            },
        )
        .unwrap();
        let completed = finish_job_impl(&context, JobState::Completed, "对齐完成").unwrap();

        let snapshots = get_job_snapshots_impl(&root).unwrap();
        let events = read_job_events_impl(&root, &started.job_id, 0, 100).unwrap();

        assert_eq!(snapshots, vec![completed.clone()]);
        assert_eq!(completed.state, JobState::Completed);
        assert_eq!(events[0].kind, JobEventKind::JobStarted);
        assert_eq!(events[1].kind, JobEventKind::StageStarted);
        assert_eq!(events[2].kind, JobEventKind::StageProgress);
        assert_eq!(events[3].kind, JobEventKind::StageCompleted);
        assert_eq!(events[4].kind, JobEventKind::JobCompleted);
        assert_eq!(events.iter().map(|event| event.sequence).collect::<Vec<_>>(), vec![1, 2, 3, 4, 5]);
        assert_eq!(read_project(&root).unwrap().jobs, vec![completed]);
        assert!(root.join("work/jobs").join(&started.job_id).join("snapshot.json").is_file());
        assert!(root.join("work/jobs").join(&started.job_id).join("events.ndjson").is_file());
        assert!(root.join("work/jobs").join(&started.job_id).join("job.log").is_file());
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn cancelling_job_is_persisted_before_process_exit() {
        let root = temp_case("cancelling");
        write_project_atomic(&root, &fixture_project()).unwrap();
        let (context, started) = begin_job_impl(&root, ProjectWorkspace::Reconstruction).unwrap();

        let cancelling = mark_job_cancelling_impl(&context).unwrap();

        assert_eq!(cancelling.state, JobState::Cancelling);
        assert!(cancelling.sequence > started.sequence);
        assert_eq!(get_job_snapshots_impl(&root).unwrap(), vec![cancelling]);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn batch_job_events_keep_project_and_task_identity() {
        let root = temp_case("identity");
        write_project_atomic(&root, &fixture_project()).unwrap();
        let (context, started) = begin_job_with_task_impl(&root, ProjectWorkspace::Media, Some("batch-task-1".to_string())).unwrap();
        assert_eq!(started.project_root.as_deref(), Some(root.to_string_lossy().as_ref()));
        assert_eq!(started.task_id.as_deref(), Some("batch-task-1"));
        let event = record_log_impl(&context, "batch progress").unwrap();
        assert_eq!(event.project_root.as_deref(), Some(root.to_string_lossy().as_ref()));
        assert_eq!(event.task_id.as_deref(), Some("batch-task-1"));
        let events = read_job_events_impl(&root, &started.job_id, 0, 100).unwrap();
        assert!(events.iter().all(|item| item.project_root.as_deref() == Some(root.to_string_lossy().as_ref())));
        assert!(events.iter().all(|item| item.task_id.as_deref() == Some("batch-task-1")));
        assert_eq!(events.last(), Some(&event));
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn recovery_marks_jobs_without_a_live_supervisor_as_interrupted() {
        let root = temp_case("orphaned");
        write_project_atomic(&root, &fixture_project()).unwrap();
        let (_context, started) = begin_job_impl(&root, ProjectWorkspace::Reconstruction).unwrap();

        let recovered = recover_orphaned_jobs_impl(&root, None).unwrap();

        assert_eq!(recovered.len(), 1);
        assert_eq!(recovered[0].job_id, started.job_id);
        assert_eq!(recovered[0].state, JobState::Interrupted);
        let events = read_job_events_impl(&root, &started.job_id, 0, 100).unwrap();
        assert_eq!(events.last().unwrap().kind, JobEventKind::JobFailed);
        assert_eq!(events.last().unwrap().state, JobState::Interrupted);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn terminal_snapshot_repairs_a_stale_running_project_index() {
        let root = temp_case("terminal-repair");
        write_project_atomic(&root, &fixture_project()).unwrap();
        let (context, _) = begin_job_impl(&root, ProjectWorkspace::Reconstruction).unwrap();
        let completed = finish_job_impl(&context, JobState::Completed, "任务已完成").unwrap();
        let mut stale_project = read_project(&root).unwrap();
        stale_project.jobs[0].state = JobState::Running;
        stale_project.jobs[0].sequence = 1;
        write_project_atomic(&root, &stale_project).unwrap();

        let repeated = finish_job_impl(&context, JobState::Completed, "任务已完成").unwrap();

        assert_eq!(repeated, completed);
        assert_eq!(read_project(&root).unwrap().jobs, vec![completed]);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn skipped_execution_plan_nodes_are_durable_job_events() {
        let root = temp_case("skipped");
        write_project_atomic(&root, &fixture_project()).unwrap();
        let (context, _) = begin_job_impl(&root, ProjectWorkspace::Reconstruction).unwrap();

        let skipped = record_skipped_stage_impl(
            &context,
            "metashape.frame.import",
            "No flat-frame media selected",
        )
        .unwrap();

        assert_eq!(skipped.kind, JobEventKind::StageSkipped);
        assert_eq!(skipped.state, JobState::Skipped);
        assert_eq!(skipped.stage_id.as_deref(), Some("metashape.frame.import"));
        let events = read_job_events_impl(&root, &context.job_id, 0, 100).unwrap();
        assert_eq!(events.last(), Some(&skipped));
        let _ = std::fs::remove_dir_all(root);
    }
}
