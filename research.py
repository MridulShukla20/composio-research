"""Research agent, pass 1.

For each app: search the web (Composio), read the best pages (Composio),
have Gemini fill in the schema with quoted evidence, validate, save.
Usage:
  python research.py --ids 81,58,90      a few apps
  python research.py --all               all 100 (skips apps already done)
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import get_args

from composio import Composio
from dotenv import load_dotenv
from google import genai

import schema
from schema import RULES, AppResearch, load_apps

sys.stdout.reconfigure(errors="replace")
load_dotenv()

MODEL = "gemini-3.5-flash-lite"
USER = "research-agent"
SEARCH = "COMPOSIO_SEARCH_TAVILY"
FETCH = "COMPOSIO_SEARCH_FETCH_URL_CONTENT"
PAGE_CHARS = 12000      # how much of each page the model reads
SNIPPET_CHARS = 700     # how much of each search snippet the model reads
MAX_PAGES = 3           # full pages read per app
GEMINI_GAP = 6.5        # seconds between model calls (free tier: 10 per minute)

ROOT = Path(__file__).parent
CACHE = ROOT / "cache"
for sub in ("search", "pages", "sources"):
    (CACHE / sub).mkdir(parents=True, exist_ok=True)

composio = Composio(api_key=os.environ["COMPOSIO_API_KEY"].strip())
gemini = genai.Client(api_key=os.environ["GEMINI_API_KEY"].strip())
_last_llm = 0.0


# ---------- small helpers ----------

def _key(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def norm(url):
    """Same page, same string: drop https://, www. and a trailing slash."""
    return re.sub(r"^https?://(www\.)?", "", url.strip().lower()).rstrip("/")


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False), encoding="utf-8")


# ---------- Composio tools ----------

def call_tool(slug, arguments):
    res = composio.tools.execute(
        slug, user_id=USER, arguments=arguments, dangerously_skip_version_check=True
    )
    if hasattr(res, "model_dump"):
        res = res.model_dump()
    if not isinstance(res, dict):
        raise RuntimeError(f"{slug}: unexpected result type")
    if res.get("successful") is False:
        raise RuntimeError(f"{slug} failed: {str(res.get('error'))[:300]}")
    return res.get("data") or {}


def search(query):
    f = CACHE / "search" / f"{_key(query)}.json"
    if f.exists():
        return _read(f)
    data = call_tool(SEARCH, {"query": query, "max_results": 5})
    results = [
        {
            "url": r.get("url", ""),
            "title": r.get("title") or "",
            "content": r.get("content") or "",
            "score": r.get("score") or 0,
        }
        for r in (data.get("results") or [])
        if r.get("url")
    ]
    _write(f, results)
    return results


def _grab(urls):
    data = call_tool(FETCH, {"urls": urls, "max_characters": PAGE_CHARS})
    return {
        norm(r.get("url") or r.get("id") or ""): (r.get("text") or "")
        for r in (data.get("results") or [])
    }


def fetch(urls):
    """Return {url: page text} for the pages that could be read."""
    out, missing = {}, []
    for u in urls:
        f = CACHE / "pages" / f"{_key(norm(u))}.json"
        if f.exists():
            out[u] = _read(f)["text"]
        else:
            missing.append(u)
    if not missing:
        return out
    try:
        got = _grab(missing)
    except Exception:
        got = {}
    for u in missing:
        text = got.get(norm(u), "")
        if not text:
            try:
                text = next(iter(_grab([u]).values()), "")
            except Exception:
                text = ""
        if len(text) >= 200:
            _write(CACHE / "pages" / f"{_key(norm(u))}.json", {"url": u, "text": text})
            out[u] = text
    return out


# ---------- choosing which pages to read ----------

URLISH = re.compile(r"((?:[a-z0-9-]+\.)+[a-z]{2,})(/[^\s()]*)?")


def parse_hint(hint):
    """From the assignment's hint, get (url, core domain, looks like a docs page)."""
    matches = list(URLISH.finditer(hint.lower()))
    if not matches:
        return None, None, False
    m = max(matches, key=lambda x: (bool(x.group(2)), x.group(1).count(".")))
    host, path = m.group(1), (m.group(2) or "")
    labels = host.split(".")
    core = ".".join(labels[-2:])
    doc_like = bool(path.strip("/")) or len(labels) > 2
    return "https://" + host + path, core, doc_like


def pick_pages(app, result_lists):
    url, core, doc_like = parse_hint(app["hint"])
    pool = [r for lst in result_lists for r in lst]
    pool.sort(key=lambda r: (0 if core and core in r["url"].lower() else 1, -r["score"]))
    candidates = []
    if url and doc_like:
        candidates.append(url)
    candidates += [r["url"] for r in pool]
    if url and not doc_like:
        candidates.append(url)
    picked, seen = [], set()
    for u in candidates:
        n = norm(u)
        if n in seen or n.endswith(".pdf") or "youtube.com" in n:
            continue
        seen.add(n)
        picked.append(u)
        if len(picked) == MAX_PAGES:
            break
    return picked


# ---------- the model ----------

def _one_of(literal):
    return "one of: " + " | ".join(get_args(literal))


ANSWER_SHAPE = json.dumps(
    {
        "one_liner": "what the app does, one line",
        "auth_methods": ["one or more of: " + " | ".join(get_args(schema.Auth))],
        "primary_auth": _one_of(schema.Auth),
        "access": _one_of(schema.Access),
        "access_note": "one sentence: how credentials are obtained",
        "api_styles": ["one or more of: " + " | ".join(get_args(schema.ApiStyle))],
        "api_breadth": _one_of(schema.Breadth),
        "api_note": "one sentence on what the API covers",
        "mcp": _one_of(schema.Mcp),
        "mcp_url": "URL of the MCP server or its docs, or empty string",
        "verdict": _one_of(schema.Verdict),
        "main_blocker": _one_of(schema.Blocker),
        "blocker_note": "one sentence, or empty string",
        "evidence": [
            {
                "field": _one_of(schema.EvidenceField),
                "url": "a URL listed in SOURCES",
                "quote": "exact words copied from that source, 20 words or fewer",
            }
        ],
        "confidence": _one_of(schema.Confidence),
        "unknowns": "what you could not determine, or empty string",
    },
    indent=1,
)


def build_prompt(app, sources):
    blocks = []
    for s in sources:
        blocks.append(f"[{s['label']}] ({s['kind']}) URL: {s['url']}\n{s['text']}")
    return (
        "You are a careful API researcher for Composio, a company that builds "
        "tool integrations for AI agents.\n\n"
        f"APP: {app['name']}\nCATEGORY: {app['category']}\nHINT: {app['hint']}\n"
        f"{RULES}\n"
        "HOW TO ANSWER\n"
        "- Base every answer on the SOURCES below. If they do not settle a field, "
        'answer "unknown", lower the confidence and explain in "unknowns".\n'
        "- Make sure the sources are about this exact app, not a different product "
        "with a similar name.\n"
        "- Every evidence url must be one of the SOURCES urls, and every quote must "
        "be copied exactly from that source.\n"
        "- Return one JSON object and nothing else, in exactly this shape:\n"
        f"{ANSWER_SHAPE}\n\n"
        "SOURCES\n\n" + "\n\n".join(blocks)
    )


def ask_gemini(prompt):
    global _last_llm
    for attempt in range(4):
        wait = GEMINI_GAP - (time.time() - _last_llm)
        if wait > 0:
            time.sleep(wait)
        _last_llm = time.time()
        try:
            r = gemini.models.generate_content(
                model=MODEL,
                contents=prompt,
                config={"response_mime_type": "application/json", "temperature": 0},
            )
            return r.text or ""
        except Exception as e:
            msg = str(e)
            if any(x in msg for x in ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE")):
                print(f"      model busy or rate limited, waiting 40s ({attempt + 1}/4)")
                time.sleep(40)
                continue
            raise
    raise RuntimeError("Gemini kept refusing; the daily free quota may be used up.")


def parse_record(text, app):
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    d = json.loads(t)
    if isinstance(d, list):
        d = d[0]
    d.update(id=app["id"], name=app["name"], category=app["category"])
    return AppResearch.model_validate(d)


# ---------- one app, start to finish ----------

def research_app(app):
    t0 = time.time()
    who = f"{app['name']} ({app['hint']})"
    queries = {
        "auth": f"{who} API authentication developer documentation",
        "access": f"{who} API access pricing plan get API key developer account",
        "mcp": f"{who} MCP server Model Context Protocol",
    }
    found = {k: search(q) for k, q in queries.items()}

    wanted = pick_pages(app, [found["auth"], found["access"]])
    pages = fetch(wanted)

    sources = []
    for u in wanted:
        if u in pages:
            sources.append(
                {"label": f"P{len(sources) + 1}", "kind": "full page", "url": u, "text": pages[u]}
            )
    n = 0
    for topic in ("auth", "access", "mcp"):
        for r in found[topic][:4]:
            n += 1
            sources.append(
                {
                    "label": f"S{n}",
                    "kind": f"search snippet, {topic} query",
                    "url": r["url"],
                    "text": r["content"][:SNIPPET_CHARS],
                }
            )

    prompt = build_prompt(app, sources)
    record, extra, attempts = None, "", 0
    for attempts in (1, 2):
        text = ask_gemini(prompt + extra)
        try:
            record = parse_record(text, app)
            break
        except Exception as e:
            extra = (
                "\n\nYour previous answer was rejected by the validator:\n"
                f"{str(e)[:800]}\nReturn corrected JSON only."
            )
    if record is None:
        raise RuntimeError("model output failed validation twice")

    _write(CACHE / "sources" / "pass1" / f"{app['id']}.json", sources)
    meta = {
        "pass": "pass1",
        "model": MODEL,
        "queries": queries,
        "sources": [{k: s[k] for k in ("label", "kind", "url")} for s in sources],
        "pages_read": [u for u in wanted if u in pages],
        "pages_unreadable": [u for u in wanted if u not in pages],
        "model_attempts": attempts,
        "seconds": round(time.time() - t0, 1),
    }
    return {"record": record.model_dump(), "meta": meta}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="", help="comma-separated app ids, e.g. 81,58")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", default="results/pass1")
    ap.add_argument("--force", action="store_true", help="redo apps already done")
    args = ap.parse_args()

    apps = load_apps()
    if args.ids:
        ids = {int(x) for x in args.ids.split(",") if x.strip()}
        apps = [a for a in apps if a["id"] in ids]
    elif not args.all:
        print("Give --ids 81,58 or --all")
        return

    out = ROOT / args.out
    done = skipped = failed = streak = 0
    for i, app in enumerate(apps, 1):
        target = out / f"{app['id']}.json"
        if target.exists() and not args.force:
            skipped += 1
            continue
        tag = f"[{i:>3}/{len(apps)}] #{app['id']:<3} {app['name']:<26}"
        try:
            result = research_app(app)
            _write(target, result)
            r, m = result["record"], result["meta"]
            print(
                f"{tag} auth={r['primary_auth']:<8} access={r['access']:<22} "
                f"verdict={r['verdict']:<16} conf={r['confidence']:<6} "
                f"pages={len(m['pages_read'])} ({m['seconds']}s)"
            )
            done += 1
            streak = 0
        except Exception as e:
            failed += 1
            streak += 1
            msg = f"{type(e).__name__}: {str(e)[:500]}"
            _write(out / "errors" / f"{app['id']}.json", {"app": app["name"], "error": msg})
            print(f"{tag} FAILED {msg}")
            if streak >= 3:
                print("Stopping: 3 apps failed in a row. Paste this output to Claude.")
                break
    print(f"\nFinished. done={done} skipped={skipped} failed={failed}")


if __name__ == "__main__":
    main()