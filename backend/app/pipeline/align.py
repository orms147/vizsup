"""E-2 fusion core (pure): snap a CN-native transcript's sentence times onto a
word-level timing track.

FunASR Paraformer gives the most accurate Mandarin TEXT but coarse sentence
times; faster-whisper/whisperx give precise word timestamps but worse hanzi.
``fuse_transcript_timing`` keeps FunASR's text and borrows the timing: greedy
monotonic character matching (zh words are 1–2 hanzi, so char anchors are
robust); a sentence matching under half its characters keeps FunASR's own
times, so fusion can never be worse than FunASR alone.
"""
from __future__ import annotations

import re

from app.models import Cue

_PUNCT = re.compile(r"[\s，。！？、：；·…~—,.!?;:\"'“”‘’()（）\[\]【】-]+")
_LOOKAHEAD = 60   # chars of forward search per anchor — bounds mismatch damage


def _chars(s: str) -> str:
    return _PUNCT.sub("", s or "")


def fuse_transcript_timing(
    sentences: list[Cue],
    words: list[tuple[str, float, float]],
) -> list[Cue]:
    """``sentences``: CN transcript cues (text wins). ``words``: (text, start,
    end) from the timing engine. Returns new cues, text unchanged, times snapped
    to the first/last matched word; monotonic by construction."""
    stream: list[tuple[str, float, float]] = []
    for w, s, e in words:
        for ch in _chars(w):
            stream.append((ch, float(s), float(e)))

    out: list[Cue] = []
    wi = 0
    for cue in sentences:
        chars = _chars(cue.text)
        matched: list[int] = []
        wj = wi
        for ch in chars:
            k = wj
            limit = min(len(stream), wj + _LOOKAHEAD)
            while k < limit and stream[k][0] != ch:
                k += 1
            if k < limit:
                matched.append(k)
                wj = k + 1
        if chars and len(matched) >= 0.5 * len(chars):
            start = stream[matched[0]][1]
            end = max(stream[matched[-1]][2], start)
            wi = matched[-1] + 1
            out.append(Cue(index=cue.index, start=start, end=end, text=cue.text))
        else:  # too little anchor evidence → trust FunASR's own times
            out.append(Cue(index=cue.index, start=cue.start, end=cue.end, text=cue.text))

    for prev, cur in zip(out, out[1:]):   # clamp any residual non-monotonicity
        if cur.start < prev.start:
            cur.start = prev.start
        if cur.end < cur.start:
            cur.end = cur.start
    return out
