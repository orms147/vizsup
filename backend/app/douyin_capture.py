"""Douyin through an embedded browser (run as ``python -m app.douyin_capture <id> <out.json>``).

Since Oct 2026 Douyin answers yt-dlp's ``aweme/detail`` call with HTTP 403 — the web API
needs signature cookies computed by Douyin's own JavaScript (yt-dlp: "TODO: Run
verification challenge code"), so fresh cookies don't help. A real Chromium does run that
code: this script opens the video page in QtWebEngine (a persistent profile under
``<storage>/_browser/douyin``), records the media URLs the page itself requests, reads the
title / author, and writes them to ``out.json``. download.py then probes and fetches the
streams with ffmpeg. A separate process because QtWebEngine must own the main thread.

The signed URLs only ever land in that JSON inside the job folder; nothing is printed.

List mode (``python -m app.douyin_capture --list <user page> <out.json> <profile> [limit]``,
pipeline/watch.py): a creator page https://www.douyin.com/user/<sec_uid> in the same profile,
scrolled a few times inside a view that is never shown (the post list loads lazily into a real
viewport); the video cards (links to /video/<id> + their titles) are read with runJavaScript.
A script injected at document creation also keeps the post-list answers the page itself
fetches (exact titles + publish times). Checked 2026-10-31 on 阿熊动漫: without a login
Douyin shows the first 18 posts (pinned one included), then "登录后查看更多作品" — that is
reported as ``login_wall``, never worked around.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

MEDIA_HINTS = ("douyinvod.com", "zjcdn.com", "/video/tos/", "mime_type=video")
WAIT_S = 75.0          # give up when the page plays nothing in this time (captcha, removed)
SETTLE_S = 4.0         # after the first media request, wait for the alternatives / title
HARD_S = WAIT_S + 15   # runJavaScript never answers on a page that did not load: quit anyway

# list mode (a creator page)
LIST_WAIT_S = 45.0     # no video card in this time: captcha, removed account, no network
LIST_STEP_S = 1.5      # one look / scroll per step
LIST_SCROLLS = 6       # at most this many scrolls for more cards
LIST_CAPTCHA_S = 20.0  # a captcha still on screen after this long: stop waiting for cards
LIST_HARD_S = 90.0


def _profile(profile_dir: Path):
    """The persistent "vizsup-douyin" profile both modes share (cookies, Douyin's own state)."""
    from PySide6.QtWebEngineCore import QWebEngineProfile

    profile = QWebEngineProfile("vizsup-douyin")
    profile.setPersistentStoragePath(str(profile_dir))
    profile.setCachePath(str(profile_dir / "cache"))
    return profile


def main(video_id: str, out: Path, profile_dir: Path) -> int:
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtWebEngineCore import (QWebEnginePage, QWebEngineSettings,
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

    profile = _profile(profile_dir)
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


# --- list mode: a creator's page ----------------------------------------------------------

# Injected at document creation (main world): keep every post-list answer the page fetches
# itself — exact titles and publish times. Best effort: the cards are read either way.
_POSTS_HOOK_JS = r"""
(function () {
  if (window.__vzPosts) return;
  window.__vzPosts = [];
  function take(u, text) {
    try {
      if (!/aweme\/v1\/web\/aweme\/post/.test(String(u))) return;
      var d = typeof text === 'string' ? JSON.parse(text) : text;
      (d && d.aweme_list || []).forEach(function (a) {
        window.__vzPosts.push({id: String(a.aweme_id || ''), title: a.desc || '',
                               published: a.create_time || null});
      });
    } catch (e) {}
  }
  var open = XMLHttpRequest.prototype.open, send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, u) { this.__vzUrl = u; return open.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function () {
    var x = this;
    x.addEventListener('load', function () {
      try {
        take(x.responseURL || x.__vzUrl,
             (x.responseType === '' || x.responseType === 'text') ? x.responseText : x.response);
      } catch (e) {}
    });
    return send.apply(this, arguments);
  };
  if (window.fetch) {
    var f = window.fetch;
    window.fetch = function (i) {
      var u = String((i && i.url) || i);
      return f.apply(this, arguments).then(function (r) {
        try { if (/aweme\/v1\/web\/aweme\/post/.test(u)) r.clone().text().then(function (t) { take(u, t); }); }
        catch (e) {}
        return r;
      });
    };
  }
})();
"""

# the post list's cards (the page footer links to unrelated videos: not those)
_CARDS_JS = r"""
function __vzCards() {
  var list = document.querySelectorAll('[data-e2e="scroll-list"] a[href*="/video/"]');
  if (!list.length) {
    list = Array.prototype.filter.call(document.querySelectorAll('li a[href*="/video/"]'),
      function (a) { return !a.closest('[data-e2e="page-footer"]'); });
  }
  return Array.prototype.slice.call(list);
}
function __vzState() {
  var body = document.body ? document.body.innerText : '';
  // every page carries a hidden "nocaptcha-container" iframe (display:none, 0x0): only a
  // captcha that is really on screen counts
  var shown = Array.prototype.some.call(document.querySelectorAll(
      '#captcha_container, .captcha_verify_container, iframe[src*="captcha"], '
      + 'iframe[src*="verifycenter"]'), function (e) {
    var r = e.getBoundingClientRect();
    return r.width > 50 && r.height > 50 && getComputedStyle(e).display !== 'none';
  });
  return {n: __vzCards().length, wall: body.indexOf('登录后查看更多') >= 0, captcha: shown};
}
"""
_COUNT_JS = _CARDS_JS + "JSON.stringify(__vzState())"
_SCROLL_JS = _CARDS_JS + r"""
(function () {
  var c = __vzCards();
  if (c.length) c[c.length - 1].scrollIntoView({block: 'end'});
  var s = document.scrollingElement;
  if (s) s.scrollTop = s.scrollHeight;
  return c.length;
})()
"""
_READ_JS = _CARDS_JS + r"""
JSON.stringify((function () {
  var cards = __vzCards().map(function (a) {
    var img = a.querySelector('img');
    return {href: a.getAttribute('href') || '', text: a.innerText || '',
            alt: img ? (img.getAttribute('alt') || '') : ''};
  });
  var st = __vzState();
  return {cards: cards, posts: window.__vzPosts || [], href: location.href,
          author: (document.querySelector('h1') || {}).innerText || '',
          title: document.title || '', wall: st.wall, captcha: st.captcha};
})())
"""

_VIDEO_ID = re.compile(r"/video/(\d{8,})")
_STAT_LINE = re.compile(r"^(?:置顶|共创|[\d.,]+\s*[万wWkK亿]?)$")


def _card_title(card: dict, author: str) -> str:
    """A card's title: the cover's alt text without its "<author>：" prefix, else the longest
    line of the card's text that is not a counter ("1229", "1.3万") or the "置顶" badge. Pure."""
    alt = str(card.get("alt") or "").strip()
    if alt:
        head, sep, rest = alt.partition("：")
        if sep and "《" not in head and (not author or head.strip() == author or len(head) <= 30):
            alt = rest.strip()
        return " ".join(alt.split())
    lines = [ln.strip() for ln in str(card.get("text") or "").splitlines()]
    lines = [ln for ln in lines if ln and not _STAT_LINE.match(ln)]
    return " ".join(max(lines, key=len).split()) if lines else ""


def merge_cards(raw: dict, limit: int = 30) -> list[dict]:
    """The page's cards (order = the page's: pinned first, then newest) joined with the
    post-list answers caught by the hook (exact title, publish time) →
    [{id, url, title, published, pinned}], at most ``limit``. Pure."""
    author = str(raw.get("author") or "").strip()
    posts = {}
    for p in raw.get("posts") or []:
        pid = str((p or {}).get("id") or "")
        if pid and pid not in posts:
            posts[pid] = p
    out: list[dict] = []
    seen: set[str] = set()

    def add(vid: str, title: str, pinned: bool) -> None:
        p = posts.get(vid) or {}
        pub = p.get("published")
        out.append({"id": vid, "url": f"https://www.douyin.com/video/{vid}",
                    "title": " ".join(str(p.get("title") or title or "").split()),
                    "published": int(pub) if isinstance(pub, (int, float)) and pub > 0 else None,
                    "pinned": pinned})
        seen.add(vid)

    for card in raw.get("cards") or []:
        m = _VIDEO_ID.search(str(card.get("href") or ""))
        if m and m.group(1) not in seen:
            add(m.group(1), _card_title(card, author), "置顶" in str(card.get("text") or ""))
    for vid in posts:                          # answered but not drawn yet
        if vid not in seen:
            add(vid, "", False)
    return out[:max(1, limit)]


def list_main(url: str, out: Path, profile_dir: Path, limit: int = 30) -> int:
    """Open a creator page, scroll for more cards, write {videos, author, href, login_wall,
    captcha} to ``out``. Exit code 2 when no video was read."""
    from PySide6.QtCore import Qt, QTimer, QUrl
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineScript
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication

    profile_dir = profile_dir.resolve()
    app = QApplication(sys.argv[:1])
    profile = _profile(profile_dir)
    page = QWebEnginePage(profile)
    hook = QWebEngineScript()
    hook.setName("vizsup-posts")
    hook.setSourceCode(_POSTS_HOOK_JS)
    hook.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    hook.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    hook.setRunsOnSubFrames(False)
    page.scripts().insert(hook)
    page.setAudioMuted(True)
    # without a viewport the list never loads more; WA_DontShowOnScreen = never on screen
    view = QWebEngineView()
    view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    view.resize(1280, 900)
    view.setPage(page)
    view.show()
    t0 = time.time()
    st: dict = {"busy": False, "n": 0, "same": 0, "scrolls": 0, "raw": {}, "done": False}

    def log(msg: str) -> None:
        print(f"{msg} after {time.time() - t0:.1f}s", file=sys.stderr, flush=True)

    def finish() -> None:
        if st["done"]:
            return
        st["done"] = True
        timer.stop()
        page.runJavaScript(_READ_JS, 0, read)

    def read(result) -> None:
        try:
            st["raw"] = json.loads(result or "{}")
        except ValueError:
            pass
        log(f"read {len((st['raw'] or {}).get('cards') or [])} cards")
        app.quit()

    def counted(result) -> None:
        st["busy"] = False
        try:
            info = json.loads(result or "{}")
        except ValueError:
            info = {}
        n = int(info.get("n") or 0)
        if not n:
            waited = time.time() - t0
            if (info.get("captcha") and waited > LIST_CAPTCHA_S) or waited > LIST_WAIT_S:
                log("no cards" + (" (captcha)" if info.get("captcha") else ""))
                finish()
            return
        st["same"] = st["same"] + 1 if n <= st["n"] else 0
        st["n"] = n
        if n >= limit or info.get("wall") or st["same"] >= 2 or st["scrolls"] >= LIST_SCROLLS:
            log(f"{n} cards" + (" (login wall)" if info.get("wall") else ""))
            finish()
            return
        st["scrolls"] += 1
        page.runJavaScript(_SCROLL_JS)

    def tick() -> None:
        if not st["busy"] and not st["done"]:
            st["busy"] = True
            page.runJavaScript(_COUNT_JS, 0, counted)

    page.loadFinished.connect(lambda ok: log(f"loaded={ok}"))
    page.load(QUrl(url))
    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(int(LIST_STEP_S * 1000))
    QTimer.singleShot(int(LIST_HARD_S * 1000), app.quit)
    app.exec()
    raw = st["raw"] or {}
    videos = merge_cards(raw, limit)
    author = str(raw.get("author") or "").strip() or \
        str(raw.get("title") or "").removesuffix(" - 抖音").removesuffix("的抖音").strip()
    out.write_text(json.dumps({"videos": videos, "author": author,
                               "href": str(raw.get("href") or ""),
                               "login_wall": bool(raw.get("wall")),
                               "captcha": bool(raw.get("captcha"))}, ensure_ascii=False),
                   encoding="utf-8")
    return 0 if videos else 2


if __name__ == "__main__":
    if sys.argv[1:2] == ["--list"]:
        sys.exit(list_main(sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4]),
                           int(sys.argv[5]) if len(sys.argv) > 5 else 30))
    sys.exit(main(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])))
