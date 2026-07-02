"""vizsup doctor — one-shot environment self-check for the whole pipeline.

Prints one line per dependency/config the pipeline relies on (ffmpeg, rubberband,
yt-dlp, OCR/ASR stacks, GPU, Vietnamese fonts, provider keys, storage) with a
concrete fix hint when something is missing. Nothing here mutates state; heavy
imports (torch) only happen if the package is already installed.

    python -m app.doctor        # or: vizsup doctor

Exit code 0 = pipeline can run (possibly degraded), 1 = a hard requirement
(ffmpeg/ffprobe) is missing.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app.config import settings
from app.providers.base import _installed

# status → (glyph, is_hard_failure)
_GLYPH = {"ok": "✓", "warn": "!", "fail": "✕", "info": "·"}

Check = tuple[str, str, str]  # (status, label, detail)


def _dist_installed(dist: str) -> bool:
    """Is a *distribution* (pip name, not import name) installed?"""
    return bool(_dist_version(dist))


def _dist_version(dist: str) -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(dist)
    except PackageNotFoundError:
        return ""


def _module_version(module: str) -> str:
    try:
        import importlib

        m = importlib.import_module(module)
        return str(getattr(m, "__version__", "") or "")
    except Exception:  # noqa: BLE001
        return ""


def _check_ffmpeg() -> list[Check]:
    from app.ffmpegutil import ffmpeg_bin, ffprobe_bin

    out: list[Check] = []
    for label, resolver in (("ffmpeg", ffmpeg_bin), ("ffprobe", ffprobe_bin)):
        try:
            exe = resolver()
            ver = ""
            try:
                r = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=10)
                first = (r.stdout or "").splitlines()[0] if r.stdout else ""
                ver = first.split("Copyright")[0].replace(f"{label} version", "").strip()
            except Exception:  # noqa: BLE001
                pass
            out.append(("ok", label, f"{ver} · {exe}".strip(" ·")))
        except RuntimeError as exc:
            out.append(("fail", label, str(exc)))
    return out


def _check_rubberband() -> Check:
    from app.pipeline.assemble import _has_rubberband

    try:
        if _has_rubberband():
            return ("ok", "rubberband", "tăng tốc lồng tiếng giữ cao độ (chất lượng tốt nhất)")
        return ("warn", "rubberband",
                "ffmpeg này không có filter rubberband — dub sẽ dùng atempo (nghe kém hơn một chút). "
                "Cài bản ffmpeg 'full' (gyan.dev) nếu muốn.")
    except Exception as exc:  # noqa: BLE001 - no ffmpeg → already reported above
        return ("info", "rubberband", f"bỏ qua (chưa có ffmpeg: {exc})")


def _check_download() -> list[Check]:
    out: list[Check] = []
    if _installed("yt_dlp"):
        out.append(("ok", "yt-dlp", _module_version("yt_dlp.version") or "đã cài"))
    else:
        out.append(("fail", "yt-dlp", 'chưa cài — pip install -e "." (backend/)'))
    if settings.ytdlp_cookies_file or settings.ytdlp_cookies_from_browser:
        src = settings.ytdlp_cookies_file or f"trình duyệt {settings.ytdlp_cookies_from_browser}"
        out.append(("ok", "cookies Douyin", src))
    else:
        out.append(("info", "cookies Douyin",
                    "chưa cấu hình — Douyin thường cần cookie (YTDLP_COOKIES_FROM_BROWSER=edge trong .env)"))
    if settings.bbdown_path:
        ok = Path(settings.bbdown_path).exists()
        out.append(("ok" if ok else "warn", "BBDown (bilibili)",
                    settings.bbdown_path if ok
                    else f"đường dẫn không tồn tại: {settings.bbdown_path}"))
    else:
        out.append(("info", "BBDown (bilibili)",
                    "chưa cấu hình — fallback khi yt-dlp lỗi (BBDOWN_PATH trong .env)"))
    return out


def _check_ocr() -> list[Check]:
    out: list[Check] = []
    if _installed("rapidocr") and not _installed("cv2"):
        # rapidocr imports cv2 at load time — without it every OCR/auto-detect run dies
        out.append(("warn", "OCR (rapidocr)",
                    "rapidocr đã cài nhưng THIẾU opencv — chạy: pip install opencv-python-headless"))
    elif _installed("rapidocr"):
        ver = _dist_version("rapidocr") or _module_version("rapidocr")
        out.append(("ok", "OCR (rapidocr)", f"v{ver or '?'} (PP-OCRv5+)"))
    elif _installed("rapidocr_onnxruntime"):
        out.append(("warn", "OCR (rapidocr)",
                    'chỉ có bản legacy rapidocr_onnxruntime — nâng cấp: pip install -e ".[ocr]"'))
    else:
        out.append(("warn", "OCR (rapidocr)",
                    'chưa cài — cần cho video có phụ đề cháy sẵn: pip install -e ".[ocr]"'))
    if _installed("onnxruntime"):
        try:
            import onnxruntime as ort

            if "CUDAExecutionProvider" in ort.get_available_providers():
                out.append(("ok", "OCR trên GPU", "onnxruntime có CUDA"))
            else:
                out.append(("info", "OCR trên GPU",
                            "onnxruntime bản CPU — muốn GPU: pip uninstall -y onnxruntime && "
                            "pip install onnxruntime-gpu"))
        except Exception as exc:  # noqa: BLE001
            out.append(("warn", "OCR trên GPU", f"onnxruntime lỗi khi nạp: {exc}"))
    # documented footgun: full opencv bundles Qt and crashes next to PySide6
    if _dist_installed("opencv-python") and _dist_installed("PySide6"):
        out.append(("fail", "opencv-python",
                    "bản FULL đứng cạnh PySide6 sẽ CRASH GUI — chạy: pip uninstall -y opencv-python "
                    "&& pip install opencv-python-headless"))
    # the other shared-dir landmine: both ORT builds fight over one package dir
    if _dist_installed("onnxruntime") and _dist_installed("onnxruntime-gpu"):
        out.append(("fail", "onnxruntime ×2",
                    "cả bản CPU lẫn GPU cùng cài — chúng ghi đè file của nhau. Chạy: "
                    "pip uninstall -y onnxruntime && pip install --force-reinstall onnxruntime-gpu"))
    return out


def _check_asr() -> list[Check]:
    out: list[Check] = []
    if _installed("faster_whisper"):
        out.append(("ok", "ASR (faster-whisper)", f"model mặc định: {settings.whisper_model}"))
    else:
        out.append(("warn", "ASR (faster-whisper)",
                    'chưa cài — cần cho video KHÔNG có phụ đề cháy sẵn: pip install -e ".[asr]"'))
    if _installed("funasr"):
        out.append(("ok", "ASR (FunASR)", "Paraformer CN-native (chính xác tiếng Trung nhất)"))
    else:
        out.append(("info", "ASR (FunASR)",
                    'tùy chọn, chính xác hơn cho tiếng Trung: pip install -e ".[asr-accurate]"'))
    if _installed("torch"):
        try:
            import torch

            if torch.cuda.is_available():
                out.append(("ok", "GPU (torch)", torch.cuda.get_device_name(0)))
            else:
                out.append(("info", "GPU (torch)", "torch bản CPU — ASR chạy chậm hơn"))
        except Exception as exc:  # noqa: BLE001
            out.append(("warn", "GPU (torch)", f"torch lỗi khi nạp: {exc}"))
    return out


def _check_tts() -> Check:
    if _installed("edge_tts"):
        return ("ok", "TTS (edge-tts)", "giọng vi-VN miễn phí, không cần key")
    return ("fail", "TTS (edge-tts)", 'chưa cài — pip install -e "." (backend/)')


def _font_dirs() -> list[Path]:
    import os

    dirs = [
        Path("C:/Windows/Fonts"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Windows/Fonts",
        Path.home() / ".fonts",
        Path("/usr/share/fonts"),
    ]
    return [d for d in dirs if d.is_dir()]


def _check_fonts() -> Check:
    """Subtitles need a Vietnamese-diacritic font (CLAUDE.md hard constraint)."""
    wanted = ("bevietnam", "notosans", "be vietnam")
    found: set[str] = set()
    for d in _font_dirs():
        try:
            for f in d.iterdir():
                n = f.name.lower().replace("-", "").replace("_", "")
                if n.startswith(wanted[0]) or "bevietnampro" in n:
                    found.add("Be Vietnam Pro")
                elif n.startswith("notosans"):
                    found.add("Noto Sans")
        except OSError:
            continue
    if found:
        return ("ok", "Font tiếng Việt", ", ".join(sorted(found)))
    return ("warn", "Font tiếng Việt",
            "không thấy Be Vietnam Pro / Noto Sans — phụ đề có thể mất dấu tiếng Việt. "
            "Tải Be Vietnam Pro từ Google Fonts rồi cài cho Windows.")


def _check_providers() -> list[Check]:
    from app.providers.registry import list_providers

    info = list_providers()
    out: list[Check] = []
    translators = [p["display_name"] for p in info["translation"]
                   if p["available"] and p["name"] != "passthrough"]
    if translators:
        out.append(("ok", "Bộ dịch có key", ", ".join(translators)))
    else:
        out.append(("warn", "Bộ dịch có key",
                    "chưa có API key nào — phụ đề sẽ KHÔNG được dịch (passthrough). "
                    "Thêm key vào backend/.env hoặc ⚙ Cài đặt."))
    tts = [p["display_name"] for p in info["tts"] if p["available"]]
    out.append(("ok" if tts else "warn", "Giọng TTS sẵn sàng", ", ".join(tts) or "không có"))
    return out


def _check_storage() -> list[Check]:
    out: list[Check] = []
    d = Path(settings.vizsup_storage_dir)
    if d.is_dir():
        try:
            probe = d / ".doctor-write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            out.append(("ok", "Thư mục storage", str(d.resolve())))
        except OSError as exc:
            out.append(("fail", "Thư mục storage", f"không ghi được {d}: {exc}"))
    else:  # don't create it — doctor must stay read-only
        out.append(("info", "Thư mục storage", f"chưa có — sẽ tạo tại {d.resolve()} khi chạy"))
    # .env is anchored to the repo root (config._ROOT) since N-20 — launch dir no
    # longer matters; report the resolved file that is actually in effect.
    from app.config import _ROOT

    env = _ROOT / ".env"
    out.append(("ok" if env.exists() else "info", "File .env",
                str(env) if env.exists()
                else f"không thấy {env} — key/cookie đọc từ biến môi trường"))
    # jobs created before the anchoring may still sit in backend/storage
    legacy = _ROOT / "backend" / "storage"
    try:
        if legacy.resolve() != d.resolve() and legacy.is_dir() and any(legacy.iterdir()):
            out.append(("warn", "storage cũ",
                        f"{legacy} còn dự án từ trước — chuyển nội dung sang "
                        f"{d.resolve()} để chúng hiện trong 'Dự án gần đây'"))
    except OSError:
        pass
    gl = d / "glossary.json"
    if gl.exists():
        out.append(("ok", "Bảng thuật ngữ", f"{gl} (tự nạp khi dịch)"))
    return out


def run_checks() -> list[Check]:
    checks: list[Check] = [
        ("info", "Python", sys.version.split()[0] + f" · {sys.executable}"),
    ]
    checks += _check_ffmpeg()
    checks.append(_check_rubberband())
    checks += _check_download()
    checks += _check_ocr()
    checks += _check_asr()
    checks.append(_check_tts())
    checks.append(_check_fonts())
    checks += _check_providers()
    checks += _check_storage()
    return checks


def main() -> int:
    try:  # Windows consoles may default to a legacy codepage → force UTF-8
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    print("vizsup doctor — kiểm tra môi trường\n" + "─" * 64)
    checks = run_checks()
    width = max(len(label) for _, label, _ in checks)
    for status, label, detail in checks:
        print(f" {_GLYPH[status]} {label.ljust(width)}  {detail}")
    fails = sum(1 for s, _, _ in checks if s == "fail")
    warns = sum(1 for s, _, _ in checks if s == "warn")
    print("─" * 64)
    if fails:
        print(f"✕ {fails} lỗi phải sửa, {warns} cảnh báo — pipeline CHƯA chạy được đầy đủ.")
    elif warns:
        print(f"! {warns} cảnh báo — pipeline chạy được nhưng thiếu một số tính năng.")
    else:
        print("✓ Mọi thứ sẵn sàng.")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
