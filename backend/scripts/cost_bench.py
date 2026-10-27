"""Cost pass (2026-10-27): price and blind-judge whole ROLE SETUPS (draft + refine + shorten
models) through the app's REAL ``translate_cues`` — same prompts, brief, budgets and
pre-gate auto-shorten users get — on the first N lines of a real job.

    python backend/scripts/cost_bench.py run   --job 703e11aadf05 --lines 120
    python backend/scripts/cost_bench.py judge --out storage/_bench/cost-<stamp> \
        --judges qwen/qwen3.7-plus z-ai/glm-5.3 google/gemini-3.8-flash
    python backend/scripts/cost_bench.py report --out storage/_bench/cost-<stamp>

``run`` drafts once with the draft model and reuses that draft (through the real
checkpoint) for every refiner, so refiners are compared on the same input; the job's own
``vi.mt.srt`` joins as system "K" (what shipped). Cost comes from OpenRouter's
``usage.cost`` (app.usage), never list prices. ``judge`` shuffles the systems behind
letters on EVERY line and scores accuracy / naturalness / length-fit 1-10; use judges
that are not candidates. Read-only on the job. ~$0.01 per setup + ~$0.02-0.04 per judge
for 120 lines. Results of 2026-10-27: docs/plan/G-cost-log.md.
"""
from __future__ import annotations

import argparse
import itertools
import json
import random
import re
import shutil
import statistics as st
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import usage  # noqa: E402
from app.config import settings  # noqa: E402
from app.pipeline.fit_estimate import MAX_TEMPO, SYL_PER_SEC, slot_seconds  # noqa: E402
from app.pipeline.srt import parse_srt, write_srt  # noqa: E402
from app.pipeline.translate import dedupe_adjacent, translate_cues  # noqa: E402
from app.providers.translation.openrouter import OpenRouter  # noqa: E402

DS = "deepseek/deepseek-v4.1-flash"
LUNA = "openai/gpt-6-luna"
DS17 = "deepseek/deepseek-v4.1-flash-1017"
# name -> (draft, refine or "", shorten); "K" = the job's shipped vi.mt.srt
SETUPS = {"F": (DS, "", DS), "L": (DS, LUNA, LUNA), "D": (DS, DS17, DS17),
          "LO": (LUNA, "", LUNA), "LL": (LUNA, LUNA, LUNA)}

JUDGE_PROMPT = (
    "You are a strict professional Chinese→Vietnamese subtitle editor for a DUBBED video.\n"
    "{brief}The lines are consecutive subtitle lines; one sentence may be split over several\n"
    "lines, and a translation may move a few words to the neighbouring line — judge the\n"
    "meaning in context, not word by word.\n"
    "For each numbered line you get the Chinese source, a syllable BUDGET (what the Vietnamese\n"
    "voice can say in that time slot at most), and several anonymous Vietnamese translations\n"
    "(letters are shuffled on every line). Score EACH translation with three integers 1-10:\n"
    "  a = accuracy: meaning vs the Chinese (wrong / missing / added meaning → ≤4; stray\n"
    "      symbols or Chinese characters in the output → ≤3)\n"
    "  n = naturalness: everyday spoken Vietnamese a creator would say; correct xưng hô\n"
    "  l = length fit: 10 = within the budget and complete; lower for every syllable over\n"
    "      budget; padding/filler also costs points\n"
    "Reply ONLY with JSON, no prose, exactly like:\n"
    '{{"lines":[{{"i":1,"A":[9,8,10],"B":[7,7,6]}}],"notes":"<=3 short sentences on the '
    'biggest differences, citing line numbers and letters"}}'
)


def _cues(job_dir: Path, n: int):
    cues = dedupe_adjacent(parse_srt(job_dir / "cn.srt")[:n])
    brief = json.loads((job_dir / "context_brief.json").read_text(encoding="utf-8"))["brief"]
    meta = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    ctx = {"title": meta.get("title"), "platform": meta.get("platform"), "_brief": brief}
    return cues, ctx


def _cost(d: Path) -> float:
    u = json.loads((d / "usage.json").read_text(encoding="utf-8"))
    return sum(m["cost"] for m in u.get("models", {}).values())


def cmd_run(a) -> None:
    job_dir = ROOT / "storage" / a.job
    out = ROOT / "storage" / "_bench" / time.strftime("cost-%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    cues, ctx = _cues(job_dir, a.lines)
    settings.openrouter_promo_model = ""
    for attr in ("model_draft", "model_refine", "model_shorten"):
        setattr(settings, attr, "")
    drafts: dict[str, tuple[Path, float]] = {}
    systems = {"K": [c.text for c in parse_srt(job_dir / "vi.mt.srt")[:len(cues)]]}
    meta = {}
    for name, (dm, rm, sm) in SETUPS.items():
        d = out / name
        d.mkdir()
        settings.model_draft, settings.model_refine, settings.model_shorten = dm, rm, sm
        reuse = drafts.get(dm) if rm else None
        if reuse is None and rm:          # draft once per draft model, then reuse it
            dd = out / f"_draft_{dm.split('/')[-1]}"
            dd.mkdir()
            t0 = time.time()
            with usage.track(dd, phase="draft"):
                translate_cues(cues, draft="openrouter", context=ctx, work_dir=dd,
                               auto_shorten=False)
            reuse = drafts[dm] = (dd, time.time() - t0)
        if reuse:
            shutil.copy2(reuse[0] / "translate.checkpoint.json", d)
        t0 = time.time()
        with usage.track(d, phase="translate"):
            res = translate_cues(cues, draft="openrouter", refine="openrouter" if rm else None,
                                 context=ctx, work_dir=d, auto_shorten=True)
        sec = time.time() - t0 + (reuse[1] if reuse else 0.0)
        cost = _cost(d) + (_cost(reuse[0]) if reuse else 0.0)
        write_srt(res, d / "vi.srt")
        systems[name] = [c.text for c in res]
        meta[name] = {"draft": dm, "refine": rm, "shorten": sm, "sec": round(sec, 1),
                      "cost": round(cost, 6)}
        print(f"{name:3s} ${cost:.5f} {sec:5.1f}s")
    rows = [{"i": i + 1, "zh": c.text, "slot": round(slot_seconds(cues, i), 2),
             **{s: v[i] for s, v in systems.items()}} for i, c in enumerate(cues)]
    (out / "lines.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), "utf-8")
    (out / "runs.json").write_text(json.dumps(meta, indent=1), "utf-8")
    (out / "brief.txt").write_text(ctx["_brief"], "utf-8")
    print(f"out: {out}")


def _budget(slot: float) -> int:
    return max(2, int(slot * MAX_TEMPO * SYL_PER_SEC))


def cmd_judge(a) -> None:
    out = Path(a.out)
    rows = json.loads((out / "lines.json").read_text(encoding="utf-8"))
    systems = [k for k in rows[0] if k not in ("i", "zh", "slot")]
    brief = (out / "brief.txt").read_text(encoding="utf-8") if (out / "brief.txt").exists() else ""
    prompt = JUDGE_PROMPT.format(brief=f"Video brief (xưng hô, tone, terms):\n{brief}\n" if brief else "")
    prov = OpenRouter()
    for model in a.judges:
        rnd = random.Random(a.seed)
        perm = {}
        for r in rows:
            order = systems[:]
            rnd.shuffle(order)
            perm[r["i"]] = dict(zip((chr(65 + k) for k in range(len(order))), order))
        jd = out / f"judge_{model.replace('/', '_')}"
        jd.mkdir(exist_ok=True)
        (jd / "usage.json").unlink(missing_ok=True)
        per_line, notes = {}, []
        with usage.track(jd, phase="judge"):
            for b0 in range(0, len(rows), a.batch):
                body = [f"{r['i']}. ZH: {r['zh']}  (budget {_budget(r['slot'])} syllables)\n"
                        + "\n".join(f"  {L}: {r[s]}" for L, s in perm[r["i"]].items())
                        for r in rows[b0:b0 + a.batch]]
                resp = prov._chat(model=model, temperature=0, max_tokens=4000,
                                  messages=[{"role": "system", "content": prompt},
                                            {"role": "user", "content": "\n\n".join(body)}])
                text = re.sub(r"^```(?:json)?\s*|\s*```$", "", resp.choices[0].message.content or "")
                m = re.search(r"\{.*\}", text, re.S)
                try:
                    data = json.loads(m.group(0) if m else text)
                except ValueError:
                    continue
                notes.append(str(data.get("notes", "")))
                for row in data.get("lines", []):
                    for L, s in perm.get(row.get("i"), {}).items():
                        v = row.get(L)
                        if isinstance(v, list) and len(v) == 3:
                            per_line.setdefault(str(row["i"]), {})[s] = v
        (jd / "result.json").write_text(json.dumps(
            {"model": model, "per_line": per_line, "notes": notes, "cost": _cost(jd)},
            ensure_ascii=False, indent=1), "utf-8")
        print(f"judge {model}: ${_cost(jd):.4f}")


def cmd_report(a) -> None:
    out = Path(a.out)
    rows = json.loads((out / "lines.json").read_text(encoding="utf-8"))
    systems = [k for k in rows[0] if k not in ("i", "zh", "slot")]
    judges = {p.name[6:]: json.loads((p / "result.json").read_text(encoding="utf-8"))
              for p in sorted(out.glob("judge_*")) if (p / "result.json").exists()}

    def avg(j, s, k):
        v = [pl[s][k] for pl in j["per_line"].values() if s in pl]
        return sum(v) / len(v) if v else float("nan")

    print("system  " + "  ".join(f"{n[:14]:>14s}" for n in judges) + "   acc   nat   len  mean  over")
    for s in systems:
        means = [sum(avg(j, s, k) for k in range(3)) / 3 for j in judges.values()]
        crit = [st.mean(avg(j, s, k) for j in judges.values()) for k in range(3)]
        over = sum(1 for r in rows if len(r[s].split()) > _budget(r["slot"]))
        print(f"{s:6s}  " + "  ".join(f"{m:14.2f}" for m in means)
              + f"  {crit[0]:.2f}  {crit[1]:.2f}  {crit[2]:.2f}  {st.mean(crit):.2f}  {over:4d}")
    for (n1, j1), (n2, j2) in itertools.combinations(judges.items(), 2):
        xs, ys = [], []
        for i, pl in j1["per_line"].items():
            for s, v in pl.items():
                if s in j2["per_line"].get(i, {}):
                    xs.append(sum(v))
                    ys.append(sum(j2["per_line"][i][s]))
        r = st.correlation(xs, ys) if len(xs) > 2 else float("nan")
        print(f"agreement {n1} vs {n2}: per-line Pearson {r:.2f} (n={len(xs)})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--job", required=True)
    r.add_argument("--lines", type=int, default=120)
    j = sub.add_parser("judge")
    j.add_argument("--out", required=True)
    j.add_argument("--judges", nargs="+", required=True)
    j.add_argument("--batch", type=int, default=20)
    j.add_argument("--seed", type=int, default=11)
    p = sub.add_parser("report")
    p.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.cmd != "report" and not settings.openrouter_api_key:
        print("OPENROUTER_API_KEY missing", file=sys.stderr)
        return 2
    {"run": cmd_run, "judge": cmd_judge, "report": cmd_report}[a.cmd](a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
