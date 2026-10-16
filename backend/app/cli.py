"""Headless pipeline CLI — demonstrates the flow before the web UI exists, and
honours the human-edit gate.

    vizsup run URL [--srt cn.srt] [--translator deepseek --refine claude]
        -> download + Chinese subs + translate -> writes vi.srt, then STOPS.
           (Edit vi.srt — this is the gate.)

    vizsup render --workdir <dir> [--tts edge --voice ...]
        -> TTS the edited vi.srt + assemble the final output.mp4.

    vizsup providers   -> list available translation/TTS/ASR engines (JSON).

    vizsup doctor      -> environment self-check (ffmpeg, OCR/ASR deps, fonts, keys).

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
                                 fps=args.ocr_fps)
        else:
            print(f"[run] transcribing (ASR: {args.asr}) ...")
            asr.transcribe(job, provider=args.asr)
            translate.clear_ocr_provenance(job)   # non-OCR source → drop stale report
        # 2.3) snap the fresh cues to the voice (local Silero VAD) before translating
        if not getattr(args, "no_snap", False):
            from app.pipeline import snap

            try:
                snap.snap_srt(job, job.cn_srt, log=lambda m: print(f"[run] {m}"))
            except Exception as exc:  # noqa: BLE001 - an aid, never fatal
                print(f"[run] voice snap skipped ({exc})")

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
                              observed=observed)
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
        translate.translate(job, draft=args.translator, refine=args.refine, force=force, log=print)
        # 3.5) E-5: AI QA pass → qa_report.json (never edits vi.srt)
        if not getattr(args, "no_qa", False) and args.translator != "passthrough":
            from app.pipeline import brief, qa

            qa.review(job, args.refine or args.translator,
                      context={"_brief": brief.load_brief(job) or ""},
                      log=lambda m: print(f"[run] {m}"))

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
    shutil.copyfile(job.cn_srt, work_dir / "cn.before-retime.srt")
    if job.vi_srt.exists():
        shutil.copyfile(job.vi_srt, work_dir / "vi.before-retime.srt")
    asr.retime(job, log=lambda m: print(f"[retime] {m}"))
    if job.vi_srt.exists():
        print("[retime] vi.srt " + ("updated" if asr.sync_vi_times(job) else
                                    "kept (lines were split/merged — re-time them in the editor)"))
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

        print("[render] separating background music (Demucs) ...")
        try:
            separate.separate_background(job, log=print)   # before TTS (both heavy)
        except Exception as exc:  # noqa: BLE001 - degrade to mix in assemble
            print(f"[render] keep-music unavailable ({exc}) -> will mix instead")

    print(f"[render] synthesizing Vietnamese voice (tts={args.tts}) ...")
    segments = tts.synthesize(job, provider=args.tts, voice=args.voice, log=print)
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
    )
    print(f"[render] done -> {out}")
    return 0


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
    r.add_argument("--no-snap", action="store_true",
                   help="keep ASR/OCR cue times as-is (default: snap each cue edge to the nearest "
                        "voice start/stop within 1 s, local Silero VAD)")
    r.add_argument("--workdir", help="explicit working directory")
    r.set_defaults(func=cmd_run)

    rn = sub.add_parser("render", help="TTS the edited vi.srt + assemble output.mp4")
    rn.add_argument("--workdir", required=True, help="job working directory from 'run'")
    rn.add_argument("--tts", default=settings.vizsup_default_tts, help="TTS provider (default: edge)")
    rn.add_argument("--voice", default=None, help="voice id (e.g. vi-VN-NamMinhNeural)")
    rn.add_argument("--keep-audio", action="store_true", help="mix the dub over the original audio")
    rn.add_argument("--audio-mode", choices=["replace", "mix", "duck", "keep-music"], default=None,
                    help="audio: replace|mix|duck|keep-music (Demucs keeps the music). Overrides --keep-audio")
    rn.add_argument("--cover-hardsubs", action="store_true", help="legacy alias for --cover box")
    rn.add_argument("--cover", choices=["off", "box", "blur"], default=None,
                    help="hide leftover CN hardsubs: opaque box or a blurred bottom band")
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

    pr.set_defaults(func=cmd_providers)

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
