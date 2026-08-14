import os
import threading
import time

import numpy as np

try:
    import cv2
except Exception:
    cv2 = None


def remap_bilinear(source, mx, my):
    height, width = source.shape[:2]
    x0 = np.floor(mx).astype(np.int32)
    y0 = np.floor(my).astype(np.int32)
    x1 = x0 + 1
    y1 = y0 + 1
    valid = (x0 >= 0) & (x1 < width) & (y0 >= 0) & (y1 < height)
    if source.ndim == 2:
        output = np.zeros(mx.shape, dtype=np.uint8)
    else:
        output = np.zeros((mx.shape[0], mx.shape[1], source.shape[2]), dtype=np.uint8)
    if not np.any(valid):
        return output

    xv = mx[valid]
    yv = my[valid]
    x0v = x0[valid]
    y0v = y0[valid]
    x1v = x1[valid]
    y1v = y1[valid]
    wx = xv - x0v
    wy = yv - y0v

    if source.ndim == 2:
        top = source[y0v, x0v] * (1.0 - wx) + source[y0v, x1v] * wx
        bottom = source[y1v, x0v] * (1.0 - wx) + source[y1v, x1v] * wx
        output[valid] = np.clip(top * (1.0 - wy) + bottom * wy, 0, 255).astype(np.uint8)
        return output

    wx = wx[:, None]
    wy = wy[:, None]
    top = source[y0v, x0v] * (1.0 - wx) + source[y0v, x1v] * wx
    bottom = source[y1v, x0v] * (1.0 - wx) + source[y1v, x1v] * wx
    output[valid] = np.clip(top * (1.0 - wy) + bottom * wy, 0, 255).astype(np.uint8)
    return output


def benchmark_remap_backends(cv2_module):
    source = np.arange(512 * 512 * 3, dtype=np.uint8).reshape(512, 512, 3)
    axis = np.linspace(1.25, 509.75, 384, dtype=np.float32)
    mx, my = np.meshgrid(axis, axis)
    timings = {}

    cv2_module.remap(
        source, mx, my, cv2_module.INTER_LINEAR,
        borderMode=cv2_module.BORDER_CONSTANT, borderValue=0,
    )
    started = time.perf_counter()
    cpu_result = cv2_module.remap(
        source, mx, my, cv2_module.INTER_LINEAR,
        borderMode=cv2_module.BORDER_CONSTANT, borderValue=0,
    )
    timings["opencv"] = time.perf_counter() - started

    if cv2_module.ocl.haveOpenCL():
        cv2_module.ocl.setUseOpenCL(True)
        source_umat = cv2_module.UMat(source)
        mx_umat = cv2_module.UMat(mx)
        my_umat = cv2_module.UMat(my)
        cv2_module.remap(
            source_umat, mx_umat, my_umat, cv2_module.INTER_LINEAR,
            borderMode=cv2_module.BORDER_CONSTANT, borderValue=0,
        ).get()
        started = time.perf_counter()
        opencl_result = cv2_module.remap(
            source_umat, mx_umat, my_umat, cv2_module.INTER_LINEAR,
            borderMode=cv2_module.BORDER_CONSTANT, borderValue=0,
        ).get()
        elapsed = time.perf_counter() - started
        delta = np.abs(opencl_result.astype(np.int16) - cpu_result.astype(np.int16))
        if delta.size and int(delta.max()) <= 4:
            timings["opencl"] = elapsed
    return timings


def select_remap_backend(cv2_module, preference=None):
    preference = (preference or os.environ.get("XPANO_REMAP_BACKEND", "auto")).strip().lower()
    if preference in {"numpy", "strict", "compatibility"} or cv2_module is None:
        return "numpy"
    if preference in {"opencv", "opencv-cpu", "cpu"}:
        return "opencv"
    if preference in {"opencl", "gpu", "hardware"}:
        return "opencl" if cv2_module.ocl.haveOpenCL() else "opencv"
    try:
        timings = benchmark_remap_backends(cv2_module)
    except Exception:
        return "opencv"
    return min(timings, key=timings.get) if timings else "opencv"


class RemapEngine:
    def __init__(self, cv2_module=cv2, preference=None, warning_cb=None):
        self.cv2 = cv2_module
        self.warning_cb = warning_cb or (lambda _message: None)
        self.backend = select_remap_backend(cv2_module, preference)
        if self.backend == "opencl":
            try:
                self.cv2.ocl.setUseOpenCL(True)
            except Exception as exc:
                self.backend = "opencv"
                self.warning_cb(f"OpenCL could not be enabled; using OpenCV CPU: {exc}")
        self._fallback_lock = threading.Lock()

    def remap(self, source, mx, my):
        backend = self.backend
        if backend == "numpy":
            return remap_bilinear(source, mx, my)
        try:
            if backend == "opencl":
                return self.cv2.remap(
                    self.cv2.UMat(source), self.cv2.UMat(mx), self.cv2.UMat(my),
                    self.cv2.INTER_LINEAR,
                    borderMode=self.cv2.BORDER_CONSTANT, borderValue=0,
                ).get()
            return self.cv2.remap(
                source, mx, my, self.cv2.INTER_LINEAR,
                borderMode=self.cv2.BORDER_CONSTANT, borderValue=0,
            )
        except Exception as exc:
            with self._fallback_lock:
                if self.backend != "numpy":
                    self.backend = "numpy"
                    self.warning_cb(f"Accelerated remap failed; falling back to NumPy: {exc}")
            return remap_bilinear(source, mx, my)
