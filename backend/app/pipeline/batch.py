"""Dựng hàng loạt — render several APPROVED projects one after another with the same
settings, in the background, while the user reviews the next episode.

The gate is unchanged: only a project whose current subtitles are the approved revision
is offered, and the render itself re-checks approval (desktop/worker.py), so an episode
edited after it was queued is skipped, never rendered with unapproved text.

Settings ("cài đặt dựng") are what the render page sends: voice, speed, audio mode,
volumes, cover, frame, quality, credit / AI notes, and the subtitle look. Every real
render saves them next to its project (``render_opts.json``) so a later batch can say
"giống tập 12". Projects rendered before that file existed are rebuilt from render.json
(cover, frame, quality, audio), voice.json (voice, speed, audio card) and style.json.
A queued project keeps its OWN subtitle look when it has one (style.json is part of the
approved revision); the template's look is used only when it has none."""
from __future__ import annotations

import json
from pathlib import Path

from app.models import Job
from app.fsutil import atomic_write_text

RENDER_OPTS = "render_opts.json"
_TRANSIENT = ("burn_cues", "preview")     # per run, never part of a template


def _read(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def save_opts(work_dir: Path, opts: dict) -> None:
    """Remember the settings of a real render (not the quick proof)."""
    if opts.get("preview"):
        return
    clean = {k: v for k, v in opts.items() if k not in _TRANSIENT and not k.startswith("_")}
    try:
        atomic_write_text((Path(work_dir) / RENDER_OPTS), json.dumps(clean, ensure_ascii=False, indent=1),
                                                  encoding="utf-8")
    except OSError:
        return
    try:                                   # Bộ phim: the first real render sets the series' look
        from app.pipeline import series

        series.learn_render(Path(work_dir), clean)
    except Exception:  # noqa: BLE001
        pass


def _rate_text(rate) -> str | None:
    """voice.json keeps the speed as a factor (1.1); the render wants "+10%"."""
    try:
        return f"{int(round((float(rate) - 1) * 100)):+d}%"
    except (TypeError, ValueError):
        return None


def template_from(work_dir: Path) -> dict | None:
    """The render settings of a project that was rendered, or None."""
    d = Path(work_dir)
    saved = _read(d / RENDER_OPTS)
    if saved:
        return saved
    info = _read(d / "render.json")
    if not info:
        return None
    voice = _read(d / "voice.json")
    style = _read(d / "style.json")
    job_opts = _read(d / "job.json").get("opts") or {}
    out = {"voice": voice.get("voice"), "rate": _rate_text(voice.get("rate")) or "+0%",
           "audio_mode": voice.get("audio_mode") or info.get("audio_mode") or "replace",
           "orig_volume": 1.0, "dub_volume": 1.0, "cover_mode": info.get("cover_mode"),
           "frame": info.get("frame") or "source", "res": info.get("res") or "source",
           "preset": info.get("preset"), "credit_overlay": False, "ai_label": False}
    if job_opts.get("tts"):
        out["tts"] = job_opts["tts"]
    for key in ("font", "size", "style"):
        if style.get(key) is not None:
            out[key] = style[key]
    return out


def opts_for(work_dir: Path, template: dict) -> dict:
    """The template + the project's own subtitle look (style.json) when it has one."""
    opts = {k: v for k, v in (template or {}).items() if k not in _TRANSIENT}
    style = _read(Path(work_dir) / "style.json")
    for key in ("font", "size", "style"):
        if style.get(key) is not None:
            opts[key] = style[key]
    if opts.get("audio_mode") == "mix":            # the retired card → its successor
        opts["audio_mode"] = "duck"
    return opts


def render_state(work_dir: Path) -> str | None:
    """"new" (approved, never rendered), "stale" (approved again after the last render)
    or None (not approved, or the render is current). Never creates folders."""
    from app.pipeline import approval

    d = Path(work_dir)
    if not (d / "vi.srt").exists() or not (d / "approved.json").exists():
        return None
    if not approval.is_approved(Job(id=d.name, work_dir=d)):
        return None
    out = d / "output.mp4"
    if not out.exists():
        return "new"
    # a render that names its revision is current exactly when that is the approved one —
    # approving the same text again later (e.g. a project copied in) is not "Cần dựng lại"
    made_from = _read(d / "render.json").get("revision")
    if made_from:
        return None if made_from == _read(d / "approved.json").get("revision") else "stale"
    return "stale" if out.stat().st_mtime < (d / "approved.json").stat().st_mtime else None


_MODES = {"keep_music": "Giữ nhạc nền & hiệu ứng", "duck": "Giữ âm gốc + lồng tiếng",
          "replace": "Chỉ giọng Việt", "original": "Không lồng tiếng — chỉ phụ đề"}
_PRESETS = {"light": "Nhẹ", "balanced": "Cân bằng", "sharp": "Nét"}


def describe(opts: dict | None) -> str:
    """One line for the dialog: "HoaiMy · +10% · Giữ nhạc nền · 1080p Nét · 9:16"."""
    if not opts:
        return "Chưa có cài đặt — dựng một video trước để làm mẫu."
    bits = []
    mode = opts.get("audio_mode") or "replace"
    if mode != "original":
        voice = str(opts.get("voice") or "giọng mặc định").replace("vi-VN-", "").replace("Neural", "")
        bits.append(voice)
        if opts.get("rate") and opts["rate"] not in ("+0%", "-0%"):
            bits.append(str(opts["rate"]))
    bits.append(_MODES.get(mode, mode))
    res = opts.get("res") or "source"
    bits.append("Cỡ gốc" if res == "source" else f"{res}p")
    bits.append(_PRESETS.get(opts.get("preset") or "sharp", "Nét"))
    if (opts.get("frame") or "source") == "tiktok":
        bits.append("khung 9:16")
    wm = opts.get("watermark") or {}
    if wm.get("on") and str(wm.get("text") or "").strip():
        bits.append(f"watermark “{str(wm['text']).strip()[:24]}”")
    if (opts.get("episode_card") or {}).get("on"):
        bits.append("thẻ tập")
    return " · ".join(bits)
