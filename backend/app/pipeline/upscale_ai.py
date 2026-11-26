"""Child process of upscale.ensure_ai: Real-ESRGAN (SRVGGNetCompact x4) over every frame of a
video, scaled down to the target size on the card, encoded near-lossless with the source's audio.

    python -m app.pipeline.upscale_ai SOURCE OUT W H MODEL_PTH ENCODER

Prints "P <done> <total>" lines for the progress. The architecture is written out here (~20 lines)
instead of pulling basicsr / spandrel (and their torch pins) into the venv.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from app.ffmpegutil import ffmpeg_bin, ffprobe_bin


def build_model(convs: int):
    from torch import nn
    from torch.nn import functional as F

    class SRVGG(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            body: list[nn.Module] = [nn.Conv2d(3, 64, 3, 1, 1), nn.PReLU(num_parameters=64)]
            for _ in range(convs):
                body += [nn.Conv2d(64, 64, 3, 1, 1), nn.PReLU(num_parameters=64)]
            body.append(nn.Conv2d(64, 3 * 16, 3, 1, 1))
            self.body = nn.ModuleList(body)
            self.ps = nn.PixelShuffle(4)

        def forward(self, x):
            out = x
            for m in self.body:
                out = m(out)
            return self.ps(out) + F.interpolate(x, scale_factor=4, mode="nearest")

    return SRVGG()


def load_model(path: str, convs: int):
    import torch

    sd = torch.load(path, map_location="cpu", weights_only=True)
    sd = sd.get("params", sd.get("params_ema", sd))
    m = build_model(convs)
    m.load_state_dict(sd)
    # channels_last: 3.9 → 5.8 fps at 1080p on an RTX 4070 Laptop (measured 2026-11-26)
    return m.eval().half().cuda().to(memory_format=torch.channels_last)


def probe(src: str) -> tuple[int, int, str, float]:
    """(width, height, frame rate, length of the VIDEO stream) — not the container's length,
    which an audio track running longer would stretch (mkv: the stream has no duration, the
    tags may; the container is the last resort)."""
    out = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,avg_frame_rate,duration:stream_tags=DURATION"
                          ":format=duration", "-of", "json", src],
                         capture_output=True, text=True).stdout
    d = json.loads(out)
    s = d["streams"][0]
    rate = s.get("avg_frame_rate") or "30/1"
    if rate in ("0/0", "0/1"):
        rate = "30/1"
    return int(s["width"]), int(s["height"]), rate, stream_seconds(s, d.get("format") or {})


def stream_seconds(stream: dict, fmt: dict) -> float:
    """The video stream's length from an ffprobe answer. Pure."""
    for raw in (stream.get("duration"), (stream.get("tags") or {}).get("DURATION")):
        if not raw or raw == "N/A":
            continue
        try:
            if ":" in str(raw):                       # mkv tag "00:01:02.500000000"
                h, m, s = str(raw).split(":")
                return int(h) * 3600 + int(m) * 60 + float(s)
            return float(raw)
        except ValueError:
            continue
    try:
        return float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        return 0.0


def missing_seconds(done: int, video_s: float, rate: str) -> float:
    """How much of the video stream's end the frames read so far leave out (0 = whole). Pure."""
    num, den = (int(x) for x in rate.split("/"))
    return max(0.0, video_s - done * den / num)


# the fps filter may round the last frame or two; anything more is a short read
MISSING_OK_S = 0.25


def video_start(path: str) -> float:
    """The first video timestamp of a file (0 when unknown)."""
    out = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=start_time", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def encoder_args(encoder: str) -> list[str]:
    """Near-lossless intermediate (the render decodes it again)."""
    if encoder == "h264_nvenc":
        return ["-c:v", "hevc_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "14", "-b:v", "0"]
    return ["-c:v", "libx264", "-preset", "fast", "-crf", "12"]


def main(src: str, out: str, W: int, H: int, model_path: str, encoder: str) -> None:
    import torch
    from torch.nn import functional as F

    from app.pipeline.upscale import MODEL_CONVS

    torch.backends.cudnn.benchmark = True
    w, h, rate, dur = probe(src)
    num, den = (int(x) for x in rate.split("/"))
    total = max(1, int(round(dur * num / den)))
    m = load_model(model_path, MODEL_CONVS)
    free, _total = torch.cuda.mem_get_info()
    print(f"M {free / 2**30:.2f}", flush=True)       # free graphics memory, for upscale.ensure_ai
    ff = ffmpeg_bin()
    # constant frame rate in and out: the raw pipe carries no timestamps
    dec = subprocess.Popen([ff, "-v", "error", "-i", src, "-vf", f"fps={rate}", "-f", "rawvideo",
                            "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    # picture only: muxed live with the source's audio, a slow pipe (the model) made ffmpeg
    # start the video ~1 s late (start_time 1.021, 2026-11-26) — the sound is added after
    pic = str(Path(out).with_suffix(".video.mkv"))
    enc = subprocess.Popen([ff, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                            "-s", f"{W}x{H}", "-r", rate, "-i", "-", *encoder_args(encoder),
                            "-pix_fmt", "yuv420p", pic], stdin=subprocess.PIPE)
    assert dec.stdout is not None and enc.stdin is not None
    size = w * h * 3
    done = 0
    try:
        with torch.inference_mode():
            while True:
                buf = dec.stdout.read(size)
                if len(buf) < size:
                    break
                x = (torch.frombuffer(bytearray(buf), dtype=torch.uint8).cuda()
                     .view(h, w, 3).permute(2, 0, 1)[None].half().div_(255)
                     .contiguous(memory_format=torch.channels_last))
                y = F.interpolate(m(x).float(), size=(H, W), mode="bicubic", antialias=True)
                enc.stdin.write((y.clamp_(0, 1) * 255).byte()[0].permute(1, 2, 0)
                                .contiguous().cpu().numpy().tobytes())
                done += 1
                if done % 30 == 0:
                    print(f"P {done} {total}", flush=True)
    finally:
        enc.stdin.close()
        dec.stdout.close()
    # a source that breaks mid-clip must not leave a short copy that later renders reuse
    if dec.wait() != 0:
        raise SystemExit(f"ffmpeg decode failed after {done} of ~{total} frames")
    short = missing_seconds(done, dur, rate)
    if short > MISSING_OK_S:
        raise SystemExit(f"only {done} of ~{total} frames decoded ({short:.2f} s of the video "
                         "missing at the end)")
    if enc.wait() != 0:
        raise SystemExit("ffmpeg encode failed")
    mux = subprocess.run([ff, "-y", "-v", "error", "-i", pic, "-i", src, "-map", "0:v",
                          "-map", "1:a?", "-c", "copy", out], capture_output=True, text=True)
    Path(pic).unlink(missing_ok=True)
    if mux.returncode != 0:
        raise SystemExit("ffmpeg mux failed: " + (mux.stderr.strip().splitlines() or ["?"])[-1])
    start = video_start(out)
    if abs(start) > 0.05:
        raise SystemExit(f"upscaled video starts at {start:.3f} s, not 0")
    print(f"P {total} {total}", flush=True)


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0], a[1], int(a[2]), int(a[3]), a[4], a[5])
