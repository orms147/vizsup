"""E-7: learn from the user's own edits.

- Translation memory (``<storage>/tm.json``): every APPROVED line is stored as
  normalised Chinese → approved Vietnamese. A later video whose Chinese line matches
  exactly gets the approved wording (repeated lyrics, catchphrases, intros) — no API.
- "Phong cách của tôi" (``<storage>/style_rules.txt``): the machine translation of each
  job is kept (``vi.mt.srt``); on approval the lines the user changed are logged
  (``edits.jsonl``) and every ``STYLE_EVERY`` approvals a model condenses the recent
  edits into ≤10 short rules that ride in every translator prompt. The file is plain
  text — the user can read and edit it (editor → Ngữ cảnh).
Everything here is best effort: memory never breaks translating or approving.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from app.models import Job
from app.fsutil import atomic_write_text

TM_FILE, EDITS_FILE, RULES_FILE, META_FILE = "tm.json", "edits.jsonl", "style_rules.txt", \
    "style_meta.json"
TM_MAX = 5000
STYLE_EVERY = 5          # approvals between two style updates
EDITS_FOR_STYLE = 60     # most recent changed lines the style update reads
_lock = threading.Lock()
_PUNCT = re.compile(r"[\s，。！？、：；·…~—,.!?;:\"'“”‘’()（）\[\]【】《》-]+")

STYLE_INSTRUCTION = (
    "Bạn là biên tập viên phụ đề. Dưới đây là các câu dịch máy (MT) và bản người dùng đã sửa "
    "(FINAL) cho cùng câu tiếng Trung (ZH). Hãy rút ra TỐI ĐA 10 quy tắc ngắn (mỗi dòng một "
    "quy tắc, bắt đầu bằng '- ') mô tả THÓI QUEN SỬA lặp lại của người dùng: xưng hô, cách "
    "gọi tên, từ ngữ họ thích / tránh, độ dài câu, giọng văn. Chỉ ghi quy tắc thấy lặp lại "
    "ít nhất 2 lần; không ghi chuyện của riêng một video. Nếu có QUY TẮC CŨ, giữ những quy "
    "tắc còn đúng và gộp lại. Chỉ trả về danh sách quy tắc."
)


def _storage() -> Path:
    from app.config import settings

    return Path(settings.vizsup_storage_dir)


def norm_zh(s: str) -> str:
    return _PUNCT.sub("", s or "")


# --- translation memory -----------------------------------------------------------

def _load_tm() -> dict:
    try:
        data = json.loads((_storage() / TM_FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def remember(pairs: list[tuple[str, str]]) -> int:
    """Store approved (zh, vi) pairs. Returns how many were added / updated."""
    n = 0
    with _lock:
        tm = _load_tm()
        now = int(time.time())
        for zh, vi in pairs:
            k, vi = norm_zh(zh), (vi or "").strip()
            if len(k) < 2 or not vi:
                continue
            if tm.get(k, {}).get("vi") != vi:
                n += 1
            tm[k] = {"vi": vi, "t": now}
        if len(tm) > TM_MAX:                     # drop the oldest
            tm = dict(sorted(tm.items(), key=lambda kv: kv[1].get("t", 0))[-TM_MAX:])
        try:
            _storage().mkdir(parents=True, exist_ok=True)
            atomic_write_text((_storage() / TM_FILE), json.dumps(tm, ensure_ascii=False), encoding="utf-8")
        except OSError:
            return 0
    return n


def apply_tm(cn_texts: list[str], vi_lines: list[str]) -> tuple[list[str], int]:
    """Approved wording for exact (normalised) Chinese matches. Pure w.r.t. inputs."""
    tm = _load_tm()
    out, n = list(vi_lines), 0
    for i, zh in enumerate(cn_texts):
        hit = tm.get(norm_zh(zh))
        if hit and hit.get("vi") and i < len(out) and out[i] != hit["vi"]:
            out[i] = hit["vi"]
            n += 1
    return out, n


# --- edits → style rules ---------------------------------------------------------------

def save_machine_translation(job: Job) -> None:
    """Keep what the machine produced (vi.mt.srt) before any human edit."""
    try:
        if job.vi_srt.exists():
            (job.work_dir / "vi.mt.srt").write_bytes(job.vi_srt.read_bytes())
    except OSError:
        pass


def record_edits(job: Job, approved: list[tuple[str, str]]) -> int:
    """Log the lines the user changed vs the machine translation; returns the number of
    approvals since the last style update (the caller decides when to update)."""
    from app.pipeline.srt import parse_srt

    mt_path = job.work_dir / "vi.mt.srt"
    changed = []
    if mt_path.exists():
        mt = {norm_zh(""): ""}
        try:
            mt_cues = parse_srt(mt_path)
        except Exception:  # noqa: BLE001
            mt_cues = []
        cn = parse_srt(job.cn_srt) if job.cn_srt.exists() else []
        if len(cn) == len(mt_cues):                   # pair the MT with its Chinese
            mt = {norm_zh(c.text): m.text for c, m in zip(cn, mt_cues)}
        for zh, final in approved:
            old = mt.get(norm_zh(zh))
            if old is not None and old.strip() and old.strip() != (final or "").strip():
                changed.append({"zh": zh, "mt": old.strip(), "final": final.strip()})
    with _lock:
        try:
            _storage().mkdir(parents=True, exist_ok=True)
            if changed:
                with (_storage() / EDITS_FILE).open("a", encoding="utf-8") as f:
                    for row in changed:
                        f.write(json.dumps(row, ensure_ascii=False) + "\n")
            meta = _meta()
            meta["since"] = meta.get("since", 0) + 1
            atomic_write_text((_storage() / META_FILE), json.dumps(meta), encoding="utf-8")
            return meta["since"]
        except OSError:
            return 0


def _meta() -> dict:
    try:
        return json.loads((_storage() / META_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def style_rules() -> str:
    try:
        return (_storage() / RULES_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def save_style_rules(text: str) -> None:
    _storage().mkdir(parents=True, exist_ok=True)
    atomic_write_text((_storage() / RULES_FILE), (text or "").strip() + "\n", encoding="utf-8")


def update_style(provider: str = "openrouter", *, log=None) -> str | None:
    """Condense recent edits into ≤10 rules (brief role). Returns the new rules or None."""
    from app.providers.registry import get_translator

    say = log or (lambda _m: None)
    try:
        rows = [json.loads(ln) for ln in
                (_storage() / EDITS_FILE).read_text(encoding="utf-8").splitlines() if ln.strip()]
    except (OSError, ValueError):
        return None
    rows = rows[-EDITS_FOR_STYLE:]
    if len(rows) < 3:
        return None
    try:
        prov = get_translator(provider)
        if not hasattr(prov, "rewrite") or not prov.available():
            return None
        body = ""
        old = style_rules()
        if old:
            body += f"QUY TẮC CŨ:\n{old}\n\n"
        body += "\n".join(f"ZH: {r['zh']}\nMT: {r['mt']}\nFINAL: {r['final']}\n" for r in rows)
        reply = prov.rewrite(body, STYLE_INSTRUCTION, max_tokens=600, role="brief")
    except Exception as exc:  # noqa: BLE001
        say(f"⚠ Không cập nhật được phong cách dịch ({type(exc).__name__}: {exc})")
        return None
    rules = "\n".join(ln.strip() for ln in (reply or "").splitlines()
                      if ln.strip().startswith("-"))[:2000]
    if not rules:
        return None
    save_style_rules(rules)
    with _lock:
        meta = _meta()
        meta["since"] = 0
        meta["updated"] = int(time.time())
        try:
            atomic_write_text((_storage() / META_FILE), json.dumps(meta), encoding="utf-8")
        except OSError:
            pass
    say("✓ Đã cập nhật “Phong cách của tôi” từ các lần bạn sửa")
    return rules
