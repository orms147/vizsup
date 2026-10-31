"""Render one APPROVED project: (Demucs) → TTS → commentary → assemble, with the settings the
render page sends (``opts``). The body of the desktop's render worker (desktop/worker.py) and
of the scheduled task's headless render — an episode approved from the Telegram bot while
the app is closed (app/pipeline/remote.py). UI-agnostic: callbacks instead of Qt signals.

The edit gate is enforced HERE, where the money is spent: TTS and the encode only run for
the exact revision the owner approved (app/pipeline/approval.py), whoever calls this.

Raises on failure; ``cancel()`` returning True stops between steps (InterruptedError), a
lost connection is the caller's to wait out (app.net.run_resilient — every step skips what
is already on disk).
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path

from app.fsutil import atomic_write_text
from app.models import Job

# edge-tts refuses at random some nights; the lines already made are kept, so waiting and
# going on finishes the dub without the owner clicking "Dựng lại" (an unattended render has
# nobody to click it)
THROTTLE_WAITS = (60.0, 180.0, 420.0)


def _noop(*_a) -> None:
    return None


def throttled(exc: BaseException) -> bool:
    """The TTS server limited us (tts.synthesize's RuntimeError from edge's TTSThrottled)."""
    return type(exc).__name__ == "TTSThrottled" or type(exc.__cause__).__name__ == "TTSThrottled"


def with_throttle_waits(make: Callable[[], list], *, log: Callable[[str], None] = _noop,
                        cancel: Callable[[], bool] | None = None,
                        waits: tuple[float, ...] = THROTTLE_WAITS,
                        sleep: Callable[[float], None] = time.sleep) -> list:
    """``make()`` (the dub), waiting and trying again while the TTS server is limiting us.
    Each try only makes the lines still missing. Cancel is honoured during the wait."""
    for i, wait in enumerate((*waits, None)):
        try:
            return make()
        except RuntimeError as exc:
            if wait is None or not throttled(exc):
                raise
            log(f"⏳ Máy chủ giọng đọc đang giới hạn — tự thử tiếp sau {wait:.0f} giây "
                f"({i + 1}/{len(waits)})")
            end = time.monotonic() + wait
            while time.monotonic() < end:
                if cancel is not None and cancel():
                    raise InterruptedError("Đã hủy.") from exc
                sleep(min(1.0, max(0.0, end - time.monotonic())))
    raise AssertionError("unreachable")


def note_hardsubs(job: Job, o: dict, *, log: Callable[[str], None] = _noop,
                  debug: Callable[[str], None] = _noop) -> None:
    """ASR was picked by hand: still look (locally, 16 frames, nothing uploaded) for
    burned-in Chinese text, so the editor can default to a box that covers it. Best
    effort — never fails the run."""
    from app.pipeline import hardsub_detect

    try:
        has = hardsub_detect.detect_hardsubs(job, crop=o.get("ocr_region", "bottom-third"))
    except Exception as exc:  # noqa: BLE001
        debug(f"hardsub note skipped: {exc}")
        return
    job.metadata["has_hardsubs"] = has
    atomic_write_text((job.work_dir / "metadata.json"),
                      json.dumps(job.metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    if has:
        # say what will really cover it (the render may use the glass, 2026-10-30)
        how = {"glass": "kính mờ", "box": "nền hộp", "blur": "dải làm mờ"}.get(
            str(o.get("cover_mode") or ""))
        log("ℹ Video có chữ Trung in sẵn — " + (f"che bằng {how}" if how else
            "nên che (màn Dựng → Che chữ Trung, hoặc kiểu chữ Kính mờ trắng)"))


def glass_region(job: Job, o: dict, *, log: Callable[[str], None] = _noop,
                 progress: Callable[[float, str], None] = _noop,
                 debug: Callable[[str], None] = _noop) -> None:
    """The glass must sit where the burned-in Chinese is: when no band was measured yet
    (ASR job, older project) look once now — local OCR on 16 frames, a few seconds, nothing
    uploaded. Best effort: without it the glass covers the usual caption band."""
    from app.pipeline import assemble, glass

    if not (glass.is_glass(o.get("style")) or o.get("cover_mode") == "glass"):
        return
    if o.get("preview") or assemble._load_region(job) is not None:
        return
    _region, hardsubs, _cn = glass.job_context(job)
    if not hardsubs:
        return
    progress(0.6, "Đang dò vị trí chữ Trung để đặt kính mờ…")
    note_hardsubs(job, o, log=log, debug=debug)


def render_job(job: Job, o: dict, *, log: Callable[[str], None] = _noop,
               progress: Callable[[float, str], None] = _noop,
               stage: Callable[[str], None] = _noop,
               cancel: Callable[[], bool] | None = None,
               debug: Callable[[str], None] = _noop) -> Path:
    """One attempt at rendering ``job`` with ``o`` (the render page's opts; ``burn_cues`` =
    the subtitles snapshot to burn, ``preview`` = the quick 640p proof). Returns the output
    path. Raises when the subtitles are not the approved revision."""
    from app.pipeline import approval, assemble, batch, commentary, disclosure, review, tts

    def check() -> None:
        if cancel is not None and cancel():
            raise InterruptedError("Đã hủy.")

    check()
    if not job.vi_srt.exists():
        raise FileNotFoundError("Chưa có vi.srt — hãy chạy tới bước duyệt trước.")
    if not approval.is_approved(job):
        raise RuntimeError("Phụ đề chưa được duyệt, hoặc đã sửa sau khi duyệt — quay lại "
                           "Sửa phụ đề và bấm Duyệt và dựng.")
    batch.save_opts(job.work_dir, o)   # every real render: a template for "Dựng hàng loạt"

    # ENG-2: when keeping the music, separate the instrumental BEFORE TTS (both heavy →
    # sequential, 8 GB rule). Failure — or no engine installed — degrades to "duck" in
    # assemble: the original music + effects stay.
    if o.get("audio_mode") == "keep_music":
        from app.pipeline import separate
        if not separate.available():
            log("⚠ Chưa cài bộ tách giọng — dùng “Giữ nguyên âm gốc + lồng tiếng” (nhạc, hiệu "
                "ứng vẫn còn).")
        else:
            stage("separate")
            progress(0.1, "Đang tách giọng khỏi nhạc nền…")
            try:
                separate.separate_background(job, log=log)
            except Exception as exc:  # noqa: BLE001 - degrade, don't abort the render
                log(f"⚠ Không tách được nhạc nền ({exc}) — dùng “Giữ nguyên âm gốc + lồng "
                    "tiếng”.")

    original = o.get("audio_mode") == "original"
    if original:
        # E-8: keep the original voice/singing — subtitles only, no TTS at all
        segments = []
        log("Giữ giọng gốc — không lồng tiếng, chỉ đốt phụ đề")
    else:
        stage("tts")
        progress(0.2, "Đang tạo lồng tiếng (TTS)…")
        segments = with_throttle_waits(lambda: tts.synthesize(
            job, provider=o.get("tts", "edge"), voice=o.get("voice"), rate=o.get("rate"), log=log,
            voice_by_gender=bool(o.get("voice_by_gender")),
            cancel=(lambda: bool(cancel and cancel())),  # per-cue (N-10)
            progress=lambda d, n: progress(0.2 + 0.3 * d / max(1, n),
                                           f"Đang tạo lồng tiếng (TTS)… {d}/{n} câu")),
            log=log, cancel=cancel)
        log(f"✓ TTS {len(segments)} câu ({o.get('tts', 'edge')})")

    # F-4: the user's own commentary — a voice other than the dub's, or their recording
    notes = commentary.load(job)
    voiced = []
    if notes:
        check()
        progress(0.5, f"Đang tạo giọng cho {len(notes)} đoạn bình luận…")
        voiced = commentary.synthesize(job, notes, provider=o.get("tts", "edge"),
                                       dub_voice=o.get("voice"), rate=o.get("rate"), log=log)
    # keeping the original voice but adding commentary: the original ducks under it
    audio_mode = ("duck" if voiced else "replace") if original else o.get("audio_mode", "replace")

    check()
    stage("assemble")
    progress(0.6, "Đang ghép video (ffmpeg)…")
    ui_size = o.get("size")
    ass_size = int(round(ui_size * 3)) if ui_size else 60  # UI px → .ass units (1080 canvas)
    glass_region(job, o, log=log, progress=progress, debug=debug)
    out = assemble.assemble(
        job, segments,
        burn_cues=o.get("burn_cues"),  # N-13 snapshot: subs match what TTS spoke
        audio_mode=audio_mode,
        orig_volume=1.0 if original else o.get("orig_volume", 1.0),
        dub_volume=o.get("dub_volume", 1.0),
        cover_hardsubs=o.get("cover_hardsubs", False),
        cover_mode=o.get("cover_mode"),
        font=o.get("font", "Be Vietnam Pro"),
        size=ass_size,
        style=o.get("style"),
        log=log,
        preview=bool(o.get("preview")),     # E-15: fast low-res check first
        frame=o.get("frame") or "source",   # E-16: "tiktok" = 1080x1920
        res=o.get("res") or "source",       # "Chất lượng": short side, never upscaled
        preset=o.get("preset"),             # light | balanced | sharp → CRF / CQ
        progress=lambda f, m: progress(0.6 + 0.4 * f, m),
        cancel=(lambda: bool(cancel and cancel())),   # a long encode stops on Hủy
        overlays=disclosure.overlays(        # F-1/F-2: credit + AI-voice label
            job, credit=bool(o.get("credit_overlay")),
            ai_voice=bool(o.get("ai_label")) and (not original or bool(voiced))),
        commentary=voiced,
        watermark=o.get("watermark"),      # the translator's own mark
        fill=o.get("tiktok_fill") or "blur",   # 9:16 bars: blurred picture / black
        episode_card=o.get("episode_card"),    # "Tập 9" chip + "Còn tiếp" end card
        safe_caption=o.get("safe_caption"),    # Short: subtitles above the platform's UI
        canvas_text=bool(o.get("canvas_text")),   # 9:16: chip / hook / end card in the bars
        bar_subs=bool(o.get("bar_subs")),         # … and the dialogue in the bottom bar
    )
    review.stamp_fit_report(job, rate=o.get("rate"))   # late flags ↔ this speed
    progress(1.0, "Hoàn tất")
    log(f"✓ Xuất: {out}")
    return Path(out)
