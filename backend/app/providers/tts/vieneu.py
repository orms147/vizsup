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

import os
import queue
import re
import subprocess
import tempfile
import threading
from pathlib import Path

from app.providers.base import TTSProvider, _installed

VOICES = ["Quốc Tuấn", "Trúc Ly", "Ngọc Huyền", "Adam bựa"]
DEFAULT_VOICE = "Trúc Ly"
GENDER = {"Quốc Tuấn": "m", "Adam bựa": "m", "Trúc Ly": "f", "Ngọc Huyền": "f"}

# Parallel lines (2026-11-02): one ONNX session speaks ONE line at a time and, with the engine's own
# default of 8 intra-op threads, left a 16-core / 32-thread CPU at ~25 % (1592 lines took ~25 min).
# A pool of several engines — each with fewer threads — speaks several lines at once. Each engine
# holds ~0.6 GB, so the count is capped by cores and by the free memory.
_pool: queue.Queue = queue.Queue()
_made = 0
_lock = threading.Lock()
MAX_WORKERS = 6
ENGINE_GB = 0.8


_gpu_failed = False


def use_gpu() -> bool:
    """Speak on the graphics card (PyTorch / CUDA): ``VIZSUP_VIENEU_DEVICE`` = cpu | cuda | auto
    (default auto: a CUDA torch + ``transformers`` installed, and no earlier load failure).
    Measured 2026-11-02 on an RTX 4070 Laptop: 0.24 s/line vs 0.62 on 6 CPU engines."""
    mode = (os.environ.get("VIZSUP_VIENEU_DEVICE") or "auto").strip().lower()
    if mode == "cpu" or _gpu_failed:
        return False
    try:
        import torch

        if not torch.cuda.is_available():
            return False
        import transformers  # noqa: F401 - the PyTorch engine needs it
    except Exception:  # noqa: BLE001 - no CUDA torch / no transformers: the CPU engines
        return False
    return True


def workers() -> int:
    """How many lines are spoken at once: ``VIZSUP_VIENEU_WORKERS`` when set (1 = the old one at a
    time), else one per 4 physical cores up to ``MAX_WORKERS`` and while ~0.8 GB of memory each
    stays free. Pure apart from reading the machine."""
    if use_gpu():
        return 1                         # one engine on the card: it is already faster than 6 CPU ones
    env = (os.environ.get("VIZSUP_VIENEU_WORKERS") or "").strip()
    if env.isdigit():
        return max(1, min(8, int(env)))
    n = max(1, min(MAX_WORKERS, (os.cpu_count() or 8) // 5))       # 32 threads → 6
    try:
        import psutil

        n = min(n, max(1, int(psutil.virtual_memory().available / 2**30 / ENGINE_GB) - 1))
    except Exception:  # noqa: BLE001 - no psutil: keep the core-based count
        pass
    return n


def _threads_each(n: int) -> int:
    physical = max(1, (os.cpu_count() or 8) // 2)
    return max(2, min(8, physical // max(1, n)))


def _tempo(rate: str | None) -> float:
    """'+10%' → 1.10, '-5%' → 0.95; clamped to atempo's comfortable range. Pure."""
    m = re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?)\s*%\s*", rate or "")
    return min(2.0, max(0.5, 1 + float(m.group(1)) / 100)) if m else 1.0


REPO = "pnnbao-ump/VieNeu-TTS-v3-Turbo"


def model_cached() -> bool:
    """The model is already in the Hugging Face cache (no ~580 MB download on first use)."""
    try:
        from huggingface_hub.constants import HF_HUB_CACHE
    except Exception:  # noqa: BLE001
        return False
    snap = Path(HF_HUB_CACHE) / ("models--" + REPO.replace("/", "--")) / "snapshots"
    return snap.is_dir() and any(snap.iterdir())


def _acquire():
    """An engine nobody is using: a free one from the pool, a new one while fewer than
    ``workers()`` exist, else wait for one to come back."""
    global _made, _gpu_failed
    try:
        return _pool.get_nowait()
    except queue.Empty:
        pass
    with _lock:
        n = workers()
        if use_gpu() and _made < 1:
            from vieneu import Vieneu

            from app.heavy import heavy_task

            try:
                _made += 1
                with heavy_task("VieNeu (nạp giọng lên card)"):
                    return Vieneu(mode="v3turbo", device="cuda")
            except Exception:  # noqa: BLE001 - out of memory, missing weights: speak on the CPU
                _made -= 1
                _gpu_failed = True
                n = workers()
        if _made < n:
            _made += 1
            from vieneu import Vieneu

            try:
                return Vieneu(mode="v3turbo", backend="onnx", device="cpu", threads=_threads_each(n))
            except Exception:
                _made -= 1
                raise
    return _pool.get()


def _release(engine) -> None:
    _pool.put(engine)


# Batched lines on the graphics card (2026-11-11): the PyTorch engine runs many lines through
# ONE forward (``infer_batch``, bucketed by length). Measured on the RTX 4070 Laptop with 96 real
# subtitle lines (~21 chars): one line at a time 0.37 s/line → 0.087 (8 per forward) → 0.073 (16)
# → 0.061 (32), peak 0.9 GB of VRAM — a 1019-line episode: ~6 min → ~1 min of speaking.
BATCH = 32                    # lines sharing a forward
GROUP = 128                   # lines per call: progress, cancel and the manifest move between calls
ENCODERS = 8                  # ffmpeg (tempo + mp3) processes at once


class NoAudioReceived(Exception):
    """The engine returned no samples for a line (nothing it can say): the pipeline skips that
    line's dub, as it does for edge-tts' error of the same name (pipeline/tts._unvoiceable)."""


def _save(wav, rate_hz: int, out_path, rate: str | None) -> Path:
    """One waveform → ``out_path`` (mp3), the speed (``rate``) applied with atempo."""
    import soundfile as sf

    from app.ffmpegutil import ffmpeg_bin

    if wav is None or len(wav) == 0:
        raise NoAudioReceived("VieNeu không trả âm thanh cho câu này")
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "line.wav"
        sf.write(str(raw), wav, rate_hz)
        af = ["-af", f"atempo={_tempo(rate):.3f}"] if abs(_tempo(rate) - 1) > 1e-3 else []
        subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-i", str(raw), *af,
                        "-c:a", "libmp3lame", "-b:a", "96k", str(out)], check=True)
    return out


def _speak_group(engine, texts: list[str], voice: str) -> list:
    """A waveform (or the exception) per text: one batched call on the PyTorch engine; when
    that fails as a whole (out of memory, one bad line) or on the CPU engine, line by line."""
    if getattr(engine, "backend", "") == "pytorch" and len(texts) > 1:
        try:
            return list(engine.infer_batch(texts, voice=voice, batch_size=BATCH))
        except Exception:  # noqa: BLE001 - fall through: each line on its own
            pass
    out: list = []
    for t in texts:
        try:
            out.append(engine.infer(t, voice=voice))
        except Exception as exc:  # noqa: BLE001 - reported for that line only
            out.append(exc)
    return out


def prewarm() -> None:
    """Load the GPU engine in the background (the render page calls this when a VieNeu voice is
    chosen): the ~25 s model load is over before the owner presses Dựng video. No-op on the CPU
    path, when the model is not downloaded yet, or when an engine already exists."""
    if os.environ.get("VIZSUP_VIENEU_PREWARM", "1") == "0":
        return
    if _made or not use_gpu() or not model_cached():
        return

    def load() -> None:
        try:
            _release(_acquire())
        except Exception:  # noqa: BLE001 - the render reports a real failure itself
            pass

    threading.Thread(target=load, name="vieneu-prewarm", daemon=True).start()


class VieNeuTTS(TTSProvider):
    name = "vieneu"
    display_name = "VieNeu (trên máy)"
    cost_note = "free · runs on this computer · Quốc Tuấn / Trúc Ly / Ngọc Huyền / Adam bựa"
    unavailable_note = "chưa cài vieneu"
    local_voices = True          # listed next to any provider's voices (registry)

    @property
    def parallel(self) -> int:
        """Lines spoken at once (pipeline/tts.py runs that many ``synthesize`` calls together)."""
        return workers()

    def available(self) -> bool:
        return _installed("vieneu") and _installed("soundfile")

    def voices(self) -> list[str]:
        return list(VOICES)

    @property
    def supports_batch(self) -> bool:
        """On the graphics card the whole job goes through ``synthesize_many`` (batched
        forwards); the CPU engines keep the parallel per-line path."""
        return use_gpu()

    def synthesize(self, text, out_path, *, voice=None, rate=None) -> Path:
        tts = _acquire()
        try:                                      # one ONNX session = one line at a time
            wav = tts.infer(text, voice=voice if voice in VOICES else DEFAULT_VOICE)
            rate_hz = tts.sample_rate
        finally:
            _release(tts)
        return _save(wav, rate_hz, out_path, rate)

    def synthesize_many(self, items, *, voice=None, rate=None, concurrency=3,
                        retry_delays=(1.0, 4.0), on_done=None, cancel=None):
        """``GROUP`` lines per engine call, ``BATCH`` per forward; the mp3 encodes of a group
        run on a small pool while ``on_done`` stays on THIS thread (the pipeline's manifest
        writes are serialized). ``cancel()`` is checked between groups: the lines not started
        are reported as InterruptedError and every finished one is kept."""
        from concurrent.futures import ThreadPoolExecutor

        v = voice if voice in VOICES else DEFAULT_VOICE
        results: list[tuple[str, Exception | None]] = []

        def done(key: str, exc: Exception | None) -> None:
            results.append((key, exc))
            if on_done:
                on_done(key, exc)

        pending = list(items)
        with ThreadPoolExecutor(max_workers=ENCODERS) as pool:
            while pending:
                if cancel and cancel():
                    for key, _t, _p in pending:
                        done(key, InterruptedError())
                    break
                tts = _acquire()
                if getattr(tts, "backend", "") != "pytorch":
                    # the card refused the model (out of memory…): the CPU engines, several
                    # lines at once, each line through the plain ``synthesize``
                    _release(tts)
                    with ThreadPoolExecutor(max_workers=workers()) as cpu:
                        futs = [(key, cpu.submit(self.synthesize, t, p, voice=v, rate=rate))
                                for key, t, p in pending]
                        for key, fut in futs:
                            try:
                                fut.result()
                                done(key, None)
                            except Exception as exc:  # noqa: BLE001 - that line only
                                done(key, exc)
                    break
                group, pending = pending[:GROUP], pending[GROUP:]
                from app.heavy import heavy_task

                try:
                    # one heavy job on the card at a time (the AI upscale of another render…)
                    with heavy_task("VieNeu (đọc lồng tiếng)", cancel=cancel):
                        wavs = _speak_group(tts, [t for _k, t, _p in group], v)
                    rate_hz = tts.sample_rate
                except InterruptedError:         # cancelled while waiting for the card
                    for key, _t, _p in group + pending:
                        done(key, InterruptedError())
                    break
                finally:
                    _release(tts)
                jobs = [None if isinstance(w, Exception) else pool.submit(_save, w, rate_hz, p, rate)
                        for w, (_k, _t, p) in zip(wavs, group)]
                for (key, _t, _p), w, fut in zip(group, wavs, jobs):
                    if fut is None:
                        done(key, w)
                        continue
                    try:
                        fut.result()
                        done(key, None)
                    except Exception as exc:  # noqa: BLE001 - that line only
                        done(key, exc)
        return results
