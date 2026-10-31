"""Cài đặt (v2, appdesign/v2 "v2 — Cài đặt"): a full page, not a dialog. Left nav
Khóa API / Tải video / AI và mô hình / Đường dẫn / Đăng video / Kiểm tra; its own Lưu /
Hủy thay đổi (the editor's "Đã lưu" never means settings were saved). Everything is written
to the repo-root .env and applied to the live settings object.

Network calls (Kiểm tra kết nối, tải danh sách model, Kết nối tài khoản đăng video) run on
a daemon thread so the UI never freezes.
"""
from __future__ import annotations

import threading
import webbrowser
from datetime import date
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from desktop import paths  # noqa: F401
from desktop.icons import icon
from desktop.theme import V2
from desktop.ui_thread import connect_on_ui, emit_quietly, guarded, spawn
from desktop.widgets.chrome import badge, button, icon_button, icon_label, label
from app.config import settings  # noqa: E402
from app.doctor import run_checks  # noqa: E402
from app.providers.registry import find_provider, get_publisher  # noqa: E402
from app.tools import cookie_age_days, update_edge_tts, update_ytdlp  # noqa: E402
from app.fsutil import atomic_write_text

# (env var, display name, settings attr, provider name)
KEYS = [
    ("OPENROUTER_API_KEY", "OpenRouter", "openrouter_api_key", "openrouter"),
    ("DEEPSEEK_API_KEY", "DeepSeek", "deepseek_api_key", "deepseek"),
    ("ANTHROPIC_API_KEY", "Claude", "anthropic_api_key", "claude"),
    ("ZHIPU_API_KEY", "Zhipu / GLM-5.2", "zhipu_api_key", "glm"),
    ("GEMINI_API_KEY", "Google Gemini", "gemini_api_key", "gemini"),
    ("DASHSCOPE_API_KEY", "Qwen / DashScope", "dashscope_api_key", "qwen"),
    ("DEEPL_API_KEY", "DeepL", "deepl_api_key", "deepl"),
    ("FPT_API_KEY", "FPT.AI", "fpt_api_key", "fpt"),
    ("AZURE_SPEECH_KEY", "Azure Speech", "azure_speech_key", "azure"),
    ("STT_API_KEY", "Máy chủ STT riêng", "stt_api_key", "cloud_stt"),
]
# The owner's OWN app credentials for posting (page "Đăng video"); the account tokens are
# not here — "Kết nối" stores them in storage/_accounts/<platform>.json.
# (env var, label, settings attr, publisher name, secret?)
PUBLISH_KEYS = [
    ("YOUTUBE_CLIENT_ID", "Client ID", "youtube_client_id", "youtube", False),
    ("YOUTUBE_CLIENT_SECRET", "Client secret", "youtube_client_secret", "youtube", True),
    ("FACEBOOK_APP_ID", "App ID", "facebook_app_id", "facebook", False),
    ("FACEBOOK_APP_SECRET", "App secret", "facebook_app_secret", "facebook", True),
    ("TIKTOK_CLIENT_KEY", "Client key", "tiktok_client_key", "tiktok", False),
    ("TIKTOK_CLIENT_SECRET", "Client secret", "tiktok_client_secret", "tiktok", True),
]
PUBLISH_PLATFORMS = ("youtube", "facebook", "tiktok")
# one line per platform: what an unaudited / test-mode app can do (J-publish-setup.md)
PLATFORM_NOTES = {
    "youtube": "Google Cloud ở chế độ Testing chỉ giữ kết nối 7 ngày — bấm “Publish app” (không "
               "cần xác minh cho riêng bạn). Hạn mức ~100 video/ngày.",
    "facebook": "App Meta ở chế độ Development thì bài đăng chỉ người có vai trò trong app thấy — "
                "chuyển app sang Live. Reels 3–90 giây, tối đa 30 Reels/24 giờ.",
    "tiktok": "App chưa audit: chỉ đăng “Chỉ mình tôi” lên tài khoản riêng tư, tài khoản công khai "
              "thì vizsup gửi bản nháp vào hộp thư TikTok để bạn tự đăng.",
}
PASTE_PROMPT = ("Trình duyệt vừa mở trang đăng nhập Facebook. Đăng nhập, chọn Trang và bấm Tiếp tục —\n"
                "Facebook chuyển tới trang “Success”. Chép TOÀN BỘ địa chỉ trên thanh địa chỉ\n"
                "(có #access_token=…) rồi dán vào đây. Hoặc dán mã truy cập từ Graph API Explorer:")
ATTR_BY_ENV = ({env: attr for env, _n, attr, _p in KEYS}
               | {env: attr for env, _n, attr, _p, _s in PUBLISH_KEYS})
KEY_ROLES = {
    "DEEPSEEK_API_KEY": "Bản nháp và tinh chỉnh", "ANTHROPIC_API_KEY": "Tinh chỉnh câu tiếng Việt",
    "ZHIPU_API_KEY": "Dịch bản nháp", "GEMINI_API_KEY": "Đọc ngữ cảnh và dịch",
    "DASHSCOPE_API_KEY": "Nhận dạng và dịch", "DEEPL_API_KEY": "Dịch văn bản",
    "FPT_API_KEY": "Giọng đọc tiếng Việt", "AZURE_SPEECH_KEY": "Giọng đọc tiếng Việt",
    "STT_API_KEY": "Để trống = dùng khóa OpenRouter",
}

# Non-secret settings. Every attr already exists in app.config.Settings — this
# page only stops the user from hand-editing .env for them.
# (env var, label, settings attr, kind, hint, choices)
OPTIONS = [
    ("YTDLP_COOKIES_FILE", "Tệp cookies.txt (Douyin cần — cách chắc chắn nhất)",
     "ytdlp_cookies_file",
     "file", "Douyin bắt buộc có cookie. Cài tiện ích \"Get cookies.txt LOCALLY\" cho Edge/Chrome, "
     "mở douyin.com, bấm tiện ích → Export, rồi chọn tệp ở đây. Tệp này được ưu tiên hơn "
     "'cookie từ trình duyệt'. Cookie hết hạn sau một thời gian — khi lỗi quay lại thì xuất lại.",
     None),
    ("YTDLP_COOKIES_FROM_BROWSER", "…hoặc cookie từ trình duyệt", "ytdlp_cookies_from_browser",
     "choice", "Edge/Chrome trên Windows KHÔNG dùng được (khoá tệp khi đang mở + mã hoá "
     "App-Bound từ bản 127+). Chỉ Firefox còn đọc được. Để trống nếu đã dùng tệp cookies.txt.",
     ["", "firefox", "edge", "chrome", "brave", "chromium", "opera", "vivaldi"]),
    ("BBDOWN_PATH", "Đường dẫn BBDown", "bbdown_path",
     "file", "Bộ tải dự phòng cho Bilibili, chỉ chạy khi yt-dlp thất bại.", None),
    ("OPENROUTER_PROMO_MODEL", "Model khuyến mãi (OpenRouter)", "openrouter_promo_model",
     "text", "Dùng model này thay model chính cho tới ngày hết hạn, rồi tự quay về model chính. "
     "Bỏ trống = không dùng.", None),
    ("OPENROUTER_PROMO_EXPIRES", "Hết hạn khuyến mãi (YYYY-MM-DD)", "openrouter_promo_expires",
     "text", "Từ ngày này trở đi model khuyến mãi không được dùng nữa. Bỏ trống = không hết hạn.",
     None),
    ("AUDIO_LLM_MODEL", "Model AI nghe âm thanh", "audio_llm_model",
     "choice", "Bỏ trống = qwen/qwen3.8-omni-flash — chính xác nhất và ỔN ĐỊNH nhất trên vlog "
     "Douyin thật (01/10/2026). MiMo đôi khi nghe tốt hơn nhưng hay lặp vòng / trả rỗng / "
     "dịch sang tiếng khác — chỉ để thử.",
     ["", "qwen/qwen3.8-omni-flash", "xiaomi/mimo-v2.6-pro", "xiaomi/mimo-v2.6-flash"]),
    ("STT_API_BASE_URL", "Máy chủ STT (mốc thời gian từng từ)", "stt_api_base_url",
     "choice", "Bỏ trống = OpenRouter (dùng luôn khóa OpenRouter). Các engine này GỬI ÂM THANH "
     "lên máy chủ — chỉ chạy khi bạn chọn chúng ở màn Bắt đầu.",
     ["", "https://openrouter.ai/api/v1", "https://api.groq.com/openai/v1",
      "https://api.openai.com/v1", "https://api.siliconflow.cn/v1"]),
    ("STT_API_MODEL", "Model STT (/audio/transcriptions)", "stt_api_model",
     "choice", "Bỏ trống = openai/whisper-large-v3 (OpenRouter). Groq: whisper-large-v3.",
     ["", "openai/whisper-large-v3", "openai/gpt-4o-mini-transcribe", "whisper-large-v3"]),
    ("ZHIPU_MODEL", "Model GLM (ghi đè mặc định)", "zhipu_model",
     "text", "Bỏ trống = dùng mặc định (glm-5.2).", None),
    ("WHISPER_MODEL", "Model Whisper cục bộ (nếu có cài)", "whisper_model",
     "choice", "Chỉ dùng khi cài engine nhận dạng cục bộ. Nhỏ hơn = nhanh hơn nhưng kém chính "
     "xác. Card 8 GB chạy được large-v3.",
     ["tiny", "base", "small", "medium", "large-v2", "large-v3"]),
    ("VIZSUP_STORAGE_DIR", "Thư mục lưu dự án", "vizsup_storage_dir",
     "dir", "Mỗi video là một thư mục con. Đổi ở đây KHÔNG di chuyển dự án cũ.", None),
    ("FFMPEG_PATH", "Đường dẫn ffmpeg", "ffmpeg_path",
     "file", "Trỏ tới ffmpeg.exe hoặc thư mục bin của nó. Bỏ trống = tìm trong PATH.", None),
]
OPT_ATTR_BY_ENV = {env: attr for env, _n, attr, _k, _h, _c in OPTIONS}
GROUPS = (("keys", "Khóa API", "KeyRound"), ("download", "Tải video", "Download"),
          ("ai", "AI và mô hình", "Sparkles"), ("paths", "Đường dẫn", "HardDrive"),
          ("accounts", "Đăng video", "Upload"), ("check", "Kiểm tra", "ShieldCheck"))
# doctor status → (icon, theme colour) for the Kiểm tra rows
_CHECK_STYLE = {"ok": ("CircleCheck", "green"), "warn": ("TriangleAlert", "amber"),
                "fail": ("X", "red"), "info": ("CircleHelp", "muted")}
OPT_GROUP = {"YTDLP_COOKIES_FILE": "download", "YTDLP_COOKIES_FROM_BROWSER": "download",
             "BBDOWN_PATH": "download", "VIZSUP_STORAGE_DIR": "paths", "FFMPEG_PATH": "paths"}
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def _write_env(updates: dict[str, str]) -> None:
    lines = _ENV_PATH.read_text(encoding="utf-8").splitlines() if _ENV_PATH.exists() else []
    seen = set()
    for i, ln in enumerate(lines):
        if "=" in ln and not ln.lstrip().startswith("#"):
            k = ln.split("=", 1)[0].strip()
            if k in updates:
                lines[i] = f"{k}={updates[k]}"
                seen.add(k)
    for k, v in updates.items():
        if k not in seen:
            lines.append(f"{k}={v}")
    atomic_write_text(_ENV_PATH, "\n".join(lines) + "\n", encoding="utf-8")


class _NetWorker(QObject):
    """Runs a blocking function once on a worker thread; emits its result/exception."""

    done = Signal(object)

    def __init__(self, fn) -> None:
        super().__init__()
        self._fn = fn

    @Slot()
    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001
            result = exc
        emit_quietly(self.done, result)


def _card() -> QFrame:
    f = QFrame()
    f.setObjectName("panel")
    f.setStyleSheet(f"QFrame#panel{{background:{V2['panel']};border:1px solid {V2['line']};"
                    f"border-radius:10px;}}")
    return f


def _ytdlp_installed_version() -> str:
    return _installed_version("yt-dlp")


def _installed_version(dist: str) -> str:
    try:
        return version(dist)
    except PackageNotFoundError:
        return "chưa cài"


class SettingsView(QWidget):
    saved = Signal()        # written to .env + applied → providers may have changed
    closed = Signal()       # Quay lại (after confirming discard if dirty)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._fields: dict[str, QLineEdit] = {}
        self._opts: dict[str, QWidget] = {}
        self._status: dict[str, QLabel] = {}
        self._badges: dict[str, QLabel] = {}
        self._buttons: list[QPushButton] = []
        self._jobs: list[tuple] = []  # (thread, _NetWorker) — the worker stays referenced until done
        self._acct: dict[str, dict] = {}            # publisher name → its card's widgets
        self._acct_cancel: threading.Event | None = None   # the browser sign-in being waited on
        self._dirty = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        outer = QHBoxLayout()
        outer.addStretch(1)
        page = QWidget()
        page.setMaximumWidth(1180)
        outer.addWidget(page, 12)
        outer.addStretch(1)
        root.addLayout(outer, 1)
        pv = QVBoxLayout(page)
        pv.setContentsMargins(24, 18, 24, 14)
        pv.setSpacing(14)

        head = QHBoxLayout()
        head.setSpacing(16)
        head.addWidget(label("Cài đặt", "h1"))
        head.addWidget(label("Mặc định cho những video tiếp theo", "secondary"))
        head.addStretch(1)
        back = button("Quay lại", "ArrowLeft", variant="ghost")
        back.clicked.connect(self.request_close)
        head.addWidget(back)
        pv.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(20)
        nav = QVBoxLayout()
        nav.setSpacing(4)
        self._nav_btns: list[QPushButton] = []
        for i, (_key, text, ic) in enumerate(GROUPS):
            b = button(text, ic, variant="ghost", checkable=True, icon_size=17)
            b.setStyleSheet("QPushButton{text-align:left;padding:0 14px;min-height:40px;"
                            "font-size:13px;}")
            b.clicked.connect(lambda _=False, k=i: self._show_group(k))
            nav.addWidget(b)
            self._nav_btns.append(b)
        nav.addStretch(1)
        nav.addWidget(icon_label("HardDrive", V2["secondary"], 15))
        nav.addWidget(label("Cấu hình được lưu trên máy.\nKhông cần tài khoản ứng dụng.", "small"))
        navw = QWidget()
        navw.setFixedWidth(196)
        navw.setLayout(nav)
        nav.setContentsMargins(0, 0, 0, 0)
        body.addWidget(navw)
        vline = QFrame()
        vline.setFixedWidth(1)
        vline.setStyleSheet(f"background:{V2['line_soft']};")
        body.addWidget(vline)

        right = QVBoxLayout()
        right.setSpacing(0)
        stack_host = QWidget()
        self._stack = QStackedLayout(stack_host)
        self._stack.addWidget(self._scroll(self._keys_page()))
        for key, _t, _i in GROUPS[1:]:
            page = {"check": self._check_page,
                    "accounts": self._accounts_page}.get(key, lambda k=key: self._options_page(k))()
            self._stack.addWidget(self._scroll(page))
        right.addWidget(stack_host, 1)
        foot = QFrame()
        foot.setStyleSheet(f"QFrame{{border-top:1px solid {V2['line_soft']};}} "
                           f"QLabel{{border:none;}}")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(0, 12, 0, 0)
        self.state_lbl = label("Cấu hình hiện tại", "small")
        fl.addWidget(self.state_lbl)
        fl.addStretch(1)
        self.cancel_btn = button("Hủy thay đổi")
        self.cancel_btn.clicked.connect(self.load_values)
        self.save_btn = button("Lưu thay đổi", "Check", variant="primary")
        self.save_btn.clicked.connect(self._save)
        self._buttons.append(self.save_btn)
        fl.addWidget(self.cancel_btn)
        fl.addWidget(self.save_btn)
        right.addWidget(foot)
        body.addLayout(right, 1)
        pv.addLayout(body, 1)

        self._show_group(0)
        self.load_values()

    @staticmethod
    def _scroll(w: QWidget) -> QScrollArea:
        s = QScrollArea()
        s.setWidgetResizable(True)
        s.setFrameShape(QFrame.NoFrame)
        s.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        s.setWidget(w)
        return s

    def _show_group(self, i: int) -> None:
        self._stack.setCurrentIndex(i)
        for k, b in enumerate(self._nav_btns):
            b.setChecked(k == i)

    def show_group(self, key: str) -> None:
        self._show_group(next((i for i, (k, _t, _c) in enumerate(GROUPS) if k == key), 0))

    # --- Khóa API ---------------------------------------------------------------
    def _keys_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(4, 4, 12, 12)
        v.setSpacing(14)
        v.addWidget(label("Một khóa, bắt đầu dễ hơn.", "h1"))
        v.addWidget(label("OpenRouter dùng cho nhận dạng giọng nói, đọc ngữ cảnh và dịch.\n"
                          "Thêm dịch vụ khác khi bạn muốn đổi công cụ.", "secondary"))

        card = _card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(10)
        top = QHBoxLayout()
        top.addWidget(label("OpenRouter", "h2"))
        self._badges["OPENROUTER_API_KEY"] = badge("Đang dùng", "purple")
        top.addWidget(self._badges["OPENROUTER_API_KEY"])
        top.addStretch(1)
        top.addWidget(label("Một khóa cho nhiều mô hình", "small"))
        cl.addLayout(top)
        cl.addWidget(label("Khóa API", "small"))
        cl.addLayout(self._key_row("OPENROUTER_API_KEY", "openrouter_api_key", "openrouter"))
        lock = QHBoxLayout()
        lock.addWidget(icon_label("LockKeyhole", V2["muted"], 14))
        lock.addWidget(label("Khóa được lưu trong cấu hình trên máy của bạn (.env).", "small"), 1)
        cl.addLayout(lock)
        v.addWidget(card)

        v.addWidget(label("DỊCH VỤ KHÁC · TÙY CHỌN", "eyebrow"))
        grid = QGridLayout()
        grid.setSpacing(10)
        for i, (env, name, attr, prov) in enumerate(KEYS[1:]):
            grid.addWidget(self._mini_key_card(env, name, attr, prov), i // 2, i % 2)
        v.addLayout(grid)
        v.addWidget(label("Công cụ chưa có khóa vẫn xuất hiện trong danh sách để bạn biết vì "
                          "sao chưa dùng được.", "small", wrap=True))
        v.addStretch(1)
        return page

    def _key_row(self, env: str, attr: str, prov: str) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(8)
        edit = QLineEdit()
        edit.setEchoMode(QLineEdit.Password)
        edit.setObjectName("mono")
        edit.setPlaceholderText("Dán khóa…")
        edit.textEdited.connect(self._mark_dirty)
        reveal = icon_button("Eye", "Hiện / ẩn khóa", checkable=True)
        reveal.toggled.connect(
            lambda on, e=edit: e.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        test = button("Kiểm tra kết nối", "Wifi")
        test.clicked.connect(lambda _=False, e=env, a=attr, p=prov: self._test(e, a, p))
        self._buttons.append(test)
        row.addWidget(edit, 1)
        row.addWidget(reveal)
        row.addWidget(test)
        box.addLayout(row)
        status = label("", "small")
        status.setWordWrap(True)
        status.setVisible(False)
        self._status[env] = status
        box.addWidget(status)
        self._fields[env] = edit
        return box

    def _mini_key_card(self, env: str, name: str, attr: str, prov: str) -> QFrame:
        card = _card()
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 10, 10)
        v.setSpacing(6)
        top = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(label(name, "h3"))
        col.addWidget(label(KEY_ROLES.get(env, ""), "small"))
        top.addLayout(col, 1)
        self._badges[env] = badge("Chưa thêm")
        top.addWidget(self._badges[env], 0, Qt.AlignVCenter)
        exp = icon_button("Plus", f"Thêm / sửa khóa {name}", checkable=True)
        top.addWidget(exp, 0, Qt.AlignVCenter)
        v.addLayout(top)
        body = QWidget()
        body.setLayout(self._key_row(env, attr, prov))
        body.layout().setContentsMargins(0, 4, 0, 0)
        body.setVisible(False)
        exp.toggled.connect(body.setVisible)
        exp.toggled.connect(lambda on, b=exp: b.setIcon(
            icon("X" if on else "Plus", V2["secondary"], 16, active=V2["purple_bright"])))
        v.addWidget(body)
        return card

    # --- option groups ------------------------------------------------------------
    def _options_page(self, group: str) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(4, 4, 12, 12)
        v.setSpacing(12)
        title, sub = {
            "download": ("Tải video", "Douyin cần cookie; Bilibili tải được ngay. Chỉ lưu trên máy."),
            "ai": ("AI và mô hình", "Mô hình dịch, nghe âm thanh và khuyến mãi đang dùng."),
            "paths": ("Thư mục và công cụ", "Nơi lưu dự án và công cụ dựng video."),
        }[group]
        v.addWidget(label(title, "h1"))
        v.addWidget(label(sub, "secondary"))
        if group == "download":
            v.addWidget(self._ytdlp_card())
        if group == "paths":
            v.addWidget(self._edge_card())
            v.addWidget(self._vieneu_card())
        if group == "ai":
            v.addWidget(self._model_card())
            v.addWidget(self._catalog_card())
            self.promo_state = label("", "small")
            self.promo_state.setWordWrap(True)
        for spec in OPTIONS:
            if OPT_GROUP.get(spec[0], "ai") == group:
                v.addWidget(self._opt_card(*spec))
                if spec[0] == "OPENROUTER_PROMO_EXPIRES":
                    v.addWidget(self.promo_state)
                if spec[0] == "YTDLP_COOKIES_FILE":
                    self.cookie_state = label("", "small", wrap=True)
                    v.addWidget(self.cookie_state)
                    self._opts[spec[0]].textChanged.connect(self._update_cookie_state)
        v.addStretch(1)
        return page

    def _model_card(self) -> QFrame:
        card = _card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(6)
        from app.providers import presets
        from app.providers.roles import ROLES, default_for

        # the package: one choice sets every text role (app/providers/presets.py)
        cl.addWidget(label("Gói mô hình", "h3"))
        self.preset_combo = QComboBox()
        for p in (presets.effective(k) for k in presets.PRESETS):
            self.preset_combo.addItem(f"{p.label} — ≈ ${p.usd_per_min:.4f}/phút", p.key)
        self.preset_combo.addItem("Tùy chỉnh — bạn tự chọn model cho từng việc", presets.CUSTOM)
        self.preset_combo.setToolTip("Chi phí đo thật 27/10/2026 tới bước sửa phụ đề (nhận dạng "
                                     "giọng nói + dịch + AI soát), vlog nói nhiều ~27 câu/phút.")
        self.preset_combo.currentIndexChanged.connect(self._preset_chosen)
        cl.addWidget(self.preset_combo)
        self.preset_hint = label("", "small", wrap=True)
        cl.addWidget(self.preset_hint)
        self._base_preset = presets.DEFAULT     # the package the role rows fall back to

        head = QHBoxLayout()
        head.addWidget(label("Mô hình cho từng việc (OpenRouter)", "h3"), 1)
        fetch = button("Tải danh sách model", "RefreshCw", variant="ghost", small=True)
        fetch.clicked.connect(self._fetch_models)
        self._buttons.append(fetch)
        head.addWidget(fetch)
        cl.addLayout(head)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        self.role_combos: dict[str, QComboBox] = {}
        for row, r in enumerate(ROLES.values()):
            name = label(r.label, "secondary")
            name.setToolTip(r.hint)
            combo = QComboBox()
            combo.setEditable(True)
            combo.setMinimumWidth(280)
            combo.addItems([""] + presets.known_models())   # any OpenRouter id can be typed
            combo.lineEdit().setPlaceholderText(f"mặc định · {default_for(r.key)}")
            combo.setToolTip(r.hint + "\nĐể trống = theo gói mô hình đã chọn. Gõ được mọi id "
                             "OpenRouter (vd. openai/gpt-6-luna).")
            combo.editTextChanged.connect(self._mark_dirty)
            combo.editTextChanged.connect(lambda _t, k=r.key: self._role_edited(k))
            self.role_combos[r.key] = combo
            grid.addWidget(name, row, 0)
            grid.addWidget(combo, row, 1)
        grid.setColumnStretch(1, 1)
        cl.addLayout(grid)
        self.model_status = label("Để trống = theo gói đã chọn (chấm mù 27/10/2026 trên video "
                                  "thật, xem docs/plan/G-cost-log.md).", "small")
        self.model_status.setWordWrap(True)
        cl.addWidget(self.model_status)
        return card

    # --- model package ----------------------------------------------------------------
    def _show_package(self, key: str) -> None:
        """Select ``key`` in the package list without side effects; refresh hint and the
        role rows' placeholders (what a blank row will use)."""
        from app.providers import presets
        from app.providers.roles import default_for

        self.preset_combo.blockSignals(True)
        self.preset_combo.setCurrentIndex(max(0, self.preset_combo.findData(key)))
        self.preset_combo.blockSignals(False)
        base = presets.effective(self._base_preset)
        if key == presets.CUSTOM:
            self.preset_hint.setText(f"Tùy chỉnh: các ô để trống vẫn theo gói “{base.label}”. "
                                     f"Chọn lại một gói ở trên để bỏ các lựa chọn riêng.")
        else:
            self.preset_hint.setText(f"{base.hint}\n{base.cost_label()} (đo thật 27/10/2026).")
        models = presets.models(self._base_preset)
        for k, combo in self.role_combos.items():
            src = "theo gói" if k in models else "mặc định"     # vision / ocr: not packaged
            combo.lineEdit().setPlaceholderText(f"{src} · {models.get(k) or default_for(k)}")

    def _preset_chosen(self, _i: int) -> None:
        from app.providers import presets
        from app.providers.roles import default_for

        key = self.preset_combo.currentData()
        if key == presets.CUSTOM:
            # show what each role uses now, ready to edit
            models = presets.models(self._base_preset)
            for k in presets.TEXT_ROLES:
                combo = self.role_combos[k]
                if not combo.currentText().strip():
                    combo.blockSignals(True)
                    combo.setEditText(models.get(k) or default_for(k))
                    combo.blockSignals(False)
        else:
            # a package replaces every hand-picked text-role model
            self._base_preset = key
            for k in presets.TEXT_ROLES:
                combo = self.role_combos[k]
                combo.blockSignals(True)
                combo.setEditText("")
                combo.blockSignals(False)
        self._show_package(key)
        self._mark_dirty()

    def _role_edited(self, role: str) -> None:
        from app.providers import presets

        if role in presets.TEXT_ROLES and self.role_combos[role].currentText().strip():
            self._show_package(presets.CUSTOM)

    # --- model catalog updates (app/catalog.py) ---------------------------------------
    def _catalog_card(self) -> QFrame:
        from app import catalog

        card = _card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(label("Cập nhật model mới", "h3"), 1)
        self.catalog_btn = button("Kiểm tra ngay", "RefreshCw", variant="ghost", small=True)
        self.catalog_btn.clicked.connect(self._check_catalog)
        self._buttons.append(self.catalog_btn)
        head.addWidget(self.catalog_btn)
        cl.addLayout(head)
        cl.addWidget(label("Mỗi ngày vizsup xem danh sách model khuyên dùng trên trang vizsup "
                           "(GitHub) — model mới, rẻ hơn được dùng ngay, không cần cài lại. Chỉ "
                           "nhận model OpenRouter còn hoạt động; model bạn tự chọn ở trên giữ "
                           "nguyên. Không gửi dữ liệu nào của bạn.", "small", wrap=True))
        self.catalog_status = label(catalog.summary(), "small", wrap=True)
        cl.addWidget(self.catalog_status)
        return card

    def _check_catalog(self) -> None:
        from app import catalog

        self.catalog_status.setStyleSheet(f"color:{V2['muted']};")
        self.catalog_status.setText("Đang kiểm tra…")
        self._bg(catalog.refresh, self._catalog_done)

    def _catalog_done(self, res) -> None:
        from app import catalog
        from app.providers import presets

        self._set_busy(False)
        if isinstance(res, Exception) or not res.get("ok"):
            why = res if isinstance(res, Exception) else "; ".join(res["problems"][:3])
            self.catalog_status.setText(f"✗ {why} — vẫn dùng danh sách cũ.")
            self.catalog_status.setStyleSheet(f"color:{V2['red']};")
            return
        bits = [catalog.summary()]
        bits.append(f"Đổi {len(res['changes'])} model." if res["changes"] else "Không có gì mới.")
        self.catalog_status.setText(" ".join(bits))
        self.catalog_status.setToolTip("\n".join(res["changes"]))
        self.catalog_status.setStyleSheet(f"color:{V2['green']};")
        for i in range(self.preset_combo.count()):          # package names / prices
            key = self.preset_combo.itemData(i)
            if key in presets.PRESETS:
                p = presets.effective(key)
                self.preset_combo.setItemText(i, f"{p.label} — ≈ ${p.usd_per_min:.4f}/phút")
        self._show_package(self.preset_combo.currentData())

    # --- Tải video: yt-dlp upkeep and cookie age -------------------------------------
    def _ytdlp_card(self) -> QFrame:
        card = _card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(label("Công cụ tải video", "h3"), 1)
        self.ytdlp_ver = label("", "secondary")
        head.addWidget(self.ytdlp_ver)
        self.ytdlp_btn = button("Cập nhật yt-dlp", "RefreshCw", variant="ghost", small=True)
        self.ytdlp_btn.clicked.connect(self._update_ytdlp)
        self._buttons.append(self.ytdlp_btn)
        head.addWidget(self.ytdlp_btn)
        cl.addLayout(head)
        cl.addWidget(label("Douyin hay đổi cách chặn tải — cập nhật khi tải lỗi.", "small",
                           wrap=True))
        self.ytdlp_status = label("", "small", wrap=True)
        self.ytdlp_status.setVisible(False)
        cl.addWidget(self.ytdlp_status)
        self._refresh_ytdlp_version()
        return card

    # --- edge-tts (the free voices): same upkeep as yt-dlp ------------------------------
    def _edge_card(self) -> QFrame:
        card = _card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(label("Giọng đọc miễn phí (edge-tts)", "h3"), 1)
        self.edge_ver = label(f"Phiên bản: {_installed_version('edge-tts')}", "secondary")
        head.addWidget(self.edge_ver)
        self.edge_btn = button("Cập nhật edge-tts", "RefreshCw", variant="ghost", small=True)
        self.edge_btn.clicked.connect(self._update_edge)
        self._buttons.append(self.edge_btn)
        head.addWidget(self.edge_btn)
        cl.addLayout(head)
        cl.addWidget(label("Microsoft thỉnh thoảng đổi cách kết nối — lồng tiếng báo lỗi 403 "
                           "thì cập nhật rồi khởi động lại vizsup.", "small", wrap=True))
        self.edge_status = label("", "small", wrap=True)
        self.edge_status.setVisible(False)
        cl.addWidget(self.edge_status)
        return card

    # --- VieNeu: local Vietnamese voices (app/tools.install_vieneu) ----------------------
    def _vieneu_state(self) -> str:
        from app.providers.tts import vieneu

        if not vieneu.VieNeuTTS().available():
            return "Chưa cài."
        return ("Đã cài · model sẵn sàng." if vieneu.model_cached()
                else "Đã cài · chưa tải model (~580 MB, lần đầu dùng sẽ tự tải).")

    def _vieneu_card(self) -> QFrame:
        from app.providers.tts import vieneu

        card = _card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(label("Giọng đọc trên máy (VieNeu)", "h3"), 1)
        self.vieneu_ver = label(self._vieneu_state(), "secondary")
        head.addWidget(self.vieneu_ver)
        self.vieneu_btn = button("Cài / tải giọng", "Download", variant="ghost", small=True)
        self.vieneu_btn.clicked.connect(self._install_vieneu)
        self._buttons.append(self.vieneu_btn)
        head.addWidget(self.vieneu_btn)
        cl.addLayout(head)
        cl.addWidget(label("Giọng miễn phí chạy ngay trên máy, không cần mạng khi đã tải: "
                           + ", ".join(vieneu.VOICES) + ". Tải một lần ~600 MB, vài phút. "
                           "Chọn giọng ở màn Dựng video (ghi “· trên máy”).", "small", wrap=True))
        self.vieneu_status = label("", "small", wrap=True)
        self.vieneu_status.setVisible(False)
        cl.addWidget(self.vieneu_status)
        return card

    def _install_vieneu(self) -> None:
        from app.tools import install_vieneu

        self.vieneu_status.setVisible(True)
        self.vieneu_status.setStyleSheet(f"color:{V2['muted']};")
        self.vieneu_status.setText("Đang cài và tải giọng VieNeu (~600 MB)… có thể mất vài phút.")
        self._bg(install_vieneu, self._install_vieneu_done)

    def _install_vieneu_done(self, res) -> None:
        self._set_busy(False)
        ok, msg = (False, f"{type(res).__name__}: {res}") if isinstance(res, Exception) else res
        self.vieneu_ver.setText(self._vieneu_state())
        self.vieneu_status.setText(f"✓ {msg} Khởi động lại vizsup để thấy giọng mới." if ok
                                   else f"✗ {msg}")
        self.vieneu_status.setStyleSheet(f"color:{V2['green'] if ok else V2['red']};")

    def _update_edge(self) -> None:
        self.edge_status.setVisible(True)
        self.edge_status.setStyleSheet(f"color:{V2['muted']};")
        self.edge_status.setText("Đang cập nhật edge-tts…")
        self._bg(update_edge_tts, self._update_edge_done)

    def _update_edge_done(self, res) -> None:
        self._set_busy(False)
        ok, msg = (False, f"{type(res).__name__}: {res}") if isinstance(res, Exception) else res
        self.edge_ver.setText(f"Phiên bản: {_installed_version('edge-tts')}")
        self.edge_status.setText(f"✓ {msg} — khởi động lại vizsup để dùng bản mới." if ok
                                 else f"✗ {msg}")
        self.edge_status.setStyleSheet(f"color:{V2['green'] if ok else V2['red']};")

    def _refresh_ytdlp_version(self) -> None:
        self.ytdlp_ver.setText(f"Phiên bản: {_ytdlp_installed_version()}")

    def _update_ytdlp(self) -> None:
        self.ytdlp_status.setVisible(True)
        self.ytdlp_status.setStyleSheet(f"color:{V2['muted']};")
        self.ytdlp_status.setText("Đang cập nhật yt-dlp… có thể mất vài chục giây.")
        self._bg(update_ytdlp, self._update_ytdlp_done)

    def _update_ytdlp_done(self, res) -> None:
        self._set_busy(False)
        if isinstance(res, Exception):
            ok, msg = False, f"{type(res).__name__}: {res}"
        else:
            ok, msg = res
        self._refresh_ytdlp_version()
        if ok:
            self.ytdlp_status.setText(f"✓ {msg} — khởi động lại vizsup để dùng bản mới.")
        else:
            self.ytdlp_status.setText(f"✗ {msg}")
        self.ytdlp_status.setStyleSheet(f"color:{V2['green'] if ok else V2['red']};")

    def _update_cookie_state(self, *_a) -> None:
        age = cookie_age_days(self._opt_value("YTDLP_COOKIES_FILE"))
        lbl = self.cookie_state
        lbl.setVisible(age is not None)
        if age is None:
            return
        days = int(age)
        if age > 20:
            lbl.setText(f"Tệp cookie đã {days} ngày — Douyin thường hết hạn sau khoảng 3 tuần, "
                        "nên xuất lại.")
            lbl.setStyleSheet(f"color:{V2['amber']};")
        else:
            lbl.setText(f"Tệp cookie: {days} ngày tuổi.")
            lbl.setStyleSheet(f"color:{V2['muted']};")

    # --- Đăng video: link YouTube / Facebook / TikTok accounts (providers/publish) -------
    def _accounts_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(4, 4, 12, 12)
        v.setSpacing(12)
        v.addWidget(label("Tài khoản đăng video", "h1"))
        v.addWidget(label("Nối kênh YouTube, Trang Facebook và TikTok một lần để vizsup tự đăng "
                          "video đã dựng — qua API chính thức, bằng ứng dụng do chính bạn tạo; "
                          "vizsup không bao giờ hỏi mật khẩu. Hướng dẫn từng bước: "
                          "docs/plan/J-publish-setup.md.", "secondary", wrap=True))
        for name in PUBLISH_PLATFORMS:
            v.addWidget(self._account_card(name))
        lock = QHBoxLayout()
        lock.addWidget(icon_label("LockKeyhole", V2["muted"], 14))
        lock.addWidget(label("Mã ứng dụng lưu trong .env; mã truy cập tài khoản lưu trong "
                             "storage/_accounts trên máy này — không gửi đi đâu khác.", "small",
                             wrap=True), 1)
        v.addLayout(lock)
        v.addStretch(1)
        return page

    def _account_card(self, name: str) -> QFrame:
        pub = get_publisher(name)
        card = _card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(8)
        top = QHBoxLayout()
        top.addWidget(label(pub.display_name, "h2"))
        badge_w = badge("Chưa nối")
        top.addWidget(badge_w, 0, Qt.AlignVCenter)
        top.addStretch(1)
        cancel = button("Hủy", "X", variant="ghost", small=True)
        cancel.setVisible(False)
        cancel.clicked.connect(self._cancel_account)
        connect = button("Kết nối", "Link", variant="primary", small=True)
        connect.clicked.connect(lambda _=False, n=name: self._connect_account(n))
        drop = button("Ngắt kết nối", "Trash2", variant="ghost", small=True)
        drop.clicked.connect(lambda _=False, n=name: self._disconnect_account(n))
        self._buttons += [connect, drop]
        for b in (cancel, connect, drop):
            top.addWidget(b)
        cl.addLayout(top)
        status = label("", "small", wrap=True)
        cl.addWidget(status)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        row = 0
        for env, text, _attr, plat, secret in PUBLISH_KEYS:
            if plat != name:
                continue
            edit = QLineEdit()
            edit.setObjectName("mono")
            edit.setPlaceholderText(f"Dán {text}…")
            edit.textEdited.connect(self._mark_dirty)
            if secret:
                edit.setEchoMode(QLineEdit.Password)
            name_lbl = label(text, "secondary")
            name_lbl.setFixedWidth(96)              # the three cards' fields line up
            grid.addWidget(name_lbl, row, 0)
            grid.addWidget(edit, row, 1)
            if secret:
                reveal = icon_button("Eye", "Hiện / ẩn", checkable=True)
                reveal.toggled.connect(
                    lambda on, e=edit: e.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
                grid.addWidget(reveal, row, 2)
            self._fields[env] = edit
            row += 1
        grid.setColumnStretch(1, 1)
        cl.addLayout(grid)
        cl.addWidget(label(PLATFORM_NOTES[name], "small", wrap=True))
        self._acct[name] = {"badge": badge_w, "status": status, "connect": connect,
                            "drop": drop, "cancel": cancel}
        return card

    def _publish_creds(self, name: str) -> dict[str, str]:
        return {env: self._fields[env].text().strip()
                for env, _t, _a, plat, _s in PUBLISH_KEYS if plat == name}

    def _account_status(self, name: str, text: str, color: str = "muted") -> None:
        lbl = self._acct[name]["status"]
        lbl.setText(text)
        lbl.setStyleSheet(f"color:{V2[color]};")

    def _refresh_accounts(self) -> None:
        """Badge / status / buttons of every platform card from the stored accounts (local
        files only — no network on the GUI thread)."""
        for name, w in self._acct.items():
            pub = get_publisher(name)
            linked = pub.connected()
            has_app = pub.available() or all(self._publish_creds(name).values())
            hint = getattr(pub, "account_hint", lambda: "")()
            b = w["badge"]
            if linked:
                b.setText("Đã nối")
                b.setObjectName("badgeOk")
                self._account_status(name, f"Đã nối: {pub.account_label() or '—'}"
                                     + (f"\n{hint}" if hint else ""), "green")
            elif not has_app:
                b.setText("Chưa có mã ứng dụng")
                b.setObjectName("badge")
                self._account_status(name, "Nhập mã ứng dụng của bạn (xem hướng dẫn) rồi bấm "
                                           "Kết nối.")
            else:
                b.setText("Chưa nối")
                b.setObjectName("badgeWarn" if hint else "badge")
                self._account_status(name, hint or "Chưa nối — bấm Kết nối để đăng nhập trong "
                                                   "trình duyệt.", "amber" if hint else "muted")
            b.style().unpolish(b)
            b.style().polish(b)
            w["connect"].setText("Kết nối lại" if linked else "Kết nối")
            w["drop"].setVisible(linked)

    def _connect_account(self, name: str) -> None:
        pub = get_publisher(name)
        creds = self._publish_creds(name)
        if not all(creds.values()):
            self._account_status(name, "Nhập đủ mã ứng dụng trước khi Kết nối.", "red")
            return
        # every later upload / token refresh needs them: keep them now (only these keys)
        _write_env(creds)
        for env, value in creds.items():
            setattr(settings, ATTR_BY_ENV[env], value)
        if getattr(pub, "auth_kind", "") == "paste":
            self._connect_by_paste(name, pub)
            return
        self._acct_cancel = cancel = threading.Event()
        self._acct[name]["cancel"].setVisible(True)
        self._account_status(name, "Đang chờ bạn đăng nhập và cho phép trong trình duyệt… "
                                   "(tối đa 5 phút)")
        self._bg(lambda: pub.connect(cancel=cancel.is_set),
                 lambda res, n=name: self._account_done(n, res))

    def _connect_by_paste(self, name: str, pub) -> None:
        webbrowser.open(pub.login_url())
        text, ok = QInputDialog.getText(self, f"Kết nối {pub.display_name}", PASTE_PROMPT)
        if not ok or not text.strip():
            self._account_status(name, "Chưa kết nối.")
            return
        self._account_status(name, "Đang lấy danh sách Trang…")
        self._bg(lambda t=text: pub.fetch_pages(t),
                 lambda res, n=name: self._pages_done(n, res))

    def _pages_done(self, name: str, res) -> None:
        self._set_busy(False)
        if isinstance(res, Exception):
            self._account_status(name, f"✗ {self._why(res)}", "red")
            return
        pages = [p for p in res if p.get("can_post")]
        if not pages:
            self._account_status(name, "✗ Không thấy Trang nào bạn có quyền đăng bài (cần quyền "
                                       "tạo nội dung trên Trang).", "red")
            return
        names = [p["name"] for p in pages]
        if len(set(names)) < len(names):
            names = [f"{p['name']} · {p['id']}" for p in pages]
        pick = pages[0]
        if len(pages) > 1:
            item, ok = QInputDialog.getItem(self, "Chọn Trang", "Đăng video lên Trang:", names,
                                            0, False)
            if not ok:
                self._account_status(name, "Chưa chọn Trang — chưa kết nối.")
                return
            pick = pages[names.index(item)]
        try:
            res = get_publisher(name).choose_page(pick["id"])
        except Exception as exc:  # noqa: BLE001 - shown to the owner
            res = exc
        self._account_done(name, res, busy=False)

    def _account_done(self, name: str, res, *, busy: bool = True) -> None:
        if busy:
            self._set_busy(False)
        self._acct[name]["cancel"].setVisible(False)
        self._acct_cancel = None
        self._refresh_accounts()
        if isinstance(res, Exception):
            self._account_status(name, f"✗ {self._why(res)}", "red")
        else:
            self._account_status(name, f"✓ Đã nối: {res}", "green")

    def _cancel_account(self) -> None:
        if self._acct_cancel is not None:
            self._acct_cancel.set()

    def _disconnect_account(self, name: str) -> None:
        pub = get_publisher(name)
        r = QMessageBox.question(self, "Ngắt kết nối",
                                 f"Ngắt kết nối {pub.display_name} ({pub.account_label()})? vizsup "
                                 "sẽ không đăng lên đó nữa cho tới khi Kết nối lại.")
        if r != QMessageBox.StandardButton.Yes:
            return
        self._account_status(name, "Đang ngắt kết nối…")
        self._bg(pub.disconnect, lambda res, n=name: self._disconnected(n, res))

    def _disconnected(self, name: str, res) -> None:
        self._set_busy(False)
        self._refresh_accounts()
        if isinstance(res, Exception):
            self._account_status(name, f"✗ {self._why(res)}", "red")

    @staticmethod
    def _why(exc: Exception) -> str:
        from app.providers.publish._common import PublishError

        return str(exc) if isinstance(exc, PublishError) else f"{type(exc).__name__}: {exc}"

    # --- Kiểm tra: doctor checks, run off the GUI thread ----------------------------
    def _check_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(4, 4, 12, 12)
        v.setSpacing(12)
        v.addWidget(label("Kiểm tra môi trường", "h1"))
        v.addWidget(label("Công cụ, bộ nhận dạng, font và khóa trên máy này — cùng nội dung "
                          "với lệnh vizsup doctor.", "secondary", wrap=True))
        card = _card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(10)
        top = QHBoxLayout()
        self.check_summary = label('Chưa kiểm tra. Bấm "Kiểm tra ngay" để xem.', "small",
                                   wrap=True)
        top.addWidget(self.check_summary, 1)
        self.check_btn = button("Kiểm tra ngay", "RefreshCw")
        self.check_btn.clicked.connect(self._run_checks)
        self._buttons.append(self.check_btn)
        top.addWidget(self.check_btn)
        cl.addLayout(top)
        self._check_grid = QGridLayout()
        self._check_grid.setHorizontalSpacing(12)
        self._check_grid.setVerticalSpacing(8)
        self._check_grid.setColumnStretch(2, 1)
        cl.addLayout(self._check_grid)
        v.addWidget(card)
        v.addStretch(1)
        return page

    def _run_checks(self) -> None:
        self.check_summary.setText("Đang kiểm tra…")
        self.check_summary.setStyleSheet(f"color:{V2['muted']};")
        self._bg(run_checks, self._checks_done)

    def _checks_done(self, res) -> None:
        self._set_busy(False)
        grid = self._check_grid
        while grid.count():
            w = grid.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        if isinstance(res, Exception):
            self.check_summary.setText(f"✗ Không chạy được kiểm tra: {type(res).__name__}: {res}")
            self.check_summary.setStyleSheet(f"color:{V2['red']};")
            return
        for r, (status, name, detail) in enumerate(res):
            glyph, color = _CHECK_STYLE.get(status, _CHECK_STYLE["info"])
            grid.addWidget(icon_label(glyph, V2[color], 16), r, 0, Qt.AlignTop)
            grid.addWidget(label(name, "h3"), r, 1, Qt.AlignTop)
            det = label(detail, "small", wrap=True)
            det.setMinimumWidth(1)  # let the wide column shrink and wrap, not push the card out
            grid.addWidget(det, r, 2, Qt.AlignTop)
        fails = sum(1 for s, _n, _d in res if s == "fail")
        warns = sum(1 for s, _n, _d in res if s == "warn")
        if fails:
            text = f"✕ {fails} lỗi phải sửa · {warns} cảnh báo — pipeline chưa chạy đủ."
            color = V2["red"]
        elif warns:
            text = f"! {warns} cảnh báo — chạy được nhưng thiếu một số tính năng."
            color = V2["amber"]
        else:
            text = "✓ Mọi thứ sẵn sàng."
            color = V2["green"]
        self.check_summary.setText(text)
        self.check_summary.setStyleSheet(f"color:{color};")

    def _opt_card(self, env: str, name: str, attr: str, kind: str,
                  hint: str, choices: list[str] | None) -> QWidget:
        card = _card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(6)
        cl.addWidget(label(name, "h3"))
        row = QHBoxLayout()
        if kind == "choice":
            w = QComboBox()
            w.setEditable(True)          # allow values we did not list
            w.addItems(choices or [])
            w.editTextChanged.connect(self._mark_dirty)
        else:
            w = QLineEdit()
            w.setObjectName("mono")
            w.textEdited.connect(self._mark_dirty)
        row.addWidget(w, 1)
        if kind in ("file", "dir"):
            browse = button("Chọn…", "FolderOpen", small=True)
            browse.clicked.connect(lambda _=False, e=w, k=kind, n=name: self._browse(e, k, n))
            self._buttons.append(browse)
            row.addWidget(browse)
        cl.addLayout(row)
        cl.addWidget(label(hint, "small", wrap=True))
        self._opts[env] = w
        return card

    def _browse(self, target, kind: str, name: str) -> None:
        start = target.text().strip() or str(Path.home())
        if kind == "dir":
            picked = QFileDialog.getExistingDirectory(self, name, start)
        else:
            picked, _ = QFileDialog.getOpenFileName(self, name, start)
        if picked:
            target.setText(picked)
            self._mark_dirty()

    def _opt_value(self, env: str) -> str:
        w = self._opts[env]
        text = w.currentText() if isinstance(w, QComboBox) else w.text()
        return text.strip()

    # --- values / dirty -------------------------------------------------------------
    def load_values(self) -> None:
        """(Re)fill every field from the live settings — also "Hủy thay đổi"."""
        for env, _n, attr, _p in KEYS:
            f = self._fields[env]
            f.blockSignals(True)
            f.setText(getattr(settings, attr, "") or "")
            f.blockSignals(False)
            has = bool(f.text().strip())
            if env != "OPENROUTER_API_KEY":
                b = self._badges[env]
                b.setText("Đã thêm" if has else "Chưa thêm")
                b.setObjectName("badgeOk" if has else "badge")
                b.style().unpolish(b)
                b.style().polish(b)
        for env, _n, attr, _p, _s in PUBLISH_KEYS:
            f = self._fields[env]
            f.blockSignals(True)
            f.setText(getattr(settings, attr, "") or "")
            f.blockSignals(False)
        self._refresh_accounts()
        for env, _n, attr, _k, _h, _c in OPTIONS:
            w = self._opts[env]
            w.blockSignals(True)
            current = str(getattr(settings, attr, "") or "")
            if isinstance(w, QComboBox):
                w.setEditText(current)
            else:
                w.setText(current)
            w.blockSignals(False)
        from app.providers.roles import ROLES

        for key, combo in self.role_combos.items():
            combo.blockSignals(True)
            combo.setEditText(getattr(settings, ROLES[key].attr, "") or "")
            combo.blockSignals(False)
        from app.providers import presets

        self._base_preset = presets.active().key
        self._show_package(presets.CUSTOM if presets.is_custom() else self._base_preset)
        self._update_promo_state()
        self._update_cookie_state()
        self._set_dirty(False)

    def _update_promo_state(self) -> None:
        model = self._opt_value("OPENROUTER_PROMO_MODEL")
        exp = self._opt_value("OPENROUTER_PROMO_EXPIRES")
        if not model:
            self.promo_state.setText("Không dùng model khuyến mãi.")
            color = V2["muted"]
        else:
            # same rule as OpenRouterTranslator.active_promo: unused FROM the expiry
            # day on, and a malformed date disables the promo rather than keeping it forever
            try:
                when = date.fromisoformat(exp) if exp else None
            except ValueError:
                when = None
                self.promo_state.setText("Ngày hết hạn sai định dạng (cần YYYY-MM-DD) — model "
                                         "khuyến mãi sẽ KHÔNG được dùng.")
                color = V2["amber"]
            else:
                if when is not None and when <= date.today():
                    self.promo_state.setText(f"Khuyến mãi đã hết hạn ({when:%d/%m/%Y}) — đang "
                                             f"dùng model chính.")
                    color = V2["amber"]
                else:
                    until = f" tới trước ngày {when:%d/%m/%Y}" if when else ""
                    self.promo_state.setText(f"Đang dùng {model}{until}, sau đó tự quay về "
                                             f"model chính.")
                    color = V2["green"]
        self.promo_state.setStyleSheet(f"color:{color};")

    def _mark_dirty(self, *_a) -> None:
        self._set_dirty(True)
        if hasattr(self, "promo_state"):
            self._update_promo_state()

    def _set_dirty(self, on: bool) -> None:
        self._dirty = on
        self.state_lbl.setText("Có thay đổi chưa lưu" if on else "Cấu hình hiện tại")
        self.state_lbl.setStyleSheet(f"color:{V2['amber'] if on else V2['muted']};")
        self.cancel_btn.setEnabled(on)

    def is_dirty(self) -> bool:
        return self._dirty

    def request_close(self) -> None:
        if self._dirty:
            r = QMessageBox.question(self, "Cài đặt", "Bỏ các thay đổi chưa lưu?",
                                     QMessageBox.StandardButton.Discard |
                                     QMessageBox.StandardButton.Cancel)
            if r != QMessageBox.StandardButton.Discard:
                return
            self.load_values()
        self.closed.emit()

    # --- background runner ---------------------------------------------------
    def _bg(self, fn, on_done) -> None:
        wk = _NetWorker(fn)
        # GUI thread — on_done touches widgets; dropped once the window is closing. The call
        # runs on a daemon thread (ui_thread.spawn), so a hanging request never delays the exit
        connect_on_ui(wk.done, guarded(on_done, lambda: not getattr(self, "_closing_jobs", False)),
                      self)
        self._set_busy(True)
        spawn(wk.run, self._jobs, wk)

    def _set_busy(self, busy: bool) -> None:
        for b in self._buttons:
            b.setEnabled(not busy)

    # --- test connection -----------------------------------------------------
    def _test(self, env: str, attr: str, prov: str) -> None:
        setattr(settings, attr, self._fields[env].text().strip())  # use the typed key
        lbl = self._status[env]
        lbl.setVisible(True)
        lbl.setStyleSheet(f"color:{V2['muted']};")
        lbl.setText("Đang kiểm tra…")
        provider = find_provider(prov)
        if provider is None:
            lbl.setText("Không tìm thấy công cụ này.")
            return
        self._bg(provider.test_connection, lambda res, e=env: self._test_done(e, res))

    def _test_done(self, env: str, res) -> None:
        self._set_busy(False)
        lbl = self._status[env]
        if isinstance(res, Exception):
            lbl.setText(f"✗ {type(res).__name__}: {res}")
            lbl.setStyleSheet(f"color:{V2['red']};")
            return
        ok, msg = res
        lbl.setText(("✓ Kết nối thành công · " if ok else "✗ ") + msg)
        lbl.setStyleSheet(f"color:{V2['green'] if ok else V2['red']};")

    # --- fetch OpenRouter models ---------------------------------------------
    def _fetch_models(self) -> None:
        settings.openrouter_api_key = self._fields["OPENROUTER_API_KEY"].text().strip()
        self.model_status.setStyleSheet(f"color:{V2['muted']};")
        self.model_status.setText("Đang tải danh sách model…")
        provider = find_provider("openrouter")
        self._bg(provider.list_models, self._fetch_done)

    def _fetch_done(self, res) -> None:
        self._set_busy(False)
        if isinstance(res, Exception):
            self.model_status.setText(f"✗ {type(res).__name__}: {res}")
            self.model_status.setStyleSheet(f"color:{V2['red']};")
            return
        for combo in self.role_combos.values():
            keep = combo.currentText().strip()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems([""] + list(res))
            combo.setEditText(keep)
            combo.blockSignals(False)
        self.model_status.setText(f"✓ {len(res)} model — chọn trong danh sách")
        self.model_status.setStyleSheet(f"color:{V2['green']};")

    # --- save ----------------------------------------------------------------
    def _save(self) -> None:
        # keys: only write non-empty ones — blanking a field must not wipe a key
        # the user still has in .env (they clear it by editing .env directly).
        updates = {env: f.text().strip() for env, f in self._fields.items() if f.text().strip()}
        from app.providers.roles import ROLES

        # role models: always written — a blank one means "back to the default"
        role_models = {k: c.currentText().strip() for k, c in self.role_combos.items()}
        for k, v in role_models.items():
            updates[f"MODEL_{k.upper()}"] = v
        updates["MODEL_PRESET"] = self._base_preset
        # options: always write, INCLUDING empties — clearing one is a real intent
        # ("no cookie browser", "resolve ffmpeg from PATH") and must round-trip.
        for env, _n, _a, _k, _h, _c in OPTIONS:
            updates[env] = self._opt_value(env)
        _write_env(updates)

        for env, f in self._fields.items():
            setattr(settings, ATTR_BY_ENV[env], f.text().strip())
        for k, v in role_models.items():
            setattr(settings, ROLES[k].attr, v)
        settings.model_preset = self._base_preset
        for env, attr in OPT_ATTR_BY_ENV.items():
            value = self._opt_value(env)
            # vizsup_storage_dir is typed Path; assigning a str breaks every "/" use
            if isinstance(getattr(settings, attr, None), Path):
                setattr(settings, attr, Path(value) if value else settings.vizsup_storage_dir)
            else:
                setattr(settings, attr, value)
        self.load_values()
        self.state_lbl.setText("Đã lưu thay đổi")
        self.state_lbl.setStyleSheet(f"color:{V2['green']};")
        self.saved.emit()

    def shutdown(self) -> None:  # in-flight network calls: results dropped, nothing joined
        self._closing_jobs = True
        self._cancel_account()            # a browser sign-in still waited on: free its port
