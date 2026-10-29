"""«Cắt Short»: a 30–60 s vertical short cut out of an approved episode.

Three steps, the human gate untouched:
  1. ``propose`` — 1–3 candidate segments with a hook. A heuristic (always, free, instant) and an
     optional AI pass (``ask_ai``, one cheap "shorten"-role call on the approved vi.srt + timings;
     the owner clicks it, the cost is shown). The user edits the range and the hook title.
  2. ``make`` — a NEW project: the source cut to the range (re-encoded, so it starts on the exact
     frame), the approved Vietnamese + Chinese lines shifted to 0, the style / brief / series link
     copied, the TTS segments of the parent reused where the voice settings match. It has NO
     approval: it opens in the editor like any project and must be approved again.
  3. ``events`` — burned notes for the Short: the hook title for the first seconds and an end card
     "Xem full tập N" (assemble adds them to every render of a project made here).
The parent is only read.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.fsutil import atomic_write_text
from app.models import Cue, Job

KEY = "short"                         # metadata.json key of a derived short
SHORT_MIN, SHORT_MAX = 30.0, 60.0     # what is proposed (a hand-picked range may be 5–180 s)
RANGE_MIN, RANGE_MAX = 5.0, 180.0     # what make() accepts (YouTube Shorts / Reels cap: 3 min)
HOOK_MAX = 60                         # characters of the title card
HOOK_SECONDS, END_SECONDS = 3.0, 3.0
HOOK_MARGIN_V = 300                   # below the credit / chip rows, clear of the top tabs
PAD_BEFORE, PAD_AFTER = 0.15, 0.25    # air around the first / last kept line


@dataclass
class Candidate:
    start: float
    end: float
    hook: str
    reason: str = ""
    score: float = 0.0

    @property
    def length(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        return {"start": round(self.start, 2), "end": round(self.end, 2), "hook": self.hook,
                "reason": self.reason, "score": round(self.score, 2)}


# --- 1. proposals ---------------------------------------------------------------------------

_END_MARK = re.compile(r"[?!…]+[\"”’)]*\s*$|\.\.\.\s*$")


def _plain(text: str) -> str:
    return " ".join((text or "").replace("\n", " ").split())


def hook_score(text: str) -> float:
    """How well a line opens a short: a question or an exclamation, short and punchy, no leading
    dash of a mid-conversation reply. Pure."""
    t = _plain(text)
    if not t:
        return -1.0
    s = 0.0
    if t.endswith("?"):
        s += 2.0
    elif t.endswith("!"):
        s += 1.5
    elif t.endswith(("…", "...")):
        s += 1.0
    if 8 <= len(t) <= 50:
        s += 1.0
    elif len(t) > 90:
        s -= 1.0
    if t[:1] in "-–—":
        s -= 0.5
    return s


def hook_text(text: str) -> str:
    """The title card text from a line: one line, ≤ HOOK_MAX characters. Pure."""
    t = _plain(text).lstrip("-–— ").strip()
    if len(t) > HOOK_MAX:
        t = t[:HOOK_MAX - 1].rstrip(" ,;:") + "…"
    return t


def heuristic(cues: list[Cue], *, n: int = 3, lo: float = SHORT_MIN, hi: float = SHORT_MAX,
              min_gap: float = 5.0) -> list[Candidate]:
    """Up to ``n`` non-overlapping windows of ``lo``–``hi`` seconds that start on a hooky line and
    end on a cliffhanger (a question, an exclamation, a trailing "…") with dense dialogue. Pure."""
    cues = [c for c in cues if _plain(c.text)]
    if not cues:
        return []
    found: list[Candidate] = []
    for i, first in enumerate(cues):
        # the last line whose end keeps the window inside [lo, hi]; prefer a cliffhanger end
        best_j, best_end = None, -1.0
        for j in range(i, len(cues)):
            length = cues[j].end - first.start
            if length > hi:
                break
            if length >= lo:
                end_bonus = 1.0 if _END_MARK.search(_plain(cues[j].text)) else 0.0
                key = end_bonus * 100 + length            # a cliffhanger first, then the longer
                if best_j is None or key > best_end:
                    best_j, best_end = j, key
        if best_j is None:
            continue
        window = cues[i:best_j + 1]
        length = window[-1].end - first.start
        chars = sum(len(_plain(c.text)) for c in window)
        density = chars / max(1.0, length)                # characters of speech per second
        score = (hook_score(first.text) + 0.5 * hook_score(window[1].text if len(window) > 1 else "")
                 + (1.0 if _END_MARK.search(_plain(window[-1].text)) else 0.0)
                 + (0.5 if 6 <= density <= 22 else -0.5))
        why = []
        if hook_score(first.text) >= 2:
            why.append("mở bằng một câu hỏi / câu cảm thán")
        if _END_MARK.search(_plain(window[-1].text)):
            why.append("kết ở chỗ lửng — người xem muốn xem tiếp")
        if 6 <= density <= 22:
            why.append("lời thoại dày, ít chỗ trống")
        found.append(Candidate(first.start, window[-1].end, hook_text(first.text),
                               ", ".join(why) or "đoạn đủ dài, lời thoại liền mạch", score))
    found.sort(key=lambda c: (-c.score, c.start))
    picked: list[Candidate] = []
    for c in found:
        if all(c.end + min_gap <= p.start or c.start >= p.end + min_gap for p in picked):
            picked.append(c)
        if len(picked) >= n:
            break
    return sorted(picked, key=lambda c: c.start)


def propose(cues: list[Cue], *, n: int = 3) -> list[Candidate]:
    """The free, instant proposals (heuristic)."""
    return heuristic(cues, n=n)


INSTRUCTION = (
    "Bạn chọn đoạn cắt Short (TikTok / YouTube Shorts / Reels) từ một tập phim đã Việt hoá. Dưới đây là "
    "lời thoại tiếng Việt, mỗi dòng: [giây bắt đầu–giây kết thúc] lời thoại. Chọn 1–3 đoạn, mỗi đoạn "
    "dài 30–60 giây, bắt đầu bằng một câu gây tò mò / xung đột / bất ngờ (người xem phải dừng lại "
    "trong 3 giây đầu) và kết ở chỗ lửng hoặc cao trào. Các đoạn không chồng nhau. Trả về DUY NHẤT một "
    'JSON: {"shorts": [{"start": 12.5, "end": 58.0, "hook": "câu tiêu đề ≤ 60 ký tự lấy từ nội dung '
    'thật, không bịa", "why": "một câu lý do"}]}. Dùng đúng các mốc thời gian trong danh sách. '
    "Không giải thích thêm."
)


def request_text(cues: list[Cue], title: str = "") -> str:
    lines = [f"[{c.start:.1f}–{c.end:.1f}] {_plain(c.text)}" for c in cues if _plain(c.text)]
    head = f"Tập phim: {title}\n\n" if title else ""
    return head + "\n".join(lines)


def parse_reply(reply: str, cues: list[Cue]) -> list[Candidate]:
    """Candidates from the model's JSON, each snapped to real cue boundaries and kept inside
    [20, 75] s; anything unusable is dropped. Pure."""
    m = re.search(r"\{.*\}", reply or "", re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except ValueError:
        return []
    rows = data.get("shorts") if isinstance(data, dict) else None
    out: list[Candidate] = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        try:
            a, b = float(r.get("start")), float(r.get("end"))
        except (TypeError, ValueError):
            continue
        fit = fit_range(cues, a, b)
        if fit is None or not (20.0 <= fit[1] - fit[0] <= 75.0):
            continue
        hook = hook_text(str(r.get("hook") or ""))
        if not hook:
            first = next((c for c in cues if fit[0] - 0.01 <= c.start < fit[1]), None)
            hook = hook_text(first.text) if first else ""
        if any(not (fit[1] <= o.start or fit[0] >= o.end) for o in out):
            continue                                       # overlaps an earlier answer
        out.append(Candidate(fit[0], fit[1], hook, " ".join(str(r.get("why") or "").split())[:140],
                             score=1.0))
    return sorted(out, key=lambda c: c.start)[:3]


def ask_ai(cues: list[Cue], title: str = "", *, provider: str = "openrouter") -> list[Candidate]:
    """One cheap call (role "shorten"); raises when the provider can't (no key) or the answer has
    nothing usable — the caller keeps the heuristic list."""
    from app.providers.registry import get_translator

    prov = get_translator(provider)
    if not hasattr(prov, "rewrite") or not prov.available():
        raise RuntimeError("Cần OpenRouter (Cài đặt → AI và mô hình) để gợi ý bằng AI.")
    got = parse_reply(prov.rewrite(request_text(cues, title), INSTRUCTION, max_tokens=700,
                                   role="shorten"), cues)
    if not got:
        raise RuntimeError("AI không trả về đoạn dùng được — dùng gợi ý tự động.")
    return got


def fit_range(cues: list[Cue], start: float, end: float) -> tuple[float, float] | None:
    """The user's range made to hold WHOLE lines: every cue whose midpoint is inside is kept
    entire (a line is never cut in half), with a little air around. None when no line is inside.
    Pure."""
    inside = [c for c in cues if start <= (c.start + c.end) / 2 < end and _plain(c.text)]
    if not inside:
        return None
    return max(0.0, inside[0].start - PAD_BEFORE), inside[-1].end + PAD_AFTER


# --- 2. the derived project -----------------------------------------------------------------------

def info(job: Job) -> dict | None:
    """The ``short`` block of a derived project's metadata, else None."""
    md = job.metadata if isinstance(job.metadata, dict) and job.metadata else {}
    if not md:
        try:
            md = json.loads((job.work_dir / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    s = md.get(KEY) if isinstance(md, dict) else None
    return s if isinstance(s, dict) and s.get("parent") else None


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def parent_pairs(parent: Job) -> list[tuple[Cue, str]]:
    """The approved Vietnamese cues with their Chinese line (zh_ref.json when the editor kept one,
    else cn.srt by index — the same pairing the editor shows)."""
    from app.pipeline.srt import parse_srt

    vi = parse_srt(parent.vi_srt) if parent.vi_srt.exists() else []
    cn = parse_srt(parent.cn_srt) if parent.cn_srt.exists() else []
    zh = [cn[i].text if i < len(cn) else "" for i in range(len(vi))]
    ref = _read_json(parent.work_dir / "zh_ref.json")
    if isinstance(ref, list) and len(ref) == len(vi):
        zh = [str(x) for x in ref]
    return list(zip(vi, zh, strict=True))


def _cut(src: Path, dst: Path, start: float, length: float, *, cancel: Callable[[], bool] | None):
    """Re-encode [start, start+length) so the clip starts on the exact frame (a stream-copy cut
    snaps to a keyframe and the shifted subtitles would drift). x264 veryfast, CRF 18."""
    tmp = dst.with_name(f"{dst.stem}.cutting.mp4")
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-i", str(src), "-t",
           f"{length:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt",
           "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(tmp)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        tail = (proc.stderr or "").strip().splitlines()[-2:]
        raise RuntimeError("Không cắt được video: " + " | ".join(tail or ["lỗi không rõ"]))
    if cancel and cancel():
        tmp.unlink(missing_ok=True)
        raise InterruptedError("Đã hủy.")
    tmp.replace(dst)


def _reuse_tts(parent: Job, child: Job, kept: list[int]) -> int:
    """Copy the parent's TTS segments of the kept lines (parent index → new 1-based index) and their
    manifest hashes: the hash is of provider|voice|rate|spoken text, so a render with the same
    voice finds them and only differences are synthesised. Returns how many were copied."""
    from app.pipeline import tts

    manifest = tts._load_manifest(parent)
    if not manifest:
        return 0
    new: dict[str, str] = {}
    copied = 0
    for j, pi in enumerate(kept, 1):
        seg = parent.work_dir / "tts" / f"seg_{pi:04d}.mp3"
        want = manifest.get(str(pi))
        if want and seg.exists() and seg.stat().st_size > 0:
            shutil.copy2(seg, child.tts_dir / f"seg_{j:04d}.mp3")
            new[str(j)] = want
            copied += 1
    if new:
        tts._save_manifest(child, new)
    return copied


def make(parent: Job, start: float, end: float, hook: str, storage: Path, *,
         log: Callable[[str], None] | None = None,
         cancel: Callable[[], bool] | None = None) -> Job:
    """The derived project for [start, end) of ``parent`` (see the module doc). Refuses an episode
    that is not approved, has no source video any more, or a range with no line / outside
    RANGE_MIN–RANGE_MAX. The new project has no approval."""
    from app.pipeline import approval, compilation
    from app.pipeline.srt import write_srt

    say = log or (lambda _m: None)
    if not parent.vi_srt.exists() or not approval.is_approved(parent):
        raise RuntimeError("Hãy duyệt phụ đề của tập này trước khi cắt Short.")
    if not parent.source_video.exists():
        raise RuntimeError("Tập này đã dọn video gốc (Chỉ xóa file nặng) — không còn gì để cắt. "
                           "Mở tập để tải lại video gốc rồi cắt.")
    pairs = parent_pairs(parent)
    fit = fit_range([c for c, _z in pairs], float(start), float(end))
    if fit is None:
        raise RuntimeError("Khoảng thời gian này không có câu thoại nào.")
    a, b = fit
    if not (RANGE_MIN <= b - a <= RANGE_MAX):
        raise RuntimeError(f"Đoạn Short phải dài {RANGE_MIN:.0f}–{RANGE_MAX:.0f} giây "
                           f"(đang {b - a:.0f} giây).")
    kept = [i + 1 for i, (c, _z) in enumerate(pairs) if a <= (c.start + c.end) / 2 < b]
    storage = Path(storage)
    jid = uuid.uuid4().hex[:12]
    child = Job(id=jid, work_dir=storage / jid, url=parent.url)
    try:
        say(f"Đang cắt video {a:.1f}–{b:.1f} s…")
        _cut(parent.source_video, child.source_video, a, b - a, cancel=cancel)
        vi_cues, cn_cues = [], []
        for k, (c, z) in enumerate((pairs[i - 1] for i in kept), 1):
            s, e = round(max(0.0, c.start - a), 3), round(min(b - a, c.end - a), 3)
            vi_cues.append(Cue(k, s, e, c.text))
            cn_cues.append(Cue(k, s, e, z))
        write_srt(vi_cues, child.vi_srt)
        if any(c.text for c in cn_cues):
            write_srt(cn_cues, child.cn_srt)
        md = dict(_read_json(parent.work_dir / "metadata.json") or {})
        for k in ("group", KEY):
            md.pop(k, None)
        title = compilation._job_title(parent.work_dir)
        n_prev = sum(1 for d in storage.iterdir() if d.is_dir() and d != child.work_dir
                     and ((_read_json(d / "metadata.json") or {}).get(KEY) or {}).get("parent")
                     == parent.id) if storage.exists() else 0
        ep = compilation.episode_range(title)
        md.update({"title": f"{title} · Short {n_prev + 1}", "duration": round(b - a, 2),
                   KEY: {"parent": parent.id, "start": round(a, 2), "end": round(b, 2),
                         "hook": hook_text(hook), "episode": ep[0] if ep else None,
                         "parent_title": title}})
        atomic_write_text(child.work_dir / "metadata.json",
                          json.dumps(md, ensure_ascii=False, indent=1), encoding="utf-8")
        child.metadata = md
        atomic_write_text(child.work_dir / "job.json", json.dumps(
            {"id": child.id, "url": child.url, "opts": {}, "stage": "await_edit",
             "title": md["title"], "platform": md.get("platform")}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        for name in ("style.json", "region.json", "brief.json", "series_link.json"):
            if (parent.work_dir / name).exists():
                shutil.copy2(parent.work_dir / name, child.work_dir / name)
        reused = _reuse_tts(parent, child, kept)
        say(f"✓ {len(vi_cues)} câu" + (f" · giữ {reused} đoạn giọng đã tạo" if reused else ""))
    except BaseException:
        shutil.rmtree(child.work_dir, ignore_errors=True)
        raise
    return child


# --- 3. burned notes ----------------------------------------------------------------------------------

def _safe(text: str) -> str:
    """No ASS override can come out of user text."""
    return re.sub(r"[{}\\]", "", " ".join(str(text or "").split()))


def end_text(info_: dict) -> str:
    ep = info_.get("episode")
    return f"Xem full tập {ep}" if ep else "Xem full video"


def events(job: Job, duration: float) -> list[dict]:
    """Tag events for a derived short: the hook title for the first HOOK_SECONDS and the
    "Xem full tập N" end card for the last END_SECONDS. [] for any other project. Pure."""
    s = info(job)
    if not s:
        return []
    dur = float(duration or 0)
    out = []
    hook = _safe(s.get("hook"))
    if hook:
        out.append({"start": 0.0, "end": min(HOOK_SECONDS, dur) if dur > 1 else HOOK_SECONDS,
                    "text": "{\\fscx130\\fscy130\\b1}" + hook, "align": 8,
                    "margin_v": HOOK_MARGIN_V})
    if dur > 2 * END_SECONDS:
        out.append({"start": dur - END_SECONDS, "end": dur,
                    "text": "{\\fscx170\\fscy170\\b1}" + _safe(end_text(s)), "align": 5})
    return out
