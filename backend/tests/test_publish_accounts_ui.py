"""Settings → "Đăng video": per-platform app credentials (written to .env), account status
from the stored account files, Kết nối on a background thread (results on the GUI thread),
Hủy, the Facebook paste flow and Ngắt kết nối. The publishers' network methods are replaced
by fakes — nothing here talks to a platform."""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")
sd = pytest.importorskip("desktop.settings_dialog")

from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox  # noqa: E402

from app.config import settings  # noqa: E402
from app.providers.publish import _common as cm  # noqa: E402
from app.providers.registry import get_publisher  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def env(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    monkeypatch.setattr(sd, "_ENV_PATH", path)
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path / "storage")
    for attr in sd.ATTR_BY_ENV.values():       # never copy real keys into the tmp .env
        monkeypatch.setattr(settings, attr, "")
    saved = {attr: getattr(settings, attr) for attr in sd.OPT_ATTR_BY_ENV.values()}
    yield path
    for attr, value in saved.items():
        setattr(settings, attr, value)


def _pump(qapp, until, timeout=5.0):
    end = time.time() + timeout
    while not until() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.005)
    assert until(), "timed out"


def _status(view, name):
    return view._acct[name]["status"].text()


def test_the_page_has_a_card_and_credential_fields_per_platform(qapp, env):
    view = sd.SettingsView()
    view.show_group("accounts")
    assert set(view._acct) == {"youtube", "facebook", "tiktok"}
    for env_name, _t, attr, plat, _secret in sd.PUBLISH_KEYS:
        assert env_name in view._fields and hasattr(settings, attr)
        assert plat in view._acct
    for name in sd.PUBLISH_PLATFORMS:
        assert "Chưa có mã ứng dụng" in view._acct[name]["badge"].text()
        assert view._acct[name]["drop"].isHidden()


def test_status_follows_credentials_and_the_stored_account(qapp, env, monkeypatch):
    monkeypatch.setattr(settings, "youtube_client_id", "id")
    monkeypatch.setattr(settings, "youtube_client_secret", "sec")
    view = sd.SettingsView()
    assert view._acct["youtube"]["badge"].text() == "Chưa nối"
    cm.save_account("youtube", {"refresh_token": "r", "channel_title": "Kênh Thử"})
    view.load_values()
    assert _status(view, "youtube").startswith("Đã nối: Kênh Thử")
    assert not view._acct["youtube"]["drop"].isHidden()
    assert view._acct["youtube"]["connect"].text() == "Kết nối lại"


def test_credentials_round_trip_through_save(qapp, env):
    view = sd.SettingsView()
    view._fields["TIKTOK_CLIENT_KEY"].setText("awkey")
    view._fields["TIKTOK_CLIENT_SECRET"].setText("awsecret")
    view._save()
    text = env.read_text(encoding="utf-8")
    assert "TIKTOK_CLIENT_KEY=awkey" in text and "TIKTOK_CLIENT_SECRET=awsecret" in text
    assert settings.tiktok_client_key == "awkey"


def test_connect_runs_off_the_gui_thread_and_reports_on_it(qapp, env, monkeypatch):
    pub = get_publisher("youtube")
    gui = threading.get_ident()
    seen = {}

    def fake_connect(*, cancel=None, **_kw):
        seen["thread"] = threading.get_ident()
        seen["cancel"] = cancel
        cm.save_account("youtube", {"refresh_token": "r", "channel_title": "Kênh Mới"})
        return "Kênh Mới"
    monkeypatch.setattr(pub, "connect", fake_connect)
    view = sd.SettingsView()
    view._fields["YOUTUBE_CLIENT_ID"].setText("cid")
    view._fields["YOUTUBE_CLIENT_SECRET"].setText("csec")
    view._connect_account("youtube")
    _pump(qapp, lambda: "Kênh Mới" in _status(view, "youtube"))
    assert seen["thread"] != gui and callable(seen["cancel"]) and not seen["cancel"]()
    assert _status(view, "youtube") == "✓ Đã nối: Kênh Mới"
    assert view._acct["youtube"]["cancel"].isHidden()
    assert "YOUTUBE_CLIENT_ID=cid" in env.read_text(encoding="utf-8")   # kept for refreshes
    assert settings.youtube_client_secret == "csec"


def test_connect_needs_the_credentials_first(qapp, env, monkeypatch):
    called = []
    monkeypatch.setattr(get_publisher("tiktok"), "connect", lambda **_k: called.append(1))
    view = sd.SettingsView()
    view._connect_account("tiktok")
    assert "Nhập đủ" in _status(view, "tiktok") and not called


def test_cancel_stops_the_browser_wait(qapp, env, monkeypatch):
    pub = get_publisher("tiktok")
    started = threading.Event()

    def waiting_connect(*, cancel=None, **_kw):
        started.set()
        while not cancel():
            time.sleep(0.01)
        raise cm.PublishError("Đã hủy kết nối.")
    monkeypatch.setattr(pub, "connect", waiting_connect)
    view = sd.SettingsView()
    view._fields["TIKTOK_CLIENT_KEY"].setText("k")
    view._fields["TIKTOK_CLIENT_SECRET"].setText("s")
    view._connect_account("tiktok")
    assert not view._acct["tiktok"]["cancel"].isHidden()
    assert started.wait(5)
    view._cancel_account()
    _pump(qapp, lambda: "hủy" in _status(view, "tiktok"))
    assert _status(view, "tiktok").startswith("✗")


def test_facebook_paste_flow_picks_a_page(qapp, env, monkeypatch):
    pub = get_publisher("facebook")
    opened = []
    monkeypatch.setattr(sd.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *_a, **_k: ("https://x/#access_token=EAA", True)))
    monkeypatch.setattr(QInputDialog, "getItem",
                        staticmethod(lambda _p, _t, _l, items, *_a: (items[1], True)))

    def fake_pages(text):
        assert threading.current_thread() is not threading.main_thread()
        assert "access_token" in text
        pub._pending = {"A": {"id": "A", "name": "Trang A", "access_token": "ta"},
                        "B": {"id": "B", "name": "Trang B", "access_token": "tb"}}
        return [{"id": "A", "name": "Trang A", "can_post": True},
                {"id": "B", "name": "Trang B", "can_post": True},
                {"id": "C", "name": "Chỉ xem", "can_post": False}]
    monkeypatch.setattr(pub, "fetch_pages", fake_pages)
    view = sd.SettingsView()
    view._fields["FACEBOOK_APP_ID"].setText("123")
    view._fields["FACEBOOK_APP_SECRET"].setText("sec")
    view._connect_account("facebook")
    _pump(qapp, lambda: "Trang B" in _status(view, "facebook"))
    assert opened and "login_success.html" in opened[0]
    assert _status(view, "facebook") == "✓ Đã nối: Trang B"
    assert cm.load_account("facebook")["page_id"] == "B"
    assert view._acct["facebook"]["badge"].text() == "Đã nối"


def test_disconnect_after_confirming(qapp, env, monkeypatch):
    monkeypatch.setattr(settings, "youtube_client_id", "id")
    monkeypatch.setattr(settings, "youtube_client_secret", "sec")
    cm.save_account("youtube", {"refresh_token": "r", "channel_title": "Kênh Thử"})
    pub = get_publisher("youtube")
    monkeypatch.setattr(pub, "disconnect", lambda: cm.forget_account("youtube"))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *_a, **_k: QMessageBox.StandardButton.Yes))
    view = sd.SettingsView()
    view._disconnect_account("youtube")
    _pump(qapp, lambda: view._acct["youtube"]["badge"].text() == "Chưa nối")
    assert view._acct["youtube"]["drop"].isHidden()
