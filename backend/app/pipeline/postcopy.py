"""Gợi ý tiêu đề + hashtag for the post (2026-10-29, roadmap "Later": AI post title).

One cheap text call (the "shorten" role — the same small model as "Dịch lại") from what the
app already knows: the original title, the context brief, the series name and episode, and
the first approved Vietnamese lines. The answer is a draft the user edits in "Mô tả đăng
bài"; nothing is posted. The prompt forbids invented facts and clickbait the video does not
deliver — misleading titles get reposts demoted.
"""
from __future__ import annotations

import json
import re

from app.models import Job

INSTRUCTION = (
    "Bạn viết bài đăng cho video đã Việt hoá (phụ đề + lồng tiếng tiếng Việt) trên TikTok, "
    "YouTube Shorts và Facebook Reels. Từ thông tin video bên dưới, trả về DUY NHẤT một JSON:\n"
    '{"titles": ["…", "…", "…"], "hashtags": ["#…", …]}\n'
    "- titles: 3 tiêu đề tiếng Việt khác kiểu nhau (tò mò / nêu nội dung / cảm xúc), mỗi cái "
    "≤ 90 ký tự, có số tập nếu là phim nhiều tập; chỉ nói điều có trong video, không bịa, "
    "không giật tít sai sự thật, không viết hoa toàn bộ.\n"
    "- hashtags: 6–10 hashtag tiếng Việt không dấu hoặc phổ biến (vd. #vietsub #longtieng), "
    "đúng thể loại; không hashtag tên người thật nếu video không nói đến.\n"
    "Không giải thích, không thêm gì ngoài JSON."
)
LINES = 25           # first approved Vietnamese lines sent as the content sample
_TAG = re.compile(r"^#[^\s#]{1,40}$")


def request_text(job: Job) -> str:
    """The facts the model gets. Pure apart from reading the job's own files."""
    from app.pipeline import brief, compilation, disclosure
    from app.pipeline.srt import parse_srt

    src = disclosure.source_info(job)
    title = compilation._job_title(job.work_dir)
    parts = [f"Tiêu đề gốc (tiếng Trung): {title}"]
    r = compilation.episode_range(title)
    if r:
        parts.append("Tập: " + (f"{r[0]}–{r[1]}" if r[1] > r[0] else str(r[0])))
    if src.get("platform"):
        parts.append(f"Nguồn: {src['platform']}")
    b = (brief.load_brief(job) or "").strip()
    if b:
        parts.append(f"Ngữ cảnh video:\n{b[:1500]}")
    if job.vi_srt.exists():
        lines = [c.text.replace("\n", " ") for c in parse_srt(job.vi_srt)[:LINES]]
        parts.append("Lời thoại tiếng Việt (đầu video):\n" + "\n".join(lines))
    return "\n\n".join(parts)


def parse(reply: str) -> dict:
    """{"titles": [≤3 str], "hashtags": [str]} from the model's answer; tolerant of code
    fences and chatter around the JSON. Pure."""
    m = re.search(r"\{.*\}", reply or "", re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except ValueError:
        data = {}
    titles = [" ".join(str(t).split())[:120] for t in data.get("titles") or [] if str(t).strip()]
    tags: list[str] = []
    for t in data.get("hashtags") or []:
        t = "#" + str(t).strip().lstrip("#").replace(" ", "").lower()   # "#sinhTon" → one style
        if _TAG.match(t) and t not in tags:
            tags.append(t)
    return {"titles": titles[:3], "hashtags": tags[:10]}


def suggest(job: Job, *, provider: str = "openrouter") -> dict:
    """Ask the model; raises when the provider can't (no key) or answers nothing usable."""
    from app.providers.registry import get_translator

    prov = get_translator(provider)
    if not hasattr(prov, "rewrite") or not prov.available():
        raise RuntimeError("Cần OpenRouter (Cài đặt → AI và mô hình) để gợi ý tiêu đề.")
    out = parse(prov.rewrite(request_text(job), INSTRUCTION, max_tokens=500, role="shorten"))
    if not out["titles"]:
        raise RuntimeError("AI không trả về tiêu đề dùng được — thử lại.")
    return out


def compose(text: str, picked: dict) -> str:
    """The description with a suggested title on top and hashtags at the end. Pure."""
    body = text.rstrip("\n")
    titles = picked.get("titles") or []
    if titles:
        body = f"{titles[0]}\n\n{body}"
    if picked.get("hashtags"):
        body += "\n\n" + " ".join(picked["hashtags"])
    return body + "\n"
