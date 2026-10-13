"""UX-5: the Settings dialog exposes the non-secret knobs, not just API keys.

Every attr here already existed in app.config.Settings — the dialog just stops the
user hand-editing .env for cookies, ffmpeg/BBDown paths, storage dir and model size.
What matters is the round-trip: a value typed here must reach .env AND the live
settings object, and clearing one must actually clear it.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")
sd = pytest.importorskip("desktop.settings_dialog")

from PySide6.QtWidgets import QApplication, QComboBox  # noqa: E402

from app.config import settings  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Point the dialog at a throwaway .env, and BLANK the real API keys first.

    The dialog prefills its key fields from the live settings object, which is
    loaded from the developer's real repo-root .env — without this, _save() would
    copy actual credentials into the test's tmp .env.
    """
    path = tmp_path / ".env"
    monkeypatch.setattr(sd, "_ENV_PATH", path)
    for attr in sd.ATTR_BY_ENV.values():
        monkeypatch.setattr(settings, attr, "")
    monkeypatch.setattr(settings, "openrouter_model", "")
    saved = {attr: getattr(settings, attr) for attr in sd.OPT_ATTR_BY_ENV.values()}
    yield path
    for attr, value in saved.items():
        setattr(settings, attr, value)


def _read(path: Path) -> dict[str, str]:
    out = {}
    for ln in path.read_text(encoding="utf-8").splitlines():
        if "=" in ln and not ln.lstrip().startswith("#"):
            k, v = ln.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _set(dlg, env_name: str, value: str) -> None:
    w = dlg._opts[env_name]
    if isinstance(w, QComboBox):
        w.setEditText(value)
    else:
        w.setText(value)


# --- the fields exist ---------------------------------------------------------

def test_every_documented_option_is_on_the_dialog(qapp, env):
    dlg = sd.SettingsView()
    assert set(dlg._opts) == {e for e, *_ in sd.OPTIONS}
    for name in ("YTDLP_COOKIES_FROM_BROWSER", "YTDLP_COOKIES_FILE", "VIZSUP_STORAGE_DIR",
                 "FFMPEG_PATH", "BBDOWN_PATH", "WHISPER_MODEL", "ZHIPU_MODEL"):
        assert name in dlg._opts


def test_every_option_maps_to_a_real_settings_attr(qapp, env):
    """A typo in OPTIONS would silently write a value nothing ever reads."""
    for attr in sd.OPT_ATTR_BY_ENV.values():
        assert hasattr(settings, attr), attr


def test_fields_are_prefilled_from_current_settings(qapp, env, monkeypatch):
    monkeypatch.setattr(settings, "whisper_model", "small")
    monkeypatch.setattr(settings, "bbdown_path", "C:/tools/BBDown.exe")
    dlg = sd.SettingsView()
    assert dlg._opt_value("WHISPER_MODEL") == "small"
    assert dlg._opt_value("BBDOWN_PATH") == "C:/tools/BBDown.exe"


# --- round-trip ---------------------------------------------------------------

def test_options_round_trip_to_env_and_to_settings(qapp, env):
    dlg = sd.SettingsView()
    _set(dlg, "YTDLP_COOKIES_FROM_BROWSER", "edge")
    _set(dlg, "FFMPEG_PATH", "C:/ffmpeg/bin")
    _set(dlg, "WHISPER_MODEL", "medium")
    _set(dlg, "ZHIPU_MODEL", "glm-5.2-air")
    dlg._save()

    written = _read(env)
    assert written["YTDLP_COOKIES_FROM_BROWSER"] == "edge"
    assert written["FFMPEG_PATH"] == "C:/ffmpeg/bin"
    assert written["WHISPER_MODEL"] == "medium"

    assert settings.ytdlp_cookies_from_browser == "edge"
    assert settings.ffmpeg_path == "C:/ffmpeg/bin"
    assert settings.whisper_model == "medium"
    assert settings.zhipu_model == "glm-5.2-air"


def test_clearing_an_option_actually_clears_it(qapp, env, monkeypatch):
    """Blanking the cookie browser must mean 'no cookie browser'. Unlike API keys,
    an empty option is a real instruction, not an 'I left it alone'."""
    monkeypatch.setattr(settings, "ytdlp_cookies_from_browser", "chrome")
    dlg = sd.SettingsView()
    _set(dlg, "YTDLP_COOKIES_FROM_BROWSER", "")
    dlg._save()

    assert _read(env)["YTDLP_COOKIES_FROM_BROWSER"] == ""
    assert settings.ytdlp_cookies_from_browser == ""


def test_storage_dir_stays_a_path_object(qapp, env, tmp_path):
    """vizsup_storage_dir is typed Path — a str would break every `/` use of it."""
    target = tmp_path / "kho"
    dlg = sd.SettingsView()
    _set(dlg, "VIZSUP_STORAGE_DIR", str(target))
    dlg._save()

    assert isinstance(settings.vizsup_storage_dir, Path)
    assert settings.vizsup_storage_dir == target
    assert (settings.vizsup_storage_dir / "job").name == "job"


def test_blank_storage_dir_keeps_the_previous_one(qapp, env):
    """There is no sane 'no storage dir' — an empty box must not produce Path('')."""
    before = settings.vizsup_storage_dir
    dlg = sd.SettingsView()
    _set(dlg, "VIZSUP_STORAGE_DIR", "")
    dlg._save()
    assert settings.vizsup_storage_dir == before


def test_saving_options_does_not_wipe_unrelated_env_lines(qapp, env):
    env.write_text("OPENROUTER_API_KEY=sk-keep-me\n# a comment\n", encoding="utf-8")
    dlg = sd.SettingsView()
    _set(dlg, "WHISPER_MODEL", "tiny")
    dlg._save()

    text = env.read_text(encoding="utf-8")
    assert "OPENROUTER_API_KEY=sk-keep-me" in text
    assert "# a comment" in text
    assert _read(env)["WHISPER_MODEL"] == "tiny"


def test_an_empty_api_key_does_not_erase_the_stored_one(qapp, env):
    """Keys keep the old behaviour: a blank field means 'unchanged', because the
    dialog shows them masked and a stray clear would lose the user's key."""
    env.write_text("DEEPSEEK_API_KEY=sk-secret\n", encoding="utf-8")
    dlg = sd.SettingsView()
    dlg._fields["DEEPSEEK_API_KEY"].setText("")
    dlg._save()
    assert "DEEPSEEK_API_KEY=sk-secret" in env.read_text(encoding="utf-8")


def test_promo_state_follows_the_translator_expiry_rule(qapp, env, monkeypatch):
    """Unused FROM the expiry day on; a malformed date disables the promo."""
    from datetime import date, timedelta

    monkeypatch.setattr(settings, "openrouter_promo_model", "stealth/x")
    view = sd.SettingsView()
    _set(view, "OPENROUTER_PROMO_EXPIRES", date.today().isoformat())
    view._mark_dirty()
    assert "hết hạn" in view.promo_state.text()
    _set(view, "OPENROUTER_PROMO_EXPIRES", (date.today() + timedelta(days=3)).isoformat())
    view._mark_dirty()
    assert view.promo_state.text().startswith("Đang dùng stealth/x")
    _set(view, "OPENROUTER_PROMO_EXPIRES", "5/10")
    view._mark_dirty()
    assert "KHÔNG" in view.promo_state.text()


def test_cancel_changes_restores_the_saved_values(qapp, env, monkeypatch):
    monkeypatch.setattr(settings, "bbdown_path", "C:/a.exe")
    view = sd.SettingsView()
    _set(view, "BBDOWN_PATH", "C:/b.exe")
    view._mark_dirty()
    assert view.is_dirty()
    view.cancel_btn.click()
    assert view._opt_value("BBDOWN_PATH") == "C:/a.exe" and not view.is_dirty()


def test_role_models_round_trip_and_blank_means_default(qapp, env, monkeypatch):
    from app.providers import roles

    for r in roles.ROLES.values():
        monkeypatch.setattr(settings, r.attr, "")
    monkeypatch.setattr(settings, "model_draft", "deepseek/deepseek-v4.1-flash")
    view = sd.SettingsView()
    assert view.role_combos["draft"].currentText() == "deepseek/deepseek-v4.1-flash"
    view.role_combos["refine"].setEditText("moonshotai/kimi-k3")
    view.role_combos["draft"].setEditText("")
    view._save()
    written = _read(env)
    assert written["MODEL_REFINE"] == "moonshotai/kimi-k3" and written["MODEL_DRAFT"] == ""
    assert roles.model_for("refine") == "moonshotai/kimi-k3"
    assert roles.model_for("draft") == roles.ROLES["draft"].default
