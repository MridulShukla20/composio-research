"""Human answer key: check 20 apps by hand, blind to the agent's answers.

Run:  python label.py          (resumes where you stopped)
      python label.py --redo 81   (re-do one app)
"""
import json
import random
import sys
from pathlib import Path

from schema import load_apps

sys.stdout.reconfigure(errors="replace")
OUT = Path(__file__).parent / "golden" / "golden.json"
SEED = 42

FIELDS = [
    ("primary_auth", "Main auth method the official docs recommend for a third-party integration", [
        ("oauth2", "OAuth 2.0 flow (a user clicks Allow)"),
        ("api_key", "static key or secret created in a dashboard"),
        ("basic", "HTTP Basic (username/password or key:secret)"),
        ("token", "personal access token, bot token, JWT, other bearer token"),
        ("other", "signed requests, sessions, anything else"),
        ("none", "no credentials at all (local tool)"),
        ("unknown", "official docs do not say / could not find"),
    ]),
    ("access", "How a developer gets working API credentials", [
        ("self_serve_free", "sign up and get credentials at no cost"),
        ("self_serve_trial", "self sign-up, only inside a time-limited trial"),
        ("paid_plan", "API only on a paid plan or paid add-on"),
        ("approval_required", "self sign-up, but vendor must review/approve for real use"),
        ("partner_or_sales_gated", "only via partnership, enterprise contract or sales"),
        ("not_applicable", "nothing to obtain (open-source or local tool)"),
        ("unknown", "could not find"),
    ]),
    ("mcp", "Is there an MCP server for this app?", [
        ("official", "published by the vendor itself (vendor site or vendor GitHub)"),
        ("community", "only third-party MCP servers exist"),
        ("none_found", "searched and found none"),
        ("unknown", "could not tell"),
    ]),
    ("verdict", "Could Composio build an agent toolkit for this today?", [
        ("yes", "public docs and self-serve credentials"),
        ("yes_with_caveats", "possible, with a real hurdle (paid plan, review, narrow API)"),
        ("no", "blocked (gated, no public API, not a hosted API)"),
        ("unknown", "could not tell"),
    ]),
]


def sample():
    apps = load_apps()
    rng = random.Random(SEED)
    categories = []
    for a in apps:
        if a["category"] not in categories:
            categories.append(a["category"])
    picked = []
    for c in categories:
        picked += rng.sample([a for a in apps if a["category"] == c], 2)
    return sorted(picked, key=lambda a: a["id"])


def choose(question, options):
    print(f"\n  {question}")
    for i, (value, gloss) in enumerate(options, 1):
        print(f"    {i}. {value:<24} {gloss}")
    while True:
        raw = input("    Type a number: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        print("    Not a valid number, try again.")


def main():
    done = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    if len(sys.argv) == 3 and sys.argv[1] == "--redo":
        done.pop(sys.argv[2], None)

    picked = sample()
    print(f"Sample of {len(picked)} apps (seed {SEED}, 2 per category):")
    for a in picked:
        mark = "done" if str(a["id"]) in done else "    "
        print(f"  [{mark}] #{a['id']:<3} {a['name']:<26} {a['hint']}")

    for a in picked:
        if str(a["id"]) in done:
            continue
        print("\n" + "=" * 70)
        print(f"#{a['id']} {a['name']}   ({a['category']})")
        print(f"Start from: {a['hint']}")
        if input("Press Enter when you have checked the docs (or type s to skip for now): ").strip().lower() == "s":
            continue
        entry = {"name": a["name"]}
        for key, question, options in FIELDS:
            entry[key] = choose(question, options)
        entry["evidence_url"] = input("\n  Paste the main docs URL you relied on: ").strip()
        entry["note"] = input("  Optional note (anything unclear), or just Enter: ").strip()
        entry["checked_by"] = "human"
        done[str(a["id"])] = entry
        OUT.parent.mkdir(exist_ok=True)
        OUT.write_text(json.dumps(done, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"  Saved. {len(done)}/{len(picked)} done.")

    print(f"\nAnswer key has {len(done)}/{len(picked)} apps.")


if __name__ == "__main__":
    main()