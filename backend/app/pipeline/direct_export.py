"""Export a locally authorized translation run and its description."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from app.ffmpegutil import ffprobe_bin
from app.fsutil import atomic_write_text, link_or_copy
from app.pipeline import approval, batch, brief, compilation, fit_estimate, series, translate, vi_lint
from app.pipeline.export import safe_name
from app.pipeline.srt import parse_srt, write_srt

ROOT = r"C:\Work\youtube\Video"
REQUEST = "_direct_export"
PREPARED = "direct_prepared_revision.txt"
READY = "direct_revision.txt"
RECEIPT = "direct_export.json"


def render_options(job, opts: dict, request: dict) -> dict:
    from app.providers.registry import voice_choices

    profile = series.profile_of(job)
    defaults = {"audio_mode": "duck", "orig_volume": 1.0, "dub_volume": 1.0,
                "rate": "+30%", "tts": opts.get("tts") or "edge",
                "frame": request.get("frame", "source"), "res": request.get("res") or "source",
                "upscale": request.get("upscale") or "fast", "preset": "sharp"}
    voice_defaults = request.get("voice_defaults") or {}
    if (voice := voice_defaults.get("voice")) and voice in voice_choices(defaults["tts"]):
        defaults["voice"] = voice
    if rate := fit_estimate.parse_rate(voice_defaults.get("rate")):
        defaults["rate"] = f"{round((rate - 1) * 100):+d}%"
    if voice_defaults.get("pitch") is not None:
        defaults["pitch"] = voice_defaults["pitch"]
    if job.metadata.get("has_hardsubs"):
        defaults["cover_mode"] = "blur"
    return batch.opts_for(job.work_dir, {
        **defaults, **(request.get("style_defaults") or {}),
        **{k: v for k, v in ((profile.render or {}) if profile else {}).items() if v is not None},
        # the branch's "Độ nét" (1440p for YouTube) is a choice about the destination: it wins
        **{k: request[k] for k in ("res", "upscale") if request.get(k)},
        "thumbnail": False,
    })


def repair(job, opts: dict, render_opts: dict, *, log, cancel) -> None:
    source, translated = parse_srt(job.cn_srt), parse_srt(job.vi_srt)
    if not translated:
        return
    if len(source) == len(translated):
        zh = [c.text for c in source]
    else:
        # the timing pass / merges change the line count (317 CN vs 313 VI on a real
        # Bilibili vlog): pair each VI line with the CN lines its time span overlaps
        zh = [" ".join(c.text for c in source if min(c.end, vi.end) > max(c.start, vi.start))
              for vi in translated]
    known = [word for name in translate.load_glossary(job).values()
             for word in re.findall(r"[A-Za-z]+", name)]
    rate = fit_estimate.parse_rate(render_opts.get("rate")) or fit_estimate.PREGATE_RATE

    def defects(text: str, slot: float) -> tuple[int, int]:
        return (len(vi_lint.HAN.findall(text)) + len(vi_lint.latin_words(text, known)),
                max(0, len(text.split()) - fit_estimate.word_budget(slot, rate)))

    selected, items = [], []
    for i, (cn, vi) in enumerate(zip(zh, translated, strict=True)):
        slot = fit_estimate.slot_seconds(translated, i)
        issues = defects(vi.text, slot)
        if not any(issues):
            continue
        selected.append((i, slot))
        items.append({"zh": cn, "vi": vi.text,
                      "before": translated[i - 1].text if i else "",
                      "after": translated[i + 1].text if i + 1 < len(translated) else ""})
    if not items:
        log("✓ Không có câu đánh dấu cần sửa")
        return
    try:
        answers = translate.retranslate_lines(
            items, brief=brief.load_brief(job) or "",
            request="Sửa riêng lỗi còn sót chữ nguồn hoặc câu quá dài. Giữ tên riêng, ý và xưng hô. "
                    "Rút gọn câu cho giọng đọc.",
            provider=opts.get("refine") or opts.get("translator", "openrouter"), cancel=cancel)
    except Exception as exc:  # noqa: BLE001 - optional pass; retain the existing translation
        log(f"⚠ Không dịch lại câu đánh dấu ({type(exc).__name__}). Dùng bản dịch hiện tại.")
        return
    if cancel():
        raise InterruptedError("Đã hủy.")
    fixed = 0
    for (i, slot), text in zip(selected, answers, strict=True):
        text = " ".join(text.split())
        current = defects(text, slot)
        if text and not any(current):
            translated[i] = replace(translated[i], text=text)
            fixed += 1
    if fixed:
        write_srt(translated, job.vi_srt)
    log(f"✓ Đã sửa {fixed}/{len(items)} câu đánh dấu")


def read_result(job) -> Path | None:
    try:
        receipt = json.loads((job.work_dir / RECEIPT).read_text(encoding="utf-8"))
        dest = Path(receipt["path"])
        video = (dest / f"{dest.name}.mp4").stat()
        if receipt["file_id"] == [video.st_dev, video.st_ino] and (dest / "Mô tả.txt").is_file():
            return dest
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _check_video(path: Path) -> None:
    probe = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-show_entries", "stream=codec_type:format=duration",
         "-of", "json", str(path)], capture_output=True, text=True, timeout=30)
    try:
        data = json.loads(probe.stdout)
        tracks = {s["codec_type"] for s in data["streams"]}
        if probe.returncode == 0 and {"video", "audio"} <= tracks and float(data["format"]["duration"]) > 0:
            return
    except (ValueError, KeyError, TypeError):
        pass
    raise RuntimeError("MP4 thành phẩm cần có hình, tiếng và thời lượng hợp lệ.")


def deliver(job, source: Path, request: dict, *, brief_jobs=(), log, cancel) -> Path:
    from app.pipeline import autopilot, thumbnail

    existing = read_result(job)
    if existing:
        return existing
    _check_video(source)
    root = Path(request["folder"])
    root.mkdir(parents=True, exist_ok=True)
    original = job.metadata.get("title") or job.id
    title = next((friendly for item in (job, *brief_jobs)
                  if (friendly := autopilot.friendly_title(item.work_dir, original))), original)
    name = safe_name(title, job.id, limit=64)
    notes = []
    for item in (job, *brief_jobs):
        if value := brief.load_brief(item):
            section = re.search(r"(?ims)^Thể loại & nội dung:\s*(.*?)(?=^Người nói & xưng hô:|\Z)",
                                value)
            notes.append(section[1].strip() if section else value)
    episode = compilation.episode_range(original)
    lines = [title, f"Tên gốc: {original}"]
    if episode:
        a, b = episode
        lines.append(f"Tập: {a}" if a == b else f"Tập: {a}–{b}")
    if job.url:
        from app.pipeline.download import normalize_url

        lines.append(f"Nguồn: {normalize_url(job.url)}")
    lines.extend(f"\n{note}" for note in dict.fromkeys(notes))

    with TemporaryDirectory(dir=root, prefix=".vizsup-") as temp:
        stage = Path(temp) / "ready"
        stage.mkdir()
        atomic_write_text(stage / "Mô tả.txt", "\n".join(lines) + "\n")
        if request.get("frames"):
            from PIL import Image
            from app.pipeline.assemble import probe_size

            size = probe_size(source)
            for i, candidate in enumerate(thumbnail.candidates(source, cancel=cancel), 1):
                frame = thumbnail.read_frame(source, candidate.t, size) if size else candidate.frame
                if frame is not None:
                    Image.fromarray(frame).save(stage / f"Khung hình {i:02}.jpg", quality=95)
        if cancel():
            raise InterruptedError("Đã hủy.")
        dest = root / name
        if dest.exists():
            dest = root / f"{name} - {job.id}"
        suffix = 2
        while dest.exists():
            dest = root / f"{name} - {job.id}-{suffix}"
            suffix += 1
        staged_video = stage / f"{dest.name}.mp4"
        link_or_copy(source, staged_video)
        if cancel():
            raise InterruptedError("Đã hủy.")
        stat = staged_video.stat()
        atomic_write_text(job.work_dir / RECEIPT, json.dumps(
            {"path": str(dest), "file_id": [stat.st_dev, stat.st_ino]}, ensure_ascii=False))
        stage.rename(dest)
    log(f"✓ Đã xuất {dest}")
    return dest


def authorize(job) -> None:
    """Approve only the revision sealed by this run at the end of translation."""
    ready = (job.work_dir / READY).read_text(encoding="utf-8").strip()
    if ready != approval.revision(job):
        raise RuntimeError("Phụ đề đã thay đổi. Mở dự án để duyệt bản sửa trước khi dựng.")
    if not approval.is_approved(job):
        approval.approve(job, auto=True)


def matches_prepared(job) -> bool:
    try:
        prepared = (job.work_dir / PREPARED).read_text(encoding="utf-8").strip()
        return prepared == approval.revision(job)
    except OSError:
        return False
