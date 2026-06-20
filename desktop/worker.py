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

    # --- to the edit gate ----------------------------------------------------
    @Slot()
    def run_to_gate(self) -> None:
        from app.pipeline import asr, download, translate
        from app.pipeline.srt import parse_srt, write_srt

        job, o = self.job, self.opts
        try:
            store.debug(f"run_to_gate begin file={bool(o.get('file'))} url={o.get('url')} src={o.get('subtitle_source')}")
            # 1) source
            self._check()
            if o.get("file"):
                self.stage_started.emit("download")
                self.progress.emit(0.1, "Đang chép tệp cục bộ…")
                shutil.copy(o["file"], job.source_video)
                job.metadata.setdefault("platform", "local")
                self.log.emit(f"✓ Dùng tệp: {Path(o['file']).name}")
            else:
                self.stage_started.emit("download")
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
            if src == "srt" and o.get("srt"):
                self.progress.emit(0.4, "Dùng phụ đề tiếng Trung có sẵn…")
                write_srt(parse_srt(Path(o["srt"])), job.cn_srt)
                self.log.emit("✓ Nạp .srt tiếng Trung")
            elif src == "ocr":
                from app.pipeline import ocr

                self.progress.emit(0.4, "Đọc chữ cháy trên video bằng OCR…")
                self.log.emit("OCR: đọc phụ đề tiếng Trung cháy sẵn…")
                ocr.extract_hardsubs(job)
                self.log.emit("✓ OCR xong")
            else:
                self.progress.emit(0.4, "Nhận dạng giọng nói (ASR)…")
                self.log.emit(f"ASR: {o.get('asr', 'faster_whisper')}")
                asr.transcribe(job, provider=o.get("asr", "faster_whisper"))
                self.log.emit("✓ Nhận dạng xong")

            store.debug("subtitle stage ok")
            # 3) translate
            self._check()
            self.stage_started.emit("translate")
            self.progress.emit(0.75, "Đang dịch CN→VI…")
            self.log.emit(f"Dịch: {o.get('translator', 'passthrough')}"
                          + (f" → {o['refine']}" if o.get("refine") else ""))
            translate.translate(job, draft=o.get("translator", "passthrough"), refine=o.get("refine"))
            self.log.emit("✓ Dịch xong — sẵn sàng để bạn duyệt")
            store.debug("translate ok → emit gate_reached")

            self.progress.emit(1.0, "Sẵn sàng sửa phụ đề")
            self.gate_reached.emit()
        except _Cancelled:
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

            self.stage_started.emit("tts")
            self.progress.emit(0.2, "Đang tạo lồng tiếng (TTS)…")
            segments = tts.synthesize(job, provider=o.get("tts", "edge"), voice=o.get("voice"), rate=o.get("rate"))
            self.log.emit(f"✓ TTS {len(segments)} câu ({o.get('tts', 'edge')})")

            self._check()
            self.stage_started.emit("assemble")
            self.progress.emit(0.6, "Đang ghép video (ffmpeg)…")
            ui_size = o.get("size")
            ass_size = int(round(ui_size * 3)) if ui_size else 60  # UI px → .ass units (1080 canvas)
            out = assemble.assemble(
                job, segments,
                replace_audio=not o.get("keep_audio", False),
                cover_hardsubs=o.get("cover_hardsubs", False),
                font=o.get("font", "Be Vietnam Pro"),
                size=ass_size,
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
