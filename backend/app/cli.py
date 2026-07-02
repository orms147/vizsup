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
    jid = hashlib.sha1(args.url.encode()).hexdigest()[:10]
    work_dir = Path(args.workdir) if args.workdir else settings.vizsup_storage_dir / jid
    job = _load_job(work_dir, url=args.url)
    force = args.force

    # 1) source — the same URL maps to the same workdir (sha1), so re-runs resume
    if job.source_video.exists() and not force:
        _skip("source.mp4", "download")
    elif args.file:
        shutil.copy(args.file, job.source_video)
        job.metadata.setdefault("platform", "local")
        print(f"[run] using local file -> {job.source_video}")
    else:
        print(f"[run] downloading {args.url} ...")
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
                src = "ocr" if hardsub_detect.detect_hardsubs(job, crop=args.ocr_region) else "asr"
                print(f"[run] hardsubs {'found -> OCR' if src == 'ocr' else 'not found -> ASR'}")
            except Exception as exc:  # noqa: BLE001 - degrade, don't abort the run
                print(f"[run] detection unavailable ({exc}) -> ASR")
                src = "asr"
            job.metadata["has_hardsubs"] = job.has_hardsubs
            _save_meta(job)  # metadata was saved before detection — persist the verdict

        if src == "srt":
            write_srt(parse_srt(Path(args.srt)), job.cn_srt)
            print(f"[run] using provided Chinese subtitles -> {job.cn_srt}")
        elif src == "ocr":
            from app.pipeline import ocr

            print(f"[run] reading hardsubs (OCR, region: {args.ocr_region}) ...")
            ocr.extract_hardsubs(job, crop=args.ocr_region)
        else:
            print(f"[run] transcribing (ASR: {args.asr}) ...")
            asr.transcribe(job, provider=args.asr)

    # 3) translate — never silently overwrite the gate's edited vi.srt
    if job.vi_srt.exists() and not force:
        _skip("vi.srt", "translate (edits preserved)")
    else:
        print(f"[run] translating CN->VI (draft={args.translator}, refine={args.refine}) ...")
        translate.translate(job, draft=args.translator, refine=args.refine, force=force, log=print)

    print("\n" + "=" * 64)
    print("EDIT GATE — review/fix the Vietnamese subtitles, then render:")
    print(f"  subtitles: {job.vi_srt}")
    print(f"  render:    vizsup render --workdir \"{job.work_dir}\"")
    print("=" * 64)
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

    print(f"[render] synthesizing Vietnamese voice (tts={args.tts}) ...")
    segments = tts.synthesize(job, provider=args.tts, voice=args.voice, log=print)
    print(f"[render] {len(segments)} segments; assembling with FFmpeg ...")
    out = assemble.assemble(
        job,
        segments,
        audio_mode="mix" if args.keep_audio else "replace",
        cover_hardsubs=args.cover_hardsubs,
        font=args.font,
        log=print,  # surface the "câu QUÁ DÀI" fit warnings headless too
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
    r.add_argument("url", help="Douyin/Bilibili URL (or any yt-dlp URL)")
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
    r.add_argument("--translator", default="passthrough", help="draft translator (default: passthrough)")
    r.add_argument("--refine", default=None, help="optional second-pass refiner (e.g. claude)")
    r.add_argument("--asr", default=settings.vizsup_default_asr, help="ASR provider for the no-hardsub path")
    r.add_argument("--workdir", help="explicit working directory")
    r.set_defaults(func=cmd_run)

    rn = sub.add_parser("render", help="TTS the edited vi.srt + assemble output.mp4")
    rn.add_argument("--workdir", required=True, help="job working directory from 'run'")
    rn.add_argument("--tts", default=settings.vizsup_default_tts, help="TTS provider (default: edge)")
    rn.add_argument("--voice", default=None, help="voice id (e.g. vi-VN-NamMinhNeural)")
    rn.add_argument("--keep-audio", action="store_true", help="mix the dub over the original audio")
    rn.add_argument("--cover-hardsubs", action="store_true", help="opaque box to hide leftover CN hardsubs")
    rn.add_argument("--font", default="Be Vietnam Pro", help="subtitle font (Vietnamese-diacritic)")
    rn.set_defaults(func=cmd_render)

    pr = sub.add_parser("providers", help="list available providers (JSON)")
    pr.set_defaults(func=cmd_providers)

    dr = sub.add_parser("doctor", help="environment self-check (deps, GPU, fonts, keys)")
    dr.set_defaults(func=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
