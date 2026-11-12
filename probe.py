"""Phase 3 probe: learn the inputs and outputs of the two Composio tools."""
import json
import os

from composio import Composio
from dotenv import load_dotenv

load_dotenv()
USER = "research-agent"
SEARCH = "COMPOSIO_SEARCH_TAVILY"
FETCH = "COMPOSIO_SEARCH_FETCH_URL_CONTENT"

composio = Composio(api_key=os.environ["COMPOSIO_API_KEY"].strip())


def show(label, obj, limit=2500):
    text = json.dumps(obj, indent=1, default=str)
    print(f"\n===== {label} =====")
    print(text[:limit])
    if len(text) > limit:
        print(f"... [{len(text) - limit} more characters cut]")


schemas = {}
try:
    for t in composio.tools.get(user_id=USER, tools=[SEARCH, FETCH]):
        fn = t.get("function", t) if isinstance(t, dict) else {}
        name = fn.get("name")
        params = fn.get("parameters", {})
        schemas[name] = params
        slim = {
            k: {"type": v.get("type"), "description": str(v.get("description", ""))[:160]}
            for k, v in params.get("properties", {}).items()
        }
        show(f"INPUTS of {name} (required: {params.get('required')})", slim)
except Exception as e:
    print("FAILED to read tool schemas:", type(e).__name__, str(e)[:600])


def run(slug, arguments):
    print(f"\nCalling {slug} with {arguments}")
    try:
        result = composio.tools.execute(
            slug,
            user_id=USER,
            arguments=arguments,
            dangerously_skip_version_check=True,
        )
        show(f"RESULT of {slug}", result)
    except Exception as e:
        print("FAILED:", type(e).__name__, str(e)[:800])


search_props = schemas.get(SEARCH, {}).get("properties", {})
query_key = "query" if "query" in search_props or not search_props else list(search_props)[0]
run(SEARCH, {query_key: "Stripe API authentication official docs"})

fetch_props = schemas.get(FETCH, {}).get("properties", {})
target = "https://docs.stripe.com/keys"
if "urls" in fetch_props:
    run(FETCH, {"urls": [target]})
else:
    run(FETCH, {"url": target})

print("\nDone.")