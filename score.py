"""Scores pass 1 and pass 2 against the human answer key.

Usage:  python score.py
Writes results/score.json and prints the before/after table.
"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(errors="replace")
ROOT = Path(__file__).parent
FIELDS = ["primary_auth", "access", "mcp", "verdict"]
GREY = {"api_key", "token"}  # the rulebook's known blurry line


def load(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def agree(field, human, agent, lenient):
    if human == agent:
        return True
    return bool(lenient and field == "primary_auth" and {human, agent} <= GREY)


def tally(golden, records, lenient=False, skip_not_blind=False):
    per = {f: {"n": 0, "correct": 0} for f in FIELDS}
    for app_id, g in golden.items():
        if skip_not_blind and "not blind" in (g.get("note") or "").lower():
            continue
        rec = records.get(app_id)
        if not rec:
            continue
        for f in FIELDS:
            if g.get(f) in (None, "", "unknown"):
                continue  # the human could not decide, so nobody is marked on it
            per[f]["n"] += 1
            per[f]["correct"] += agree(f, g[f], rec.get(f), lenient)
    n = sum(v["n"] for v in per.values())
    c = sum(v["correct"] for v in per.values())
    return {"fields": per, "n": n, "correct": c, "accuracy": round(c / n, 4) if n else None}


def main():
    golden = load(ROOT / "golden" / "golden.json") or {}
    if not golden:
        print("No answer key yet (golden/golden.json). Run label.py first.")
        return
    passes = {}
    for p in ("pass1", "pass2"):
        passes[p] = {}
        for app_id in golden:
            d = load(ROOT / "results" / p / f"{app_id}.json")
            if d:
                passes[p][app_id] = d["record"]
                passes[p][app_id]["_trust"] = d.get("meta", {}).get("trust")

    out = {"apps_in_key": len(golden), "fields": FIELDS, "views": {}, "misses": [], "per_app": []}
    for view, kw in (("strict", {}), ("lenient_auth", {"lenient": True}),
                     ("strict_blind_only", {"skip_not_blind": True})):
        out["views"][view] = {p: tally(golden, passes[p], **kw) for p in passes}

    for app_id, g in sorted(golden.items(), key=lambda x: int(x[0])):
        row = {"id": int(app_id), "name": g.get("name"), "note": g.get("note", ""),
               "evidence_url": g.get("evidence_url", ""), "fields": {}}
        for f in FIELDS:
            h = g.get(f)
            a1 = passes["pass1"].get(app_id, {}).get(f)
            a2 = passes["pass2"].get(app_id, {}).get(f)
            row["fields"][f] = {"human": h, "pass1": a1, "pass2": a2}
            if h in (None, "", "unknown"):
                continue
            if h != a1 or (a2 is not None and h != a2):
                out["misses"].append({
                    "id": int(app_id), "name": g.get("name"), "field": f, "human": h,
                    "pass1": a1, "pass2": a2,
                    "pass1_ok": h == a1, "pass2_ok": (h == a2) if a2 is not None else None,
                    "grey_zone": f == "primary_auth" and {h, a1, a2} - {None} <= GREY,
                })
        row["trust"] = passes["pass2"].get(app_id, {}).get("_trust")
        out["per_app"].append(row)

    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "score.json").write_text(json.dumps(out, indent=1), encoding="utf-8")

    def pct(t):
        return "   n/a" if not t["n"] else f"{100 * t['correct'] / t['n']:5.1f}%"

    print(f"Answer key: {len(golden)} apps, hand-checked. Pass-2 records found: {len(passes['pass2'])}")
    for view, label in (("strict", "STRICT (exact match)"),
                        ("lenient_auth", "LENIENT (api_key and token count as the same)"),
                        ("strict_blind_only", "STRICT, excluding apps marked 'not blind'")):
        v = out["views"][view]
        print(f"\n{label}")
        print(f"  {'field':<14}{'n':>4}   pass1   pass2")
        for f in FIELDS:
            a, b = v["pass1"]["fields"][f], v["pass2"]["fields"][f]
            print(f"  {f:<14}{a['n']:>4}  {pct(a)}  {pct(b) if b['n'] else '   n/a'}")
        print(f"  {'ALL':<14}{v['pass1']['n']:>4}  {pct(v['pass1'])}  {pct(v['pass2']) if v['pass2']['n'] else '   n/a'}")

    print(f"\nDisagreements with the human key: {len(out['misses'])}")
    for m in out["misses"]:
        arrow = "fixed" if (not m["pass1_ok"] and m["pass2_ok"]) else \
                "broken" if (m["pass1_ok"] and m["pass2_ok"] is False) else "still off"
        print(f"  #{m['id']:<3} {m['name']:<20} {m['field']:<13} human={m['human']:<22} "
              f"pass1={m['pass1']} pass2={m['pass2']}  [{arrow}]")


if __name__ == "__main__":
    main()
