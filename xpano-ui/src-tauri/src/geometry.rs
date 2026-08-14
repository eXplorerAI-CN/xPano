use crate::contracts::{
    JobState, PointCloudVariant, PointVariantKind, PointVariantStatus, ProjectWorkspace,
    XpanoProjectV2,
};
use crate::project::{
    atomic_replace, read_project, touch_project, write_json_value_atomic, write_project_atomic,
    ProjectCommandError,
};
use chrono::{SecondsFormat, Utc};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::fs::File;
use std::io::{BufReader, BufWriter, Read, Write};
use std::path::{Path, PathBuf};
use uuid::Uuid;
use tauri::Emitter;

const STANDARD_VARIANT_PATH: &str = "work/geometry/variants/standard/points3D.bin";
const BASE_IMAGES_PATH: &str = "work/geometry/base_images.bin";
const ACTIVE_TRANSACTION_PATH: &str = "work/geometry/active_variant_transaction.json";
const ACTIVE_ROLLBACK_PATH: &str = "work/geometry/active_points.rollback";
const ACTIVE_IMAGES_ROLLBACK_PATH: &str = "work/geometry/active_images.rollback";

fn geometry_error(code: &str, message: impl Into<String>) -> ProjectCommandError {
    ProjectCommandError::new(code, message.into())
}

fn io_error(context: &str, error: impl std::fmt::Display) -> ProjectCommandError {
    geometry_error("invalid_geometry", format!("{}: {}", context, error))
}

fn artifact_error(context: &str, error: impl std::fmt::Display) -> ProjectCommandError {
    geometry_error("artifact_corrupt", format!("{}: {}", context, error))
}

fn now_iso8601() -> String {
    Utc::now().to_rfc3339_opts(SecondsFormat::Millis, true)
}

fn authoritative_sparse_root(
    project_root: &Path,
    project: &XpanoProjectV2,
) -> Result<PathBuf, ProjectCommandError> {
    let mut candidates = Vec::new();
    if let Some(relative) = project.reconstruction.colmap_path.as_deref() {
        let root = if relative == "." {
            project_root.to_path_buf()
        } else {
            project_root.join(relative)
        };
        candidates.push(root.join("sparse/0"));
    }
    candidates.push(project_root.join("colmap/sparse/0"));
    candidates.push(project_root.join("sparse/0"));
    candidates
        .into_iter()
        .find(|candidate| {
            ["cameras.bin", "images.bin", "points3D.bin"]
                .iter()
                .all(|name| candidate.join(name).is_file())
        })
        .ok_or_else(|| {
            geometry_error(
                "artifact_corrupt",
                "project does not contain a complete COLMAP sparse/0 model",
            )
        })
}

fn validate_rigid_transform(matrix: &[f64; 16]) -> Result<(), ProjectCommandError> {
    if !matrix.iter().all(|value| value.is_finite()) {
        return Err(geometry_error(
            "invalid_geometry",
            "worldFromCanonical contains non-finite values",
        ));
    }
    let epsilon = 1e-8;
    if matrix[12].abs() > epsilon
        || matrix[13].abs() > epsilon
        || matrix[14].abs() > epsilon
        || (matrix[15] - 1.0).abs() > epsilon
    {
        return Err(geometry_error(
            "invalid_geometry",
            "worldFromCanonical is not an affine rigid transform",
        ));
    }
    let rotation = [
        [matrix[0], matrix[1], matrix[2]],
        [matrix[4], matrix[5], matrix[6]],
        [matrix[8], matrix[9], matrix[10]],
    ];
    for row in 0..3 {
        for column in 0..3 {
            let dot = (0..3)
                .map(|index| rotation[index][row] * rotation[index][column])
                .sum::<f64>();
            let expected = if row == column { 1.0 } else { 0.0 };
            if (dot - expected).abs() > 1e-6 {
                return Err(geometry_error(
                    "invalid_geometry",
                    "worldFromCanonical rotation is not orthonormal",
                ));
            }
        }
    }
    let determinant = rotation[0][0]
        * (rotation[1][1] * rotation[2][2] - rotation[1][2] * rotation[2][1])
        - rotation[0][1]
            * (rotation[1][0] * rotation[2][2] - rotation[1][2] * rotation[2][0])
        + rotation[0][2]
            * (rotation[1][0] * rotation[2][1] - rotation[1][1] * rotation[2][0]);
    if (determinant - 1.0).abs() > 1e-6 {
        return Err(geometry_error(
            "invalid_geometry",
            "worldFromCanonical rotation determinant must be +1",
        ));
    }
    Ok(())
}

fn inverse_rigid_transform(matrix: &[f64; 16]) -> Result<[f64; 16], ProjectCommandError> {
    validate_rigid_transform(matrix)?;
    let translation = [matrix[3], matrix[7], matrix[11]];
    let mut inverse = [0.0; 16];
    inverse[15] = 1.0;
    for row in 0..3 {
        for column in 0..3 {
            inverse[row * 4 + column] = matrix[column * 4 + row];
        }
        inverse[row * 4 + 3] = -(0..3)
            .map(|index| inverse[row * 4 + index] * translation[index])
            .sum::<f64>();
    }
    Ok(inverse)
}

fn transform_point(matrix: &[f64; 16], point: [f64; 3]) -> [f64; 3] {
    [
        matrix[0] * point[0] + matrix[1] * point[1] + matrix[2] * point[2] + matrix[3],
        matrix[4] * point[0] + matrix[5] * point[1] + matrix[6] * point[2] + matrix[7],
        matrix[8] * point[0] + matrix[9] * point[1] + matrix[10] * point[2] + matrix[11],
    ]
}

#[derive(Clone)]
struct ImagePose {
    id: u32,
    camera_id: u32,
    name: Vec<u8>,
    points2d_count: u64,
    rotation: [[f64; 3]; 3],
    translation: [f64; 3],
}

fn quaternion_to_rotation(quaternion: [f64; 4]) -> Result<[[f64; 3]; 3], ProjectCommandError> {
    if !quaternion.iter().all(|value| value.is_finite()) {
        return Err(geometry_error(
            "artifact_corrupt",
            "images.bin contains a non-finite quaternion",
        ));
    }
    let norm = quaternion.iter().map(|value| value * value).sum::<f64>().sqrt();
    if norm <= 1e-12 {
        return Err(geometry_error(
            "artifact_corrupt",
            "images.bin contains a zero quaternion",
        ));
    }
    let [w, x, y, z] = quaternion.map(|value| value / norm);
    Ok([
        [
            1.0 - 2.0 * (y * y + z * z),
            2.0 * (x * y - z * w),
            2.0 * (x * z + y * w),
        ],
        [
            2.0 * (x * y + z * w),
            1.0 - 2.0 * (x * x + z * z),
            2.0 * (y * z - x * w),
        ],
        [
            2.0 * (x * z - y * w),
            2.0 * (y * z + x * w),
            1.0 - 2.0 * (x * x + y * y),
        ],
    ])
}

fn rotation_to_quaternion(rotation: [[f64; 3]; 3]) -> [f64; 4] {
    let trace = rotation[0][0] + rotation[1][1] + rotation[2][2];
    let mut quaternion = if trace > 0.0 {
        let scale = (trace + 1.0).sqrt() * 2.0;
        [
            0.25 * scale,
            (rotation[2][1] - rotation[1][2]) / scale,
            (rotation[0][2] - rotation[2][0]) / scale,
            (rotation[1][0] - rotation[0][1]) / scale,
        ]
    } else if rotation[0][0] > rotation[1][1] && rotation[0][0] > rotation[2][2] {
        let scale = (1.0 + rotation[0][0] - rotation[1][1] - rotation[2][2]).sqrt() * 2.0;
        [
            (rotation[2][1] - rotation[1][2]) / scale,
            0.25 * scale,
            (rotation[0][1] + rotation[1][0]) / scale,
            (rotation[0][2] + rotation[2][0]) / scale,
        ]
    } else if rotation[1][1] > rotation[2][2] {
        let scale = (1.0 + rotation[1][1] - rotation[0][0] - rotation[2][2]).sqrt() * 2.0;
        [
            (rotation[0][2] - rotation[2][0]) / scale,
            (rotation[0][1] + rotation[1][0]) / scale,
            0.25 * scale,
            (rotation[1][2] + rotation[2][1]) / scale,
        ]
    } else {
        let scale = (1.0 + rotation[2][2] - rotation[0][0] - rotation[1][1]).sqrt() * 2.0;
        [
            (rotation[1][0] - rotation[0][1]) / scale,
            (rotation[0][2] + rotation[2][0]) / scale,
            (rotation[1][2] + rotation[2][1]) / scale,
            0.25 * scale,
        ]
    };
    let norm = quaternion.iter().map(|value| value * value).sum::<f64>().sqrt();
    for value in &mut quaternion {
        *value /= norm;
    }
    if quaternion[0] < 0.0 {
        for value in &mut quaternion {
            *value = -*value;
        }
    }
    quaternion
}

fn transformed_image_pose(
    rotation: [[f64; 3]; 3],
    translation: [f64; 3],
    world_from_canonical: &[f64; 16],
) -> ([[f64; 3]; 3], [f64; 3]) {
    let world_rotation = [
        [world_from_canonical[0], world_from_canonical[1], world_from_canonical[2]],
        [world_from_canonical[4], world_from_canonical[5], world_from_canonical[6]],
        [world_from_canonical[8], world_from_canonical[9], world_from_canonical[10]],
    ];
    let mut transformed_rotation = [[0.0; 3]; 3];
    for row in 0..3 {
        for column in 0..3 {
            transformed_rotation[row][column] = (0..3)
                .map(|index| rotation[row][index] * world_rotation[column][index])
                .sum();
        }
    }
    let world_translation = [
        world_from_canonical[3],
        world_from_canonical[7],
        world_from_canonical[11],
    ];
    let mut transformed_translation = translation;
    for row in 0..3 {
        transformed_translation[row] -= (0..3)
            .map(|index| transformed_rotation[row][index] * world_translation[index])
            .sum::<f64>();
    }
    (transformed_rotation, transformed_translation)
}

fn process_images_file(
    source: &Path,
    target: Option<(&Path, &[f64; 16])>,
) -> Result<Vec<ImagePose>, ProjectCommandError> {
    let source_file = File::open(source)
        .map_err(|error| artifact_error("failed to open images.bin", error))?;
    let source_len = source_file
        .metadata()
        .map_err(|error| artifact_error("failed to inspect images.bin", error))?
        .len();
    let mut reader = BufReader::new(source_file);
    let count_bytes = read_exact::<8>(&mut reader, "missing COLMAP image count")?;
    let count = u64::from_le_bytes(count_bytes);
    if count == 0 || count > source_len.saturating_sub(8) / 69 {
        return Err(geometry_error(
            "artifact_corrupt",
            "images.bin has an invalid record count",
        ));
    }
    let mut writer = if let Some((path, _)) = target {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|error| io_error("failed to create image artifact directory", error))?;
        }
        let file = File::create(path)
            .map_err(|error| io_error("failed to create transformed images file", error))?;
        let mut writer = BufWriter::new(file);
        writer
            .write_all(&count_bytes)
            .map_err(|error| io_error("failed to write COLMAP image count", error))?;
        Some(writer)
    } else {
        None
    };
    let mut poses = Vec::with_capacity(usize::try_from(count).unwrap_or(0).min(100_000));
    for _ in 0..count {
        let id_bytes = read_exact::<4>(&mut reader, "truncated COLMAP image id")?;
        let id = u32::from_le_bytes(id_bytes);
        let mut quaternion = [0.0; 4];
        for value in &mut quaternion {
            *value = f64::from_le_bytes(read_exact::<8>(
                &mut reader,
                "truncated COLMAP image quaternion",
            )?);
        }
        let rotation = quaternion_to_rotation(quaternion)?;
        let mut translation = [0.0; 3];
        for value in &mut translation {
            *value = f64::from_le_bytes(read_exact::<8>(
                &mut reader,
                "truncated COLMAP image translation",
            )?);
        }
        if !translation.iter().all(|value| value.is_finite()) {
            return Err(geometry_error(
                "artifact_corrupt",
                "images.bin contains non-finite translation values",
            ));
        }
        let camera_id_bytes = read_exact::<4>(&mut reader, "truncated COLMAP camera id")?;
        let camera_id = u32::from_le_bytes(camera_id_bytes);
        let mut name = Vec::new();
        loop {
            let byte = read_exact::<1>(&mut reader, "unterminated COLMAP image name")?[0];
            name.push(byte);
            if byte == 0 {
                break;
            }
            if name.len() > 1024 * 1024 {
                return Err(geometry_error(
                    "artifact_corrupt",
                    "COLMAP image name is unreasonably long",
                ));
            }
        }
        let points_count_bytes = read_exact::<8>(&mut reader, "truncated points2D count")?;
        let points2d_count = u64::from_le_bytes(points_count_bytes);
        let points_bytes = points2d_count.checked_mul(24).ok_or_else(|| {
            geometry_error("artifact_corrupt", "COLMAP points2D length overflow")
        })?;

        let (output_rotation, output_translation) = if let Some((_, transform)) = target {
            transformed_image_pose(rotation, translation, transform)
        } else {
            (rotation, translation)
        };
        if let Some(writer) = writer.as_mut() {
            writer
                .write_all(&id_bytes)
                .map_err(|error| io_error("failed to write COLMAP image id", error))?;
            for value in rotation_to_quaternion(output_rotation) {
                writer
                    .write_all(&value.to_le_bytes())
                    .map_err(|error| io_error("failed to write image quaternion", error))?;
            }
            for value in output_translation {
                writer
                    .write_all(&value.to_le_bytes())
                    .map_err(|error| io_error("failed to write image translation", error))?;
            }
            writer
                .write_all(&camera_id_bytes)
                .and_then(|_| writer.write_all(&name))
                .and_then(|_| writer.write_all(&points_count_bytes))
                .map_err(|error| io_error("failed to write COLMAP image record", error))?;
            copy_exact(&mut reader, writer, points_bytes)?;
        } else {
            let mut sink = std::io::sink();
            let copied = std::io::copy(&mut reader.by_ref().take(points_bytes), &mut sink)
                .map_err(|error| artifact_error("failed to read COLMAP points2D", error))?;
            if copied != points_bytes {
                return Err(geometry_error(
                    "artifact_corrupt",
                    "images.bin contains truncated points2D data",
                ));
            }
        }
        poses.push(ImagePose {
            id,
            camera_id,
            name,
            points2d_count,
            rotation: output_rotation,
            translation: output_translation,
        });
    }
    let mut trailing = [0u8; 1];
    if reader
        .read(&mut trailing)
        .map_err(|error| artifact_error("failed to finish reading images.bin", error))?
        != 0
    {
        return Err(geometry_error(
            "artifact_corrupt",
            "images.bin contains trailing bytes",
        ));
    }
    if let Some(mut writer) = writer {
        writer
            .flush()
            .and_then(|_| writer.get_ref().sync_all())
            .map_err(|error| io_error("failed to sync transformed images", error))?;
    }
    Ok(poses)
}

fn read_point_samples(path: &Path, limit: usize) -> Result<Vec<[f64; 3]>, ProjectCommandError> {
    let file = File::open(path).map_err(|error| artifact_error("failed to sample points3D.bin", error))?;
    let mut reader = BufReader::new(file);
    let count = u64::from_le_bytes(read_exact::<8>(&mut reader, "missing COLMAP point count")?);
    let mut samples = Vec::new();
    for index in 0..count {
        let _ = read_exact::<8>(&mut reader, "truncated COLMAP point id")?;
        let mut xyz = [0.0; 3];
        for value in &mut xyz {
            *value = f64::from_le_bytes(read_exact::<8>(
                &mut reader,
                "truncated COLMAP point coordinates",
            )?);
        }
        let _ = read_exact::<3>(&mut reader, "truncated COLMAP point color")?;
        let _ = read_exact::<8>(&mut reader, "truncated COLMAP point error")?;
        let track_len = u64::from_le_bytes(read_exact::<8>(&mut reader, "truncated track length")?);
        let mut sink = std::io::sink();
        let track_bytes = track_len.checked_mul(8).ok_or_else(|| {
            geometry_error("artifact_corrupt", "COLMAP point track length overflow")
        })?;
        if std::io::copy(&mut reader.by_ref().take(track_bytes), &mut sink)
            .map_err(|error| artifact_error("failed to skip COLMAP point track", error))?
            != track_bytes
        {
            return Err(geometry_error("artifact_corrupt", "truncated COLMAP point track"));
        }
        if index < limit as u64 {
            samples.push(xyz);
        }
    }
    Ok(samples)
}

fn camera_coordinates(pose: &ImagePose, point: [f64; 3]) -> [f64; 3] {
    let mut coordinates = pose.translation;
    for row in 0..3 {
        coordinates[row] += (0..3)
            .map(|column| pose.rotation[row][column] * point[column])
            .sum::<f64>();
    }
    coordinates
}

fn validate_projection_invariance(
    canonical_poses: &[ImagePose],
    world_poses: &[ImagePose],
    canonical_points: &Path,
    world_from_canonical: &[f64; 16],
) -> Result<(), ProjectCommandError> {
    let samples = read_point_samples(canonical_points, 8)?;
    for (canonical_pose, world_pose) in canonical_poses.iter().zip(world_poses).take(8) {
        if canonical_pose.id != world_pose.id
            || canonical_pose.camera_id != world_pose.camera_id
            || canonical_pose.name != world_pose.name
            || canonical_pose.points2d_count != world_pose.points2d_count
        {
            return Err(geometry_error(
                "artifact_corrupt",
                "image identity changed during geometry materialization",
            ));
        }
        for canonical_point in &samples {
            let canonical_camera = camera_coordinates(canonical_pose, *canonical_point);
            let world_point = transform_point(world_from_canonical, *canonical_point);
            let world_camera = camera_coordinates(world_pose, world_point);
            if canonical_camera[2].abs() <= 1e-9 || world_camera[2].abs() <= 1e-9 {
                continue;
            }
            let canonical_projection = [
                canonical_camera[0] / canonical_camera[2],
                canonical_camera[1] / canonical_camera[2],
            ];
            let world_projection = [
                world_camera[0] / world_camera[2],
                world_camera[1] / world_camera[2],
            ];
            if (canonical_projection[0] - world_projection[0]).abs() > 1e-8
                || (canonical_projection[1] - world_projection[1]).abs() > 1e-8
            {
                return Err(geometry_error(
                    "artifact_corrupt",
                    "camera projection changed during geometry materialization",
                ));
            }
        }
    }
    Ok(())
}

fn read_exact<const N: usize>(
    reader: &mut BufReader<File>,
    label: &str,
) -> Result<[u8; N], ProjectCommandError> {
    let mut bytes = [0u8; N];
    reader
        .read_exact(&mut bytes)
        .map_err(|error| artifact_error(label, error))?;
    Ok(bytes)
}

fn copy_exact(
    reader: &mut BufReader<File>,
    writer: &mut BufWriter<File>,
    mut bytes: u64,
) -> Result<(), ProjectCommandError> {
    let mut buffer = [0u8; 8192];
    while bytes > 0 {
        let chunk = usize::try_from(bytes.min(buffer.len() as u64)).unwrap();
        reader
            .read_exact(&mut buffer[..chunk])
            .map_err(|error| artifact_error("truncated COLMAP point track", error))?;
        writer
            .write_all(&buffer[..chunk])
            .map_err(|error| io_error("failed to write COLMAP point track", error))?;
        bytes -= chunk as u64;
    }
    Ok(())
}

fn process_points_file(
    source: &Path,
    target: Option<(&Path, &[f64; 16])>,
) -> Result<u64, ProjectCommandError> {
    let source_file = File::open(source)
        .map_err(|error| artifact_error("failed to open points3D.bin", error))?;
    let source_len = source_file
        .metadata()
        .map_err(|error| artifact_error("failed to inspect points3D.bin", error))?
        .len();
    let mut reader = BufReader::new(source_file);
    let count_bytes = read_exact::<8>(&mut reader, "missing COLMAP point count")?;
    let count = u64::from_le_bytes(count_bytes);
    if count == 0 || count > source_len.saturating_sub(8) / 51 {
        return Err(geometry_error(
            "artifact_corrupt",
            "points3D.bin has an invalid record count",
        ));
    }

    let mut writer = if let Some((path, _)) = target {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|error| io_error("failed to create point variant directory", error))?;
        }
        let file = File::create(path)
            .map_err(|error| io_error("failed to create transformed points file", error))?;
        let mut writer = BufWriter::new(file);
        writer
            .write_all(&count_bytes)
            .map_err(|error| io_error("failed to write COLMAP point count", error))?;
        Some(writer)
    } else {
        None
    };

    for _ in 0..count {
        let id = read_exact::<8>(&mut reader, "truncated COLMAP point id")?;
        let mut xyz = [0.0; 3];
        for value in &mut xyz {
            *value = f64::from_le_bytes(read_exact::<8>(
                &mut reader,
                "truncated COLMAP point coordinates",
            )?);
        }
        if !xyz.iter().all(|value| value.is_finite()) {
            return Err(geometry_error(
                "artifact_corrupt",
                "points3D.bin contains non-finite coordinates",
            ));
        }
        let rgb = read_exact::<3>(&mut reader, "truncated COLMAP point color")?;
        let error_bytes = read_exact::<8>(&mut reader, "truncated COLMAP point error")?;
        let error = f64::from_le_bytes(error_bytes);
        if !error.is_finite() {
            return Err(geometry_error(
                "artifact_corrupt",
                "points3D.bin contains a non-finite reprojection error",
            ));
        }
        let track_len_bytes = read_exact::<8>(&mut reader, "truncated COLMAP track length")?;
        let track_len = u64::from_le_bytes(track_len_bytes);
        let track_bytes = track_len.checked_mul(8).ok_or_else(|| {
            geometry_error("artifact_corrupt", "COLMAP point track length overflow")
        })?;

        if let Some(writer) = writer.as_mut() {
            let matrix = target.unwrap().1;
            let transformed = transform_point(matrix, xyz);
            if !transformed.iter().all(|value| value.is_finite()) {
                return Err(geometry_error(
                    "invalid_geometry",
                    "point transform produced non-finite coordinates",
                ));
            }
            writer
                .write_all(&id)
                .map_err(|error| io_error("failed to write COLMAP point id", error))?;
            for value in transformed {
                writer
                    .write_all(&value.to_le_bytes())
                    .map_err(|error| io_error("failed to write COLMAP point coordinates", error))?;
            }
            writer
                .write_all(&rgb)
                .and_then(|_| writer.write_all(&error_bytes))
                .and_then(|_| writer.write_all(&track_len_bytes))
                .map_err(|error| io_error("failed to write COLMAP point record", error))?;
            copy_exact(&mut reader, writer, track_bytes)?;
        } else {
            let mut sink = std::io::sink();
            let copied = std::io::copy(&mut reader.by_ref().take(track_bytes), &mut sink)
                .map_err(|error| artifact_error("failed to read COLMAP point track", error))?;
            if copied != track_bytes {
                return Err(geometry_error(
                    "artifact_corrupt",
                    "points3D.bin contains a truncated track",
                ));
            }
        }
    }
    let mut trailing = [0u8; 1];
    if reader
        .read(&mut trailing)
        .map_err(|error| artifact_error("failed to finish reading points3D.bin", error))?
        != 0
    {
        return Err(geometry_error(
            "artifact_corrupt",
            "points3D.bin contains trailing bytes",
        ));
    }
    if let Some(mut writer) = writer {
        writer
            .flush()
            .map_err(|error| io_error("failed to flush transformed points", error))?;
        writer
            .get_ref()
            .sync_all()
            .map_err(|error| io_error("failed to sync transformed points", error))?;
    }
    Ok(count)
}

pub(crate) fn read_points_count(path: &Path) -> Result<u64, ProjectCommandError> {
    process_points_file(path, None)
}

fn sha256_file(path: &Path) -> Result<String, ProjectCommandError> {
    let file = File::open(path).map_err(|error| artifact_error("failed to hash point variant", error))?;
    let mut reader = BufReader::new(file);
    let mut digest = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    loop {
        let read = reader
            .read(&mut buffer)
            .map_err(|error| artifact_error("failed to hash point variant", error))?;
        if read == 0 {
            break;
        }
        digest.update(&buffer[..read]);
    }
    Ok(format!("{:x}", digest.finalize()))
}

fn transformed_file_atomic(
    source: &Path,
    target: &Path,
    transform: &[f64; 16],
) -> Result<(u64, String), ProjectCommandError> {
    validate_rigid_transform(transform)?;
    let temp = target.with_extension("bin.tmp");
    let _ = std::fs::remove_file(&temp);
    let count = match process_points_file(source, Some((&temp, transform))) {
        Ok(count) => count,
        Err(error) => {
            let _ = std::fs::remove_file(&temp);
            return Err(error);
        }
    };
    let parsed_count = read_points_count(&temp)?;
    if parsed_count != count {
        let _ = std::fs::remove_file(&temp);
        return Err(geometry_error(
            "artifact_corrupt",
            "transformed point variant changed record count",
        ));
    }
    let checksum = sha256_file(&temp)?;
    atomic_replace(&temp, target)
        .map_err(|error| io_error("failed to atomically replace point variant", error))?;
    Ok((count, checksum))
}

fn copy_file_atomic(source: &Path, target: &Path) -> Result<(), ProjectCommandError> {
    let parent = target
        .parent()
        .ok_or_else(|| geometry_error("invalid_geometry", "artifact has no parent directory"))?;
    std::fs::create_dir_all(parent)
        .map_err(|error| io_error("failed to create geometry artifact directory", error))?;
    let temp = target.with_extension("tmp");
    let _ = std::fs::remove_file(&temp);
    let mut input = BufReader::new(
        File::open(source).map_err(|error| artifact_error("failed to open source artifact", error))?,
    );
    let file = File::create(&temp)
        .map_err(|error| io_error("failed to create geometry artifact temp file", error))?;
    let mut output = BufWriter::new(file);
    std::io::copy(&mut input, &mut output)
        .map_err(|error| io_error("failed to copy geometry artifact", error))?;
    output
        .flush()
        .and_then(|_| output.get_ref().sync_all())
        .map_err(|error| io_error("failed to sync geometry artifact", error))?;
    atomic_replace(&temp, target)
        .map_err(|error| io_error("failed to atomically replace geometry artifact", error))
}

fn latest_reconstruction_job_id(project: &XpanoProjectV2) -> Option<String> {
    project
        .jobs
        .iter()
        .rev()
        .find(|job| {
            job.workspace == ProjectWorkspace::Reconstruction && job.state == JobState::Completed
        })
        .map(|job| job.job_id.clone())
}

fn latest_reconstruction_job_id_any_state(project: &XpanoProjectV2) -> Option<String> {
    project
        .jobs
        .iter()
        .rev()
        .find(|job| job.workspace == ProjectWorkspace::Reconstruction)
        .map(|job| job.job_id.clone())
}

fn upsert_variant(project: &mut XpanoProjectV2, variant: PointCloudVariant) {
    if let Some(existing) = project
        .geometry
        .variants
        .iter_mut()
        .find(|existing| existing.id == variant.id)
    {
        *existing = variant;
    } else {
        project.geometry.variants.push(variant);
    }
}

fn recover_active_variant_transaction(
    project_root: &Path,
) -> Result<(), ProjectCommandError> {
    let marker_path = project_root.join(ACTIVE_TRANSACTION_PATH);
    if !marker_path.is_file() {
        return Ok(());
    }
    let marker: serde_json::Value = serde_json::from_slice(
        &std::fs::read(&marker_path)
            .map_err(|error| artifact_error("failed to read geometry transaction", error))?,
    )
    .map_err(|error| artifact_error("failed to parse geometry transaction", error))?;
    let project = read_project(project_root)?;
    let sparse = authoritative_sparse_root(project_root, &project)?;
    let rollback_path = project_root.join(ACTIVE_ROLLBACK_PATH);
    let images_rollback_path = project_root.join(ACTIVE_IMAGES_ROLLBACK_PATH);
    let committed = if let Some(new_revision) = marker
        .get("newTransformRevision")
        .and_then(serde_json::Value::as_u64)
    {
        project.geometry.transform.revision == new_revision
    } else if let Some(new_variant_id) = marker
        .get("newVariantId")
        .and_then(serde_json::Value::as_str)
    {
        project.geometry.active_variant_id == new_variant_id
    } else {
        return Err(geometry_error(
            "artifact_corrupt",
            "geometry transaction is incomplete",
        ));
    };
    if !committed {
        rollback_active_geometry_files(project_root, &sparse)?;
        return Ok(());
    } else {
        let _ = std::fs::remove_file(&rollback_path);
        let _ = std::fs::remove_file(&images_rollback_path);
    }
    std::fs::remove_file(marker_path)
        .map_err(|error| io_error("failed to clear geometry transaction", error))?;
    Ok(())
}

fn rollback_active_geometry_files(
    project_root: &Path,
    sparse: &Path,
) -> Result<(), ProjectCommandError> {
    let points_rollback = project_root.join(ACTIVE_ROLLBACK_PATH);
    let images_rollback = project_root.join(ACTIVE_IMAGES_ROLLBACK_PATH);
    if points_rollback.is_file() {
        atomic_replace(&points_rollback, &sparse.join("points3D.bin"))
            .map_err(|error| io_error("failed to roll back active points", error))?;
    }
    if images_rollback.is_file() {
        atomic_replace(&images_rollback, &sparse.join("images.bin"))
            .map_err(|error| io_error("failed to roll back active images", error))?;
    }
    // WARN: Keep the transaction marker until every available rollback file is restored.
    std::fs::remove_file(project_root.join(ACTIVE_TRANSACTION_PATH))
        .map_err(|error| io_error("failed to clear rolled-back geometry transaction", error))?;
    Ok(())
}

pub(crate) fn apply_world_transform_impl(
    project_root: &Path,
    expected_transform_revision: u64,
    world_from_canonical: [f64; 16],
) -> Result<XpanoProjectV2, ProjectCommandError> {
    recover_active_variant_transaction(project_root)?;
    validate_rigid_transform(&world_from_canonical)?;
    let mut project = read_project(project_root)?;
    if project.geometry.transform.revision != expected_transform_revision {
        return Err(geometry_error(
            "revision_conflict",
            format!(
                "geometry transform revision changed from {} to {}",
                expected_transform_revision, project.geometry.transform.revision
            ),
        ));
    }
    if project
        .geometry
        .transform
        .world_from_canonical
        .iter()
        .zip(world_from_canonical)
        .all(|(current, requested)| (*current - requested).abs() <= 1e-12)
    {
        return Ok(project);
    }
    let variant = project
        .geometry
        .variants
        .iter()
        .find(|variant| variant.id == project.geometry.active_variant_id)
        .cloned()
        .ok_or_else(|| geometry_error("invalid_geometry", "active point variant does not exist"))?;
    if variant.status != PointVariantStatus::Ready {
        return Err(geometry_error(
            "invalid_geometry",
            "active point variant is not ready for materialization",
        ));
    }
    let canonical_points = project_root.join(&variant.canonical_path);
    let canonical_count = read_points_count(&canonical_points)?;
    let canonical_checksum = sha256_file(&canonical_points)?;
    if canonical_count != variant.point_count
        || (!variant.checksum_sha256.is_empty()
            && canonical_checksum != variant.checksum_sha256)
    {
        return Err(geometry_error(
            "artifact_corrupt",
            "active canonical point variant changed",
        ));
    }
    let base_images = project_root.join(BASE_IMAGES_PATH);
    let canonical_poses = process_images_file(&base_images, None)?;
    let sparse = authoritative_sparse_root(project_root, &project)?;
    let active_points = sparse.join("points3D.bin");
    let active_images = sparse.join("images.bin");
    let points_temp = active_points.with_extension("bin.tmp");
    let images_temp = active_images.with_extension("bin.tmp");
    let _ = std::fs::remove_file(&points_temp);
    let _ = std::fs::remove_file(&images_temp);
    process_points_file(
        &canonical_points,
        Some((&points_temp, &world_from_canonical)),
    )?;
    let written_poses = match process_images_file(
        &base_images,
        Some((&images_temp, &world_from_canonical)),
    ) {
        Ok(poses) => poses,
        Err(error) => {
            let _ = std::fs::remove_file(&points_temp);
            let _ = std::fs::remove_file(&images_temp);
            return Err(error);
        }
    };
    if read_points_count(&points_temp)? != canonical_count {
        let _ = std::fs::remove_file(&points_temp);
        let _ = std::fs::remove_file(&images_temp);
        return Err(geometry_error(
            "artifact_corrupt",
            "world point materialization changed record count",
        ));
    }
    let parsed_poses = process_images_file(&images_temp, None)?;
    if canonical_poses.len() != parsed_poses.len()
        || written_poses.len() != parsed_poses.len()
    {
        let _ = std::fs::remove_file(&points_temp);
        let _ = std::fs::remove_file(&images_temp);
        return Err(geometry_error(
            "artifact_corrupt",
            "world image materialization changed record count",
        ));
    }
    validate_projection_invariance(
        &canonical_poses,
        &parsed_poses,
        &canonical_points,
        &world_from_canonical,
    )?;

    let points_rollback = project_root.join(ACTIVE_ROLLBACK_PATH);
    let images_rollback = project_root.join(ACTIVE_IMAGES_ROLLBACK_PATH);
    copy_file_atomic(&active_points, &points_rollback)?;
    copy_file_atomic(&active_images, &images_rollback)?;
    let new_transform_revision = project.geometry.transform.revision.saturating_add(1);
    write_json_value_atomic(
        &project_root.join(ACTIVE_TRANSACTION_PATH),
        &serde_json::json!({
            "kind": "transform",
            "newTransformRevision": new_transform_revision,
        }),
    )?;
    if let Err(error) = atomic_replace(&points_temp, &active_points) {
        let _ = std::fs::remove_file(&images_temp);
        rollback_active_geometry_files(project_root, &sparse)?;
        return Err(io_error("failed to replace world points", error));
    }
    if let Err(error) = atomic_replace(&images_temp, &active_images) {
        let _ = std::fs::remove_file(&images_temp);
        rollback_active_geometry_files(project_root, &sparse)?;
        return Err(io_error("failed to replace world images", error));
    }

    project.geometry.transform.world_from_canonical = world_from_canonical;
    project.geometry.transform.revision = new_transform_revision;
    project.revisions.geometry += 1;
    project.revision += 1;
    touch_project(&mut project);
    if let Err(error) = write_project_atomic(project_root, &project) {
        rollback_active_geometry_files(project_root, &sparse)?;
        return Err(error);
    }
    let _ = std::fs::remove_file(points_rollback);
    let _ = std::fs::remove_file(images_rollback);
    let _ = std::fs::remove_file(project_root.join(ACTIVE_TRANSACTION_PATH));
    Ok(project)
}

pub(crate) fn reset_geometry_from_reconstruction_impl(
    project_root: &Path,
    project: &mut XpanoProjectV2,
) -> Result<(), ProjectCommandError> {
    recover_active_variant_transaction(project_root)?;
    let sparse = authoritative_sparse_root(project_root, project)?;
    let identity = [
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    ];
    let target = project_root.join(STANDARD_VARIANT_PATH);
    let (point_count, checksum_sha256) =
        transformed_file_atomic(&sparse.join("points3D.bin"), &target, &identity)?;
    copy_file_atomic(&sparse.join("images.bin"), &project_root.join(BASE_IMAGES_PATH))?;

    let transform_revision = project.geometry.transform.revision.saturating_add(1);
    for variant in &mut project.geometry.variants {
        variant.status = PointVariantStatus::Stale;
    }
    let standard = PointCloudVariant {
        id: "standard".to_string(),
        label: "标准点云".to_string(),
        kind: PointVariantKind::Standard,
        canonical_path: STANDARD_VARIANT_PATH.to_string(),
        point_count,
        created_at: now_iso8601(),
        source_job_id: latest_reconstruction_job_id_any_state(project),
        protected: true,
        checksum_sha256,
        transform_revision,
        status: PointVariantStatus::Ready,
    };
    upsert_variant(project, standard);
    project.geometry.transform.world_from_canonical = identity;
    project.geometry.transform.revision = transform_revision;
    project.geometry.active_variant_id = "standard".to_string();
    project.revisions.geometry += 1;
    Ok(())
}

pub(crate) fn materialize_standard_variant_impl(
    project_root: &Path,
) -> Result<PointCloudVariant, ProjectCommandError> {
    recover_active_variant_transaction(project_root)?;
    let mut project = read_project(project_root)?;
    let sparse = authoritative_sparse_root(project_root, &project)?;
    let source_points = sparse.join("points3D.bin");
    let target_points = project_root.join(STANDARD_VARIANT_PATH);
    if project.geometry.active_variant_id != "standard" && !target_points.is_file() {
        return Err(geometry_error(
            "invalid_geometry",
            "cannot derive a missing standard variant while a densified variant is active",
        ));
    }
    let inverse = inverse_rigid_transform(&project.geometry.transform.world_from_canonical)?;
    let (point_count, checksum_sha256) = if target_points.is_file() {
        let count = read_points_count(&target_points)?;
        let checksum = sha256_file(&target_points)?;
        (count, checksum)
    } else {
        transformed_file_atomic(&source_points, &target_points, &inverse)?
    };

    let base_images = project_root.join(BASE_IMAGES_PATH);
    if !base_images.is_file() {
        if project.geometry.transform.revision != 0 {
            return Err(geometry_error(
                "invalid_geometry",
                "cannot derive canonical image extrinsics after geometry transforms have been applied",
            ));
        }
        copy_file_atomic(&sparse.join("images.bin"), &base_images)?;
    }

    let existing_created_at = project
        .geometry
        .variants
        .iter()
        .find(|variant| variant.id == "standard" && variant.point_count > 0)
        .map(|variant| variant.created_at.clone());
    let variant = PointCloudVariant {
        id: "standard".to_string(),
        label: "标准点云".to_string(),
        kind: PointVariantKind::Standard,
        canonical_path: STANDARD_VARIANT_PATH.to_string(),
        point_count,
        created_at: existing_created_at.unwrap_or_else(now_iso8601),
        source_job_id: latest_reconstruction_job_id(&project),
        protected: true,
        checksum_sha256,
        transform_revision: project.geometry.transform.revision,
        status: PointVariantStatus::Ready,
    };
    upsert_variant(&mut project, variant.clone());
    project.revisions.geometry += 1;
    project.revision += 1;
    touch_project(&mut project);
    write_project_atomic(project_root, &project)?;
    Ok(variant)
}

#[tauri::command]
pub fn materialize_standard_variant(
    app: tauri::AppHandle,
    project_root: String,
) -> Result<PointCloudVariant, ProjectCommandError> {
    let variant = materialize_standard_variant_impl(Path::new(&project_root))?;
    let project = read_project(Path::new(&project_root))?;
    let _ = app.emit(
        "project:updated",
        crate::media::ProjectUpdatedEvent {
            project_root,
            project,
        },
    );
    Ok(variant)
}

pub(crate) fn register_densified_variant_impl(
    project_root: &Path,
    dense_candidate: &Path,
    source_job_id: Option<&str>,
) -> Result<PointCloudVariant, ProjectCommandError> {
    recover_active_variant_transaction(project_root)?;
    let root = project_root
        .canonicalize()
        .map_err(|error| io_error("failed to resolve project root", error))?;
    let candidate = dense_candidate
        .canonicalize()
        .map_err(|error| artifact_error("failed to resolve densified result", error))?;
    if !candidate.starts_with(&root) {
        return Err(geometry_error(
            "invalid_geometry",
            "densified result is outside the project directory",
        ));
    }
    let mut project = read_project(project_root)?;
    let inverse = inverse_rigid_transform(&project.geometry.transform.world_from_canonical)?;
    let id = format!("densified_{}", Uuid::new_v4().simple());
    let relative = format!("work/geometry/variants/{}/points3D.bin", id);
    let target = project_root.join(&relative);
    let (point_count, checksum_sha256) = transformed_file_atomic(&candidate, &target, &inverse)?;
    let variant = PointCloudVariant {
        id: id.clone(),
        label: format!("致密化 #{}", project.geometry.variants.iter().filter(|v| v.kind == PointVariantKind::Densified).count() + 1),
        kind: PointVariantKind::Densified,
        canonical_path: relative,
        point_count,
        created_at: now_iso8601(),
        source_job_id: source_job_id.map(str::to_string),
        protected: false,
        checksum_sha256,
        transform_revision: project.geometry.transform.revision,
        status: PointVariantStatus::Ready,
    };
    project.geometry.variants.push(variant.clone());
    project.revisions.geometry += 1;
    project.revision += 1;
    touch_project(&mut project);
    write_project_atomic(project_root, &project)?;
    Ok(variant)
}

pub(crate) fn set_active_point_variant_impl(
    project_root: &Path,
    variant_id: &str,
    expected_transform_revision: u64,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    recover_active_variant_transaction(project_root)?;
    let mut project = read_project(project_root)?;
    if project.geometry.transform.revision != expected_transform_revision {
        return Err(geometry_error(
            "revision_conflict",
            format!(
                "geometry transform revision changed from {} to {}",
                expected_transform_revision, project.geometry.transform.revision
            ),
        ));
    }
    let variant = project
        .geometry
        .variants
        .iter()
        .find(|variant| variant.id == variant_id)
        .cloned()
        .ok_or_else(|| geometry_error("invalid_geometry", "point variant does not exist"))?;
    if variant.status != PointVariantStatus::Ready {
        return Err(geometry_error(
            "invalid_geometry",
            "point variant is not ready for activation",
        ));
    }
    let canonical = project_root.join(&variant.canonical_path);
    let point_count = read_points_count(&canonical)?;
    let checksum = sha256_file(&canonical)?;
    if variant.point_count != point_count
        || (!variant.checksum_sha256.is_empty() && variant.checksum_sha256 != checksum)
    {
        return Err(geometry_error(
            "artifact_corrupt",
            "point variant checksum or record count changed",
        ));
    }
    validate_rigid_transform(&project.geometry.transform.world_from_canonical)?;
    let sparse = authoritative_sparse_root(project_root, &project)?;
    let active = sparse.join("points3D.bin");
    let temp = active.with_extension("bin.tmp");
    let _ = std::fs::remove_file(&temp);
    process_points_file(
        &canonical,
        Some((&temp, &project.geometry.transform.world_from_canonical)),
    )?;
    if read_points_count(&temp)? != point_count {
        let _ = std::fs::remove_file(&temp);
        return Err(geometry_error(
            "artifact_corrupt",
            "materialized training point cloud changed record count",
        ));
    }

    let rollback = project_root.join(ACTIVE_ROLLBACK_PATH);
    copy_file_atomic(&active, &rollback)?;
    write_json_value_atomic(
        &project_root.join(ACTIVE_TRANSACTION_PATH),
        &serde_json::json!({
            "oldVariantId": project.geometry.active_variant_id,
            "newVariantId": variant_id,
        }),
    )?;
    atomic_replace(&temp, &active)
        .map_err(|error| io_error("failed to activate point variant", error))?;

    project.geometry.active_variant_id = variant_id.to_string();
    if let Some(metadata) = project
        .geometry
        .variants
        .iter_mut()
        .find(|metadata| metadata.id == variant_id)
    {
        metadata.checksum_sha256 = checksum;
        metadata.status = PointVariantStatus::Ready;
    }
    project.revisions.geometry += 1;
    project.revision += 1;
    touch_project(&mut project);
    if let Err(error) = write_project_atomic(project_root, &project) {
        rollback_active_geometry_files(project_root, &sparse)?;
        return Err(error);
    }
    let _ = std::fs::remove_file(rollback);
    let _ = std::fs::remove_file(project_root.join(ACTIVE_TRANSACTION_PATH));
    Ok(project)
}

pub(crate) fn delete_point_variant_impl(
    project_root: &Path,
    variant_id: &str,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    recover_active_variant_transaction(project_root)?;
    let mut project = read_project(project_root)?;
    if variant_id == "standard" {
        return Err(geometry_error(
            "invalid_geometry",
            "the standard point variant cannot be deleted",
        ));
    }
    if project.geometry.active_variant_id == variant_id {
        return Err(geometry_error(
            "invalid_geometry",
            "the active training point variant cannot be deleted",
        ));
    }
    let index = project
        .geometry
        .variants
        .iter()
        .position(|variant| variant.id == variant_id)
        .ok_or_else(|| geometry_error("invalid_geometry", "point variant does not exist"))?;
    if project.geometry.variants[index].protected {
        return Err(geometry_error(
            "invalid_geometry",
            "protected point variants cannot be deleted",
        ));
    }
    let canonical = project_root.join(&project.geometry.variants[index].canonical_path);
    project.geometry.variants.remove(index);
    project.revisions.geometry += 1;
    project.revision += 1;
    touch_project(&mut project);
    write_project_atomic(project_root, &project)?;
    if let Some(parent) = canonical.parent() {
        let _ = std::fs::remove_dir_all(parent);
    }
    Ok(project)
}

fn inspected_variants(project_root: &Path, project: &XpanoProjectV2) -> Vec<PointCloudVariant> {
    project
        .geometry
        .variants
        .iter()
        .cloned()
        .map(|mut variant| {
            let path = project_root.join(&variant.canonical_path);
            if !path.is_file() {
                variant.status = PointVariantStatus::Missing;
                return variant;
            }
            match (read_points_count(&path), sha256_file(&path)) {
                (Ok(count), Ok(checksum))
                    if count == variant.point_count
                        && (variant.checksum_sha256.is_empty()
                            || checksum == variant.checksum_sha256) =>
                {
                    if variant.status != PointVariantStatus::Stale {
                        variant.status = PointVariantStatus::Ready;
                    }
                    if variant.checksum_sha256.is_empty() {
                        variant.checksum_sha256 = checksum;
                    }
                }
                _ => variant.status = PointVariantStatus::Corrupt,
            }
            variant
        })
        .collect()
}

pub(crate) fn list_point_variants_impl(
    project_root: &Path,
) -> Result<Vec<PointCloudVariant>, ProjectCommandError> {
    recover_active_variant_transaction(project_root)?;
    let project = read_project(project_root)?;
    Ok(inspected_variants(project_root, &project))
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PointVariantPreview {
    pub variant: PointCloudVariant,
    pub canonical_path: String,
    pub world_from_canonical: [f64; 16],
    pub transform_revision: u64,
}

pub(crate) fn preview_point_variant_impl(
    project_root: &Path,
    variant_id: &str,
) -> Result<PointVariantPreview, ProjectCommandError> {
    recover_active_variant_transaction(project_root)?;
    let project = read_project(project_root)?;
    let variant = inspected_variants(project_root, &project)
        .into_iter()
        .find(|variant| variant.id == variant_id)
        .ok_or_else(|| geometry_error("invalid_geometry", "point variant does not exist"))?;
    if variant.status != PointVariantStatus::Ready {
        return Err(geometry_error(
            "invalid_geometry",
            "point variant is not ready for preview",
        ));
    }
    Ok(PointVariantPreview {
        canonical_path: project_root
            .join(&variant.canonical_path)
            .to_string_lossy()
            .to_string(),
        variant,
        world_from_canonical: project.geometry.transform.world_from_canonical,
        transform_revision: project.geometry.transform.revision,
    })
}

#[tauri::command]
pub fn list_point_variants(
    project_root: String,
) -> Result<Vec<PointCloudVariant>, ProjectCommandError> {
    list_point_variants_impl(Path::new(&project_root))
}

#[tauri::command]
pub fn preview_point_variant(
    project_root: String,
    variant_id: String,
) -> Result<PointVariantPreview, ProjectCommandError> {
    preview_point_variant_impl(Path::new(&project_root), &variant_id)
}

#[tauri::command]
pub fn set_active_point_variant(
    app: tauri::AppHandle,
    project_root: String,
    variant_id: String,
    expected_transform_revision: u64,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    let project = set_active_point_variant_impl(
        Path::new(&project_root),
        &variant_id,
        expected_transform_revision,
    )?;
    let _ = app.emit(
        "project:updated",
        crate::media::ProjectUpdatedEvent {
            project_root,
            project: project.clone(),
        },
    );
    Ok(project)
}

#[tauri::command]
pub fn delete_point_variant(
    app: tauri::AppHandle,
    project_root: String,
    variant_id: String,
) -> Result<XpanoProjectV2, ProjectCommandError> {
    let project = delete_point_variant_impl(Path::new(&project_root), &variant_id)?;
    let _ = app.emit(
        "project:updated",
        crate::media::ProjectUpdatedEvent {
            project_root,
            project: project.clone(),
        },
    );
    Ok(project)
}

#[tauri::command]
pub fn register_densified_variant(
    app: tauri::AppHandle,
    project_root: String,
    dense_points_path: String,
    source_job_id: Option<String>,
) -> Result<PointCloudVariant, ProjectCommandError> {
    let variant = register_densified_variant_impl(
        Path::new(&project_root),
        Path::new(&dense_points_path),
        source_job_id.as_deref(),
    )?;
    let project = read_project(Path::new(&project_root))?;
    let _ = app.emit(
        "project:updated",
        crate::media::ProjectUpdatedEvent {
            project_root,
            project,
        },
    );
    Ok(variant)
}

#[tauri::command]
pub fn apply_world_transform(
    app: tauri::AppHandle,
    project_root: String,
    expected_transform_revision: u64,
    world_from_canonical: [f64; 16],
) -> Result<XpanoProjectV2, ProjectCommandError> {
    let project = apply_world_transform_impl(
        Path::new(&project_root),
        expected_transform_revision,
        world_from_canonical,
    )?;
    let _ = app.emit(
        "project:updated",
        crate::media::ProjectUpdatedEvent {
            project_root,
            project: project.clone(),
        },
    );
    Ok(project)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::contracts::{PointVariantKind, ReconstructionStatus, XpanoProjectV2};
    use crate::project::{read_project, write_project_atomic};
    use std::io::Write;
    use std::path::{Path, PathBuf};

    fn temp_case(name: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!(
            "xpano-geometry-{}-{}-{}",
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
        let mut project: XpanoProjectV2 = serde_json::from_str(include_str!(
            "../../../schemas/fixtures/xpano_project_v3.example.json"
        ))
        .unwrap();
        project.reconstruction.status = ReconstructionStatus::Complete;
        project.reconstruction.colmap_path = Some("colmap".to_string());
        project.geometry.active_variant_id = "standard".to_string();
        project
    }

    fn write_points(path: &Path, count: u64) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        let mut file = std::fs::File::create(path).unwrap();
        file.write_all(&count.to_le_bytes()).unwrap();
        for index in 0..count {
            file.write_all(&(index + 1).to_le_bytes()).unwrap();
            file.write_all(&(index as f64).to_le_bytes()).unwrap();
            file.write_all(&(index as f64 + 1.0).to_le_bytes()).unwrap();
            file.write_all(&(index as f64 + 2.0).to_le_bytes()).unwrap();
            file.write_all(&[10, 20, 30]).unwrap();
            file.write_all(&0.25f64.to_le_bytes()).unwrap();
            file.write_all(&0u64.to_le_bytes()).unwrap();
        }
        file.sync_all().unwrap();
    }

    fn write_images(path: &Path) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        let mut file = std::fs::File::create(path).unwrap();
        file.write_all(&1u64.to_le_bytes()).unwrap();
        file.write_all(&1u32.to_le_bytes()).unwrap();
        for value in [1.0f64, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0] {
            file.write_all(&value.to_le_bytes()).unwrap();
        }
        file.write_all(&1u32.to_le_bytes()).unwrap();
        file.write_all(b"image.jpg\0").unwrap();
        file.write_all(&0u64.to_le_bytes()).unwrap();
        file.sync_all().unwrap();
    }

    fn write_cameras(path: &Path) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        let mut file = std::fs::File::create(path).unwrap();
        file.write_all(&1u64.to_le_bytes()).unwrap();
        file.write_all(&1u32.to_le_bytes()).unwrap();
        file.write_all(&1u32.to_le_bytes()).unwrap();
        file.write_all(&640u64.to_le_bytes()).unwrap();
        file.write_all(&480u64.to_le_bytes()).unwrap();
        for value in [500.0f64, 500.0, 320.0, 240.0] {
            file.write_all(&value.to_le_bytes()).unwrap();
        }
        file.sync_all().unwrap();
    }

    fn read_first_xyz(path: &Path) -> [f64; 3] {
        let bytes = std::fs::read(path).unwrap();
        let mut xyz = [0.0; 3];
        for (index, value) in xyz.iter_mut().enumerate() {
            let offset = 16 + index * 8;
            *value = f64::from_le_bytes(bytes[offset..offset + 8].try_into().unwrap());
        }
        xyz
    }

    fn read_first_image_translation(path: &Path) -> [f64; 3] {
        let bytes = std::fs::read(path).unwrap();
        let mut translation = [0.0; 3];
        for (index, value) in translation.iter_mut().enumerate() {
            let offset = 44 + index * 8;
            *value = f64::from_le_bytes(bytes[offset..offset + 8].try_into().unwrap());
        }
        translation
    }

    fn setup_project(root: &Path) -> PathBuf {
        let project = fixture_project();
        write_project_atomic(root, &project).unwrap();
        let sparse = root.join("colmap/sparse/0");
        write_cameras(&sparse.join("cameras.bin"));
        write_points(&sparse.join("points3D.bin"), 1);
        write_images(&sparse.join("images.bin"));
        sparse
    }

    #[test]
    fn standard_and_densified_variants_switch_without_destroying_either_version() {
        let root = temp_case("variants");
        let sparse = setup_project(&root);
        let original_standard = std::fs::read(sparse.join("points3D.bin")).unwrap();

        let standard = materialize_standard_variant_impl(&root).unwrap();
        assert_eq!(standard.id, "standard");
        assert_eq!(standard.point_count, 1);
        assert!(!standard.checksum_sha256.is_empty());
        assert!(root.join("work/geometry/base_images.bin").is_file());
        assert_eq!(std::fs::read(sparse.join("points3D.bin")).unwrap(), original_standard);

        let dense_candidate = root.join("work/densify/run-1/points3D_dense.bin");
        write_points(&dense_candidate, 2);
        let registered = register_densified_variant_impl(
            &root,
            &dense_candidate,
            Some("job-dense-1"),
        )
        .unwrap();
        assert_eq!(registered.kind, PointVariantKind::Densified);
        assert_eq!(registered.point_count, 2);
        assert!(dense_candidate.is_file());
        assert_eq!(std::fs::read(sparse.join("points3D.bin")).unwrap(), original_standard);

        let project = read_project(&root).unwrap();
        let dense_project = set_active_point_variant_impl(
            &root,
            &registered.id,
            project.geometry.transform.revision,
        )
        .unwrap();
        assert_eq!(dense_project.geometry.active_variant_id, registered.id);
        assert_eq!(read_points_count(&sparse.join("points3D.bin")).unwrap(), 2);

        let standard_project = set_active_point_variant_impl(
            &root,
            "standard",
            dense_project.geometry.transform.revision,
        )
        .unwrap();
        assert_eq!(standard_project.geometry.active_variant_id, "standard");
        assert_eq!(std::fs::read(sparse.join("points3D.bin")).unwrap(), original_standard);
        assert_eq!(standard_project.geometry.variants.len(), 2);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn standard_and_active_variants_cannot_be_deleted() {
        let root = temp_case("delete-protection");
        setup_project(&root);
        materialize_standard_variant_impl(&root).unwrap();
        let dense_candidate = root.join("work/densify/run-1/points3D_dense.bin");
        write_points(&dense_candidate, 2);
        let dense = register_densified_variant_impl(&root, &dense_candidate, None).unwrap();

        let standard_error = delete_point_variant_impl(&root, "standard").unwrap_err();
        assert_eq!(standard_error.code, "invalid_geometry");
        let project = read_project(&root).unwrap();
        set_active_point_variant_impl(
            &root,
            &dense.id,
            project.geometry.transform.revision,
        )
        .unwrap();
        let active_error = delete_point_variant_impl(&root, &dense.id).unwrap_err();
        assert_eq!(active_error.code, "invalid_geometry");
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn densified_world_points_are_normalized_to_canonical_and_reapplied_on_activation() {
        let root = temp_case("canonical-normalization");
        let sparse = setup_project(&root);
        materialize_standard_variant_impl(&root).unwrap();

        let mut project = read_project(&root).unwrap();
        project.geometry.transform.world_from_canonical = [
            1.0, 0.0, 0.0, 10.0,
            0.0, 1.0, 0.0, -4.0,
            0.0, 0.0, 1.0, 2.0,
            0.0, 0.0, 0.0, 1.0,
        ];
        project.geometry.transform.revision = 3;
        write_project_atomic(&root, &project).unwrap();

        let dense_candidate = root.join("work/densify/run-world/points3D_dense.bin");
        write_points(&dense_candidate, 2);
        let dense = register_densified_variant_impl(&root, &dense_candidate, None).unwrap();
        assert_eq!(read_first_xyz(&root.join(&dense.canonical_path)), [-10.0, 5.0, 0.0]);

        let updated = set_active_point_variant_impl(&root, &dense.id, 3).unwrap();
        assert_eq!(updated.geometry.active_variant_id, dense.id);
        assert_eq!(read_first_xyz(&sparse.join("points3D.bin")), [0.0, 1.0, 2.0]);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn corrupt_variant_cannot_replace_the_active_points_file() {
        let root = temp_case("corrupt-activation");
        let sparse = setup_project(&root);
        materialize_standard_variant_impl(&root).unwrap();
        let original_active = std::fs::read(sparse.join("points3D.bin")).unwrap();
        let dense_candidate = root.join("work/densify/run-corrupt/points3D_dense.bin");
        write_points(&dense_candidate, 2);
        let dense = register_densified_variant_impl(&root, &dense_candidate, None).unwrap();
        std::fs::write(root.join(&dense.canonical_path), b"corrupt").unwrap();

        let error = set_active_point_variant_impl(&root, &dense.id, 0).unwrap_err();
        assert_eq!(error.code, "artifact_corrupt");
        assert_eq!(std::fs::read(sparse.join("points3D.bin")).unwrap(), original_active);
        assert_eq!(read_project(&root).unwrap().geometry.active_variant_id, "standard");
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn missing_standard_variant_is_not_recreated_from_an_active_dense_cloud() {
        let root = temp_case("standard-from-dense-guard");
        let sparse = setup_project(&root);
        let standard = materialize_standard_variant_impl(&root).unwrap();
        let dense_candidate = root.join("work/densify/run-guard/points3D_dense.bin");
        write_points(&dense_candidate, 2);
        let dense = register_densified_variant_impl(&root, &dense_candidate, None).unwrap();
        set_active_point_variant_impl(&root, &dense.id, 0).unwrap();
        std::fs::remove_file(root.join(&standard.canonical_path)).unwrap();
        let active_dense = std::fs::read(sparse.join("points3D.bin")).unwrap();

        let error = materialize_standard_variant_impl(&root).unwrap_err();

        assert_eq!(error.code, "invalid_geometry");
        assert_eq!(std::fs::read(sparse.join("points3D.bin")).unwrap(), active_dense);
        assert!(!root.join(&standard.canonical_path).exists());
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn inactive_densified_variant_can_be_deleted_but_stale_transform_revision_is_rejected() {
        let root = temp_case("delete-inactive");
        setup_project(&root);
        materialize_standard_variant_impl(&root).unwrap();
        let dense_candidate = root.join("work/densify/run-delete/points3D_dense.bin");
        write_points(&dense_candidate, 2);
        let dense = register_densified_variant_impl(&root, &dense_candidate, None).unwrap();

        let stale = set_active_point_variant_impl(&root, &dense.id, 99).unwrap_err();
        assert_eq!(stale.code, "revision_conflict");
        let updated = delete_point_variant_impl(&root, &dense.id).unwrap();
        assert!(updated.geometry.variants.iter().all(|variant| variant.id != dense.id));
        assert!(!root.join(&dense.canonical_path).exists());
        assert!(dense_candidate.is_file());
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn a_new_reconstruction_replaces_standard_and_marks_old_dense_variants_stale() {
        let root = temp_case("reconstruction-reset");
        let sparse = setup_project(&root);
        materialize_standard_variant_impl(&root).unwrap();
        let dense_candidate = root.join("work/densify/run-old/points3D_dense.bin");
        write_points(&dense_candidate, 2);
        let dense = register_densified_variant_impl(&root, &dense_candidate, None).unwrap();

        write_points(&sparse.join("points3D.bin"), 3);
        let mut project = read_project(&root).unwrap();
        project.geometry.transform.world_from_canonical[3] = 12.0;
        project.geometry.transform.revision = 4;
        reset_geometry_from_reconstruction_impl(&root, &mut project).unwrap();
        write_project_atomic(&root, &project).unwrap();

        let updated = read_project(&root).unwrap();
        let standard = updated
            .geometry
            .variants
            .iter()
            .find(|variant| variant.id == "standard")
            .unwrap();
        let old_dense = updated
            .geometry
            .variants
            .iter()
            .find(|variant| variant.id == dense.id)
            .unwrap();
        assert_eq!(standard.point_count, 3);
        assert_eq!(standard.status, PointVariantStatus::Ready);
        assert_eq!(old_dense.status, PointVariantStatus::Stale);
        assert_eq!(updated.geometry.active_variant_id, "standard");
        assert_eq!(updated.geometry.transform.revision, 5);
        assert_eq!(updated.geometry.transform.world_from_canonical[3], 0.0);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn applying_world_transform_preserves_projection_and_immutable_canonical_files() {
        let root = temp_case("world-transform");
        let sparse = setup_project(&root);
        let standard = materialize_standard_variant_impl(&root).unwrap();
        let canonical_points = std::fs::read(root.join(&standard.canonical_path)).unwrap();
        let canonical_images = std::fs::read(root.join(BASE_IMAGES_PATH)).unwrap();
        let before = read_project(&root).unwrap();
        let world_from_canonical = [
            1.0, 0.0, 0.0, 10.0,
            0.0, 1.0, 0.0, -4.0,
            0.0, 0.0, 1.0, 2.0,
            0.0, 0.0, 0.0, 1.0,
        ];

        let updated = apply_world_transform_impl(
            &root,
            before.geometry.transform.revision,
            world_from_canonical,
        )
        .unwrap();

        assert_eq!(read_first_xyz(&sparse.join("points3D.bin")), [10.0, -3.0, 4.0]);
        assert_eq!(read_first_image_translation(&sparse.join("images.bin")), [-10.0, 4.0, -2.0]);
        assert_eq!(std::fs::read(root.join(&standard.canonical_path)).unwrap(), canonical_points);
        assert_eq!(std::fs::read(root.join(BASE_IMAGES_PATH)).unwrap(), canonical_images);
        assert_eq!(updated.geometry.transform.world_from_canonical, world_from_canonical);
        assert_eq!(updated.geometry.transform.revision, before.geometry.transform.revision + 1);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn reflection_and_corrupt_base_images_leave_world_materialization_untouched() {
        let root = temp_case("transform-failure");
        let sparse = setup_project(&root);
        materialize_standard_variant_impl(&root).unwrap();
        let original_points = std::fs::read(sparse.join("points3D.bin")).unwrap();
        let original_images = std::fs::read(sparse.join("images.bin")).unwrap();
        let project = read_project(&root).unwrap();
        let reflection = [
            -1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0,
        ];
        let reflection_error = apply_world_transform_impl(
            &root,
            project.geometry.transform.revision,
            reflection,
        )
        .unwrap_err();
        assert_eq!(reflection_error.code, "invalid_geometry");

        std::fs::write(root.join(BASE_IMAGES_PATH), b"corrupt").unwrap();
        let translation = [
            1.0, 0.0, 0.0, 1.0,
            0.0, 1.0, 0.0, 2.0,
            0.0, 0.0, 1.0, 3.0,
            0.0, 0.0, 0.0, 1.0,
        ];
        let corrupt_error = apply_world_transform_impl(
            &root,
            project.geometry.transform.revision,
            translation,
        )
        .unwrap_err();
        assert_eq!(corrupt_error.code, "artifact_corrupt");
        assert_eq!(std::fs::read(sparse.join("points3D.bin")).unwrap(), original_points);
        assert_eq!(std::fs::read(sparse.join("images.bin")).unwrap(), original_images);
        assert_eq!(read_project(&root).unwrap().geometry.transform.revision, project.geometry.transform.revision);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn interrupted_world_transform_restores_active_points_and_images() {
        let root = temp_case("interrupted-transform");
        let sparse = setup_project(&root);
        materialize_standard_variant_impl(&root).unwrap();
        let active_points = sparse.join("points3D.bin");
        let active_images = sparse.join("images.bin");
        let original_points = std::fs::read(&active_points).unwrap();
        let original_images = std::fs::read(&active_images).unwrap();
        copy_file_atomic(&active_points, &root.join(ACTIVE_ROLLBACK_PATH)).unwrap();
        copy_file_atomic(&active_images, &root.join(ACTIVE_IMAGES_ROLLBACK_PATH)).unwrap();
        write_points(&active_points, 2);
        std::fs::write(&active_images, b"interrupted replacement").unwrap();
        let project = read_project(&root).unwrap();
        write_json_value_atomic(
            &root.join(ACTIVE_TRANSACTION_PATH),
            &serde_json::json!({
                "kind": "transform",
                "newTransformRevision": project.geometry.transform.revision + 1,
            }),
        )
        .unwrap();

        recover_active_variant_transaction(&root).unwrap();

        assert_eq!(std::fs::read(active_points).unwrap(), original_points);
        assert_eq!(std::fs::read(active_images).unwrap(), original_images);
        assert!(!root.join(ACTIVE_TRANSACTION_PATH).exists());
        assert!(!root.join(ACTIVE_ROLLBACK_PATH).exists());
        assert!(!root.join(ACTIVE_IMAGES_ROLLBACK_PATH).exists());
        let _ = std::fs::remove_dir_all(root);
    }
}
