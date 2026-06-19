"""Editor-side cue model: pairs Vietnamese (editable) with Chinese (reference).
Bridges the backend Cue/SRT utilities and the editor widgets.
"""
from __future__ import annotations

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


def load_pair(job) -> list[EditorCue]:
    """Load vi.srt (editable) aligned with cn.srt (reference) by order."""
    vi = parse_srt(job.vi_srt) if job.vi_srt.exists() else []
    zh = parse_srt(job.cn_srt) if job.cn_srt.exists() else []
    out: list[EditorCue] = []
    for i, c in enumerate(vi):
        zh_text = zh[i].text if i < len(zh) else ""
        out.append(EditorCue(start=c.start, end=c.end, vi=c.text, zh=zh_text))
    return out


def save_vi(job, cues: list[EditorCue]) -> None:
    rows = [Cue(index=i + 1, start=c.start, end=c.end, text=c.vi) for i, c in enumerate(cues)]
    write_srt(rows, job.vi_srt)
