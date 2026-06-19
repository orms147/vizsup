"""Stage: translate CN→VI. Two-pass, provider-pluggable.

Pass 1 (draft): a CN-native model (best source comprehension).
Pass 2 (refine, optional): a frontier model improves Vietnamese register/fluency.

Timecodes are stripped here and re-attached after — models only ever see text.
The result is written to ``job.vi_srt``, which the human edit gate then edits.
"""
from __future__ import annotations

from app.models import Cue, Job
from app.pipeline.srt import parse_srt, write_srt
from app.providers.registry import get_translator

# Batch size keeps each request bounded while giving the model surrounding context.
BATCH = 50


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
    texts = [c.text for c in cues]

    def run(provider_name: str, src: list[str]) -> list[str]:
        provider = get_translator(provider_name)
        if not provider.available():
            raise RuntimeError(
                f"Translator '{provider_name}' is not available (missing key or dependency)."
            )
        out: list[str] = []
        for batch in _batched(src, BATCH):
            out.extend(provider.translate_texts(batch, context=context, glossary=glossary))
        return out

    translated = run(draft, texts)
    if refine:
        translated = run(refine, translated)

    return [Cue(index=c.index, start=c.start, end=c.end, text=t) for c, t in zip(cues, translated)]


def translate(
    job: Job,
    *,
    draft: str = "deepseek",
    refine: str | None = None,
    glossary: dict[str, str] | None = None,
) -> Job:
    """Read ``job.cn_srt``, translate, write ``job.vi_srt``."""
    cues = parse_srt(job.cn_srt)
    context = {
        "title": job.metadata.get("title"),
        "platform": job.metadata.get("platform"),
    }
    vi = translate_cues(cues, draft=draft, refine=refine, context=context, glossary=glossary)
    write_srt(vi, job.vi_srt)
    return job
