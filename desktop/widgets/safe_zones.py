"""Platform safe zones — the table and the hint live in ``app.pipeline.safezones`` (the Short
preset places subtitles from them in the backend); this module keeps the desktop import path."""
from app.pipeline.safezones import (  # noqa: F401
    ORDER,
    SAFE_ZONES,
    Platform,
    Zone,
    hint_for,
    zones,
)
