"""Stage: ASR (no-hardsub path). Thin orchestrator over the chosen ASR provider.

Extracts audio from the video, runs the provider, and writes the Chinese cues to
``job.cn_srt``. Working providers: faster-whisper (CUDA→CPU fallback) and FunASR
Paraformer (CN-native). The fused word-timing provider is planned as N-7.
"""
from __future__ import annotations

import subprocess

from app.models import Job
from app.pipeline.srt import write_srt
from app.providers.registry import get_asr


def _extract_audio(job: Job) -> None:
    from app.ffmpegutil import ffmpeg_bin

    subprocess.run(
        [ffmpeg_bin(), "-y", "-i", str(job.source_video), "-vn", "-ac", "1", "-ar", "16000",
         str(job.source_audio)],
        check=True, capture_output=True,
    )


def transcribe(job: Job, *, provider: str | None = None, language: str = "zh",
               log=None) -> Job:
    if provider is None:
        from app.config import settings

        provider = settings.vizsup_default_asr
    asr = get_asr(provider)
    if not asr.available():
        raise RuntimeError(f"ASR provider '{provider}' is not available (missing dependency "
                           f"or API key).")
    if not job.source_audio.exists():
        if log:
            log("Tách âm thanh từ video (ffmpeg)…")
        _extract_audio(job)
    from app.heavy import heavy_task

    with heavy_task(f"ASR ({provider})"):
        try:
            cues = asr.transcribe(job.source_audio, language=language, log=log)
        finally:
            asr.unload()   # free VRAM before the next heavy stage (8 GB rule)
    write_srt(cues, job.cn_srt)
    _persist_timing(job, asr, cues)
    return job


def _persist_timing(job: Job, asr, cues) -> None:
    """Keep what timing was based on: the word track + per-chunk sentences
    (``asr_words.json`` — re-timing never needs another upload) and which cues were
    guessed rather than anchored on heard words (``timing_report.json`` → review flag
    "Timing đoán" in the editor). Best effort."""
    import json

    words = getattr(asr, "last_words", None)
    groups = getattr(asr, "last_groups", None)
    try:
        if words is not None or groups is not None:
            (job.work_dir / "asr_words.json").write_text(json.dumps(
                {"words": words or [], "groups": [[list(sp), sents] for sp, sents in groups or []]},
                ensure_ascii=False), encoding="utf-8")
        report = [{"index": c.index, "guess": True} for c in cues if getattr(c, "guess", False)]
        path = job.work_dir / "timing_report.json"
        if report:
            path.write_text(json.dumps(report), encoding="utf-8")
        elif path.exists():
            path.unlink()
    except OSError:
        pass


def retime(job: Job, *, log=None, language: str = "zh") -> int:
    """Re-time ``cn.srt``'s existing sentences against the word track + local VAD —
    no new transcript, so the text (and its translation) stays. Uses the saved
    ``asr_words.json`` when present; otherwise fetches only the cloud word track
    (cheap Whisper timing) and regroups the cues into upload chunks by their current
    start. Rewrites ``cn.srt`` + ``timing_report.json``; returns how many cues moved.
    Raises when there is nothing to re-time (no cn.srt / no audio)."""
    import json

    from app.pipeline import vad
    from app.pipeline.srt import parse_srt
    from app.providers.asr import _cloud
    from app.providers.asr.audio_llm import CHUNK_SEC

    say = log or (lambda _m: None)
    if not job.cn_srt.exists():
        raise FileNotFoundError("Chưa có cn.srt để đo lại thời gian.")
    if not job.source_audio.exists():
        if not job.source_video.exists():
            raise FileNotFoundError("Không có âm thanh / video nguồn.")
        _extract_audio(job)
    src = job.source_audio
    old = parse_srt(job.cn_srt)

    saved = job.work_dir / "asr_words.json"
    words = groups = None
    try:
        data = json.loads(saved.read_text(encoding="utf-8"))
        words = [tuple(w) for w in data.get("words") or []] or None
        if words and data.get("groups"):
            texts = [s for _sp, sents in data["groups"] for s in sents]
            if texts == [c.text for c in old]:          # same sentences → exact grouping
                groups = [(tuple(sp), list(sents)) for sp, sents in data["groups"]]
    except (OSError, ValueError, TypeError):
        pass
    spans = _cloud.plan_chunks(src, CHUNK_SEC)
    if groups is None:
        groups = [(sp, []) for sp in spans]
        for c in old:                                   # by current start; the last chunk
            k = next((i for i, (_lo, hi) in enumerate(spans) if c.start < hi), len(spans) - 1)
            groups[k][1].append(c.text)                 # takes anything past the end
    if words is None:
        say(f"Lấy mốc thời gian từng từ ({_cloud.timing_model()})…")
        cache, aid = _cloud.cache_for(src)
        with _cloud.chunk_files(src, spans) as paths:
            words = _cloud.cloud_words(list(zip(paths, spans)), language,
                                       cache_dir=cache, audio_id=aid)
    voice = vad.spans_for(src, job.work_dir, log=say)
    groups = [(sp, sents) for sp, sents in groups if sents]
    new = _cloud.reindex(_cloud.time_groups(src, groups, language, words=words, voice=voice))
    if [c.text for c in new] != [c.text for c in old]:
        raise RuntimeError("Đo lại thời gian làm đổi nội dung câu — giữ nguyên cn.srt.")
    # a chunk the timing model heard (almost) nothing of keeps its old times — cloud
    # Whisper returned zero words for a whole 240 s of singing once (2026-10-13)
    k = 0
    for (lo, hi), sents in groups:
        heard = sum(len(_cloud._CJK_CHARS.findall(w[0])) for w in words if lo <= w[1] <= hi)
        got = sum(len(_cloud._CJK_CHARS.findall(s)) for s in sents)
        if got and heard < 0.3 * got:
            say(f"  đoạn {lo:.0f}–{hi:.0f} s: không nghe đủ chữ — giữ thời gian cũ")
            for i in range(k, k + len(sents)):
                new[i].start, new[i].end, new[i].guess = old[i].start, old[i].end, False
        k += len(sents)
    from app.pipeline.align import repair_runs
    from app.pipeline.assemble import probe_duration

    repaired = repair_runs(new, voice=voice, duration=probe_duration(src) or None)
    if repaired:
        say(f"  sửa {repaired} câu bị dồn / quá dài theo giọng nói")

    class _Saved:                                       # reuse _persist_timing's writer
        last_words, last_groups = words, groups

    write_srt(new, job.cn_srt)
    _persist_timing(job, _Saved(), new)
    moved = sum(1 for a, b in zip(old, new) if abs(a.start - b.start) > 0.05
                or abs(a.end - b.end) > 0.05)
    say(f"✓ Đo lại thời gian: {moved}/{len(new)} câu đổi mốc · "
        f"{sum(1 for c in new if c.guess)} câu ước theo giọng nói")
    return moved


def vi_time_map(job: Job, vi_zh: list[str]) -> list[tuple[float, float]] | None:
    """New (start, end) for each Vietnamese line from the re-timed ``cn.srt`` — only
    when the lines still pair 1:1 with it (same count, same Chinese, in order). None
    when the user split / merged / deleted lines: then times are not guessed across."""
    from app.pipeline.srt import parse_srt

    cn = parse_srt(job.cn_srt) if job.cn_srt.exists() else []
    if len(cn) != len(vi_zh):
        return None
    if any(z and z.strip() != c.text.strip() for z, c in zip(vi_zh, cn)):
        return None
    return [(c.start, c.end) for c in cn]


def sync_vi_times(job: Job) -> bool:
    """CLI helper: apply :func:`vi_time_map` to ``vi.srt`` on disk."""
    from app.pipeline.srt import parse_srt

    if not job.vi_srt.exists():
        return False
    vi = parse_srt(job.vi_srt)
    try:
        import json

        zh = json.loads((job.work_dir / "zh_ref.json").read_text(encoding="utf-8"))
        zh = [str(x) for x in zh] if isinstance(zh, list) and len(zh) == len(vi) else None
    except (OSError, ValueError):
        zh = None
    times = vi_time_map(job, zh or [""] * len(vi))
    if times is None:
        return False
    for c, (s, e) in zip(vi, times):
        c.start, c.end = s, e
    write_srt(vi, job.vi_srt)
    return True
