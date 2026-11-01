"""Follow-ups of the 2026-11-01 UX audit: readable errors, an empty "Dựng hàng loạt", the
speaker as a mute button (the seek bar keeps its length), Vietnamese names in "Thêm video
vào bản ghép"."""
from __future__ import annotations

import pytest


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class APIConnectionError(Exception):
    pass


class AuthenticationError(Exception):
    pass


class RateLimitError(Exception):
    pass


def test_errors_read_as_plain_vietnamese():
    from desktop import errors

    assert errors.user_message(APIConnectionError("Connection error.")) == errors.OFFLINE
    assert errors.user_message(OSError("[Errno 11001] getaddrinfo failed")) == errors.OFFLINE
    assert errors.user_message(AuthenticationError("Error code: 401")) == errors.BAD_KEY
    assert errors.user_message(RateLimitError("429")) == errors.SERVER_BUSY
    assert errors.user_message(RuntimeError("Máy chủ giọng đọc đang giới hạn")) == \
        "Máy chủ giọng đọc đang giới hạn"                       # our own sentence, as it is
    assert errors.user_message(KeyError("x")) == "KeyError: 'x'"  # unknown: keeps its type


def test_the_editor_task_reports_the_readable_sentence(qapp):
    from desktop import errors
    from desktop.views.editor_view import _Task

    got = []
    t = _Task(lambda: (_ for _ in ()).throw(APIConnectionError("Connection error.")))
    t.failed.connect(got.append)
    t.run()
    assert got == [errors.OFFLINE]


def test_an_empty_batch_shows_a_sentence_not_an_empty_list(qapp):
    from desktop.widgets.batch_dialog import BatchDialog

    d = BatchDialog(None, [], [])
    d.show()
    qapp.processEvents()
    assert not d.list.isVisible()
    assert not d.ok_btn.isEnabled()
    d.close()


def test_the_speaker_mutes_and_a_narrow_panel_keeps_only_it(qapp):
    from desktop.widgets.video_panel import VOLUME_WIDE_PANEL, VideoPanel

    p = VideoPanel("Video nguồn")
    p.resize(VOLUME_WIDE_PANEL - 200, 500)
    p.show()
    qapp.processEvents()
    assert p._vol_icon.isVisible() and not p._vol.isVisible()
    p._vol_icon.setChecked(True)
    if p._audio is not None:
        assert p._audio.isMuted()
    p.resize(VOLUME_WIDE_PANEL + 100, 500)
    qapp.processEvents()
    assert p._vol.isVisible()
    p.close()


def test_add_to_join_lists_the_series_name(qapp, monkeypatch):
    from app.pipeline.compilation import Source
    from desktop import store
    from desktop.widgets import compile_dialog

    monkeypatch.setattr(store, "friendly_title", lambda iid, t: "Chuyến Xe Tận Thế · Tập 9")
    src = Source("e9", "job", "第7集 | 《末日校车》09 我一个普通大巴司机", [], [(0.0, "Tập 9")],
                 [], "ok", "", 119.0)
    d = compile_dialog._PickDialog(None, [src])
    text = d.list.item(0).text()
    assert "Chuyến Xe Tận Thế · Tập 9" in text and "普通" not in text
    d.close()
