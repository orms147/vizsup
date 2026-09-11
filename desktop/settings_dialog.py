"""Settings dialog: per-provider API keys (password fields) plus the non-secret
knobs (cookies, paths, model sizes), all saved to the repo-root .env. Each provider
has a working **Test connection** button and OpenRouter a **Fetch models** picker.

Network calls run on a background QThread so the UI never freezes.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from desktop import paths  # noqa: F401
from desktop.theme import C
from app.config import settings  # noqa: E402
from app.providers.registry import find_provider  # noqa: E402

# (env var, display name, settings attr, provider name)
KEYS = [
    ("OPENROUTER_API_KEY", "OpenRouter — 1 key, mọi model", "openrouter_api_key", "openrouter"),
    ("DEEPSEEK_API_KEY", "DeepSeek", "deepseek_api_key", "deepseek"),
    ("ANTHROPIC_API_KEY", "Anthropic / Claude", "anthropic_api_key", "claude"),
    ("ZHIPU_API_KEY", "Zhipu / GLM-5.2", "zhipu_api_key", "glm"),
    ("GEMINI_API_KEY", "Google Gemini", "gemini_api_key", "gemini"),
    ("DASHSCOPE_API_KEY", "Qwen (DashScope)", "dashscope_api_key", "qwen"),
    ("DEEPL_API_KEY", "DeepL", "deepl_api_key", "deepl"),
    ("FPT_API_KEY", "FPT.AI (TTS)", "fpt_api_key", "fpt"),
    ("AZURE_SPEECH_KEY", "Azure Speech (TTS)", "azure_speech_key", "azure"),
]
ATTR_BY_ENV = {env: attr for env, _n, attr, _p in KEYS}

# Non-secret settings. Every attr already exists in app.config.Settings — this
# dialog only stops the user from hand-editing .env for them.
# (env var, label, settings attr, kind, hint, choices)
OPTIONS = [
    ("YTDLP_COOKIES_FROM_BROWSER", "Cookie từ trình duyệt", "ytdlp_cookies_from_browser",
     "choice", "Douyin thường cần cookie mới. Chọn trình duyệt đã đăng nhập douyin.com "
     "rồi ĐÓNG trình duyệt đó trước khi tải.",
     ["", "edge", "chrome", "firefox", "brave", "chromium", "opera", "vivaldi"]),
    ("YTDLP_COOKIES_FILE", "…hoặc tệp cookies.txt", "ytdlp_cookies_file",
     "file", "Dùng khi không lấy được cookie trực tiếp từ trình duyệt.", None),
    ("FFMPEG_PATH", "Đường dẫn ffmpeg", "ffmpeg_path",
     "file", "Trỏ tới ffmpeg.exe hoặc thư mục bin của nó. Bỏ trống = tìm trong PATH.", None),
    ("BBDOWN_PATH", "Đường dẫn BBDown", "bbdown_path",
     "file", "Bộ tải dự phòng cho Bilibili, chỉ chạy khi yt-dlp thất bại.", None),
    ("VIZSUP_STORAGE_DIR", "Thư mục lưu dự án", "vizsup_storage_dir",
     "dir", "Mỗi video là một thư mục con. Đổi ở đây KHÔNG di chuyển dự án cũ.", None),
    ("WHISPER_MODEL", "Model faster-whisper", "whisper_model",
     "choice", "Nhỏ hơn = nhanh hơn nhưng kém chính xác. Card 8 GB chạy được large-v3.",
     ["tiny", "base", "small", "medium", "large-v2", "large-v3"]),
    ("ZHIPU_MODEL", "Model GLM (ghi đè mặc định)", "zhipu_model",
     "text", "Bỏ trống = dùng mặc định (glm-5.2).", None),
]
OPT_ATTR_BY_ENV = {env: attr for env, _n, attr, _k, _h, _c in OPTIONS}
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


class SettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("vizsup — Cài đặt")
        self.resize(620, 680)
        self._fields: dict[str, QLineEdit] = {}
        self._opts: dict[str, QWidget] = {}
        self._status: dict[str, QLabel] = {}
        self._buttons: list[QPushButton] = []
        self._jobs: list[tuple] = []  # (QThread, _NetWorker) — keep BOTH alive until done

        root = QVBoxLayout(self)
        title = QLabel("Khóa API")
        title.setObjectName("h1")
        sub = QLabel("Thêm khóa cho dịch vụ trả phí. Bỏ trống nếu chỉ dùng công cụ cục bộ "
                     "(WhisperX/faster-whisper, edge-tts). Lưu cục bộ trong .env trên máy bạn — "
                     "không gửi đi đâu khác.")
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(sub)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setSpacing(10)
        for env, name, attr, prov in KEYS:
            bl.addWidget(self._key_card(env, name, attr, prov))

        opt_title = QLabel("Tải video, đường dẫn & model")
        opt_title.setObjectName("h1")
        opt_sub = QLabel("Các tùy chọn không phải khóa bí mật. Cũng lưu vào .env.")
        opt_sub.setObjectName("muted")
        opt_sub.setWordWrap(True)
        bl.addSpacing(10)
        bl.addWidget(opt_title)
        bl.addWidget(opt_sub)
        for spec in OPTIONS:
            bl.addWidget(self._opt_card(*spec))
        bl.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("Đóng")
        cancel.setObjectName("ghost")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Lưu thay đổi")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        self._buttons.append(save)
        btns.addWidget(cancel)
        btns.addWidget(save)
        root.addLayout(btns)

    # --- per-provider card ---------------------------------------------------
    def _key_card(self, env: str, name: str, attr: str, prov: str) -> QWidget:
        card = QWidget()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        cl.addWidget(QLabel(name))

        row = QHBoxLayout()
        edit = QLineEdit(getattr(settings, attr, "") or "")
        edit.setEchoMode(QLineEdit.Password)
        edit.setObjectName("mono")
        edit.setPlaceholderText(f"Dán khóa {name}…")
        reveal = QPushButton("👁")
        reveal.setObjectName("ghost")
        reveal.setFixedWidth(40)
        reveal.setCheckable(True)
        reveal.toggled.connect(
            lambda on, e=edit: e.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password)
        )
        test = QPushButton("Test kết nối")
        test.setObjectName("ghost")
        test.clicked.connect(lambda _=False, e=env, a=attr, p=prov: self._test(e, a, p))
        self._buttons.append(test)
        row.addWidget(edit, 1)
        row.addWidget(reveal)
        row.addWidget(test)
        cl.addLayout(row)

        status = QLabel("")
        status.setObjectName("muted")
        status.setWordWrap(True)
        status.setVisible(False)
        self._status[env] = status
        cl.addWidget(status)
        self._fields[env] = edit

        # OpenRouter: model fetch + picker
        if env == "OPENROUTER_API_KEY":
            mrow = QHBoxLayout()
            mlabel = QLabel("Model")
            self.or_model = QComboBox()
            self.or_model.setEditable(True)
            self.or_model.setMinimumWidth(280)
            if settings.openrouter_model:
                self.or_model.setEditText(settings.openrouter_model)
            fetch = QPushButton("Tải danh sách model")
            fetch.setObjectName("ghost")
            fetch.clicked.connect(self._fetch_models)
            self._buttons.append(fetch)
            mrow.addWidget(mlabel)
            mrow.addWidget(self.or_model, 1)
            mrow.addWidget(fetch)
            cl.addLayout(mrow)
        return card

    # --- non-secret option card ----------------------------------------------
    def _opt_card(self, env: str, name: str, attr: str, kind: str,
                  hint: str, choices: list[str] | None) -> QWidget:
        card = QWidget()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        cl.addWidget(QLabel(name))

        current = str(getattr(settings, attr, "") or "")
        row = QHBoxLayout()
        if kind == "choice":
            w = QComboBox()
            w.setEditable(True)          # allow values we did not list
            w.addItems(choices or [])
            w.setEditText(current)
        else:
            w = QLineEdit(current)
            w.setObjectName("mono")
        row.addWidget(w, 1)
        if kind in ("file", "dir"):
            browse = QPushButton("Chọn…")
            browse.setObjectName("ghost")
            browse.clicked.connect(lambda _=False, e=w, k=kind, n=name: self._browse(e, k, n))
            self._buttons.append(browse)
            row.addWidget(browse)
        cl.addLayout(row)

        lbl = QLabel(hint)
        lbl.setObjectName("muted")
        lbl.setWordWrap(True)
        cl.addWidget(lbl)
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

    def _opt_value(self, env: str) -> str:
        w = self._opts[env]
        text = w.currentText() if isinstance(w, QComboBox) else w.text()
        return text.strip()

    # --- background runner ---------------------------------------------------
    def _bg(self, fn, on_done) -> None:
        th = QThread(self)
        wk = _NetWorker(fn)
        wk.moveToThread(th)
        pair = (th, wk)
        self._jobs.append(pair)  # CRUCIAL: keep BOTH referenced or the worker is GC'd → crash
        th.started.connect(wk.run)
        wk.done.connect(on_done)
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
        lbl.setStyleSheet(f"color:{C['muted']};")
        lbl.setText("Đang kiểm tra…")
        provider = find_provider(prov)
        if provider is None:
            lbl.setText("Không tìm thấy provider.")
            return
        self._bg(provider.test_connection, lambda res, e=env: self._test_done(e, res))

    def _test_done(self, env: str, res) -> None:
        self._set_busy(False)
        lbl = self._status[env]
        if isinstance(res, Exception):
            lbl.setText(f"✗ {type(res).__name__}: {res}")
            lbl.setStyleSheet(f"color:{C['red']};")
            return
        ok, msg = res
        lbl.setText(("✓ " if ok else "✗ ") + msg)
        lbl.setStyleSheet(f"color:{C['green'] if ok else C['red']};")

    # --- fetch OpenRouter models ---------------------------------------------
    def _fetch_models(self) -> None:
        settings.openrouter_api_key = self._fields["OPENROUTER_API_KEY"].text().strip()
        lbl = self._status["OPENROUTER_API_KEY"]
        lbl.setVisible(True)
        lbl.setStyleSheet(f"color:{C['muted']};")
        lbl.setText("Đang tải danh sách model…")
        provider = find_provider("openrouter")
        self._bg(provider.list_models, self._fetch_done)

    def _fetch_done(self, res) -> None:
        self._set_busy(False)
        lbl = self._status["OPENROUTER_API_KEY"]
        if isinstance(res, Exception):
            lbl.setText(f"✗ {type(res).__name__}: {res}")
            lbl.setStyleSheet(f"color:{C['red']};")
            return
        keep = self.or_model.currentText().strip()
        self.or_model.clear()
        self.or_model.addItems(res)
        if keep:
            self.or_model.setEditText(keep)
        lbl.setText(f"✓ {len(res)} model — chọn ở ô Model")
        lbl.setStyleSheet(f"color:{C['green']};")

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
        self.accept()

    def closeEvent(self, e) -> None:  # join any in-flight network threads
        for th, _wk in list(self._jobs):
            try:
                if th.isRunning():
                    th.quit()
                    th.wait(3000)
            except RuntimeError:
                pass
        e.accept()
