"""Douyin through an embedded browser (run as ``python -m app.douyin_capture <id> <out.json>``).

Since Oct 2026 Douyin answers yt-dlp's ``aweme/detail`` call with HTTP 403 — the web API
needs signature cookies computed by Douyin's own JavaScript (yt-dlp: "TODO: Run
verification challenge code"), so fresh cookies don't help. A real Chromium does run that
code: this script opens the video page in QtWebEngine (a persistent profile under
``<storage>/_browser/douyin``), records the media URLs the page itself requests, reads the
title / author, and writes them to ``out.json``. download.py then probes and fetches the
streams with ffmpeg. A separate process because QtWebEngine must own the main thread.

The signed URLs only ever land in that JSON inside the job folder; nothing is printed.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

MEDIA_HINTS = ("douyinvod.com", "zjcdn.com", "/video/tos/", "mime_type=video")
WAIT_S = 75.0          # give up when the page plays nothing in this time (captcha, removed)
SETTLE_S = 4.0         # after the first media request, wait for the alternatives / title
HARD_S = WAIT_S + 15   # runJavaScript never answers on a page that did not load: quit anyway


def main(video_id: str, out: Path, profile_dir: Path) -> int:
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtWebEngineCore import (QWebEnginePage, QWebEngineProfile, QWebEngineSettings,
                                         QWebEngineUrlRequestInterceptor)
    from PySide6.QtWidgets import QApplication

    profile_dir = profile_dir.resolve()      # a relative path breaks Chromium's disk caches
    app = QApplication(sys.argv[:1])
    seen: list[str] = []

    class Catch(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info):  # noqa: N802 - Qt API
            u = info.requestUrl().toString()
            if any(k in u for k in MEDIA_HINTS) and u not in seen:
                seen.append(u)

    profile = QWebEngineProfile("vizsup-douyin")
    profile.setPersistentStoragePath(str(profile_dir))
    profile.setCachePath(str(profile_dir / "cache"))
    catch = Catch()
    profile.setUrlRequestInterceptor(catch)
    page = QWebEnginePage(profile)
    page.settings().setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
    page.setAudioMuted(True)
    page.loadFinished.connect(lambda ok: print(f"loaded={ok} after {time.time() - t0:.1f}s",
                                                file=sys.stderr, flush=True))
    t0 = time.time()
    page.load(QUrl(f"https://www.douyin.com/video/{video_id}"))
    state = {"first": None, "meta": {}}
    js = ("JSON.stringify({title: (document.querySelector('h1')||{}).textContent "
          "|| document.title, "
          "author: ((document.querySelector('[data-e2e=\"video-author\"], "
          "[data-e2e=\"user-info\"]')||{}).innerText || '').split('\\n')[0], "
          "duration: (document.querySelector('video')||{}).duration || 0, "
          "href: location.href})")

    def done(result):
        print(f"meta after {time.time() - t0:.1f}s: {str(result)[:200]}", file=sys.stderr, flush=True)
        try:
            state["meta"] = json.loads(result or "{}")
        except ValueError:
            pass
        app.quit()

    def poll():
        now = time.time()
        if seen and state["first"] is None:
            state["first"] = now
            print(f"first media after {now - t0:.1f}s", file=sys.stderr, flush=True)
        if (state["first"] and now - state["first"] >= SETTLE_S) or now - t0 > WAIT_S:
            timer.stop()
            page.runJavaScript(js, 0, done)

    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(500)
    QTimer.singleShot(int(HARD_S * 1000), app.quit)
    app.exec()
    meta = state["meta"]
    title = str(meta.get("title") or "").removesuffix(" - 抖音").strip()
    out.write_text(json.dumps({"urls": seen, "user_agent": profile.httpUserAgent(),
                               "title": title, "uploader": str(meta.get("author") or "").strip(),
                               "duration": meta.get("duration") or 0,
                               "href": str(meta.get("href") or "")},
                              ensure_ascii=False), encoding="utf-8")
    return 0 if seen else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])))
