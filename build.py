"""Builds the case-study page: docs/index.html and docs/data.json.

Reads apps.json, results/pass1, results/pass2, golden/golden.json, results/score.json
and (optional) site.json. Every number on the page is computed here from those files.
Usage:  python build.py
"""
import html
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from schema import load_apps

sys.stdout.reconfigure(errors="replace")
ROOT = Path(__file__).parent
DOCS = ROOT / "docs"


def load(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def e(x):
    return html.escape(str(x if x is not None else ""))


def nice(x):
    return e(str(x or "unknown").replace("_", " "))


def pct(a, b):
    return "n/a" if not b else f"{100 * a / b:.0f}%"


def collect():
    rows = []
    for app in load_apps():
        p1 = load(ROOT / "results" / "pass1" / f"{app['id']}.json")
        p2 = load(ROOT / "results" / "pass2" / f"{app['id']}.json")
        if not p1 and not p2:
            continue
        final = (p2 or p1)["record"]
        meta2 = (p2 or {}).get("meta", {})
        rows.append({
            "id": app["id"], "name": app["name"], "category": app["category"], "hint": app["hint"],
            "record": final,
            "pass1": p1["record"] if p1 else None,
            "verified_pass": bool(p2),
            "trust": meta2.get("trust", "not_verified"),
            "re_researched": meta2.get("re_researched", False),
            "changed_fields": meta2.get("changed_fields", []),
            "flags_before": meta2.get("flags_before", []),
            "flags_after": meta2.get("flags_after", []),
            "skeptic": meta2.get("skeptic"),
            "evidence_checks": meta2.get("evidence_checks", []),
        })
    return rows


def patterns(rows):
    n = len(rows)
    rec = [r["record"] for r in rows]
    auth = Counter(x["primary_auth"] for x in rec)
    access = Counter(x["access"] for x in rec)
    mcp = Counter(x["mcp"] for x in rec)
    verdict = Counter(x["verdict"] for x in rec)
    blockers = Counter(x["main_blocker"] for x in rec if x["main_blocker"] not in ("none", None))
    simple = {"api_key", "token", "basic"}
    dual = sum(1 for x in rec if "oauth2" in x["auth_methods"] and simple & set(x["auth_methods"]))
    self_serve = access["self_serve_free"] + access["self_serve_trial"]
    gated = access["partner_or_sales_gated"]
    cats = []
    for r in rows:
        if r["category"] not in cats:
            cats.append(r["category"])
    matrix = []
    for c in cats:
        sub = [r["record"] for r in rows if r["category"] == c]
        a = Counter(x["access"] for x in sub)
        matrix.append({
            "category": c, "n": len(sub),
            "self_serve": a["self_serve_free"] + a["self_serve_trial"],
            "paid_plan": a["paid_plan"], "approval": a["approval_required"],
            "gated": a["partner_or_sales_gated"],
            "other": a["not_applicable"] + a["unknown"],
            "oauth2": sum(1 for x in sub if x["primary_auth"] == "oauth2"),
            "mcp_official": sum(1 for x in sub if x["mcp"] == "official"),
            "yes": sum(1 for x in sub if x["verdict"] == "yes"),
        })
    order = {"verified": 0, "partly_verified": 1, "needs_human": 2, "not_verified": 3}
    easy = sorted([r for r in rows if r["record"]["verdict"] == "yes"
                   and r["record"]["access"] in ("self_serve_free", "self_serve_trial")],
                  key=lambda r: (order.get(r["trust"], 9), r["id"]))
    outreach = [r for r in rows if r["record"]["access"] in ("partner_or_sales_gated", "approval_required")
                or r["record"]["verdict"] == "no"]
    trust = Counter(r["trust"] for r in rows)
    return {
        "n": n, "auth": dict(auth.most_common()), "access": dict(access.most_common()),
        "mcp": dict(mcp.most_common()), "verdict": dict(verdict.most_common()),
        "blockers": dict(blockers.most_common()), "dual_auth": dual,
        "self_serve": self_serve, "gated": gated, "matrix": matrix,
        "easy_wins": [{"id": r["id"], "name": r["name"], "trust": r["trust"]} for r in easy],
        "outreach": [{"id": r["id"], "name": r["name"], "access": r["record"]["access"],
                      "blocker": r["record"]["main_blocker"]} for r in outreach],
        "trust": dict(trust),
        "verified_apps": sum(1 for r in rows if r["verified_pass"]),
        "re_researched": sum(1 for r in rows if r["re_researched"]),
        "apps_changed": sum(1 for r in rows if r["changed_fields"]),
        "flags_before": sum(len(r["flags_before"]) for r in rows),
        "flags_after": sum(len(r["flags_after"]) for r in rows),
        "changed_field_counts": dict(Counter(f for r in rows for f in r["changed_fields"]).most_common()),
        "pass1_confidence": dict(Counter((r["pass1"] or {}).get("confidence") for r in rows if r["pass1"])),
        "skeptic_flagged_apps": sum(1 for r in rows if r["skeptic"] and
                                    any(v["judgement"] != "supported" for v in r["skeptic"].values())),
        "skeptic_ran": sum(1 for r in rows if r["skeptic"]),
    }


BADGE = {
    "self_serve_free": "good", "self_serve_trial": "good", "paid_plan": "warn",
    "approval_required": "warn", "partner_or_sales_gated": "bad", "not_applicable": "mute",
    "yes": "good", "yes_with_caveats": "warn", "no": "bad",
    "verified": "good", "partly_verified": "warn", "needs_human": "bad", "not_verified": "mute",
    "official": "good", "community": "warn", "none_found": "mute", "unknown": "mute",
}


def badge(value):
    return f'<span class="b {BADGE.get(value, "mute")}">{nice(value)}</span>'


def table_rows(rows):
    out = []
    for r in rows:
        x = r["record"]
        others = [a for a in x["auth_methods"] if a != x["primary_auth"]]
        ev = "".join(
            f'<li><b>{e(v["field"])}</b>: &ldquo;{e(v["quote"])}&rdquo; '
            f'<a href="{e(v["url"])}" rel="noopener">{e(v["url"][:70])}</a></li>'
            for v in x.get("evidence", [])[:6])
        flags = "".join(f"<li>{e(f['detail'])}</li>" for f in r["flags_after"])
        sk = ""
        if r["skeptic"]:
            sk = "".join(f"<li><b>{e(k)}</b>: {e(v['judgement'])}. {e(v['why'])}</li>"
                         for k, v in r["skeptic"].items() if v["judgement"] != "supported")
        detail = (f"<p>{e(x.get('access_note'))}</p><p>{e(x.get('api_note'))}</p>"
                  + (f"<p><b>Blocker:</b> {e(x.get('blocker_note'))}</p>" if x.get("blocker_note") else "")
                  + (f"<p><b>Unknowns:</b> {e(x.get('unknowns'))}</p>" if x.get("unknowns") else "")
                  + (f"<p><b>Evidence</b></p><ul>{ev}</ul>" if ev else "")
                  + (f"<p><b>Open audit flags</b></p><ul>{flags}</ul>" if flags else "")
                  + (f"<p><b>Second-model doubts</b></p><ul>{sk}</ul>" if sk else "")
                  + (f"<p><b>Changed by verification:</b> {e(', '.join(r['changed_fields']))}</p>"
                     if r["changed_fields"] else ""))
        first = x.get("evidence", [{}])[0].get("url", "") if x.get("evidence") else ""
        out.append(
            f'<tr data-cat="{e(r["category"])}" data-access="{e(x["access"])}" '
            f'data-verdict="{e(x["verdict"])}" data-trust="{e(r["trust"])}">'
            f'<td class="num">{r["id"]}</td>'
            f'<td><b>{e(r["name"])}</b><div class="sub">{e(x["one_liner"])}</div>'
            f'<details><summary>evidence and notes</summary>{detail}</details></td>'
            f'<td class="sub">{e(r["category"])}</td>'
            f'<td>{nice(x["primary_auth"])}<div class="sub">{e(", ".join(o.replace("_", " ") for o in others))}</div></td>'
            f'<td>{badge(x["access"])}</td>'
            f'<td>{e(", ".join(x["api_styles"]))}<div class="sub">{nice(x["api_breadth"])}</div></td>'
            f'<td>{badge(x["mcp"])}</td>'
            f'<td>{badge(x["verdict"])}<div class="sub">{"" if x["main_blocker"] in ("none", None) else nice(x["main_blocker"])}</div></td>'
            f'<td>{badge(r["trust"])}</td>'
            f'<td>{f"""<a href="{e(first)}" rel="noopener">source</a>""" if first else ""}</td></tr>')
    return "\n".join(out)


def score_block(score):
    if not score:
        return "<p>No answer key found yet. Run <code>python label.py</code> then <code>python score.py</code>.</p>", ""
    labels = {"strict": "Strict: exact match",
              "lenient_auth": "Lenient: api key and token counted as the same",
              "strict_blind_only": "Strict, excluding apps the labeller had already seen in detail"}
    parts = []
    for key, label in labels.items():
        v = score["views"][key]
        body = ""
        for f in score["fields"]:
            a, b = v["pass1"]["fields"][f], v["pass2"]["fields"][f]
            body += (f"<tr><td>{nice(f)}</td><td class='num'>{a['n']}</td>"
                     f"<td class='num'>{a['correct']} ({pct(a['correct'], a['n'])})</td>"
                     f"<td class='num'>{b['correct']} ({pct(b['correct'], b['n'])})</td></tr>")
        a, b = v["pass1"], v["pass2"]
        body += (f"<tr class='total'><td>All four fields</td><td class='num'>{a['n']}</td>"
                 f"<td class='num'>{a['correct']} ({pct(a['correct'], a['n'])})</td>"
                 f"<td class='num'>{b['correct']} ({pct(b['correct'], b['n'])})</td></tr>")
        parts.append(f"<h4>{e(label)}</h4><table class='small'><thead><tr><th>Field</th><th>Judged</th>"
                     f"<th>Pass 1 correct</th><th>Pass 2 correct</th></tr></thead><tbody>{body}</tbody></table>")
    miss = ""
    for m in score["misses"]:
        if not m["pass1_ok"] and m["pass2_ok"]:
            state = '<span class="b good">fixed by verification</span>'
        elif m["pass1_ok"] and m["pass2_ok"] is False:
            state = '<span class="b bad">broken by verification</span>'
        else:
            state = '<span class="b warn">still disagrees</span>'
        grey = ' <span class="sub">(grey zone)</span>' if m.get("grey_zone") else ""
        miss += (f"<tr><td>{e(m['name'])}</td><td>{nice(m['field'])}{grey}</td><td>{nice(m['human'])}</td>"
                 f"<td>{nice(m['pass1'])}</td><td>{nice(m['pass2'])}</td><td>{state}</td></tr>")
    misses = (f"<table class='small'><thead><tr><th>App</th><th>Field</th><th>Human</th><th>Pass 1</th>"
              f"<th>Pass 2</th><th>Outcome</th></tr></thead><tbody>{miss}</tbody></table>") if miss else "<p>None.</p>"
    return "".join(parts), misses


def main():
    rows = collect()
    if not rows:
        print("No results found. Run research.py first.")
        return
    P = patterns(rows)
    score = load(ROOT / "results" / "score.json")
    site = load(ROOT / "site.json", {}) or {}
    notes = load(ROOT / "notes.json", {}) or {}
    n = P["n"]
    top_auth = list(P["auth"].items())
    top_block = list(P["blockers"].items())
    strict = (score or {}).get("views", {}).get("strict")
    acc1 = pct(strict["pass1"]["correct"], strict["pass1"]["n"]) if strict else "n/a"
    acc2 = pct(strict["pass2"]["correct"], strict["pass2"]["n"]) if strict else "n/a"
    key_n = (score or {}).get("apps_in_key", 0)

    auto = [
        f"<b>{nice(top_auth[0][0])} is the most common primary auth: {top_auth[0][1]} of {n} apps.</b> "
        + ", ".join(f"{nice(k)} {v}" for k, v in top_auth[1:5]) + f". {P['dual_auth']} apps offer OAuth2 "
        "and a simpler key or token side by side, so the single label depends on who you are building for.",
        f"<b>{P['self_serve']} of {n} apps are self-serve</b> (free or trial). {P['access'].get('paid_plan', 0)} need a paid plan, "
        f"{P['access'].get('approval_required', 0)} need vendor approval and {P['gated']} are gated behind sales or a partnership.",
        (f"<b>The most common blocker is {nice(top_block[0][0])} ({top_block[0][1]} apps)</b>"
         + ("; then " + ", ".join(f"{nice(k)} {v}" for k, v in top_block[1:4]) if len(top_block) > 1 else "") + ".")
        if top_block else "<b>No blockers were recorded.</b>",
        f"<b>{P['mcp'].get('official', 0)} of {n} apps have a vendor-published MCP server</b>; "
        f"{P['mcp'].get('community', 0)} have community servers only and {P['mcp'].get('none_found', 0)} have none found.",
        f"<b>{len(P['easy_wins'])} apps are easy wins</b> (verdict yes with self-serve access); "
        f"{len(P['outreach'])} need outreach, approval or are blocked.",
    ]
    headline = notes.get("headline") or auto

    mrows = "".join(
        f"<tr><td>{e(m['category'])}</td><td class='num'>{m['self_serve']}</td><td class='num'>{m['paid_plan']}</td>"
        f"<td class='num'>{m['approval']}</td><td class='num'>{m['gated']}</td><td class='num'>{m['other']}</td>"
        f"<td class='num'>{m['oauth2']}</td><td class='num'>{m['mcp_official']}</td><td class='num'>{m['yes']}</td></tr>"
        for m in P["matrix"])
    easy = ", ".join(e(x["name"]) for x in P["easy_wins"]) or "none"
    outreach = ", ".join(f"{e(x['name'])} <span class='sub'>({nice(x['access'])})</span>" for x in P["outreach"]) or "none"
    tables, misses = score_block(score)
    cats = "".join(f"<option>{e(m['category'])}</option>" for m in P["matrix"])

    human = notes.get("human_needed") or [
        "Wrote the rulebook that defines every field and its allowed values.",
        f"Hand-checked {key_n} apps against vendor docs to build the answer key.",
        "Replaced a wrong-type Composio key and worked around a Windows policy that blocked pip.",
        "Inspected one full pass-1 record by eye, which is where the four failure types the verifier targets came from.",
    ]
    limits = notes.get("limitations") or [
        "The answer key is small, so each accuracy figure can move several points on one app.",
        "The labeller had seen the agent's summary lines before labelling, and compared answers with a second AI reviewer after each label.",
        "Primary auth is the least reliable field: many vendors offer a key for your own account and OAuth for apps serving others.",
        "One access label cannot hold both 'a developer can get test credentials' and 'a customer's plan allows API use'.",
        "Evidence pages are not dated. A page from before a vendor launched an API or MCP server can be confidently out of date.",
        "The 'vendor-owned page' check matches the app name in the web address, so a different company with a similar name can pass.",
        "The second-model skeptic errs towards doubt and marks some correct answers as weak.",
        "No browser-automation check was built. Pages that need JavaScript to render were read only through the fetch tool.",
    ]
    li = lambda items: "".join(f"<li>{x}</li>" for x in items)
    data = {"generated": str(date.today()), "patterns": P, "score": score, "apps": rows}

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>100 apps, one research agent: what it takes to build agent toolkits</title>
<style>
:root{{--ink:#16202a;--mute:#5d6b78;--line:#dfe4ea;--bg:#f6f7f9;--card:#fff;--good:#0f7b4f;--goodbg:#e3f4ec;
--warn:#8a5a00;--warnbg:#fbf0d9;--bad:#a12a2a;--badbg:#fae4e4;--mutebg:#eceff3;--accent:#2a4fd6}}
*{{box-sizing:border-box}}body{{margin:0;font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--ink);background:var(--bg)}}
main{{max-width:1180px;margin:0 auto;padding:28px 20px 80px}}h1{{font-size:30px;line-height:1.2;margin:0 0 8px;letter-spacing:-.01em}}
h2{{font-size:21px;margin:44px 0 12px;padding-top:14px;border-top:2px solid var(--ink)}}h3{{font-size:16px;margin:22px 0 8px}}h4{{font-size:14px;margin:18px 0 6px}}
p{{margin:8px 0}}a{{color:var(--accent)}}code{{background:var(--mutebg);padding:1px 5px;border-radius:4px;font-size:13px}}
.lede{{color:var(--mute);max-width:760px}}nav{{display:flex;gap:14px;flex-wrap:wrap;margin:16px 0 4px;font-size:14px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:18px 0}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}}.kpi b{{display:block;font-size:24px;letter-spacing:-.01em}}.kpi span{{color:var(--mute);font-size:13px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 18px;margin:10px 0}}
ol.find{{margin:0;padding-left:22px}}ol.find li{{margin:9px 0}}
table{{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:14px}}
th,td{{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{background:var(--mutebg);font-size:12.5px;text-transform:uppercase;letter-spacing:.04em;color:var(--mute)}}
td.num,th.num{{text-align:right;font-variant-numeric:tabular-nums}}tr.total td{{font-weight:700;border-top:2px solid var(--ink)}}table.small{{max-width:760px}}
.sub{{color:var(--mute);font-size:12.5px}}.b{{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12.5px;white-space:nowrap}}
.good{{background:var(--goodbg);color:var(--good)}}.warn{{background:var(--warnbg);color:var(--warn)}}.bad{{background:var(--badbg);color:var(--bad)}}.mute{{background:var(--mutebg);color:var(--mute)}}
.filters{{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}}.filters input,.filters select{{padding:7px 9px;border:1px solid var(--line);border-radius:8px;font:inherit;background:#fff}}
details summary{{cursor:pointer;color:var(--accent);font-size:12.5px;margin-top:4px}}details ul{{margin:4px 0;padding-left:18px}}details li{{margin:3px 0;font-size:13px;word-break:break-word}}
.steps{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}}.step{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}}
.step b{{display:block}}.step i{{font-style:normal;color:var(--mute);font-size:12.5px}}.wrap{{overflow-x:auto}}
</style></head><body><main>
<h1>100 apps, one research agent: what it takes to build agent toolkits</h1>
<p class="lede">A take-home for Composio's AI Product Ops Intern role{(" by " + e(site["author"])) if site.get("author") else ""}.
An agent researched {n} apps for auth, access, API surface, MCP availability and buildability. A second pass tried to break its answers.
A human checked a sample by hand. Generated {date.today():%d %b %Y}.</p>
<nav><a href="#patterns">Patterns</a><a href="#findings">Findings table</a><a href="#agent">The agent</a>
<a href="#verification">Verification</a><a href="#limits">Limits</a><a href="#proof">Proof and data</a></nav>
<div class="kpis">
<div class="kpi"><b>{n}</b><span>apps researched</span></div>
<div class="kpi"><b>{P['self_serve']}</b><span>self-serve access</span></div>
<div class="kpi"><b>{P['mcp'].get('official', 0)}</b><span>with an official MCP server</span></div>
<div class="kpi"><b>{acc1} &rarr; {acc2}</b><span>accuracy on {key_n} hand-checked apps, pass 1 to pass 2</span></div>
<div class="kpi"><b>{P['flags_before']} &rarr; {P['flags_after']}</b><span>evidence problems found by the audit, before and after</span></div>
</div>

<h2 id="patterns">The patterns</h2>
<div class="card"><ol class="find">{li(headline)}</ol></div>
<h3>By category</h3>
<div class="wrap"><table><thead><tr><th>Category (10 apps each)</th><th class="num">Self-serve</th><th class="num">Paid plan</th>
<th class="num">Approval</th><th class="num">Sales gated</th><th class="num">N/A or unknown</th><th class="num">OAuth2 primary</th>
<th class="num">Official MCP</th><th class="num">Verdict yes</th></tr></thead><tbody>{mrows}</tbody></table></div>
<h3>Easy wins: buildable today with self-serve credentials ({len(P['easy_wins'])})</h3>
<div class="card">{easy}</div>
<h3>Needs outreach, approval, or blocked ({len(P['outreach'])})</h3>
<div class="card">{outreach}</div>

<h2 id="findings">Findings: all {n} apps</h2>
<p class="sub">Final answers after verification. "Trust" is computed from the checks, not self-reported by the model. Open a row for evidence quotes, notes and remaining doubts.</p>
<div class="filters"><input id="q" placeholder="Search app or text" size="24">
<select id="fc"><option value="">All categories</option>{cats}</select>
<select id="fa"><option value="">Any access</option><option>self_serve_free</option><option>self_serve_trial</option><option>paid_plan</option><option>approval_required</option><option>partner_or_sales_gated</option><option>not_applicable</option><option>unknown</option></select>
<select id="fv"><option value="">Any verdict</option><option>yes</option><option>yes_with_caveats</option><option>no</option><option>unknown</option></select>
<select id="ft"><option value="">Any trust</option><option>verified</option><option>partly_verified</option><option>needs_human</option><option>not_verified</option></select>
<span class="sub" id="count"></span></div>
<div class="wrap"><table id="t"><thead><tr><th>#</th><th>App</th><th>Category</th><th>Auth (primary, others)</th><th>Access</th><th>API</th><th>MCP</th><th>Buildable?</th><th>Trust</th><th>Evidence</th></tr></thead>
<tbody>{table_rows(rows)}</tbody></table></div>

<h2 id="agent">The agent</h2>
<div class="steps">
<div class="step"><b>1. Search</b>Three web searches per app through Composio's search tool. <i>COMPOSIO_SEARCH_TAVILY</i></div>
<div class="step"><b>2. Read</b>Up to three full pages, vendor domain first. <i>COMPOSIO_SEARCH_FETCH_URL_CONTENT</i></div>
<div class="step"><b>3. Extract</b>A model fills a fixed form and must quote its evidence. <i>Gemini Flash-Lite</i></div>
<div class="step"><b>4. Audit</b>Plain code checks each quote is really on the cited page, the page is vendor-owned, and fields agree. <i>no AI</i></div>
<div class="step"><b>5. Re-research</b>Apps that fail are researched again on vendor domains only, with the objections attached. <i>{P['re_researched']} apps</i></div>
<div class="step"><b>6. Skeptic</b>A different model judges whether the evidence supports each claim. <i>GPT-OSS 120B on Groq</i></div>
<div class="step"><b>7. Trust score</b>Computed from steps 4 and 6. It replaces the model's own confidence. <i>no AI</i></div>
</div>
<h3>Where a human was needed</h3><div class="card"><ul>{li(e(x) for x in human)}</ul></div>

<h2 id="verification">Verification</h2>
<p>Pass 1 is the raw agent. Pass 2 is the same answers after the audit, re-research and skeptic. Both are scored against {key_n} apps checked by hand,
sampled at random (2 per category, fixed seed). Fields the human marked "unknown" are not scored.</p>
{tables}
<h3>Every disagreement with the human answer key</h3>{misses}
<h3>What the verification loop did across all {n} apps</h3>
<div class="card"><ul>
<li>Pass 1 rated its own confidence as: {", ".join(f"{nice(k)} {v}" for k, v in P['pass1_confidence'].items())}.</li>
<li>The audit raised {P['flags_before']} evidence problems. {P['re_researched']} apps were re-researched and {P['apps_changed']} had at least one answer changed. {P['flags_after']} problems remain.</li>
<li>Fields changed most often: {", ".join(f"{nice(k)} {v}" for k, v in list(P['changed_field_counts'].items())[:6]) or "none"}.</li>
<li>The skeptic model doubted at least one claim in {P['skeptic_flagged_apps']} of {P['skeptic_ran']} apps it reviewed.</li>
<li>Final trust: {", ".join(f"{nice(k)} {v}" for k, v in P['trust'].items())}.</li>
</ul></div>

<h2 id="limits">Limits and known failure cases</h2>
<div class="card"><ul>{li(e(x) for x in limits)}</ul></div>

<h2 id="proof">Proof and data</h2>
<div class="card"><ul>
<li>Source code and README: {f'<a href="{e(site["repo_url"])}">{e(site["repo_url"])}</a>' if site.get("repo_url") else "repo link to be added"}</li>
<li>Run it: <code>python research.py --all</code>, <code>python verify.py --all</code>, <code>python score.py</code>, <code>python build.py</code></li>
<li>Machine-readable results for agents: <a href="data.json">data.json</a> (every app, every evidence quote, every check). The same data is embedded in this page in the script tag with id <code>data</code>.</li>
<li>Tools: Composio SDK for search and page reading; Gemini and Groq free tiers for the two models.</li>
</ul></div>
<script type="application/json" id="data">{json.dumps(data, ensure_ascii=False).replace("</", "<\\/")}</script>
<script>
const q=document.getElementById('q'),fc=document.getElementById('fc'),fa=document.getElementById('fa'),fv=document.getElementById('fv'),ft=document.getElementById('ft');
const trs=[...document.querySelectorAll('#t tbody tr')];
function run(){{const s=q.value.toLowerCase();let n=0;trs.forEach(r=>{{const ok=(!s||r.textContent.toLowerCase().includes(s))&&(!fc.value||r.dataset.cat===fc.value)&&(!fa.value||r.dataset.access===fa.value)&&(!fv.value||r.dataset.verdict===fv.value)&&(!ft.value||r.dataset.trust===ft.value);r.style.display=ok?'':'none';if(ok)n++;}});document.getElementById('count').textContent=n+' of '+trs.length+' shown';}}
[q,fc,fa,fv,ft].forEach(x=>x.addEventListener('input',run));run();
</script>
</main></body></html>"""
    DOCS.mkdir(exist_ok=True)
    (DOCS / "index.html").write_text(page, encoding="utf-8")
    (DOCS / "data.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")

    print(f"Built docs/index.html and docs/data.json for {n} apps.\n")
    print("PATTERN SUMMARY (paste this to Claude)")
    for k in ("auth", "access", "mcp", "verdict", "blockers", "trust", "pass1_confidence", "changed_field_counts"):
        print(f"  {k}: {P[k]}")
    print(f"  dual_auth: {P['dual_auth']}  self_serve: {P['self_serve']}  gated: {P['gated']}")
    print(f"  flags: {P['flags_before']} -> {P['flags_after']}  re_researched: {P['re_researched']}  apps_changed: {P['apps_changed']}")
    print(f"  skeptic doubted: {P['skeptic_flagged_apps']} of {P['skeptic_ran']}")
    print(f"  easy wins: {len(P['easy_wins'])}  outreach: {len(P['outreach'])}")
    for m in P["matrix"]:
        print(f"  {m['category'][:34]:<34} self={m['self_serve']} paid={m['paid_plan']} appr={m['approval']} gated={m['gated']} other={m['other']} oauth={m['oauth2']} mcp={m['mcp_official']} yes={m['yes']}")


if __name__ == "__main__":
    main()
