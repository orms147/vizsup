"""Cloud OCR: an OpenRouter vision model reads the burned-in subtitles. UPLOADS frames
(only the subtitle band, downscaled) — opt-in, labelled in the UI.

Several numbered subtitle strips are stacked into one small image per request and the
model answers ``{"1": "…", "2": "", …}``. Benchmarked 2026-10-14 on 441 frames of two
real Douyin vlogs (docs/plan/E-roadmap.md "Cloud OCR"): local RapidOCR misread or missed
~31 frames (stylised captions, busy backgrounds); qwen3.7-flash 4 (2 frames of song
credits, 1 traditional character twice) for ~$0.002 — ~$0.11 for a 3-hour video;
qwen3.7-plus 0 at ~10x the price. Model: the "ocr" role (Settings → AI và mô hình).

Requests stay small (~50 KB): large uploads to OpenRouter were dropped by this
machine's network several times out of six that day, so each request is retried with
backoff, and the OCR stage checkpoints every batch (a failed run resumes). With 16
requests in flight a 137 s video took 22 s end to end (≈ 30 min for 3 hours).
"""
from __future__ import annotations

import base64
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

from app.providers.base import OCRProvider

TILE_W = 640         # strip width in the tile (px)
TILE_H = 480         # tile height budget; strips per request follow from it
LABEL_W = 56
WORKERS = 16        # measured: 6 → 78 s, 12 → 36 s, 16 → 22 s for a 137 s video
ATTEMPTS = 6

PROMPT = (
    "The image stacks {n} numbered strips. Each strip is the bottom part of ONE video frame "
    "from a Chinese short video. For EACH strip, transcribe the burned-in SUBTITLE text exactly "
    "as shown (Simplified or Traditional Chinese as written; keep English words, numbers and "
    "punctuation that are part of the subtitle). Ignore channel logos, watermarks, usernames, "
    "app UI and scrolling viewer comments. If a strip has no subtitle, use an empty string. "
    "Never guess text you cannot see. Reply with ONLY a JSON object mapping each strip number "
    "to its text, e.g. {{\"1\": \"…\", \"2\": \"\"}}."
)


def per_tile(strip_h: int, strip_w: int) -> int:
    """How many strips fit in one request at TILE_W wide. Pure."""
    h = max(1, round(strip_h * TILE_W / max(1, strip_w))) + 6
    return max(1, TILE_H // h)


def tile(images: list) -> bytes:
    """Numbered strips stacked into one JPEG."""
    import cv2
    import numpy as np

    rows = []
    for k, img in enumerate(images, 1):
        h = max(1, round(img.shape[0] * TILE_W / img.shape[1]))
        img = cv2.resize(img, (TILE_W, h), interpolation=cv2.INTER_AREA)
        label = np.full((h, LABEL_W, 3), 255, np.uint8)
        cv2.putText(label, str(k), (6, h // 2 + 12), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 200), 2)
        rows.append(np.hstack([label, img]))
        rows.append(np.full((6, TILE_W + LABEL_W, 3), (0, 200, 255), np.uint8))   # separator
    ok, buf = cv2.imencode(".jpg", np.vstack(rows[:-1]), [cv2.IMWRITE_JPEG_QUALITY, 78])
    if not ok:
        raise RuntimeError("Không mã hóa được ảnh gửi đi.")
    return buf.tobytes()


def parse(text: str, n: int) -> list[str | None]:
    """The model's JSON answer → one text per strip (None = the strip is missing). Pure."""
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return [str(data[str(k)]).strip() if str(k) in data and data[str(k)] is not None else None
            for k in range(1, n + 1)]


class VisionLLMOCR(OCRProvider):
    name = "vision_llm"
    display_name = "AI đọc chữ qua mạng (OpenRouter) — gửi dải phụ đề lên mạng"
    cost_note = ("cloud · đọc đúng chữ nghệ thuật / nền rối hơn hẳn OCR trên máy · "
                 "~$0,1 cho video 3 giờ (qwen3.7-flash) · không dùng GPU")
    local = False
    batch_frames = 400          # 200 s of video per checkpoint; keeps 16 requests busy
    crop_to_band = True

    def _client(self):
        from app.providers.registry import get_translator

        return get_translator("openrouter")

    def available(self) -> bool:
        return self._client().available()

    @property
    def model(self) -> str:
        from app.providers import roles

        return roles.model_for("ocr")

    def cache_tag(self) -> str:
        return f"{self.name}:{self.model}"

    def _ask(self, images: list, *, cancel=None) -> list[str]:
        """One request with retries; a strip the model skipped counts as empty."""
        prov = self._client()
        img = base64.b64encode(tile(images)).decode()
        content = [{"type": "text", "text": PROMPT.format(n=len(images))},
                   {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}}]
        texts: list[str | None] = [None] * len(images)
        last: Exception | None = None
        for attempt in range(ATTEMPTS):
            if cancel and cancel():
                raise InterruptedError("Đã hủy.")
            try:
                resp = prov._chat(model=self.model, temperature=0,
                                  max_tokens=80 * len(images) + 200,
                                  messages=[{"role": "user", "content": content}])
                texts = parse(resp.choices[0].message.content, len(images))
                if all(t is not None for t in texts):
                    return texts                                  # type: ignore[return-value]
            except Exception as exc:  # noqa: BLE001 - network drops: retried below
                last = exc
            if attempt < ATTEMPTS - 1:
                time.sleep(min(20.0, 1.5 * 2 ** attempt))
        if any(t is not None for t in texts):
            return [t or "" for t in texts]
        raise RuntimeError(f"Không gửi được khung hình tới {self.model} sau {ATTEMPTS} lần "
                           f"({type(last).__name__ if last else 'trả lời không đọc được'}). "
                           f"Bấm Bắt đầu lại để chạy tiếp từ khung đã đọc.")

    @contextmanager
    def reader(self, *, log=None, cancel=None):
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            def read(images: list) -> list[list[tuple[str, float]]]:
                if not images:
                    return []
                n = per_tile(images[0].shape[0], images[0].shape[1])
                groups = [images[i:i + n] for i in range(0, len(images), n)]
                texts = [t for part in ex.map(lambda g: self._ask(g, cancel=cancel), groups)
                         for t in part]
                return [[(ln.strip(), 1.0) for ln in t.splitlines() if ln.strip()] for t in texts]

            yield read
