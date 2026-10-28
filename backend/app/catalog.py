"""Model catalog updates (2026-10-29): newer / cheaper models without reinstalling vizsup.

The owner keeps ``catalog/models.json`` in the vizsup repository (public on GitHub). The app
fetches it with a plain GET (no key, nothing about the user is sent), checks it and caches it
in ``storage/_cache/model_catalog.json``. Role defaults and model packages then read from it:

    MODEL_<ROLE> (the user's own pick) → package (catalog, else presets.py)
                                       → role default (catalog, else roles.py)

A catalog is used only when it is well-formed, names known roles and packages only, and
every model id in it exists on OpenRouter's live model list — a typo or a retired model can
never break translation, and the built-in tables stay the fallback. It also names the latest
app version, so Settings can say "Có bản mới" (the user downloads the installer themselves).
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from pathlib import Path

from app import __version__
from app.config import settings
from app.fsutil import atomic_write_text

SCHEMA = 1
URL = "https://raw.githubusercontent.com/orms147/vizsup/main/catalog/models.json"
OPENROUTER_MODELS = "https://openrouter.ai/api/v1/models"     # public, no key
RELEASES = "https://github.com/orms147/vizsup/"                # the only allowed app link
CACHE = Path("_cache") / "model_catalog.json"
MAX_AGE = 24 * 3600.0           # check at most once a day
_ID = re.compile(r"^[a-z0-9][\w.-]*/[\w.:-]+$", re.I)
_VER = re.compile(r"^\d+(\.\d+){0,3}$")

_memo: tuple[tuple[str, float], dict] | None = None   # ((cache path, mtime), catalog)
_URL = re.compile(r"^https://github\.com/orms147/vizsup/[A-Za-z0-9._~/%-]*$")
# a catalog can only name models in this price range ($/M tokens): the dearest package
# model tonight is Kimi K3 at 20 out; OpenRouter lists models at 90–180 out
MAX_USD_IN, MAX_USD_OUT = 5.0, 25.0
IMAGE_ROLES = ("vision", "ocr")     # these send pictures: the model must take image input


def _get_json(url: str, timeout: float = 10.0):
    req = urllib.request.Request(url, headers={"User-Agent": f"vizsup/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:    # noqa: S310 (fixed https URLs)
        return json.loads(r.read().decode("utf-8"))


def cache_path() -> Path:
    return Path(settings.vizsup_storage_dir) / CACHE


def _ids(data: dict) -> set[str]:
    ids = set((data.get("role_defaults") or {}).values())
    for p in (data.get("presets") or {}).values():
        ids.update((p.get("overrides") or {}).values())
    return ids


def _usd_per_m(model: dict, kind: str) -> float | None:
    try:
        return float((model.get("pricing") or {}).get(kind)) * 1e6
    except (TypeError, ValueError):
        return None


def _role_pairs(data: dict) -> list[tuple[str, str]]:
    pairs = list((data.get("role_defaults") or {}).items())
    for p in (data.get("presets") or {}).values():
        pairs += list((p.get("overrides") or {}).items())
    return pairs


def validate(data, live: set[str] | dict | None) -> list[str]:
    """Problems with a downloaded catalog ([] = usable). ``live`` = OpenRouter's model ids,
    or {id: model record} to also check price and image input (None skips the live
    checks — tests, or reading back our own cache). Pure."""
    from app.providers.presets import PRESETS, TEXT_ROLES
    from app.providers.roles import ROLES

    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return [f"không phải danh mục model (schema {SCHEMA})"]
    bad: list[str] = []
    rd = data.get("role_defaults") or {}
    if not isinstance(rd, dict):
        return ["role_defaults phải là bảng"]
    for role, mid in rd.items():
        if role not in ROLES:
            bad.append(f"việc không có trong app: {role}")
        if not isinstance(mid, str) or not _ID.match(mid):
            bad.append(f"id model sai: {mid!r}")
    ps = data.get("presets") or {}
    if not isinstance(ps, dict):
        return bad + ["presets phải là bảng"]
    for key, p in ps.items():
        if key not in PRESETS or not isinstance(p, dict):
            bad.append(f"gói không có trong app: {key}")
            continue
        ov = p.get("overrides") or {}
        if not isinstance(ov, dict):
            bad.append(f"{key}.overrides phải là bảng")
            continue
        for role, mid in ov.items():
            if role not in TEXT_ROLES:
                bad.append(f"{key}: việc không thuộc gói: {role}")
            if not isinstance(mid, str) or not _ID.match(mid):
                bad.append(f"{key}: id model sai: {mid!r}")
        for f in ("usd_per_min", "usd_per_video"):
            if f in p and not (isinstance(p[f], (int, float)) and 0 <= p[f] < 1):
                bad.append(f"{key}.{f} không hợp lệ")
        for f in ("label", "hint"):
            # shown in QLabels that render HTML: a catalog may carry plain text only
            if f in p and not (isinstance(p[f], str) and len(p[f]) <= 400 and "<" not in p[f]):
                bad.append(f"{key}.{f} không hợp lệ")
    app = data.get("app") or {}
    if app:
        if not isinstance(app, dict) or not _VER.match(str(app.get("latest") or "")):
            bad.append("app.latest không hợp lệ")
        elif app.get("url") and not (_URL.match(str(app["url"]))
                                     and ".." not in str(app["url"]).split("/")):
            bad.append("app.url phải là trang vizsup trên GitHub")
    if live is not None and not bad:
        gone = sorted(i for i in _ids(data) if i not in live)
        bad += [f"OpenRouter không có model: {i}" for i in gone]
        if isinstance(live, dict):
            for role, mid in _role_pairs(data):
                m = live.get(mid)
                if not isinstance(m, dict):
                    continue
                pin, pout = _usd_per_m(m, "prompt"), _usd_per_m(m, "completion")
                if (pin is not None and pin > MAX_USD_IN) or (pout is not None and pout > MAX_USD_OUT):
                    bad.append(f"{mid}: giá ngoài mức cho phép ({pin}/{pout} $/M)")
                mods = (m.get("architecture") or {}).get("input_modalities") or []
                if role in IMAGE_ROLES and mods and "image" not in mods:
                    bad.append(f"{mid}: không nhận ảnh, không dùng được cho {role}")
    return sorted(set(bad), key=bad.index)


def current() -> dict:
    """The cached catalog ({} when there is none or it is unreadable)."""
    global _memo
    p = cache_path()
    try:
        stamp = (str(p), p.stat().st_mtime)      # the path too: another storage is another catalog
    except OSError:
        return {}
    if _memo and _memo[0] == stamp:
        return _memo[1]
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if validate(data, None):
        data = {}
    _memo = (stamp, data)
    return data


def role_default(role: str) -> str | None:
    return (current().get("role_defaults") or {}).get(role) or None


def preset_patch(key: str) -> dict:
    """The catalog's changes to one package: {overrides?, label?, hint?, usd_*?}."""
    return dict((current().get("presets") or {}).get(key) or {})


def newer(latest: str | None, have: str = __version__) -> bool:
    def parts(v: str) -> tuple[int, ...]:
        return tuple(int(x) for x in v.split("."))
    try:
        return bool(latest) and parts(str(latest)) > parts(have)
    except ValueError:
        return False


def due(now: float | None = None) -> bool:
    """A check is due: no cache yet, or the last successful one is older than a day.
    Never with VIZSUP_NO_UPDATE_CHECK=1 (the test suite; anyone who wants no checks)."""
    if os.environ.get("VIZSUP_NO_UPDATE_CHECK"):
        return False
    try:
        age = (now or time.time()) - cache_path().stat().st_mtime
    except OSError:
        return True
    return age > MAX_AGE


def _effective(data: dict) -> dict[str, str]:
    """"role" and "package/role" → model, under a catalog (for the change list)."""
    from app.providers.presets import PRESETS, TEXT_ROLES
    from app.providers.roles import ROLES

    rd = data.get("role_defaults") or {}
    out = {r: rd.get(r) or ROLES[r].default for r in ROLES}
    for key, p in PRESETS.items():
        ov = {**p.overrides, **((data.get("presets") or {}).get(key) or {}).get("overrides", {})}
        for r in TEXT_ROLES:
            out[f"{key}/{r}"] = ov.get(r) or out[r]
    return out


def refresh(*, get=_get_json) -> dict:
    """Download, check and cache the catalog. Returns {ok, changes [str], problems [str],
    latest, update (bool), url}. A bad catalog keeps the previous one. Never raises."""
    try:
        data = get(URL)
        live = {m.get("id"): m for m in (get(OPENROUTER_MODELS).get("data") or [])
                if isinstance(m, dict)}
    except Exception as exc:  # noqa: BLE001 — offline, GitHub/OpenRouter down, bad JSON
        return {"ok": False, "changes": [], "problems": [f"không tải được: {exc}"],
                "latest": None, "update": False, "url": ""}
    problems = validate(data, live)
    if problems:
        return {"ok": False, "changes": [], "problems": problems, "latest": None,
                "update": False, "url": ""}
    before, after = _effective(current()), _effective(data)
    changes = [f"{k}: {before[k]} → {after[k]}" for k in after if before.get(k) != after[k]]
    p = cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(p, json.dumps({**data, "_fetched": time.time()}, ensure_ascii=False,
                                    indent=1), encoding="utf-8")
    app = data.get("app") or {}
    return {"ok": True, "changes": changes, "problems": [], "latest": app.get("latest"),
            "update": newer(app.get("latest")), "url": app.get("url") or RELEASES + "releases"}


def summary() -> str:
    """One line for Settings: what the cached catalog is."""
    data = current()
    if not data:
        return "Đang dùng danh sách model có sẵn trong app."
    try:
        when = time.strftime("%d/%m/%Y", time.localtime(float(data.get("_fetched") or 0)))
    except (TypeError, ValueError, OverflowError, OSError):     # a hand-edited / old cache file
        when = "?"
    line = f"Danh sách model: bản {data.get('updated') or '?'} (kiểm tra {when})."
    latest = (data.get("app") or {}).get("latest")
    if newer(latest):
        line += f" Có vizsup {latest} mới (đang dùng {__version__})."
    return line
