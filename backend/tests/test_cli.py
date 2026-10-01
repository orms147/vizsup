"""CLI: argument wiring, artifact skipping, subtitle-source routing, batch mode.

`cli.py` is the headless entry point and had zero tests, yet it owns rules that
cost real money or real edits when they break: never re-run a stage whose artifact
exists, never silently overwrite a hand-edited vi.srt, and never let one bad URL
kill a batch. Every stage is faked — no network, no ffmpeg, no models.
"""
from __future__ import annotations

import hashlib

import pytest

from app import cli
from app.pipeline import hardsub_detect


@pytest.fixture()
def stages(monkeypatch, tmp_path):
    """Fake every pipeline stage; record what got called."""
    called: dict[str, int] = {}

    def counter(name, effect=None):
        def fn(*a, **kw):
            called[name] = called.get(name, 0) + 1
            if effect:
                effect(*a, **kw)
        return fn

    def dl(job):
        job.source_video.write_bytes(b"v")
        job.metadata["title"] = "tiêu đề"
        job.metadata["platform"] = "bilibili"

    def asr_(job, **kw):
        job.cn_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8")

    def ocr_(job, **kw):
        job.cn_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nOCR文字\n", encoding="utf-8")

    def tr(job, **kw):
        job.vi_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nxin chào\n", encoding="utf-8")

    monkeypatch.setattr(cli.download, "download", counter("download", dl))
    monkeypatch.setattr(cli.asr, "transcribe", counter("asr", asr_))
    monkeypatch.setattr(cli.translate, "translate", counter("translate", tr))
    monkeypatch.setattr(cli.settings, "vizsup_storage_dir", tmp_path / "storage")

    import app.pipeline.ocr as ocr_mod
    monkeypatch.setattr(ocr_mod, "extract_hardsubs", counter("ocr", ocr_))
    return called


def run(*argv) -> int:
    return cli.main(list(argv))


# --- happy path ---------------------------------------------------------------

def test_run_stops_at_the_edit_gate(stages, tmp_path, capsys):
    wd = tmp_path / "job"
    rc = run("run", "https://b23.tv/x", "--workdir", str(wd), "--source", "asr")
    assert rc == 0
    assert (wd / "vi.srt").exists()
    assert not (wd / "output.mp4").exists()        # the gate: nothing rendered
    assert "EDIT GATE" in capsys.readouterr().out


def test_metadata_is_persisted_for_resume(stages, tmp_path):
    wd = tmp_path / "job"
    run("run", "https://b23.tv/x", "--workdir", str(wd), "--source", "asr")
    assert "tiêu đề" in (wd / "metadata.json").read_text(encoding="utf-8")


def test_workdir_defaults_to_a_stable_hash_of_the_url(stages, tmp_path):
    """Same URL -> same dir, which is what makes a bare re-run resume."""
    url = "https://b23.tv/x"
    run("run", url, "--source", "asr")
    jid = hashlib.sha1(url.encode()).hexdigest()[:10]
    assert (tmp_path / "storage" / jid / "vi.srt").exists()


# --- artifact skipping --------------------------------------------------------

def test_existing_source_skips_download(stages, tmp_path):
    wd = tmp_path / "job"
    wd.mkdir()
    (wd / "source.mp4").write_bytes(b"already here")
    run("run", "https://b23.tv/x", "--workdir", str(wd), "--source", "asr")
    assert "download" not in stages
    assert stages["asr"] == 1


def test_existing_cn_srt_skips_the_subtitle_stage(stages, tmp_path):
    wd = tmp_path / "job"
    wd.mkdir()
    (wd / "cn.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8")
    run("run", "https://b23.tv/x", "--workdir", str(wd), "--source", "asr")
    assert "asr" not in stages
    assert stages["translate"] == 1


def test_existing_vi_srt_is_never_silently_retranslated(stages, tmp_path):
    """vi.srt is the file the human edits at the gate — the hard invariant."""
    wd = tmp_path / "job"
    wd.mkdir()
    (wd / "vi.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nBẢN SỬA TAY\n", encoding="utf-8")
    run("run", "https://b23.tv/x", "--workdir", str(wd), "--source", "asr")
    assert "translate" not in stages
    assert "BẢN SỬA TAY" in (wd / "vi.srt").read_text(encoding="utf-8")


def test_force_redoes_every_stage(stages, tmp_path):
    wd = tmp_path / "job"
    wd.mkdir()
    (wd / "source.mp4").write_bytes(b"v")
    (wd / "cn.srt").write_text("x", encoding="utf-8")
    (wd / "vi.srt").write_text("x", encoding="utf-8")
    run("run", "https://b23.tv/x", "--workdir", str(wd), "--source", "asr", "--force")
    assert stages["download"] == 1
    assert stages["asr"] == 1
    assert stages["translate"] == 1


# --- subtitle-source routing --------------------------------------------------

def test_auto_detects_hardsubs_and_routes_to_ocr(stages, tmp_path, monkeypatch):
    monkeypatch.setattr(hardsub_detect, "detect_hardsubs", lambda job, **kw: True)
    wd = tmp_path / "job"
    run("run", "https://b23.tv/x", "--workdir", str(wd), "--source", "auto")
    assert stages.get("ocr") == 1
    assert "asr" not in stages


def test_auto_without_hardsubs_routes_to_asr(stages, tmp_path, monkeypatch):
    monkeypatch.setattr(hardsub_detect, "detect_hardsubs", lambda job, **kw: False)
    wd = tmp_path / "job"
    run("run", "https://b23.tv/x", "--workdir", str(wd), "--source", "auto")
    assert stages.get("asr") == 1
    assert "ocr" not in stages


def test_auto_degrades_to_asr_when_detection_blows_up(stages, tmp_path, monkeypatch, capsys):
    """Missing OCR deps must not abort the run."""
    def boom(job, **kw):
        raise ImportError("no rapidocr")

    monkeypatch.setattr(hardsub_detect, "detect_hardsubs", boom)
    wd = tmp_path / "job"
    rc = run("run", "https://b23.tv/x", "--workdir", str(wd), "--source", "auto")
    assert rc == 0
    assert stages.get("asr") == 1
    assert "detection unavailable" in capsys.readouterr().out


def test_auto_persists_the_hardsub_verdict(stages, tmp_path, monkeypatch):
    """metadata.json is written BEFORE detection, so the verdict needs a re-save."""
    monkeypatch.setattr(hardsub_detect, "detect_hardsubs", lambda job, **kw: True)
    wd = tmp_path / "job"
    run("run", "https://b23.tv/x", "--workdir", str(wd), "--source", "auto")
    assert "has_hardsubs" in (wd / "metadata.json").read_text(encoding="utf-8")


def test_provided_srt_wins_over_source_and_clears_ocr_provenance(stages, tmp_path):
    """A stale ocr_report.json would wrongly add the OCR-tolerance hint (API-7)."""
    wd = tmp_path / "job"
    wd.mkdir()
    (wd / "ocr_report.json").write_text("{}", encoding="utf-8")
    cn = tmp_path / "given.srt"
    cn.write_text("1\n00:00:00,000 --> 00:00:02,000\n给定字幕\n", encoding="utf-8")

    run("run", "https://b23.tv/x", "--workdir", str(wd), "--srt", str(cn))
    assert "asr" not in stages and "ocr" not in stages
    assert "给定字幕" in (wd / "cn.srt").read_text(encoding="utf-8")
    assert not (wd / "ocr_report.json").exists()


def test_local_file_is_used_instead_of_downloading(stages, tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"local")
    wd = tmp_path / "job"
    run("run", "local://clip", "--file", str(src), "--workdir", str(wd), "--source", "asr")
    assert "download" not in stages
    assert (wd / "source.mp4").read_bytes() == b"local"


# --- batch mode ---------------------------------------------------------------

def test_batch_runs_every_url(stages, tmp_path):
    rc = run("run", "https://b23.tv/a", "https://b23.tv/b", "--source", "asr")
    assert rc == 0
    assert stages["translate"] == 2


def test_one_bad_url_does_not_kill_the_batch(stages, tmp_path, monkeypatch, capsys):
    calls = {"n": 0}
    real = cli.download.download

    def flaky(job):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("404 riêng URL này")
        return real(job)

    monkeypatch.setattr(cli.download, "download", flaky)
    rc = run("run", "https://b23.tv/a", "https://b23.tv/b", "--source", "asr")
    assert rc == 1                      # surfaced...
    assert stages["translate"] == 1     # ...but the second URL still ran
    assert "LỖI với" in capsys.readouterr().err


def test_batch_rejects_single_job_flags(stages, tmp_path, capsys):
    rc = run("run", "https://b23.tv/a", "https://b23.tv/b", "--workdir", str(tmp_path / "j"))
    assert rc == 2
    assert "MỘT URL" in capsys.readouterr().err


# --- render -------------------------------------------------------------------

@pytest.fixture()
def render_stages(monkeypatch):
    called: dict[str, dict] = {}

    def fake_tts(job, **kw):
        called["tts"] = kw
        return []

    def fake_assemble(job, segments, **kw):
        called["assemble"] = kw
        job.output_video.write_bytes(b"o")
        return job.output_video

    def fake_separate(job, **kw):
        called["separate"] = kw

    monkeypatch.setattr(cli.tts, "synthesize", fake_tts)
    monkeypatch.setattr(cli.assemble, "assemble", fake_assemble)
    import app.pipeline.separate as sep
    monkeypatch.setattr(sep, "separate_background", fake_separate)
    return called


def _gated_job(tmp_path):
    wd = tmp_path / "job"
    wd.mkdir()
    (wd / "vi.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nxin chào\n", encoding="utf-8")
    return wd


def test_render_missing_workdir_exits_2(render_stages, tmp_path, capsys):
    assert run("render", "--workdir", str(tmp_path / "nope")) == 2
    assert "workdir not found" in capsys.readouterr().err


def test_render_without_vi_srt_exits_2(render_stages, tmp_path, capsys):
    wd = tmp_path / "job"
    wd.mkdir()
    assert run("render", "--workdir", str(wd)) == 2
    assert "no vi.srt" in capsys.readouterr().err


def test_render_produces_output(render_stages, tmp_path):
    wd = _gated_job(tmp_path)
    assert run("render", "--workdir", str(wd)) == 0
    assert (wd / "output.mp4").exists()


def test_keep_audio_maps_to_mix(render_stages, tmp_path):
    run("render", "--workdir", str(_gated_job(tmp_path)), "--keep-audio")
    assert render_stages["assemble"]["audio_mode"] == "mix"


def test_audio_mode_flag_wins_over_keep_audio(render_stages, tmp_path):
    run("render", "--workdir", str(_gated_job(tmp_path)), "--keep-audio", "--audio-mode", "duck")
    assert render_stages["assemble"]["audio_mode"] == "duck"


def test_keep_music_separates_before_tts(render_stages, tmp_path):
    """Demucs and TTS are both heavy — separation must happen first, not concurrently."""
    run("render", "--workdir", str(_gated_job(tmp_path)), "--audio-mode", "keep-music")
    assert "separate" in render_stages
    assert render_stages["assemble"]["audio_mode"] == "keep_music"


def test_keep_music_degrades_when_demucs_fails(render_stages, tmp_path, monkeypatch, capsys):
    import app.pipeline.separate as sep

    def boom(job, **kw):
        raise RuntimeError("demucs missing")

    monkeypatch.setattr(sep, "separate_background", boom)
    wd = _gated_job(tmp_path)
    assert run("render", "--workdir", str(wd), "--audio-mode", "keep-music") == 0
    assert "keep-music unavailable" in capsys.readouterr().out
    assert (wd / "output.mp4").exists()


def test_cover_hardsubs_legacy_flag_still_reaches_assemble(render_stages, tmp_path):
    run("render", "--workdir", str(_gated_job(tmp_path)), "--cover-hardsubs")
    assert render_stages["assemble"]["cover_hardsubs"] is True


# --- per-video context brief --------------------------------------------------

@pytest.fixture()
def brief_calls(monkeypatch):
    from app.pipeline import brief

    calls: list[dict] = []

    def fake(job, provider, *, note=None, force=False, log=None):
        calls.append({"provider": provider, "note": note, "force": force})
        return "Xưng hô: mình / các bạn"

    monkeypatch.setattr(brief, "build_brief", fake)
    return calls


def test_context_note_builds_a_brief_with_that_note(stages, tmp_path, brief_calls, capsys):
    run("run", "https://b23.tv/x", "--workdir", str(tmp_path / "j"), "--source", "asr",
        "--translator", "openrouter", "--context", "vlog nấu ăn")
    assert brief_calls == [{"provider": "openrouter", "note": "vlog nấu ăn", "force": False}]
    assert "Xưng hô: mình" in capsys.readouterr().out


def test_auto_context_builds_a_brief_without_a_note(stages, tmp_path, brief_calls):
    run("run", "https://b23.tv/x", "--workdir", str(tmp_path / "j"), "--source", "asr",
        "--auto-context")
    assert brief_calls and brief_calls[0]["note"] is None


def test_no_flags_no_brief(stages, tmp_path, brief_calls):
    run("run", "https://b23.tv/x", "--workdir", str(tmp_path / "j"), "--source", "asr")
    assert brief_calls == []


def test_no_brief_when_a_kept_vi_srt_means_no_translation(stages, tmp_path, brief_calls):
    """A brief is only worth paying for when translate will actually run."""
    wd = tmp_path / "j"
    wd.mkdir()
    (wd / "vi.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nđã sửa\n", encoding="utf-8")
    run("run", "https://b23.tv/x", "--workdir", str(wd), "--source", "asr", "--auto-context")
    assert brief_calls == []
