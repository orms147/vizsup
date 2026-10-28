"""Which font FILES the burned subtitles need, so ffmpeg's libass finds the chosen face.

libass is pointed at one folder (``fontsdir``). It holds the bundled Be Vietnam Pro, every
font the user added in "Kiểu chữ → Thêm font…" (``storage/_fonts``) and, for a font that
comes with Windows (Arial, Segoe UI, Times New Roman …), that family's files looked up in
the Windows font registry — without them libass silently fell back to another face and
the video looked different from the editor.

``storage/_fonts/index.json`` maps each added file to the family name Qt read from it."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

USER_DIR = "_fonts"
INDEX = "index.json"
FONT_EXT = (".ttf", ".otf", ".ttc")
_STYLE_WORDS = {"regular", "bold", "italic", "oblique", "light", "semilight", "semibold",
                "black", "medium", "thin", "extralight", "extrabold", "demibold",
                "heavy", "book"}      # "Narrow" / "Condensed" are families of their own


def user_dir(storage: Path) -> Path:
    return Path(storage) / USER_DIR


def user_index(storage: Path) -> dict[str, str]:
    """{file name: family} of the fonts the user added."""
    try:
        d = json.loads((user_dir(storage) / INDEX).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in d.items()} if isinstance(d, dict) else {}


def _registry_fonts() -> list[tuple[str, Path]]:
    """(registry name, file) for every installed font (machine + current user)."""
    if os.name != "nt":
        return []
    import winreg

    out: list[tuple[str, Path]] = []
    windir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    key = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, key) as k:
                for i in range(winreg.QueryInfoKey(k)[1]):
                    name, value, _t = winreg.EnumValue(k, i)
                    p = Path(str(value))
                    if not p.is_absolute():
                        p = windir / p
                    out.append((str(name), p))
        except OSError:
            continue
    return out


def _matches(reg_name: str, family: str) -> bool:
    """"Arial Bold Italic (TrueType)" belongs to "Arial"; "Cambria & Cambria Math
    (TrueType)" to both. Pure."""
    base = reg_name.split(" (")[0].strip().lower()
    fam = family.strip().lower()
    for part in (p.strip() for p in base.split("&")):
        if part == fam:
            return True
        if part.startswith(fam + " "):
            rest = part[len(fam) + 1:].replace("-", " ").split()
            if rest and all(w in _STYLE_WORDS for w in rest):
                return True
    return False


def system_files(family: str, registry: list[tuple[str, Path]] | None = None) -> list[Path]:
    reg = _registry_fonts() if registry is None else registry
    return [p for name, p in reg if _matches(name, family) and p.suffix.lower() in FONT_EXT]


def files_for(family: str | None, storage: Path, bundled: Path) -> list[Path]:
    """Font files to put next to the .ass: bundled + user-added + the system family's."""
    files: list[Path] = []
    if bundled.is_dir():
        files += sorted(bundled.glob("*.ttf"))
    udir = user_dir(storage)
    if udir.is_dir():
        files += sorted(p for p in udir.iterdir() if p.suffix.lower() in FONT_EXT)
    if family:
        known = {f.lower() for f in user_index(storage).values()}
        fam = family.strip().lower()
        if fam not in known and not fam.startswith("be vietnam pro"):   # bundled weights
            files += system_files(family)
    seen, out = set(), []
    for f in files:
        if f.name.lower() not in seen and f.exists():
            seen.add(f.name.lower())
            out.append(f)
    return out


def copy_into(dst: Path, files: list[Path]) -> int:
    """Copy font files into ``dst`` (skipping identical ones); returns how many exist."""
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in files:
        target = dst / f.name
        try:
            if not target.exists() or target.stat().st_size != f.stat().st_size:
                shutil.copyfile(f, target)
            n += 1
        except OSError:
            continue
    return n
