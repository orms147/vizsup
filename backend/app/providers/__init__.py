"""Pluggable, UI-selectable providers for translation, TTS, and ASR.

Add a provider = drop one file implementing the interface in ``base.py`` and
register it in ``registry.py``. Never hardcode a single engine in pipeline code.
"""
