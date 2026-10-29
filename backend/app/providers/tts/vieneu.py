"""VieNeu-TTS — free Vietnamese voices that run on this machine (CPU, ONNX), no key, no
network once the model is downloaded (~580 MB, Hugging Face cache, first use only).

Owner's picks after a listening test (2026-10-29, docs/plan/H-overnight-log.md): Quốc Tuấn,
Trúc Ly, Adam bựa, Ngọc Huyền out of 25 presets. Offered next to the chosen provider's
voices as ``vieneu:<name>`` (registry.voice_choices / split_voice), so one voice list on
the render page holds both. Install: ``uv pip install --no-deps vieneu==3.8.3`` plus
sea-g2p, soundfile, soxr, kaldi-native-fbank — never vieneu's own onnxruntime pin: it would
replace onnxruntime-directml (the OCR GPU path).

The engine is forced to ONNX / CPU (with CUDA PyTorch present VieNeu would pick a GPU path
that needs transformers and competes with Demucs for 8 GB VRAM). Output carries VieNeu's
inaudible AI watermark (default on). Speed: the ``rate`` ("+10%") is applied with atempo.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
import threading
from pathlib import Path

from app.providers.base import TTSProvider, _installed

VOICES = ["Quốc Tuấn", "Trúc Ly", "Ngọc Huyền", "Adam bựa"]
DEFAULT_VOICE = "Trúc Ly"
GENDER = {"Quốc Tuấn": "m", "Adam bựa": "m", "Trúc Ly": "f", "Ngọc Huyền": "f"}

_engine = None
_lock = threading.Lock()


def _tempo(rate: str | None) -> float:
    """'+10%' → 1.10, '-5%' → 0.95; clamped to atempo's comfortable range. Pure."""
    m = re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?)\s*%\s*", rate or "")
    return min(2.0, max(0.5, 1 + float(m.group(1)) / 100)) if m else 1.0


def _get_engine():
    global _engine
    with _lock:
        if _engine is None:
            from vieneu import Vieneu

            _engine = Vieneu(mode="v3turbo", backend="onnx", device="cpu")
        return _engine


class VieNeuTTS(TTSProvider):
    name = "vieneu"
    display_name = "VieNeu (trên máy)"
    cost_note = "free · runs on this computer · Quốc Tuấn / Trúc Ly / Ngọc Huyền / Adam bựa"
    unavailable_note = "chưa cài vieneu"
    local_voices = True          # listed next to any provider's voices (registry)

    def available(self) -> bool:
        return _installed("vieneu") and _installed("soundfile")

    def voices(self) -> list[str]:
        return list(VOICES)

    def synthesize(self, text, out_path, *, voice=None, rate=None) -> Path:
        import soundfile as sf

        from app.ffmpegutil import ffmpeg_bin

        tts = _get_engine()
        with _lock:                               # one ONNX session: one line at a time
            wav = tts.infer(text, voice=voice if voice in VOICES else DEFAULT_VOICE)
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "line.wav"
            sf.write(str(raw), wav, tts.sample_rate)
            af = ["-af", f"atempo={_tempo(rate):.3f}"] if abs(_tempo(rate) - 1) > 1e-3 else []
            subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-i", str(raw), *af,
                            "-c:a", "libmp3lame", "-b:a", "96k", str(out)], check=True)
        return out
