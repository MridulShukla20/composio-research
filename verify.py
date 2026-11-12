"""Verification loop, pass 2.

Takes every pass-1 answer and tries to break it:
  1. AUDIT (code, no AI): is every quote really in the source? is the source the
     vendor's own site? do the fields contradict each other?
  2. RE-RESEARCH (Gemini): apps that fail the audit are researched again,
     searching only the vendor's own domains, with the auditor's objections attached.
  3. SKEPTIC (a different model, on Groq): judges whether the evidence actually
     supports each key claim.
  4. TRUST: a score computed from the checks, replacing the model's self-rated confidence.
Usage:
  python verify.py --ids 90,83,81
  python verify.py --all
"""
import argparse
import json
import os
import re
import sys
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse

sys.stdout.reconfigure(errors="replace")
ROOT = Path(__file__).parent

GROQ_MODEL = "openai/gpt-oss-120b"
GROQ_GAP = 9.0  # seconds between skeptic calls (free tier token-per-minute limit)
GENERIC = {"github.com", "github.io", "gitlab.com", "stoplight.io", "readme.io", "gitbook.io"}
KEY_FIELDS = ("auth", "access", "mcp", "verdict")
GATE_WORDS = re.compile(
    r"contact (our |the )?sales|sales team|contract|agreement|partnership|partner program"
    r"|enterprise (plan|only|contract|agreement)|request access|waitlist|invite[- ]only",
    re.I,
)
URLISH = re.compile(r"((?:[a-z0-9-]+\.)+[a-z]{2,})(/[^\s()]*)?")


# ---------- which websites count as the vendor's own ----------

def official_domains(app):
    out = set()
    for m in URLISH.finditer(app["hint"].lower()):
        core = ".".join(m.group(1).split(".")[-2:])
        if core not in GENERIC:
            out.add(core)
    return out


def name_slugs(app):
    name = re.sub(r"\(.*?\)", " ", app["name"]).lower()
    words = re.findall(r"[a-z0-9]+", name)
    slugs = set()
    if words and len(words[0]) >= 5:
        slugs.add(words[0])
    if len(words) >= 2:
        slugs.add(words[0] + words[1])
    return slugs


def is_official(url, app):
    p = urlparse(url if "://" in url else "https://" + url)
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    slugs = name_slugs(app)
    if host in ("github.com", "gitlab.com"):
        org = (p.path.strip("/").split("/") or [""])[0].lower()
        return any(s in org for s in slugs)
    if any(host == d or host.endswith("." + d) for d in official_domains(app)):
        return True
    return any(s in host for s in slugs)


# ---------- audit: checks done by plain code ----------

def ntext(s):
    s = unicodedata.normalize("NFKC", s or "")
    for a, b in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'),
                 ("–", "-"), ("—", "-")):
        s = s.replace(a, b)
    s = re.sub(r"[*_`#|>\\\[\]]", " ", s)
    return re.sub(r"\s+", " ", s).lower().strip()


def nurl(url):
    return re.sub(r"^https?://(www\.)?", "", url.strip().lower()).rstrip("/")


def quote_status(quote, url, sources):
    """exact = quote is in the cited page. other_url = it is in a different source.
    near = at least 80% of it appears in one piece. missing = not found."""
    q = ntext(quote)
    if len(q) < 8:
        return "missing"
    same = [s for s in sources if nurl(s["url"]) == nurl(url)]
    other = [s for s in sources if nurl(s["url"]) != nurl(url)]
    if any(q in ntext(s["text"]) for s in same):
        return "exact"
    if any(q in ntext(s["text"]) for s in other):
        return "other_url"
    for s in same or sources:
        t = ntext(s["text"])
        m = SequenceMatcher(None, q, t, autojunk=False).find_longest_match(0, len(q), 0, len(t))
        if m.size >= 0.8 * len(q):
            return "near"
    return "missing"


def audit(record, app, sources):
    flags, rows = [], []

    def flag(code, field, detail):
        flags.append({"code": code, "field": field, "detail": detail})

    by_field = {}
    for e in record.get("evidence", []):
        row = {
            "field": e["field"], "url": e["url"], "quote": e["quote"],
            "official": is_official(e["url"], app),
            "quote_check": quote_status(e["quote"], e["url"], sources),
        }
        rows.append(row)
        by_field.setdefault(e["field"], []).append(row)
        if row["quote_check"] == "missing":
            flag("quote_not_found", e["field"], f"quote not in cited source: {e['quote'][:80]}")

    for f in ("auth", "access", "api"):
        if not by_field.get(f):
            flag("evidence_missing", f, f"no evidence given for {f}")
        elif not any(r["official"] for r in by_field[f]):
            flag("third_party_only", f, f"{f} is backed only by non-vendor pages")

    if record.get("mcp") == "official" and not any(r["official"] for r in by_field.get("mcp", [])):
        flag("mcp_official_unbacked", "mcp", "mcp=official but no vendor-owned page or vendor GitHub org cited")

    notes = " ".join([record.get("access_note", ""), record.get("blocker_note", "")])
    access = record.get("access")
    if GATE_WORDS.search(notes) and access not in ("partner_or_sales_gated", "approval_required"):
        flag("note_suggests_gate", "access", f"access={access} but the notes mention a sales or contract gate")

    verdict, blocker = record.get("verdict"), record.get("main_blocker")
    if access == "partner_or_sales_gated" and verdict == "yes":
        flag("verdict_inconsistent", "verdict", "verdict=yes although access is gated")
    if verdict == "yes" and blocker not in ("none", None):
        flag("verdict_inconsistent", "verdict", f"verdict=yes but main_blocker={blocker}")
    if verdict == "no" and blocker == "none":
        flag("verdict_inconsistent", "verdict", "verdict=no but main_blocker=none")
    if access in ("self_serve_free", "self_serve_trial") and blocker in ("paid_plan", "partner_or_sales_gate"):
        flag("verdict_inconsistent", "access", f"access={access} but main_blocker={blocker}")

    if record.get("primary_auth") not in record.get("auth_methods", []):
        flag("primary_not_in_methods", "auth", "primary_auth is not listed in auth_methods")

    return flags, rows


def penalty(flags, skeptic):
    pts = len([f for f in flags if f["field"] in KEY_FIELDS or f["code"] == "evidence_missing"])
    for v in (skeptic or {}).values():
        j = (v or {}).get("judgement")
        pts += 3 if j == "contradicted" else 1 if j == "weak" else 0
    return pts


def trust_of(points):
    if points == 0:
        return "verified", "high"
    if points <= 2:
        return "partly_verified", "medium"
    return "needs_human", "low"


# ---------- the parts that call outside services ----------

def run(args):
    from groq import Groq

    import research as R
    from schema import RULES, load_apps

    groq = Groq(api_key=os.environ["GROQ_API_KEY"].strip())
    state = {"last_groq": 0.0}

    def osearch(app, query, extra=()):
        include = sorted(official_domains(app)) + list(extra)
        f = R.CACHE / "search" / f"{R._key('p2|' + query + '|' + ','.join(include))}.json"
        if f.exists():
            return R._read(f)
        arguments = {"query": query, "max_results": 5}
        if include:
            arguments["include_domains"] = include
        data = R.call_tool(R.SEARCH, arguments)
        results = [
            {"url": r.get("url", ""), "title": r.get("title") or "",
             "content": r.get("content") or "", "score": r.get("score") or 0}
            for r in (data.get("results") or []) if r.get("url")
        ]
        R._write(f, results)
        return results

    def reresearch(app, old, flags, old_sources):
        name = app["name"]
        found = {
            "auth": osearch(app, f"{name} API authentication"),
            "access": osearch(app, f"{name} API access pricing plans developer account API key"),
            "mcp": osearch(app, f"{name} MCP server", extra=("github.com",)),
        }
        pool = sorted(found["auth"] + found["access"], key=lambda r: -r["score"])
        wanted, seen = [], set()
        for r in pool:
            n = nurl(r["url"])
            if n in seen or n.endswith(".pdf") or not is_official(r["url"], app):
                continue
            seen.add(n)
            wanted.append(r["url"])
            if len(wanted) == 3:
                break
        pages = R.fetch(wanted) if wanted else {}

        sources = []

        def add(kind, url, text):
            tag = "OFFICIAL" if is_official(url, app) else "THIRD-PARTY"
            sources.append({"label": f"X{len(sources) + 1}", "kind": f"{tag} {kind}",
                            "url": url, "text": text})

        for u in wanted:
            if u in pages:
                add("full page", u, pages[u])
        for s in old_sources:
            if s["kind"] == "full page" and nurl(s["url"]) not in seen:
                seen.add(nurl(s["url"]))
                official = is_official(s["url"], app)
                add("full page", s["url"], s["text"] if official else s["text"][:3000])
        for topic in ("auth", "access", "mcp"):
            for r in found[topic][:3]:
                add(f"search snippet ({topic})", r["url"], r["content"][:R.SNIPPET_CHARS])

        problems = "\n".join(f"- [{f['field']}] {f['detail']}" for f in flags) or "- none"
        blocks = "\n\n".join(f"[{s['label']}] ({s['kind']}) URL: {s['url']}\n{s['text']}" for s in sources)
        prompt = (
            "You are re-checking an earlier research answer for Composio, a company that "
            "builds tool integrations for AI agents.\n\n"
            f"APP: {app['name']}\nCATEGORY: {app['category']}\nHINT: {app['hint']}\n{RULES}\n"
            "EARLIER ANSWER (it may contain mistakes):\n"
            f"{json.dumps(old, indent=1)}\n\n"
            f"PROBLEMS AN AUDITOR FOUND:\n{problems}\n\n"
            "STRICTER RULES FOR THIS RE-CHECK\n"
            "- Sources marked OFFICIAL belong to the vendor. Prefer them. Use THIRD-PARTY "
            "sources only for fields no official source covers, and then confidence cannot be high.\n"
            "- mcp is 'official' only if an OFFICIAL source (vendor site or the vendor's own "
            "GitHub organisation) shows the vendor publishes an MCP server. A directory or "
            "marketplace listing is not proof: use 'community' if third-party servers exist, "
            "'none_found' if none, 'unknown' if unclear.\n"
            "- access: if credentials need a contract, a sales conversation, a partnership or an "
            "enterprise agreement, answer partner_or_sales_gated, even when it also costs money. "
            "paid_plan is only for plans a developer can buy by self-service.\n"
            "- List an auth method or API style only if a source states it.\n"
            "- verdict must agree with access and main_blocker. Gated access is never 'yes'.\n"
            "- Keep earlier values the sources still support. Change the ones they do not.\n"
            "- Every evidence url must be a SOURCES url and every quote copied exactly from it.\n"
            "- Return one JSON object and nothing else, in exactly this shape:\n"
            f"{R.ANSWER_SHAPE}\n\nSOURCES\n\n{blocks}"
        )
        extra = ""
        for _ in (1, 2):
            text = R.ask_gemini(prompt + extra)
            try:
                return R.parse_record(text, app).model_dump(), sources
            except Exception as e:
                extra = ("\n\nYour previous answer was rejected by the validator:\n"
                         f"{str(e)[:800]}\nReturn corrected JSON only.")
        raise RuntimeError("re-research output failed validation twice")

    def context(quote, url, sources):
        q = ntext(quote)
        for s in sources:
            if nurl(s["url"]) == nurl(url):
                t = ntext(s["text"])
                i = t.find(q)
                if i >= 0:
                    return t[max(0, i - 200): i + len(q) + 200]
        return ""

    def skeptic(app, rec, rows, sources):
        lines = []
        for r in rows:
            if r["field"] not in KEY_FIELDS:
                continue
            tag = "OFFICIAL" if r["official"] else "THIRD-PARTY"
            lines.append(
                f"- field={r['field']} | {tag} | {r['url']}\n  quote: \"{r['quote']}\" "
                f"(found in source: {r['quote_check']})\n  context: {context(r['quote'], r['url'], sources)[:450]}"
            )
        prompt = (
            "You are a skeptical reviewer of API research. Decide whether the evidence "
            "supports each claim. Judge mainly from the evidence shown. You may also use what "
            "you know about the product to call a claim contradicted, but then say so in 'why'.\n\n"
            f"APP: {app['name']} ({app['hint']})\n\nCLAIMS\n"
            f"- auth: primary_auth={rec['primary_auth']}, auth_methods={rec['auth_methods']}\n"
            f"- access: {rec['access']} ({rec['access_note']})\n"
            f"- mcp: {rec['mcp']} {rec.get('mcp_url', '')}\n"
            f"- verdict: {rec['verdict']}, main_blocker={rec['main_blocker']} ({rec.get('blocker_note', '')})\n\n"
            "MEANING OF VALUES\n"
            "access: self_serve_free | self_serve_trial | paid_plan (self-service paid) | "
            "approval_required (vendor review) | partner_or_sales_gated (contract or sales) | not_applicable.\n"
            "mcp: official (published by the vendor) | community | none_found | unknown.\n"
            "auth: oauth2 | api_key | basic | token (PAT, bot token, JWT, bearer) | other | none.\n\n"
            "EVIDENCE\n" + ("\n".join(lines) or "- none given") + "\n\n"
            "Return JSON only, with exactly these four keys:\n"
            '{"auth": {"judgement": "supported|weak|contradicted", "why": "20 words or fewer", '
            '"suggested": "better value or empty string"}, "access": {...}, "mcp": {...}, "verdict": {...}}'
        )
        for attempt in range(3):
            wait = GROQ_GAP - (time.time() - state["last_groq"])
            if wait > 0:
                time.sleep(wait)
            state["last_groq"] = time.time()
            try:
                r = groq.chat.completions.create(
                    model=GROQ_MODEL, temperature=0,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = r.choices[0].message.content or ""
                m = re.search(r"\{.*\}", text, re.S)
                d = json.loads(m.group(0))
                out = {}
                for k in KEY_FIELDS:
                    v = d.get(k) or {}
                    j = str(v.get("judgement", "")).lower()
                    out[k] = {"judgement": j if j in ("supported", "weak", "contradicted") else "weak",
                              "why": str(v.get("why", ""))[:200],
                              "suggested": str(v.get("suggested", ""))[:60]}
                return out
            except Exception as e:
                msg = str(e)
                if "429" in msg or "rate" in msg.lower():
                    print(f"      skeptic rate limited, waiting 30s ({attempt + 1}/3)")
                    time.sleep(30)
                    continue
                if attempt == 2:
                    return None
        return None

    apps = load_apps()
    if args.ids:
        ids = {int(x) for x in args.ids.split(",") if x.strip()}
        apps = [a for a in apps if a["id"] in ids]
    elif not args.all:
        print("Give --ids 90,83 or --all")
        return

    out_dir = ROOT / "results" / "pass2"
    counts = {"verified": 0, "partly_verified": 0, "needs_human": 0}
    done = skipped = failed = rechecked = streak = 0
    for i, app in enumerate(apps, 1):
        target = out_dir / f"{app['id']}.json"
        if target.exists() and not args.force:
            skipped += 1
            continue
        tag = f"[{i:>3}/{len(apps)}] #{app['id']:<3} {app['name']:<24}"
        try:
            p1 = R._read(ROOT / "results" / "pass1" / f"{app['id']}.json")
            old = p1["record"]
            src1 = R._read(R.CACHE / "sources" / "pass1" / f"{app['id']}.json")
            flags1, _ = audit(old, app, src1)

            rec, sources, did = old, src1, False
            if flags1:
                new, src2 = reresearch(app, old, flags1, src1)
                all_src = src2 + src1
                flags_new, _ = audit(new, app, all_src)
                if len(flags_new) <= len(flags1):
                    rec, sources, did = new, all_src, True
            flags2, rows = audit(rec, app, sources)

            sk = None if args.no_skeptic else skeptic(app, rec, rows, sources)
            points = penalty(flags2, sk)
            trust, conf = trust_of(points)
            rec = dict(rec)
            rec["confidence"] = conf
            changed = [k for k in ("primary_auth", "auth_methods", "access", "api_styles",
                                   "api_breadth", "mcp", "verdict", "main_blocker")
                       if old.get(k) != rec.get(k)]
            R._write(R.CACHE / "sources" / "pass2" / f"{app['id']}.json", sources)
            R._write(target, {
                "record": rec,
                "meta": {
                    "pass": "pass2", "re_researched": did, "changed_fields": changed,
                    "flags_before": flags1, "flags_after": flags2, "evidence_checks": rows,
                    "skeptic_model": None if args.no_skeptic else GROQ_MODEL, "skeptic": sk,
                    "penalty_points": points, "trust": trust,
                    "pass1_confidence": old.get("confidence"),
                },
            })
            counts[trust] += 1
            rechecked += did
            done += 1
            streak = 0
            bad = [k for k, v in (sk or {}).items() if v["judgement"] != "supported"]
            print(f"{tag} flags {len(flags1)}->{len(flags2)}  changed={','.join(changed) or '-':<28} "
                  f"skeptic={'n/a' if sk is None else (','.join(bad) or 'ok'):<18} trust={trust}")
        except Exception as e:
            failed += 1
            streak += 1
            msg = f"{type(e).__name__}: {str(e)[:500]}"
            R._write(out_dir / "errors" / f"{app['id']}.json", {"app": app["name"], "error": msg})
            print(f"{tag} FAILED {msg}")
            if streak >= 3:
                print("Stopping: 3 apps failed in a row. Paste this output to Claude.")
                break
    print(f"\nFinished. done={done} skipped={skipped} failed={failed} re_researched={rechecked}")
    print("Trust:", counts)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-skeptic", action="store_true")
    run(ap.parse_args())
