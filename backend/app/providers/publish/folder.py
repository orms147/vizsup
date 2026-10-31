"""Gói đăng — the always-available "platform": copies the video, a thumbnail and a post.txt
(title, description, hashtags, the planned time) into ``<storage>/_posts/<date> <title>/`` so
the owner can post by hand (or a platform that is not connected yet). Never uploads."""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

from app.config import settings
from app.providers.base import PostMeta, PostResult, PublishProvider


def _safe(name: str, limit: int = 60) -> str:
    s = re.sub(r'[\/:*?"<>|\r\n\t]+', " ", name).strip(" .")
    return (s[:limit].rstrip() or "video")


class FolderPublisher(PublishProvider):
    name = "folder"
    display_name = "Gói đăng (thư mục)"
    cost_note = "không đăng — xuất sẵn video + tiêu đề + mô tả để bạn tự đăng"
    local = True

    def connected(self) -> bool:
        return True

    def account_label(self) -> str:
        return str(Path(settings.vizsup_storage_dir) / "_posts")

    def publish(self, video, meta: PostMeta, *, progress=None) -> PostResult:
        when = time.strftime("%Y-%m-%d %H%M", time.localtime(meta.publish_at or time.time()))
        d = Path(settings.vizsup_storage_dir) / "_posts" / f"{when} {_safe(meta.title)}"
        d.mkdir(parents=True, exist_ok=True)
        dst = d / f"{_safe(meta.title)}{Path(video).suffix or '.mp4'}"
        shutil.copy2(video, dst)
        if meta.thumbnail and Path(meta.thumbnail).exists():
            shutil.copy2(meta.thumbnail, d / f"thumb{Path(meta.thumbnail).suffix}")
        tags = " ".join(meta.tags)
        (d / "post.txt").write_text(
            f"{meta.title}\n\n{meta.description.strip()}\n\n{tags}\n\n"
            f"[{meta.kind}] dự kiến đăng: {when}\n", encoding="utf-8")
        if progress:
            progress(1.0)
        return PostResult(True, "draft", url=str(d), remote_id=dst.name,
                          message=f"Đã xuất gói đăng: {d}")
