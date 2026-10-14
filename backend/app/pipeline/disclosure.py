"""F-1/F-2/F-3: credit the source, label the AI voice, and note the right to use the video.

Why (docs/plan/F-roadmap.md, rules re-checked 2026-10-14):
- Meta demotes and demonetises reposts made "without crediting" the creator; Vietnamese
  IP law (Art. 19) gives authors the right to be named. A credit does NOT make a repost
  original or licensed — it is the minimum.
- TikTok requires a label on realistic AI-generated content; Vietnam's AI Law
  134/2025/QH15 Art. 11(4) requires one for AI audio that imitates a real person's voice
  (a stock TTS voice probably isn't, a cloned one is). A short visible "Lồng tiếng AI" at
  the start is cheap and honest either way.
- Douyin's terms forbid showing its content elsewhere without authorisation, and YouTube's
  reused-content rule applies "even if you have permission": the user records what they
  have (own video / permission / licence) and is warned when it is unknown. Never blocks.

Burned-in notes ride in the subtitle .ass as "Tag" events (top corners, first seconds).
"""
from __future__ import annotations

import json
from pathlib import Path

from app.models import Job

PLATFORMS = {"douyin": "Douyin", "bilibili": "Bilibili", "local": ""}
RIGHTS = {
    "unknown": "Chưa ghi",
    "own": "Video của tôi",
    "permission": "Được tác giả cho phép",
    "licensed": "Có bản quyền (đã mua / được cấp phép)",
}
RIGHTS_FILE = "rights.json"
TAG_SECONDS = 5.0           # how long the corner notes stay at the start
CREDIT_MAX = 22             # uploader name chars on screen (the full name goes in the post)


def source_info(job: Job) -> dict:
    """Who posted the original, where and when (from the download metadata)."""
    md = job.metadata or {}
    if not md and (job.work_dir / "metadata.json").exists():
        try:
            md = json.loads((job.work_dir / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            md = {}
    date = str(md.get("upload_date") or "")
    return {
        "uploader": (md.get("uploader") or md.get("channel") or "").strip(),
        "uploader_id": (md.get("uploader_id") or "").strip(),
        "platform": PLATFORMS.get(md.get("platform") or "", md.get("platform") or ""),
        "url": md.get("webpage_url") or job.url or "",
        "date": f"{date[6:8]}/{date[4:6]}/{date[:4]}" if len(date) == 8 else "",
        "title": (md.get("title") or "").strip(),
    }


def credit_line(job: Job) -> str:
    """Short on-screen credit: "Nguồn: @người_đăng · Douyin" ('' when nothing is known)."""
    s = source_info(job)
    who = s["uploader"] or (f"@{s['uploader_id']}" if s["uploader_id"] else "")
    if len(who) > CREDIT_MAX:              # one short corner line, clear of the AI label
        who = who[:CREDIT_MAX - 1].rstrip() + "…"
    bits = [b for b in (who, s["platform"]) if b]
    return f"Nguồn: {' · '.join(bits)}" if bits else ""


def load_rights(job: Job) -> dict:
    try:
        data = json.loads((job.work_dir / RIGHTS_FILE).read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("basis") in RIGHTS:
            return data
    except (OSError, ValueError):
        pass
    return {"basis": "unknown", "note": "", "proof": ""}


def save_rights(job: Job, basis: str, *, note: str = "", proof: Path | None = None) -> dict:
    """Record the right to use this video; ``proof`` (a screenshot, an email…) is copied
    into the project folder so it travels with it."""
    import shutil

    data = load_rights(job)
    data["basis"] = basis if basis in RIGHTS else "unknown"
    data["note"] = note.strip()
    if proof is not None:
        dst_dir = job.work_dir / "rights"
        dst_dir.mkdir(exist_ok=True)
        dst = dst_dir / Path(proof).name
        shutil.copyfile(proof, dst)
        data["proof"] = f"rights/{dst.name}"
    (job.work_dir / RIGHTS_FILE).write_text(json.dumps(data, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
    return data


def overlays(job: Job, *, credit: bool, ai_voice: bool, duration: float | None = None) -> list[dict]:
    """Burned corner notes for the start of the video: the credit (top left) and the
    AI-voice label (top right)."""
    end = TAG_SECONDS if not duration else min(TAG_SECONDS, max(1.0, duration))
    out = []
    if credit and (line := credit_line(job)):
        out.append({"start": 0.0, "end": end, "text": line, "align": 7})
    if ai_voice:
        out.append({"start": 0.0, "end": end, "text": "Lồng tiếng AI", "align": 9})
    return out


def post_description(job: Job, *, ai_voice: bool, title: str = "") -> str:
    """Text for the post's description: what it is, who made the original, how the
    Vietnamese was made, and the right to use it — editable by the user."""
    s = source_info(job)
    rights = load_rights(job)
    lines: list[str] = []
    if title or s["title"]:
        lines += [title or s["title"], ""]
    if s["uploader"] or s["url"]:
        src = "Video gốc: " + (s["uploader"] or "tác giả") + (f" ({s['platform']})" if s["platform"]
                                                              else "")
        if s["date"]:
            src += f", đăng {s['date']}"
        lines.append(src + (f" — {s['url']}" if s["url"] else ""))
    lines.append("Phụ đề tiếng Việt: dịch bằng AI và biên tập lại bởi người đăng.")
    if ai_voice:
        lines.append("Lồng tiếng: giọng đọc AI (giọng tổng hợp có sẵn), không phải giọng của "
                     "người trong video.")
    basis = rights.get("basis")
    if basis == "permission":
        lines.append("Đăng lại với sự cho phép của tác giả."
                     + (f" ({rights['note']})" if rights.get("note") else ""))
    elif basis == "licensed":
        lines.append("Nội dung được cấp phép sử dụng."
                     + (f" ({rights['note']})" if rights.get("note") else ""))
    return "\n".join(lines).strip() + "\n"


def checklist(*, ai_voice: bool, credit: bool, rights: str) -> list[tuple[bool, str]]:
    """Before posting: [(done, item)] — reminders, not legal advice."""
    return [
        (rights in ("own", "permission", "licensed"),
         "Có quyền dùng video (của bạn / được tác giả cho phép / có bản quyền). Douyin cấm "
         "đăng lại nội dung của họ ở nơi khác khi chưa được phép."),
        (credit, "Ghi nguồn tác giả gốc (trên video hoặc trong mô tả)."),
        (not ai_voice,
         "Khi đăng, nếu nền tảng hỏi có dùng nội dung do AI tạo không: chọn Có cho giọng "
         "lồng tiếng AI (TikTok, YouTube, Facebook đều có mục này)." if ai_voice else
         "Không lồng tiếng AI — không cần nhãn AI cho giọng nói."),
        (False, "Thêm phần của riêng bạn (bình luận, giới thiệu, góc nhìn): YouTube, Facebook và "
                "TikTok đều giảm phân phối / tắt kiếm tiền với video đăng lại không thêm gì."),
    ]
