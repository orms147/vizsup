"""watch.check_all / check_series: due series only, the per-check cap, dry looks record
nothing, failures are retried up to MAX_TRIES, one real check at a time — and the CLI
`autopilot --once` that runs new episodes to the edit gate with the owner's saved choices
(never approving, never rendering). Listing and the pipeline are faked."""
from __future__ import annotations

import json
import os
import time

import pytest

from app import cli
from app.config import settings
from app.pipeline import series, watch

SRC = "https://www.douyin.com/user/MS4wLjABAAAA-fjbtESaHwMcQ7_MYwdz6vhfPcWQbQ7lhVayouaq9ow"


def page(*eps, name="末日校车"):
    return [{"id": f"77{n:017d}", "url": f"https://www.douyin.com/video/77{n:017d}",
             "title": f"《{name}》{n:02d} tiêu đề", "published": 1_790_000_000 + n} for n in eps]


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    return tmp_path


@pytest.fixture()
def followed(storage):
    p = series.create("末日校车")
    return series.save_autopilot(p.slug, on=True, sources=[SRC], every_h=6, max_per_check=2)


def lister_of(videos, calls=None):
    def lister(src, limit=30):
        if calls is not None:
            calls.append(src)
        return list(videos)
    return lister


# --- check_series / check_all ------------------------------------------------------------------

def test_a_dry_look_lists_what_would_run_and_records_nothing(followed):
    found = watch.check_all(lister=lister_of(page(9, 7, 8)))
    assert [(f["episode"], f["status"]) for f in found] == [(7, "new"), (8, "new"), (9, "later")]
    assert not watch.state_path().exists()
    assert not (watch.state_path().parent / watch.LOCK).exists()


def test_a_real_check_runs_the_oldest_episodes_first_up_to_the_cap(followed):
    ran = []
    found = watch.check_all(run=lambda url, slug: ran.append((url, slug)) or f"dir{len(ran)}",
                            lister=lister_of(page(9, 7, 8)), now=1000.0)
    assert ran == [(page(7)[0]["url"], followed.slug), (page(8)[0]["url"], followed.slug)]
    assert [(f["episode"], f["status"]) for f in found] == [(7, "done"), (8, "done"), (9, "later")]
    assert found[0]["result"] == "dir1"
    st = watch.load_state()
    assert st["series"][followed.slug]["checked"] == 1000.0
    assert st["sources"][SRC] == {"at": 1000.0, "n": 3, "error": ""}
    assert st["last_run"]["items"] == found
    assert not (watch.state_path().parent / watch.LOCK).exists()
    # the next check (forced: not due yet) takes what was left
    again = watch.check_all(run=lambda url, slug: ran.append((url, slug)),
                            lister=lister_of(page(9, 7, 8)), force=True)
    assert [(f["episode"], f["status"]) for f in again] == [(9, "done")]


def test_not_due_is_skipped_unless_forced(followed):
    st = watch.load_state()
    watch.series_state(st, followed.slug)["checked"] = time.time() - 3600
    watch.save_state(st)
    logs, calls = [], []
    assert watch.check_all(run=lambda *a: None, lister=lister_of(page(9), calls),
                           log=logs.append) == []
    assert calls == [] and any("chưa tới giờ" in m for m in logs)
    # due a few minutes early is fine: the task fires every N h, the last check ended later
    assert watch.is_due(followed, st, now=time.time() - 3600 + 6 * 3600 - 300)
    assert not watch.is_due(followed, st, now=time.time() - 3600 + 6 * 3600 - 900)
    assert watch.check_all(run=lambda *a: None, lister=lister_of(page(9)), force=True)


def test_a_failed_episode_is_retried_then_given_up(followed):
    def boom(url, slug):
        raise RuntimeError("mất mạng")

    for i in range(watch.MAX_TRIES):
        found = watch.check_all(run=boom, lister=lister_of(page(9)), force=True)
        assert found[0]["status"] == "failed" and found[0]["tries"] == i + 1
        assert "mất mạng" in found[0]["error"]
    assert watch.check_all(run=boom, lister=lister_of(page(9)), force=True) == []
    assert "lần 3/3" in watch.describe({**found[0], "series": "末日校车"})


def test_only_followed_series_are_checked_and_an_episode_of_another_series_is_ignored(storage):
    a = series.create("末日校车")
    series.save_autopilot(a.slug, on=True, sources=[SRC])
    b = series.create("萧族降世")
    series.save_autopilot(b.slug, on=False, sources=[SRC])
    found = watch.check_all(lister=lister_of(page(3) + page(4, name="萧族降世")))
    assert [(f["series"], f["episode"]) for f in found] == [("末日校车", 3)]


def test_episodes_already_in_the_library_are_not_fetched_again(followed, storage):
    d = storage / "ep8"
    d.mkdir()
    (d / "vi.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nx\n", encoding="utf-8")
    (d / "series_link.json").write_text(json.dumps({"slug": followed.slug, "episode": 8,
                                                    "last": 8}), encoding="utf-8")
    found = watch.check_all(lister=lister_of(page(6, 7, 8, 9)))
    assert [f["episode"] for f in found] == [9]


def test_a_source_error_is_recorded_and_the_check_goes_on(followed):
    def lister(src, limit=30):
        raise RuntimeError("Douyin đòi xác minh (captcha) — thử lại sau.")

    logs = []
    assert watch.check_all(run=lambda *a: None, lister=lister, log=logs.append) == []
    st = watch.load_state()
    assert "captcha" in st["sources"][SRC]["error"] and any("captcha" in m for m in logs)
    assert st["series"][followed.slug]["checked"]


def test_one_real_check_at_a_time_and_a_stale_lock_is_taken_over(followed):
    lock = watch.state_path().parent / watch.LOCK
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("123", encoding="utf-8")
    logs = []
    assert watch.check_all(run=lambda *a: None, lister=lister_of(page(9)), log=logs.append) == []
    assert "đang chạy" in logs[-1]
    assert watch.check_all(lister=lister_of(page(9)))[0]["status"] == "new"   # a look still works
    old = time.time() - watch.LOCK_STALE_S - 10
    os.utime(lock, (old, old))
    assert watch.check_all(run=lambda *a: None, lister=lister_of(page(9)))[0]["status"] == "done"
    assert not lock.exists()


def test_nothing_followed_says_so(storage):
    logs = []
    assert watch.check_all(run=lambda *a: None, log=logs.append) == []
    assert "Chưa có bộ phim nào" in logs[0]


# --- the CLI: the owner's saved choices ------------------------------------------------------------

PROVIDERS = {
    "translation": [{"name": "passthrough", "available": True, "can_refine": False},
                    {"name": "openrouter", "available": True, "can_refine": True},
                    {"name": "deepseek", "available": False, "can_refine": True}],
    "asr": [{"name": "faster_whisper", "available": False}, {"name": "audio_llm", "available": True}],
    "ocr": [{"name": "rapidocr", "available": True}, {"name": "vision_llm", "available": False}],
    "tts": [{"name": "edge", "available": True}],
}


def test_autopilot_opts_take_the_saved_choices_with_the_start_screens_defaults():
    ui = {"translator": "openrouter", "refine": "openrouter", "asr": "audio_llm",
          "subtitle_source": "ocr", "ocr_engine": "vision_llm", "ocr_fast": True,
          "auto_context": False, "ai_review": False, "voice_guess": True, "ocr_region": "full"}
    o = cli.autopilot_opts(ui, PROVIDERS)
    assert (o["translator"], o["refine"], o["asr"]) == ("openrouter", "openrouter", "audio_llm")
    assert o["subtitle_source"] == "ocr" and o["ocr_region"] == "full"
    assert o["ocr_engine"] == "rapidocr"                       # vision_llm has no key: local OCR
    assert o["ocr_fast"] and not o["auto_context"] and not o["ai_review"] and o["voice_guess"]
    d = cli.autopilot_opts({"translator": "deepseek", "subtitle_source": "srt"}, PROVIDERS)
    assert d["translator"] == "passthrough"                    # the combo's first usable engine
    assert d["refine"] is None and d["asr"] == "audio_llm" and d["subtitle_source"] == "auto"
    assert d["auto_context"] and d["auto_snap"] and d["vi_lint"] and not d["voice_guess"]


def test_autopilot_args_map_to_run_flags(tmp_path):
    o = cli.autopilot_opts({"translator": "openrouter", "ocr_fast": True, "ai_review": False,
                            "timing_pass": False}, PROVIDERS)
    a = cli.autopilot_args("https://www.douyin.com/video/1", "末日校车", o, tmp_path / "w")
    assert a.url == ["https://www.douyin.com/video/1"] and a.series == "末日校车"
    assert a.translator == "openrouter" and a.source == "auto" and a.ocr_fps == 1.0
    assert a.no_qa and a.no_timing_pass and not a.no_snap and not a.no_vi_lint
    assert a.auto_context and a.workdir == str(tmp_path / "w") and not a.force


# --- the CLI: a real (faked) run ---------------------------------------------------------------------

@pytest.fixture()
def pipeline(followed, storage, monkeypatch):
    """`autopilot --once` with the listing, the providers and the pipeline faked; every
    render / approval entry point explodes if touched."""
    from app.pipeline import approval
    from app.providers import registry

    monkeypatch.setattr(watch, "list_videos", lister_of(page(7, 8, 9)))
    monkeypatch.setattr(registry, "list_providers", lambda: PROVIDERS)
    (storage / "ui_state.json").write_text(json.dumps({"translator": "openrouter",
                                                       "asr": "audio_llm"}), encoding="utf-8")
    runs = []

    def fake_run_one(args, url):
        runs.append(args)
        wd = cli.Path(args.workdir)
        (wd / "metadata.json").write_text(json.dumps({"title": f"t {url[-2:]}",
                                                      "platform": "douyin"}), encoding="utf-8")
        (wd / "vi.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nx\n", encoding="utf-8")
        return 0

    def forbidden(*a, **k):
        raise AssertionError("autopilot must never render or approve")

    monkeypatch.setattr(cli, "_run_one", fake_run_one)
    for mod, name in ((cli.tts, "synthesize"), (cli.assemble, "assemble"),
                      (approval, "approve")):
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name, forbidden)
    return runs


def test_autopilot_once_runs_new_episodes_to_the_gate(pipeline, storage, capsys):
    assert cli.main(["autopilot", "--once"]) == 0
    assert [a.url[0][-2:] for a in pipeline] == ["07", "08"]          # cap 2, oldest first
    a = pipeline[0]
    assert a.translator == "openrouter" and a.asr == "audio_llm" and a.series == followed_slug(storage)
    job = json.loads((cli.Path(a.workdir) / "job.json").read_text(encoding="utf-8"))
    assert job["stage"] == "await_edit" and job["opts"]["autopilot"] is True
    assert job["url"] == a.url[0] and job["title"] == "t 07" and job["platform"] == "douyin"
    assert cli.Path(a.workdir).name == cli.hashlib.sha1(a.url[0].encode()).hexdigest()[:10]
    out = capsys.readouterr().out
    assert "2 chờ duyệt, 0 lỗi" in out and "để lần kiểm tra sau" in out
    assert not list(storage.rglob("approved*.json"))


def followed_slug(storage) -> str:
    return series.followed()[0].slug


def test_autopilot_dry_run_downloads_nothing(pipeline, storage, capsys):
    assert cli.main(["autopilot", "--once", "--dry-run"]) == 0
    assert pipeline == [] and not watch.state_path().exists()
    assert "sẽ lấy" in capsys.readouterr().out


def test_autopilot_without_a_real_translator_fails_each_episode_and_retries_later(
        pipeline, storage, monkeypatch, capsys):
    from app.providers import registry

    only_pass = {**PROVIDERS, "translation": PROVIDERS["translation"][:1]}
    monkeypatch.setattr(registry, "list_providers", lambda: only_pass)
    assert cli.main(["autopilot", "--once"]) == 1
    assert pipeline == []
    assert "chưa có bộ dịch" in capsys.readouterr().out
    assert watch.seen_ids(watch.load_state(), followed_slug(storage)) == set()


def test_a_failing_pipeline_marks_the_episode_failed(pipeline, storage, monkeypatch, capsys):
    def broken(args, url):
        raise RuntimeError("yt-dlp 403")

    monkeypatch.setattr(cli, "_run_one", broken)
    assert cli.main(["autopilot", "--once"]) == 1
    st = watch.load_state()["series"][followed_slug(storage)]["seen"]
    assert {e["status"] for e in st.values()} == {"failed"}
    job = next(storage.glob("*/job.json"))
    assert json.loads(job.read_text(encoding="utf-8"))["stage"] == "queued"   # resumable in the app


def test_without_a_console_the_output_goes_to_a_log_file(storage, monkeypatch):
    import sys

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    cli._autopilot_console()
    try:
        print("hello log")
        sys.stdout.flush()
        log = storage / "_autopilot" / "autopilot.log"
        assert "hello log" in log.read_text(encoding="utf-8")
    finally:
        sys.stdout.close()
