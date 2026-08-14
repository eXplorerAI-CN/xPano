REFERENCE_FISHEYE_SIDE_PX = 3840
REFERENCE_FISHEYE_PIXEL_SIZE_MM = 0.0024
REFERENCE_FISHEYE_FOCAL_MM = 2.5

_REFERENCE_FOCAL_PX = REFERENCE_FISHEYE_FOCAL_MM / REFERENCE_FISHEYE_PIXEL_SIZE_MM
_REFERENCE_FOCAL_PER_PIXEL = _REFERENCE_FOCAL_PX / REFERENCE_FISHEYE_SIDE_PX


def normalized_fisheye_focal_px(width, height):
    width = int(width)
    height = int(height)
    if width <= 0 or height <= 0:
        raise ValueError(f"Fisheye image dimensions must be positive: {width}x{height}")
    return min(width, height) * _REFERENCE_FOCAL_PER_PIXEL


def effective_fisheye_pixel_size_mm(width, height):
    return REFERENCE_FISHEYE_FOCAL_MM / normalized_fisheye_focal_px(width, height)
