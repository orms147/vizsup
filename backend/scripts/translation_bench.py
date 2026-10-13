"""E-3: compare OpenRouter models on the app's OWN draft prompt (brief + glossary +
numbered lines) over real jobs, then let a judge model score them blind.

    python backend/scripts/translation_bench.py --jobs 2dc712c162d7 fea1d38fb4d7
    python backend/scripts/translation_bench.py --jobs <id> --models a/x b/y --lines 30

Writes ``storage/_bench/<stamp>/results.json`` + ``report.html`` (side by side, judge
scores, timing, leftover hanzi, lines over the dub budget). Read-only on the jobs.
Costs a few cents. Verify model ids against https://openrouter.ai/api/v1/models first.
"""
from __future__ import annotations

import argparse
import html
import json
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import settings  # noqa: E402
from app.models import Job  # noqa: E402
from app.pipeline.brief import load_brief  # noqa: E402
from app.pipeline.fit_estimate import MAX_TEMPO, SYL_PER_SEC  # noqa: E402
from app.pipeline.srt import parse_srt  # noqa: E402
from app.pipeline.translate import load_glossary  # noqa: E402
from app.providers.translation.openrouter import OpenRouter  # noqa: E402

CANDIDATES = [   # 2026-10-13 shortlist (see docs/plan/E-roadmap.md)
    "qwen/qwen3.7-plus",
    "deepseek/deepseek-v4-pro-0813",
    "deepseek/deepseek-v4.1-flash",
    "qwen/qwen3.8-flash",
]
JUDGE = "moonshotai/kimi-k3"     # strong, and not one of the default candidates
_CJK = re.compile(r"[一-鿿]")

JUDGE_PROMPT = (
    "You are a strict professional Chinese→Vietnamese subtitle editor. For each line "
    "you get the Chinese source and several anonymous Vietnamese translations (A, B, …). "
    "Score EACH translation 1-10: meaning accuracy first (wrong meaning ≤ 4), then "
    "natural spoken Vietnamese, correct and consistent xưng hô for the speakers, names, "
    "and brevity (subtitles are spoken in a short slot). Garbage OCR input: reward "
    "translations that stay sensible instead of copying noise. Reply ONLY with JSON: "
    '{"lines":[{"i":1,"scores":{"A":7,"B":9}}],"notes":{"A":"one short sentence"}}'
)


def load_job(job_id: str, n: int):
    d = ROOT / "storage" / job_id
    job = Job(id=d.name, work_dir=d)
    meta = (json.loads((d / "metadata.json").read_text(encoding="utf-8"))
            if (d / "metadata.json").exists() else {})
    cues = parse_srt(d / "cn.srt")[:n]
    ctx = {"title": meta.get("title"), "platform": meta.get("platform")}
    b = load_brief(job)
    if b:
        ctx["_brief"] = b
    return {"texts": [c.text for c in cues], "durs": [c.end - c.start for c in cues],
            "ctx": ctx, "glossary": load_glossary(job)}


def translate(model: str, job: dict) -> dict:
    class Pinned(OpenRouter):
        def model_for(self, role: str) -> str:   # noqa: ARG002 - pin every role
            return model

    t0 = time.time()
    try:
        out = Pinned().translate_texts(job["texts"], context=job["ctx"], glossary=job["glossary"])
        err = None
    except Exception as exc:  # noqa: BLE001
        out, err = None, f"{type(exc).__name__}: {exc}"[:300]
    return {"out": out, "err": err, "sec": round(time.time() - t0, 1)}


def judge(job: dict, outs: dict[str, list[str]], judge_model: str, seed: int) -> dict:
    """Blind scores: models are shuffled behind letters; batches of 20 lines."""
    models = [m for m, o in outs.items() if o]
    letters = [chr(65 + k) for k in range(len(models))]
    rnd = random.Random(seed)
    order = models[:]
    rnd.shuffle(order)
    who = dict(zip(letters, order))
    scores = {m: [] for m in models}
    notes: dict[str, str] = {}

    class Judge(OpenRouter):
        def model_for(self, role: str) -> str:   # noqa: ARG002
            return judge_model

    prov = Judge()
    for b0 in range(0, len(job["texts"]), 20):
        lines = []
        for i in range(b0, min(b0 + 20, len(job["texts"]))):
            cands = "\n".join(f"  {L}: {outs[who[L]][i]}" for L in letters)
            lines.append(f"{i + 1}. ZH: {job['texts'][i]}\n{cands}")
        resp = prov._chat(model=judge_model, temperature=0, max_tokens=4000,
                          response_format={"type": "json_object"},
                          messages=[{"role": "system", "content": JUDGE_PROMPT},
                                    {"role": "user", "content": "\n\n".join(lines)}])
        try:
            data = json.loads(resp.choices[0].message.content or "{}")
        except ValueError:
            continue
        for row in data.get("lines", []):
            for L, s in (row.get("scores") or {}).items():
                if L in who and isinstance(s, (int, float)):
                    scores[who[L]].append(float(s))
        for L, note in (data.get("notes") or {}).items():
            if L in who:
                notes[who[L]] = (notes.get(who[L], "") + " " + str(note)).strip()
    return {m: {"avg": round(sum(v) / len(v), 2) if v else None, "n": len(v),
                "note": notes.get(m, "")} for m, v in scores.items()}


def metrics(out: list[str] | None, durs: list[float]) -> dict:
    if not out:
        return {}
    syl = [len(x.split()) for x in out]
    return {"cjk_left": sum(len(_CJK.findall(x)) for x in out),
            "empty": sum(1 for x in out if not x.strip()),
            "over_budget": sum(1 for s, t in zip(syl, durs) if t > 0 and s > t * MAX_TEMPO * SYL_PER_SEC),
            "syl_per_line": round(sum(syl) / max(1, len(syl)), 1)}


def report(path: Path, res: dict) -> None:
    e = html.escape
    parts = ["<!doctype html><meta charset=utf-8><title>Translation bench</title><style>"
             "body{font:14px system-ui;margin:24px;background:#101116;color:#eee}"
             "table{border-collapse:collapse;margin:12px 0}td,th{border:1px solid #333;"
             "padding:4px 8px;vertical-align:top}th{background:#1e1f28}.zh{color:#aaa}"
             ".s{color:#bb99ff;font-weight:600}</style><h1>Translation bench</h1>"]
    for jid, j in res["jobs"].items():
        parts.append(f"<h2>{e(jid)}</h2><table><tr><th>model</th><th>judge avg</th>"
                     "<th>sec</th><th>hanzi left</th><th>over budget</th><th>syl/line</th>"
                     "<th>judge note</th></tr>")
        for m in res["models"]:
            r, sc = j["runs"][m], j["judge"].get(m, {})
            mt = r.get("metrics", {})
            parts.append(f"<tr><td>{e(m)}</td><td class=s>{sc.get('avg')}</td><td>{r['sec']}</td>"
                         f"<td>{mt.get('cjk_left')}</td><td>{mt.get('over_budget')}</td>"
                         f"<td>{mt.get('syl_per_line')}</td><td>{e(sc.get('note', ''))}</td></tr>")
        parts.append("</table><table><tr><th>#</th><th>中文</th>"
                     + "".join(f"<th>{e(m.split('/')[-1])}</th>" for m in res["models"]) + "</tr>")
        for i, zh in enumerate(j["texts"]):
            cells = "".join(f"<td>{e((j['runs'][m]['out'] or [''] * (i + 1))[i])}</td>"
                            for m in res["models"])
            parts.append(f"<tr><td>{i + 1}</td><td class=zh>{e(zh)}</td>{cells}</tr>")
        parts.append("</table>")
    path.write_text("".join(parts), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--jobs", nargs="+", required=True, help="job ids under storage/")
    ap.add_argument("--models", nargs="+", default=CANDIDATES)
    ap.add_argument("--judge", default=JUDGE)
    ap.add_argument("--lines", type=int, default=40)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    if not settings.openrouter_api_key:
        print("OPENROUTER_API_KEY missing", file=sys.stderr)
        return 2
    settings.openrouter_promo_model = ""
    jobs = {jid: load_job(jid, a.lines) for jid in a.jobs}
    tasks = [(m, jid) for jid in jobs for m in a.models]
    with ThreadPoolExecutor(4) as ex:
        runs = list(ex.map(lambda t: (t, translate(t[0], jobs[t[1]])), tasks))
    res = {"models": a.models, "judge": a.judge, "jobs": {}}
    for jid, job in jobs.items():
        jr = {(m): r for (m, j), r in runs if j == jid}
        for m, r in jr.items():
            r["metrics"] = metrics(r["out"], job["durs"])
            print(f"{m:34s} {jid}  {'ok' if r['out'] else 'FAIL'}  {r['sec']}s {r['err'] or ''}")
        scores = judge(job, {m: r["out"] for m, r in jr.items()}, a.judge, a.seed)
        res["jobs"][jid] = {"texts": job["texts"], "runs": jr, "judge": scores}
        for m, s in sorted(scores.items(), key=lambda kv: -(kv[1]["avg"] or 0)):
            print(f"  judge {s['avg']}  ({s['n']} lines)  {m}")
    out = ROOT / "storage" / "_bench" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    report(out / "report.html", res)
    print(f"report: {out / 'report.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
