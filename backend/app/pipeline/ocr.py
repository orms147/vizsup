"""Stage: OCR — read hardcoded (burned-in) Chinese subtitles off the video into
job.cn_srt. For videos that already carry on-screen subtitles, this is usually
more accurate than ASR (and works when there's little/no speech).

Approach (Windows-friendly, no PaddlePaddle): sample frames with ffmpeg cropped
to the bottom third (where subs live, also drops watermarks), OCR each crop with
RapidOCR (ONNX PP-OCRv5 models), then post-process into clean timed cues. Prefers
the current `rapidocr` package (PP-OCRv5, CUDA when onnxruntime-gpu is present)
and falls back to the legacy `rapidocr_onnxruntime` build and to CPU.

Post-processing (this is what keeps cn.srt readable):
- per OCR line, drop low-confidence reads and Latin symbol-soup, while keeping
  Chinese, numbers/prices, and word-like Latin (so an English line the author
  burned in survives OCR'd transitions/background noise);
- group *consecutive* frames that show the same subtitle by text similarity, not
  exact match (so a slightly-misread or garbage-suffixed frame doesn't split one
  subtitle into many fragments);
- emit the cleanest variant in each group (the read with the most Chinese chars).
Remaining errors get fixed at the human edit gate.
"""
from __future__ import annotations

import re
import subprocess
from difflib import SequenceMatcher

from app.ffmpegutil import ffmpeg_bin
from app.models import Cue, Job
from app.pipeline.srt import parse_srt, write_srt
from app.providers.base import _installed

_PROOF_BATCH = 50   # lines per LLM proofread request

# crop region for OCR (full width): crop=w:h:x:y. Bottom-third is the default
# (subtitles + drops watermarks); other regions are for subs that sit higher or
# move around (then use "full" — slower, noisier, but catches them anywhere).
_CROP = {
    "bottom-third": "crop=iw:ih/3:0:ih*2/3",
    "bottom-quarter": "crop=iw:ih/4:0:ih*3/4",
    "bottom-half": "crop=iw:ih/2:0:ih/2",
    "middle-third": "crop=iw:ih/3:0:ih/3",
    "top-third": "crop=iw:ih/3:0:0",
    "full": "crop=iw:ih:0:0",
}

_CJK = re.compile(r"[一-鿿]")
# pure number/price/punct lines are legit subtitle content (e.g. "800 1+1", "2900元")
_NUMERIC = re.compile(r"^[\d\s+\-.,:;%×xX*元¥$/()~]+$")
_LATIN = re.compile(r"[A-Za-z]")


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _keep_line(text: str) -> bool:
    """Is this OCR line real subtitle content (vs Latin gibberish off the background)?
    Keep Chinese, numeric/price lines, AND word-like Latin so author-inserted English
    lines survive — only drop short symbol-soup. Recall-biased on purpose: the human
    edit gate removes the occasional stray line, but a dropped real line is lost."""
    if _CJK.search(text) or _NUMERIC.match(text):
        return True
    letters = len(_LATIN.findall(text))
    if letters < 2:                       # 1 stray letter amid symbols → noise
        return False
    compact = _norm(text)
    return bool(compact) and sum(c.isalnum() for c in compact) >= 0.6 * len(compact)


def _cjk_count(s: str) -> int:
    return len(_CJK.findall(s or ""))


def _to_lines(result) -> list[tuple[str, float]]:
    """Normalize a RapidOCR call result into (text, score) pairs, hiding the API
    difference between the two packages:
    - new `rapidocr` (>=3.0): a RapidOCROutput with .txts / .scores;
    - legacy `rapidocr_onnxruntime`: a (result_list, elapse) tuple where each
      item is ``[box, text, score]``.
    A blank frame (either returns None) yields ``[]``.
    """
    if result is None:
        return []
    if hasattr(result, "txts"):  # new RapidOCROutput (blank frame → .txts is None/empty)
        txts = list(result.txts or ())
        scores = list(getattr(result, "scores", None) or [])
        scores += [1.0] * (len(txts) - len(scores))
        out: list[tuple[str, float]] = []
        for t, s in zip(txts, scores):
            try:
                out.append(((t or ""), float(s)))
            except (TypeError, ValueError):
                out.append(((t or ""), 1.0))
        return out
    res_list = result[0] if isinstance(result, tuple) else result  # legacy (list, elapse)
    out = []
    for item in res_list or []:
        text = item[1] if len(item) > 1 else ""
        try:
            score = float(item[2]) if len(item) > 2 else 1.0
        except (TypeError, ValueError):
            score = 1.0
        out.append((text, score))
    return out


def _clean_frame(lines: list[tuple[str, float]], min_conf: float) -> str:
    """Join the OCR lines of one frame, dropping low-confidence reads and Latin
    symbol-soup while keeping Chinese, numbers/prices, and real inserted text
    (e.g. an English line the author burned in)."""
    kept: list[str] = []
    for text, score in lines:
        text = (text or "").strip()
        if not text or score < min_conf:
            continue
        if _keep_line(text):
            kept.append(text)
    return " ".join(kept).strip()


def _build_ocr():
    """Construct a RapidOCR engine. Prefer the current `rapidocr` package (PP-OCRv5,
    CUDA when onnxruntime-gpu is installed); if it isn't installed, fall back to the
    legacy `rapidocr_onnxruntime` build. A new package that's installed-but-failing
    surfaces its real error rather than masking it with the legacy engine.

    ONNX Runtime already falls back to CPU on its own when CUDA is unavailable, so the
    retry ladder below only guards against `rapidocr` *rejecting* the CUDA/PP-OCRv5
    param keys — OCR still works (on newer default models / CPU) even then. Caveat: a
    rejected GPU key degrades to CPU silently, so after installing rapidocr v3 +
    onnxruntime-gpu, confirm the GPU actually engaged (nvidia-smi / frame timing).
    """
    if _installed("rapidocr"):
        from rapidocr import RapidOCR

        v5: dict = {}
        try:  # pin PP-OCRv5 detection+recognition when the enum is exposed
            from rapidocr import OCRVersion

            v5 = {"Det.ocr_version": OCRVersion.PPOCRV5, "Rec.ocr_version": OCRVersion.PPOCRV5}
        except Exception:  # noqa: BLE001 - older/newer layout: fall back to defaults
            v5 = {}
        gpu = {"EngineConfig.onnxruntime.use_cuda": True,
               "EngineConfig.onnxruntime.cuda_ep_cfg.device_id": 0}
        # Degradation ladder — GPU outranks the v5 pin. Newer rapidocr builds default
        # to PP-OCRv6+ and *reject* the v5 enum for some model types; when the pin and
        # the CUDA keys rode in one params dict, that rejection silently cost the GPU
        # too (observed on rapidocr 3.x: v5 refused → fell through to CPU defaults).
        for params in (v5 | gpu, gpu, v5):
            if not params:
                continue
            try:
                return RapidOCR(params=params)
            except Exception:  # noqa: BLE001 - drop to the next (less capable) config
                continue
        return RapidOCR()  # last resort: all defaults
    if _installed("rapidocr_onnxruntime"):
        from rapidocr_onnxruntime import RapidOCR

        return RapidOCR()
    raise RuntimeError('OCR chưa được cài. Chạy: pip install -e ".[ocr]"')


def _similar(a: str, b: str, sim: float) -> bool:
    """Same subtitle still on screen? True if one read contains the other or the
    normalized texts are similar enough (catches misreads/garbage suffixes)."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if len(min(na, nb, key=len)) >= 3 and (na in nb or nb in na):
        return True
    return SequenceMatcher(None, na, nb).ratio() >= sim


def _best(texts: list[str]) -> str:
    """Cleanest read of a subtitle group: most Chinese chars, then longest."""
    return max(texts, key=lambda s: (_cjk_count(s), len(s))) if texts else ""


def extract_hardsubs(job: Job, *, fps: float = 2.0, crop: str = "bottom-third",
                     min_dur: float = 0.4, min_conf: float = 0.5, sim: float = 0.5) -> Job:
    """OCR burned-in subtitles → job.cn_srt."""
    ocr = _build_ocr()

    frames_dir = job.work_dir / "frames"
    frames_dir.mkdir(exist_ok=True)
    for old in frames_dir.glob("*.png"):
        old.unlink()

    vf = f"fps={fps}," + _CROP.get(crop, _CROP["bottom-third"])
    subprocess.run(
        [ffmpeg_bin(), "-y", "-i", str(job.source_video), "-vf", vf, str(frames_dir / "%05d.png")],
        check=True, capture_output=True,
    )

    frames = sorted(frames_dir.glob("*.png"))
    # 1) OCR + clean each frame to a single text line (normalizing both RapidOCR APIs)
    per_frame = [(k / fps, _clean_frame(_to_lines(ocr(str(fp))), min_conf)) for k, fp in enumerate(frames)]

    # 2) group consecutive frames showing the same subtitle, keep the cleanest read
    cues: list[Cue] = []
    group: list[str] = []
    start = 0.0
    rep = ""

    def flush(end_t: float) -> None:
        if group and rep:
            cues.append(Cue(index=len(cues) + 1, start=start, end=end_t, text=rep))

    for t, text in per_frame:
        if text and (not rep or _similar(text, rep, sim)):
            if not group:
                start = t
            group.append(text)
            rep = _best(group)
        else:  # empty frame or a different subtitle → close the current group
            flush(t)
            group, rep = ([text], text) if text else ([], "")
            start = t if text else start

    flush(len(frames) / fps if frames else 0.0)

    cues = [c for c in cues if c.duration >= min_dur]
    for i, c in enumerate(cues, 1):
        c.index = i
    write_srt(cues, job.cn_srt)
    return job


def proofread_srt(job: Job, *, provider: str, context: dict | None = None, log=None) -> int:
    """Opt-in OCR cleanup: send the OCR'd Chinese lines to an LLM to fix recognition
    errors (TEXT ONLY — no frames uploaded) and rewrite ``job.cn_srt`` in place.

    Reuses the translator the user already picked (e.g. OpenRouter). Returns the
    number of lines proofread; a no-op returns 0. Never blanks a line: on a parse
    miss the provider keeps the original OCR text. Skips cleanly when the provider
    can't proofread (passthrough / missing key) so it can't break the OCR run.
    """
    from app.providers.registry import get_translator

    prov = get_translator(provider)
    if provider == "passthrough" or not prov.available():
        if log:
            log("⚠ Bỏ qua sửa lỗi OCR: bộ dịch chưa sẵn sàng (thiếu key) hoặc là passthrough.")
        return 0

    cues = parse_srt(job.cn_srt)
    if not cues:
        return 0
    texts = [c.text for c in cues]
    fixed: list[str] = []
    for i in range(0, len(texts), _PROOF_BATCH):
        fixed.extend(prov.proofread_texts(texts[i:i + _PROOF_BATCH], context=context))
    for c, t in zip(cues, fixed):
        if t and t.strip():
            c.text = t.strip()
    write_srt(cues, job.cn_srt)
    return len(cues)
