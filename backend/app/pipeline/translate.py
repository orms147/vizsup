"""Stage: translate CN→VI. Two-pass, provider-pluggable.

Pass 1 (draft): a CN-native model (best source comprehension).
Pass 2 (refine, optional): a frontier model improves Vietnamese register/fluency.

Timecodes are stripped here and re-attached after — models only ever see text.
The result is written to ``job.vi_srt``, which the human edit gate then edits.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.models import Cue, Job
from app.pipeline.srt import parse_srt, write_srt
from app.providers.registry import get_translator

# Batch size keeps each request bounded while giving the model surrounding context.
BATCH = 50


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


def translate_cues(
    cues: list[Cue],
    *,
    draft: str = "deepseek",
    refine: str | None = None,
    context: dict | None = None,
    glossary: dict[str, str] | None = None,
) -> list[Cue]:
    cn = [c.text for c in cues]

    def _require(provider_name: str):
        provider = get_translator(provider_name)
        if not provider.available():
            raise RuntimeError(
                f"Translator '{provider_name}' is not available (missing key or dependency)."
            )
        return provider

    def draft_pass(provider_name: str, src: list[str]) -> list[str]:
        provider = _require(provider_name)
        out: list[str] = []
        for batch in _batched(src, BATCH):
            out.extend(provider.translate_texts(batch, context=context, glossary=glossary))
        return out

    def refine_pass(provider_name: str, cn_src: list[str], vi_draft: list[str]) -> list[str]:
        # Feed the refiner BOTH the Chinese source and the VI draft (batches stay
        # aligned since cn/vi are 1:1); parse falls back to the draft, never the source.
        provider = _require(provider_name)
        out: list[str] = []
        for cn_b, vi_b in zip(_batched(cn_src, BATCH), _batched(vi_draft, BATCH)):
            out.extend(provider.refine_texts(cn_b, vi_b, context=context, glossary=glossary))
        return out

    vi = draft_pass(draft, cn)
    if refine:
        vi = refine_pass(refine, cn, vi)

    return [Cue(index=c.index, start=c.start, end=c.end, text=t) for c, t in zip(cues, vi)]


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


def translate(
    job: Job,
    *,
    draft: str = "deepseek",
    refine: str | None = None,
    glossary: dict[str, str] | None = None,
) -> Job:
    """Read ``job.cn_srt``, translate, write ``job.vi_srt``."""
    cues = dedupe_adjacent(parse_srt(job.cn_srt))   # one translation per real subtitle
    context = {
        "title": job.metadata.get("title"),
        "platform": job.metadata.get("platform"),
    }
    vi = translate_cues(cues, draft=draft, refine=refine, context=context, glossary=glossary)
    write_srt(vi, job.vi_srt)
    return job
