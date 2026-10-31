"""Windows background run for "theo dõi tập mới": ONE scheduled task, ``vizsup-autopilot``,
that runs ``pythonw -m app.cli autopilot --once`` every N hours in the repo's backend folder
while the owner is logged on. It is created / removed only when the owner clicks the button
(Bộ phim → Theo dõi → "Bật chạy nền"), never by the app on its own.

``schtasks /Create /XML`` is used because it is the only schtasks form that sets the working
folder, and it lets the task start on battery (a task made with ``/SC HOURLY`` does not — the
owner's machine is a laptop), skip a start while the previous run is still going and catch up
after sleep. If Windows refuses the XML, the plain ``/SC HOURLY /MO <h> /TR …`` form is used
(it runs from System32: still fine, ``app`` is installed and .env / storage are anchored to the
repo in app/config.py). pythonw = no console window; the run's output goes to
``<storage>/_autopilot/autopilot.log``.

With the Telegram bot paired (app/pipeline/remote.py) the task repeats every
``REMOTE_EVERY_MIN`` minutes instead, so a link or a ✅ sent from the phone is picked up soon
while the app is closed; each followed series is still checked only at its own interval
(watch.is_due). Still created / changed only by the owner's click (Bộ phim → Theo dõi →
"Bật chạy nền" / "Đặt lại lịch", or Cài đặt → Đăng video → Telegram).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

TASK = "vizsup-autopilot"
ARGS = "-m app.cli autopilot --once"
_NO_WINDOW = 0x08000000          # CREATE_NO_WINDOW
_TR_MAX = 261                    # schtasks' /TR limit
REMOTE_EVERY_MIN = 15            # with the Telegram bot paired: read the owner's messages this often


def supported() -> bool:
    return sys.platform == "win32"


def backend_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def python_exe() -> str:
    """pythonw.exe next to this interpreter (no console window), else this interpreter."""
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.exists() else exe)


def tr_command() -> str:
    """The /TR value: the interpreter quoted (its path may have spaces), then the arguments."""
    return f'"{python_exe()}" {ARGS}'


def _hours(every_h) -> int:
    try:
        return max(1, min(168, int(every_h)))
    except (TypeError, ValueError):
        return 6


def _minutes(every_min) -> int | None:
    try:
        return max(5, min(60, int(every_min))) if every_min else None
    except (TypeError, ValueError):
        return None


def _remote_paired() -> bool:
    try:
        from app.remote import telegram

        return telegram.paired()
    except Exception:  # noqa: BLE001 - the task works without the bot
        return False


def task_xml(every_h: int, *, start: datetime | None = None, user: str | None = None,
             every_min: int | None = None) -> str:
    """The task definition (Task Scheduler schema 1.2): every ``every_h`` hours, or every
    ``every_min`` minutes when given. Pure."""
    h = _hours(every_h)
    m = _minutes(every_min)
    interval = f"PT{m}M" if m else f"PT{h}H"
    start = (start or datetime.now() + timedelta(minutes=5)).replace(microsecond=0)
    who = f"\n      <UserId>{escape(user)}</UserId>" if user else ""
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>vizsup: kiểm tra tập mới của các bộ phim đang theo dõi, chạy tới bước duyệt (không bao giờ tự duyệt); nếu đã ghép bot Telegram thì đọc tin nhắn của bạn và chỉ dựng tập bạn đã bấm Duyệt. Tắt trong vizsup → Bộ phim → Theo dõi.</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <Repetition>
        <Interval>{interval}</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>{start.isoformat()}</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">{who}
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <Enabled>true</Enabled>
    <ExecutionTimeLimit>PT12H</ExecutionTimeLimit>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(python_exe())}</Command>
      <Arguments>{escape(ARGS)}</Arguments>
      <WorkingDirectory>{escape(str(backend_dir()))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *args], capture_output=True, text=True, timeout=30,
                          errors="replace", creationflags=_NO_WINDOW if supported() else 0)


def _why(r: subprocess.CompletedProcess) -> str:
    return " ".join(((r.stderr or "") + " " + (r.stdout or "")).split())[:300] or \
        f"mã {r.returncode}"


def _user() -> str | None:
    name = os.environ.get("USERNAME")
    dom = os.environ.get("USERDOMAIN")
    return f"{dom}\\{name}" if name and dom else name


def install_task(every_h: int, *, every_min: int | None = None) -> str:
    """Create (or replace) the task: every ``every_h`` hours — every ``every_min`` minutes
    when given, every REMOTE_EVERY_MIN when the Telegram bot is paired — first run in ~5
    minutes. Returns a short Vietnamese summary; raises RuntimeError when Windows refuses."""
    if not supported():
        raise RuntimeError("Chạy nền theo lịch chỉ có trên Windows.")
    h = _hours(every_h)
    m = _minutes(every_min if every_min is not None else
                 (REMOTE_EVERY_MIN if _remote_paired() else None))
    start = datetime.now() + timedelta(minutes=5)
    with tempfile.TemporaryDirectory(prefix="vizsup-task-") as td:
        xml = Path(td) / f"{TASK}.xml"
        xml.write_text(task_xml(h, start=start, user=_user(), every_min=m), encoding="utf-16")
        r = _schtasks("/Create", "/TN", TASK, "/XML", str(xml), "/F")
    if r.returncode != 0:
        first = _why(r)
        tr = tr_command()
        if len(tr) > _TR_MAX:
            raise RuntimeError(f"Windows không nhận lịch ({first}).")
        if m:
            r = _schtasks("/Create", "/SC", "MINUTE", "/MO", str(m), "/TN", TASK, "/TR", tr, "/F")
        else:
            r = _schtasks("/Create", "/SC", "HOURLY", "/MO", str(min(h, 23)), "/TN", TASK,
                          "/TR", tr, "/F")
        if r.returncode != 0:
            raise RuntimeError(f"Windows không tạo được lịch chạy nền: {_why(r)}")
        h = min(h, 23)
    if m:
        return (f"Đã bật chạy nền: mỗi {m} phút (đọc tin nhắn Telegram; tập mới vẫn kiểm tra "
                f"theo lịch của từng bộ phim), lần đầu khoảng {start:%H:%M}.")
    return f"Đã bật chạy nền: mỗi {h} giờ, lần đầu khoảng {start:%H:%M}."


def remove_task() -> bool:
    """Delete the task. False when there was none (or Windows refused)."""
    if not supported():
        return False
    return _schtasks("/Delete", "/TN", TASK, "/F").returncode == 0


def task_installed() -> bool:
    if not supported():
        return False
    try:
        return _schtasks("/Query", "/TN", TASK).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
