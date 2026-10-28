"""Spoken text for the dub (pipeline/speak.py): Vietnamese number reading, units, dates,
abbreviations, the user's pronounce.json and its place in the TTS cache key. No network."""
from __future__ import annotations

import json

import pytest

from app.config import settings
from app.models import Job
from app.pipeline import speak
from app.pipeline import tts as tts_mod
from app.pipeline.speak import read_number, spoken
from app.pipeline.tts import _seg_hash, synthesize


@pytest.mark.parametrize("n,words", [
    (0, "không"), (5, "năm"), (10, "mười"), (11, "mười một"), (15, "mười lăm"),
    (21, "hai mươi mốt"), (24, "hai mươi bốn"), (25, "hai mươi lăm"), (100, "một trăm"),
    (105, "một trăm lẻ năm"), (110, "một trăm mười"), (1000, "một nghìn"),
    (2026, "hai nghìn không trăm hai mươi sáu"),
    (1_000_005, "một triệu không trăm lẻ năm"),
    (3_400_000, "ba triệu bốn trăm nghìn"),
    (2_000_000_000, "hai tỷ"),
    (-5, "âm năm"),
])
def test_read_number(n, words):
    assert read_number(n) == words


def test_huge_number_is_read_digit_by_digit():
    assert read_number(10**16).startswith("một không không")


@pytest.mark.parametrize("text,out", [
    ("Năm 2026", "Năm hai nghìn không trăm hai mươi sáu"),
    ("1,5", "một phẩy năm"),
    ("0,05", "không phẩy không năm"),
    ("3.5 điểm", "ba phẩy năm điểm"),
    ("1.000.000 đồng", "một triệu đồng"),
    ("1.234,56", "một nghìn hai trăm ba mươi bốn phẩy năm sáu"),
    ("15%", "mười lăm phần trăm"),
    ("12,5%", "mười hai phẩy năm phần trăm"),
    ("từ 5-10 người", "từ năm đến mười người"),
    ("-5 độ", "âm năm độ"),
    ("0912345678", "không chín một hai ba bốn năm sáu bảy tám"),     # a phone number
    ("192.168.1.1", "một trăm chín mươi hai chấm một trăm sáu mươi tám chấm một chấm một"),
    ("10k người", "mười nghìn người"),
    ("2tr", "hai triệu"),
])
def test_numbers(text, out):
    assert spoken(text) == out


@pytest.mark.parametrize("text,out", [
    ("thứ 2", "thứ hai"), ("thứ 1", "thứ nhất"), ("thứ 4", "thứ tư"), ("thứ 21", "thứ hai mươi mốt"),
    ("tháng 4", "tháng tư"), ("tháng 12", "tháng mười hai"),
    ("lúc 13:05", "lúc mười ba giờ lẻ năm phút"),
    ("lúc 9:30", "lúc chín giờ ba mươi phút"),
    ("lúc 17:00", "lúc mười bảy giờ"),
    ("9h30", "chín giờ ba mươi"),
    ("12/10", "ngày mười hai tháng mười"),
    ("ngày 12/10", "ngày mười hai tháng mười"),            # no doubled "ngày"
    ("12/10/2025", "ngày mười hai tháng mười năm hai nghìn không trăm hai mươi lăm"),
    ("2025-10-12", "ngày mười hai tháng mười năm hai nghìn không trăm hai mươi lăm"),
    ("1/2 chai", "một phần hai chai"),
    ("15/20", "mười lăm phần hai mươi"),
    ("24/7", "hai mươi bốn trên bảy"),
])
def test_times_dates_ordinals(text, out):
    assert spoken(text) == out


@pytest.mark.parametrize("text,out", [
    ("50.000đ", "năm mươi nghìn đồng"),
    ("$5", "năm đô la"), ("$5 triệu", "năm triệu đô la"), ("¥20", "hai mươi tệ"),
    ("20元", "hai mươi tệ"), ("100 USD", "một trăm đô la Mỹ"),
    ("5km", "năm ki lô mét"), ("5 km", "năm ki lô mét"), ("70kg", "bảy mươi ki lô gam"),
    ("36°C", "ba mươi sáu độ xê"), ("50m²", "năm mươi mét vuông"), ("50m2", "năm mươi mét vuông"),
    ("60km/h", "sáu mươi ki lô mét trên giờ"), ("8GB", "tám gi ga bai"),
])
def test_money_and_units(text, out):
    assert spoken(text) == out


@pytest.mark.parametrize("text,out", [
    ("TP.HCM", "thành phố Hồ Chí Minh"), ("VN", "Việt Nam"), ("AI", "ây ai"),
    ("CEO của công ty", "xi i âu của công ty"), ("OK", "ô kê"), ("ok", "ô kê"),
    ("mạng 5G", "mạng năm gi"), ("ai biết", "ai biết"),       # the word "ai" is untouched
    ("iPhone", "iPhone"), ("A4", "A4"), ("COVID-19", "COVID-mười chín"),   # Latin words stay, the number is read
])
def test_abbreviations_and_latin(text, out):
    assert spoken(text) == out


@pytest.mark.parametrize("text,out", [
    ("Anh... em", "Anh, em"), ("Chờ đã…", "Chờ đã"), ("...Thật sao", "Thật sao"),
    ("Sao?...", "Sao?"), ("Cái gì?!?!", "Cái gì?"), ("Trời ơi!!!", "Trời ơi!"),
    ("Hay lắm 😂👍", "Hay lắm"), ("♪ la la ♪", "la la"), ("Đi — nhanh", "Đi, nhanh"),
    ("Tom & Jerry", "Tom và Jerry"), ("🙂", ""),
])
def test_punctuation_and_symbols(text, out):
    assert spoken(text) == out


def test_nothing_to_change_is_identity():
    for t in ("Chào mọi người, hôm nay trời đẹp quá!", "Em không biết."):
        assert spoken(t) == t


# --- the user's dictionary ----------------------------------------------------------------

def test_dictionary_whole_word_case_insensitive():
    rules = {"Lục Trầm": "Lục Trầm Vương", "phở": "phở bò"}
    assert spoken("lục trầm ăn PHỞ", rules) == "Lục Trầm Vương ăn phở bò"
    assert spoken("phởi", rules) == "phởi"                # whole word only


def test_all_caps_key_matches_only_its_spelling():
    """"AI" must not turn the Vietnamese word "ai" into letters."""
    assert spoken("AI và ai", {"AI": "a i"}) == "a i và ai"


def test_dictionary_beats_builtin_and_longest_key_wins():
    assert spoken("AI", {"AI": "ây-ai"}) == "ây-ai"
    assert spoken("Hà Nội mới", {"Hà": "ha", "Hà Nội": "Hà Nội thủ đô"}) == "Hà Nội thủ đô mới"


def test_dictionary_value_gets_numbers_read_too():
    assert spoken("xe X", {"X": "số 7"}) == "xe số bảy"


def test_empty_value_silences_a_word():
    assert spoken("à thì ừm đi", {"ừm": ""}) == "à thì đi"


def test_merge_project_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path / "st")
    (tmp_path / "st").mkdir()
    (tmp_path / "st" / speak.FILE).write_text(
        json.dumps({"Tiểu Hắc": "tiểu hắc", "boss": "sếp"}), encoding="utf-8")
    job = Job(id="p", work_dir=tmp_path / "job")
    (job.work_dir / speak.FILE).write_text(json.dumps({"tiểu hắc": "Tiểu Hắc Hắc"}), encoding="utf-8")
    rules = speak.load_rules(job)
    assert rules == {"tiểu hắc": "Tiểu Hắc Hắc", "boss": "sếp"}     # shadowed by case-folded key
    assert spoken("Tiểu Hắc và boss", rules) == "Tiểu Hắc Hắc và sếp"


def test_broken_dictionary_files_are_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    (tmp_path / speak.FILE).write_text("{not json", encoding="utf-8")
    job = Job(id="p", work_dir=tmp_path / "job")
    (job.work_dir / speak.FILE).write_text("[1, 2]", encoding="utf-8")
    assert speak.load_rules(job) == {}


def test_save_rules_round_trip_and_empty_removes(tmp_path):
    p = tmp_path / speak.FILE
    speak.save_rules(p, {"b": "2", "a": "1", " ": "x"})
    assert json.loads(p.read_text(encoding="utf-8")) == {"a": "1", "b": "2"}
    speak.save_rules(p, {})
    assert not p.exists()


# --- wired into the TTS stage --------------------------------------------------------------

VI = ("1\n00:00:00,000 --> 00:00:01,000\ntăng 15%\n\n"
      "2\n00:00:01,000 --> 00:00:02,000\ncâu hai\n\n"
      "3\n00:00:02,000 --> 00:00:03,000\ngặp Lục Trầm\n")


class FakeTTS:
    def __init__(self):
        self.calls: list[str] = []

    def available(self):
        return True

    def synthesize(self, text, out_path, *, voice=None, rate=None):
        self.calls.append(text)
        out_path.write_bytes(b"mp3" + text.encode())
        return out_path


@pytest.fixture()
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path / "storage")
    (tmp_path / "storage").mkdir()
    j = Job(id="t", work_dir=tmp_path / "job")
    j.vi_srt.write_text(VI, encoding="utf-8")
    return j


def _use(monkeypatch):
    fake = FakeTTS()
    monkeypatch.setattr(tts_mod, "get_tts", lambda name: fake)
    return fake


def test_voice_gets_spoken_text_and_subtitles_keep_written(job, monkeypatch):
    fake = _use(monkeypatch)
    synthesize(job, provider="fake")
    assert fake.calls == ["tăng mười lăm phần trăm", "câu hai", "gặp Lục Trầm"]
    assert "tăng 15%" in job.vi_srt.read_text(encoding="utf-8")        # vi.srt untouched


def test_dictionary_edit_revoices_only_changed_lines(job, monkeypatch):
    fake = _use(monkeypatch)
    synthesize(job, provider="fake")
    fake.calls.clear()
    synthesize(job, provider="fake")
    assert fake.calls == []                                            # cached
    (job.work_dir / speak.FILE).write_text(json.dumps({"Lục Trầm": "Lục Trầm Vương"}),
                                           encoding="utf-8")
    synthesize(job, provider="fake")
    assert fake.calls == ["gặp Lục Trầm Vương"]                        # one line, not three


def test_global_dictionary_applies_and_project_overrides(job, monkeypatch, tmp_path):
    fake = _use(monkeypatch)
    (tmp_path / "storage" / speak.FILE).write_text(json.dumps({"câu hai": "câu thứ hai"}),
                                                   encoding="utf-8")
    synthesize(job, provider="fake")
    assert "câu thứ hai" in fake.calls
    (job.work_dir / speak.FILE).write_text(json.dumps({"câu hai": "câu số hai"}), encoding="utf-8")
    fake.calls.clear()
    synthesize(job, provider="fake")
    assert fake.calls == ["câu số hai"]


def test_cache_key_is_the_spoken_text():
    assert _seg_hash("p", "v", None, "mười lăm phần trăm") != _seg_hash("p", "v", None, "15%")
    assert _seg_hash("p", "v", None, "x") == _seg_hash("p", "v", None, "x")


def test_line_that_speaks_as_nothing_is_skipped(job, monkeypatch):
    fake = _use(monkeypatch)
    job.vi_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n😂😂\n\n"
                          "2\n00:00:01,000 --> 00:00:02,000\ncâu hai\n", encoding="utf-8")
    segs = synthesize(job, provider="fake")
    assert fake.calls == ["câu hai"]
    assert [c.index for c, _ in segs] == [2]
