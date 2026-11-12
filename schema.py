"""The single definition of what the agent must find out about each app.

Every other script imports from here, so the researcher, the verifier,
the scorer and the web page all agree on field names and allowed values.
"""
import json
from pathlib import Path
from typing import List, Literal

from pydantic import BaseModel, Field

Auth = Literal["oauth2", "api_key", "basic", "token", "other", "none", "unknown"]
Access = Literal[
    "self_serve_free",
    "self_serve_trial",
    "paid_plan",
    "approval_required",
    "partner_or_sales_gated",
    "not_applicable",
    "unknown",
]
ApiStyle = Literal["rest", "graphql", "other", "none", "unknown"]
Breadth = Literal["broad", "moderate", "narrow", "none", "unknown"]
Mcp = Literal["official", "community", "none_found", "unknown"]
Verdict = Literal["yes", "yes_with_caveats", "no", "unknown"]
Blocker = Literal[
    "none",
    "partner_or_sales_gate",
    "paid_plan",
    "approval_or_app_review",
    "no_public_api",
    "not_a_hosted_api",
    "docs_not_found",
    "other",
]
Confidence = Literal["high", "medium", "low"]
EvidenceField = Literal["one_liner", "auth", "access", "api", "mcp", "verdict"]

# Plain-language rules. This exact text is given to the AI models, and it is
# also the rulebook the human uses when hand-checking the answer key.
RULES = """
FIELD RULES (follow exactly; when the pages you read do not say, answer "unknown")

auth_methods: every method the official docs say the API accepts.
  oauth2  = OAuth 2.0 authorization flow (user clicks "Allow", app gets tokens).
  api_key = a static key or secret generated in a dashboard and tied to an account or project.
  basic   = HTTP Basic auth (username/password or key:secret, base64 encoded).
  token   = personal access token, bot token, JWT or other bearer token that is not OAuth2.
  other   = anything else (signed requests such as AWS SigV4 or HMAC, sessions, mTLS).
  none    = no credentials needed at all (for example a local command-line tool).
primary_auth: the one method the docs recommend for a third-party integration.
  If the docs offer OAuth2 for public or multi-user apps, choose oauth2.

access: how a developer gets working API credentials.
  self_serve_free        = sign up and get credentials at no cost (free plan, sandbox or developer account).
  self_serve_trial       = self sign-up, but only inside a time-limited free trial.
  paid_plan              = API access exists only on a paid plan or paid add-on.
  approval_required      = self sign-up, but real use needs the vendor to review or approve the app or account.
  partner_or_sales_gated = credentials only through a partnership, an enterprise contract or contacting sales.
  not_applicable         = nothing to obtain (open-source or local tool with no hosted API).

api_styles: rest, graphql, other (SOAP, gRPC, SDK-only, CLI-only), none.
api_breadth: broad = most of the product is covered, read and write (roughly 50+ operations).
  moderate = the core objects are covered (roughly 10 to 50 operations).
  narrow = a handful of operations, or read-only. none = no public API.

mcp: official = an MCP server published by the vendor itself.
  community = only third-party MCP servers exist. none_found = you searched and found none.

verdict: could Composio build an agent toolkit for this app today?
  yes = public docs and self-serve credentials. yes_with_caveats = possible, with a real hurdle
  (paid plan, app review, narrow API). no = blocked. main_blocker = the single biggest hurdle, or none.

evidence: at least one entry each for auth, access and api. Every quote must be copied
  word for word from the page at that URL, 20 words or fewer. Never invent a URL or a quote.
confidence: high = stated directly in official docs you read. medium = partly inferred.
  low = based on third-party pages or weak signals.
"""


class Evidence(BaseModel):
    field: EvidenceField
    url: str
    quote: str = Field(description="Exact phrase copied from the page, 20 words or fewer")


class AppResearch(BaseModel):
    id: int
    name: str
    category: str
    one_liner: str = Field(description="What the app does, in one line")
    auth_methods: List[Auth]
    primary_auth: Auth
    access: Access
    access_note: str = Field(description="One sentence: how credentials are obtained")
    api_styles: List[ApiStyle]
    api_breadth: Breadth
    api_note: str = Field(description="One sentence on what the API covers")
    mcp: Mcp
    mcp_url: str = ""
    verdict: Verdict
    main_blocker: Blocker
    blocker_note: str = ""
    evidence: List[Evidence]
    confidence: Confidence
    unknowns: str = Field(default="", description="What could not be determined")


def load_apps():
    """Read the 100 apps from apps.json."""
    path = Path(__file__).parent / "apps.json"
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    apps = load_apps()
    per_category = {}
    for a in apps:
        per_category[a["category"]] = per_category.get(a["category"], 0) + 1

    assert len(apps) == 100, f"expected 100 apps, found {len(apps)}"
    assert sorted(a["id"] for a in apps) == list(range(1, 101)), "ids must be 1 to 100"
    assert len(per_category) == 10, f"expected 10 categories, found {len(per_category)}"
    assert all(n == 10 for n in per_category.values()), per_category
    print(f"Apps: {len(apps)} in {len(per_category)} categories, 10 each: OK")

    # A made-up record, only to prove the form accepts a correctly filled answer.
    AppResearch(
        id=0, name="ExampleApp", category="Example", one_liner="Format example only.",
        auth_methods=["oauth2", "api_key"], primary_auth="oauth2",
        access="self_serve_free", access_note="Example.",
        api_styles=["rest"], api_breadth="broad", api_note="Example.",
        mcp="none_found", verdict="yes", main_blocker="none",
        evidence=[{"field": "auth", "url": "https://example.com/docs", "quote": "example"}],
        confidence="high",
    )
    print("Schema accepts a valid record: OK")

    # The form must also reject a value that is not on the allowed list.
    try:
        AppResearch(
            id=0, name="Bad", category="Example", one_liner="x",
            auth_methods=["password"], primary_auth="oauth2",
            access="self_serve_free", access_note="x",
            api_styles=["rest"], api_breadth="broad", api_note="x",
            mcp="none_found", verdict="yes", main_blocker="none",
            evidence=[], confidence="high",
        )
        print("PROBLEM: schema accepted an invalid value")
    except Exception:
        print("Schema rejects an invalid value: OK")