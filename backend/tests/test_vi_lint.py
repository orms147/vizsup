"""Vietnamese subtitle lint (pipeline/vi_lint.py): safe auto-fixes, review flags, the series
ban, the worker/CLI hook and the editor action (one undo step). No network."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from app.config import settings
from app.models import Cue, Job
from app.pipeline import review, series, vi_lint
from app.pipeline.srt import parse_srt, write_srt

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# --- auto-fix --------------------------------------------------------------------------------

@pytest.mark.parametrize("src,out", [
    ("Xin  chào   bạn", "Xin chào bạn"),
    ("Xin chào , bạn !", "Xin chào, bạn!"),
    ("Đi đâu ?  Về nhà .", "Đi đâu? Về nhà."),
    ("Anh đi,em ở lại", "Anh đi, em ở lại"),
    ("Thật sao?Không thể!Đi nào", "Thật sao? Không thể! Đi nào"),
    ("Ghi chú:xem kỹ", "Ghi chú: xem kỹ"),
    ("Xong rồi.Tiếp theo nhé", "Xong rồi. Tiếp theo nhé"),
    ("Chờ chút...", "Chờ chút…"),
    ("Chờ chút ... rồi đi", "Chờ chút… rồi đi"),
    ("Hả.. thật sao", "Hả… thật sao"),
    ("Ừm……được", "Ừm… được"),
    ("Chờ chút…. rồi đi", "Chờ chút… rồi đi"),
    ("你好，世界。", "你好, 世界."),
    ('Anh nói "đi đi" rồi "về"', "Anh nói “đi đi” rồi “về”"),
    ("Cái này là là của của tôi", "Cái này là của tôi"),
    ("Ở ở đây và và kia", "Ở đây và kia"),
    ("  mở đầu  ", "mở đầu"),
    ("Dòng một  .\nDòng hai ,ok", "Dòng một.\nDòng hai, ok"),
])
def test_fixes(src, out):
    assert vi_lint.fix_text(src)[0] == out


@pytest.mark.parametrize("text", [
    "Chào mọi người, hôm nay trời đẹp quá!",
    "Lúc 13:05 giá 1,5 triệu hoặc 2.500.000đ",
    "TP.HCM, v.v. và www.google.com/a.Bc hay a@b.Com",
    "Xem https://example.com/x?y=1,z để biết",
    "Đi từ từ thôi, mãi mãi, dần dần, được được",          # real reduplication stays
    'Anh nói "đi',                                         # odd quotes: left alone
    "Rồi,",                                                # a lone trailing comma stays
    "3.5 điểm và ok.",
    "",
])
def test_things_that_must_not_change(text):
    assert vi_lint.fix_text(text) == (text, [])


def test_fixes_report_which_rules_fired_in_a_stable_order():
    out, fired = vi_lint.fix_text("Anh  đi ,em ở...")
    assert out == "Anh đi, em ở…"
    assert fired == ["ellipsis", "space_before", "space_after", "double_space"]


def test_fix_is_idempotent():
    once = vi_lint.fix_text('A  ,b ... "c" là là d.E')[0]
    assert vi_lint.fix_text(once) == (once, [])


def test_fix_cues_counts_lines_per_rule():
    cues = [Cue(1, 0, 1, "a  b"), Cue(2, 1, 2, "ok"), Cue(3, 2, 3, "c ,d  e")]
    counts = vi_lint.fix_cues(cues)
    assert counts == {"space_before": 1, "space_after": 1, "double_space": 2}
    assert [c.text for c in cues] == ["a b", "ok", "c, d e"]
    assert "dấu cách đôi (2)" in vi_lint.describe(counts)


# --- flags -------------------------------------------------------------------------------------

@pytest.mark.parametrize("word,ok", [
    ("lan", True), ("minh", True), ("dong", True), ("nguyen", True), ("quang", True), ("gia", True),
    ("zhang", False), ("xiao", False), ("shuo", False), ("wang", False), ("qing", False),
    ("jing", False), ("hello", False), ("and", False),
])
def test_is_vietnamese_syllable(word, ok):
    assert vi_lint.is_vietnamese_syllable(word) is ok


def test_latin_words_skips_vietnamese_acronyms_brands_glossary_and_allowlist():
    text = "Zhang Wei nói với AI về iPhone, ok, Tesla và Lan ở Việt Nam, xem YouTube"
    assert vi_lint.latin_words(text, ["Tesla"]) == ["Zhang", "Wei"]


def test_flags_han_num_latin():
    assert vi_lint.flags("Anh nói 你好 rồi") == ["han"]
    assert vi_lint.flags("Anh gặp Zhang ở đó") == ["latin"]
    assert vi_lint.flags("Anh ấy mua 3 cái", "他买了3个") == []
    assert vi_lint.flags("Anh ấy mua cái này", "他买了3个") == ["num"]
    assert vi_lint.flags("Tăng mười lăm phần trăm", "增长15%") == []         # spelled out
    assert vi_lint.flags("Năm hai nghìn không trăm hai mươi sáu", "2026年") == []
    assert vi_lint.flags("Giá 1,5 triệu", "价格1.5百万") == []                # same digits, other mark
    assert vi_lint.flags("", "你好") == []


def test_numbers_missing_lists_the_tokens():
    assert vi_lint.numbers_missing("他10岁，她8岁", "Cậu ấy mười tuổi") == ["8"]
    assert vi_lint.numbers_missing("第１集", "Tập 1") == []                  # fullwidth digit


def test_glossary_term_flag_is_live():
    gl = {"林默": "Lâm Mặc", "校车": "xe buýt"}
    assert vi_lint.flags("Lâm Mặc lên xe buýt", "林默上了校车", glossary=gl) == []
    assert vi_lint.flags("Anh ấy lên xe", "林默上了校车", glossary=gl) == ["term"]
    assert vi_lint.glossary_missing("林默上了校车", "lâm mặc lên xe", gl) == ["校车"]    # case-insensitive
    assert vi_lint.flags("Anh ấy lên xe", "", glossary=gl) == []             # no Chinese → no check


def test_forbidden_pronouns_only_from_an_explicit_ban():
    notes = ["Phim học đường. Không dùng: tao, mày. Tránh dùng từ lóng.", "Nhân vật chính xưng anh"]
    assert vi_lint.forbidden_pronouns(*notes) == {"tao", "mày"}
    assert vi_lint.forbidden_pronouns("xưng anh gọi em", "cấm dùng ông bà / ngươi") == \
        {"ông", "bà", "ngươi"}
    assert vi_lint.forbidden_pronouns("tránh dùng từ lóng", "") == frozenset()
    assert vi_lint.flags("Mày đi đi", forbidden=frozenset({"mày"})) == ["pron"]
    assert vi_lint.flags("Mày", forbidden=frozenset()) == []
    assert vi_lint.flags("Cái mảy", forbidden=frozenset({"mày"})) == []        # whole word only


# --- cue_issues integration ---------------------------------------------------------------------

class Ed:
    def __init__(self, start, end, vi, zh=""):
        self.start, self.end, self.vi, self.zh = start, end, vi, zh


def test_review_flags_come_only_with_lint_and_sort_in_order():
    cues = [Ed(0, 3, "Anh nói 你好 với Zhang", "他说你好")]
    assert review.cue_issues(cues) == [[]]
    got = review.cue_issues(cues, lint={"glossary": {}, "forbidden": frozenset()})
    assert got == [["han", "latin"]]
    assert all(k in review.LABELS for k in ("han", "num", "pron", "latin"))
    assert list(review.ORDER).index("han") < list(review.ORDER).index("long")


def test_live_glossary_replaces_the_stale_report():
    cues = [Ed(0, 3, "Anh ấy lên xe", "林默上了校车"), Ed(3, 6, "Lâm Mặc lên xe buýt", "林默上了校车")]
    spans = [review.Span(3.0, 6.0)]                 # the translate-time report pointed at line 2
    lint = {"glossary": {"校车": "xe buýt"}, "forbidden": frozenset()}
    assert review.cue_issues(cues, term_spans=spans) == [[], ["term"]]               # old behaviour
    assert review.cue_issues(cues, term_spans=spans, lint=lint) == [["term"], []]    # live truth
    plain = [Ed(0, 3, "Anh ấy lên xe", ""), Ed(3, 6, "x", "")]                       # no Chinese
    assert review.cue_issues(plain, term_spans=[review.Span(3.0, 6.0)], lint=lint) == [[], ["term"]]


# --- context (glossary + series ban) ---------------------------------------------------------------

@pytest.fixture()
def storage(tmp_path, monkeypatch):
    st = tmp_path / "storage"
    st.mkdir()
    monkeypatch.setattr(settings, "vizsup_storage_dir", st)
    return st


def test_context_reads_glossary_and_the_series_ban_and_refreshes(storage):
    job = Job(id="e", work_dir=storage / "e", metadata={"title": "《末日校车》06 x"})
    p = series.resolve(job)
    p.brief = "Phim học đường. Không dùng: tao, mày."
    p.glossary = {"校车": "xe buýt"}
    series.save(p)
    ctx = vi_lint.context(job)
    assert ctx["glossary"] == {"校车": "xe buýt"} and ctx["forbidden"] == {"tao", "mày"}
    assert vi_lint.context(job) is ctx                                  # cached while files are unchanged
    (job.work_dir / "glossary.json").write_text(json.dumps({"林默": "Lâm Mặc"}), encoding="utf-8")
    assert vi_lint.context(job)["glossary"]["林默"] == "Lâm Mặc"          # a new file invalidates it
    p.brief = "Không ban gì cả."
    series.save(p)
    assert vi_lint.context(job)["forbidden"] == frozenset()


def test_context_without_a_series(storage):
    job = Job(id="solo", work_dir=storage / "solo")
    assert vi_lint.context(job) == {"glossary": {}, "forbidden": frozenset()}


# --- the pipeline step ------------------------------------------------------------------------------

def test_fix_job_rewrites_vi_srt_and_its_machine_copy(storage):
    job = Job(id="j", work_dir=storage / "j")
    cues = [Cue(1, 0, 2, "Xin  chào ,bạn"), Cue(2, 2, 4, "Ổn rồi")]
    write_srt(cues, job.vi_srt)
    write_srt(cues, job.work_dir / "vi.mt.srt")
    logs: list[str] = []
    counts = vi_lint.fix_job(job, log=logs.append)
    assert counts and parse_srt(job.vi_srt)[0].text == "Xin chào, bạn"
    assert parse_srt(job.work_dir / "vi.mt.srt")[0].text == "Xin chào, bạn"   # not the user's "edit"
    assert "đã sửa" in logs[-1]
    mtime = job.vi_srt.stat().st_mtime_ns
    assert vi_lint.fix_job(job, log=logs.append) == {} and "không có lỗi" in logs[-1]
    assert job.vi_srt.stat().st_mtime_ns == mtime                              # nothing rewritten
    assert vi_lint.fix_job(Job(id="none", work_dir=storage / "none")) == {}


pytest.importorskip("PySide6.QtWidgets")


def test_worker_step_failure_is_logged_not_raised(storage, monkeypatch):
    from desktop.worker import PipelineWorker

    def boom(*_a, **_k):
        raise RuntimeError("disk")

    monkeypatch.setattr(vi_lint, "fix_job", boom)
    w = PipelineWorker(Job(id="w", work_dir=storage / "w"), {})
    logs: list[str] = []
    w.log.connect(logs.append)
    w._vi_lint(w.job)
    assert logs and logs[-1].startswith("⚠ Bỏ qua bước soát lỗi tiếng Việt")


def test_the_option_is_remembered_and_listed():
    from desktop import store

    assert "vi_lint" in store._UI_KEYS


# --- editor ------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def view(qapp, storage):
    from desktop.cuemodel import EditorCue
    from desktop.views import editor_view

    v = editor_view.EditorView()
    v.job = Job(id="ed", work_dir=storage / "ed")
    v.cues = [EditorCue(0, 2, "Xin  chào ,bạn", "你好"), EditorCue(2, 4, "Ổn rồi", "好"),
              EditorCue(4, 6, "Anh gặp Zhang", "他遇见张")]
    v._reload(dirty=False)
    return v


def test_editor_action_fixes_everything_as_one_undo_step_and_flags_the_rest(view):
    msgs: list[str] = []
    view.message.connect(lambda m, _k: msgs.append(m))
    depth = len(view._undo_stack)
    view._vi_lint()
    assert [c.vi for c in view.cues] == ["Xin chào, bạn", "Ổn rồi", "Anh gặp Zhang"]
    assert len(view._undo_stack) == depth + 1
    assert "Đã sửa" in msgs[-1] and "1 câu cần xem tay" in msgs[-1]
    assert "latin" in view.issues[2]
    view._undo_action()
    assert view.cues[0].vi == "Xin  chào ,bạn"


def test_editor_action_with_nothing_to_fix_keeps_undo_and_still_refreshes(view):
    view.cues[0].vi = "Xin chào, bạn"
    view._reload(dirty=False)
    depth = len(view._undo_stack)
    msgs: list[str] = []
    view.message.connect(lambda m, _k: msgs.append(m))
    view._vi_lint()
    assert len(view._undo_stack) == depth and "Không có lỗi trình bày" in msgs[-1]


def test_cue_list_menu_has_the_action(view):
    got: list[bool] = []
    view.cue_list.viLintRequested.connect(lambda: got.append(True))
    acts = [a for a in view.cue_list.more_btn.menu().actions() if a.text() == "Soát lỗi tiếng Việt"]
    assert acts
    acts[0].trigger()
    assert got == [True]


def test_start_screen_option(qapp, storage):
    from desktop.views import input_view
    from app.providers.registry import list_providers

    v = input_view.InputView(list_providers())
    assert v.vi_lint.isChecked() and v._collect_opts()["vi_lint"] is True
    v.vi_lint.setChecked(False)
    assert v._collect_opts()["vi_lint"] is False


def test_latin_skips_english_that_is_in_the_source_line_too():
    assert vi_lint.flags("Ngoại hình check", "外貌 check") == []
    assert vi_lint.flags("Can we go home", "Can we go home") == []
    assert vi_lint.flags("Ngoại hình check", "外貌") == ["latin"]            # not in the source
    assert vi_lint.flags("Anh gặp Zhang", "他遇见Zhang") == []              # case-insensitive match
    assert vi_lint.flags("Anh gặp zhang", "他遇见Zhang") == []


def test_library_summary_counts_lint_flags(storage, monkeypatch):
    from desktop import store

    job = Job(id="abcdef123456", work_dir=storage / "abcdef123456")
    write_srt([Cue(1, 0, 3, "Anh nói 你好")], job.vi_srt)
    write_srt([Cue(1, 0, 3, "他说你好")], job.cn_srt)
    store.save_job(job, {}, "await_edit")
    assert store.project_summary(job.id)["pending"] == 1             # "han" flag
    write_srt([Cue(1, 0, 3, "Anh nói xin chào")], job.vi_srt)
    assert store.project_summary(job.id)["pending"] == 0
