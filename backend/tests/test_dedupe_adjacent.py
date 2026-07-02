"""N-5: lock translate.dedupe_adjacent — the pre-translation merge of OCR'd
near-identical consecutive cues. A wrong merge destroys a real subtitle line
BEFORE translation, so the policy boundaries are pinned here.
"""
from __future__ import annotations

from app.models import Cue
from app.pipeline.translate import dedupe_adjacent


def _cues(*texts_times):
    return [Cue(index=i + 1, start=s, end=e, text=t)
            for i, (t, s, e) in enumerate(texts_times)]


def test_equal_text_merges_and_extends_end():
    out = dedupe_adjacent(_cues(("你好世界", 0.0, 1.0), ("你好世界", 1.0, 2.5)))
    assert len(out) == 1
    assert out[0].start == 0.0 and out[0].end == 2.5
    assert out[0].text == "你好世界"


def test_containment_merge_keeps_longer_text():
    # garbage-suffix / partial OCR read: shorter (>=3 normalized chars) contained
    # in the longer one → one cue, cleaner (longer) text wins
    out = dedupe_adjacent(_cues(("今天天气不错", 0.0, 1.0), ("今天天气不错哦", 1.0, 2.0)))
    assert len(out) == 1
    assert out[0].text == "今天天气不错哦"


def test_short_containment_not_merged():
    # a 2-char line contained in the next is NOT containment-merged (min length 3
    # guards against merging genuinely distinct short lines like 好/好的吧...)
    out = dedupe_adjacent(_cues(("你好", 0.0, 1.0), ("你好世界真美", 1.0, 2.0)))
    assert len(out) == 2


def test_similarity_ratio_merges_near_identical():
    # one hanzi differs out of seven → ratio ≈ 0.857 ≥ 0.85 → same subtitle
    out = dedupe_adjacent(_cues(("今天天气真好啊", 0.0, 1.0), ("今天天气真好吧", 1.0, 2.0)))
    assert len(out) == 1


def test_distinct_lines_survive():
    out = dedupe_adjacent(_cues(("我们出发吧", 0.0, 1.0), ("到了记得给我打电话", 1.0, 2.0)))
    assert len(out) == 2
    assert [c.text for c in out] == ["我们出发吧", "到了记得给我打电话"]


def test_whitespace_ignored_in_comparison():
    out = dedupe_adjacent(_cues(("你好 世界", 0.0, 1.0), ("你好世界", 1.0, 2.0)))
    assert len(out) == 1


def test_indices_renumbered_sequentially():
    out = dedupe_adjacent(_cues(
        ("第一句", 0.0, 1.0), ("第一句", 1.0, 2.0),
        ("第二句话啊", 2.0, 3.0), ("第三句又不同", 3.0, 4.0),
    ))
    assert [c.index for c in out] == [1, 2, 3]


def test_merge_is_adjacent_only():
    # same text separated by a different line stays two cues (only consecutive merge)
    out = dedupe_adjacent(_cues(
        ("你好世界", 0.0, 1.0), ("完全不同的一句话", 1.0, 2.0), ("你好世界", 2.0, 3.0),
    ))
    assert len(out) == 3


def test_empty_and_single():
    assert dedupe_adjacent([]) == []
    single = dedupe_adjacent(_cues(("好", 0.0, 1.0)))
    assert len(single) == 1 and single[0].text == "好"
