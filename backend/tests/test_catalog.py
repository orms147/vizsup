"""Model catalog updates (app/catalog.py): a downloaded catalog changes role defaults and
packages only when it is valid and every model is live on OpenRouter; the user's own picks
always win; a bad or unreachable catalog keeps the previous one. No network here."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import catalog  # noqa: E402
from app.config import settings  # noqa: E402
from app.providers import presets, roles  # noqa: E402

REPO_CATALOG = Path(__file__).resolve().parents[2] / "catalog" / "models.json"
NEW = "openai/gpt-7-mini"


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    monkeypatch.setattr(settings, "model_preset", "")
    for r in roles.ROLES.values():
        monkeypatch.setattr(settings, r.attr, "")
    monkeypatch.setattr(catalog, "_memo", None)
    monkeypatch.delenv("VIZSUP_NO_UPDATE_CHECK", raising=False)
    return tmp_path


def _cat(**kw):
    return {"schema": 1, "updated": "2026-11-01", "role_defaults": {}, "presets": {}, **kw}


def _getter(cat, live):
    def get(url):
        if url == catalog.URL:
            if isinstance(cat, Exception):
                raise cat
            return cat
        assert url == catalog.OPENROUTER_MODELS
        return {"data": [{"id": i} for i in live]}
    return get


def test_the_repo_catalog_is_valid_and_matches_the_built_in_tables():
    data = json.loads(REPO_CATALOG.read_text(encoding="utf-8"))
    assert catalog.validate(data, None) == []
    for role, mid in data["role_defaults"].items():
        assert roles.ROLES[role].default == mid       # publishing it changes nothing


def test_validate_rejects_unknowns_and_dead_models():
    assert catalog.validate({"schema": 2}, None)
    assert catalog.validate(_cat(role_defaults={"dance": NEW}), None)
    assert catalog.validate(_cat(role_defaults={"draft": "not an id"}), None)
    assert catalog.validate(_cat(presets={"mystery": {}}), None)
    assert catalog.validate(_cat(presets={"economy": {"overrides": {"vision": NEW}}}), None)
    assert catalog.validate(_cat(app={"latest": "1.0", "url": "https://evil.example/x"}), None)
    ok = _cat(role_defaults={"draft": NEW})
    assert catalog.validate(ok, {NEW}) == []
    assert catalog.validate(ok, {"other/model"}) == [f"OpenRouter không có model: {NEW}"]


def test_refresh_applies_a_valid_catalog(storage):
    live = {NEW, *presets.known_models()}
    res = catalog.refresh(get=_getter(_cat(role_defaults={"refine": NEW},
                                           presets={"economy": {"overrides": {"draft": NEW},
                                                                "usd_per_min": 0.001}},
                                           app={"latest": "9.0.0"}), live))
    assert res["ok"] and res["update"] and res["latest"] == "9.0.0"
    assert f"refine: openai/gpt-6-luna → {NEW}" in res["changes"]
    assert roles.model_for("refine") == NEW                 # the default package follows
    assert presets.models("economy")["draft"] == NEW
    assert presets.effective("economy").usd_per_min == 0.001
    assert presets.models("economy")["refine"] == presets.LUNA   # the package's own pick wins
    assert NEW in presets.known_models()
    assert "Có vizsup 9.0.0 mới" in catalog.summary()
    assert not catalog.due()


def test_the_users_own_pick_wins(storage, monkeypatch):
    catalog.refresh(get=_getter(_cat(role_defaults={"refine": NEW}), {NEW}))
    monkeypatch.setattr(settings, "model_refine", "anthropic/claude-x")
    assert roles.model_for("refine") == "anthropic/claude-x"


def test_a_bad_or_unreachable_catalog_keeps_the_previous_one(storage):
    assert catalog.refresh(get=_getter(_cat(role_defaults={"refine": NEW}), {NEW}))["ok"]
    res = catalog.refresh(get=_getter(_cat(role_defaults={"refine": "x/retired"}), {NEW}))
    assert not res["ok"] and "x/retired" in res["problems"][0]
    assert roles.model_for("refine") == NEW
    res = catalog.refresh(get=_getter(OSError("offline"), {NEW}))
    assert not res["ok"] and "offline" in res["problems"][0]
    assert roles.model_for("refine") == NEW


def test_without_a_catalog_nothing_changes(storage):
    assert catalog.current() == {} and catalog.due()
    assert roles.model_for("refine") == roles.ROLES["refine"].default
    assert presets.effective("economy") is presets.PRESETS["economy"]
    assert "có sẵn trong app" in catalog.summary()


def test_newer():
    assert catalog.newer("0.2.0", "0.1.0") and not catalog.newer("0.1.0", "0.1.0")
    assert not catalog.newer(None) and not catalog.newer("abc")


def test_settings_card_checks_and_relabels(storage, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication([])
    from desktop.settings_dialog import SettingsView

    page = SettingsView()
    res = catalog.refresh(get=_getter(_cat(presets={"economy": {"label": "Tiết kiệm 2"}}), set()))
    page._catalog_done(res)
    i = page.preset_combo.findData("economy")
    assert page.preset_combo.itemText(i).startswith("Tiết kiệm 2")
    assert "Không có gì mới" in page.catalog_status.text()
    page._catalog_done({"ok": False, "problems": ["không tải được: offline"]})
    assert "vẫn dùng danh sách cũ" in page.catalog_status.text()


def test_never_due_when_switched_off(storage, monkeypatch):
    monkeypatch.setenv("VIZSUP_NO_UPDATE_CHECK", "1")
    assert not catalog.due()


def _m(mid, pin, pout, mods=("text",)):
    return {"id": mid, "pricing": {"prompt": str(pin / 1e6), "completion": str(pout / 1e6)},
            "architecture": {"input_modalities": list(mods)}}


def test_live_records_cap_price_and_need_images_for_vision():
    live = {NEW: _m(NEW, 0.1, 0.5), "x/dear": _m("x/dear", 1, 180),
            "x/textonly": _m("x/textonly", 0.1, 0.4), "x/eyes": _m("x/eyes", 0.1, 0.4, ("text", "image"))}
    assert catalog.validate(_cat(role_defaults={"refine": NEW}), live) == []
    bad = catalog.validate(_cat(role_defaults={"refine": "x/dear"}), live)
    assert bad and "giá ngoài mức" in bad[0]
    assert "không nhận ảnh" in catalog.validate(_cat(role_defaults={"ocr": "x/textonly"}), live)[0]
    assert catalog.validate(_cat(role_defaults={"ocr": "x/eyes"}), live) == []
    assert catalog.validate(_cat(presets={"economy": {"overrides": {"draft": "x/dear"}}}), live)


def test_refresh_passes_the_records(storage):
    live = [NEW, *presets.known_models()]

    def get(url):
        if url == catalog.URL:
            return _cat(role_defaults={"refine": NEW})
        return {"data": [_m(i, 0.1, 30 if i == NEW else 0.5) for i in live]}
    res = catalog.refresh(get=get)
    assert not res["ok"] and "giá ngoài mức" in res["problems"][0]
