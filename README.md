# 100 apps, one research agent

Take-home for Composio's AI Product Ops Intern role. An agent researches 100 apps
(auth, access, API surface, MCP, buildability), a verification loop tries to break
its answers, and a human-labelled sample measures accuracy.

**Live case study:** https://YOUR_USERNAME.github.io/composio-research/

## Result

- 100 of 100 apps researched and verified.
- Accuracy on 78 hand-checked answers (20 apps): 80.8% after pass 1, 85.9% after verification.
  Verification fixed 5 answers and broke 1.
- Every number on the page is computed by `build.py` from the files in `results/` and `golden/`.

## How it works

| Step | Script | What it does |
|---|---|---|
| 1 | `research.py` | Per app: 3 web searches and up to 3 full pages via Composio tools, then Gemini fills a fixed schema with quoted evidence. |
| 2 | `label.py` | Human answer key: 20 apps sampled at random (2 per category, seed 42). |
| 3 | `verify.py` | Audit in plain code (quote really on the page? vendor-owned page? fields consistent?), re-research on vendor domains, a second model as skeptic, and a computed trust score. |
| 4 | `score.py` | Scores pass 1 and pass 2 against the answer key. |
| 5 | `build.py` | Generates `docs/index.html` and `docs/data.json`. |

`schema.py` defines every field and its allowed values. The same rulebook text is given
to the models and used by the human labeller.

## Run it

Needs Python 3.10 or later and three free API keys.

```
python -m pip install -r requirements.txt
copy .env.example .env
python hello.py
python research.py --all
python verify.py --all
python score.py
python build.py
```

After copying `.env.example` to `.env`, paste your keys into `.env`. `hello.py` checks
that all three keys work.

Keys: a Composio **project** API key (starts with `ak_`), a Gemini key from Google AI Studio,
and a Groq key. All three have free tiers. A full run takes roughly 90 minutes on free tiers
because of rate limits. Runs are resumable: finished apps are skipped.

To try it on a few apps first: `python research.py --ids 81,58,90`

## Where a human was needed, and known limits

Both are listed on the case-study page under "The agent" and "Limits". The short version:
the answer key is human-labelled with AI-assisted review, the sample is small, access is the
weakest field, and one correct answer (Jira's MCP) was broken by a bug in the verifier that
was left unfixed so the score is not tuned against the answer key.

## Tools

Composio SDK (`COMPOSIO_SEARCH_TAVILY`, `COMPOSIO_SEARCH_FETCH_URL_CONTENT`),
Gemini Flash-Lite as researcher, GPT-OSS 120B on Groq as skeptic, Pydantic for validation.
Built with AI assistance (Claude) for code and review.