"""Audio output that follows the system's default device.

A QAudioOutput created once keeps the device that was default at that moment on some
Windows setups: plugging in headphones left the preview playing through the laptop
speakers (user report 2026-10-28). Every player in the app attaches its output through
``follow_default_output``, which points the output at the current default device now and
again whenever the set of output devices changes (headphones in / out).
"""
from __future__ import annotations


def follow_default_output(audio) -> object | None:
    """Point ``audio`` (a QAudioOutput) at the default output device and keep it there.
    Returns the QMediaDevices watcher (owned by ``audio``), or None without Qt Multimedia."""
    try:
        from PySide6.QtMultimedia import QMediaDevices
    except ImportError:
        return None

    def _sync(*_args) -> None:
        dev = QMediaDevices.defaultAudioOutput()
        if not dev.isNull():
            audio.setDevice(dev)

    watcher = QMediaDevices(audio)          # parented: lives exactly as long as the output
    watcher.audioOutputsChanged.connect(_sync)
    _sync()
    return watcher
