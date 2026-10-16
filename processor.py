
import ctypes
import os
import time
import io
import base64
import numpy as np
from PIL import Image
from multiprocessing import Pool, current_process, Queue
from multiprocessing import TimeoutError as MPTimeout
import functools

#  Load shared library 

# FILTERS_LIB lets a container keep the compiled library outside the code folder
# (so the code can be mounted as a read-only volume); default: next to this file.
LIB_PATH = os.environ.get("FILTERS_LIB") or os.path.join(os.path.dirname(__file__), "libfilters.so")
_lib = None   # lazy per-process init (ctypes is not fork-safe if loaded before fork)


def _get_lib():
    global _lib
    if _lib is None:
        _lib = ctypes.CDLL(LIB_PATH)
        _setup_sigs(_lib)
    return _lib


def _setup_sigs(lib):
    """Set ctypes argument / return types for every exported symbol."""
    uint8p = ctypes.POINTER(ctypes.c_uint8)
    intp   = ctypes.POINTER(ctypes.c_int)

    def _reg(name, argtypes, restype=None):
        fn = getattr(lib, name)
        fn.argtypes = argtypes
        fn.restype  = restype or None

    _reg("apply_grayscale",      [uint8p, ctypes.c_int, ctypes.c_int])
    _reg("apply_blur",           [uint8p, ctypes.c_int, ctypes.c_int, ctypes.c_int])
    _reg("apply_edge_detection", [uint8p, ctypes.c_int, ctypes.c_int])
    _reg("apply_brightness",     [uint8p, ctypes.c_int, ctypes.c_int, ctypes.c_float])
    _reg("apply_sharpen",        [uint8p, ctypes.c_int, ctypes.c_int])
    _reg("apply_invert",         [uint8p, ctypes.c_int, ctypes.c_int])
    _reg("apply_sepia",          [uint8p, ctypes.c_int, ctypes.c_int])

    _reg("augment_hflip",        [uint8p, ctypes.c_int, ctypes.c_int])
    _reg("augment_vflip",        [uint8p, ctypes.c_int, ctypes.c_int])
    _reg("augment_noise",        [uint8p, ctypes.c_int, ctypes.c_int, ctypes.c_float])
    _reg("augment_color_jitter", [uint8p, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_float, ctypes.c_float])
    _reg("augment_center_crop",  [uint8p, ctypes.c_int, ctypes.c_int, ctypes.c_float,
                                   intp, intp])
    _reg("augment_rotate90",     [uint8p, ctypes.c_int, ctypes.c_int, ctypes.c_int])
    _reg("get_thread_count",     [], ctypes.c_int)


# Per-pixel helpers 

def _pix_ptr(arr: np.ndarray):
    """Return ctypes uint8 pointer to a C-contiguous numpy array."""
    assert arr.dtype == np.uint8 and arr.flags["C_CONTIGUOUS"]
    return arr.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))


def _img_to_rgb_array(img: Image.Image) -> np.ndarray:
    img = img.convert("RGB")
    return np.ascontiguousarray(np.array(img, dtype=np.uint8))


def _encode_result(arr: np.ndarray, w: int, h: int) -> bytes:
    """
    Encode the processed pixels as full-size JPEG *bytes* (not base64: base64 costs
    +33% size and CPU, and the web layer only encodes what it really has to send).
    Previews and thumbnails are built later, outside the timed job.
    """
    img = Image.fromarray(arr[:h, :w], "RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


# Single-image worker

def process_image(task: dict) -> dict:
    """
    Top-level function passed to Pool.map().

    task keys:
        image_bytes : raw bytes of the uploaded image (image_b64 also still accepted)
        filters     : list of filter dicts  [{name, params…}]
        augmentations: list of aug dicts   [{name, params…}]
        image_name  : display name
        worker_id   : hint (unused; real worker is current_process().pid)

    Returns dict with timing, base64 result, metadata.
    """
    lib   = _get_lib()
    pid   = os.getpid()
    start = time.perf_counter()

    # Decode image
    raw   = task.get("image_bytes")
    if raw is None:
        raw = base64.b64decode(task["image_b64"])
    img   = Image.open(io.BytesIO(raw))
    arr   = _img_to_rgb_array(img)
    h, w  = arr.shape[:2]
    ptr   = _pix_ptr(arr)

    filter_times = {}

    # ── Apply Filters ──
    for f in task.get("filters", []):
        fname = f["name"]
        t0    = time.perf_counter()

        if fname == "grayscale":
            lib.apply_grayscale(ptr, w, h)
        elif fname == "blur":
            lib.apply_blur(ptr, w, h, int(f.get("radius", 3)))
        elif fname == "edge_detection":
            lib.apply_edge_detection(ptr, w, h)
        elif fname == "brightness":
            lib.apply_brightness(ptr, w, h, float(f.get("factor", 1.3)))
        elif fname == "sharpen":
            lib.apply_sharpen(ptr, w, h)
        elif fname == "invert":
            lib.apply_invert(ptr, w, h)
        elif fname == "sepia":
            lib.apply_sepia(ptr, w, h)

        filter_times[fname] = round((time.perf_counter() - t0) * 1000, 2)

    # ── Apply Augmentations ──
    aug_times = {}
    for aug in task.get("augmentations", []):
        aname = aug["name"]
        t0    = time.perf_counter()

        if aname == "hflip":
            lib.augment_hflip(ptr, w, h)
        elif aname == "vflip":
            lib.augment_vflip(ptr, w, h)
        elif aname == "noise":
            lib.augment_noise(ptr, w, h, float(aug.get("std_dev", 20.0)))
        elif aname == "color_jitter":
            lib.augment_color_jitter(ptr, w, h,
                                      float(aug.get("saturation", 1.5)),
                                      float(aug.get("brightness", 1.0)))
        elif aname == "center_crop":
            out_w = ctypes.c_int(0)
            out_h = ctypes.c_int(0)
            lib.augment_center_crop(ptr, w, h,
                                     float(aug.get("crop_pct", 0.2)),
                                     ctypes.byref(out_w), ctypes.byref(out_h))
            w, h = out_w.value, out_h.value   # update dims after crop
            ptr  = _pix_ptr(arr)              # re-bind (array untouched after first nw*nh bytes)
        elif aname == "rotate90":
            lib.augment_rotate90(ptr, w, h, int(aug.get("direction", 1)))
            w, h = h, w   # swap dims

        aug_times[aname] = round((time.perf_counter() - t0) * 1000, 2)

    total_ms = round((time.perf_counter() - start) * 1000, 2)

    result_jpeg = _encode_result(arr, w, h)

    return {
        "image_name"  : task["image_name"],
        "pid"         : pid,
        "total_ms"    : total_ms,
        "filter_times": filter_times,
        "aug_times"   : aug_times,
        "result_jpeg" : result_jpeg,
        "out_w"       : w,
        "out_h"       : h,
    }


# Batch runner 

class JobTimeout(Exception):
    """The worker pool did not finish in time (a worker was probably killed)."""


def run_parallel(tasks: list, n_workers: int, timeout: float = 100.0) -> dict:
    """
    Run tasks through a Pool of n_workers processes.
    Workers finish a task and immediately pick up the next available one (Pool handles this).
    """
    wall_start = time.perf_counter()
    with Pool(processes=n_workers) as pool:
        try:
            # map_async(...).get(timeout) instead of map(): if the OS kills a
            # worker (out of memory), plain map() waits forever.
            results = pool.map_async(process_image, tasks).get(timeout=timeout)
        except MPTimeout:
            raise JobTimeout(f"pool did not finish within {timeout:.0f}s")
    wall_ms = round((time.perf_counter() - wall_start) * 1000, 2)
    return {"results": results, "wall_ms": wall_ms, "mode": "parallel", "workers": n_workers}


def run_sequential(tasks: list) -> dict:
    """Baseline: process images one after another in the main process."""
    wall_start = time.perf_counter()
    results    = [process_image(t) for t in tasks]
    wall_ms    = round((time.perf_counter() - wall_start) * 1000, 2)
    return {"results": results, "wall_ms": wall_ms, "mode": "sequential", "workers": 1}


def get_omp_threads() -> int:
    lib = _get_lib()
    return lib.get_thread_count()


def safe_worker_count(requested: int, max_pixels: int) -> int:
    """
    Cap the pool size so N workers x (memory for the biggest image) fits in the
    RAM that is actually free. Without this, a big batch on a small server gets
    a worker killed by the kernel. ~12 bytes per pixel covers the decoded array,
    PIL's copy, the filter buffer and the JPEG encoder.
    """
    if os.environ.get("WORKER_GUARD", "1") == "0":
        return requested          # guard switched off: use exactly what was asked for
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    avail = int(line.split()[1]) * 1024
                    break
            else:
                return requested
    except OSError:
        return requested          # not Linux: no guard
    per_worker = max(1, max_pixels * 12)
    return max(1, min(requested, int(avail * 0.7 // per_worker)))
