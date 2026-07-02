"""Editor-side cue model: pairs Vietnamese (editable) with Chinese (reference).
Bridges the backend Cue/SRT utilities and the editor widgets.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from desktop import paths  # noqa: F401
from app.models import Cue  # noqa: E402
from app.pipeline.srt import parse_srt, write_srt  # noqa: E402

MAX_WPS = 5.0  # words/sec above which a line is "too long to dub"


@dataclass
class EditorCue:
    start: float
    end: float
    vi: str
    zh: str = ""
    gain_db: float = 0.0          # per-line dub volume trim
    mute: bool = False            # drop this line's dub (keep the subtitle)
    style: dict | None = None     # per-line subtitle style override (sparse)

    @property
    def dur(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def est_speak(self) -> float:
        """Rough estimate of spoken length for the VI text (sec)."""
        words = len(self.vi.split())
        return words / MAX_WPS if words else 0.0

    @property
    def too_long(self) -> bool:
        return self.dur > 0 and self.est_speak > self.dur


def fmt_time(s: float) -> str:
    s = max(0.0, s)
    m = int(s // 60)
    sec = s - m * 60
    ss = int(sec)
    d = int((sec - ss) * 10 + 1e-4)
    return f"{m:02d}:{ss:02d}.{d}"


def parse_time(text: str) -> float | None:
    text = text.strip()
    m = re.match(r"^(\d+):(\d+(?:\.\d+)?)$", text)
    if m:
        return int(m.group(1)) * 60 + float(m.group(2))
    try:
        return float(text)
    except ValueError:
        return None


def load_overrides(job) -> list[dict]:
    """Per-cue dub overrides — delegates to the shared backend loader."""
    from app.pipeline import sidecars

    return sidecars.load_overrides(job)


def load_style_overrides(job) -> dict:
    """Per-cue style overrides (string keys) — delegates to the shared loader."""
    from app.pipeline import sidecars

    return sidecars.load_style_overrides(job)


def _zh_ref_path(job):
    return job.work_dir / "zh_ref.json"


def _default_zh(job, n: int) -> list[str]:
    """The naive reference pairing: row i ↔ cn.srt line i."""
    zh = parse_srt(job.cn_srt) if job.cn_srt.exists() else []
    return [zh[i].text if i < len(zh) else "" for i in range(n)]


def load_pair(job) -> list[EditorCue]:
    """Load vi.srt (editable) aligned with the Chinese reference + dub & style
    overrides. The zh column prefers ``zh_ref.json`` (written by save_vi when
    structural edits shifted rows off the naive cn.srt index pairing); a
    missing/length-mismatched sidecar degrades to the index pairing — never
    worse than the old behavior."""
    vi = parse_srt(job.vi_srt) if job.vi_srt.exists() else []
    zh_texts = _default_zh(job, len(vi))
    try:
        data = json.loads(_zh_ref_path(job).read_text(encoding="utf-8"))
        if isinstance(data, list) and len(data) == len(vi):
            zh_texts = [str(x) for x in data]
    except (OSError, ValueError):
        pass
    from app.pipeline import sidecars

    ov = sidecars.load_overrides(job)
    sov = sidecars.load_style_overrides(job)
    out: list[EditorCue] = []
    for i, c in enumerate(vi):
        o = ov[i] if i < len(ov) else {}
        out.append(EditorCue(start=c.start, end=c.end, vi=c.text, zh=zh_texts[i],
                             gain_db=float(o.get("gain_db", 0.0)), mute=bool(o.get("mute", False)),
                             style=sov.get(str(i + 1))))
    return out


def save_vi(job, cues: list[EditorCue]) -> None:
    from app.pipeline import sidecars

    rows = [Cue(index=i + 1, start=c.start, end=c.end, text=c.vi) for i, c in enumerate(cues)]
    write_srt(rows, job.vi_srt)
    sidecars.save_overrides(job, [{"gain_db": round(c.gain_db, 2), "mute": c.mute} for c in cues])
    sidecars.save_style_overrides(job, {str(i + 1): c.style for i, c in enumerate(cues) if c.style})
    # zh reference sidecar: rows are positional, so split/merge/delete shift later
    # rows off the naive cn.srt pairing — persist the editor's zh column whenever
    # it differs (and drop the file when the pairing is redundant again)
    zh_list = [c.zh for c in cues]
    p = _zh_ref_path(job)
    if zh_list != _default_zh(job, len(cues)):
        p.write_text(json.dumps(zh_list, ensure_ascii=False), encoding="utf-8")
    elif p.exists():
        p.unlink()


# --- pure structural edits (policy lives HERE, headless-testable; the editor
# widget methods are thin wrappers) ------------------------------------------

def split_cue(c: EditorCue) -> tuple[EditorCue, EditorCue]:
    """Split at the time midpoint, words halved; both halves inherit the per-line
    dub + style settings (P0-2 / C-3 policy); the zh reference stays on the
    first half."""
    mid = (c.start + c.end) / 2
    words = c.vi.split()
    half = len(words) // 2
    a = EditorCue(start=c.start, end=mid, vi=" ".join(words[:half]), zh=c.zh,
                  gain_db=c.gain_db, mute=c.mute, style=c.style)
    b = EditorCue(start=mid, end=c.end, vi=" ".join(words[half:]), zh="",
                  gain_db=c.gain_db, mute=c.mute, style=c.style)
    return a, b


def merge_cues(a: EditorCue, b: EditorCue) -> EditorCue:
    """Merge b into a: texts join (zh gains a space only between two non-empty
    parts), mute ORs (never silently un-mute), the larger |gain| wins, and the
    FIRST cue's style is kept (C-3 policy)."""
    zh = f"{a.zh} {b.zh}".strip() if (a.zh and b.zh) else (a.zh + b.zh).strip()
    return EditorCue(
        start=a.start, end=b.end,
        vi=(a.vi + " " + b.vi).strip(), zh=zh,
        gain_db=a.gain_db if abs(a.gain_db) >= abs(b.gain_db) else b.gain_db,
        mute=a.mute or b.mute,
        style=a.style,
    )
