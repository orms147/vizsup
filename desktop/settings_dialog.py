"""Cài đặt (v2, appdesign/v2 "v2 — Cài đặt"): a full page, not a dialog. Left nav
Khóa API / Tải video / AI và mô hình / Đường dẫn; its own Lưu / Hủy thay đổi
(the editor's "Đã lưu" never means settings were saved). Everything is written to
the repo-root .env and applied to the live settings object.

Network calls (Kiểm tra kết nối, tải danh sách model) run on a background QThread
so the UI never freezes.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
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
from desktop.ui_thread import connect_on_ui
from desktop.widgets.chrome import badge, button, icon_button, icon_label, label
from app.config import settings  # noqa: E402
from app.providers.registry import find_provider  # noqa: E402

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
ATTR_BY_ENV = {env: attr for env, _n, attr, _p in KEYS}
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
          ("ai", "AI và mô hình", "Sparkles"), ("paths", "Đường dẫn", "HardDrive"))
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
    _ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


class _NetWorker(QObject):
    """Runs a blocking function once on a worker thread; emits its result/exception."""

    done = Signal(object)

    def __init__(self, fn) -> None:
        super().__init__()
        self._fn = fn

    @Slot()
    def run(self) -> None:
        try:
            self.done.emit(self._fn())
        except Exception as exc:  # noqa: BLE001
            self.done.emit(exc)


def _card() -> QFrame:
    f = QFrame()
    f.setObjectName("panel")
    f.setStyleSheet(f"QFrame#panel{{background:{V2['panel']};border:1px solid {V2['line']};"
                    f"border-radius:10px;}}")
    return f


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
        self._jobs: list[tuple] = []  # (QThread, _NetWorker) — keep BOTH alive until done
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
            self._stack.addWidget(self._scroll(self._options_page(key)))
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
        if group == "ai":
            v.addWidget(self._model_card())
            self.promo_state = label("", "small")
            self.promo_state.setWordWrap(True)
        for spec in OPTIONS:
            if OPT_GROUP.get(spec[0], "ai") == group:
                v.addWidget(self._opt_card(*spec))
                if spec[0] == "OPENROUTER_PROMO_EXPIRES":
                    v.addWidget(self.promo_state)
        v.addStretch(1)
        return page

    def _model_card(self) -> QFrame:
        card = _card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(6)
        cl.addWidget(label("Model dịch chính (OpenRouter)", "h3"))
        row = QHBoxLayout()
        self.or_model = QComboBox()
        self.or_model.setEditable(True)
        self.or_model.setMinimumWidth(280)
        self.or_model.editTextChanged.connect(self._mark_dirty)
        fetch = button("Tải danh sách model", "RefreshCw", variant="ghost", small=True)
        fetch.clicked.connect(self._fetch_models)
        self._buttons.append(fetch)
        row.addWidget(self.or_model, 1)
        row.addWidget(fetch)
        cl.addLayout(row)
        self.model_status = label("Dùng cho bản nháp, đọc ngữ cảnh và tinh chỉnh khi chọn "
                                  "OpenRouter.", "small")
        self.model_status.setWordWrap(True)
        cl.addWidget(self.model_status)
        return card

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
        for env, _n, attr, _k, _h, _c in OPTIONS:
            w = self._opts[env]
            w.blockSignals(True)
            current = str(getattr(settings, attr, "") or "")
            if isinstance(w, QComboBox):
                w.setEditText(current)
            else:
                w.setText(current)
            w.blockSignals(False)
        self.or_model.blockSignals(True)
        self.or_model.setEditText(settings.openrouter_model or "")
        self.or_model.blockSignals(False)
        self._update_promo_state()
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
        th = QThread(self)
        wk = _NetWorker(fn)
        wk.moveToThread(th)
        pair = (th, wk)
        self._jobs.append(pair)  # CRUCIAL: keep BOTH referenced or the worker is GC'd → crash
        th.started.connect(wk.run)
        connect_on_ui(wk.done, on_done, self)   # GUI thread — on_done touches widgets
        wk.done.connect(th.quit)
        th.finished.connect(wk.deleteLater)
        th.finished.connect(th.deleteLater)
        th.finished.connect(lambda p=pair: p in self._jobs and self._jobs.remove(p))
        self._set_busy(True)
        th.start()

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
        keep = self.or_model.currentText().strip()
        self.or_model.blockSignals(True)
        self.or_model.clear()
        self.or_model.addItems(res)
        if keep:
            self.or_model.setEditText(keep)
        self.or_model.blockSignals(False)
        self.model_status.setText(f"✓ {len(res)} model — chọn trong danh sách")
        self.model_status.setStyleSheet(f"color:{V2['green']};")

    # --- save ----------------------------------------------------------------
    def _save(self) -> None:
        # keys: only write non-empty ones — blanking a field must not wipe a key
        # the user still has in .env (they clear it by editing .env directly).
        updates = {env: f.text().strip() for env, f in self._fields.items() if f.text().strip()}
        model = self.or_model.currentText().strip()
        if model:
            updates["OPENROUTER_MODEL"] = model
        # options: always write, INCLUDING empties — clearing one is a real intent
        # ("no cookie browser", "resolve ffmpeg from PATH") and must round-trip.
        for env, _n, _a, _k, _h, _c in OPTIONS:
            updates[env] = self._opt_value(env)
        _write_env(updates)

        for env, f in self._fields.items():
            setattr(settings, ATTR_BY_ENV[env], f.text().strip())
        if model:
            settings.openrouter_model = model
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

    def shutdown(self) -> None:  # join any in-flight network threads
        for th, _wk in list(self._jobs):
            try:
                if th.isRunning():
                    th.quit()
                    th.wait(3000)
            except RuntimeError:
                pass
