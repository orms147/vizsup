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
from app.fsutil import atomic_write_text


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
            atomic_write_text((job.work_dir / "asr_words.json"), json.dumps(
                {"words": words or [], "groups": [[list(sp), sents] for sp, sents in groups or []]},
                ensure_ascii=False), encoding="utf-8")
        report = [{"index": c.index, "guess": True} for c in cues if getattr(c, "guess", False)]
        path = job.work_dir / "timing_report.json"
        if report:
            atomic_write_text(path, json.dumps(report), encoding="utf-8")
        elif path.exists():
            path.unlink()
    except OSError:
        pass


def regroup_by_text(lines: list[str], saved: list[tuple[tuple[float, float], list[str]]]
                    ) -> list[tuple[tuple[float, float], list[str]]] | None:
    """Put the current lines into the upload chunks they were HEARD in, by their place in
    the chunks' saved transcript text — not by their current start: a line whose time
    was wrong (40 lines stacked on the 240.000 s chunk edge, 2026-10-28) went to the
    wrong chunk and could never be aligned. Works when the lines are the saved sentences
    split or merged (same characters in the same order); None otherwise. Pure."""
    import bisect

    from app.pipeline.align import _chars

    chunk_text = ["".join(_chars(x) for x in sents) for _sp, sents in saved]
    if "".join(_chars(t) for t in lines) != "".join(chunk_text):
        return None
    ends, total = [], 0
    for t in chunk_text:
        total += len(t)
        ends.append(total)
    out: list[tuple[tuple[float, float], list[str]]] = [(sp, []) for sp, _ in saved]
    pos = 0
    for line in lines:
        k = min(bisect.bisect_right(ends, pos), len(saved) - 1)
        out[k][1].append(line)
        pos += len(_chars(line))
    return out


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
    from app.fsutil import atomic_write_text

    # the times before: vi_time_map moves Vietnamese lines that no longer pair 1:1
    atomic_write_text(job.work_dir / BEFORE_RETIME, job.cn_srt.read_text(encoding="utf-8"))

    saved = job.work_dir / "asr_words.json"
    words = groups = None
    try:
        data = json.loads(saved.read_text(encoding="utf-8"))
        words = [tuple(w) for w in data.get("words") or []] or None
        if words and data.get("groups"):
            saved_groups = [(tuple(sp), list(sents)) for sp, sents in data["groups"]]
            texts = [s for _sp, sents in saved_groups for s in sents]
            if texts == [c.text for c in old]:          # same sentences → exact grouping
                groups = saved_groups
            else:                                       # lines split (Tách vế) / merged
                groups = regroup_by_text([c.text for c in old], saved_groups)
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
                                       cache_dir=cache, audio_id=aid, media=src, log=say)
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


BEFORE_RETIME = "cn.before-retime.srt"


def time_warp(old: list, new: list):
    """t → new time, from the Chinese lines before / after a re-time: every line's start
    and end are anchors, times between them are interpolated (outside, shifted like the
    nearest anchor). Monotonic by construction (an anchor that would run backwards is
    dropped). ``old`` / ``new`` are the same lines in the same order. Pure."""
    import bisect

    pts: list[tuple[float, float]] = []
    for a, b in zip(old, new, strict=True):
        for o, n in ((a.start, b.start), (a.end, b.end)):
            if not pts or (o > pts[-1][0] and n >= pts[-1][1]):
                pts.append((o, n))
    if not pts:
        return lambda t: t
    xs = [x for x, _ in pts]

    def warp(t: float) -> float:
        k = bisect.bisect_right(xs, t)
        if k == 0:
            return max(0.0, t + pts[0][1] - pts[0][0])
        if k == len(pts):
            return t + pts[-1][1] - pts[-1][0]
        (x0, y0), (x1, y1) = pts[k - 1], pts[k]
        return y0 + (t - x0) * (y1 - y0) / (x1 - x0)

    return warp


def _iou(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = min(a[1], b[1]) - max(a[0], b[0])
    union = max(a[1], b[1]) - min(a[0], b[0])
    return max(0.0, inter) / union if union > 0 else 0.0


def align_lines(old: list[tuple[float, float]], spans: list[tuple[float, float]]
                ) -> list[tuple[str, list[int], list[int]]]:
    """Pair the Chinese lines (``old`` spans) with the Vietnamese ones (``spans``) IN
    ORDER, as 1↔1, 2→1 (the translation merged two), 1→2 (split) or a line left alone,
    maximising how well the paired spans overlap (dynamic programming). The order of a
    translation never changes, so a line can't be paired across others — unlike a
    nearest-overlap guess, which misplaced lines whose old times were far off.
    Returns [(kind, cn indices, vi indices)]. Pure."""
    n, m = len(old), len(spans)
    skip = -0.35                     # leaving a line alone costs about one poor pair
    neg = float("-inf")
    best = [[neg] * (m + 1) for _ in range(n + 1)]
    back: list[list[tuple[int, int, str] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    best[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            cur = best[i][j]
            if cur == neg:
                continue
            moves = []
            if i < n and j < m:
                moves.append((1, 1, "11", _iou(old[i], spans[j])))
            if i + 1 < n and j < m:
                moves.append((2, 1, "21", _iou((old[i][0], old[i + 1][1]), spans[j]) - 0.05))
            if i < n and j + 1 < m:
                moves.append((1, 2, "12", _iou(old[i], (spans[j][0], spans[j + 1][1])) - 0.05))
            if i < n:
                moves.append((1, 0, "10", skip))
            if j < m:
                moves.append((0, 1, "01", skip))
            for di, dj, kind, gain in moves:
                if cur + gain > best[i + di][j + dj]:
                    best[i + di][j + dj] = cur + gain
                    back[i + di][j + dj] = (di, dj, kind)
    out: list[tuple[str, list[int], list[int]]] = []
    i, j = n, m
    while i or j:
        di, dj, kind = back[i][j]
        out.append((kind, list(range(i - di, i)), list(range(j - dj, j))))
        i, j = i - di, j - dj
    return out[::-1]


def remap_by_overlap(old: list, new: list, spans: list[tuple[float, float]]
                     ) -> list[tuple[float, float]]:
    """New times for Vietnamese lines that do not pair 1:1 with the Chinese by text:
    :func:`align_lines` pairs them in order on the OLD times, then each takes its Chinese
    line's NEW span (merged → both lines' span, split → shared by old length). A line
    paired with nothing moves with :func:`time_warp`. ``old`` / ``new``: the Chinese
    lines before / after a re-time, same order. Pure."""
    warp = time_warp(old, new)
    out: list[tuple[float, float] | None] = [None] * len(spans)
    for kind, ci, vj in align_lines([(c.start, c.end) for c in old], spans):
        if not vj:
            continue
        if not ci:
            s, e = spans[vj[0]]
            out[vj[0]] = (warp(s), warp(e))
            continue
        ns, ne = new[ci[0]].start, new[ci[-1]].end
        if len(vj) == 1:
            out[vj[0]] = (ns, ne)
            continue
        lens = [max(0.05, spans[k][1] - spans[k][0]) for k in vj]
        t = ns
        for k, ln in zip(vj, lens, strict=True):
            step = (ne - ns) * ln / sum(lens)
            out[k] = (t, t + step)
            t += step
    res: list[tuple[float, float]] = []
    for k, (s, e) in enumerate(spans):
        ns, ne = out[k] if out[k] is not None else (warp(s), warp(e))
        if res and ns < res[-1][0]:            # never start before the previous line
            ns = res[-1][0]
        res.append((round(ns, 3), round(max(ne, ns + 0.2), 3)))
    return res


def vi_time_map(job: Job, vi_zh: list[str],
                vi_spans: list[tuple[float, float]] | None = None
                ) -> list[tuple[float, float]] | None:
    """New (start, end) for each Vietnamese line from the re-timed ``cn.srt``. When the
    lines still pair 1:1 with it (same count, same Chinese, in order) they take its times.
    Otherwise — the translation merged two lines, or the user split / merged / deleted
    some — the old times (``vi_spans``) are mapped by :func:`remap_by_overlap` from cn.srt
    before / after the re-time. None when neither is possible."""
    from app.pipeline.srt import parse_srt

    cn = parse_srt(job.cn_srt) if job.cn_srt.exists() else []
    if len(cn) == len(vi_zh) and not any(z and z.strip() != c.text.strip()
                                         for z, c in zip(vi_zh, cn)):
        return [(c.start, c.end) for c in cn]
    before = job.work_dir / BEFORE_RETIME
    if not vi_spans or not before.exists():
        return None
    old = parse_srt(before)
    if len(old) != len(cn):
        return None
    return remap_by_overlap(old, cn, vi_spans)


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
    times = vi_time_map(job, zh or [""] * len(vi), [(c.start, c.end) for c in vi])
    if times is None:
        return False
    for c, (s, e) in zip(vi, times):
        c.start, c.end = s, e
    write_srt(vi, job.vi_srt)
    return True
