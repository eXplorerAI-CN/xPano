use crate::contracts::{TrainingStatus, XpanoProjectV2};
use crate::project::ProjectCommandError;
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};


#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TrainingConfig {
    pub iterations: u64,
    pub strategy: String,
    pub sh_degree: u8,
    pub max_gaussians: u64,
    pub resize_factor: String,
    pub max_width: u64,
    pub test_every: u64,
    pub use_cpu_cache: bool,
    pub use_fs_cache: bool,
    pub centralize: String,
    pub undistort: bool,
    pub enable_mip: bool,
    pub bilateral_grid: bool,
    pub enable_eval: bool,
    pub background_mode: String,
    pub background_color: String,
    pub gui: bool,
}

impl Default for TrainingConfig {
    fn default() -> Self {
        Self {
            iterations: 30_000,
            strategy: "mrnf".to_string(),
            sh_degree: 3,
            max_gaussians: 1_000_000,
            resize_factor: "auto".to_string(),
            max_width: 3840,
            test_every: 0,
            use_cpu_cache: true,
            use_fs_cache: true,
            centralize: "off".to_string(),
            undistort: false,
            enable_mip: false,
            bilateral_grid: true,
            enable_eval: false,
            background_mode: "solidcolor".to_string(),
            background_color: "#000000".to_string(),
            gui: true,
        }
    }
}

pub(crate) fn validate_config(config: &TrainingConfig) -> Result<(), ProjectCommandError> {
    if config.iterations == 0 {
        return Err(ProjectCommandError::new("invalid_training_config", "iterations must be greater than 0"));
    }
    if !matches!(config.strategy.as_str(), "mcmc" | "mrnf" | "igs+") {
        return Err(ProjectCommandError::new("invalid_training_config", "unsupported LichtFeld strategy"));
    }
    if config.sh_degree > 3 || config.max_gaussians == 0 {
        return Err(ProjectCommandError::new("invalid_training_config", "invalid SH degree or Gaussian cap"));
    }
    if !matches!(config.resize_factor.as_str(), "auto" | "1" | "2" | "4" | "8") {
        return Err(ProjectCommandError::new("invalid_training_config", "unsupported resize factor"));
    }
    if !matches!(config.centralize.as_str(), "off" | "by_pointcloud" | "by_cameras") {
        return Err(ProjectCommandError::new("invalid_training_config", "unsupported centralize mode"));
    }
    if !matches!(config.background_mode.as_str(), "solidcolor" | "modulation" | "random") {
        return Err(ProjectCommandError::new("invalid_training_config", "unsupported background mode"));
    }
    Ok(())
}

pub fn resolve_training_dataset(project_root: &Path, project: &XpanoProjectV2) -> Result<PathBuf, ProjectCommandError> {
    let configured = project
        .reconstruction
        .colmap_path
        .as_deref()
        .filter(|value| !value.trim().is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    let candidate = if configured.is_absolute() {
        configured
    } else {
        project_root.join(configured)
    };
    let roots = [candidate, project_root.to_path_buf()];
    for root in roots {
        if root.join("images").is_dir()
            && root.join("sparse").join("0").join("cameras.bin").is_file()
            && root.join("sparse").join("0").join("images.bin").is_file()
            && root.join("sparse").join("0").join("points3D.bin").is_file()
        {
            return root.canonicalize().map_err(|error| {
                ProjectCommandError::new("training_not_ready", format!("failed to resolve COLMAP dataset: {error}"))
            });
        }
    }
    Err(ProjectCommandError::new(
        "training_not_ready",
        "COLMAP dataset requires images and sparse/0 model files",
    ))
}

pub(crate) fn save_training_config_impl(
    project_root: &Path,
    expected_revision: u64,
    config: &TrainingConfig,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    validate_config(config)?;
    let mut project = crate::project::read_project(project_root)?;
    if project.revision != expected_revision {
        return Err(crate::project::ProjectCommandError::revision_conflict(expected_revision, project.revision));
    }
    if project.training.status == crate::contracts::TrainingStatus::Running {
        return Err(crate::project::ProjectCommandError::new("job_conflict", "training configuration is locked while training is running"));
    }
    let value = serde_json::to_value(config).map_err(|error| crate::project::ProjectCommandError::new("invalid_training_config", error.to_string()))?;
    if project.training.config == value && project.training.total_iterations == config.iterations {
        return Ok(project);
    }
    project.training.config = value;
    project.training.total_iterations = config.iterations;
    project.revision += 1;
    crate::project::touch_project(&mut project);
    crate::project::write_project_atomic(project_root, &project)?;
    Ok(project)
}

pub fn validate_training_start_inputs(
    project_root: &Path,
    project: &XpanoProjectV2,
    config: &TrainingConfig,
) -> Result<PathBuf, ProjectCommandError> {
    validate_config(config)?;
    let dataset = resolve_training_dataset(project_root, project)?;
    let geometry_ready = matches!(
        project.reconstruction.status,
        crate::contracts::ReconstructionStatus::Complete | crate::contracts::ReconstructionStatus::Stale
    ) && project.geometry.variants.iter().any(|variant| {
        variant.id == project.geometry.active_variant_id
            && variant.status == crate::contracts::PointVariantStatus::Ready
    });
    if !geometry_ready {
        return Err(ProjectCommandError::new(
            "training_not_ready",
            "reconstruction geometry is not ready",
        ));
    }
    Ok(dataset)
}

fn normalized_relative(path: &Path) -> String {
    path.to_string_lossy().replace('\\', "/")
}

fn latest_artifact(root: &Path) -> Option<PathBuf> {
    let mut candidates = Vec::new();
    let mut pending = vec![root.to_path_buf()];
    while let Some(directory) = pending.pop() {
        let entries = std::fs::read_dir(directory).ok()?;
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                pending.push(path);
            } else if path
                .extension()
                .and_then(|value| value.to_str())
                .is_some_and(|value| matches!(value.to_ascii_lowercase().as_str(), "ply" | "sog" | "spz"))
            {
                candidates.push(path);
            }
        }
    }
    candidates.into_iter().max_by_key(|path| {
        path.metadata()
            .and_then(|metadata| metadata.modified())
            .ok()
    })
}

pub fn begin_training_impl(
    project_root: &Path,
    expected_revision: u64,
    job_id: &str,
    config: &TrainingConfig,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    let mut project = crate::project::read_project(project_root)?;
    if project.revision != expected_revision {
        return Err(ProjectCommandError::revision_conflict(expected_revision, project.revision));
    }
    validate_training_start_inputs(project_root, &project, config)?;
    let output_relative = PathBuf::from("work")
        .join("training")
        .join("runs")
        .join(job_id);
    std::fs::create_dir_all(project_root.join(&output_relative)).map_err(|error| {
        ProjectCommandError::new("artifact_corrupt", format!("failed to create training output: {error}"))
    })?;
    project.training.status = TrainingStatus::Running;
    project.training.input_revision = project.revisions.geometry;
    project.training.config = serde_json::to_value(config).map_err(|error| {
        ProjectCommandError::new("invalid_training_config", error.to_string())
    })?;
    project.training.output_path = Some(normalized_relative(&output_relative));
    project.training.artifact_path = None;
    project.training.source_job_id = Some(job_id.to_string());
    project.training.last_iteration = 0;
    project.training.total_iterations = config.iterations;
    project.training.last_loss = None;
    project.training.splat_count = 0;
    project.training.error = None;
    project.revision += 1;
    crate::project::touch_project(&mut project);
    crate::project::write_project_atomic(project_root, &project)?;
    Ok(project)
}

pub fn finalize_training_job_impl(project_root: &Path) -> Result<XpanoProjectV2, ProjectCommandError> {
    let mut project = crate::project::read_project(project_root)?;
    let output_relative = project.training.output_path.as_deref().ok_or_else(|| {
        ProjectCommandError::new("artifact_corrupt", "training output path is missing")
    })?;
    let output = project_root.join(output_relative);
    let artifact = latest_artifact(&output).ok_or_else(|| {
        ProjectCommandError::new("artifact_corrupt", "LichtFeld training produced no Gaussian artifact")
    })?;
    let relative = artifact.strip_prefix(project_root).map_err(|_| {
        ProjectCommandError::new("artifact_corrupt", "training artifact escaped the project root")
    })?;
    project.training.status = TrainingStatus::Complete;
    project.training.artifact_path = Some(normalized_relative(relative));
    project.training.last_iteration = project.training.total_iterations;
    project.training.error = None;
    project.revision += 1;
    crate::project::touch_project(&mut project);
    crate::project::write_project_atomic(project_root, &project)?;
    Ok(project)
}

pub fn fail_training_job_impl(project_root: &Path, message: &str) -> Result<XpanoProjectV2, ProjectCommandError> {
    let mut project = crate::project::read_project(project_root)?;
    project.training.status = TrainingStatus::Failed;
    project.training.error = Some(message.to_string());
    project.revision += 1;
    crate::project::touch_project(&mut project);
    crate::project::write_project_atomic(project_root, &project)?;
    Ok(project)
}

pub fn interrupt_training_job_impl(project_root: &Path) -> Result<XpanoProjectV2, ProjectCommandError> {
    let mut project = crate::project::read_project(project_root)?;
    project.training.status = TrainingStatus::Interrupted;
    project.training.error = Some("training was interrupted".to_string());
    project.revision += 1;
    crate::project::touch_project(&mut project);
    crate::project::write_project_atomic(project_root, &project)?;
    Ok(project)
}


#[cfg(test)]
mod tests {
    use super::*;
    use crate::contracts::{PointVariantStatus, ReconstructionStatus, TrainingStatus};

    fn training_project(name: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!("xpano-training-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let source = root.with_extension("jpg");
        std::fs::write(&source, b"image").unwrap();
        crate::project::create_project_impl("Training", &source, Some(&root)).unwrap();
        std::fs::create_dir_all(root.join("images")).unwrap();
        std::fs::create_dir_all(root.join("sparse").join("0")).unwrap();
        std::fs::write(root.join("images").join("0001.jpg"), b"image").unwrap();
        for name in ["cameras.bin", "images.bin", "points3D.bin"] {
            std::fs::write(root.join("sparse").join("0").join(name), b"colmap").unwrap();
        }
        let mut project = crate::project::read_project(&root).unwrap();
        project.reconstruction.status = ReconstructionStatus::Complete;
        project.reconstruction.colmap_path = Some(".".to_string());
        project.revisions.geometry = 3;
        project.geometry.variants[0].status = PointVariantStatus::Ready;
        crate::project::write_project_atomic(&root, &project).unwrap();
        root
    }

    #[test]
    fn begins_single_stage_training_from_the_project_colmap_dataset() {
        let root = training_project("begin");
        let project = crate::project::read_project(&root).unwrap();

        let updated = begin_training_impl(
            &root,
            project.revision,
            "training-job-1",
            &TrainingConfig::default(),
        )
        .unwrap();

        assert_eq!(updated.training.status, TrainingStatus::Running);
        assert_eq!(updated.training.input_revision, 3);
        assert_eq!(updated.training.source_job_id.as_deref(), Some("training-job-1"));
        assert_eq!(updated.training.total_iterations, 30_000);
        assert!(updated.training.output_path.as_deref().unwrap().contains("training-job-1"));
        let _ = std::fs::remove_dir_all(&root);
        let _ = std::fs::remove_file(root.with_extension("jpg"));
    }

    #[test]
    fn validates_training_inputs_without_marking_the_project_running() {
        let root = training_project("preflight-inputs");
        let before = crate::project::read_project(&root).unwrap();

        let dataset = validate_training_start_inputs(&root, &before, &TrainingConfig::default()).unwrap();
        let after = crate::project::read_project(&root).unwrap();

        assert_eq!(dataset, root.canonicalize().unwrap());
        assert_eq!(after.revision, before.revision);
        assert_eq!(after.training.status, TrainingStatus::Idle);
        let _ = std::fs::remove_dir_all(&root);
        let _ = std::fs::remove_file(root.with_extension("jpg"));
    }

    #[test]
    fn saves_training_config_without_starting_a_job() {
        let root = training_project("save-config");
        let before = crate::project::read_project(&root).unwrap();
        let mut config = TrainingConfig::default();
        config.iterations = 1234;
        let updated = save_training_config_impl(&root, before.revision, &config).unwrap();
        assert_eq!(updated.training.status, TrainingStatus::Idle);
        assert_eq!(updated.training.total_iterations, 1234);
        assert_eq!(updated.training.config["iterations"], serde_json::json!(1234));
        let unchanged = save_training_config_impl(&root, updated.revision, &config).unwrap();
        assert_eq!(unchanged.revision, updated.revision);
        let _ = std::fs::remove_dir_all(&root);
        let _ = std::fs::remove_file(root.with_extension("jpg"));
    }

    #[test]
    fn finalizes_training_only_after_a_gaussian_artifact_exists() {
        let root = training_project("finalize");
        let project = crate::project::read_project(&root).unwrap();
        let running = begin_training_impl(
            &root,
            project.revision,
            "training-job-2",
            &TrainingConfig::default(),
        )
        .unwrap();
        let output = root.join(running.training.output_path.as_deref().unwrap());
        std::fs::create_dir_all(&output).unwrap();
        std::fs::write(output.join("xpano_gaussian.ply"), b"ply").unwrap();
        std::fs::create_dir_all(output.join("checkpoints")).unwrap();
        std::thread::sleep(std::time::Duration::from_millis(5));
        std::fs::write(output.join("checkpoints").join("checkpoint.resume"), b"resume").unwrap();

        let completed = finalize_training_job_impl(&root).unwrap();

        assert_eq!(completed.training.status, TrainingStatus::Complete);
        assert!(completed.training.artifact_path.as_deref().unwrap().ends_with("xpano_gaussian.ply"));
        let _ = std::fs::remove_dir_all(&root);
        let _ = std::fs::remove_file(root.with_extension("jpg"));
    }

    #[test]
    fn rejects_training_when_the_colmap_dataset_is_missing() {
        let root = training_project("missing");
        std::fs::remove_dir_all(root.join("sparse")).unwrap();
        let project = crate::project::read_project(&root).unwrap();

        let error = begin_training_impl(
            &root,
            project.revision,
            "training-job-3",
            &TrainingConfig::default(),
        )
        .unwrap_err();

        assert_eq!(error.code, "training_not_ready");
        let _ = std::fs::remove_dir_all(&root);
        let _ = std::fs::remove_file(root.with_extension("jpg"));
    }
}
