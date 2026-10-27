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
from app.config import settings  # noqa: E402
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
    split_done = Signal(list)          # long video → part opts to queue (no gate for the parent)
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

    def _snap_to_voice(self, job: Job) -> None:
        """Optional aid: a VAD/snap failure is logged, never fatal."""
        from app.pipeline import snap

        try:
            snap.snap_srt(job, job.cn_srt, log=self.log.emit)
        except Exception as exc:  # noqa: BLE001
            store.debug(f"auto-snap failed: {type(exc).__name__}: {exc}")
            self.log.emit(f"⚠ Bỏ qua bước tự canh lời ({type(exc).__name__}: {exc})")

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

    def _split_if_long(self, job: Job, o: dict) -> bool:
        """Long-video split (pipeline/split.py). True when the job became a group of
        parts (split_done emitted, nothing else to do in this run)."""
        from app.pipeline import split
        from app.pipeline.assemble import probe_duration

        if not o.get("split_long", True) or split.group_info(job) or job.cn_srt.exists():
            return False
        duration = probe_duration(job.source_video)
        if not split.should_split(duration):
            return False
        self._check()
        self.stage_started.emit("split")
        title = (job.metadata.get("title") or (Path(o["file"]).stem if o.get("file") else "")
                 or job.id)
        group = split.split_job(job, settings.vizsup_storage_dir, title=title, log=self.log.emit,
                                progress=lambda f, m: self.progress.emit(0.15 + 0.1 * f, m),
                                cancel=lambda: self._cancel)
        whole_srt = None
        if o.get("subtitle_source") == "srt" and o.get("srt"):   # the user's own .srt
            from app.pipeline.srt import parse_srt, write_srt

            whole_srt = parse_srt(Path(o["srt"]))
        items = []
        for part in group["parts"]:
            loaded = store.load_job(part["id"])
            if loaded is None:
                continue
            pjob, _ = loaded
            if whole_srt is not None:      # each part gets ITS lines, on its own timeline
                write_srt(split.slice_subtitles(whole_srt, part["start"], part["end"]
                                                if part["index"] < len(group["parts"]) else None),
                          pjob.cn_srt)
            popts = {k: v for k, v in o.items() if k not in ("file", "url", "srt")}
            popts["_job_id"] = part["id"]
            store.save_job(pjob, popts, "queued")
            items.append(popts)
        import json as _json

        group["opts"] = {k: v for k, v in o.items() if k not in ("file", "url")}
        (job.work_dir / split.GROUP_FILE).write_text(
            _json.dumps(group, ensure_ascii=False, indent=2), encoding="utf-8")
        (job.work_dir / "job.json").unlink(missing_ok=True)   # the parent is a group now
        self.log.emit(f"✓ Đã chia {len(items)} phần — chạy lần lượt; phần nào xong sẽ báo để bạn "
                      f"duyệt trong lúc phần sau còn chạy")
        self.split_done.emit(items)
        return True

    @Slot()
    def run_join(self) -> None:
        """Join a long video's rendered parts (opts["_group"] = group id)."""
        from app.pipeline import split

        gdir = settings.vizsup_storage_dir / str(self.opts.get("_group"))
        try:
            self.stage_started.emit("assemble")
            out = split.join(gdir, settings.vizsup_storage_dir, log=self.log.emit,
                             progress=lambda f, m: self.progress.emit(f, m),
                             cancel=lambda: self._cancel)
            self.progress.emit(1.0, "Hoàn tất")
            self.render_done.emit(str(out))
        except (_Cancelled, InterruptedError):
            self.failed.emit("Đã hủy.")
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(_user_message(exc))
        finally:
            self.finished.emit()

    # --- to the edit gate ----------------------------------------------------
    @Slot()
    def run_to_gate(self) -> None:
        """E-14: every AI call of this run is costed into <job>/usage.json."""
        from app import usage

        with usage.track(self.job.work_dir, phase="gate"):
            self._run_to_gate()

    def _run_to_gate(self) -> None:
        from app.pipeline import asr, download, translate
        from app.pipeline.srt import parse_srt, write_srt

        job, o = self.job, self.opts
        try:
            store.debug(f"run_to_gate begin file={bool(o.get('file'))} url={o.get('url')} src={o.get('subtitle_source')}")
            # 0) pre-flight: validate the translator choice BEFORE spending download/OCR
            self._preflight_translators(o)
            if o.get("subtitle_source", "asr") in ("auto", "ocr"):
                from app.providers.registry import get_ocr

                eng = get_ocr(o.get("ocr_engine") or "rapidocr")
                if not eng.available():
                    raise RuntimeError(f"Chưa dùng được “{eng.display_name}” — thêm khóa "
                                       f"OpenRouter trong Cài đặt, hoặc chọn đọc chữ trên máy.")
            # 1) source — skip when the artifact already exists (resume-friendly)
            self._check()
            self.stage_started.emit("download")
            if job.source_video.exists():
                self.log.emit("↷ Đã có video nguồn — dùng lại, không tải lại")
            elif o.get("file"):
                self.progress.emit(0.1, "Đang chép tệp cục bộ…")
                shutil.copy(o["file"], job.source_video)
                job.metadata.setdefault("platform", "local")
                job.metadata.setdefault("title", Path(o["file"]).stem)   # not the job id
                self.log.emit(f"✓ Dùng tệp: {Path(o['file']).name}")
            else:
                self.progress.emit(0.1, "Đang tải video…")
                self.log.emit(f"$ yt-dlp {download.normalize_url(job.url)}")
                download.download(job, log=self.log.emit)
                self.log.emit(f"✓ Tải xong: {job.metadata.get('title')!r}")

            store.debug("source ready (video on disk)")
            # persist metadata so the project is listed/resumable later
            import json as _json
            if not job.metadata and (job.work_dir / "metadata.json").exists():
                job.metadata = _json.loads((job.work_dir / "metadata.json").read_text(encoding="utf-8"))
            (job.work_dir / "metadata.json").write_text(
                _json.dumps(job.metadata, ensure_ascii=False, indent=2), encoding="utf-8")

            # 1.5) a long video becomes ~30-min parts that run through the queue
            if self._split_if_long(job, o):
                return

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
                        job, crop=o.get("ocr_region", "bottom-third"),
                        engine=o.get("ocr_engine") or "rapidocr")
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
                engine = o.get("ocr_engine") or "rapidocr"
                self.progress.emit(0.4, "Đọc chữ cháy trên video bằng OCR…")
                self.log.emit(f"OCR: đọc phụ đề tiếng Trung cháy sẵn… (vùng: {region}"
                              + (", AI đọc qua mạng" if engine != "rapidocr" else "") + ")")
                ocr.extract_hardsubs(
                    job, crop=region, engine=engine, log=self.log.emit,
                    fps=1.0 if o.get("ocr_fast") else 2.0,             # "OCR nhanh"
                    cancel=lambda: self._cancel,                       # long videos: ETA + resume
                    progress=lambda f, m: self.progress.emit(0.4 + 0.15 * f, m))
                self.log.emit("✓ OCR xong")
                job.metadata["has_hardsubs"] = True          # → editor defaults to a cover box
                (job.work_dir / "metadata.json").write_text(
                    _json.dumps(job.metadata, ensure_ascii=False, indent=2), encoding="utf-8")
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
                asr_name = o.get("asr") or settings.vizsup_default_asr
                self.log.emit(f"ASR: {asr_name}")

                def asr_log(msg: str) -> None:   # chunk progress → UI log + debug trail
                    store.debug(f"asr: {msg.strip()}")
                    self.log.emit(msg)

                asr.transcribe(job, provider=asr_name, log=asr_log)
                translate.clear_ocr_provenance(job)   # non-OCR source → drop stale report
                self.log.emit("✓ Nhận dạng xong")
                if "has_hardsubs" not in job.metadata:
                    self._note_hardsubs(job, o)

            store.debug("subtitle stage ok")
            # 2.3) snap fresh Chinese cues to the voice (local Silero VAD) BEFORE
            # translation, so vi.srt inherits the times. Never fails the run.
            if src != "existing" and o.get("auto_snap", True):
                self._check()
                self.progress.emit(0.65, "Canh lời theo giọng nói…")
                self._snap_to_voice(job)
            # 2.4) per-video brief: the AI skims the video (auto) and/or expands the
            # user's own note into a context sheet the translator reads with every
            # batch. Only when translate will actually run — a kept vi.srt needs none.
            note = (o.get("context_note") or "").strip()
            from app.pipeline import split as _split
            if not job.vi_srt.exists() and _split.inherit_brief(job, settings.vizsup_storage_dir):
                self.log.emit("↷ Dùng ngữ cảnh của phần đầu (cùng người nói, cùng xưng hô)")
            elif (o.get("auto_context") or note) and not job.vi_srt.exists():
                self._check()
                from app.pipeline import brief, speakers
                self.progress.emit(0.7, "Đọc nhanh ngữ cảnh video…")
                # E-4: who is speaking (≤60 s audio excerpt; frames only if opted in)
                observed = (speakers.observe(job, frames=bool(o.get("ai_frames")),
                                             log=self.log.emit)
                            if o.get("auto_context") else None)
                b = brief.build_brief(job, o.get("translator", "passthrough"),
                                      note=note or None, log=self.log.emit, observed=observed)
                if b:
                    self.log.emit("── Brief ngữ cảnh (bộ dịch sẽ theo) ──")
                    for ln in b.splitlines():
                        if ln.strip():
                            self.log.emit("   " + ln.rstrip())
                    _split.share_brief(job, settings.vizsup_storage_dir)
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
            if not job.vi_srt.exists() and _split.group_info(job):
                n = _split.share_terms(job, settings.vizsup_storage_dir)
                if n:
                    self.log.emit(f"… thuật ngữ chung của cả video: {n} mục (phần trước được ưu tiên)")
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
                # 3.5) E-5: a strong model points at lines worth a human look (never edits)
                if o.get("ai_review", True) and o.get("translator", "passthrough") != "passthrough":
                    self._check()
                    from app.pipeline import brief, qa

                    self.progress.emit(0.92, "AI soát bản dịch…")
                    qa.review(job, o.get("refine") or o["translator"],
                              context={"_brief": brief.load_brief(job) or ""},
                              log=self.log.emit)
            store.debug("translate ok → emit gate_reached")

            self.progress.emit(1.0, "Sẵn sàng sửa phụ đề")
            self.gate_reached.emit()
        except (_Cancelled, translate.TranslateCancelled, InterruptedError):
            self.failed.emit("Đã hủy.")
        except Exception as exc:  # noqa: BLE001 - surface to UI
            import traceback as _tb
            store.debug(f"run_to_gate FAILED {type(exc).__name__}: {exc}\n{_tb.format_exc()}")
            self.failed.emit(_user_message(exc))
        finally:
            self.finished.emit()

    def _note_hardsubs(self, job, o: dict) -> None:
        """ASR was picked by hand: still look (locally, 16 frames, nothing uploaded)
        for burned-in Chinese text, so the editor can default to a box that covers it.
        Best effort — never fails the run."""
        import json as _json

        from app.pipeline import hardsub_detect

        try:
            has = hardsub_detect.detect_hardsubs(job, crop=o.get("ocr_region", "bottom-third"))
        except Exception as exc:  # noqa: BLE001
            store.debug(f"hardsub note skipped: {exc}")
            return
        job.metadata["has_hardsubs"] = has
        (job.work_dir / "metadata.json").write_text(
            _json.dumps(job.metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        if has:
            self.log.emit("ℹ Video có chữ Trung in sẵn — phụ đề sẽ dùng nền hộp để che")

    def _glass_region(self, job, o: dict) -> None:
        """The glass must sit where the burned-in Chinese is: when no band was measured
        yet (ASR job, older project) look once now — local OCR on 16 frames, a few
        seconds, nothing uploaded. Best effort: without it the glass covers the usual
        caption band."""
        from app.pipeline import assemble, glass

        if not (glass.is_glass(o.get("style")) or o.get("cover_mode") == "glass"):
            return
        if o.get("preview") or assemble._load_region(job) is not None:
            return
        _region, hardsubs, _cn = glass.job_context(job)
        if not hardsubs:
            return
        self.progress.emit(0.6, "Đang dò vị trí chữ Trung để đặt kính mờ…")
        self._note_hardsubs(job, o)

    # --- render --------------------------------------------------------------
    @Slot()
    def run_render(self) -> None:
        """E-14: every AI call of this run is costed into <job>/usage.json."""
        from app import usage

        with usage.track(self.job.work_dir, phase="render"):
            self._run_render()

    def _run_render(self) -> None:
        from app.pipeline import assemble, disclosure, review, tts

        job, o = self.job, self.opts
        try:
            self._check()
            if not job.vi_srt.exists():
                raise FileNotFoundError("Chưa có vi.srt — hãy chạy tới bước duyệt trước.")
            # the edit gate, enforced where the money is spent: TTS + encode only run
            # for the exact revision the user approved (app/pipeline/approval.py)
            from app.pipeline import approval
            if not approval.is_approved(job):
                raise RuntimeError("Phụ đề chưa được duyệt, hoặc đã sửa sau khi duyệt — quay lại "
                                   "Sửa phụ đề và bấm Duyệt và dựng.")

            # ENG-2: when keeping the music, separate the instrumental BEFORE TTS
            # (both heavy → sequential, 8 GB rule). Failure — or no engine installed —
            # degrades to "duck" in assemble: the original music + effects stay.
            if o.get("audio_mode") == "keep_music":
                from app.pipeline import separate
                if not separate.available():
                    self.log.emit("⚠ Chưa cài bộ tách giọng — dùng “Giữ nguyên âm gốc + lồng "
                                  "tiếng” (nhạc, hiệu ứng vẫn còn).")
                else:
                    self.stage_started.emit("separate")
                    self.progress.emit(0.1, "Đang tách giọng khỏi nhạc nền…")
                    try:
                        separate.separate_background(job, log=self.log.emit)
                    except Exception as exc:  # noqa: BLE001 - degrade, don't abort the render
                        self.log.emit(f"⚠ Không tách được nhạc nền ({exc}) — dùng “Giữ nguyên "
                                      "âm gốc + lồng tiếng”.")

            original = o.get("audio_mode") == "original"
            if original:
                # E-8: keep the original voice/singing — subtitles only, no TTS at all
                segments = []
                self.log.emit("Giữ giọng gốc — không lồng tiếng, chỉ đốt phụ đề")
            else:
                self.stage_started.emit("tts")
                self.progress.emit(0.2, "Đang tạo lồng tiếng (TTS)…")
                segments = tts.synthesize(job, provider=o.get("tts", "edge"), voice=o.get("voice"),
                                          rate=o.get("rate"), log=self.log.emit,
                                          cancel=lambda: self._cancel,  # per-cue cancel (N-10)
                                          progress=lambda d, n: self.progress.emit(
                                              0.2 + 0.3 * d / max(1, n),
                                              f"Đang tạo lồng tiếng (TTS)… {d}/{n} câu"))
                self.log.emit(f"✓ TTS {len(segments)} câu ({o.get('tts', 'edge')})")

            # F-4: the user's own commentary — a voice other than the dub's, or their recording
            from app.pipeline import commentary

            notes = commentary.load(job)
            voiced = []
            if notes:
                self._check()
                self.progress.emit(0.5, f"Đang tạo giọng cho {len(notes)} đoạn bình luận…")
                voiced = commentary.synthesize(job, notes, provider=o.get("tts", "edge"),
                                               dub_voice=o.get("voice"), rate=o.get("rate"),
                                               log=self.log.emit)
            # keeping the original voice but adding commentary: the original ducks under it
            audio_mode = ("duck" if voiced else "replace") if original else \
                o.get("audio_mode", "replace")

            self._check()
            self.stage_started.emit("assemble")
            self.progress.emit(0.6, "Đang ghép video (ffmpeg)…")
            ui_size = o.get("size")
            ass_size = int(round(ui_size * 3)) if ui_size else 60  # UI px → .ass units (1080 canvas)
            self._glass_region(job, o)
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
                log=self.log.emit,
                preview=bool(o.get("preview")),     # E-15: fast low-res check first
                frame=o.get("frame") or "source",   # E-16: "tiktok" = 1080x1920
                res=o.get("res") or "source",       # "Chất lượng": short side, never upscaled
                preset=o.get("preset"),             # light | balanced | sharp → CRF / CQ
                progress=lambda f, m: self.progress.emit(0.6 + 0.4 * f, m),
                cancel=lambda: self._cancel,        # a long encode stops on Hủy
                overlays=disclosure.overlays(        # F-1/F-2: credit + AI-voice label
                    job, credit=bool(o.get("credit_overlay")),
                    ai_voice=bool(o.get("ai_label")) and (not original or bool(voiced))),
                commentary=voiced,
            )
            review.stamp_fit_report(job, rate=o.get("rate"))   # late flags ↔ this speed
            self.progress.emit(1.0, "Hoàn tất")
            self.log.emit(f"✓ Xuất: {out}")
            self.render_done.emit(str(out))
        except (_Cancelled, InterruptedError):
            self.failed.emit("Đã hủy.")
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(_user_message(exc))
        finally:
            self.finished.emit()


def _user_message(exc: Exception) -> str:
    """What the error card shows: our own messages are already Vietnamese sentences
    ("Máy chủ giọng đọc đang giới hạn …") — "RuntimeError:" in front only scares; a
    foreign exception keeps its type name, which is what makes it debuggable."""
    if type(exc) in (RuntimeError, ValueError) and str(exc).strip():
        return str(exc)
    return f"{type(exc).__name__}: {exc}"
