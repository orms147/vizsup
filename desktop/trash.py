"""Move files and folders to the Recycle Bin — never a permanent delete.

The project library (desktop/views/library_view.py) deletes through here so a slip
can be undone from the Windows Recycle Bin. ``send2trash`` is used when installed;
otherwise on Windows the shell's own ``SHFileOperationW`` with FOF_ALLOWUNDO (what
Explorer's Delete key does). Anywhere else, without send2trash, nothing is deleted.

FOF_WANTNUKEWARNING: when an item cannot be recycled (too big for the bin, a drive
without one) Windows ASKS before destroying it, instead of FOF_NOCONFIRMATION
silently deleting it for good.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

FO_DELETE = 0x0003
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_WANTNUKEWARNING = 0x4000
FLAGS = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_WANTNUKEWARNING


class TrashError(RuntimeError):
    """Nothing (or not everything) reached the Recycle Bin; the message is for the user."""


def move_to_trash(paths) -> None:
    """Send every existing path in ``paths`` to the Recycle Bin. Raises TrashError when
    that is impossible or something is still on disk afterwards."""
    items = [str(Path(p).resolve()) for p in paths if os.path.lexists(p)]
    if not items:
        return
    try:
        from send2trash import send2trash  # type: ignore[import-not-found]
    except ImportError:
        send2trash = None
    if send2trash is not None:
        try:
            for p in items:
                send2trash(p)
        except Exception as exc:  # noqa: BLE001 - report, never fall back to rmtree
            raise TrashError(f"Không chuyển được vào Thùng rác: {exc}") from exc
    elif sys.platform == "win32":
        _shell_delete(items)
    else:
        raise TrashError("Máy này không có cách chuyển vào Thùng rác (cài gói send2trash) — "
                         "chưa xóa gì.")
    left = [p for p in items if os.path.lexists(p)]
    if left:
        raise TrashError("Một số mục chưa chuyển được vào Thùng rác (đang được mở ở chương "
                         "trình khác?):\n" + "\n".join(left[:6]))


def _shell_delete(items: list[str]) -> None:
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    # pFrom: NUL-separated full paths ending in a double NUL (the buffer adds the last one)
    buf = ctypes.create_unicode_buffer("\0".join(items) + "\0")
    op = SHFILEOPSTRUCTW(hwnd=None, wFunc=FO_DELETE, pFrom=ctypes.cast(buf, wintypes.LPCWSTR),
                         pTo=None, fFlags=FLAGS)
    try:
        rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    except Exception as exc:  # noqa: BLE001
        raise TrashError(f"Không gọi được Thùng rác của Windows: {exc}") from exc
    if op.fAnyOperationsAborted:
        raise TrashError("Đã hủy — chưa chuyển hết vào Thùng rác.")
    if rc != 0:
        raise TrashError(f"Windows không chuyển được vào Thùng rác (mã {rc:#x}).")
