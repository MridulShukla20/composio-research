"""Phase 1 check: proves the three API keys work. Prints no secrets."""
import os
from dotenv import load_dotenv

load_dotenv()
PROMPT = "Reply with the single word: OK"


def have(name):
    ok = bool(os.getenv(name, "").strip())
    print(f"{name}: {'found' if ok else 'MISSING in .env'}")
    return ok


def fail(e):
    print("FAILED:", type(e).__name__, str(e)[:600])


print("--- Keys ---")
keys = {n: have(n) for n in ["GEMINI_API_KEY", "GROQ_API_KEY", "COMPOSIO_API_KEY"]}

print("\n--- Gemini (researcher model) ---")
if keys["GEMINI_API_KEY"]:
    try:
        from google import genai

        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"].strip())
        names = sorted(m.name for m in client.models.list())
        flash = [n for n in names if "flash" in n]
        print("Flash models on your account:")
        for n in flash:
            print("  ", n)
        skip = ("tts", "image", "live", "audio", "embedding")
        lite = [n for n in flash if "flash-lite" in n and not any(s in n for s in skip)]
        if lite:
            pick = lite[-1]
            print("Test call using:", pick)
            r = client.models.generate_content(model=pick, contents=PROMPT)
            print("Gemini replied:", (r.text or "").strip())
        else:
            print("No flash-lite model found; paste this output to Claude.")
    except Exception as e:
        fail(e)

print("\n--- Groq (verifier model) ---")
if keys["GROQ_API_KEY"]:
    try:
        from groq import Groq

        client = Groq(api_key=os.environ["GROQ_API_KEY"].strip())
        ids = sorted(m.id for m in client.models.list().data)
        print("Models on your account:")
        for i in ids:
            print("  ", i)
        if "openai/gpt-oss-120b" in ids:
            pick = "openai/gpt-oss-120b"
        else:
            pick = next((i for i in ids if "llama" in i), ids[0])
        print("Test call using:", pick)
        r = client.chat.completions.create(
            model=pick, messages=[{"role": "user", "content": PROMPT}]
        )
        print("Groq replied:", (r.choices[0].message.content or "").strip())
    except Exception as e:
        fail(e)

print("\n--- Composio (search tools) ---")
if keys["COMPOSIO_API_KEY"]:
    try:
        from composio import Composio

        composio = Composio(api_key=os.environ["COMPOSIO_API_KEY"].strip())
        tools = composio.tools.get(user_id="research-agent", toolkits=["COMPOSIO_SEARCH"])
        found = []
        for t in tools:
            if isinstance(t, dict):
                found.append(t.get("function", {}).get("name") or t.get("name"))
            else:
                found.append(getattr(t, "name", str(t)))
        print(f"Composio returned {len(found)} search tools:")
        for n in found:
            print("  ", n)
    except Exception as e:
        fail(e)

print("\nDone.")