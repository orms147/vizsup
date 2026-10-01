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


# --- global alignment for text-only cloud engines -----------------------------
# fuse_transcript_timing anchors one sentence at a time inside a 60-char window, and
# keeps the sentence's OWN times when anchoring fails. That is right for FunASR (its
# own times are decent) but wrong for text-only cloud engines, whose "own" times are a
# proportional guess across a whole chunk: on a real 136 s Douyin vlog one missed
# sentence knocked the following ones out of the window and cues landed 13 s off.
# align_text_to_words aligns the WHOLE chunk text to the word track at once
# (difflib, monotonic by construction) and interpolates every unmatched character
# between its nearest anchors — an error stays inside the gap between two anchors.

SECONDS_PER_CHAR = 0.22   # Mandarin ≈ 4–6 chars/s; used only beyond the outermost anchors
MIN_CUE = 0.3


def align_text_to_words(
    sentences: list[str],
    words: list[tuple[str, float, float]],
    span: tuple[float, float],
) -> list[Cue]:
    """Time each sentence of a text-only transcript of the audio window ``span``
    using a word-level timing track (``(text, start, end)``, absolute seconds).
    Returns cues with index 0 (callers reindex). Never raises; with no usable
    anchors it falls back to spreading the sentences over ``span`` by length."""
    import difflib

    lo, hi = span
    a_parts = [_chars(s) for s in sentences]
    a = "".join(a_parts)
    stream: list[tuple[str, float, float]] = []
    for w, s, e in words:
        for ch in _chars(w):
            stream.append((ch, float(s), float(e)))
    b = "".join(ch for ch, _, _ in stream)

    anchors: dict[int, tuple[float, float]] = {}
    if a and b:
        sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
        for blk in sm.get_matching_blocks():
            if blk.size < 2:          # lone common hanzi (的/了/我) match anywhere — noise
                continue
            for k in range(blk.size):
                _, s, e = stream[blk.b + k]
                anchors[blk.a + k] = (s, e)
    if not anchors:
        return _spread(sentences, lo, hi)

    keys = sorted(anchors)

    def at(k: int, edge: int) -> float:       # edge 0 = start time, 1 = end time
        if k in anchors:
            return anchors[k][edge]
        import bisect

        j = bisect.bisect_left(keys, k)
        if j == 0:                             # before the first anchor
            first = keys[0]
            return max(lo, anchors[first][0] - (first - k) * SECONDS_PER_CHAR)
        if j == len(keys):                     # after the last anchor
            last = keys[-1]
            return min(hi, anchors[last][1] + (k - last) * SECONDS_PER_CHAR)
        i0, i1 = keys[j - 1], keys[j]
        t0, t1 = anchors[i0][1], anchors[i1][0]
        return t0 + (t1 - t0) * (k - i0) / (i1 - i0)

    out: list[Cue] = []
    pos = 0
    for text, part in zip(sentences, a_parts):
        if not part:
            continue
        start, end = at(pos, 0), at(pos + len(part) - 1, 1)
        out.append(Cue(index=0, start=start, end=max(end, start + MIN_CUE), text=text))
        pos += len(part)

    for prev, cur in zip(out, out[1:]):        # monotonic, no overlaps
        if cur.start < prev.start:
            cur.start = prev.start
        if prev.end > cur.start and cur.start > prev.start:
            prev.end = cur.start
        if cur.end < cur.start + MIN_CUE:
            cur.end = cur.start + MIN_CUE
    for c in out:
        c.start, c.end = round(c.start, 3), round(c.end, 3)
    return out


def _spread(sentences: list[str], lo: float, hi: float) -> list[Cue]:
    weights = [max(1, len(_chars(s))) for s in sentences]
    total, t, out = sum(weights) or 1, lo, []
    for s, w in zip(sentences, weights):
        d = (hi - lo) * w / total
        out.append(Cue(index=0, start=round(t, 3), end=round(t + d, 3), text=s))
        t += d
    return out
