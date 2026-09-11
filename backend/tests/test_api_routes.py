"""FastAPI routes: the HTTP surface of the job lifecycle.

`test_api_contract.py` drives the JobManager directly; this file drives the HTTP
layer, which had exactly one test (a 400). The important route is the subtitle
pair — GET/PUT /subtitles IS the human edit gate expressed over HTTP, so a
round-trip that loses text or times would silently destroy the user's edits.

No stage ever runs here: run_until_gate/render are replaced with fakes.
"""
from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from app import jobs as jobs_mod  # noqa: E402
from app.jobs import Stage  # noqa: E402
from app.main import app  # noqa: E402

SRT = "1\n00:00:01,000 --> 00:00:02,500\nxin chào\n\n2\n00:00:03,000 --> 00:00:04,000\ntạm biệt\n"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_mod.settings, "vizsup_storage_dir", tmp_path)
    monkeypatch.setattr(jobs_mod.manager, "_jobs", {})

    async def fake_gate(job_id):
        st = jobs_mod.manager.get(job_id)
        st.job.cn_srt.write_text("1\n00:00:01,000 --> 00:00:02,500\n你好\n", encoding="utf-8")
        st.job.vi_srt.write_text(SRT, encoding="utf-8")
        jobs_mod.manager._emit(st, stage=Stage.AWAIT_EDIT, progress=1.0, message="gate")

    async def fake_render(job_id):
        st = jobs_mod.manager.get(job_id)
        st.job.output_video.write_bytes(b"mp4")
        jobs_mod.manager._emit(st, stage=Stage.DONE, progress=1.0, message="done")

    monkeypatch.setattr(jobs_mod.manager, "run_until_gate", fake_gate)
    monkeypatch.setattr(jobs_mod.manager, "render", fake_render)
    with TestClient(app) as c:
        yield c


def _job(client) -> str:
    r = client.post("/api/jobs", json={"url": "https://b23.tv/x"})
    assert r.status_code == 200
    return r.json()["id"]


# --- basics -------------------------------------------------------------------

def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_providers_lists_the_three_kinds(client):
    body = client.get("/api/providers").json()
    assert {"translation", "tts", "asr"} <= set(body)


def test_create_job_accepts_a_local_file_too(client, tmp_path):
    r = client.post("/api/jobs", json={"file": str(tmp_path / "clip.mp4")})
    assert r.status_code == 200 and r.json()["id"]


def test_unknown_job_is_404_everywhere(client):
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.get("/api/jobs/nope/subtitles").status_code == 404
    assert client.put("/api/jobs/nope/subtitles", json={"vi": []}).status_code == 404
    assert client.post("/api/jobs/nope/render", json={}).status_code == 404
    assert client.post("/api/jobs/nope/cancel").status_code == 404
    assert client.get("/api/jobs/nope/download/output.mp4").status_code == 404


# --- the edit gate over HTTP --------------------------------------------------

def test_subtitles_round_trip_preserves_text_and_times(client):
    jid = _job(client)
    got = client.get(f"/api/jobs/{jid}/subtitles").json()
    assert [c["text"] for c in got["vi"]] == ["xin chào", "tạm biệt"]
    assert [c["text"] for c in got["cn"]] == ["你好"]
    assert got["vi"][0]["start"] == 1.0 and got["vi"][0]["end"] == 2.5

    edited = got["vi"]
    edited[0]["text"] = "Chào bạn — đã sửa"
    r = client.put(f"/api/jobs/{jid}/subtitles", json={"vi": edited})
    assert r.status_code == 200 and r.json()["count"] == 2

    back = client.get(f"/api/jobs/{jid}/subtitles").json()["vi"]
    assert back[0]["text"] == "Chào bạn — đã sửa"
    assert back[0]["start"] == 1.0 and back[0]["end"] == 2.5   # times survive the edit
    assert back[1]["text"] == "tạm biệt"


def test_subtitles_before_any_stage_ran_are_empty_not_an_error(client, tmp_path):
    (tmp_path / "bare").mkdir()
    got = client.get("/api/jobs/bare/subtitles")
    assert got.status_code == 200
    assert got.json() == {"cn": [], "vi": []}


# --- render gating ------------------------------------------------------------

def test_render_before_the_gate_is_409(client, tmp_path):
    (tmp_path / "bare").mkdir()
    r = client.post("/api/jobs/bare/render", json={})
    assert r.status_code == 409
    assert "vi.srt" in r.json()["detail"]


def test_render_after_the_gate_produces_a_downloadable_artifact(client):
    jid = _job(client)
    assert client.post(f"/api/jobs/{jid}/render", json={"tts": "edge"}).status_code == 200
    assert client.get(f"/api/jobs/{jid}").json()["stage"] == Stage.DONE.value
    assert client.get(f"/api/jobs/{jid}/download/output.mp4").content == b"mp4"


def test_only_allowlisted_artifact_names_are_served(client):
    """The name goes into a path — an unknown key must not reach the filesystem."""
    jid = _job(client)
    assert client.get(f"/api/jobs/{jid}/download/metadata.json").status_code == 404
    assert client.get(f"/api/jobs/{jid}/download/vi.srt").status_code == 200


def test_render_resets_a_stale_cancel_flag(client):
    jid = _job(client)
    client.post(f"/api/jobs/{jid}/cancel")
    assert jobs_mod.manager.get(jid).cancelled is True
    client.post(f"/api/jobs/{jid}/render", json={})
    assert jobs_mod.manager.get(jid).cancelled is False


# --- state reload from disk ---------------------------------------------------

def test_stage_is_inferred_from_artifacts_after_a_restart(client, tmp_path):
    """get_or_load rebuilds a minimal state so the gate survives a server restart."""
    gated = tmp_path / "gated"
    gated.mkdir()
    (gated / "vi.srt").write_text(SRT, encoding="utf-8")
    assert client.get("/api/jobs/gated").json()["stage"] == Stage.AWAIT_EDIT.value

    done = tmp_path / "done"
    done.mkdir()
    (done / "vi.srt").write_text(SRT, encoding="utf-8")
    (done / "output.mp4").write_bytes(b"o")
    assert client.get("/api/jobs/done").json()["stage"] == Stage.DONE.value


# --- websocket ----------------------------------------------------------------

def test_ws_for_an_unknown_job_reports_and_closes(client):
    with client.websocket_connect("/ws/jobs/nope") as ws:
        assert "error" in ws.receive_json()


def test_ws_sends_an_initial_snapshot(client):
    jid = _job(client)
    with client.websocket_connect(f"/ws/jobs/{jid}") as ws:
        snap = ws.receive_json()
        assert snap["id"] == jid
        assert "stage" in snap and "progress" in snap


class _FakeWS:
    """Minimal WebSocket stand-in so the handler can be driven deterministically.

    A live TestClient socket cannot be used here: if the handler regresses to
    never closing, `receive_json()` blocks forever and the run HANGS instead of
    failing. Driving the coroutine lets asyncio.wait_for bound it.
    """

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False

    async def accept(self) -> None:
        pass

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True


async def _drive_until(job_id, stage, *, timeout=2.0):
    """Run job_ws, publish one `stage` event, and wait for the handler to finish."""
    import asyncio

    from app.main import job_ws

    state = jobs_mod.manager.create(job_id, url="u")
    ws = _FakeWS()
    task = asyncio.create_task(job_ws(ws, job_id))
    for _ in range(5):                       # let the handler accept + subscribe
        await asyncio.sleep(0)
    jobs_mod.manager._emit(state, stage=stage, progress=1.0, message="x")
    try:
        await asyncio.wait_for(task, timeout=timeout)
        finished = True
    except TimeoutError:
        task.cancel()
        finished = False
    return ws, finished


@pytest.mark.parametrize("stage", [Stage.DONE, Stage.ERROR])
def test_ws_closes_after_a_terminal_event(client, stage):
    """DONE/ERROR are terminal: the server closes, instead of holding the socket
    open forever on a `continue` that did nothing."""
    import asyncio

    ws, finished = asyncio.run(_drive_until(f"term-{stage.value}", stage))
    assert finished, "handler never returned — the socket is being held open"
    assert ws.closed
    assert ws.sent[-1]["stage"] == stage.value


def test_ws_stays_open_at_the_edit_gate(client):
    """AWAIT_EDIT is NOT terminal — the client parks at the gate and the same
    socket carries the render progress that follows."""
    import asyncio

    ws, finished = asyncio.run(_drive_until("gate-ws", Stage.AWAIT_EDIT, timeout=0.4))
    assert not finished, "socket closed at the gate; render progress would be lost"
    assert not ws.closed
    assert ws.sent[-1]["stage"] == Stage.AWAIT_EDIT.value
