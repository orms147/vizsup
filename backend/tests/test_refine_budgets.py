"""API-8: fold per-line length budgets into the refine prompt so an overlong draft
line is shortened WHILE it's polished — killing the draft → refine-the-long-version
→ autoshorten triple-pay. Only overlong lines are annotated; fitting lines render
byte-identically to before, and the private _budgets key never leaks into the text.
"""
from __future__ import annotations

from app.models import Cue
from app.pipeline import translate
from app.providers.translation._common import build_refine_prompt


def test_only_overlong_lines_annotated():
    ctx = {"_budgets": [None, 5, None]}
    p = build_refine_prompt(["a", "b", "c"], ["va", "vb", "vc"], context=ctx)
    assert p.count("rút còn tối đa") == 1
    assert "rút còn tối đa ~5 từ): vb" in p
    assert "[1] nháp: va" in p and "[3] nháp: vc" in p     # plain form untouched
    assert "_budgets" not in p                              # key never leaks


def test_all_fitting_is_byte_identical_to_plain():
    plain = build_refine_prompt(["a", "b"], ["va", "vb"])
    none_budgets = build_refine_prompt(["a", "b"], ["va", "vb"],
                                       context={"_budgets": [None, None]})
    assert plain == none_budgets


def test_budgets_absent_key_is_plain():
    plain = build_refine_prompt(["a"], ["va"])
    with_ctx = build_refine_prompt(["a"], ["va"], context={"title": "T"})
    assert "[1] nháp: va" in plain and "[1] nháp: va" in with_ctx


# --- integration: an annotated overlong batch that fails still degrades to draft --

class _RaisingRefiner:
    def available(self):
        return True

    def translate_texts(self, texts, **kw):
        return ["một " * 20] * len(texts)     # overlong draft → budget computed

    def refine_texts(self, cn, vi, **kw):
        # sanity: the overlong batch really was annotated with a budget
        assert kw["context"]["_budgets"][0] is not None
        raise RuntimeError("refiner down")


def test_refine_failure_on_annotated_batch_degrades(monkeypatch):
    monkeypatch.setattr(translate, "get_translator", lambda name: _RaisingRefiner())
    cues = [Cue(index=1, start=0.0, end=2.0, text="cn")]   # tight slot → draft overlong
    logs: list[str] = []
    out = translate.translate_cues(cues, draft="fake", refine="fake",
                                   auto_shorten=False, log=logs.append, _sleep=lambda s: None)
    assert out[0].text.startswith("một")                   # kept the draft
    assert any("Tinh chỉnh thất bại" in m for m in logs)
