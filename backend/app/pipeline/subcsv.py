"""Subtitles as a CSV table, for proof-reading in Google Sheets / Excel.

Columns: ``index,start,end,zh,vi`` — one row per cue, times as ``HH:MM:SS.mmm`` text, UTF-8
WITH a BOM (Excel opens Vietnamese correctly only then). Importing matches rows by ``index``
and changes the Vietnamese text (and the times only on request); a file whose row count or
Chinese column does not match the project is REFUSED, with the reason — a CSV of another
video must never overwrite this one. Pure: the editor does the undo step and the dialogs.
"""
from __future__ import annotations

import csv
import io
import re
from collections.abc import Sequence
from pathlib import Path

COLUMNS = ("index", "start", "end", "zh", "vi")
BOM = "﻿"
MAX_LISTED = 5          # mismatches named in an error message
# what Excel / Google Sheets write into a cell whose "formula" failed (a Vietnamese line that
# starts with "-" / "=" / "+" can be taken for one): importing it would replace the subtitle
_SHEET_ERROR = re.compile(r"^\s*#(NAME\?|VALUE!|REF!|DIV/0!|N/A|NUM!|NULL!|ERROR!?|SPILL!|CALC!)\s*$",
                          re.I)


class CsvError(ValueError):
    """The file cannot be applied; ``str(e)`` says why (Vietnamese, shown to the user)."""


def fmt_ts(t: float) -> str:
    ms = max(0, int(round(float(t) * 1000)))
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


_TS = re.compile(r"^\s*(?:(\d+):)?(\d+):(\d+)(?:[.,](\d{1,3}))?\s*$")


def parse_ts(text: str) -> float | None:
    """"HH:MM:SS.mmm" / "MM:SS,mmm" / "12.5" / "12,5" (seconds) → seconds, None when it is
    none of those."""
    t = (text or "").strip()
    if not t:
        return None
    m = _TS.match(t)
    if m:
        h, mi, s, frac = m.groups()
        ms = int((frac or "0").ljust(3, "0"))
        return int(h or 0) * 3600 + int(mi) * 60 + int(s) + ms / 1000.0
    try:
        return float(t.replace(",", "."))
    except ValueError:
        return None


def export_text(cues: Sequence) -> str:
    """The CSV text (with the BOM) for cues having start, end, zh, vi."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(COLUMNS)
    for i, c in enumerate(cues, 1):
        w.writerow([i, fmt_ts(c.start), fmt_ts(c.end), guard(c.zh or ""), guard(c.vi or "")])
    return BOM + buf.getvalue()


def write_file(path: str | Path, cues: Sequence) -> Path:
    p = Path(path)
    p.write_bytes(export_text(cues).encode("utf-8"))
    return p


def read_text(path: str | Path) -> str:
    """The file's text: UTF-8 with or without a BOM (Excel's "CSV UTF-8"), else Windows-1258 /
    Latin-1 as Excel's plain "CSV" saves it — never a traceback."""
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp1258"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


FORMULA_STARTS = "-=+@"        # Excel / Sheets read a cell starting with one of these as a formula


def guard(text: str) -> str:
    """A text cell safe to open in a spreadsheet: a leading apostrophe before ``- = + @`` (a
    dialogue dash "- Anh đi đâu?" would become a formula). Also guards a text that already starts
    with "'" + one of them, so that :func:`unguard` removes exactly the guard and nothing else."""
    probe = text[1:] if text.startswith("'") else text
    return "'" + text if probe[:1] and probe[0] in FORMULA_STARTS else text


def unguard(text: str) -> str:
    """Undo :func:`guard`: strip ONE leading apostrophe when what follows it (past at most one more
    apostrophe — the one :func:`guard` keeps for a text that itself began with "'") starts with
    ``- = + @``. A cell that merely starts with an apostrophe is left alone."""
    if text[:1] != "'":
        return text
    rest = text[1:]
    probe = rest[1:] if rest.startswith("'") else rest
    return rest if probe[:1] and probe[0] in FORMULA_STARTS else text


def _nl(text: str) -> str:
    """A line break inside a cell: Excel / Sheets write \r\n or \n — the editor uses \n."""
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def read_rows(text: str) -> list[dict]:
    """The rows of a CSV (comma, semicolon or tab separated — Excel in a Vietnamese locale
    writes semicolons) as dicts with the five columns. CsvError for a file without them."""
    text = (text or "").lstrip(BOM)
    if not text.strip():
        raise CsvError("File trống.")
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    delim = max(",;\t", key=first.count)          # the header line says which (not the text)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delim)
    rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not rows:
        raise CsvError("File trống.")
    head = [h.strip().lower() for h in rows[0]]
    missing = [c for c in COLUMNS if c not in head]
    if missing:
        raise CsvError("Thiếu cột: " + ", ".join(missing) + ". Cần đủ các cột "
                       + ", ".join(COLUMNS) + " — hãy dùng file xuất từ vizsup.")
    pos = {c: head.index(c) for c in COLUMNS}
    out = []
    for n, r in enumerate(rows[1:], 2):
        cell = lambda c: r[pos[c]] if pos[c] < len(r) else ""   # noqa: E731
        try:
            idx = int(float(cell("index").strip().replace(",", ".")))
        except (ValueError, OverflowError):        # "abc", "", "nan", "inf", "1e999"
            raise CsvError(f"Dòng {n} của file: cột index “{cell('index')}” không phải số.") from None
        out.append({"index": idx, "start": cell("start"), "end": cell("end"),
                    "zh": unguard(_nl(cell("zh"))), "vi": unguard(_nl(cell("vi")))})
    return out


def match(cues: Sequence, rows: list[dict]) -> list[dict]:
    """The file's rows in cue order (row ``k`` ↔ cue ``k``), or CsvError saying why they do
    not belong to these cues: another row count, repeated / unknown indexes, or a Chinese
    column that differs."""
    n = len(cues)
    if len(rows) != n:
        raise CsvError(f"File có {len(rows)} dòng nhưng dự án có {n} câu — không phải file của "
                       "video này, hoặc đã thêm / xóa dòng. Chỉ sửa chữ trong các ô, đừng thêm "
                       "hay xóa dòng.")
    by_index: dict[int, dict] = {}
    for r in rows:
        if r["index"] in by_index:
            raise CsvError(f"Số thứ tự {r['index']} xuất hiện hai lần trong cột index.")
        by_index[r["index"]] = r
    unknown = sorted(set(by_index) - set(range(1, n + 1)))
    if unknown:
        raise CsvError("Cột index có số ngoài 1–" + str(n) + ": "
                       + ", ".join(map(str, unknown[:MAX_LISTED])) + ".")
    ordered = [by_index[i] for i in range(1, n + 1)]
    bad = [i for i, (c, r) in enumerate(zip(cues, ordered), 1) if _norm(c.zh) != _norm(r["zh"])]
    if bad:
        shown = ", ".join(map(str, bad[:MAX_LISTED])) + ("…" if len(bad) > MAX_LISTED else "")
        raise CsvError(f"Cột zh khác dự án ở {len(bad)} câu (câu {shown}) — file này không phải "
                       "của video này, hoặc cột zh đã bị sửa. Chỉ sửa cột vi.")
    broken = [i for i, r in enumerate(ordered, 1) if _SHEET_ERROR.match(r["vi"] or "")]
    if broken:
        shown = ", ".join(map(str, broken[:MAX_LISTED])) + ("…" if len(broken) > MAX_LISTED else "")
        raise CsvError(f"Cột vi có ô lỗi công thức của Excel / Google Sheets (#NAME?, #VALUE!…) ở "
                       f"{len(broken)} câu (câu {shown}) — thường do câu bắt đầu bằng “-”, “=”, “+”. "
                       "Sửa lại các ô đó (gõ dấu ' trước chữ) rồi nhập lại; chưa đổi gì trong "
                       "dự án.")
    return ordered


def changes(cues: Sequence, ordered: list[dict], *, with_times: bool = False
            ) -> tuple[list[int], list[int]]:
    """(rows whose Vietnamese text differs, rows whose times differ) — 0-based. Times count
    only with ``with_times``, never for locked cues, and a bad time cell is a CsvError."""
    text_rows, time_rows = [], []
    for i, (c, r) in enumerate(zip(cues, ordered)):
        if (r["vi"] or "") != (c.vi or ""):
            text_rows.append(i)
        if with_times and not getattr(c, "locked", False):
            s, e = parse_ts(r["start"]), parse_ts(r["end"])
            if s is None or e is None:
                raise CsvError(f"Câu {i + 1}: thời gian “{r['start']}” – “{r['end']}” không đọc được "
                               "(dạng HH:MM:SS.mmm).")
            if e <= s:
                raise CsvError(f"Câu {i + 1}: thời gian kết thúc phải sau thời gian bắt đầu.")
            if abs(s - c.start) > 0.0005 or abs(e - c.end) > 0.0005:
                time_rows.append(i)
    return text_rows, time_rows
