"""Side-effect import: put the backend package (`app`) on sys.path so the
desktop GUI can call it directly. Import this before any `import app.*`.
Run the app with:  python -m desktop.main   (from the repo root)
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
