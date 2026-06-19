"""Core data types: a subtitle Cue and a per-video Job (a working directory)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Cue:
    """One subtitle line. Times are absolute seconds from the start of the video."""

    index: int
    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class Job:
    """A single video's working directory and the canonical file paths within it.

    Every pipeline stage reads/writes files under ``work_dir`` so jobs are
    resumable and the human can stop at the edit gate and come back.
    """

    id: str
    work_dir: Path
    url: str | None = None
    metadata: dict = field(default_factory=dict)
    has_hardsubs: bool | None = None

    def __post_init__(self) -> None:
        self.work_dir = Path(self.work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)

    # canonical artifact paths -------------------------------------------------
    @property
    def source_video(self) -> Path:
        return self.work_dir / "source.mp4"

    @property
    def source_audio(self) -> Path:
        return self.work_dir / "source.wav"

    @property
    def cn_srt(self) -> Path:
        """Chinese subtitles (from OCR or ASR), pre-translation."""
        return self.work_dir / "cn.srt"

    @property
    def vi_srt(self) -> Path:
        """Vietnamese subtitles — THE editable file the human gate edits."""
        return self.work_dir / "vi.srt"

    @property
    def subs_ass(self) -> Path:
        """Styled subtitles burned into the final video."""
        return self.work_dir / "subs.ass"

    @property
    def tts_dir(self) -> Path:
        d = self.work_dir / "tts"
        d.mkdir(exist_ok=True)
        return d

    @property
    def output_video(self) -> Path:
        return self.work_dir / "output.mp4"
