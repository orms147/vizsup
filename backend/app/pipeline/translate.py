"""Stage: translate CN→VI. Two-pass, provider-pluggable.

Pass 1 (draft): a CN-native model (best source comprehension).
Pass 2 (refine, optional): a frontier model improves Vietnamese register/fluency.

Timecodes are stripped here and re-attached after — models only ever see text.
The result is written to ``job.vi_srt``, which the human edit gate then edits.

Resilience (N-4): every provider batch call gets 3 attempts with backoff; the
draft pass checkpoints each finished batch (``translate.checkpoint.json`` +
incremental ``vi.draft.srt``) so a hard failure at batch N keeps batches 1..N-1
on disk and the next run resumes instead of re-buying them; a refine batch that
still fails after retries keeps that batch's DRAFT lines — polish failure never
discards paid draft work.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from collections.abc import Callable
from difflib import SequenceMatcher
from pathlib import Path

from app.models import Cue, Job
from app.pipeline.srt import parse_srt, write_srt
from app.providers.registry import get_translator

# Batch size keeps each request bounded while giving the model surrounding context.
BATCH = 50
_RETRY_DELAYS = (2.0, 8.0)   # 3 attempts: try, wait 2s, try, wait 8s, final try
# Client errors that retrying only re-pays for (bad key, bad request, not-found).
_NON_RETRYABLE = {400, 401, 403, 404, 422}


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def dedupe_adjacent(cues: list[Cue], sim: float = 0.85) -> list[Cue]:
    """Merge consecutive (near-)identical cues into one spanning the combined time.

    A burned-in subtitle that lingers on screen gets OCR'd into several near-identical
    cues; without this they'd each be translated separately into slightly different
    Vietnamese. Merging first means one translation per real subtitle, and the VN line
    covers the subtitle's full duration. Safe for distinct ASR/SRT lines (only merges
    when the text is genuinely near-identical)."""
    out: list[Cue] = []
    for c in cues:
        if out:
            a, b = _norm(out[-1].text), _norm(c.text)
            shorter = min(a, b, key=len)
            same = (a == b) or (len(shorter) >= 3 and (a in b or b in a)) \
                or (a and b and SequenceMatcher(None, a, b).ratio() >= sim)
            if same:
                out[-1].end = max(out[-1].end, c.end)
                if len(c.text) > len(out[-1].text):     # keep the cleaner/longer read
                    out[-1].text = c.text
                continue
        out.append(Cue(index=len(out) + 1, start=c.start, end=c.end, text=c.text))
    return out


def _batched(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def _status(exc: Exception) -> int | None:
    # openai/anthropic APIStatusError both expose .status_code; duck-typing keeps
    # this module free of SDK imports.
    return getattr(exc, "status_code", None)


def _retry_after(exc: Exception) -> float | None:
    # Honor a 429/503 Retry-After header, clamped to 30 s so a hostile/huge value
    # can't freeze the run. Any missing/malformed header → None (fall back to backoff).
    try:
        return min(30.0, float(exc.response.headers["retry-after"]))
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def _retry(fn, *, sleep: Callable[[float], None]):
    """3 attempts with backoff. Retries only transient EXCEPTIONS — a non-retryable
    client error (bad key/request, 401/400/…) raises on the FIRST failure instead of
    burning 3 paid duplicate calls; a 429 carrying Retry-After waits that long (≤30 s).
    Parse misses already degrade safely inside parse_numbered and must not double-spend."""
    for delay in (*_RETRY_DELAYS, None):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            if delay is None or _status(exc) in _NON_RETRYABLE:
                raise
            sleep(max(delay, _retry_after(exc) or 0.0))


def _ckpt_key(provider: str, cn: list[str]) -> str:
    # covers the POST-dedupe text list (a stale checkpoint after cn.srt changed
    # would misalign lines) and the provider (never mix two engines' drafts)
    return hashlib.sha1((provider + "\n" + "\n".join(cn)).encode()).hexdigest()


def _load_checkpoint(path: Path, key: str) -> list[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("key") == key and isinstance(data.get("done"), list):
            done = [str(x) for x in data["done"]]
            return done[: len(done) - len(done) % BATCH]   # only whole batches count
    except (OSError, ValueError):
        pass
    return []


def translate_cues(
    cues: list[Cue],
    *,
    draft: str = "deepseek",
    refine: str | None = None,
    context: dict | None = None,
    glossary: dict[str, str] | None = None,
    work_dir: Path | None = None,
    log: Callable[[str], None] | None = None,
    auto_shorten: bool = True,
    _sleep: Callable[[float], None] = time.sleep,
) -> list[Cue]:
    cn = [c.text for c in cues]
    ckpt = (work_dir / "translate.checkpoint.json") if work_dir else None
    ckpt_refine = (work_dir / "translate.refine.checkpoint.json") if work_dir else None
    draft_srt = (work_dir / "vi.draft.srt") if work_dir else None
    n_batches = (len(cn) + BATCH - 1) // BATCH

    def _require(provider_name: str):
        provider = get_translator(provider_name)
        if not provider.available():
            raise RuntimeError(
                f"Translator '{provider_name}' is not available (missing key or dependency)."
            )
        return provider

    def _persist_progress(key: str, out: list[str]) -> None:
        if ckpt is not None:
            ckpt.write_text(json.dumps({"key": key, "done": out}, ensure_ascii=False),
                            encoding="utf-8")
        if draft_srt is not None:   # timecodes re-attached for the completed prefix
            write_srt([Cue(index=c.index, start=c.start, end=c.end, text=t)
                       for c, t in zip(cues, out)], draft_srt)

    def draft_pass(provider_name: str, src: list[str]) -> list[str]:
        provider = _require(provider_name)
        key = _ckpt_key(provider_name, src)
        out: list[str] = _load_checkpoint(ckpt, key) if ckpt else []
        if out and log:
            log(f"↷ Tiếp tục từ điểm dừng trước: {len(out)}/{len(src)} dòng nháp đã có")
        for i in range(0, len(src), BATCH):
            batch = src[i:i + BATCH]
            if i + len(batch) <= len(out):
                continue   # covered by the checkpoint
            result = _retry(
                lambda b=batch: provider.translate_texts(b, context=context, glossary=glossary),
                sleep=_sleep)
            out.extend(result)
            _persist_progress(key, out)   # a hard failure later keeps this batch
            if log and n_batches > 1:
                log(f"… dịch lô {i // BATCH + 1}/{n_batches}")
        return out

    def _persist_refine(key: str, out: list[str]) -> None:
        # Refine is ~95-98% of two-pass dollars; checkpoint each finished batch so a
        # crash/kill re-buys one partial batch, not all completed ones. Do NOT touch
        # vi.draft.srt — it must keep meaning *draft*, not half-refined.
        if ckpt_refine is not None:
            ckpt_refine.write_text(json.dumps({"key": key, "done": out}, ensure_ascii=False),
                                   encoding="utf-8")

    def refine_pass(provider_name: str, cn_src: list[str], vi_draft: list[str]) -> list[str]:
        # Feed the refiner BOTH the Chinese source and the VI draft (batches stay
        # aligned since cn/vi are 1:1); parse falls back to the draft, never the source.
        # A batch that degrades to draft is checkpointed as final for this run.
        provider = _require(provider_name)
        # Key covers BOTH source and draft (a re-run whose draft differs must
        # invalidate) with an unambiguous separator that can't appear in cue text.
        key = _ckpt_key("refine|" + provider_name, cn_src + ["\x00"] + vi_draft)
        out: list[str] = _load_checkpoint(ckpt_refine, key) if ckpt_refine else []
        if out and log:
            log(f"↷ Tiếp tục tinh chỉnh từ điểm dừng: {len(out)}/{len(vi_draft)} dòng đã có")
        for bi, i in enumerate(range(0, len(vi_draft), BATCH), 1):
            cn_b, vi_b = cn_src[i:i + BATCH], vi_draft[i:i + BATCH]
            if i + len(vi_b) <= len(out):
                continue   # covered by the checkpoint
            try:
                out.extend(_retry(
                    lambda c=cn_b, v=vi_b: provider.refine_texts(
                        c, v, context=context, glossary=glossary),
                    sleep=_sleep))
            except Exception as exc:  # noqa: BLE001 - polish failure keeps the draft
                out.extend(vi_b)
                if log:
                    log(f"⚠ Tinh chỉnh thất bại ở lô {bi} ({type(exc).__name__}) — "
                        f"dùng bản nháp cho lô này.")
            _persist_refine(key, out)   # a hard failure later keeps this batch
        return out

    def _check_count(out: list[str], stage: str, provider_name: str) -> None:
        # A short reply must be a loud error, not a silent truncation: the final
        # zip() would drop trailing cues. parse_numbered pads chat providers, but
        # DeepL-style APIs return their list verbatim.
        if len(out) != len(cn):
            raise RuntimeError(
                f"Bộ dịch '{provider_name}' ({stage}) trả về {len(out)} dòng cho "
                f"{len(cn)} câu — dừng lại để không ghi vi.srt lệch hàng."
            )

    vi = draft_pass(draft, cn)
    _check_count(vi, "bản nháp", draft)
    if refine:
        vi = refine_pass(refine, cn, vi)
        _check_count(vi, "tinh chỉnh", refine)
    if auto_shorten:
        vi = _autoshorten(cues, vi, refine or draft, log=log)

    return [Cue(index=c.index, start=c.start, end=c.end, text=t) for c, t in zip(cues, vi)]


def _autoshorten(cues: list[Cue], vi: list[str], provider_name: str,
                 *, log: Callable[[str], None] | None = None) -> list[str]:
    """N-6 light: one shorten_line rewrite for every line that can't be spoken
    inside its slot even at the 1.30× cap — BEFORE the gate, so the user reviews
    already-fitting lines. Skips silently when the provider can't rewrite; a
    rewrite that fails or doesn't get shorter keeps the original."""
    from app.pipeline.fit_estimate import MAX_TEMPO, fits, slot_seconds, speech_seconds

    prov = get_translator(provider_name)
    if not hasattr(prov, "rewrite") or not prov.available():
        return vi
    out = list(vi)
    shortened = 0
    for i in range(len(cues)):
        slot = slot_seconds(cues, i)
        if slot <= 0 or fits(out[i], slot):
            continue
        ratio = max(0.5, min(0.9, (slot * MAX_TEMPO) / max(0.1, speech_seconds(out[i]))))
        try:
            short = shorten_line(out[i], provider=provider_name, ratio=ratio)
        except Exception:  # noqa: BLE001 - optional polish; keep the draft line
            continue
        if short and speech_seconds(short) < speech_seconds(out[i]):
            out[i] = short
            shortened += 1
    if shortened and log:
        log(f"✂ Tự rút gọn {shortened} câu quá dài so với khe thời gian (trước cổng duyệt)")
    return out


def shorten_line(text: str, *, provider: str = "openrouter", ratio: float = 0.7) -> str:
    """Ask the LLM to shorten one Vietnamese line (so it fits its dub slot) while
    keeping the meaning and a natural spoken tone. Returns the shortened line."""
    prov = get_translator(provider)
    if not hasattr(prov, "rewrite"):
        raise RuntimeError(f"Bộ dịch '{provider}' không hỗ trợ rút gọn câu.")
    if not prov.available():
        raise RuntimeError(f"Bộ dịch '{provider}' chưa sẵn sàng (thiếu API key).")
    instruction = (
        "Bạn là biên tập phụ đề tiếng Việt. Hãy RÚT GỌN câu sau xuống khoảng "
        f"{int(ratio * 100)}% độ dài, GIỮ NGUYÊN ý nghĩa và giọng nói tự nhiên để lồng tiếng. "
        "Chỉ trả về DUY NHẤT một câu tiếng Việt đã rút gọn, không kèm giải thích hay dấu ngoặc kép."
    )
    out = prov.rewrite(text, instruction).strip().strip('"').strip()
    return out or text


def load_glossary(job: Job) -> dict[str, str]:
    """User-maintained zh→vi term table, merged from two optional files:
    ``<storage>/glossary.json`` (all jobs) then ``<work_dir>/glossary.json``
    (this video — its entries win). Missing/invalid files are simply skipped,
    so the pipeline never fails because of a hand-edited glossary."""
    from app.config import settings

    merged: dict[str, str] = {}
    for p in (Path(settings.vizsup_storage_dir) / "glossary.json",
              job.work_dir / "glossary.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            merged.update({str(k): str(v) for k, v in data.items() if str(k).strip()})
    return merged


def save_glossary(path: Path, entries: dict[str, str]) -> None:
    """Persist a zh→vi glossary (sorted keys, atomic temp+rename); an empty dict
    removes the file. Counterpart of load_glossary — the GUI dialog uses these
    so nobody hand-edits JSON."""
    path = Path(path)
    if not entries:
        path.unlink(missing_ok=True)
        return
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(dict(sorted(entries.items())), ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(path)


def translate(
    job: Job,
    *,
    draft: str = "deepseek",
    refine: str | None = None,
    glossary: dict[str, str] | None = None,
    force: bool = False,
    log: Callable[[str], None] | None = None,
) -> Job:
    """Read ``job.cn_srt``, translate, write ``job.vi_srt``.

    ``glossary=None`` auto-loads the user's glossary files (see load_glossary);
    pass an explicit dict (or {}) to override.

    ``vi.srt`` is THE file the human edit gate produces — an existing one is
    never silently overwritten: without ``force`` this raises before any paid
    API call; with ``force`` the old file is first backed up to
    ``vi.srt.bak-<timestamp>``. (An mtime "was it edited?" heuristic can't work:
    translate itself always leaves vi.srt newer than cn.srt.)
    """
    if job.vi_srt.exists():
        if not force:
            raise RuntimeError(
                "vi.srt đã tồn tại (có thể chứa bản sửa tay ở cổng duyệt) — không ghi đè. "
                "Dùng --force (CLI) hoặc tùy chọn 'Dịch lại' để dịch đè; bản cũ sẽ được "
                "sao lưu thành vi.srt.bak-<thời điểm>."
            )
        backup = job.work_dir / f"vi.srt.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(job.vi_srt, backup)

    cues = dedupe_adjacent(parse_srt(job.cn_srt))   # one translation per real subtitle
    context = {
        "title": job.metadata.get("title"),
        "platform": job.metadata.get("platform"),
    }
    if glossary is None:
        glossary = load_glossary(job) or None
    vi = translate_cues(cues, draft=draft, refine=refine, context=context,
                        glossary=glossary, work_dir=job.work_dir, log=log)
    write_srt(vi, job.vi_srt)
    # the paid work is now safely in vi.srt — the recovery artifacts are redundant
    (job.work_dir / "translate.checkpoint.json").unlink(missing_ok=True)
    (job.work_dir / "translate.refine.checkpoint.json").unlink(missing_ok=True)
    (job.work_dir / "vi.draft.srt").unlink(missing_ok=True)
    return job
