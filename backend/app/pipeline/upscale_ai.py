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


def model_input(w: int, h: int, W: int, H: int) -> tuple[int, int]:
    """The size the model reads: the source, or smaller when the target is not much bigger.
    Its x4 output is scaled down to the target anyway, and on a compressed 1080p source
    (Douyin ~1.2 Mb/s, Bilibili 3 Mb/s) reading at 2/3 of the target kept skin / lash detail
    closer to the source than reading every 1080 line (smoother, "waxy") and ran 2x faster
    (2026-11-26: 1080 in 5.6 fps, 720 in 11.8). Never below a quarter of the target. Pure."""
    short, tshort = min(w, h), min(W, H)
    want = max(_even(tshort * IN_RATIO), _even(tshort / 4 + 1))
    if short <= want:
        return w, h
    s = want / short
    return _even(w * s), _even(h * s)


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


IN_RATIO = 2 / 3
# frames per forward: 4 gained ~30 % for the bare model but nothing end to end once the
# decoder / encoder run beside the card (2026-11-26: 10.2 vs 9.6 fps) — kept at 1
BATCH = 1


def main(src: str, out: str, W: int, H: int, model_path: str, encoder: str) -> None:
    import queue
    import threading

    import torch
    from torch.nn import functional as F

    from app.pipeline.upscale import MODEL_CONVS

    torch.backends.cudnn.benchmark = True
    w0, h0, rate, dur = probe(src)
    w, h = model_input(w0, h0, W, H)
    num, den = (int(x) for x in rate.split("/"))
    total = max(1, int(round(dur * num / den)))
    m = load_model(model_path, MODEL_CONVS)
    free, _total = torch.cuda.mem_get_info()
    print(f"M {free / 2**30:.2f}", flush=True)       # free graphics memory, for upscale.ensure_ai
    # x4 output of a batch in fp16 + the model's 64-channel maps: ~0.45 GB per 720p frame
    batch = BATCH if free > (w * h / (1280 * 720)) * 0.6 * 2**30 * BATCH + 2**30 else 1
    ff = ffmpeg_bin()
    # constant frame rate in and out: the raw pipe carries no timestamps
    vf = f"fps={rate}" + (f",scale={w}:{h}:flags=lanczos" if (w, h) != (w0, h0) else "")
    dec = subprocess.Popen([ff, "-v", "error", "-i", src, "-vf", vf, "-f", "rawvideo",
                            "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    # picture only; the sound is added after (see the mux below)
    pic = str(Path(out).with_suffix(".video.mkv"))
    enc = subprocess.Popen([ff, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                            "-s", f"{W}x{H}", "-r", rate, "-i", "-", *encoder_args(encoder),
                            "-pix_fmt", "yuv420p", pic], stdin=subprocess.PIPE)
    assert dec.stdout is not None and enc.stdin is not None
    size = w * h * 3
    # the decoder, the card and the encoder work at the same time (one thread each side)
    inq: queue.Queue = queue.Queue(maxsize=batch * 3)
    outq: queue.Queue = queue.Queue(maxsize=batch * 3)
    failed: list[BaseException] = []

    def read() -> None:
        try:
            while True:
                buf = dec.stdout.read(size)
                if len(buf) < size:
                    break
                inq.put(buf)
        except BaseException as exc:  # noqa: BLE001 - reported by the main thread
            failed.append(exc)
        finally:
            inq.put(None)

    def write() -> None:
        try:
            while (data := outq.get()) is not None:
                enc.stdin.write(data)
        except BaseException as exc:  # noqa: BLE001
            failed.append(exc)
            while outq.get() is not None:      # keep draining: the GPU side must not block
                pass

    reader = threading.Thread(target=read, daemon=True)
    writer = threading.Thread(target=write, daemon=True)
    reader.start()
    writer.start()
    done = 0
    try:
        with torch.inference_mode():
            end = False
            while not end and not failed:
                bufs = []
                while len(bufs) < batch:
                    b = inq.get()
                    if b is None:
                        end = True
                        break
                    bufs.append(b)
                if not bufs:
                    break
                x = (torch.frombuffer(bytearray(b"".join(bufs)), dtype=torch.uint8).cuda()
                     .view(len(bufs), h, w, 3).permute(0, 3, 1, 2).half().div_(255)
                     .contiguous(memory_format=torch.channels_last))
                # fp16 bicubic: same picture as fp32 (mean difference 0.03 / 255), the fp32
                # copy of a 4x frame cost ~40 % of the time at 720 in
                y = F.interpolate(m(x), size=(H, W), mode="bicubic", antialias=True)
                frames = (y.clamp_(0, 1).mul_(255).byte().permute(0, 2, 3, 1).contiguous()
                          .cpu().numpy())
                for f in frames:
                    outq.put(f.tobytes())
                before, done = done, done + len(bufs)
                if done // 30 != before // 30:
                    print(f"P {done} {total}", flush=True)
    finally:
        outq.put(None)
        writer.join()
        enc.stdin.close()
        dec.stdout.close()
        reader.join(5)
    if failed:
        raise SystemExit(f"AI pass failed: {failed[0]}")
    # a source that breaks mid-clip must not leave a short copy that later renders reuse
    if dec.wait() != 0:
        raise SystemExit(f"ffmpeg decode failed after {done} of ~{total} frames")
    short = missing_seconds(done, dur, rate)
    if short > MISSING_OK_S:
        raise SystemExit(f"only {done} of ~{total} frames decoded ({short:.2f} s of the video "
                         "missing at the end)")
    if enc.wait() != 0:
        raise SystemExit("ffmpeg encode failed")
    # the audio DECODED to FLAC, not copied: an AAC track with priming packets before 0 (a
    # source cut with -ss … -c copy) made the mkv muxer shift EVERY stream so they are not
    # negative — the picture then started 1.021 s late (2026-11-26, found by the check below)
    mux = subprocess.run([ff, "-y", "-v", "error", "-i", pic, "-i", src, "-map", "0:v",
                          "-map", "1:a?", "-c:v", "copy", "-c:a", "flac", out],
                         capture_output=True, text=True)
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
