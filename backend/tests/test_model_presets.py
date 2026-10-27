"""2026-10-27 cost pass: model packages ("gói") in Settings → AI và mô hình.

One choice sets every text role; a hand-picked role model still wins for its role and
makes the package read "Tùy chỉnh". Persisted as MODEL_PRESET in .env.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from app.config import settings
from app.providers import presets, roles
from app.providers.roles import ROLES

LUNA, DS17, KIMI = presets.LUNA, presets.DS_1017, presets.KIMI


@pytest.fixture()
def clean(monkeypatch):
    monkeypatch.setattr(settings, "model_preset", "")
    for r in ROLES.values():
        monkeypatch.setattr(settings, r.attr, "")
    return settings


# --- mapping ----------------------------------------------------------------------

def test_the_default_package_is_the_role_table_and_not_kimi(clean):
    assert presets.active().key == presets.DEFAULT == "balanced"
    assert presets.PRESETS["balanced"].overrides == {}
    assert presets.models("balanced") == {r: ROLES[r].default for r in presets.TEXT_ROLES}
    assert roles.model_for("refine") == LUNA
    assert roles.model_for("shorten") == LUNA
    assert roles.model_for("brief") == DS17
    assert KIMI not in presets.models("balanced").values()


@pytest.mark.parametrize("key, expect", [
    ("economy", {"draft": LUNA, "refine": LUNA, "shorten": LUNA,
                 "brief": "deepseek/deepseek-v4.1-flash"}),
    ("quality", {"refine": DS17, "shorten": DS17, "brief": KIMI}),
    ("kimi", {"refine": KIMI, "brief": KIMI, "shorten": KIMI}),
])
def test_a_package_sets_its_roles_and_keeps_the_rest(clean, key, expect):
    clean.model_preset = key
    for role, model in expect.items():
        assert roles.model_for(role) == model
    for role in set(presets.TEXT_ROLES) - set(expect):
        assert roles.model_for(role) == ROLES[role].default
    assert roles.model_for("qa") == "qwen/qwen3.8-flash"       # a different vendor everywhere
    assert roles.model_for("vision") == ROLES["vision"].default  # opt-in roles: not packaged


def test_a_hand_picked_role_wins_over_the_package(clean):
    clean.model_preset = "kimi"
    clean.model_refine = "openai/gpt-6-luna-pro"
    assert roles.model_for("refine") == "openai/gpt-6-luna-pro"
    assert roles.model_for("brief") == KIMI
    assert presets.is_custom()


def test_an_unknown_package_falls_back_to_the_default(clean):
    clean.model_preset = "nonsense"
    assert presets.active().key == "balanced"


def test_the_openrouter_provider_asks_the_package(clean):
    from app.providers.translation.openrouter import OpenRouter

    clean.model_preset = "quality"
    p = OpenRouter()
    assert p.model_for("refine") == DS17
    assert p.model_for("draft") == ROLES["draft"].default
    assert "1017" in p.cost_note


def test_package_ids_are_current_and_shown_in_the_role_lists():
    known = presets.known_models()
    for m in (LUNA, DS17, KIMI, "deepseek/deepseek-v4.1-flash", "qwen/qwen3.8-flash"):
        assert m in known
    for p in presets.PRESETS.values():
        assert set(p.overrides) <= set(presets.TEXT_ROLES)
        assert all("deepseek-chat" not in m and "deepseek-reasoner" not in m
                   for m in p.overrides.values())
        assert p.usd_per_min > 0 and "/phút" in p.cost_label()
    assert presets.PRESETS["kimi"].usd_per_min > 5 * presets.PRESETS["balanced"].usd_per_min


# --- stray Chinese punctuation in a translated line (seen with luna-on-luna) --------

def test_stray_chinese_punctuation_is_cleaned_from_vietnamese_lines():
    from app.pipeline.translate import clean_vi

    assert clean_vi("Bọn mình đoán là Duo】【。") == "Bọn mình đoán là Duo."
    assert clean_vi("Ừ，được rồi！") == "Ừ, được rồi!"
    assert clean_vi("Bình thường, “đẹp” lắm.") == "Bình thường, “đẹp” lắm."
    assert clean_vi("还没翻译。") == "还没翻译。"          # a real miss stays visible
    assert clean_vi("") == ""


def test_translate_cues_returns_clean_lines():
    from app.models import Cue
    from app.pipeline import translate as tr

    class Fake:
        name = "fake"
        parallel_batches = 1

        def available(self):
            return True

        def translate_texts(self, texts, **_kw):
            return [f"câu {i}】。" for i, _t in enumerate(texts)]

    cues = [Cue(index=i + 1, start=i, end=i + 1, text="你好") for i in range(3)]
    orig = tr.get_translator
    tr.get_translator = lambda _n: Fake()
    try:
        out = tr.translate_cues(cues, draft="fake", auto_shorten=False)
    finally:
        tr.get_translator = orig
    assert [c.text for c in out] == ["câu 0.", "câu 1.", "câu 2."]


# --- Settings → AI và mô hình (offscreen) -----------------------------------------

@pytest.fixture()
def dlg_env(tmp_path, monkeypatch, clean):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    sd = pytest.importorskip("desktop.settings_dialog")
    QApplication.instance() or QApplication([])
    path = tmp_path / ".env"
    monkeypatch.setattr(sd, "_ENV_PATH", path)
    for attr in sd.ATTR_BY_ENV.values():            # never copy real keys into the tmp .env
        monkeypatch.setattr(settings, attr, "")
    for attr in sd.OPT_ATTR_BY_ENV.values():
        monkeypatch.setattr(settings, attr, getattr(settings, attr))
    return sd, path


def _env(path: Path) -> dict[str, str]:
    return dict(ln.split("=", 1) for ln in path.read_text(encoding="utf-8").splitlines()
                if "=" in ln)


def _pick(dlg, key: str) -> None:
    dlg.preset_combo.setCurrentIndex(dlg.preset_combo.findData(key))


def test_settings_lists_every_package_with_its_cost(dlg_env):
    sd, _path = dlg_env
    dlg = sd.SettingsView()
    items = [dlg.preset_combo.itemText(i) for i in range(dlg.preset_combo.count())]
    assert len(items) == len(presets.PRESETS) + 1 and items[-1].startswith("Tùy chỉnh")
    for p, text in zip(presets.PRESETS.values(), items):
        assert p.label in text and f"${p.usd_per_min:.4f}/phút" in text
    assert dlg.preset_combo.currentData() == "balanced"
    assert "video 30 phút" in dlg.preset_hint.text()
    assert dlg.role_combos["refine"].lineEdit().placeholderText().endswith(LUNA)
    # the new ids are in the lists before any “Tải danh sách model”
    combo = dlg.role_combos["refine"]
    assert combo.findText(LUNA) >= 0 and combo.findText(DS17) >= 0


def test_choosing_a_package_round_trips_to_env_and_settings(dlg_env):
    sd, path = dlg_env
    dlg = sd.SettingsView()
    _pick(dlg, "economy")
    assert dlg.is_dirty()
    assert dlg.role_combos["draft"].lineEdit().placeholderText().endswith(LUNA)
    dlg._save()
    env = _env(path)
    assert env["MODEL_PRESET"] == "economy" and env["MODEL_REFINE"] == ""
    assert settings.model_preset == "economy"
    assert roles.model_for("draft") == LUNA
    again = sd.SettingsView()                         # a fresh page shows the saved choice
    assert again.preset_combo.currentData() == "economy"


def test_editing_one_role_reads_custom_and_a_package_clears_it(dlg_env):
    sd, path = dlg_env
    dlg = sd.SettingsView()
    _pick(dlg, "quality")
    dlg.role_combos["refine"].setEditText("openai/gpt-6-luna-pro")
    assert dlg.preset_combo.currentData() == presets.CUSTOM
    assert "Chất lượng cao" in dlg.preset_hint.text()
    dlg._save()
    env = _env(path)
    assert env["MODEL_PRESET"] == "quality" and env["MODEL_REFINE"] == "openai/gpt-6-luna-pro"
    assert roles.model_for("refine") == "openai/gpt-6-luna-pro"
    assert roles.model_for("brief") == KIMI                     # rest still from the package
    reloaded = sd.SettingsView()
    assert reloaded.preset_combo.currentData() == presets.CUSTOM
    _pick(reloaded, "balanced")                                   # a package drops own picks
    assert reloaded.role_combos["refine"].currentText() == ""
    reloaded._save()
    assert settings.model_refine == "" and settings.model_preset == "balanced"
    assert roles.model_for("refine") == LUNA


def test_choosing_custom_fills_the_rows_with_what_they_use_now(dlg_env):
    sd, _path = dlg_env
    dlg = sd.SettingsView()
    _pick(dlg, "kimi")
    _pick(dlg, presets.CUSTOM)
    assert dlg.role_combos["refine"].currentText() == KIMI
    assert dlg.role_combos["draft"].currentText() == ROLES["draft"].default
    assert dlg.role_combos["vision"].currentText() == ""          # not part of a package
