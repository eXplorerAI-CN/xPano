use serde::{Deserialize, Serialize};
use std::io::{BufRead, BufReader};
use std::process::{Command, Stdio};
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

/// Last emitted general log line — only emit when content actually changes.
static LAST_LOG_LINE: std::sync::Mutex<Option<String>> = std::sync::Mutex::new(None);
use tauri::{AppHandle, Emitter, Manager};

use crate::contracts::ProjectMediaItem;
use crate::process_job::ProcessJob;

fn configure_metashape_runtime(command: &mut Command, site_packages: Option<&str>) {
    if let Some(site_packages) = site_packages.filter(|value| !value.trim().is_empty()) {
        // WARN: Some Metashape builds filter custom environment variables; the CLI path is authoritative.
        command
            .env("XPANO_METASHAPE_SITE_PACKAGES", site_packages)
            .arg("--metashape-site-packages")
            .arg(site_packages);
    }
}

fn is_training_supervisor_environment_override(name: &std::ffi::OsStr) -> bool {
    let name = name.to_string_lossy().to_ascii_uppercase();
    matches!(
        name.as_str(),
        "CONDA_DEFAULT_ENV"
            | "CONDA_PREFIX"
            | "PYTHONHOME"
            | "PYTHONPATH"
            | "PYTHONUSERBASE"
            | "QT_PLUGIN_PATH"
            | "QT_QPA_PLATFORM_PLUGIN_PATH"
            | "VIRTUAL_ENV"
            | "XPANO_PYTHON"
            | "XPANO_ROOT"
            | "CUDA_HOME"
            | "CUDA_ROOT"
    ) || name.starts_with("CUDA_PATH")
        || name.starts_with("VULKAN_")
        || name.starts_with("VK_")
}

pub(crate) fn configure_training_supervisor_environment(command: &mut Command, python: &Path) {
    for name in [
        "CONDA_DEFAULT_ENV",
        "CONDA_PREFIX",
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONUSERBASE",
        "QT_PLUGIN_PATH",
        "QT_QPA_PLATFORM_PLUGIN_PATH",
        "VIRTUAL_ENV",
        "XPANO_PYTHON",
        "XPANO_ROOT",
        "CUDA_PATH",
        "VULKAN_SDK",
        "VK_ADD_DRIVER_FILES",
        "VK_ADD_LAYER_PATH",
        "VK_DRIVER_FILES",
        "VK_ICD_FILENAMES",
        "VK_LAYER_PATH",
    ] {
        command.env_remove(name);
    }
    for (name, _) in std::env::vars_os() {
        if is_training_supervisor_environment_override(&name) {
            command.env_remove(name);
        }
    }
    let system_root = std::env::var_os("SystemRoot")
        .map(std::path::PathBuf::from)
        .filter(|path| !path.as_os_str().is_empty())
        .unwrap_or_else(|| std::path::PathBuf::from(r"C:\Windows"));
    let python_dir = python.parent().unwrap_or_else(|| Path::new("."));
    let system32 = system_root.join("System32");
    let path_separator = if cfg!(windows) { ";" } else { ":" };
    let search_path = [python_dir, system32.as_path(), system_root.as_path()]
    .iter()
    .map(|path| path.to_string_lossy())
    .collect::<Vec<_>>()
    .join(path_separator);
    command
        .env("PATH", search_path)
        .env("SystemRoot", &system_root)
        .env("WINDIR", &system_root)
        .env("ComSpec", system_root.join("System32").join("cmd.exe"));
}

fn find_media_project_root(args: &[String]) -> Option<String> {
    args.windows(2).find_map(|pair| {
        (pair[0] == "--project-root").then(|| pair[1].clone())
    })
}

fn settle_media_project(
    app: &AppHandle,
    project_root: Option<&str>,
    succeeded: bool,
) -> Result<(), String> {
    let project_root = project_root.ok_or_else(|| "media job did not include a project root".to_string())?;
    let project = if succeeded {
        crate::media::sync_media_job_result_impl(Path::new(project_root))
    } else {
        crate::media::fail_media_job_impl(Path::new(project_root))
    }
    .map_err(|error| error.message)?;
    let _ = app.emit(
        "project:updated",
        crate::media::ProjectUpdatedEvent {
            project_root: project_root.to_string(),
            project,
        },
    );
    Ok(())
}

fn update_reconstruction_project(
    app: &AppHandle,
    project_root: &str,
    succeeded: Option<bool>,
) -> Result<(), String> {
    if project_root.trim().is_empty() {
        return Err("reconstruction job did not include an output project root".to_string());
    }
    let root = Path::new(project_root);
    let project = match succeeded {
        None => crate::reconstruction::begin_reconstruction_job_impl(root),
        Some(true) => crate::reconstruction::finalize_reconstruction_job_impl(root),
        Some(false) => crate::reconstruction::fail_reconstruction_job_impl(root),
    }
    .map_err(|error| error.message)?;
    let _ = app.emit(
        "project:updated",
        crate::media::ProjectUpdatedEvent {
            project_root: project_root.to_string(),
            project,
        },
    );
    Ok(())
}

fn interrupt_reconstruction_project(app: &AppHandle, project_root: &str) -> Result<(), String> {
    if project_root.trim().is_empty() {
        return Err("reconstruction job did not include an output project root".to_string());
    }
    let project = crate::reconstruction::interrupt_reconstruction_job_impl(Path::new(project_root))
        .map_err(|error| error.message)?;
    let _ = app.emit(
        "project:updated",
        crate::media::ProjectUpdatedEvent {
            project_root: project_root.to_string(),
            project,
        },
    );
    Ok(())
}

fn update_training_project(
    app: &AppHandle,
    project_root: &str,
    succeeded: Option<bool>,
    error: Option<&str>,
) -> Result<(), String> {
    if project_root.trim().is_empty() {
        return Err("training job did not include a project root".to_string());
    }
    let root = Path::new(project_root);
    let project = match succeeded {
        Some(true) => crate::training::finalize_training_job_impl(root),
        Some(false) => crate::training::fail_training_job_impl(
            root,
            error.unwrap_or("LichtFeld training failed"),
        ),
        None => crate::training::interrupt_training_job_impl(root),
    }
    .map_err(|error| error.message)?;
    let _ = app.emit(
        "project:updated",
        crate::media::ProjectUpdatedEvent {
            project_root: project_root.to_string(),
            project,
        },
    );
    Ok(())
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PipelineProgressEvent {
    pub phase: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stage: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub track_id: Option<String>,
    pub percent: f64,
    pub message: String,
    pub elapsed: u64,
    pub phase_percents: PhasePercents,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub current: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub total: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub eta_seconds: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub aligned_cameras: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub total_cameras: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub alignment_rate: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub loss: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub splat_count: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub trainer_state: Option<String>,
    pub heartbeat: bool,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PhasePercents {
    pub extract: f64,
    pub align: f64,
    pub export: f64,
}

#[derive(Deserialize)]
struct StructuredPipelineEvent {
    phase: Option<String>,
    stage: Option<String>,
    #[serde(default, alias = "trackId")]
    track_id: Option<String>,
    percent: Option<f64>,
    #[serde(default, alias = "phasePercent")]
    phase_percent: Option<f64>,
    #[serde(default, alias = "phasePercents")]
    phase_percents: Option<PhasePercents>,
    message: Option<String>,
    current: Option<u64>,
    total: Option<u64>,
    #[serde(default, alias = "etaSeconds")]
    eta_seconds: Option<u64>,
    #[serde(default, alias = "alignedCameras")]
    aligned_cameras: Option<u64>,
    #[serde(default, alias = "totalCameras")]
    total_cameras: Option<u64>,
    #[serde(default, alias = "alignmentRate")]
    alignment_rate: Option<f64>,
    loss: Option<f64>,
    #[serde(default, alias = "splatCount")]
    splat_count: Option<u64>,
    #[serde(default, alias = "trainerState")]
    trainer_state: Option<String>,
    #[serde(default)]
    heartbeat: bool,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PipelineCompleteEvent {
    pub output_path: String,
    pub job_kind: String,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PipelineErrorEvent {
    pub error: String,
    pub job_kind: String,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PipelineMediaItemEvent {
    pub track_id: String,
    pub item: ProjectMediaItem,
}

pub struct PipelineState {
    pid: Option<u32>,
    cancelled: Option<Arc<AtomicBool>>,
    job: Option<ProcessJob>,
    active_job: Option<crate::job::JobContext>,
}

const PROGRESS_EXTRACT_START: f64 = 4.0;
const PROGRESS_EXTRACT_END: f64 = 30.0;
const PROGRESS_ALIGN_END: f64 = 86.0;
const PROGRESS_EXPORT_END: f64 = 100.0;

fn phase_for_progress(pct: f64) -> &'static str {
    if pct < PROGRESS_EXTRACT_END {
        "extract"
    } else if pct < PROGRESS_ALIGN_END {
        "align"
    } else {
        "export"
    }
}

fn phase_percents(pct: f64) -> PhasePercents {
    let extract = if pct <= PROGRESS_EXTRACT_START {
        0.0
    } else {
        ((pct - PROGRESS_EXTRACT_START) / (PROGRESS_EXTRACT_END - PROGRESS_EXTRACT_START) * 100.0)
            .clamp(0.0, 100.0)
    };
    let align = if pct <= PROGRESS_EXTRACT_END {
        0.0
    } else {
        ((pct - PROGRESS_EXTRACT_END) / (PROGRESS_ALIGN_END - PROGRESS_EXTRACT_END) * 100.0)
            .clamp(0.0, 100.0)
    };
    let export = if pct <= PROGRESS_ALIGN_END {
        0.0
    } else {
        ((pct - PROGRESS_ALIGN_END) / (PROGRESS_EXPORT_END - PROGRESS_ALIGN_END) * 100.0)
            .clamp(0.0, 100.0)
    };
    PhasePercents {
        extract,
        align,
        export,
    }
}

fn phase_percent_from_overall(phase: &str, percent: f64) -> f64 {
    match phase {
        "extract" => ((percent - PROGRESS_EXTRACT_START)
            / (PROGRESS_EXTRACT_END - PROGRESS_EXTRACT_START)
            * 100.0)
            .clamp(0.0, 100.0),
        "align" => ((percent - PROGRESS_EXTRACT_END) / (PROGRESS_ALIGN_END - PROGRESS_EXTRACT_END)
            * 100.0)
            .clamp(0.0, 100.0),
        "export" => ((percent - PROGRESS_ALIGN_END) / (PROGRESS_EXPORT_END - PROGRESS_ALIGN_END)
            * 100.0)
            .clamp(0.0, 100.0),
        "complete" => 100.0,
        _ => 0.0,
    }
}

fn phase_percents_for_structured(phase: &str, phase_percent: f64, percent: f64) -> PhasePercents {
    let value = phase_percent.clamp(0.0, 100.0);
    match phase {
        "extract" => PhasePercents {
            extract: value,
            align: 0.0,
            export: 0.0,
        },
        "align" => PhasePercents {
            extract: 100.0,
            align: value,
            export: 0.0,
        },
        "export" => PhasePercents {
            extract: 100.0,
            align: 100.0,
            export: value,
        },
        "complete" => PhasePercents {
            extract: 100.0,
            align: 100.0,
            export: 100.0,
        },
        "train" => PhasePercents {
            extract: 0.0,
            align: 0.0,
            export: 0.0,
        },
        _ => phase_percents(percent),
    }
}

fn structured_progress_event(raw: &str, elapsed: u64) -> Option<PipelineProgressEvent> {
    let payload: StructuredPipelineEvent = serde_json::from_str(raw).ok()?;
    let percent = payload.percent.unwrap_or(0.0).clamp(0.0, 100.0);
    let phase = payload
        .phase
        .unwrap_or_else(|| phase_for_progress(percent).to_string());
    let phase_percent = payload
        .phase_percent
        .unwrap_or_else(|| phase_percent_from_overall(&phase, percent));
    let phase_percents = payload
        .phase_percents
        .unwrap_or_else(|| phase_percents_for_structured(&phase, phase_percent, percent));
    let message = payload
        .message
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| format!("{} {:.0}%", phase, percent));

    Some(PipelineProgressEvent {
        phase,
        stage: payload.stage,
        track_id: payload.track_id,
        percent,
        message,
        elapsed,
        phase_percents,
        current: payload.current,
        total: payload.total,
        eta_seconds: payload.eta_seconds,
        aligned_cameras: payload.aligned_cameras,
        total_cameras: payload.total_cameras,
        alignment_rate: payload.alignment_rate,
        loss: payload.loss,
        splat_count: payload.splat_count,
        trainer_state: payload.trainer_state,
        heartbeat: payload.heartbeat,
    })
}

fn is_current_pipeline(app: &AppHandle, pid: u32) -> bool {
    app.try_state::<crate::AppState>()
        .and_then(|state| state.pipeline.lock().ok().map(|p| p.is_current_pid(pid)))
        .unwrap_or(false)
}

fn configure_python_io(command: &mut Command) {
    command.env("PYTHONIOENCODING", "utf-8:replace");
    command.env("PYTHONUTF8", "1");
    command.env("PYTHONDONTWRITEBYTECODE", "1");
    command.env("PYTHONNOUSERSITE", "1");
}

fn training_exit_message(exit_code: &str, supervisor_error: Option<&str>) -> String {
    supervisor_error
        .filter(|message| !message.trim().is_empty())
        .map(str::to_owned)
        .unwrap_or_else(|| format!("LichtFeld exited with code {exit_code}"))
}

fn emit_job_event(app: &AppHandle, event: crate::contracts::JobEvent) {
    crate::batch::observe_job_event(app, &event);
    let _ = app.emit("job:event", event);
}

fn emit_pipeline_event<T: Serialize>(
    app: &AppHandle,
    event_name: &str,
    payload: T,
    context: Option<&crate::job::JobContext>,
) {
    let Ok(mut value) = serde_json::to_value(payload) else { return };
    if let (Some(context), Some(object)) = (context, value.as_object_mut()) {
        object.insert(
            "projectRoot".to_string(),
            serde_json::Value::String(context.project_root().to_string_lossy().to_string()),
        );
        object.insert(
            "jobId".to_string(),
            serde_json::Value::String(context.job_id.clone()),
        );
        if let Some(task_id) = context.task_id.as_ref() {
            object.insert("taskId".to_string(), serde_json::Value::String(task_id.clone()));
        }
    }
    let _ = app.emit(event_name, value);
}

fn emit_pipeline_progress(
    app: &AppHandle,
    job_context: Option<&crate::job::JobContext>,
    event: PipelineProgressEvent,
) {
    if let Some(context) = job_context {
        let persisted = if event.phase.is_empty() {
            crate::job::record_log_impl(context, &event.message).map(|event| vec![event])
        } else {
            crate::job::record_progress_impl(
                context,
                crate::job::JobProgressUpdate {
                    stage_id: event.stage.clone(),
                    current: event.current,
                    total: event.total,
                    percent: Some(event.percent),
                    eta_seconds: event.eta_seconds,
                    message: event.message.clone(),
                    heartbeat: event.heartbeat,
                },
            )
        };
        match persisted {
            Ok(events) => events.into_iter().for_each(|event| emit_job_event(app, event)),
            Err(error) => {
                let _ = app.emit(
                    "job:persistence-error",
                    serde_json::json!({
                        "jobId": context.job_id,
                        "error": error.message,
                    }),
                );
            }
        }
    }
    emit_pipeline_event(app, "pipeline:progress", event, job_context);
}

fn finish_persisted_job(
    app: &AppHandle,
    job_context: Option<&crate::job::JobContext>,
    state: crate::contracts::JobState,
    message: &str,
) {
    let Some(context) = job_context else { return };
    match crate::job::finish_job_impl(context, state, message) {
        Ok(snapshot) => {
            let _ = app.emit("job:snapshot", snapshot);
            if state == crate::contracts::JobState::Completed {
                if let Err(error) = crate::performance::record_completed_job_for_app(app, context) {
                    let _ = app.emit(
                        "performance:warning",
                        serde_json::json!({ "error": error.message }),
                    );
                }
            }
        }
        Err(error) => {
            let _ = app.emit(
                "job:persistence-error",
                serde_json::json!({
                    "jobId": context.job_id.clone(),
                    "error": error.message,
                }),
            );
        }
    }
}

impl PipelineState {
    pub fn new() -> Self {
        Self {
            pid: None,
            cancelled: None,
            job: None,
            active_job: None,
        }
    }

    pub fn start_with_metashape_runtime(
        &mut self,
        app: AppHandle,
        python_exe: &str,
        script: &str,
        args: &[String],
        metashape_site_packages: Option<&str>,
    ) -> Result<(), String> {
        self.start_internal(
            app,
            python_exe,
            script,
            args,
            true,
            None,
            metashape_site_packages,
        )
    }

    pub fn start_registered_reconstruction(
        &mut self,
        app: AppHandle,
        python_exe: &str,
        script: &str,
        args: &[String],
        job_context: crate::job::JobContext,
        metashape_site_packages: Option<&str>,
    ) -> Result<(), String> {
        self.start_internal(
            app,
            python_exe,
            script,
            args,
            false,
            Some(job_context),
            metashape_site_packages,
        )
    }

    pub fn start_registered_job(
        &mut self,
        app: AppHandle,
        python_exe: &str,
        script: &str,
        args: &[String],
        job_context: crate::job::JobContext,
    ) -> Result<(), String> {
        self.start_internal(app, python_exe, script, args, false, Some(job_context), None)
    }

    fn start_internal(
        &mut self,
        app: AppHandle,
        python_exe: &str,
        script: &str,
        args: &[String],
        register_reconstruction: bool,
        job_context: Option<crate::job::JobContext>,
        metashape_site_packages: Option<&str>,
    ) -> Result<(), String> {
        self.ensure_startable()?;

        let python = crate::tool_resolver::resolve_python(python_exe);
        let script_path = crate::tool_resolver::resolve_script_path(script);
        let is_training_supervisor = script_path
            .file_name()
            .and_then(|value| value.to_str())
            .is_some_and(|value| value.eq_ignore_ascii_case("lichtfeld_training.py"));
        let mut cmd = Command::new(&python);
        if is_training_supervisor {
            configure_python_io(&mut cmd);
            configure_training_supervisor_environment(&mut cmd, Path::new(&python));
        } else {
            configure_python_io(&mut cmd);
            cmd.env_remove("PYTHONPATH");
        }
        #[cfg(target_os = "windows")]
        cmd.creation_flags(0x08000000); // CREATE_NO_WINDOW
        if let Some(root) = script_path.parent().and_then(|path| path.parent()) {
            cmd.current_dir(root);
        }
        if !is_training_supervisor {
            let ffmpeg = crate::tool_resolver::locate_ffmpeg();
            let ffprobe = crate::tool_resolver::locate_ffprobe();
            cmd.env("XPANO_FFMPEG", &ffmpeg);
            cmd.env("XPANO_FFPROBE", &ffprobe);
            if let Some(ffmpeg_dir) = std::path::Path::new(&ffmpeg).parent() {
                let current_path = std::env::var("PATH").unwrap_or_default();
                let separator = if cfg!(windows) { ";" } else { ":" };
                cmd.env(
                    "PATH",
                    format!(
                        "{}{}{}",
                        ffmpeg_dir.to_string_lossy(),
                        separator,
                        current_path
                    ),
                );
            }
        }
        cmd.arg(script_path.to_str().unwrap_or(script));
        for arg in args {
            cmd.arg(arg);
        }
        configure_metashape_runtime(&mut cmd, metashape_site_packages);
        cmd.stdout(Stdio::piped());
        cmd.stderr(Stdio::piped());

        let mut child = cmd.spawn().map_err(|e| format!("启动失败: {}", e))?;
        let pid = child.id();
        let stdout = child.stdout.take().ok_or("No stdout")?;
        let stderr = child.stderr.take().ok_or("No stderr")?;

        let cancelled = Arc::new(AtomicBool::new(false));
        self.pid = Some(pid);
        self.cancelled = Some(cancelled.clone());
        self.active_job = job_context.clone();
        self.job = match ProcessJob::new().and_then(|job| {
            job.assign_pid(pid)?;
            Ok(job)
        }) {
            Ok(job) => Some(job),
            Err(error) => {
                emit_pipeline_progress(
                    &app,
                    job_context.as_ref(),
                    PipelineProgressEvent {
                        phase: String::new(),
                        stage: None,
                        track_id: None,
                        percent: 0.0,
                        message: format!("WARN: {}", error),
                        elapsed: 0,
                        phase_percents: PhasePercents {
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
                None
            }
        };

        let mut output_path = args
            .windows(2)
            .find_map(|pair| {
                if pair[0] == "--output" {
                    Some(pair[1].clone())
                } else {
                    None
                }
            })
            .unwrap_or_default();
        let script_name = script_path
            .file_name()
            .and_then(|value| value.to_str())
            .unwrap_or_default();
        let job_kind = if script_name.eq_ignore_ascii_case("run_xpano_prepare_project.py") {
            "media"
        } else if script_name.eq_ignore_ascii_case("lichtfeld_training.py") {
            "training"
        } else {
            "reconstruction"
        }
        .to_string();
        let project_root_arg = find_media_project_root(args);
        if job_kind == "training" {
            output_path = project_root_arg.clone().unwrap_or_default();
        }
        let media_project_root = (job_kind == "media")
            .then_some(project_root_arg.clone())
            .flatten();
        if job_kind == "reconstruction" && register_reconstruction {
            if let Err(error) = update_reconstruction_project(&app, &output_path, None) {
                let _ = child.kill();
                let _ = child.wait();
                self.clear();
                return Err(format!("无法登记重建任务: {}", error));
            }
        }

        let start_time = Arc::new(Mutex::new(std::time::Instant::now()));
        let latest_progress = Arc::new(Mutex::new(None::<PipelineProgressEvent>));
        let latest_supervisor_error = Arc::new(Mutex::new(None::<String>));
        let (reader_done_sender, reader_done_receiver) = std::sync::mpsc::channel::<()>();

        // Spawn stdout reader
        {
            let app = app.clone();
            let start = start_time.clone();
            let latest = latest_progress.clone();
            let supervisor_error = latest_supervisor_error.clone();
            let reader_done = reader_done_sender.clone();
            let stdout_job_kind = job_kind.clone();
            let stdout_job_context = job_context.clone();
            std::thread::spawn(move || {
                let reader = BufReader::new(stdout);
                for line in reader.lines() {
                    let Ok(text) = line else { break };
                    let trimmed = text.trim();
                    if trimmed.is_empty() {
                        continue;
                    }
                    if !is_current_pipeline(&app, pid) {
                        break;
                    }

                    if let Some(payload) = trimmed.strip_prefix("PIPELINE_EVENT:") {
                        if let Some(event) = structured_progress_event(
                            payload.trim(),
                            start.lock().unwrap().elapsed().as_secs(),
                        ) {
                            if let Ok(mut current) = latest.lock() {
                                *current = Some(event.clone());
                            }
                            emit_pipeline_progress(&app, stdout_job_context.as_ref(), event);
                        }
                        continue;
                    }

                    if let Some(payload) = trimmed.strip_prefix("MEDIA_ITEM:") {
                        if let Ok(event) = serde_json::from_str::<PipelineMediaItemEvent>(payload.trim()) {
                            emit_pipeline_event(&app, "pipeline:media-item", event, stdout_job_context.as_ref());
                        }
                        continue;
                    }

                    // PROGRESS:N format
                    if let Some(val) = trimmed.strip_prefix("PROGRESS:") {
                        if let Ok(pct) = val.trim().parse::<f64>() {
                            let elapsed = start.lock().unwrap().elapsed().as_secs();
                            let event = PipelineProgressEvent {
                                phase: phase_for_progress(pct).to_string(),
                                stage: None,
                                track_id: None,
                                percent: pct,
                                message: format!("进度 {}%", pct as i32),
                                elapsed,
                                phase_percents: phase_percents(pct),
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
                            };
                            if let Ok(mut current) = latest.lock() {
                                *current = Some(event.clone());
                            }
                            emit_pipeline_progress(&app, stdout_job_context.as_ref(), event);
                        }
                        continue;
                    }

                    // PREVIEW:left|right format
                    if let Some(payload) = trimmed.strip_prefix("PREVIEW:") {
                        if let Some((left, right)) = payload.split_once('|') {
                            emit_pipeline_event(
                                &app,
                                "pipeline:preview",
                                serde_json::json!({ "left": left.trim(), "right": right.trim() }),
                                stdout_job_context.as_ref(),
                            );
                        }
                        continue;
                    }

                    // ERROR: prefix
                    if let Some(err) = trimmed.strip_prefix("ERROR:") {
                        if stdout_job_kind == "training" {
                            if let Ok(mut latest) = supervisor_error.lock() {
                                *latest = Some(err.trim().to_string());
                            }
                        }
                        emit_pipeline_event(
                            &app,
                            "pipeline:error",
                            PipelineErrorEvent {
                                error: err.trim().to_string(),
                                job_kind: stdout_job_kind.clone(),
                            },
                            stdout_job_context.as_ref(),
                        );
                        continue;
                    }

                    // General log line — only emit when content changes
                    {
                        let mut last = LAST_LOG_LINE.lock().unwrap();
                        let msg = trimmed.to_string();
                        if last.as_ref() == Some(&msg) {
                            continue;
                        }
                        *last = Some(msg.clone());
                        emit_pipeline_progress(
                            &app,
                            stdout_job_context.as_ref(),
                            PipelineProgressEvent {
                                phase: String::new(),
                                stage: None,
                                track_id: None,
                                percent: 0.0,
                                message: msg,
                                elapsed: start.lock().unwrap().elapsed().as_secs(),
                                phase_percents: PhasePercents {
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
                }
                let _ = reader_done.send(());
            });
        }

        // Spawn stderr reader (prevents pipe deadlock, reports errors)
        {
            let app = app.clone();
            let start = start_time.clone();
            let supervisor_error = latest_supervisor_error.clone();
            let reader_done = reader_done_sender;
            let stderr_job_kind = job_kind.clone();
            let stderr_job_context = job_context.clone();
            std::thread::spawn(move || {
                let reader = BufReader::new(stderr);
                for text in reader.lines().map_while(Result::ok) {
                    let trimmed = text.trim();
                    if !trimmed.is_empty() {
                        if !is_current_pipeline(&app, pid) {
                            break;
                        }
                        if let Some(err) = trimmed.strip_prefix("ERROR:") {
                            if stderr_job_kind == "training" {
                                if let Ok(mut latest) = supervisor_error.lock() {
                                    *latest = Some(err.trim().to_string());
                                }
                            }
                            emit_pipeline_event(
                                &app,
                                "pipeline:error",
                                PipelineErrorEvent {
                                    error: err.trim().to_string(),
                                    job_kind: stderr_job_kind.clone(),
                                },
                                stderr_job_context.as_ref(),
                            );
                        } else {
                            emit_pipeline_progress(
                                &app,
                                stderr_job_context.as_ref(),
                                PipelineProgressEvent {
                                    phase: String::new(),
                                    stage: None,
                                    track_id: None,
                                    percent: 0.0,
                                    message: trimmed.to_string(),
                                    elapsed: start.lock().unwrap().elapsed().as_secs(),
                                    phase_percents: PhasePercents {
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
                    }
                }
                let _ = reader_done.send(());
            });
        }

        {
            let app = app.clone();
            let cancelled = cancelled.clone();
            let start = start_time.clone();
            let latest = latest_progress.clone();
            let heartbeat_job_context = job_context.clone();
            std::thread::spawn(move || loop {
                std::thread::sleep(std::time::Duration::from_secs(1));
                if cancelled.load(Ordering::SeqCst) || !is_current_pipeline(&app, pid) {
                    break;
                }
                let event = latest
                    .lock()
                    .ok()
                    .and_then(|current| current.clone())
                    .map(|mut event| {
                        event.elapsed = start.lock().unwrap().elapsed().as_secs();
                        event.heartbeat = true;
                        event
                    });
                if let Some(event) = event {
                    emit_pipeline_progress(&app, heartbeat_job_context.as_ref(), event);
                }
            });
        }

        // A process is complete only when the child exits successfully.  Closing
        // stdout also happens on cancellation and crashes, so readers never emit
        // `pipeline:complete`.
        {
            let app = app.clone();
            let cancelled = cancelled.clone();
            let watcher_job_context = job_context.clone();
            let supervisor_error = latest_supervisor_error.clone();
            std::thread::spawn(move || {
                let status = child.wait();
                for _ in 0..2 {
                    let _ = reader_done_receiver.recv_timeout(std::time::Duration::from_millis(250));
                }
                let was_cancelled = cancelled.load(Ordering::SeqCst);
                let last_supervisor_error = supervisor_error
                    .lock()
                    .ok()
                    .and_then(|value| value.clone());

                let is_current = is_current_pipeline(&app, pid);

                if is_current {
                    match status {
                        Ok(_exit) if was_cancelled => {
                            if job_kind == "media" {
                                let _ = settle_media_project(
                                    &app,
                                    media_project_root.as_deref(),
                                    false,
                                );
                            } else if job_kind == "training" {
                                let _ = update_training_project(&app, &output_path, None, None);
                            } else {
                                let _ = interrupt_reconstruction_project(&app, &output_path);
                            }
                            finish_persisted_job(
                                &app,
                                watcher_job_context.as_ref(),
                                crate::contracts::JobState::Cancelled,
                                "任务已取消",
                            );
                            emit_pipeline_event(
                                &app,
                                "pipeline:error",
                                PipelineErrorEvent {
                                    error: "任务已取消".to_string(),
                                    job_kind: job_kind.clone(),
                                },
                                watcher_job_context.as_ref(),
                            );
                        }
                        Ok(exit) if exit.success() => {
                            let settled = if job_kind == "media" {
                                settle_media_project(
                                    &app,
                                    media_project_root.as_deref(),
                                    true,
                                )
                            } else if job_kind == "training" {
                                update_training_project(&app, &output_path, Some(true), None)
                            } else {
                                update_reconstruction_project(
                                    &app,
                                    &output_path,
                                    Some(true),
                                )
                            };
                            match settled {
                                Ok(()) => {
                                    finish_persisted_job(
                                        &app,
                                        watcher_job_context.as_ref(),
                                        crate::contracts::JobState::Completed,
                                        "任务已完成",
                                    );
                                    emit_pipeline_event(
                                        &app,
                                        "pipeline:complete",
                                        PipelineCompleteEvent {
                                            output_path,
                                            job_kind,
                                        },
                                        watcher_job_context.as_ref(),
                                    );
                                }
                                Err(error) => {
                                    finish_persisted_job(
                                        &app,
                                        watcher_job_context.as_ref(),
                                        crate::contracts::JobState::Failed,
                                        &format!("任务结果提交失败: {}", error),
                                    );
                                    emit_pipeline_event(
                                        &app,
                                        "pipeline:error",
                                        PipelineErrorEvent {
                                            error: format!("任务结果提交失败: {}", error),
                                            job_kind,
                                        },
                                        watcher_job_context.as_ref(),
                                    );
                                }
                            }
                        }
                        Ok(exit) => {
                            let code = exit
                                .code()
                                .map(|value| value.to_string())
                                .unwrap_or_else(|| "unknown".to_string());
                            let training_error = (job_kind == "training").then(|| {
                                training_exit_message(&code, last_supervisor_error.as_deref())
                            });
                            if job_kind == "media" {
                                let _ = settle_media_project(
                                    &app,
                                    media_project_root.as_deref(),
                                    false,
                                );
                            } else if job_kind == "training" {
                                let _ = update_training_project(
                                    &app,
                                    &output_path,
                                    Some(false),
                                    training_error.as_deref(),
                                );
                            } else {
                                let _ = update_reconstruction_project(
                                    &app,
                                    &output_path,
                                    Some(false),
                                );
                            }
                            finish_persisted_job(
                                &app,
                                watcher_job_context.as_ref(),
                                crate::contracts::JobState::Failed,
                                &format!("任务异常结束，退出码 {}", code),
                            );
                            emit_pipeline_event(
                                &app,
                                "pipeline:error",
                                PipelineErrorEvent {
                                    error: format!("任务异常结束，退出码 {}", code),
                                    job_kind: job_kind.clone(),
                                },
                                watcher_job_context.as_ref(),
                            );
                        }
                        Err(error) => {
                            if job_kind == "media" {
                                let _ = settle_media_project(
                                    &app,
                                    media_project_root.as_deref(),
                                    false,
                                );
                            } else if job_kind == "training" {
                                let _ = update_training_project(
                                    &app,
                                    &output_path,
                                    Some(false),
                                    Some(&error.to_string()),
                                );
                            } else {
                                let _ = update_reconstruction_project(
                                    &app,
                                    &output_path,
                                    Some(false),
                                );
                            }
                            finish_persisted_job(
                                &app,
                                watcher_job_context.as_ref(),
                                crate::contracts::JobState::Failed,
                                &format!("无法获取任务退出状态: {}", error),
                            );
                            emit_pipeline_event(
                                &app,
                                "pipeline:error",
                                PipelineErrorEvent {
                                    error: format!("无法获取任务退出状态: {}", error),
                                    job_kind: job_kind.clone(),
                                },
                                watcher_job_context.as_ref(),
                            );
                        }
                    }
                }

                if let Some(state) = app.try_state::<crate::AppState>() {
                    let _ = state.pipeline.lock().map(|mut p| p.clear_if_pid(pid));
                }
            });
        }

        Ok(())
    }

    pub fn ensure_startable(&self) -> Result<(), String> {
        if self.is_running() {
            return Err("ALREADY_RUNNING: another pipeline task is already active".to_string());
        }
        Ok(())
    }

    /// Release the recorded process metadata after the watcher thread has
    /// observed the process exit.
    pub fn clear(&mut self) {
        self.pid = None;
        self.cancelled = None;
        self.job = None;
        self.active_job = None;
    }

    pub fn clear_if_pid(&mut self, pid: u32) {
        if self.pid == Some(pid) {
            self.clear();
        }
    }

    pub fn is_current_pid(&self, pid: u32) -> bool {
        self.pid == Some(pid)
    }

    pub fn is_running(&self) -> bool {
        self.pid.is_some()
    }

    pub fn active_job(&self) -> Option<&crate::job::JobContext> {
        self.active_job.as_ref()
    }

    pub fn register_external_process(
        &mut self,
        pid: u32,
        job_context: crate::job::JobContext,
    ) -> Result<Arc<AtomicBool>, String> {
        self.ensure_startable()?;
        let cancelled = Arc::new(AtomicBool::new(false));
        let job = ProcessJob::new()?;
        job.assign_pid(pid)?;
        self.pid = Some(pid);
        self.cancelled = Some(cancelled.clone());
        self.job = Some(job);
        self.active_job = Some(job_context);
        Ok(cancelled)
    }

    pub fn finish_registered_process(&mut self, pid: u32) -> Result<bool, String> {
        if self.pid != Some(pid) {
            return Err("registered process is no longer active".to_string());
        }
        let was_cancelled = self
            .cancelled
            .as_ref()
            .is_some_and(|cancelled| cancelled.load(Ordering::SeqCst));
        self.clear();
        Ok(was_cancelled)
    }

    fn mark_cancel_requested(&mut self) -> Option<u32> {
        if let Some(cancelled) = &self.cancelled {
            cancelled.store(true, Ordering::SeqCst);
        }
        if let Some(job) = &self.job {
            job.terminate();
        }
        self.pid
    }

    pub fn cancel(&mut self) -> Result<(), String> {
        if let Some(pid) = self.mark_cancel_requested() {
            // On Windows, kill the entire process tree so subprocesses
            // (Metashape / COLMAP commands) don't become orphans.
            #[cfg(target_os = "windows")]
            {
                let mut cmd = std::process::Command::new("taskkill");
                cmd.creation_flags(0x08000000);
                let _ = cmd
                    .args(["/F", "/T", "/PID", &pid.to_string()])
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .status();
            }

            #[cfg(not(target_os = "windows"))]
            {
                let _ = std::process::Command::new("kill")
                    .args(["-TERM", &pid.to_string()])
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .status();
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    #[test]
    fn python_commands_force_utf8_stdio() {
        let mut command = Command::new("python");

        configure_python_io(&mut command);

        let env = command
            .get_envs()
            .filter_map(|(key, value)| value.map(|value| (key.to_string_lossy().to_string(), value.to_string_lossy().to_string())))
            .collect::<HashMap<_, _>>();
        assert_eq!(env.get("PYTHONIOENCODING").map(String::as_str), Some("utf-8:replace"));
        assert_eq!(env.get("PYTHONUTF8").map(String::as_str), Some("1"));
        assert_eq!(env.get("PYTHONDONTWRITEBYTECODE").map(String::as_str), Some("1"));
        assert_eq!(env.get("PYTHONNOUSERSITE").map(String::as_str), Some("1"));
    }

    #[test]
    fn training_supervisor_removes_development_python_and_qt_overrides() {
        let mut command = Command::new("python");
        command.env("PATH", r"C:\\HostPython;C:\\HostQt");

        configure_training_supervisor_environment(
            &mut command,
            Path::new(r"C:\\xPano\\binaries\\python\\python.exe"),
        );

        let env = command
            .get_envs()
            .map(|(key, value)| {
                (
                    key.to_string_lossy().to_string(),
                    value.map(|value| value.to_string_lossy().to_string()),
                )
            })
            .collect::<HashMap<_, _>>();
        for name in [
            "CONDA_DEFAULT_ENV",
            "CONDA_PREFIX",
            "PYTHONHOME",
            "PYTHONPATH",
            "PYTHONUSERBASE",
            "QT_PLUGIN_PATH",
            "QT_QPA_PLATFORM_PLUGIN_PATH",
            "VIRTUAL_ENV",
            "XPANO_PYTHON",
            "XPANO_ROOT",
            "CUDA_PATH",
            "VK_ICD_FILENAMES",
            "VK_LAYER_PATH",
        ] {
            assert_eq!(env.get(name), Some(&None), "{name} must not reach LFS");
        }
        let path = env.get("PATH").and_then(|value| value.as_deref()).unwrap();
        assert!(path.starts_with(r"C:\\xPano\\binaries\\python"));
        assert!(!path.contains("HostPython"));
    }

    #[test]
    fn training_supervisor_rejects_future_cuda_and_vulkan_toolkit_overrides() {
        for name in [
            "CUDA_PATH_V13_0",
            "cuda_home",
            "VULKAN_SDK",
            "VK_INSTANCE_LAYERS",
        ] {
            assert!(is_training_supervisor_environment_override(
                std::ffi::OsStr::new(name)
            ));
        }
    }

    #[test]
    fn training_exit_preserves_the_supervisor_failure_for_project_recovery() {
        assert_eq!(
            training_exit_message(
                "120",
                Some("LFS_VULKAN_RUNTIME_FAILED: Vulkan device initialization failed"),
            ),
            "LFS_VULKAN_RUNTIME_FAILED: Vulkan device initialization failed",
        );
        assert_eq!(
            training_exit_message("120", None),
            "LichtFeld exited with code 120",
        );
    }

    #[test]
    fn reconstruction_command_receives_resolved_metashape_site_packages_explicitly() {
        let mut command = Command::new("python");

        configure_metashape_runtime(&mut command, Some(r"C:\Users\测试\xPano Runtime\site-packages"));

        let env = command
            .get_envs()
            .filter_map(|(key, value)| value.map(|value| (key.to_string_lossy().to_string(), value.to_string_lossy().to_string())))
            .collect::<HashMap<_, _>>();
        assert_eq!(
            env.get("XPANO_METASHAPE_SITE_PACKAGES").map(String::as_str),
            Some(r"C:\Users\测试\xPano Runtime\site-packages")
        );
        assert!(!env.contains_key("PYTHONPATH"));
        assert_eq!(
            command
                .get_args()
                .map(|value| value.to_string_lossy().to_string())
                .collect::<Vec<_>>(),
            vec![
                "--metashape-site-packages".to_string(),
                r"C:\Users\测试\xPano Runtime\site-packages".to_string(),
            ]
        );
    }

    #[test]
    fn active_pipeline_rejects_a_second_start_without_clearing_the_first_pid() {
        let mut pipeline = PipelineState::new();
        pipeline.pid = Some(4242);

        let error = pipeline.ensure_startable().unwrap_err();

        assert!(error.contains("ALREADY_RUNNING"));
        assert_eq!(pipeline.pid, Some(4242));
    }

    #[test]
    fn cancellation_keeps_active_pid_until_the_watcher_observes_exit() {
        let cancelled = Arc::new(AtomicBool::new(false));
        let mut pipeline = PipelineState::new();
        pipeline.pid = Some(4242);
        pipeline.cancelled = Some(cancelled.clone());

        let pid = pipeline.mark_cancel_requested();

        assert_eq!(pid, Some(4242));
        assert_eq!(pipeline.pid, Some(4242));
        assert!(cancelled.load(Ordering::SeqCst));
    }

    #[test]
    fn registered_preflight_reports_cancellation_before_clearing_state() {
        let cancelled = Arc::new(AtomicBool::new(true));
        let mut pipeline = PipelineState::new();
        pipeline.pid = Some(4242);
        pipeline.cancelled = Some(cancelled);

        let was_cancelled = pipeline.finish_registered_process(4242).unwrap();

        assert!(was_cancelled);
        assert!(!pipeline.is_running());
    }

    #[test]
    fn structured_training_progress_preserves_lichtfeld_metrics_without_reconstruction_phase_noise() {
        let event = structured_progress_event(
            r#"{"phase":"train","stage":"training.optimize","percent":25,"current":125,"total":500,"etaSeconds":38,"loss":0.03125,"splatCount":456789,"trainerState":"running"}"#,
            12,
        )
        .unwrap();

        assert_eq!(event.phase, "train");
        assert_eq!(event.current, Some(125));
        assert_eq!(event.loss, Some(0.03125));
        assert_eq!(event.splat_count, Some(456789));
        assert_eq!(event.trainer_state.as_deref(), Some("running"));
        assert_eq!(event.phase_percents.extract, 0.0);
        assert_eq!(event.phase_percents.align, 0.0);
        assert_eq!(event.phase_percents.export, 0.0);
    }
}
