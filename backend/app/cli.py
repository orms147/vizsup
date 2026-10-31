"""Headless pipeline CLI — demonstrates the flow before the web UI exists, and
honours the human-edit gate.

    vizsup run URL [--srt cn.srt] [--translator deepseek --refine claude]
        -> download + Chinese subs + translate -> writes vi.srt, then STOPS.
           (Edit vi.srt — this is the gate.)

    vizsup render --workdir <dir> [--tts edge --voice ...]
        -> TTS the edited vi.srt + assemble the final output.mp4.

    vizsup providers   -> list available translation/TTS/ASR engines (JSON).

    vizsup doctor      -> environment self-check (ffmpeg, OCR/ASR deps, fonts, keys).

    vizsup autopilot --once [--dry-run] [--force]
        -> "theo dõi tập mới": every followed series that is due — its new episodes run to
           the edit gate with the owner's saved start-screen choices. Never approves on its
           own (what the Windows scheduled task runs, app/schedtask.py). With the Telegram
           bot paired and the app closed it also answers the owner's messages: links run to
           the gate, and ONLY an episode the owner approved with the bot's ✅ is rendered
           and its posts planned (app/pipeline/remote.py).

Defaults to the offline stack (passthrough translator + edge-tts) so it runs
with no API keys for a quick smoke test.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

from app.config import settings
from app.models import Job
from app.pipeline import asr, assemble, download, translate, tts
from app.pipeline.srt import parse_srt, write_srt


def _load_job(work_dir: Path, url: str | None = None) -> Job:
    job = Job(id=work_dir.name, work_dir=work_dir, url=url)
    meta = work_dir / "metadata.json"
    if meta.exists():
        job.metadata = json.loads(meta.read_text(encoding="utf-8"))
    return job


def _save_meta(job: Job) -> None:
    (job.work_dir / "metadata.json").write_text(
        json.dumps(job.metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _skip(name: str, stage: str) -> None:
    print(f"[run] found existing {name} -> skipping {stage} (use --force to redo)")


def cmd_run(args: argparse.Namespace) -> int:
    if len(args.url) > 1 and (args.file or args.srt or args.workdir):
        print("--file/--srt/--workdir chỉ dùng được với MỘT URL.", file=sys.stderr)
        return 2
    rc = 0
    for i, url in enumerate(args.url, 1):
        if len(args.url) > 1:
            print(f"\n===== [{i}/{len(args.url)}] {url} =====")
        try:
            rc = max(rc, _run_one(args, url))
        except Exception as exc:  # noqa: BLE001 - one bad URL must not kill the batch
            print(f"[run] LỖI với {url}: {type(exc).__name__}: {exc}", file=sys.stderr)
            rc = 1
    return rc


def _run_one(args: argparse.Namespace, url: str) -> int:
    jid = hashlib.sha1(url.encode()).hexdigest()[:10]
    work_dir = Path(args.workdir) if args.workdir else settings.vizsup_storage_dir / jid
    job = _load_job(work_dir, url=url)
    force = args.force

    # 1) source — the same URL maps to the same workdir (sha1), so re-runs resume
    if job.source_video.exists() and not force:
        _skip("source.mp4", "download")
    elif args.file:
        shutil.copy(args.file, job.source_video)
        job.metadata.setdefault("platform", "local")
        print(f"[run] using local file -> {job.source_video}")
    else:
        print(f"[run] downloading {url} ...")
        download.download(job)
        print(f"[run] downloaded: {job.metadata.get('title')!r} ({job.metadata.get('platform')})")
    _save_meta(job)

    # 2) Chinese subtitles
    if job.cn_srt.exists() and not force:
        _skip("cn.srt", "subtitle stage")
    else:
        src = "srt" if args.srt else args.source
        area = None
        if src in ("auto", "ocr"):
            # "Khoanh vùng chữ Trung": the band the owner drew for this series (ocr_area.py)
            from app.pipeline import ocr_area
            from app.pipeline import series as _ser

            try:
                _ser.resolve(job, getattr(args, "series", None) or "auto")
                area = ocr_area.for_job(job)
            except Exception as exc:  # noqa: BLE001 - detect the band as before
                print(f"[run] series band unavailable ({exc})")
            if area is not None:
                ocr_area.remember(job, area, series_too=False)
                print("[run] reading the series' marked subtitle band")
                src = "ocr"
        if src == "auto":
            # E-1: sampled-frame OCR routes OCR vs ASR; missing OCR deps → ASR.
            from app.pipeline import hardsub_detect

            print("[run] detecting burned-in subtitles ...")
            try:
                src = "ocr" if hardsub_detect.detect_hardsubs(
                    job, crop=args.ocr_region, engine=args.ocr_engine) else "asr"
                print(f"[run] hardsubs {'found -> OCR' if src == 'ocr' else 'not found -> ASR'}")
            except Exception as exc:  # noqa: BLE001 - degrade, don't abort the run
                print(f"[run] detection unavailable ({exc}) -> ASR")
                src = "asr"
            job.metadata["has_hardsubs"] = job.has_hardsubs
            _save_meta(job)  # metadata was saved before detection — persist the verdict

        if src == "srt":
            write_srt(parse_srt(Path(args.srt)), job.cn_srt)
            translate.clear_ocr_provenance(job)   # non-OCR source → drop stale report
            print(f"[run] using provided Chinese subtitles -> {job.cn_srt}")
        elif src == "ocr":
            from app.pipeline import ocr

            print(f"[run] reading hardsubs (OCR, region: {args.ocr_region}) ...")
            ocr.extract_hardsubs(job, crop=args.ocr_region, engine=args.ocr_engine, log=print,
                                 fps=args.ocr_fps, area=area)
        else:
            print(f"[run] transcribing (ASR: {args.asr}) ...")
            asr.transcribe(job, provider=args.asr, vocals=not getattr(args, "no_vocal_timing", False))
            translate.clear_ocr_provenance(job)   # non-OCR source → drop stale report
            if not getattr(args, "no_clause_split", False):
                from app.pipeline import clauses

                clauses.split_job(job, log=lambda m: print(f"[run] {m}"))
        # 2.3) snap the fresh cues to the voice (local Silero VAD) before translating
        if not getattr(args, "no_snap", False):
            from app.pipeline import snap

            try:
                snap.snap_srt(job, job.cn_srt, log=lambda m: print(f"[run] {m}"))
            except Exception as exc:  # noqa: BLE001 - an aid, never fatal
                print(f"[run] voice snap skipped ({exc})")
        # 2.35) tidy the cue times: min gap, chaining, min duration (scene cuts opt-in)
        if not getattr(args, "no_timing_pass", False):
            from app.pipeline import timing_pass

            try:
                timing_pass.run_srt(job, job.cn_srt, scenes=getattr(args, "timing_scenes", False),
                                    log=lambda m: print(f"[run] {m}"))
            except Exception as exc:  # noqa: BLE001 - an aid, never fatal
                print(f"[run] timing pass skipped ({exc})")

    # 2.38) Bộ phim: link the episode to its series, take over its style / render settings
    from app.pipeline import series as _series

    try:
        prof = _series.resolve(job, getattr(args, "series", None) or "auto")
        if prof is not None:
            print(f"[run] {_series.summary(prof)}")
            _series.inherit(job, prof, log_fn=lambda m: print(f"[run] {m}"))
    except Exception as exc:  # noqa: BLE001 - an aid, never fatal
        print(f"[run] series skipped ({exc})")
    # 2.39) Bộ phim: names + xưng hô from the newest approved episode (one call, once)
    if force or not job.vi_srt.exists():
        from app.pipeline import cast

        cast.learn(job, args.translator, log=lambda m: print(f"[run] {m}"))

    # 2.4) optional: per-video brief (AI skim and/or your own note) for the translator
    note = (getattr(args, "context", None) or "").strip()
    if (getattr(args, "auto_context", False) or note) and (force or not job.vi_srt.exists()):
        from app.pipeline import brief

        print("[run] building the video context brief ...")
        from app.pipeline import speakers

        observed = (speakers.observe(job, frames=getattr(args, "ai_frames", False),
                                     log=lambda m: print(f"[run] {m}"))
                    if getattr(args, "auto_context", False) else None)
        b = brief.build_brief(job, args.translator, note=note or None, force=force, log=print,
                              observed=observed, fixed=_series.sheet_for(job))
        if b:
            print("[run] brief:\n" + "\n".join("    " + ln for ln in b.splitlines() if ln.strip()))

    # 2.5) optional: auto-suggest a glossary before translating
    if getattr(args, "extract_terms", False) and job.cn_srt.exists():
        from app.pipeline import terms

        n = terms.suggest_glossary(job, args.translator,
                                   context={"title": job.metadata.get("title"),
                                            "platform": job.metadata.get("platform")},
                                   log=print)
        print(f"[run] extracted {n} suggested term(s)")

    # 3) translate — never silently overwrite the gate's edited vi.srt
    if job.vi_srt.exists() and not force:
        _skip("vi.srt", "translate (edits preserved)")
    else:
        print(f"[run] translating CN->VI (draft={args.translator}, refine={args.refine}) ...")
        translate.translate(job, draft=args.translator, refine=args.refine, force=force, log=print,
                            voice_hints=getattr(args, "voice_hints", False))
        # 3.4) Vietnamese typography lint, before the AI review (its findings need unchanged text)
        if not getattr(args, "no_vi_lint", False):
            from app.pipeline import vi_lint

            try:
                vi_lint.fix_job(job, log=lambda m: print(f"[run] {m}"))
            except Exception as exc:  # noqa: BLE001 - an aid, never fatal
                print(f"[run] vi lint skipped ({exc})")
        # 3.5) E-5: AI QA pass → qa_report.json (never edits vi.srt)
        if not getattr(args, "no_qa", False) and args.translator != "passthrough":
            from app.pipeline import brief, qa

            qa.review(job, args.refine or args.translator,
                      context={"_brief": brief.load_brief(job) or ""},
                      log=lambda m: print(f"[run] {m}"))
        # 3.6) which lines are a man's / a woman's (pitch) for `render --voice-by-gender`
        if getattr(args, "voice_guess", False) and not getattr(args, "no_voice_guess", False):
            from app.pipeline import voices

            try:
                voices.guess_job(job, log=lambda m: print(f"[run] {m}"))
            except Exception as exc:  # noqa: BLE001 - an aid, never fatal
                print(f"[run] voice guess skipped ({exc})")

    print("\n" + "=" * 64)
    print("EDIT GATE — review/fix the Vietnamese subtitles, then render:")
    print(f"  subtitles: {job.vi_srt}")
    print(f"  render:    vizsup render --workdir \"{job.work_dir}\"")
    print("=" * 64)
    return 0


def cmd_retime(args: argparse.Namespace) -> int:
    """E-1: re-time an existing job's Chinese lines (text kept) and, when the
    Vietnamese lines still pair 1:1, copy the new times into vi.srt."""
    work_dir = Path(args.workdir)
    if not work_dir.exists():
        print(f"workdir not found: {work_dir}", file=sys.stderr)
        return 2
    job = _load_job(work_dir)
    if job.vi_srt.exists():
        shutil.copyfile(job.vi_srt, work_dir / "vi.before-retime.srt")
    asr.retime(job, log=lambda m: print(f"[retime] {m}"))
    if job.vi_srt.exists():
        print("[retime] vi.srt " + ("updated" if asr.sync_vi_times(job) else
                                    "kept (no time map — re-time it in the editor)"))
    print(f"[retime] backups: cn.before-retime.srt / vi.before-retime.srt in {work_dir}")
    return 0


def cmd_join(args: argparse.Namespace) -> int:
    """Join a split long video's rendered parts (the parent folder holds group.json)."""
    from app.pipeline import split

    gdir = Path(args.group).resolve()
    if split.load_group(gdir) is None:
        print(f"error: no group.json in {gdir}", file=sys.stderr)
        return 2
    try:
        out = split.join(gdir, gdir.parent, log=print)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"[join] done -> {out} (+ {gdir / 'vi.srt'})")
    return 0


def cmd_compile(args: argparse.Namespace) -> int:
    """Ghép tập: join rendered projects / split videos / compilations into one video."""
    import uuid

    from app.pipeline import compilation

    storage = Path(args.storage).resolve() if args.storage else settings.vizsup_storage_dir
    sources = []
    for item in args.items:
        s = compilation.source_from_dir(storage, item)
        if s is None:
            print(f"error: no project folder {storage / item}", file=sys.stderr)
            return 2
        sources.append(s)
    if not args.keep_order:
        sources = compilation.order(sources)
    for w in compilation.warnings(sources):
        print(f"[compile] ⚠ {w}")
    cid = uuid.uuid4().hex[:12]
    try:
        out = compilation.compile_videos(storage / cid, sources, log=print,
                                         title=args.title or compilation.default_title(sources))
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        shutil.rmtree(storage / cid, ignore_errors=True)
        return 2
    print(f"[compile] done -> {out} (+ vi.srt, {compilation.CHAPTERS_TXT})")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    work_dir = Path(args.workdir)
    if not work_dir.exists():
        print(f"workdir not found: {work_dir}", file=sys.stderr)
        return 2
    job = _load_job(work_dir)
    if not job.vi_srt.exists():
        print(f"no vi.srt in {work_dir}; run the 'run' step first.", file=sys.stderr)
        return 2

    audio_mode = (args.audio_mode.replace("-", "_") if args.audio_mode
                  else ("mix" if args.keep_audio else "replace"))
    if audio_mode == "keep_music":
        from app.pipeline import separate

        print("[render] separating the voice from the background music ...")
        try:
            separate.separate_background(job, log=print)   # before TTS (both heavy)
        except Exception as exc:  # noqa: BLE001 - degrade to duck in assemble
            print(f"[render] keep-music unavailable ({exc}) -> keeping the original audio "
                  "ducked under the dub instead")

    print(f"[render] synthesizing Vietnamese voice (tts={args.tts}) ...")
    segments = tts.synthesize(job, provider=args.tts, voice=args.voice, log=print,
                              voice_by_gender=getattr(args, "voice_by_gender", False))
    print(f"[render] {len(segments)} segments; assembling with FFmpeg ...")
    from app.pipeline import commentary

    notes = commentary.load(job)       # F-4: the user's own commentary, if any
    voiced = commentary.synthesize(job, notes, provider=args.tts, dub_voice=args.voice,
                                   log=print) if notes else []
    out = assemble.assemble(
        job,
        segments,
        commentary=voiced,
        audio_mode=audio_mode,
        cover_hardsubs=args.cover_hardsubs,
        cover_mode=args.cover,
        font=args.font,
        log=print,  # surface the "câu QUÁ DÀI" fit warnings headless too
        frame=args.frame,
        res=getattr(args, "res", "source"),
        preset=getattr(args, "quality", None),
    )
    print(f"[render] done -> {out}")
    return 0


# --- autopilot: "theo dõi tập mới" --------------------------------------------------------------

_OCR_REGIONS = ("bottom-third", "bottom-quarter", "bottom-half", "middle-third", "top-third", "full")


def _pick(rows: list[dict], wanted: str | None, *, require: str | None = None,
          fallback: bool = True) -> str | None:
    """``wanted`` when it names a usable provider, else (``fallback``) the first usable one —
    what the start screen's combo shows when nothing was saved. Pure."""
    def ok(p: dict) -> bool:
        return bool(p.get("available", True)) and (not require or bool(p.get(require, True)))

    for p in rows:
        if p.get("name") == wanted and ok(p):
            return wanted
    if not fallback:
        return None
    return next((p["name"] for p in rows if ok(p)), None)


def autopilot_opts(ui: dict, providers: dict) -> dict:
    """The start screen's opts for an unattended run: the owner's saved choices
    (storage/ui_state.json) with the screen's own defaults for anything never saved —
    "không dùng" refine when none was saved, the first usable engine otherwise. Pure."""
    def on(key: str, default: bool) -> bool:
        v = ui.get(key)
        return default if v is None else bool(v)

    ocr = [p for p in providers.get("ocr", []) if p.get("name")]
    return {
        "subtitle_source": ui.get("subtitle_source") if ui.get("subtitle_source") in ("ocr", "asr")
        else "auto",
        "asr": _pick(providers.get("asr", []), ui.get("asr")) or settings.vizsup_default_asr,
        "translator": _pick(providers.get("translation", []), ui.get("translator")),
        "refine": _pick(providers.get("translation", []), ui.get("refine"), require="can_refine",
                        fallback=False),
        "tts": ui.get("tts"),
        "ocr_region": ui.get("ocr_region") if ui.get("ocr_region") in _OCR_REGIONS
        else "bottom-third",
        "ocr_engine": _pick(ocr, ui.get("ocr_engine"), fallback=False) or "rapidocr",
        "ocr_fast": on("ocr_fast", False),
        "extract_terms": on("extract_terms", False),
        "auto_context": on("auto_context", True),
        "auto_snap": on("auto_snap", True),
        "clause_split": on("clause_split", True),
        "timing_pass": on("timing_pass", True),
        "vi_lint": on("vi_lint", True),
        "voice_guess": on("voice_guess", False),
        "voice_hints": on("voice_hints", False),
        "ai_review": on("ai_review", True),
        "ai_frames": on("ai_frames", False),
    }


def autopilot_args(url: str, slug: str, opts: dict, work_dir: Path) -> argparse.Namespace:
    """``vizsup run`` flags for one episode from the start-screen opts. Pure."""
    a = build_parser().parse_args(["run", url])
    a.source = opts["subtitle_source"]
    a.asr = opts["asr"]
    a.translator = opts["translator"]
    a.refine = opts["refine"]
    a.ocr_region = opts["ocr_region"]
    a.ocr_engine = opts["ocr_engine"]
    a.ocr_fps = 1.0 if opts["ocr_fast"] else 2.0
    a.extract_terms = opts["extract_terms"]
    a.auto_context = opts["auto_context"]
    a.ai_frames = opts["ai_frames"]
    a.no_qa = not opts["ai_review"]
    a.no_snap = not opts["auto_snap"]
    a.no_clause_split = not opts["clause_split"]
    a.no_timing_pass = not opts["timing_pass"]
    a.no_vi_lint = not opts["vi_lint"]
    a.voice_guess = opts["voice_guess"]
    a.voice_hints = opts["voice_hints"]
    a.series = slug
    a.workdir = str(work_dir)
    return a


def _write_job(work_dir: Path, url: str, opts: dict, stage: str) -> None:
    """job.json as the desktop app writes it, so the episode is listed (title, link, the
    choices it ran with) and can be resumed / opened at the gate there."""
    from app.fsutil import atomic_write_text

    try:
        meta = json.loads((work_dir / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    atomic_write_text(work_dir / "job.json", json.dumps(
        {"id": work_dir.name, "url": url, "opts": opts, "stage": stage,
         "title": meta.get("title") or work_dir.name, "platform": meta.get("platform")},
        ensure_ascii=False, indent=2))


def _autopilot_runner():
    """run(url, slug) for watch.check_all: the normal `run` path to the edit gate with the
    owner's saved choices. Raises when no real translator is usable (an unattended run must
    never hand back untranslated Chinese)."""
    from app.providers.registry import list_providers

    try:
        ui = json.loads((Path(settings.vizsup_storage_dir) / "ui_state.json")
                        .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ui = {}
    base = autopilot_opts(ui if isinstance(ui, dict) else {}, list_providers())

    def run(url: str, slug: str) -> str:
        from app import usage

        if base["translator"] in (None, "passthrough"):
            raise RuntimeError("chưa có bộ dịch dùng được — thêm khóa API trong Cài đặt")
        work_dir = Path(settings.vizsup_storage_dir) / hashlib.sha1(url.encode()).hexdigest()[:10]
        work_dir.mkdir(parents=True, exist_ok=True)
        opts = {**base, "url": url, "series": slug, "autopilot": True}
        _write_job(work_dir, url, opts, "queued")
        with usage.track(work_dir, phase="gate"):
            _run_one(autopilot_args(url, slug, base, work_dir), url)
        _write_job(work_dir, url, opts, "await_edit")
        return str(work_dir)

    return run


def _autopilot_console() -> None:
    """pythonw (the scheduled task) has no console: print into <storage>/_autopilot/autopilot.log
    (the previous one kept as .old once it passes 1 MB)."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    d = Path(settings.vizsup_storage_dir) / "_autopilot"
    d.mkdir(parents=True, exist_ok=True)
    log = d / "autopilot.log"
    try:
        if log.stat().st_size > 1_000_000:
            log.replace(d / "autopilot.old.log")
    except OSError:
        pass
    f = open(log, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 - lives as long as the process
    sys.stdout = sys.stdout or f
    sys.stderr = sys.stderr or f


def cmd_autopilot(args: argparse.Namespace) -> int:
    """Check the followed series once; new episodes run to the edit gate (never approved
    here). With the Telegram bot paired (app/pipeline/remote.py) and the app closed, also
    read the owner's messages: links run to the gate, and an episode the owner approved with
    the bot's [✅ Duyệt] is rendered headless + its posts planned — only that, never anything
    unapproved. Exit 1 when an episode failed (it is retried at the next check)."""
    import time

    from app import winproc
    from app.pipeline import remote, watch

    _autopilot_console()
    winproc.hide_child_consoles()          # pythonw: no black ffmpeg windows

    def say(m: str) -> None:
        print(f"[autopilot] {m}")

    print(f"[autopilot] {time.strftime('%Y-%m-%d %H:%M:%S')} — "
          + ("xem thử (không tải gì)" if args.dry_run else "kiểm tra tập mới"))
    if args.dry_run:
        found = watch.check_all(run=None, log=say, force=args.force)
        print(f"[autopilot] xong: {len(found)} tập")
        for f in found:
            print("  " + watch.describe(f))
        return 0
    runner = _autopilot_runner()
    with remote.CliSession(log=say, run_link=runner) as rs:
        rs.inbox()                             # the owner's links / approvals from the phone
        found = watch.check_all(run=rs.wrap(runner), log=say, force=args.force)
        print(f"[autopilot] xong: {len(found)} tập"
              f" — {sum(f['status'] == 'done' for f in found)} chờ duyệt, "
              f"{sum(f['status'] == 'failed' for f in found)} lỗi")
        for f in found:
            print("  " + watch.describe(f))
        rs.inbox()                             # approvals that came in meanwhile
        from app.pipeline import autopilot     # posts whose slot came while the app was closed

        posted = autopilot.run_due(log=say)
        if posted:
            print(f"[autopilot] đăng: {len(posted)} bài")
        rs.posted(posted)
    return 1 if any(f["status"] == "failed" for f in found) else 0


def cmd_providers(_args: argparse.Namespace) -> int:
    from app.providers.registry import list_providers

    print(json.dumps(list_providers(), ensure_ascii=False, indent=2))
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    from app.doctor import main as doctor_main

    return doctor_main()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vizsup", description="CN->VI video subtitle + dubbing tool")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="download + subtitles + translate (stops at the edit gate)")
    r.add_argument("url", nargs="+",
                   help="one or more Douyin/Bilibili URLs — several run sequentially, each "
                        "parking at its own edit gate (batch mode)")
    r.add_argument("--file", help="use a local video file instead of downloading")
    r.add_argument("--srt", help="use this Chinese .srt instead of running ASR")
    r.add_argument("--source", choices=["auto", "asr", "ocr"], default="auto",
                   help="Chinese-subtitle source: auto-detect hardsubs (default, falls back to "
                        "ASR when OCR deps are missing), force ASR, or force OCR (--srt overrides)")
    r.add_argument("--force", action="store_true",
                   help="redo stages whose output already exists (an existing vi.srt is backed "
                        "up to vi.srt.bak-<timestamp> first)")
    r.add_argument("--ocr-region", default="bottom-third",
                   choices=["bottom-third", "bottom-quarter", "bottom-half", "middle-third", "top-third", "full"],
                   help="crop region for OCR/detection (default: bottom-third)")
    r.add_argument("--ocr-fps", type=float, default=2.0,
                   help="frames per second to OCR (1 = twice as fast; fine for films, may miss "
                        "captions shorter than ~1 s)")
    r.add_argument("--ocr-engine", default="rapidocr", choices=["rapidocr", "vision_llm"],
                   help="OCR engine: rapidocr (local, default) or vision_llm (OpenRouter "
                        "vision model — UPLOADS the subtitle band of every frame)")
    r.add_argument("--translator", default="passthrough", help="draft translator (default: passthrough)")
    r.add_argument("--refine", default=None, help="optional second-pass refiner (e.g. claude)")
    r.add_argument("--extract-terms", action="store_true",
                   help="auto-suggest a glossary of recurring names/brands before translating")
    r.add_argument("--auto-context", action="store_true",
                   help="let the translator skim the video first and write a context brief "
                        "(genre, speakers, xưng hô, names) it then follows")
    r.add_argument("--context", metavar="NOTE",
                   help="your own context note for this video; the AI expands it into a brief "
                        "and treats it as authoritative (implies a brief even without --auto-context)")
    r.add_argument("--asr", default=settings.vizsup_default_asr, help="ASR provider for the no-hardsub path")
    r.add_argument("--ai-frames", action="store_true",
                   help="with --auto-context: also upload ~6 frames so a vision model sees who "
                        "is on screen (better xưng hô)")
    r.add_argument("--no-qa", action="store_true",
                   help="skip the AI review pass that flags suspect lines (qa_report.json)")
    r.add_argument("--no-vocal-timing", action="store_true",
                   help="time the lines on the original sound (default: on the speech "
                        "separated from the music by Demucs, when a GPU is available)")
    r.add_argument("--no-clause-split", action="store_true",
                   help="keep one subtitle per ASR line (default: one per spoken clause, timed "
                        "from the word track, so a punchline isn't shown before it is said)")
    r.add_argument("--no-snap", action="store_true",
                   help="keep ASR/OCR cue times as-is (default: snap each cue edge to the nearest "
                        "voice start/stop within 1 s, local Silero VAD)")
    r.add_argument("--series", default="auto", metavar="auto|SLUG|none",
                   help="series profile (\"Bộ phim\"): auto = from the 《…》 in the title, a saved "
                        "series' slug, or none")
    r.add_argument("--no-vi-lint", action="store_true",
                   help="do not auto-fix Vietnamese typography (spaces, …, quotes) after translating")
    r.add_argument("--voice-guess", action="store_true",
                   help="guess each line's male/female voice from the pitch (for "
                        "`render --voice-by-gender`; off by default)")
    r.add_argument("--no-voice-guess", action="store_true", help=argparse.SUPPRESS)  # old flag
    r.add_argument("--voice-hints", action="store_true",
                   help="tell the translator which lines are a man's / a woman's voice, measured "
                        "from the pitch (helps the xưng hô; the dub keeps one voice; off by default)")
    r.add_argument("--no-timing-pass", action="store_true",
                   help="skip the timing tidy-up (default: 2-frame gaps, chain gaps < 0.5 s, "
                        "lines at least 20 frames long)")
    r.add_argument("--timing-scenes", action="store_true",
                   help="also snap cue edges to scene cuts (one extra ffmpeg pass; off by "
                        "default — fast-cutting videos snap everywhere)")
    r.add_argument("--workdir", help="explicit working directory")
    r.set_defaults(func=cmd_run)

    rn = sub.add_parser("render", help="TTS the edited vi.srt + assemble output.mp4")
    rn.add_argument("--workdir", required=True, help="job working directory from 'run'")
    rn.add_argument("--tts", default=settings.vizsup_default_tts, help="TTS provider (default: edge)")
    rn.add_argument("--voice", default=None, help="voice id (e.g. vi-VN-NamMinhNeural)")
    rn.add_argument("--voice-by-gender", action="store_true",
                    help="read each line with the male / female voice guessed from the pitch "
                         "(see overrides.json); other lines use --voice")
    rn.add_argument("--keep-audio", action="store_true", help="mix the dub over the original audio")
    rn.add_argument("--audio-mode", choices=["replace", "mix", "duck", "keep-music"], default=None,
                    help="audio: replace|mix|duck|keep-music (Demucs keeps the music). Overrides --keep-audio")
    rn.add_argument("--cover-hardsubs", action="store_true", help="legacy alias for --cover box")
    rn.add_argument("--cover", choices=["off", "box", "blur", "glass"], default=None,
                    help="hide leftover CN hardsubs: opaque box, a blurred bottom band, or "
                         "frosted white glass under each line (dark text)")
    rn.add_argument("--res", choices=["source", "1080", "720", "480"], default="source",
                    help="output resolution (short side; never above the source)")
    rn.add_argument("--quality", choices=["light", "balanced", "sharp"], default="sharp",
                    help="encode quality/size: light (small) | balanced | sharp (default)")
    rn.add_argument("--font", default="Be Vietnam Pro", help="subtitle font (Vietnamese-diacritic)")
    rn.add_argument("--frame", choices=["source", "tiktok"], default="source",
                    help="output frame: source size, or tiktok = 1080x1920 (fit + black bars)")
    rn.set_defaults(func=cmd_render)

    pr = sub.add_parser("providers", help="list available providers (JSON)")
    rt = sub.add_parser("retime", help="re-time an existing job's lines to the audio (text kept)")
    rt.add_argument("--workdir", required=True, help="job working directory")
    rt.set_defaults(func=cmd_retime)
    jn = sub.add_parser("join", help="join a split long video's rendered parts into one MP4")
    jn.add_argument("--group", required=True, help="the original video's folder (has group.json)")
    jn.set_defaults(func=cmd_join)
    cp = sub.add_parser("compile", help="join rendered episodes (or earlier compilations) into "
                                        "one video with chapters")
    cp.add_argument("items", nargs="+", help="project / split-video / compilation folder names")
    cp.add_argument("--title", default="", help="video title (default: Tổng hợp tập A–B)")
    cp.add_argument("--keep-order", action="store_true",
                    help="join in the given order (default: by episode number in the titles)")
    cp.add_argument("--storage", default="", help="storage folder (default: the app's)")
    cp.set_defaults(func=cmd_compile)

    pr.set_defaults(func=cmd_providers)

    au = sub.add_parser("autopilot", help="theo dõi tập mới: run the followed series' new "
                                          "episodes to the edit gate (never approves / renders)")
    au.add_argument("--once", action="store_true",
                    help="check once and exit — the only mode; the Windows scheduled task "
                         "repeats it every N hours")
    au.add_argument("--dry-run", action="store_true",
                    help="only list what would be fetched (nothing downloaded or recorded)")
    au.add_argument("--force", action="store_true",
                    help="check every followed series now, even when its check is not due")
    au.set_defaults(func=cmd_autopilot)

    dr = sub.add_parser("doctor", help="environment self-check (deps, GPU, fonts, keys)")
    dr.set_defaults(func=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> int:
    # Legacy Windows consoles default to cp1252, which can't print the pipeline's
    # Vietnamese log lines ("⚠ Câu …") — every CLI path prints them via log=print.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001 - non-console streams lack reconfigure
        pass
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
