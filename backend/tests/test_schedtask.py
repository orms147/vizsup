"""Windows background run (app/schedtask.py) and its button in the Bộ phim dialog. Every
schtasks call is faked — no test (and no agent) ever creates a real scheduled task."""
from __future__ import annotations

import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import pytest

from app import schedtask
from app.config import settings
from app.pipeline import series

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


@pytest.fixture()
def fake_schtasks(monkeypatch):
    """Record every schtasks call; answer from ``codes`` (a list popped per call, default 0).
    The XML file is read while it still exists."""
    calls: list[list[str]] = []
    xmls: list[str] = []
    codes: list[int] = []

    def run(cmd, **kw):
        assert cmd[0] == "schtasks"
        calls.append(cmd)
        if "/XML" in cmd:
            raw = Path(cmd[cmd.index("/XML") + 1]).read_bytes()
            assert raw[:2] == b"\xff\xfe"                       # UTF-16 LE with a BOM
            xmls.append(raw.decode("utf-16"))
        code = codes.pop(0) if codes else 0
        return subprocess.CompletedProcess(cmd, code, "", "LỖI: từ chối" if code else "")

    monkeypatch.setattr(schedtask.subprocess, "run", run)
    monkeypatch.setattr(schedtask, "supported", lambda: True)
    return calls, xmls, codes


def _parse(xml: str) -> ET.Element:
    return ET.fromstring(xml.split("?>", 1)[1])


def test_task_xml_runs_the_cli_every_n_hours_in_the_backend_folder():
    root = _parse(schedtask.task_xml(6, start=datetime(2026, 10, 31, 12, 5, 0, 123), user="PC\\me"))
    assert root.find("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval", NS).text == "PT6H"
    assert root.find("t:Triggers/t:TimeTrigger/t:StartBoundary", NS).text == "2026-10-31T12:05:00"
    ex = root.find("t:Actions/t:Exec", NS)
    assert ex.find("t:Command", NS).text == schedtask.python_exe()
    assert ex.find("t:Arguments", NS).text == "-m app.cli autopilot --once"
    assert Path(ex.find("t:WorkingDirectory", NS).text) == Path(schedtask.__file__).resolve().parents[1]
    assert (Path(ex.find("t:WorkingDirectory", NS).text) / "app" / "cli.py").exists()
    s = root.find("t:Settings", NS)
    assert s.find("t:MultipleInstancesPolicy", NS).text == "IgnoreNew"
    assert s.find("t:DisallowStartIfOnBatteries", NS).text == "false"
    assert s.find("t:StartWhenAvailable", NS).text == "true"
    p = root.find("t:Principals/t:Principal", NS)
    assert p.find("t:UserId", NS).text == "PC\\me" and p.find("t:LogonType", NS).text == "InteractiveToken"
    assert _parse(schedtask.task_xml(0)).find(".//t:Interval", NS).text == "PT1H"      # clamped
    assert _parse(schedtask.task_xml(500)).find(".//t:Interval", NS).text == "PT168H"
    assert _parse(schedtask.task_xml(6)).find(".//t:UserId", NS) is None


def test_python_exe_prefers_pythonw(tmp_path, monkeypatch):
    exe = tmp_path / "python.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(exe))
    assert schedtask.python_exe() == str(exe)
    (tmp_path / "pythonw.exe").write_bytes(b"")
    assert schedtask.python_exe() == str(tmp_path / "pythonw.exe")
    assert schedtask.tr_command() == f'"{tmp_path / "pythonw.exe"}" -m app.cli autopilot --once'


def test_install_creates_the_task_from_xml(fake_schtasks):
    calls, xmls, _codes = fake_schtasks
    msg = schedtask.install_task(6)
    assert calls[0][:3] == ["schtasks", "/Create", "/TN"] and calls[0][3] == "vizsup-autopilot"
    assert "/XML" in calls[0] and calls[0][-1] == "/F" and len(calls) == 1
    assert "PT6H" in xmls[0] and "Đã bật chạy nền: mỗi 6 giờ" in msg
    assert not Path(calls[0][calls[0].index("/XML") + 1]).exists()        # temp file removed


def test_install_falls_back_to_the_plain_hourly_form(fake_schtasks):
    calls, _xmls, codes = fake_schtasks
    codes[:] = [1, 0]
    msg = schedtask.install_task(30)
    assert calls[1][1:] == ["/Create", "/SC", "HOURLY", "/MO", "23", "/TN", "vizsup-autopilot",
                            "/TR", schedtask.tr_command(), "/F"]
    assert "mỗi 23 giờ" in msg


def test_install_reports_a_refusal(fake_schtasks):
    _calls, _xmls, codes = fake_schtasks
    codes[:] = [1, 1]
    with pytest.raises(RuntimeError, match="từ chối"):
        schedtask.install_task(6)


def test_remove_and_query(fake_schtasks):
    calls, _xmls, codes = fake_schtasks
    assert schedtask.remove_task() is True
    assert calls[-1] == ["schtasks", "/Delete", "/TN", "vizsup-autopilot", "/F"]
    codes[:] = [1]
    assert schedtask.task_installed() is False
    assert calls[-1] == ["schtasks", "/Query", "/TN", "vizsup-autopilot"]
    assert schedtask.task_installed() is True


def test_nothing_runs_off_windows(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no schtasks off Windows")

    monkeypatch.setattr(schedtask.subprocess, "run", boom)
    monkeypatch.setattr(schedtask, "supported", lambda: False)
    assert schedtask.task_installed() is False and schedtask.remove_task() is False
    with pytest.raises(RuntimeError, match="Windows"):
        schedtask.install_task(6)


# --- the button in the Bộ phim dialog ----------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")
sd = pytest.importorskip("desktop.series_dialog")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class SyncRunner:
    def run(self, fn, on_done, on_fail=None):
        try:
            res = fn()
        except Exception as exc:  # noqa: BLE001
            on_fail(f"{type(exc).__name__}: {exc}")
            return
        on_done(res)

    def shutdown(self):
        pass


@pytest.fixture()
def dialog(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    monkeypatch.setattr(schedtask, "supported", lambda: True)
    calls = []
    monkeypatch.setattr(schedtask, "task_installed", lambda: calls.append("query") or False)
    monkeypatch.setattr(schedtask, "install_task",
                        lambda h: calls.append(("install", h)) or f"Đã bật chạy nền: mỗi {h} giờ.")
    monkeypatch.setattr(schedtask, "remove_task", lambda: calls.append("remove") or True)
    other = series.create("萧族降世")
    series.save_autopilot(other.slug, on=True, sources=["https://space.bilibili.com/1"], every_h=4)
    dlg = sd.SeriesDialog(series.create("末日校车"))
    dlg._bg = SyncRunner()
    yield dlg, calls
    dlg.reject()


def test_the_task_is_only_looked_up_when_the_tab_opens(dialog):
    dlg, calls = dialog
    assert calls == []
    dlg.tabs.setCurrentIndex(0)
    assert calls == []
    dlg.tabs.setCurrentIndex(dlg._watch_index)
    dlg.tabs.setCurrentIndex(0)
    dlg.tabs.setCurrentIndex(dlg._watch_index)
    assert calls == ["query"]
    assert "chưa bật" in dlg.task_lbl.text() and not dlg.task_off.isEnabled()


def test_install_uses_the_most_frequent_followed_series_and_nothing_without_a_click(dialog):
    dlg, calls = dialog
    dlg.watch_every.setValue(6)
    dlg.task_on.click()
    assert calls == [("install", 4)]                     # the other series checks every 4 h
    assert "mỗi 4 giờ" in dlg.task_lbl.text() and "Lưu" in dlg.task_lbl.text()
    assert dlg.task_off.isEnabled() and dlg.task_on.text() == "Đặt lại lịch"
    dlg.watch_on.setChecked(True)
    dlg.watch_every.setValue(2)
    dlg.task_on.click()
    assert calls[-1] == ("install", 2) and "Lưu" not in dlg.task_lbl.text()
    dlg.task_off.click()
    assert calls[-1] == "remove" and "Đã tắt" in dlg.task_lbl.text()


def test_a_refused_install_is_shown(dialog, monkeypatch):
    dlg, _calls = dialog

    def refuse(h):
        raise RuntimeError("Windows không tạo được lịch chạy nền: access denied")

    monkeypatch.setattr(schedtask, "install_task", refuse)
    dlg.task_on.click()
    assert "access denied" in dlg.task_lbl.text() and dlg.task_on.isEnabled()
