"""Upscale for the "1440p · YouTube" render quality (2026-11-26, docs/plan sharpness lab).

YouTube serves a 1440p upload with its better codec (VP9/AV1) at a far higher bitrate than a
1080p one, so a soft 1080p Douyin / Bilibili source looks sharper on the platform when it is
uploaded at 1440p. The picture is made big FIRST — clean → upscale → light sharpen — and the
cover, the glass and the subtitles are then drawn at the final size (libass renders crisp text).

Engines (o["upscale"]):

* "fast" (default): a light hqdn3d denoise at the source size, then libplacebo's
  ewa_lanczossharp on the graphics card (Vulkan); the CPU lanczos scaler when no Vulkan
  device works. Measured (backend/scripts/sharp_lab.py): 60–100 fps at 1440p.
* "ai": Real-ESRGAN realesr-animevideov3 (made for animation; also cleans the soft AI-film
  look; skin gets smoother) run once per source as a pre-pass in its own process
  (app.pipeline.upscale_ai, torch CUDA fp16 channels_last, ~5.8 fps on an RTX 4070 Laptop —
  about 5× the video's length) into a cached near-lossless intermediate upscaled/<key>.mkv
  (the source's audio copied in), so a re-render with another style or voice costs no AI time.

Measured on 3 clips: denoising with nlmeans_vulkan cost 2× time for no visible gain (not used);
cas after the upscale at 0.4 (the 1080p render keeps SHARPEN_CAS before everything).
"""
from __future__ import annotations

import hashlib
import json
import queue
import re
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from app import fsutil
from app.ffmpegutil import ffmpeg_bin

ENGINES = ("fast", "ai")
UP_CAS = 0.4                      # sharpening after an upscale (the 1080p render: SHARPEN_CAS)
CLEAN = "hqdn3d=1.5:1.5:3:3"      # light denoise at the source size: block noise is not upscaled
DIR = "upscaled"                  # the AI intermediates, beside the source (cleanup: cache)

MODEL = "realesr-animevideov3"
MODEL_URL = ("https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/"
             "realesr-animevideov3.pth")
MODEL_SHA256 = "b8a8376811077954d82ca3fcf476f1ac3da3e8a68a4f4d71363008000a18b75d"
MODEL_CONVS = 16                  # SRVGGNetCompact depth of this model (x4, 64 features)
AI_FPS = 5.8
# bumped when the AI copy's making changes so old copies are made again: 2 = picture encoded
# alone + audio copied in after (version 1 muxed live and could start the picture 1 s late)
PIPELINE = 2
# 2026-11-26: with 1.1 GB free (Edge, an IDE … holding 6.6 GB) the same model ran ~1 fps
LOW_VRAM_GB = 2.0                      # measured frames per second at 1080p input (time estimate)

_VULKAN: str | None | bool = False      # False = not probed yet; None = no usable device


def engine(o_upscale: str | None) -> str:
    return o_upscale if o_upscale in ENGINES else "fast"


# --- graphics card (Vulkan) --------------------------------------------------------------

def vulkan_device() -> str | None:
    """The Vulkan device index libplacebo runs on (the discrete card when ffmpeg lists
    several), or None when no device runs a one-frame libplacebo test. Cached per process."""
    global _VULKAN
    if _VULKAN is not False:
        return _VULKAN  # type: ignore[return-value]
    _VULKAN = None
    try:
        r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-v", "verbose", "-init_hw_device",
                            "vulkan=vk", "-filter_hw_device", "vk", "-f", "lavfi", "-i",
                            "color=s=64x64:d=0.1", "-vf", "format=yuv420p,hwupload,"
                            "libplacebo=w=128:h=128,hwdownload,format=yuv420p",
                            "-frames:v", "1", "-f", "null", "-"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    listing = re.findall(r"^\[Vulkan[^\]]*\]\s+(\d+): (.+)$", r.stderr, re.M)
    _VULKAN = next((i for i, name in listing if "discrete" in name),
                   listing[0][0] if listing else "0")
    return _VULKAN


def hw_args(kind: str | None) -> list[str]:
    """Global ffmpeg options the GPU chain needs (before the inputs)."""
    dev = vulkan_device() if kind == "gpu" else None
    return ["-init_hw_device", f"vulkan=vk:{dev}", "-filter_hw_device", "vk"] if dev else []


def chain(kind: str, w: int, h: int) -> str:
    """Clean + upscale to w×h ("gpu": libplacebo on Vulkan, "cpu": lanczos). Pure."""
    if kind == "gpu":
        return (f"{CLEAN},format=yuv420p,hwupload,libplacebo=w={w}:h={h}:"
                "upscaler=ewa_lanczossharp:format=yuv420p,hwdownload,format=yuv420p")
    return f"{CLEAN},scale={w}:{h}:flags=lanczos"


def fast_kind() -> str:
    return "gpu" if vulkan_device() else "cpu"


# --- AI (Real-ESRGAN) ----------------------------------------------------------------------

def model_path() -> Path:
    from app.config import settings

    return Path(settings.vizsup_storage_dir) / "_models" / f"{MODEL}.pth"


def ensure_model(log: Callable[[str], None] | None = None) -> Path:
    """The Real-ESRGAN weights, downloaded once (pinned release, sha256-checked, atomic)."""
    path = model_path()
    if path.exists():
        return path
    import httpx

    if log:
        log("Tải mô hình AI làm nét Real-ESRGAN (một lần, ~2,5 MB)…")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    with httpx.Client(follow_redirects=True, timeout=120) as c:
        r = c.get(MODEL_URL)
        r.raise_for_status()
        tmp.write_bytes(r.content)
    got = hashlib.sha256(tmp.read_bytes()).hexdigest()
    if got != MODEL_SHA256:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Mô hình Real-ESRGAN tải về sai checksum ({got[:12]}…)")
    tmp.replace(path)
    return path


def ai_available() -> bool:
    """torch with a CUDA card (the AI pass is far too slow on a CPU)."""
    try:
        import torch
    except ImportError:
        return False
    return bool(torch.cuda.is_available())


def ai_key(source: Path, w: int, h: int) -> str:
    """Cache key of an intermediate: the source (size, mtime, first MB), the model, the size."""
    st = source.stat()
    with source.open("rb") as f:
        head = hashlib.sha1(f.read(1 << 20)).hexdigest()[:12]
    raw = json.dumps([st.st_size, int(st.st_mtime), head, MODEL, w, h, PIPELINE])
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def ai_path(job, w: int, h: int) -> Path:
    return job.work_dir / DIR / f"{ai_key(job.source_video, w, h)}.mkv"


def ai_seconds(duration: float, fps: float = 30.0) -> float:
    """Rough time of the AI pass for a video of 'duration' seconds."""
    return duration * fps / AI_FPS


def ensure_ai(job, w: int, h: int, *, log: Callable[[str], None] | None = None,
              progress: Callable[[float], None] | None = None,
              cancel: Callable[[], bool] | None = None) -> Path:
    """The cached AI-upscaled copy of the source at w×h (made now when missing). Runs the model
    in a child process so the graphics memory is given back when it ends."""
    out = ai_path(job, w, h)
    if out.exists():
        return out
    if not ai_available():
        raise RuntimeError("AI làm nét cần card NVIDIA với PyTorch CUDA.")
    model = ensure_model(log)
    out.parent.mkdir(parents=True, exist_ok=True)
    for old in out.parent.glob("*.mkv"):          # an intermediate of an older source / size
        old.unlink(missing_ok=True)
    part = out.with_name(out.stem + ".part.mkv")
    from app.pipeline.assemble import _video_encoder

    cmd = [sys.executable, "-m", "app.pipeline.upscale_ai", str(job.source_video), str(part),
           str(w), str(h), str(model), _video_encoder()]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace")
    err: list[str] = []
    lines: queue.Queue[str | None] = queue.Queue()

    def pump(stream, sink, end: bool) -> None:   # both pipes drained: no full-pipe stall
        for line in stream:
            sink(line)
        if end:
            lines.put(None)

    assert proc.stdout is not None and proc.stderr is not None
    readers = [threading.Thread(target=pump, args=(proc.stdout, lines.put, True), daemon=True),
               threading.Thread(target=pump, args=(proc.stderr, err.append, False), daemon=True)]
    for t in readers:
        t.start()
    try:
        while True:
            if cancel and cancel():          # checked even while the child prints nothing
                proc.kill()
                raise InterruptedError("Đã hủy.")
            try:
                line = lines.get(timeout=0.5)
            except queue.Empty:
                continue
            if line is None:
                break
            if line.startswith("M ") and log:
                try:
                    free = float(line.split()[1])
                except ValueError:
                    free = 99.0
                if free < LOW_VRAM_GB:
                    log(f"⚠ Card đồ họa chỉ còn {free:.1f} GB trống (trình duyệt / ứng dụng khác "
                        "đang dùng) — AI làm nét sẽ chậm hơn nhiều lần. Đóng bớt ứng dụng nặng "
                        "rồi dựng lại nếu cần.")
            m = re.match(r"P (\d+) (\d+)", line)
            if m and progress:
                progress(min(1.0, int(m[1]) / max(1, int(m[2]))))
        proc.wait()
        readers[1].join(5)
        err = [e.strip() for e in err if e.strip()][-3:]
    except BaseException:
        proc.kill()
        part.unlink(missing_ok=True)
        raise
    if proc.returncode != 0 or not part.exists():
        part.unlink(missing_ok=True)
        raise RuntimeError("AI làm nét lỗi: " + " | ".join(err or ["lỗi không rõ"]))
    fsutil.replace(part, out)
    return out

