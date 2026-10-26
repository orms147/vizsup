"""E-4: who is actually speaking — so the brief can fix Vietnamese xưng hô.

Vietnamese pronouns (anh / chị / em / cô / chú / mình / bạn …) depend on the speakers'
gender, age and relationship; the transcript alone rarely says. Before the brief:

- AUDIO (part of "Tự đọc ngữ cảnh video"): the listening model (ASR audio role) hears a
  ≤60 s excerpt chosen where local VAD hears the most voice and describes the speakers.
- FRAMES (separate opt-in, uploads ~6 images): the vision role model describes who is on
  screen and the setting.

Both are cached in ``observed.json`` (keyed by the source file) and handed to
``brief.build_brief`` as OBSERVED notes. Never raises: no observation → brief as before.
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
import tempfile
from pathlib import Path

from app.models import Job

EXCERPT_SEC = 60.0
N_FRAMES = 6
_CACHE = "observed.json"

AUDIO_QUESTION = (
    "Listen to this excerpt of a Chinese video. Answer in Vietnamese, at most 80 words, "
    "plain text: how many people speak or sing; for each one their likely gender and age "
    "group (trẻ em / thanh niên / trung niên / lớn tuổi); whether they mostly SING or TALK; "
    "who they address (khán giả, bạn bè, người yêu, gia đình, đồng nghiệp…) and their "
    "relationship if it is audible. Write 'không rõ' when unsure. Never invent names. "
    "End with one last line exactly in the form 'HÁT: có' (mostly singing), 'HÁT: xen kẽ' "
    "(singing and talking) or 'HÁT: không' (talking)."
)
_SINGING = {"có": "yes", "xen kẽ": "mixed", "không": "no"}
FRAMES_QUESTION = (
    "These are frames from one Chinese short video, in order. Answer in Vietnamese, at most "
    "80 words, plain text: who is on screen (number of people, likely gender and age "
    "group), the setting (livestream, vlog, phim, quảng cáo…), and anything that tells how "
    "the people relate to each other or to the viewer. Do NOT say who is speaking or "
    "singing — a person on screen may be silent. Write 'không rõ' when unsure."
)


def pick_excerpt(spans: list[tuple[float, float]] | None, duration: float,
                 seconds: float = EXCERPT_SEC) -> tuple[float, float]:
    """The window of ``seconds`` with the most voice in it (VAD spans). Pure."""
    if duration <= seconds or not spans:
        return 0.0, min(duration, seconds) if duration > 0 else seconds
    best, best_v = 0.0, -1.0
    for s, _e in spans:
        a = max(0.0, min(s, duration - seconds))
        b = a + seconds
        v = sum(max(0.0, min(e2, b) - max(s2, a)) for s2, e2 in spans)
        if v > best_v:
            best, best_v = a, v
    return round(best, 2), round(best + seconds, 2)


def _key(job: Job) -> str:
    src = job.source_audio if job.source_audio.exists() else job.source_video
    try:
        st = src.stat()
        return f"{src.name}:{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        return ""


def _load(job: Job) -> dict:
    try:
        data = json.loads((job.work_dir / _CACHE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data.get("key") == _key(job) else {}
    except (OSError, ValueError):
        return {}


def _save(job: Job, data: dict) -> None:
    try:
        (job.work_dir / _CACHE).write_text(json.dumps({**data, "key": _key(job)},
                                                      ensure_ascii=False, indent=1),
                                           encoding="utf-8")
    except OSError:
        pass


def _window(job: Job):
    """(src, a, b, voice spans) of the excerpt both the listener and the pitch check use."""
    from app.pipeline import vad
    from app.pipeline.assemble import probe_duration

    src = job.source_audio if job.source_audio.exists() else job.source_video
    if not src.exists():
        return None
    spans = vad.spans_for(src, job.work_dir)
    a, b = pick_excerpt(spans, probe_duration(src) or EXCERPT_SEC)
    return src, a, b, spans


def _measure(job: Job, _say) -> dict | None:
    """Local pitch verdict on the same excerpt (pitch.py) — nothing uploaded."""
    from app.pipeline import pitch

    win = _window(job)
    return pitch.measure(*win) if win else None


def _listen(job: Job, say) -> str | None:
    from app.ffmpegutil import ffmpeg_bin
    from app.providers.registry import get_asr

    prov = get_asr("audio_llm")
    if not prov.available():
        return None
    win = _window(job)
    if win is None:
        return None
    src, a, b, _spans = win
    with tempfile.TemporaryDirectory(prefix="vizsup_spk_") as tmp:
        clip = Path(tmp) / "excerpt.mp3"
        subprocess.run([ffmpeg_bin(), "-y", "-ss", f"{a:.2f}", "-t", f"{b - a:.2f}", "-i", str(src),
                        "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", str(clip)],
                       check=True, capture_output=True)
        say(f"AI nghe đoạn {a:.0f}–{b:.0f} s để biết ai đang nói…")
        return prov.ask(clip, AUDIO_QUESTION, max_tokens=500) or None


def _look(job: Job, say) -> str | None:
    from app.ffmpegutil import ffmpeg_bin
    from app.pipeline.assemble import probe_duration
    from app.providers import roles
    from app.providers.translation.openrouter import OpenRouter

    if not job.source_video.exists():
        return None
    prov = OpenRouter()
    if not prov.available():
        return None
    dur = probe_duration(job.source_video) or 0.0
    images = []
    with tempfile.TemporaryDirectory(prefix="vizsup_frm_") as tmp:
        for k in range(N_FRAMES):
            t = dur * (k + 0.5) / N_FRAMES
            out = Path(tmp) / f"f{k}.jpg"
            subprocess.run([ffmpeg_bin(), "-y", "-ss", f"{t:.2f}", "-i", str(job.source_video),
                            "-frames:v", "1", "-vf", "scale=512:-2", "-q:v", "4", str(out)],
                           capture_output=True)
            if out.exists():
                images.append(base64.b64encode(out.read_bytes()).decode())
    if not images:
        return None
    say(f"AI xem {len(images)} khung hình ({roles.model_for('vision')})…")
    content = [{"type": "text", "text": FRAMES_QUESTION}] + [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b}"}} for b in images]
    resp = prov._chat(model=roles.model_for("vision"), temperature=0, max_tokens=500,
                      messages=[{"role": "user", "content": content}])
    return (resp.choices[0].message.content or "").strip() or None


_VOICE = {"low": "giọng trầm (thường là nam)", "high": "giọng cao (thường là nữ)"}
_NOT_GENDER = re.compile(r"việt nam|miền nam|phía nam|hướng nam|nam bộ|nam phi|nam mỹ|"
                         r"nam kinh|đông nam|tây nam")
_CHILD = re.compile(r"trẻ em|trẻ con|em bé|cậu bé|cô bé|bé trai|bé gái|thiếu nhi")


def genders(text: str | None) -> set[str]:
    """{'low', 'high'} for the genders the listener names ('nam' / 'nữ'). Pure."""
    t = _NOT_GENDER.sub(" ", (text or "").lower())
    out = set()
    if re.search(r"\bnam\b", t):
        out.add("low")
    if re.search(r"\bnữ\b", t):
        out.add("high")
    return out


def pitch_note(audio: str | None, measured: dict | None) -> str | None:
    """A line for the brief when the measured pitch says something the listener did not:
    it contradicts a single named gender (→ CHƯA CHẮC) or fills in an unnamed one. Only
    for talking — singing and children's voices don't follow the speech ranges. Pure."""
    voice = (measured or {}).get("voice")
    if voice not in _VOICE or parse_singing(audio) != "no" or _CHILD.search((audio or "").lower()):
        return None
    said = genders(audio)
    head = f"Cao độ giọng đo trên máy: trung vị ~{measured['median']} Hz — nghiêng về {_VOICE[voice]}"
    if said and voice not in said:
        return (f"{head}, trái với nhận xét nghe ở trên. Giới tính người nói CHƯA CHẮC: dùng "
                f"xưng hô trung tính (mình / bạn) trừ khi lời thoại cho biết rõ.")
    if not said:
        return head + "."
    return None


def parse_singing(text: str | None) -> str | None:
    """'yes' / 'mixed' / 'no' from the audio answer's last 'HÁT: …' line, else None."""
    m = re.search(r"HÁT\s*[:：]\s*(có|xen kẽ|không)", text or "", re.I)
    return _SINGING.get(m.group(1).lower()) if m else None


def singing(job: Job) -> str | None:
    """Cached verdict for this job (E-8), or None when nothing was observed."""
    return parse_singing(_load(job).get("audio"))


def observe(job: Job, *, frames: bool = False, log=None) -> str | None:
    """OBSERVED notes for the brief (audio always, frames only when ``frames``), cached.
    None when nothing could be observed. Never raises."""
    say = log or (lambda _m: None)
    data = _load(job)
    changed = False
    for kind, wanted, fn in (("audio", True, _listen), ("pitch", True, _measure),
                             ("frames", frames, _look)):
        if not wanted or data.get(kind):
            continue
        try:
            text = fn(job, say)
        except Exception as exc:  # noqa: BLE001 - an aid; the brief works without it
            say(f"⚠ Không quan sát được ({kind}: {type(exc).__name__}: {exc})")
            text = None
        if text:
            data[kind] = text
            changed = True
    if changed:
        _save(job, data)
    parts = []
    if data.get("audio"):
        parts.append(f"Nghe âm thanh (tin cậy cho ai nói / hát và giới tính): {data['audio']}")
        note = pitch_note(data["audio"], data.get("pitch"))
        if note:
            parts.append(note)
    if frames and data.get("frames"):
        parts.append(f"Xem khung hình (chỉ bối cảnh và ai xuất hiện): {data['frames']}")
    return "\n".join(parts) or None


# voice-name hints → the pitch class of the voice (first match wins: "female" ⊃ "male")
_VOICE_HINTS = (("hoaimy", "high"), ("namminh", "low"), ("female", "high"), ("male", "low"),
                ("nữ", "high"), ("nam", "low"))


def speaker_gender(job: Job) -> str | None:
    """'low' / 'high' when the listener heard ONE gender (observed.json), else None."""
    g = genders(_load(job).get("audio"))
    return next(iter(g)) if len(g) == 1 else None


def voice_for(voices: list[str], gender: str | None) -> str | None:
    """The first voice whose name says it matches ``gender`` (a male reviewer should not
    get HoaiMy by default). Pure."""
    if gender is None:
        return None
    for v in voices:
        low = v.lower()
        for key, g in _VOICE_HINTS:
            if key in low:
                if g == gender:
                    return v
                break
    return None
