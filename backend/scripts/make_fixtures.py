"""Generate synthetic test clips for the eval scripts — no downloads, no copyright.

`samples/` is gitignored on purpose: real Douyin/Bilibili clips are copyrighted and
large, so they must never be committed. But `scripts/{asr_timing_eval,ocr_eval,
duck_eval}.py` need *some* media to run against. This builds it from ffmpeg
primitives instead, so the eval path is reproducible on any machine.

What you get (in samples/, gitignored):
  hardsub.mp4   testsrc2 + burned-in Chinese subtitle lines  -> ocr_eval, hardsub detect
  speech.mp4    a tone "speech" band mixed over a music bed   -> duck_eval

and a matching reference SRT per clip in samples/refs/ (COMMITTED — text only).
For ocr_eval the generated ref is exact by construction: we know what we burned in.

    python backend/scripts/make_fixtures.py            # build everything
    python backend/scripts/make_fixtures.py --only hardsub

For ASR timing evaluation a synthetic clip is NOT enough (there is no real speech
to align): asr_timing_eval.py still needs a real clip you hand-label once. Put the
labels in samples/refs/<clip>.cn.ref.srt and keep the media out of git.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # make `app` importable
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")                    # console may be cp1252

from app.ffmpegutil import ffmpeg_bin                           # noqa: E402
from app.models import Cue                                      # noqa: E402
from app.pipeline.srt import write_srt                          # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "samples"
REFS = SAMPLES / "refs"

# (start, end, text) — burned in AND written to the reference SRT, so the OCR
# reference is exact by construction rather than hand-labeled.
HARDSUB_LINES = [
    (0.0, 2.5, "你好世界欢迎观看这个视频"),
    (2.5, 5.0, "今天我们来聊一聊人工智能"),
    (5.0, 7.5, "这项技术发展得非常快"),
    (7.5, 10.0, "谢谢观看记得点赞订阅"),
]

# A CJK font that actually has these glyphs. drawtext silently draws tofu without one.
FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",        # Microsoft YaHei (Windows)
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]


def _font() -> str:
    for c in FONT_CANDIDATES:
        if Path(c).exists():
            return c
    raise SystemExit(
        "Không tìm thấy font CJK để vẽ phụ đề. Cài Noto Sans CJK (hoặc dùng máy "
        "Windows có msyh.ttc) rồi chạy lại."
    )


def _esc(path: str) -> str:
    """ffmpeg filter args: the drive colon must be escaped (C\\:/Windows/...)."""
    return path.replace("\\", "/").replace(":", "\\:")


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stderr[-2000:], file=sys.stderr)
        raise SystemExit(f"ffmpeg thất bại (mã {proc.returncode})")


def build_hardsub() -> Path:
    """A clip with burned-in Chinese subtitles — the OCR / hardsub-detect fixture.

    A full-width opaque plate covers the subtitle band, the way many Chinese
    uploads actually caption. That is not cosmetic: without it the testsrc2
    pattern fills the rest of the bottom-third crop and RapidOCR reads CJK
    glyphs out of the noise, so the "exact by construction" reference would be
    a lie. This fixture is therefore CLEAN — to measure proofread quality on
    noisy OCR, point ocr_eval.py at a real clip with a hand-fixed reference.
    """
    out = SAMPLES / "hardsub.mp4"
    font = _esc(_font())
    # The plate spans the full bottom THIRD — that is exactly the region ocr.py
    # crops. A shorter band leaves testsrc2's frame counter inside the crop and
    # OCR reads it as a line prefix ("1226 这项技术…").
    draws = ["drawbox=x=0:y=2*ih/3:w=iw:h=ih/3:color=black@1.0:t=fill"]
    for start, end, text in HARDSUB_LINES:
        draws.append(
            f"drawtext=fontfile='{font}':text='{text}':fontsize=28:fontcolor=white:"
            f"borderw=2:x=(w-text_w)/2:y=h-text_h-14:"
            f"enable='between(t,{start},{end})'"
        )
    _run([
        ffmpeg_bin(), "-y",
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=10",
        "-f", "lavfi", "-i", "sine=frequency=220:duration=10",
        "-vf", ",".join(draws),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        str(out),
    ])

    ref = REFS / "hardsub.cn.ref.srt"
    write_srt([Cue(index=i, start=s, end=e, text=t)
               for i, (s, e, t) in enumerate(HARDSUB_LINES, 1)], ref)
    print(f"✓ {out.relative_to(ROOT)}  (+ ref {ref.relative_to(ROOT)})")
    return out


def build_speech() -> Path:
    """Speech-band tone over a music bed — the Demucs / duck_eval fixture.

    Not real speech (Demucs will not split it cleanly); it exercises the *plumbing*
    and duck_eval's band measurement. For a real acceptance number, run duck_eval
    on an actual speech+music clip.
    """
    out = SAMPLES / "speech.mp4"
    _run([
        ffmpeg_bin(), "-y",
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=20",
        # "music": low bed, continuous. "speech": 200-4000 Hz band, only 5-15 s.
        "-f", "lavfi", "-i", "sine=frequency=110:duration=20",
        "-f", "lavfi", "-i", "sine=frequency=900:duration=20",
        "-filter_complex",
        "[2:a]volume=enable='between(t,5,15)':volume=1.0,"
        "volume=enable='lt(t,5)+gt(t,15)':volume=0[sp];"
        "[1:a][sp]amix=inputs=2:duration=first[a]",
        "-map", "0:v", "-map", "[a]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        str(out),
    ])
    print(f"✓ {out.relative_to(ROOT)}   (speech section 5-15 s, music-only 0-5 s)")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="build synthetic eval fixtures")
    ap.add_argument("--only", choices=["hardsub", "speech"], help="build just one")
    args = ap.parse_args()

    SAMPLES.mkdir(parents=True, exist_ok=True)
    REFS.mkdir(parents=True, exist_ok=True)

    if args.only in (None, "hardsub"):
        build_hardsub()
    if args.only in (None, "speech"):
        build_speech()

    print(
        "\nTiếp theo:\n"
        "  python backend/scripts/ocr_eval.py --workdir storage/<id> "
        "--ref samples/refs/hardsub.cn.ref.srt --provider openrouter\n"
        "  python backend/scripts/duck_eval.py --source samples/speech.mp4 "
        "--bg storage/<id>/bg.wav --range 5:15"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
