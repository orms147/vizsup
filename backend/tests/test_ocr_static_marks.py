"""A creator logo that is on the screen all video long ("只蝉漫画 bilibili", 2026-11-02, job
68fcd78739c9) must not glue two different short lines into one OCR group."""
from __future__ import annotations

from app.pipeline import ocr

MARK = "只蝉漫画 bilibili"


def _frames(lines: list[tuple[str, int]], n_total: int = 400) -> list[str]:
    """``lines`` = [(dialogue or "", frames)] spread to n_total frames, the logo on every one
    (misread now and then)."""
    reads = ["只蝉漫画 bilibili", "只蝉漫画 bilbili", "只婵漫画 bilibili", "只蝉漫画bililili"]
    out: list[str] = []
    for text, n in lines:
        for _ in range(n):
            out.append(f"{text} {reads[len(out) % len(reads)]}".strip())
    return out + [reads[0]] * (n_total - len(out))


def test_the_logo_is_found_from_the_reads_and_dialogue_is_not():
    texts = _frames([(f"第{i}句话", 3) for i in range(80)])
    marks = ocr.static_marks(texts)
    assert set(marks) == {"只蝉漫画", "bilibili"}
    assert ocr.static_marks(texts[:50]) == []                  # too few frames to know


def test_a_refrain_in_one_stretch_is_not_a_mark():
    texts = ["别走"] * 60 + [f"台词{i}号" for i in range(340)]     # 15 % of frames, one slice only
    assert ocr.static_marks(texts) == []


def test_misreads_and_glued_forms_are_cut_but_dialogue_stays():
    marks = ["只蝉漫画", "bilibili"]
    assert ocr.strip_static("你说清楚 只蝉漫画 bilbili", marks) == "你说清楚"
    assert ocr.strip_static("被我们逮住了 只蝉漫画bililili", marks) == "被我们逮住了"
    assert ocr.strip_static("我是谁 只婵漫画 bibi", marks) == "我是谁"
    assert ocr.strip_static("只蝉漫画 bilibili", marks) == ""
    assert ocr.strip_static("我用iPhone拍的", marks) == "我用iPhone拍的"   # untouched, no re-spacing


def test_short_lines_are_no_longer_swallowed_by_their_neighbours():
    per = []
    tail = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥天地玄黄宇宙洪荒"   # every line differs
    for i in range(30):
        for text in ("万幸您终于挺过来了", "什么玩意？", "徭役？", "", "你说清楚"):
            per += [f"{text}{tail[i]}" if text else ""] * 2
    texts = _frames([(t, 1) for t in per], n_total=len(per))
    frames = [(k / 2, t, 0.95) for k, t in enumerate(texts)]
    with_mark, _c, _r = zip(*ocr.group_frames(frames, sim=0.5, fps=2.0, min_dur=0.4))
    marks = ocr.static_marks(texts)
    clean = [(t, ocr.strip_static(x, marks), c) for t, x, c in frames]
    kept = [c.text for c, _cf, _rf in ocr.group_frames(clean, sim=0.5, fps=2.0, min_dur=0.4)]
    assert kept[:4] == ["万幸您终于挺过来了甲", "什么玩意？甲", "徭役？甲", "你说清楚甲"]
    assert len(kept) == 4 * 30
    assert len(with_mark) < len(kept)                           # the old grouping lost lines
