"""Sharpness lab (dev tool): render one short cut of a source through several picture
chains / encoders and report speed, size, encode fidelity (VMAF vs a lossless render of the
same chain) and a crop-sharpness number, plus side-by-side crops to look at.

    python backend/scripts/sharp_lab.py SOURCE OUT_DIR [--start 30] [--len 20] [--only a,b]

VMAF only judges the ENCODER (same filters, lossless vs encoded); denoise / upscale change
the picture on purpose and VMAF would punish them, so those are judged on the crops.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ffmpegutil import ffmpeg_bin, ffprobe_bin  # noqa: E402

NV_OLD = ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "0"]
NV_NEW = ["-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "vbr", "-cq", "19",
          "-b:v", "0", "-multipass", "fullres", "-spatial-aq", "1", "-temporal-aq", "1",
          "-aq-strength", "8", "-rc-lookahead", "32", "-bf", "3", "-b_ref_mode", "middle"]
NV_AQ = ["-rc", "vbr", "-cq", "21", "-b:v", "0", "-multipass", "fullres", "-spatial-aq", "1",
         "-temporal-aq", "1", "-rc-lookahead", "32", "-bf", "3", "-b_ref_mode", "middle"]
NV_AQ_P5 = ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", *NV_AQ]
NV_AQ_P7 = ["-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq", *NV_AQ]
X264_OLD = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
X264_NEW = ["-c:v", "libx264", "-preset", "slow", "-crf", "18", "-tune", "film",
            "-x264-params", "aq-mode=3"]
TAGS = ["-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709",
        "-color_trc", "bt709"]
VK = ["-init_hw_device", "vulkan=vk:0", "-filter_hw_device", "vk"]


def gpu_up(w: int, h: int, s: float, deband: bool) -> str:
    return (f"format=yuv420p,hwupload,nlmeans_vulkan=s={s}:p=3:r=7,"
            f"libplacebo=w={w}:h={h}:upscaler=ewa_lanczossharp:deband={int(deband)}"
            ":format=yuv420p:colorspace=bt709:color_primaries=bt709:color_trc=bt709,"
            "hwdownload,format=yuv420p,cas=0.4")


# name: (picture chain, encoder args, needs vulkan)
def variants(w: int, h: int) -> dict[str, tuple[str, list[str], bool]]:
    uw, uh = (2560, 1440) if w >= h else (1440, 2560)
    return {
        "now_nvenc": ("cas=0.6", NV_OLD, False),          # what the app renders today
        "now_x264": ("cas=0.6", X264_OLD, False),         # its CPU fallback
        "enc_nvenc": ("cas=0.6", NV_NEW, False),          # phase 1: encoder only
        "enc_x264": ("cas=0.6", X264_NEW, False),
        "clean": ("hqdn3d=1.5:1.5:3:3,cas=0.5", NV_NEW, False),
        "aq_p5_cq21": ("cas=0.6", NV_AQ_P5, False),
        "aq_p7_cq21": ("cas=0.6", NV_AQ_P7, False),
        "x264_medium": ("cas=0.6", ["-c:v", "libx264", "-preset", "medium", "-crf", "20",
                                    "-x264-params", "aq-mode=3"], False),
        "x264_slow20": ("cas=0.6", ["-c:v", "libx264", "-preset", "slow", "-crf", "20",
                                    "-x264-params", "aq-mode=3"], False),
        "up1440_gpu": (gpu_up(uw, uh, 1.2, False), NV_NEW, True),
        "up1440_gpu_deband": (gpu_up(uw, uh, 1.5, True), NV_NEW, True),
        "up1440_gpu_fast": ("hqdn3d=1.5:1.5:3:3,format=yuv420p,hwupload,"
                            f"libplacebo=w={uw}:h={uh}:upscaler=ewa_lanczossharp:format=yuv420p,"
                            "hwdownload,format=yuv420p,cas=0.4", NV_NEW, True),
        "up1440_cpu": (f"hqdn3d=1.5:1.5:3:3,scale={uw}:{uh}:flags=lanczos,cas=0.4", NV_NEW, False),
        "up1440_plain": (f"scale={uw}:{uh}:flags=lanczos", NV_NEW, False),
    }


def run(cmd: list[str]) -> tuple[float, str]:
    t0 = time.monotonic()
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr[-1500:])
    return time.monotonic() - t0, r.stderr


def probe(path: Path) -> dict:
    out = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height:format=duration,size", "-of", "json", str(path)],
                         capture_output=True, text=True).stdout
    d = json.loads(out)
    s, f = d["streams"][0], d["format"]
    return {"w": s["width"], "h": s["height"], "dur": float(f["duration"]), "size": int(f["size"])}


def laplacian(png: Path) -> float:
    import cv2

    g = cv2.imread(str(png), cv2.IMREAD_GRAYSCALE)
    return round(float(cv2.Laplacian(g, cv2.CV_64F).var()), 1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("out")
    ap.add_argument("--start", type=float, default=30)
    ap.add_argument("--len", type=float, default=20)
    ap.add_argument("--only", default="")
    ap.add_argument("--crop", default="0.5,0.4", help="crop centre as fractions x,y")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ff = ffmpeg_bin()
    cut = out / "cut.mkv"                       # the same frames for every variant
    if not cut.exists():
        run([ff, "-y", "-v", "error", "-ss", str(a.start), "-i", a.source, "-t", str(a.len),
             "-an", "-c:v", "ffv1", str(cut)])
    src = probe(cut)
    w, h = src["w"], src["h"]
    cx, cy = (float(v) for v in a.crop.split(","))
    rows = []
    for name, (chain, enc, vk) in variants(w, h).items():
        if a.only and name not in a.only.split(","):
            continue
        mp4 = out / f"{name}.mp4"
        hw = VK if vk else []
        secs, _ = run([ff, "-y", "-v", "error", *hw, "-i", str(cut), "-vf", chain, *enc, *TAGS,
                       str(mp4)])
        info = probe(mp4)
        row = {"variant": name, "size": f"{info['w']}x{info['h']}", "fps": round(len_frames(cut) / secs, 1),
               "mbps": round(info["size"] * 8 / info["dur"] / 1e6, 2)}
        # encoder fidelity: the same chain rendered lossless vs this encode
        ref = out / f"{name}.ref.mkv"
        run([ff, "-y", "-v", "error", *hw, "-i", str(cut), "-vf", chain, "-c:v", "ffv1", str(ref)])
        _, err = run([ff, "-v", "info", "-i", str(mp4), "-i", str(ref), "-lavfi",
                      "[0:v]settb=AVTB,setpts=N/FRAME_RATE/TB[a];[1:v]settb=AVTB,setpts=N/FRAME_RATE/TB[b];"
                      "[a][b]libvmaf=n_threads=16",
                      "-f", "null", "-"])
        m = re.search(r"VMAF score: ([\d.]+)", err)
        row["vmaf_enc"] = float(m[1]) if m else None
        ref.unlink(missing_ok=True)
        # the same region of the picture, shown at 1440-line display size for every variant
        png = out / f"{name}.crop.png"
        cw, ch = 0.25, 0.25
        run([ff, "-y", "-v", "error", "-ss", str(a.len / 2), "-i", str(mp4), "-frames:v", "1", "-vf",
             f"crop=iw*{cw}:ih*{ch}:iw*{cx - cw / 2}:ih*{cy - ch / 2},scale=960:-2:flags=lanczos",
             str(png)])
        row["laplacian"] = laplacian(png)
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    (out / "results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    crops = [out / f"{r['variant']}.crop.png" for r in rows]
    if crops:
        inputs = sum((["-i", str(c)] for c in crops), [])
        font = (Path(__file__).resolve().parents[2] / "desktop" / "assets" / "fonts").glob("*.ttf")
        fontfile = str(next(font, "")).replace("\\", "/").replace(":", "\\:")
        labels = ";".join(f"[{i}]scale=960:540,drawtext=fontfile='{fontfile}':text='{r['variant']}':x=8:y=8:"
                          f"fontsize=30:fontcolor=yellow:box=1:boxcolor=black@0.6[c{i}]"
                          for i, r in enumerate(rows))
        cols = 3
        rws = []
        for r0 in range(0, len(rows), cols):
            idx = list(range(r0, min(r0 + cols, len(rows))))
            while len(idx) < cols:
                idx.append(None)
            rws.append(idx)
        pads, stacks = [], []
        for ri, idx in enumerate(rws):
            names = []
            for k, i in enumerate(idx):
                if i is None:
                    pads.append(f"color=black:s=960x540:d=1[p{ri}{k}]")
                    names.append(f"[p{ri}{k}]")
                else:
                    names.append(f"[c{i}]")
            stacks.append(f"{''.join(names)}hstack={cols}[r{ri}]")
        tail = (f"{''.join(f'[r{i}]' for i in range(len(rws)))}vstack={len(rws)}"
                if len(rws) > 1 else "[r0]copy")
        graph = ";".join([labels, *pads, *stacks, tail])
        run([ff, "-y", "-v", "error", *inputs, "-filter_complex", graph, "-frames:v", "1",
             str(out / "crops.png")])


def len_frames(path: Path) -> int:
    out = subprocess.run([ffprobe_bin(), "-v", "error", "-count_packets", "-select_streams", "v:0",
                          "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    return int(out or 1)


if __name__ == "__main__":
    main()
