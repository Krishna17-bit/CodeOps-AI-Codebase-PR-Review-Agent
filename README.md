# CodeOps AI — Codebase RAG + PR Review Agent

CodeOps AI is a local-first codebase intelligence workspace for repository analysis, architecture mapping, dependency review, risk detection, security smell detection, test gap review, generated starter tests, PR diff review, refactor planning, and README/report generation.

It is designed as a client-grade AI engineering demo, not a generic chatbot. The app can run fully with local deterministic analysis. When an AI reasoning key is configured locally, it can add senior-engineer narrative summaries and codebase Q&A.

## Features

- Upload GitHub repository ZIP
- Analyze included sample repo without uploading anything
- Repo architecture map
- Internal dependency graph
- Language and LOC dashboard
- Risky file detection
- Security smell detection
- Hardcoded secret pattern detection
- Unsafe subprocess, pickle, YAML, SQL, weak hash, CORS wildcard checks
- Test gap detection
- Generated starter pytest files
- Pull request diff reviewer
- Codebase Q&A over retrieved source chunks
- Refactor roadmap
- README/documentation generator
- Audit JSON, Markdown report, CSV findings, and generated tests ZIP exports

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -r requirements.txt
copy .env.example .env
streamlit run app.py
```

On macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
streamlit run app.py
```

## Optional local AI reasoning

Open `.env` and add your local key:

```env
GEMINI_API_KEY=your_api_key_here
GEMINI_MODEL=gemini-2.5-pro
```

The UI does not display provider or model branding. The app will still run without this key using deterministic local analysis.

## Test quickly

1. Keep **Use included sample repo** checked.
2. Click **Run codebase analysis**.
3. Open **Risk + Security**.
4. Open **Test Gap + Generated Tests**.
5. Open **PR Reviewer** and click **Review PR diff** using the preloaded sample diff.
6. Open **Ask Codebase** and ask: `Where is refund logic implemented and what risks exist?`
7. Open **Docs + Export** to download the audit package.

