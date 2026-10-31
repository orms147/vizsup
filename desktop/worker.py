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
from app.fsutil import atomic_write_text


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
    area_needed = Signal(object)       # {"frames", "suggestion", "series"}: draw the OCR band

    def __init__(self, job: Job, opts: dict) -> None:
        super().__init__()
        self.job = job
        self.opts = opts
        self._cancel = False
        import threading

        self._area_event = threading.Event()
        self._area_answer: tuple[dict | None, bool] = (None, False)

    def answer_area(self, rect: dict | None, series_too: bool = True) -> None:
        """The picker's answer (GUI thread): the band, or None = let the app detect it."""
        self._area_answer = (rect, series_too)
        self._area_event.set()

    def _ask_area(self, job: Job, o: dict, suggestion: dict | None = None) -> dict | None:
        """"Khoanh vùng chữ Trung": show frames + the detected band, wait for the owner.
        Only for a run the owner started in the app (never the watcher's or the bot's)."""
        from app.pipeline import assemble, hardsub_detect, ocr_area, series

        self.progress.emit(0.3, "Đang dò phụ đề cháy sẵn trên video…")
        try:
            has = hardsub_detect.detect_hardsubs(job, crop=o.get("ocr_region", "bottom-third"),
                                                 engine=o.get("ocr_engine") or "rapidocr")
        except Exception as exc:  # noqa: BLE001 - the picker still works without a guess
            store.debug(f"area detect failed: {exc}")
            has = True
        if not has and o.get("subtitle_source") == "auto":
            return None                                  # no burned subtitles: ASR, no question
        frames = ocr_area.sample_frames(job)
        if not frames:
            return None
        p = series.profile_of(job)
        self.progress.emit(0.32, "Chờ bạn khoanh vùng chữ Trung…")
        self.log.emit("Khoanh vùng chữ Trung: kéo chuột quanh dải phụ đề trong cửa sổ vừa mở")
        self._area_event.clear()
        self.area_needed.emit({"frames": frames,
                               "suggestion": suggestion or assemble._load_region(job),
                               "series": p.name if p is not None else None})
        while not self._area_event.wait(0.2):
            self._check()
        rect, series_too = self._area_answer
        if rect is None:
            self.log.emit("↷ Để app tự dò vùng chữ")
            return None
        kept = ocr_area.remember(job, rect, series_too=series_too)
        if kept and series_too and p is not None:
            self.log.emit(f"✓ Đã nhớ vùng chữ cho bộ phim 《{p.name}》 — các tập sau dùng luôn")
        return kept

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

    def _timing_pass(self, job: Job, o: dict) -> None:
        """Optional aid, like the snap: a failure is logged, never fatal."""
        from app.pipeline import timing_pass

        try:
            timing_pass.run_srt(job, job.cn_srt, scenes=bool(o.get("timing_scenes", False)),
                                log=self.log.emit)
        except Exception as exc:  # noqa: BLE001
            store.debug(f"timing pass failed: {type(exc).__name__}: {exc}")
            self.log.emit(f"⚠ Bỏ qua bước chỉnh thời gian chuẩn ({type(exc).__name__}: {exc})")

    def _vi_lint(self, job: Job) -> None:
        from app.pipeline import vi_lint

        try:
            vi_lint.fix_job(job, log=self.log.emit)
        except Exception as exc:  # noqa: BLE001
            store.debug(f"vi lint failed: {type(exc).__name__}: {exc}")
            self.log.emit(f"⚠ Bỏ qua bước soát lỗi tiếng Việt ({type(exc).__name__}: {exc})")

    def _guess_voices(self, job: Job) -> None:
        from app.pipeline import voices

        try:
            voices.guess_job(job, log=self.log.emit)
        except Exception as exc:  # noqa: BLE001
            store.debug(f"voice guess failed: {type(exc).__name__}: {exc}")
            self.log.emit(f"⚠ Bỏ qua bước đoán giọng nam/nữ ({type(exc).__name__}: {exc})")

    def _series(self, job: Job, o: dict) -> None:
        """Link to / create the series profile (option ``series``: auto | none | slug) and
        copy its style and render settings into a project that has none. An aid: logged,
        never fatal."""
        from app.pipeline import series

        try:
            prof = series.resolve(job, o.get("series") or "auto")
            if prof is None:
                return
            self.log.emit(series.summary(prof))
            series.inherit(job, prof, log_fn=self.log.emit)
        except Exception as exc:  # noqa: BLE001
            store.debug(f"series failed: {type(exc).__name__}: {exc}")
            self.log.emit(f"⚠ Bỏ qua bước bộ phim ({type(exc).__name__}: {exc})")

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
        atomic_write_text((job.work_dir / split.GROUP_FILE),
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

    @Slot()
    def run_compile(self) -> None:
        """Ghép tập: join rendered episodes / compilations into ``self.job.work_dir``
        (a new folder). opts["_compile"] = {title, ids, names} from CompileDialog.spec().
        A run that fails or is cancelled removes its half-made folder."""
        from app.pipeline import compilation

        spec = self.opts.get("_compile") or {}
        storage = settings.vizsup_storage_dir
        out_dir = self.job.work_dir
        try:
            self.stage_started.emit("compile")
            names = spec.get("names") or {}
            sources = []
            for sid in spec.get("ids") or []:
                s = compilation.source_from_dir(storage, sid)
                if s is None:
                    raise RuntimeError("Một video trong danh sách không còn trên máy (đã xóa?).")
                if s.kind != "compilation" and names.get(sid):
                    s.chapters = [(0.0, str(names[sid]))]
                sources.append(s)
            out = compilation.compile_videos(
                out_dir, sources, title=spec.get("title") or compilation.default_title(sources),
                log=self.log.emit, progress=lambda f, m: self.progress.emit(f, m),
                cancel=lambda: self._cancel)
            self.progress.emit(1.0, "Hoàn tất")
            self.render_done.emit(str(out))
        except (_Cancelled, InterruptedError):
            self.failed.emit("Đã hủy.")
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(_user_message(exc))
        finally:
            if not (out_dir / compilation.COMP_FILE).exists():
                shutil.rmtree(out_dir, ignore_errors=True)
            self.finished.emit()

    def _resilient(self, body) -> None:
        """Run ``body``; when it fails because the internet dropped, show it, wait for the
        connection and run it again (app/net.py) — nothing done so far is lost."""
        from app import net

        def waiting(secs: float) -> None:
            self.progress.emit(-1.0, f"Mất kết nối mạng — đang chờ có mạng lại để tự chạy "
                                     f"tiếp ({int(secs)} giây)…")

        def back() -> None:
            self.log.emit("✓ Có mạng lại — chạy tiếp từ chỗ đã dừng")

        def lost_once() -> None:
            self.log.emit("⚠ Mất kết nối mạng — giữ nguyên phần đã làm, chờ có mạng lại…")

        state = {"told": False}

        def on_wait(secs: float) -> None:
            if not state["told"]:
                state["told"] = True
                lost_once()
            waiting(secs)

        def on_back() -> None:
            state["told"] = False
            back()

        net.run_resilient(body, cancel=lambda: self._cancel, on_wait=on_wait, on_back=on_back)

    # --- to the edit gate ----------------------------------------------------
    @Slot()
    def run_to_gate(self) -> None:
        """E-14: every AI call of this run is costed into <job>/usage.json."""
        from app import usage

        with usage.track(self.job.work_dir, phase="gate"):
            self._run_to_gate()

    def _gate_body(self) -> None:
        """One attempt of _run_to_gate (raises); re-run as a whole after a lost
        connection — every stage skips what is already on disk."""
        from app.pipeline import asr, download, translate
        from app.pipeline.srt import parse_srt, write_srt

        job, o = self.job, self.opts
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
        if job.source_video.exists() and not _playable(job.source_video):
            # a download cut off by an older version / a crash: never build on half a file
            self.log.emit("⚠ Video nguồn tải dở (không đọc được) — tải lại")
            job.source_video.unlink(missing_ok=True)
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
        atomic_write_text((job.work_dir / "metadata.json"),
            _json.dumps(job.metadata, ensure_ascii=False, indent=2), encoding="utf-8")

        # 1.5) a long video becomes ~30-min parts that run through the queue
        if self._split_if_long(job, o):
            return

        # 1.7) "Khoanh vùng chữ Trung": the band OCR reads — the series' saved one, else asked
        # once in the app (never for the watcher's / the bot's runs: nobody is at the screen)
        area = None
        if not job.cn_srt.exists() and o.get("subtitle_source", "asr") in ("auto", "ocr"):
            from app.pipeline import ocr_area

            try:                                     # the series link, so its band is found
                from app.pipeline import series as _ser

                _ser.resolve(job, o.get("series") or "auto")
            except Exception as exc:  # noqa: BLE001 - the 2.38 step reports series problems
                store.debug(f"area series link: {exc}")
            area = ocr_area.for_job(job)
            if o.get("_ask_area"):                   # "Đọc lại phụ đề": draw it again
                self._check()
                area = self._ask_area(job, o, suggestion=area)
            elif area is not None:
                ocr_area.remember(job, area, series_too=False)   # the cover band follows it
            elif o.get("pick_area") and not (o.get("_remote") or o.get("_watch")):
                self._check()
                area = self._ask_area(job, o)

        # 2) Chinese subtitles — source chosen by the user in step 1
        self._check()
        self.stage_started.emit("subtitle")
        src = o.get("subtitle_source", "asr")
        if area is not None and src == "auto":
            src = "ocr"                                # the owner drew the band: burned subs
            self.log.emit("✓ Đọc chữ trong vùng đã khoanh")
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
                atomic_write_text((job.work_dir / "metadata.json"),
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
                job, crop=region, engine=engine, log=self.log.emit, area=area,
                fps=1.0 if o.get("ocr_fast") else 2.0,             # "OCR nhanh"
                cancel=lambda: self._cancel,                       # long videos: ETA + resume
                progress=lambda f, m: self.progress.emit(0.4 + 0.15 * f, m))
            self.log.emit("✓ OCR xong")
            job.metadata["has_hardsubs"] = True          # → editor defaults to a cover box
            atomic_write_text((job.work_dir / "metadata.json"),
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

            asr.transcribe(job, provider=asr_name, log=asr_log, vocals=o.get("vocal_timing", True))
            translate.clear_ocr_provenance(job)   # non-OCR source → drop stale report
            self.log.emit("✓ Nhận dạng xong")
            if "has_hardsubs" not in job.metadata:
                self._note_hardsubs(job, o)

        store.debug("subtitle stage ok")
        # 2.2) one subtitle per spoken clause, like the original captions — a line that
        # holds "A, B, punchline." would show the punchline before it is said
        if src == "asr" and o.get("clause_split", True):
            from app.pipeline import clauses

            try:
                clauses.split_job(job, log=self.log.emit)
            except Exception as exc:  # noqa: BLE001 - an aid, never fatal
                store.debug(f"clause split failed: {type(exc).__name__}: {exc}")
        # 2.3) snap fresh Chinese cues to the voice (local Silero VAD) BEFORE
        # translation, so vi.srt inherits the times. Never fails the run.
        if src != "existing" and o.get("auto_snap", True):
            self._check()
            self.progress.emit(0.65, "Canh lời theo giọng nói…")
            self._snap_to_voice(job)
        # 2.35) tidy the cue times (min gap, chaining, min duration; scene cuts opt-in)
        if src != "existing" and o.get("timing_pass", True):
            self._check()
            self._timing_pass(job, o)
        # 2.38) Bộ phim: link the episode to its series and take over its style / render
        self._series(job, o)
        # 2.39) Bộ phim: names + xưng hô from the newest approved episode (one call, once)
        if not job.vi_srt.exists():
            from app.pipeline import cast
            self._check()
            cast.learn(job, o.get("translator", "passthrough"), log=self.log.emit)
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
            from app.pipeline import series
            b = brief.build_brief(job, o.get("translator", "passthrough"),
                                  note=note or None, log=self.log.emit, observed=observed,
                                  fixed=series.sheet_for(job))
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
                                log=self.log.emit, cancel=lambda: self._cancel,
                                voice_hints=o.get("voice_hints", True))
            self.log.emit("✓ Dịch xong — sẵn sàng để bạn duyệt")
            # 3.4) Vietnamese typography lint (spaces, …, quotes, repeated function words)
            # BEFORE the AI review, whose findings are tied to the unchanged line text
            if o.get("vi_lint", True):
                self._vi_lint(job)
            # 3.5) E-5: a strong model points at lines worth a human look (never edits)
            if o.get("ai_review", True) and o.get("translator", "passthrough") != "passthrough":
                self._check()
                from app.pipeline import brief, qa

                self.progress.emit(0.92, "AI soát bản dịch…")
                qa.review(job, o.get("refine") or o["translator"],
                          context={"_brief": brief.load_brief(job) or ""},
                          log=self.log.emit)
            # 3.6) which lines are a man's / a woman's (pitch) — used when "Giọng theo
            # nhân vật" is on; only runs right after translating, so it never revokes an
            # approval. An aid: logged, never fatal. Off by default (2026-10-29: the owner
            # dubs with one voice; the guess may start a Demucs separation for nothing).
            if o.get("voice_guess", False):
                self._guess_voices(job)
        store.debug("translate ok → emit gate_reached")

        self.progress.emit(1.0, "Sẵn sàng sửa phụ đề")
        self.gate_reached.emit()

    def _run_to_gate(self) -> None:
        from app.pipeline import translate

        try:
            self._resilient(self._gate_body)
        except (_Cancelled, translate.TranslateCancelled, InterruptedError):
            self.failed.emit("Đã hủy.")
        except Exception as exc:  # noqa: BLE001 - surface to UI
            import traceback as _tb
            store.debug(f"run_to_gate FAILED {type(exc).__name__}: {exc}\n{_tb.format_exc()}")
            self.failed.emit(_user_message(exc))
        finally:
            self.finished.emit()

    def _note_hardsubs(self, job, o: dict) -> None:
        """ASR was picked by hand: still look for burned-in Chinese text (app/pipeline/render.py)."""
        from app.pipeline import render

        render.note_hardsubs(job, o, log=self.log.emit, debug=store.debug)

    # --- render --------------------------------------------------------------
    @Slot()
    def run_render(self) -> None:
        """E-14: every AI call of this run is costed into <job>/usage.json."""
        from app import usage

        with usage.track(self.job.work_dir, phase="render"):
            self._run_render()

    def _render_body(self) -> None:
        """One attempt of _run_render (raises); re-run as a whole after a lost
        connection — every stage skips what is already on disk. The body itself is
        app/pipeline/render.py (shared with the scheduled task's headless render); it
        refuses anything but the approved revision."""
        from app.pipeline import render

        out = render.render_job(self.job, self.opts, log=self.log.emit,
                                progress=self.progress.emit, stage=self.stage_started.emit,
                                cancel=lambda: self._cancel, debug=store.debug)
        self.render_done.emit(str(out))

    def _run_render(self) -> None:
        try:
            self._resilient(self._render_body)
        except (_Cancelled, InterruptedError):
            self.failed.emit("Đã hủy.")
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(_user_message(exc))
        finally:
            self.finished.emit()


def _playable(path: Path) -> bool:
    """A complete media file: ffprobe reads a duration (a cut-off MP4 has no index)."""
    from app.pipeline.assemble import probe_duration

    try:
        return probe_duration(path) > 0
    except Exception:  # noqa: BLE001
        return False


def _user_message(exc: Exception) -> str:
    """What the error card shows: our own messages are already Vietnamese sentences
    ("Máy chủ giọng đọc đang giới hạn …") — "RuntimeError:" in front only scares; a
    foreign exception keeps its type name, which is what makes it debuggable."""
    if type(exc) in (RuntimeError, ValueError) and str(exc).strip():
        return str(exc)
    return f"{type(exc).__name__}: {exc}"
