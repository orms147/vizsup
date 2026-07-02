"""N-20: settings must not depend on the launch directory. Before this, .env and
./storage resolved against the CWD — launching from backend/ silently lost every
API key, and job storage on the dev machine had already split into two dirs.

No importlib.reload here: it would swap app.config.settings out from under other
tests. A fresh Settings() instance under a foreign cwd proves the same thing.
"""
from __future__ import annotations

from pathlib import Path

from app import config


def test_storage_dir_anchored_to_repo_root(tmp_path, monkeypatch):
    monkeypatch.delenv("VIZSUP_STORAGE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)              # a directory with no .env, no storage
    d = Path(config.Settings().vizsup_storage_dir)
    assert d.is_absolute()
    assert d == config._ROOT / "storage"     # cwd played no part


def test_root_is_the_repo_not_backend():
    assert config._ROOT.name != "backend"
    assert (config._ROOT / "backend" / "app" / "config.py").exists()
    assert (config._ROOT / "CLAUDE.md").exists()


def test_env_file_anchored_to_repo_root():
    env_file = config.Settings.model_config.get("env_file")
    assert env_file is not None
    assert Path(env_file).is_absolute()
    assert Path(env_file) == config._ROOT / ".env"
