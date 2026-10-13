"""Suite-wide guards."""
from __future__ import annotations

import pytest

@pytest.fixture(autouse=True)
def _no_vad_download(monkeypatch):
    """Tests never hit the network: the one-time Silero VAD model download fails fast,
    so pipeline code paths that snap to the voice just skip (speech_spans → None).
    Tests that exercise the real model use it only when it is already on disk."""
    try:
        from app.pipeline import vad
    except Exception:  # noqa: BLE001 - module absent in a partial checkout
        return

    def offline(log=None):
        if vad.model_ready():
            return vad.model_path()
        raise RuntimeError("Silero VAD download disabled in tests")

    monkeypatch.setattr(vad, "ensure_model", offline)


@pytest.fixture(autouse=True)
def _private_storage(tmp_path_factory, monkeypatch):
    """Tests never read or write the user's real storage/ (translation memory, style
    rules, ui_state, glossary…). A test may still point it elsewhere itself."""
    try:
        from app.config import settings
    except Exception:  # noqa: BLE001
        return
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path_factory.mktemp("storage"))
    # renders are built for libx264 unless a test asks otherwise — a machine with an
    # NVIDIA GPU must not change what the graph tests see
    monkeypatch.setattr(settings, "vizsup_video_encoder", "cpu")
