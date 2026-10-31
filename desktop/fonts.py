"""Subtitle fonts for "Kiểu chữ": the bundled Be Vietnam Pro, fonts the user adds from
files (kept in ``storage/_fonts`` and registered with Qt at start-up) and every
installed font that has the Vietnamese letters.

A font without the stacked diacritics (ặ ẫ ể ộ ữ …) shows boxes or wrong marks, so a
file added without them is still kept but reported; the list only offers fonts that
pass ``supports_vietnamese``. The render finds the same files (app/fontfiles.py)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase, QRawFont

from app import fontfiles
from app.fsutil import atomic_write_text

BUNDLED = ("Be Vietnam Pro", "Bangers", "Coiny")   # OFL, desktop/assets/fonts
UI_ONLY = ("JetBrains Mono",)      # bundled for the app's own numbers, not a subtitle face
VI_SAMPLE = "ặẫểộữỹđƯơ"          # stacked marks + đ / ư / ơ
_vi_cache: dict[str, bool] = {}
_registered: set[str] = set()


def _storage() -> Path:
    from desktop import store

    return store._storage()


def supports_vietnamese(family: str) -> bool:
    if family not in _vi_cache:
        raw = QRawFont.fromFont(QFont(family))
        # code points, not str: the QChar overload answered False for Arial's "ặ"; and a
        # family Qt silently replaced by another face does not count
        _vi_cache[family] = (raw.isValid() and raw.familyName().lower() == family.lower()
                             and all(raw.supportsCharacter(ord(c)) for c in VI_SAMPLE))
    return _vi_cache[family]


def register_user_fonts() -> list[str]:
    """Load storage/_fonts into Qt (once per file); returns their families."""
    families = []
    d = fontfiles.user_dir(_storage())
    if not d.is_dir():
        return families
    for f in sorted(d.iterdir()):
        if f.suffix.lower() not in fontfiles.FONT_EXT:
            continue
        if str(f) not in _registered:
            fid = QFontDatabase.addApplicationFont(str(f))
            _registered.add(str(f))
            if fid < 0:
                continue
        families += user_families_of(f)
    return sorted(set(families))


def user_families_of(path: Path) -> list[str]:
    fam = fontfiles.user_index(_storage()).get(path.name)
    return [fam] if fam else []


def add_font_files(paths: list[str | Path]) -> list[tuple[str, bool, str]]:
    """Copy font files into storage/_fonts and register them. Returns per file
    (family or file name, has Vietnamese letters, error text or "")."""
    d = fontfiles.user_dir(_storage())
    d.mkdir(parents=True, exist_ok=True)
    index = fontfiles.user_index(_storage())
    out = []
    for raw in paths:
        src = Path(raw)
        if src.suffix.lower() not in fontfiles.FONT_EXT:
            out.append((src.name, False, "không phải tệp font (.ttf / .otf / .ttc)"))
            continue
        dst = d / src.name
        try:
            if src.resolve() != dst.resolve():
                shutil.copyfile(src, dst)
        except OSError as exc:
            out.append((src.name, False, f"không chép được ({exc})"))
            continue
        fid = QFontDatabase.addApplicationFont(str(dst))
        _registered.add(str(dst))
        fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
        if not fams:
            dst.unlink(missing_ok=True)
            out.append((src.name, False, "tệp font hỏng hoặc không đọc được"))
            continue
        index[dst.name] = fams[0]
        _vi_cache.pop(fams[0], None)
        out.append((fams[0], supports_vietnamese(fams[0]), ""))
    atomic_write_text(fontfiles.user_dir(_storage()) / fontfiles.INDEX,
                      json.dumps(index, ensure_ascii=False, indent=1))
    return out


def families() -> tuple[list[str], list[str], list[str]]:
    """(bundled, added by the user, installed) — each with Vietnamese letters, sorted;
    no vertical "@" faces, no duplicates across the groups."""
    user = [f for f in register_user_fonts() if supports_vietnamese(f)]
    every = QFontDatabase.families()
    bundled = [f for f in every if f.startswith(BUNDLED)]      # its weights too
    bundled.sort(key=lambda f: (f != BUNDLED[0], f))
    taken = set(bundled) | set(user)
    system = []
    for fam in QFontDatabase.families(QFontDatabase.WritingSystem.Vietnamese):
        if fam.startswith(("@", *UI_ONLY)) or fam in taken or QFontDatabase.isPrivateFamily(fam):
            continue
        if supports_vietnamese(fam):
            system.append(fam)
            taken.add(fam)
    return bundled, sorted(user), sorted(system, key=str.lower)
