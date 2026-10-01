"""PipelineWorker — runs the synchronous backend pipeline off the UI thread.

Two entry slots:
- run_to_gate(): download -> (provided srt | ASR) -> translate, then gate_reached.
- run_render(): TTS -> assemble, then render_done(output_path).

Cancellation is cooperative: cancel() sets a flag checked between stages.
Errors are surfaced via failed(str) instead of crashing the UI.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from desktop import paths  # noqa: F401  (puts backend on sys.path)
from desktop import store  # noqa: E402
from app.models import Job  # noqa: E402


class _Cancelled(RuntimeError):
    pass


class PipelineWorker(QObject):
    stage_started = Signal(str)        # human label of the stage starting
    progress = Signal(float, str)      # 0..1, message
    log = Signal(str)                  # one log line
    failed = Signal(str)               # error message
    gate_reached = Signal()            # subtitles ready for editing
    render_done = Signal(str)          # output video path
    finished = Signal()                # worker done (success or failure)

    def __init__(self, job: Job, opts: dict) -> None:
        super().__init__()
        self.job = job
        self.opts = opts
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def _check(self) -> None:
        if self._cancel:
            raise _Cancelled()

    def _preflight_translators(self, o: dict) -> None:
        """Validate the translator choice up front so the user isn't told 'missing
        key' only after a long download/OCR/ASR run — and isn't silently handed
        untranslated Chinese when the (always-available) passthrough default is in
        effect. Raises RuntimeError to fail fast; logs a loud warning for passthrough.
        """
        from app.providers.registry import get_translator

        draft = o.get("translator", "passthrough")
        refine = o.get("refine")
        for role, name in (("bản nháp", draft), ("tinh chỉnh", refine)):
            if not name:
                continue
            try:
                prov = get_translator(name)
            except KeyError as exc:
                raise RuntimeError(f"Bộ dịch '{name}' không tồn tại.") from exc
            if not prov.available():
                raise RuntimeError(
                    f"Bộ dịch '{name}' ({role}) chưa sẵn sàng — thiếu API key. "
                    f"Vào ⚙ Cài đặt thêm key, hoặc đổi bộ dịch ở bước 1."
                )
        # a saved ui_state may still carry a refiner whose refine pass is a no-op
        # (the combo greys them now, but old state predates that)
        if refine and not getattr(get_translator(refine), "can_refine", True):
            o["refine"] = None
            self.log.emit(f"⚠ Bộ '{refine}' không hỗ trợ tinh chỉnh — bỏ qua bước tinh chỉnh.")
        if draft == "passthrough":
            self.log.emit(
                "⚠ Bộ dịch đang là 'passthrough' — phụ đề sẽ GIỮ NGUYÊN tiếng Trung "
                "(không dịch). Chọn OpenRouter/DeepSeek… ở bước 1 nếu muốn dịch sang tiếng Việt."
            )

    # --- to the edit gate ----------------------------------------------------
    @Slot()
    def run_to_gate(self) -> None:
        from app.pipeline import asr, download, translate
        from app.pipeline.srt import parse_srt, write_srt

        job, o = self.job, self.opts
        try:
            store.debug(f"run_to_gate begin file={bool(o.get('file'))} url={o.get('url')} src={o.get('subtitle_source')}")
            # 0) pre-flight: validate the translator choice BEFORE spending download/OCR
            self._preflight_translators(o)
            # 1) source — skip when the artifact already exists (resume-friendly)
            self._check()
            self.stage_started.emit("download")
            if job.source_video.exists():
                self.log.emit("↷ Đã có video nguồn — dùng lại, không tải lại")
            elif o.get("file"):
                self.progress.emit(0.1, "Đang chép tệp cục bộ…")
                shutil.copy(o["file"], job.source_video)
                job.metadata.setdefault("platform", "local")
                self.log.emit(f"✓ Dùng tệp: {Path(o['file']).name}")
            else:
                self.progress.emit(0.1, "Đang tải video…")
                self.log.emit(f"$ yt-dlp {download.normalize_url(job.url)}")
                download.download(job)
                self.log.emit(f"✓ Tải xong: {job.metadata.get('title')!r}")

            store.debug("source ready (video on disk)")
            # persist metadata so the project is listed/resumable later
            import json as _json
            (job.work_dir / "metadata.json").write_text(
                _json.dumps(job.metadata, ensure_ascii=False, indent=2), encoding="utf-8")

            # 2) Chinese subtitles — source chosen by the user in step 1
            self._check()
            self.stage_started.emit("subtitle")
            src = o.get("subtitle_source", "asr")
            store.debug(f"subtitle stage begin src={src}")
            if job.cn_srt.exists():
                src = "existing"  # resume: keep the already-extracted subtitles
                self.log.emit("↷ Đã có cn.srt — dùng lại, không chạy lại OCR/ASR")
            elif src == "auto":
                # E-1: sampled-frame OCR decides OCR vs ASR; any failure (no OCR
                # deps, unreadable video) falls back to ASR instead of aborting.
                from app.pipeline import hardsub_detect

                self.progress.emit(0.3, "Đang dò phụ đề cháy sẵn trên video…")
                self.log.emit("Dò phụ đề cháy sẵn (OCR nhanh trên khung hình mẫu)…")
                try:
                    has = hardsub_detect.detect_hardsubs(
                        job, crop=o.get("ocr_region", "bottom-third"))
                    src = "ocr" if has else "asr"
                    self.log.emit("✓ Có phụ đề cháy sẵn → đọc chữ bằng OCR" if has
                                  else "✓ Không thấy phụ đề cháy sẵn → nhận dạng giọng nói (ASR)")
                    job.metadata["has_hardsubs"] = has
                    (job.work_dir / "metadata.json").write_text(
                        _json.dumps(job.metadata, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception as exc:  # noqa: BLE001 - degrade, don't abort the run
                    src = "asr"
                    self.log.emit(f"⚠ Không dò được phụ đề cháy sẵn ({exc}) → dùng ASR.")
                store.debug(f"auto hardsub-detect → src={src}")
            if src == "existing":
                pass
            elif src == "srt" and o.get("srt"):
                self.progress.emit(0.4, "Dùng phụ đề tiếng Trung có sẵn…")
                write_srt(parse_srt(Path(o["srt"])), job.cn_srt)
                translate.clear_ocr_provenance(job)   # non-OCR source → drop stale report
                self.log.emit("✓ Nạp .srt tiếng Trung")
            elif src == "ocr":
                from app.pipeline import ocr

                region = o.get("ocr_region", "bottom-third")
                self.progress.emit(0.4, "Đọc chữ cháy trên video bằng OCR…")
                self.log.emit(f"OCR: đọc phụ đề tiếng Trung cháy sẵn… (vùng: {region})")
                ocr.extract_hardsubs(job, crop=region)
                self.log.emit("✓ OCR xong")
                if o.get("ocr_proofread"):
                    self._check()
                    prov = o.get("translator", "passthrough")
                    self.progress.emit(0.6, "AI đang sửa lỗi OCR…")
                    self.log.emit(f"Sửa lỗi OCR bằng AI ({prov})…")
                    n = ocr.proofread_srt(
                        job, provider=prov,
                        context={"title": job.metadata.get("title"),
                                 "platform": job.metadata.get("platform")},
                        log=self.log.emit,
                    )
                    if n:
                        self.log.emit(f"✓ Đã rà soát {n} dòng OCR")
            else:
                self.progress.emit(0.4, "Nhận dạng giọng nói (ASR)…")
                self.log.emit(f"ASR: {o.get('asr', 'faster_whisper')}")
                asr.transcribe(job, provider=o.get("asr", "faster_whisper"))
                translate.clear_ocr_provenance(job)   # non-OCR source → drop stale report
                self.log.emit("✓ Nhận dạng xong")

            store.debug("subtitle stage ok")
            # 2.4) per-video brief: the AI skims the video (auto) and/or expands the
            # user's own note into a context sheet the translator reads with every
            # batch. Only when translate will actually run — a kept vi.srt needs none.
            note = (o.get("context_note") or "").strip()
            if (o.get("auto_context") or note) and not job.vi_srt.exists():
                self._check()
                from app.pipeline import brief
                self.progress.emit(0.7, "Đọc nhanh ngữ cảnh video…")
                b = brief.build_brief(job, o.get("translator", "passthrough"),
                                      note=note or None, log=self.log.emit)
                if b:
                    self.log.emit("── Brief ngữ cảnh (bộ dịch sẽ theo) ──")
                    for ln in b.splitlines():
                        if ln.strip():
                            self.log.emit("   " + ln.rstrip())
            # 2.5) optional pre-pass: suggest a glossary of recurring names/brands so
            # they translate consistently (writes glossary.suggested.json; user wins)
            if o.get("extract_terms"):
                self._check()
                from app.pipeline import terms
                self.progress.emit(0.72, "Trích xuất thuật ngữ…")
                terms.suggest_glossary(
                    job, o.get("translator", "passthrough"),
                    context={"title": job.metadata.get("title"),
                             "platform": job.metadata.get("platform")},
                    log=self.log.emit)
            # 3) translate
            self._check()
            self.stage_started.emit("translate")
            if job.vi_srt.exists():
                # resume: vi.srt may hold hand edits from the gate — never redo it silently
                self.log.emit("↷ Đã có vi.srt — bỏ qua dịch (giữ bản sửa tay)")
            else:
                self.progress.emit(0.75, "Đang dịch CN→VI…")
                self.log.emit(f"Dịch: {o.get('translator', 'passthrough')}"
                              + (f" → {o['refine']}" if o.get("refine") else ""))
                gl = translate.load_glossary(job)
                if gl:
                    self.log.emit(f"… dùng bảng thuật ngữ ({len(gl)} mục: storage/glossary.json + của dự án)")
                translate.translate(job, draft=o.get("translator", "passthrough"),
                                    refine=o.get("refine"), glossary=gl or None,
                                    log=self.log.emit, cancel=lambda: self._cancel)
                self.log.emit("✓ Dịch xong — sẵn sàng để bạn duyệt")
            store.debug("translate ok → emit gate_reached")

            self.progress.emit(1.0, "Sẵn sàng sửa phụ đề")
            self.gate_reached.emit()
        except (_Cancelled, translate.TranslateCancelled):
            self.failed.emit("Đã hủy.")
        except Exception as exc:  # noqa: BLE001 - surface to UI
            import traceback as _tb
            store.debug(f"run_to_gate FAILED {type(exc).__name__}: {exc}\n{_tb.format_exc()}")
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            self.finished.emit()

    # --- render --------------------------------------------------------------
    @Slot()
    def run_render(self) -> None:
        from app.pipeline import assemble, tts

        job, o = self.job, self.opts
        try:
            self._check()
            if not job.vi_srt.exists():
                raise FileNotFoundError("Chưa có vi.srt — hãy chạy tới bước duyệt trước.")

            # ENG-2: when keeping the music, separate the instrumental BEFORE TTS
            # (both heavy → sequential, 8 GB rule). Failure degrades to mix in assemble.
            if o.get("audio_mode") == "keep_music":
                from app.pipeline import separate
                self.progress.emit(0.1, "Đang tách nhạc nền (Demucs)…")
                try:
                    separate.separate_background(job, log=self.log.emit)
                except Exception as exc:  # noqa: BLE001 - degrade, don't abort the render
                    self.log.emit(f"⚠ Không tách được nhạc nền ({exc}) — sẽ trộn với âm gốc.")

            self.stage_started.emit("tts")
            self.progress.emit(0.2, "Đang tạo lồng tiếng (TTS)…")
            segments = tts.synthesize(job, provider=o.get("tts", "edge"), voice=o.get("voice"),
                                      rate=o.get("rate"), log=self.log.emit,
                                      cancel=lambda: self._cancel)  # per-cue cancel (N-10)
            self.log.emit(f"✓ TTS {len(segments)} câu ({o.get('tts', 'edge')})")

            self._check()
            self.stage_started.emit("assemble")
            self.progress.emit(0.6, "Đang ghép video (ffmpeg)…")
            ui_size = o.get("size")
            ass_size = int(round(ui_size * 3)) if ui_size else 60  # UI px → .ass units (1080 canvas)
            out = assemble.assemble(
                job, segments,
                burn_cues=o.get("burn_cues"),  # N-13 snapshot: subs match what TTS spoke
                audio_mode=o.get("audio_mode", "replace"),
                orig_volume=o.get("orig_volume", 1.0),
                dub_volume=o.get("dub_volume", 1.0),
                cover_hardsubs=o.get("cover_hardsubs", False),
                cover_mode=o.get("cover_mode"),
                font=o.get("font", "Be Vietnam Pro"),
                size=ass_size,
                style=o.get("style"),
                log=self.log.emit,
            )
            self.progress.emit(1.0, "Hoàn tất")
            self.log.emit(f"✓ Xuất: {out}")
            self.render_done.emit(str(out))
        except _Cancelled:
            self.failed.emit("Đã hủy.")
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            self.finished.emit()
