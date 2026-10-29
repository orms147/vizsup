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
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from difflib import SequenceMatcher
from pathlib import Path

from app.models import Cue, Job
from app.pipeline.srt import parse_srt, write_srt
from app.providers.registry import get_translator
from app.providers.translation._common import _completion_cap, parse_numbered

# Batch size keeps each request bounded while giving the model surrounding context.
BATCH = 50
_RETRY_DELAYS = (2.0, 8.0)   # 3 attempts: try, wait 2s, try, wait 8s, final try
# Client errors that retrying only re-pays for (bad key, bad request, not-found).
_NON_RETRYABLE = {400, 401, 403, 404, 422}


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


# Chinese punctuation a model sometimes leaves in a Vietnamese line (2026-10-27 cost pass:
# GPT-6 Luna refining its own draft returned "Bọn mình đoán là Duo】【。"). Mapped to the
# Latin mark or dropped; Chinese CHARACTERS are left alone — that is a real miss to review.
_CJK_PUNCT = str.maketrans({"。": ". ", "，": ", ", "、": ", ", "！": "! ", "？": "? ",
                            "：": ": ", "；": "; ", "（": " (", "）": ") ",
                            "【": "", "】": "", "「": "", "」": "", "『": "", "』": "",
                            "《": "", "》": ""})
_HANZI = re.compile(r"[㐀-䶿一-鿿]")


def clean_vi(text: str) -> str:
    """A translated line without stray Chinese punctuation (and the space it leaves).
    A line that still holds Chinese characters (passthrough, an untranslated miss) is
    returned as is."""
    if not text or text.isascii() or _HANZI.search(text):
        return text
    out = text.translate(_CJK_PUNCT)
    if out == text:
        return text
    out = re.sub(r"\s+([.,!?:;)])", r"\1", out)
    out = re.sub(r"([.,!?:;])\1+", r"\1", out)
    return re.sub(r"\s{2,}", " ", out).strip()


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


class TranslateCancelled(RuntimeError):
    """Raised when translate_cues is cancelled mid-run. Every completed batch is
    already persisted to the checkpoint, so a resume re-buys nothing."""


def _load_checkpoint(path: Path, key: str, n_lines: int) -> dict[int, list[str]]:
    """Return completed batches as ``{batch_index: [lines]}``. Accepts the v2 format
    (``{"v":2,"batches":{"0":[…]}}``) AND the legacy v1 whole-batch prefix
    (``{"done":[…]}`` — incl. API-6's refine file) by slicing that prefix into
    batches. A stale key, or a batch whose length doesn't match its expected size,
    is dropped."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("key") != key:
        return {}
    n_batches = (n_lines + BATCH - 1) // BATCH

    def _expected(bi: int) -> int:
        return min(BATCH, n_lines - bi * BATCH)

    out: dict[int, list[str]] = {}
    if data.get("v") == 2 and isinstance(data.get("batches"), dict):
        for k, v in data["batches"].items():
            try:
                bi = int(k)
            except (TypeError, ValueError):
                continue
            if 0 <= bi < n_batches and isinstance(v, list) and len(v) == _expected(bi):
                out[bi] = [str(x) for x in v]
        return out
    done = data.get("done")                       # legacy v1 whole-batch prefix
    if isinstance(done, list):
        whole = len(done) - len(done) % BATCH
        for bi in range(whole // BATCH):
            out[bi] = [str(x) for x in done[bi * BATCH:(bi + 1) * BATCH]]
    return out


def _persist_batches(ckpt_path: Path | None, draft_srt_path: Path | None, key: str,
                     batches: dict[int, list[str]], cues: list[Cue], n_batches: int) -> None:
    """Atomically write the v2 checkpoint (tmp+replace, mirroring save_glossary).
    When a draft-SRT path is given, also write it from the LONGEST CONTIGUOUS PREFIX
    of completed batches, so the recovery SRT never has holes (a missing batch just
    stops the prefix)."""
    if ckpt_path is not None:
        data = {"key": key, "v": 2,
                "batches": {str(bi): batches[bi] for bi in sorted(batches)}}
        tmp = ckpt_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(ckpt_path)
    if draft_srt_path is not None:
        prefix: list[str] = []
        for bi in range(n_batches):
            if bi not in batches:
                break
            prefix.extend(batches[bi])
        write_srt([Cue(index=c.index, start=c.start, end=c.end, text=t)
                   for c, t in zip(cues, prefix)], draft_srt_path)


def _run_parallel(missing: list[int], width: int,
                  task: Callable[[int], object], on_done: Callable[[int, object], None],
                  *, cancel: Callable[[], bool] | None) -> None:
    """Run batch indices ``missing`` through ``task`` up to ``width``-wide using a
    SLIDING WINDOW: at most ``width`` futures are ever submitted, and the next batch
    is submitted only after a cancel check — so a cancel stops further provider calls
    immediately (the pool can't auto-start a queued batch behind our back).
    ``on_done(bi, result)`` runs on THIS thread as each finishes (persist + log stay
    serialized). The first task exception aborts admission and, after in-flight
    batches drain (their results still persisted), is re-raised; a cancel drains then
    raises TranslateCancelled. A hard failure or cancel never loses paid work."""
    if cancel and cancel():
        raise TranslateCancelled("Đã hủy khi đang dịch.")
    ex = ThreadPoolExecutor(max_workers=max(1, width))
    queue = list(missing)
    qi = 0
    inflight: dict = {}
    error: Exception | None = None
    cancelled = False
    try:
        while True:
            # Admit new batches only while healthy; check cancel BEFORE each submit.
            while (error is None and not cancelled
                   and len(inflight) < width and qi < len(queue)):
                if cancel and cancel():
                    cancelled = True
                    break
                bi = queue[qi]
                qi += 1
                inflight[ex.submit(task, bi)] = bi
            if not inflight:
                break
            done, _ = wait(inflight, return_when=FIRST_COMPLETED)
            for fut in done:
                bi = inflight.pop(fut)
                try:
                    result = fut.result()
                except Exception as exc:  # noqa: BLE001 - first failure aborts admission
                    if error is None:
                        error = exc
                    continue
                on_done(bi, result)       # persist every completed batch, even mid-drain
            if error is None and not cancelled and cancel and cancel():
                cancelled = True
    finally:
        ex.shutdown(wait=True, cancel_futures=True)
    if error:
        raise error
    if cancelled:
        raise TranslateCancelled("Đã hủy khi đang dịch.")


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
    rate: float | None = None,
    cancel: Callable[[], bool] | None = None,
    _sleep: Callable[[float], None] = time.sleep,
) -> list[Cue]:
    from app.pipeline.fit_estimate import PREGATE_RATE

    rate = rate or PREGATE_RATE         # the voice speed lines are budgeted for (job_rate)
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

    def draft_pass(provider_name: str, src: list[str]) -> list[str]:
        # N-4 + API-9: draft each batch, up to `width`-wide, checkpointing every
        # finished batch (v2 per-batch format) so a crash/cancel keeps completed
        # batches and the resume re-buys nothing.
        provider = _require(provider_name)
        key = _ckpt_key(provider_name, src)
        completed = _load_checkpoint(ckpt, key, len(src)) if ckpt else {}
        if completed and log:
            have = sum(len(v) for v in completed.values())
            log(f"↷ Tiếp tục từ điểm dừng trước: {have}/{len(src)} dòng nháp đã có")
        missing = [bi for bi in range(n_batches) if bi not in completed]
        if missing:
            width = min(4, getattr(provider, "parallel_batches", 1), len(missing))

            def task(bi: int) -> list[str]:
                batch = src[bi * BATCH:(bi + 1) * BATCH]
                result = _retry(lambda: provider.translate_texts(
                    batch, context=context, glossary=glossary), sleep=_sleep)
                if len(result) != len(batch):   # per-batch count guard, inside the task
                    raise RuntimeError(
                        f"Bộ dịch '{provider_name}' trả về {len(result)} dòng cho lô "
                        f"{bi + 1} ({len(batch)} câu) — dừng để không ghi lệch hàng.")
                return result

            def on_done(bi: int, result: object) -> None:
                completed[bi] = result  # type: ignore[assignment]
                _persist_batches(ckpt, draft_srt, key, completed, cues, n_batches)
                if log and n_batches > 1:
                    log(f"… dịch lô {len(completed)}/{n_batches}")

            _run_parallel(missing, width, task, on_done, cancel=cancel)
        return [line for bi in range(n_batches) for line in completed[bi]]

    def _draft_budgets(vi_draft: list[str]) -> list[int | None]:
        # API-8: per-line word budget for overlong draft lines, folded into the
        # refine prompt so the refiner shortens WHILE polishing (kills the
        # draft → refine-the-long-version → autoshorten triple-pay). Fitting lines
        # get None so their prompt block stays byte-identical to before.
        from app.pipeline.fit_estimate import fits, slot_seconds, word_budget

        budgets: list[int | None] = []
        for j in range(len(vi_draft)):
            slot = slot_seconds(cues, j)
            budgets.append(word_budget(slot, rate)
                           if slot > 0 and not fits(vi_draft[j], slot, rate) else None)
        return budgets

    def refine_pass(provider_name: str, cn_src: list[str], vi_draft: list[str]) -> list[str]:
        # Feed the refiner BOTH the Chinese source and the VI draft (batches stay
        # aligned since cn/vi are 1:1); parse falls back to the draft, never the source.
        # A batch whose polish fails degrades to its draft (never fatal) and is
        # checkpointed as final for this run. Refine never writes vi.draft.srt — that
        # file must keep meaning *draft*, not half-refined. Refine is ~95-98% of a
        # two-pass run's dollars, so every finished batch is persisted immediately.
        provider = _require(provider_name)
        # Key covers BOTH source and draft (a re-run whose draft differs must
        # invalidate) with an unambiguous separator that can't appear in cue text.
        key = _ckpt_key("refine|" + provider_name, cn_src + ["\x00"] + vi_draft)
        completed = _load_checkpoint(ckpt_refine, key, len(vi_draft)) if ckpt_refine else {}
        if completed and log:
            have = sum(len(v) for v in completed.values())
            log(f"↷ Tiếp tục tinh chỉnh từ điểm dừng: {have}/{len(vi_draft)} dòng đã có")
        budgets = _draft_budgets(vi_draft)
        missing = [bi for bi in range(n_batches) if bi not in completed]
        if missing:
            width = min(4, getattr(provider, "parallel_batches", 1), len(missing))

            def task(bi: int) -> tuple[list[str], Exception | None]:
                cn_b = cn_src[bi * BATCH:(bi + 1) * BATCH]
                vi_b = vi_draft[bi * BATCH:(bi + 1) * BATCH]
                ctx_b = {**(context or {}), "_budgets": budgets[bi * BATCH:(bi + 1) * BATCH]}
                try:
                    return (_retry(lambda: provider.refine_texts(
                        cn_b, vi_b, context=ctx_b, glossary=glossary), sleep=_sleep), None)
                except Exception as exc:  # noqa: BLE001 - polish failure keeps the draft
                    return (vi_b, exc)

            def on_done(bi: int, result: object) -> None:
                lines, exc = result  # type: ignore[misc]
                completed[bi] = lines
                if exc is not None and log:
                    log(f"⚠ Tinh chỉnh thất bại ở lô {bi + 1} ({type(exc).__name__}) — "
                        f"dùng bản nháp cho lô này.")
                _persist_batches(ckpt_refine, None, key, completed, cues, n_batches)

            _run_parallel(missing, width, task, on_done, cancel=cancel)
        return [line for bi in range(n_batches) for line in completed[bi]]

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
        vi = _autoshorten(cues, vi, refine or draft, log=log, rate=rate)

    return [Cue(index=c.index, start=c.start, end=c.end, text=clean_vi(t))
            for c, t in zip(cues, vi)]


SHORTEN_BATCH_INSTRUCTION = (
    "Bạn là biên tập phụ đề lồng tiếng. Mỗi dòng có dạng `n| (rút còn ~X%) <câu>`. "
    "Hãy RÚT GỌN mỗi câu về khoảng X% độ dài, GIỮ NGUYÊN ý nghĩa và giọng nói tự nhiên "
    "để lồng tiếng. Trả về ĐÚNG số dòng, mỗi dòng dạng `n| <câu đã rút gọn>`, KHÔNG "
    "kèm giải thích, KHÔNG lặp lại phần `(rút còn ~X%)`, không dùng dấu ngoặc kép."
)


def _autoshorten(cues: list[Cue], vi: list[str], provider_name: str,
                 *, log: Callable[[str], None] | None = None,
                 rate: float | None = None) -> list[str]:
    """N-6 light + API-5: shorten every line that can't be spoken inside its slot
    even at the 1.30× cap — in ONE batched rewrite() call per BATCH-sized chunk,
    BEFORE the gate (so the user reviews already-fitting lines). Skips silently when
    the provider can't rewrite; a rewrite that fails or doesn't shorten a line keeps
    the original. shorten_line (per-line, GUI editor) stays untouched."""
    from app.pipeline.fit_estimate import (MAX_TEMPO, PREGATE_RATE, SEG_PAD, fits, slot_seconds,
                                           speech_seconds)

    rate = rate or PREGATE_RATE
    prov = get_translator(provider_name)
    if not hasattr(prov, "rewrite") or not prov.available():
        return vi
    out = list(vi)
    # Pass 1: which lines overflow, and to roughly what % (bucketed → constant-ish
    # instruction stays cacheable; the exact ratio doesn't need per-line precision).
    todo: list[tuple[int, int]] = []
    for i in range(len(cues)):
        slot = slot_seconds(cues, i)
        if slot <= 0 or fits(out[i], slot, rate):
            continue
        # the words must shrink; the fixed per-segment pad cannot
        ratio = max(0.0, slot * MAX_TEMPO - SEG_PAD) / max(0.1, speech_seconds(out[i], rate) - SEG_PAD)
        pct = max(50, min(90, round(ratio * 10) * 10))
        todo.append((i, pct))
    if not todo:
        return vi
    # Pass 2: one rewrite() round-trip per chunk instead of one per overlong line.
    shortened = 0
    for chunk in _batched(todo, BATCH):
        body = "\n".join(f"{k}| (rút còn ~{pct}%) {out[i]}"
                         for k, (i, pct) in enumerate(chunk, 1))
        try:
            reply = prov.rewrite(body, SHORTEN_BATCH_INSTRUCTION,
                                 max_tokens=_completion_cap(len(chunk)))
        except Exception:  # noqa: BLE001 - optional polish; keep the draft lines
            continue
        current = [out[i] for i, _ in chunk]
        for (i, _), cand in zip(chunk, parse_numbered(reply, len(chunk), current)):
            cand = cand.strip().strip('"').strip()
            # acceptance gate: only take a candidate that is genuinely shorter
            if cand and speech_seconds(cand) < speech_seconds(out[i]):
                out[i] = cand
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


def shorten_lines(items: list[tuple[str, int]], *, provider: str = "openrouter",
                  cancel: Callable[[], bool] | None = None) -> list[str]:
    """Editor "Rút gọn hàng loạt": shorten many VI lines, each to about ``pct``% of
    its length — ``items`` = [(text, pct)] — in one batched rewrite() per BATCH lines
    (same prompt as the pre-gate auto-shorten). Returns one line per item; a line the
    model did not shorten comes back unchanged. Raises when the provider can't."""
    prov = get_translator(provider)
    if not hasattr(prov, "rewrite"):
        raise RuntimeError(f"Bộ dịch '{provider}' không hỗ trợ rút gọn câu.")
    if not prov.available():
        raise RuntimeError(f"Bộ dịch '{provider}' chưa sẵn sàng (thiếu API key).")
    out = [t for t, _ in items]
    for start in range(0, len(items), BATCH):
        if cancel and cancel():
            break
        chunk = items[start:start + BATCH]
        body = "\n".join(f"{k}| (rút còn ~{pct}%) {t}" for k, (t, pct) in enumerate(chunk, 1))
        reply = prov.rewrite(body, SHORTEN_BATCH_INSTRUCTION,
                             max_tokens=_completion_cap(len(chunk)))
        current = [t for t, _ in chunk]
        for k, cand in enumerate(parse_numbered(reply, len(chunk), current)):
            cand = cand.strip().strip('"').strip()
            if cand and len(cand) < len(current[k]):
                out[start + k] = cand
    return out


ALT_STYLES = ("Sát nghĩa", "Tự nhiên", "Ngắn gọn")
ALT_INSTRUCTION = (
    "Bạn là biên tập viên phụ đề Trung → Việt. Cho DÒNG CẦN DỊCH LẠI (tiếng Trung + bản "
    "tiếng Việt hiện tại) cùng vài dòng xung quanh làm ngữ cảnh, hãy viết đúng 3 phương án "
    "tiếng Việt cho riêng dòng đó, mỗi dòng một phương án theo dạng `n| <câu>`:\n"
    "1| sát nghĩa nhất với câu gốc\n"
    "2| tự nhiên nhất khi nói (giữ đúng xưng hô của video)\n"
    "3| ngắn gọn nhất mà vẫn đủ ý (để lồng tiếng vừa thời lượng)\n"
    "Không giải thích, không thêm dòng nào khác."
)


def alternatives(zh: str, vi: str, *, before: list[tuple[str, str]] = (),
                 after: list[tuple[str, str]] = (), request: str = "", brief: str = "",
                 provider: str = "openrouter") -> list[str]:
    """E-6 "Dịch lại": three renderings of one line (sát nghĩa / tự nhiên / ngắn gọn)
    with neighbouring (zh, vi) lines as context and an optional user request. Uses
    the "shorten" role model. Raises when the provider can't do it."""
    prov = get_translator(provider)
    if not hasattr(prov, "rewrite"):
        raise RuntimeError(f"Bộ dịch '{provider}' không hỗ trợ dịch lại câu.")
    if not prov.available():
        raise RuntimeError(f"Bộ dịch '{provider}' chưa sẵn sàng (thiếu API key).")
    parts = []
    if brief.strip():
        parts.append(f"Ngữ cảnh video:\n{brief.strip()}")
    ctx = [f"ZH: {z}\nVI: {v}" for z, v in before]
    ctx.append(f">>> DÒNG CẦN DỊCH LẠI\nZH: {zh}\nVI hiện tại: {vi}")
    ctx += [f"ZH: {z}\nVI: {v}" for z, v in after]
    parts.append("\n\n".join(ctx))
    if request.strip():
        parts.append(f"Yêu cầu thêm của người dùng (ưu tiên): {request.strip()}")
    reply = prov.rewrite("\n\n".join(parts), ALT_INSTRUCTION, max_tokens=400, role="shorten")
    out = [s.strip().strip('"').strip() for s in parse_numbered(reply or "", 3, ["", "", ""])]
    return [s for s in out if s] or [vi]

def clear_ocr_provenance(job: Job) -> None:
    """Remove a leftover ``ocr_report.json`` when cn.srt is (re)generated from a
    NON-OCR source (provided SRT / ASR). Otherwise a re-run of the same job dir
    would still see the old report and wrongly add the OCR-tolerance hint (API-7)
    to translate prompts. Called by the worker's and CLI's srt/asr branches."""
    (job.work_dir / "ocr_report.json").unlink(missing_ok=True)


def load_glossary(job: Job) -> dict[str, str]:
    """User-maintained zh→vi term table, merged from two optional files:
    ``<storage>/glossary.json`` (all jobs) then ``<work_dir>/glossary.json``
    (this video — its entries win). Missing/invalid files are simply skipped,
    so the pipeline never fails because of a hand-edited glossary."""
    from app.config import settings

    from app.pipeline import series

    merged: dict[str, str] = {}
    # precedence low→high (dict.update): auto-suggested (ENG-1) < this series' terms and
    # characters < global < this job
    for p in (job.work_dir / "glossary.suggested.json", None,
              Path(settings.vizsup_storage_dir) / "glossary.json",
              job.work_dir / "glossary.json"):
        if p is None:
            merged.update(series.glossary_for(job))
            continue
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
    cancel: Callable[[], bool] | None = None,
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
    # API-7: when the CN text came from OCR, tell the translator to tolerate OCR
    # noise IN THE PROMPT (private _ocr_source key — never rendered as meta text).
    # This makes the separate proofread pass optional instead of the default.
    if (job.work_dir / "ocr_report.json").exists():
        context["_ocr_source"] = True
        if log:
            log("ℹ Nguồn OCR — dịch chịu lỗi OCR ngay trong prompt (+~25 token/lô)")
    # Per-video brief (pipeline/brief.py), if one was built for this job. Private key:
    # it renders into the SYSTEM prompt, never into the "Video context:" meta line.
    from app.pipeline.brief import load_brief

    brief = load_brief(job)
    if brief:
        context["_brief"] = brief
        if log:
            log("ℹ Dịch theo brief ngữ cảnh của video này")
    # E-8: the listening model heard mostly singing → lyric-style translation
    from app.pipeline.speakers import singing

    if singing(job) in ("yes", "mixed"):
        context["_lyrics"] = True
        if log:
            log("ℹ Video có hát — dịch lời hát cho tự nhiên, giữ nhịp, điệp khúc thống nhất")
    # E-7: the user's own recurring corrections ride in every prompt
    from app.pipeline import memory

    rules = memory.style_rules()
    if rules:
        context["_style"] = rules
        if log:
            log("ℹ Dịch theo “Phong cách của tôi” (rút ra từ các lần bạn sửa)")
    # Bộ phim: the series sheet (fixed names / xưng hô) and the last lines of the previous
    # episode ride in the prompt, so episode N picks up where N-1 stopped
    from app.pipeline import series

    ser = series.context_for(job)
    if ser:
        context.update(ser)
        if log:
            log("ℹ Dịch theo bộ phim của video này" + (" + vài câu cuối tập trước" if "_prev" in ser else ""))
    if glossary is None:
        glossary = load_glossary(job) or None
    from app.pipeline.fit_estimate import job_rate

    rate = job_rate(job)                 # the speed this project / series is dubbed at
    if log:
        log(f"ℹ Ngân sách độ dài câu tính theo tốc độ đọc ×{rate:.2f}")
    vi = translate_cues(cues, draft=draft, refine=refine, context=context,
                        glossary=glossary, work_dir=job.work_dir, log=log, cancel=cancel,
                        rate=rate)
    # E-7: lines you already approved in another video keep your approved wording
    texts, reused = memory.apply_tm([c.text for c in cues], [c.text for c in vi])
    if reused:
        for c, t in zip(vi, texts):
            c.text = t
        if log:
            log(f"↷ {reused} câu dùng lại bản bạn đã duyệt trước đây (bộ nhớ dịch)")
    write_srt(vi, job.vi_srt)
    memory.save_machine_translation(job)
    # ENG-1: flag lines where a glossary term's VI target is missing (advisory — the
    # user fixes them at the gate; never rewrites the translation).
    from app.pipeline import terms
    n_bad = terms.write_glossary_report(job, cues, vi, glossary)
    if n_bad and log:
        log(f"⚠ {n_bad} dòng lệch thuật ngữ — kiểm tra ở bước sửa phụ đề")
    # the paid work is now safely in vi.srt — the recovery artifacts are redundant
    (job.work_dir / "translate.checkpoint.json").unlink(missing_ok=True)
    (job.work_dir / "translate.refine.checkpoint.json").unlink(missing_ok=True)
    (job.work_dir / "vi.draft.srt").unlink(missing_ok=True)
    return job
