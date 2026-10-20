# ApplyTrack AI: a job-application tracker you talk to

![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB)
![OpenAI Agents SDK](https://img.shields.io/badge/OpenAI-Agents%20SDK-412991)
![Streamlit](https://img.shields.io/badge/Streamlit-UI-FF4B4B)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

An agent that keeps your job-application spreadsheet in sync with reality.
Ask it "did I hear back from anyone?" and it reads your Google Sheet, skips
applications that are already closed, searches Gmail once per company still
in play, and updates the rows that changed. Give it an application link and
it opens the form in a real browser, fills what it can from your profile,
and leaves the rest for you to review. Nothing is submitted unless you say so.

## What it does

- **Keeps the tracker in sync with Gmail.** One scoped Gmail search per
  active company, starting from the last known date for that application
  (interview date, else application date, else 60 days back). Rows marked
  Rejected, Withdrawn, Offer Accepted, Declined or Ghosted are skipped.
  Gmail timeouts are retried, and companies that still fail are reported
  back instead of dropped.
- **Edits the sheet in plain language.** Add, update or remove
  applications; it reads before writing and reads again to verify.
- **Formats and analyses.** An `execute_code` tool runs Python against the
  Sheets API for formatting, conditional colours, charts and pivot tables.
- **Fills application forms.** Playwright opens Google Forms or ordinary
  HTML forms in a visible Chrome window, fills matching fields from your
  profile (heuristic and fuzzy label matching), and hands the unmatched
  fields to the model. `submit_form` is a separate tool, only used when you
  ask for that application to be submitted.
- **Remembers the conversation** across runs (OpenAI Agents SDK
  `SQLiteSession`).
- **Shows its work live.** The Streamlit UI streams the agent's narration
  and each tool call as it happens.

## Architecture

```
Streamlit UI (app.py) / CLI (main.py)
          │
          ▼
   Tracker Agent  (OpenAI Agents SDK, gpt-5-mini, SQLite session memory)
          │
   ┌──────┼───────────────┬──────────────────┐
   ▼      ▼               ▼                  ▼
 Sheets  execute_code   Gmail tools        Form-filling tools
 tools   (Sheets API)   (read-only scope)  (Playwright + Chrome)
```

`src/tools/` holds one file per tool, `src/services/` the Google API
clients, and `src/prompt/tracker_prompt.py` the agent's instructions:
which tools to use for which kind of request, and the rules (never invent
a status, verify every write, ask when a company or row is ambiguous).

## Run it

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), Google Chrome and
a Google Cloud project with the Sheets, Drive and Gmail APIs enabled.

```bash
git clone https://github.com/Harsh251005/applytrack-ai.git
cd applytrack-ai
uv sync
uv run playwright install chrome

cp .env.example .env                    # OpenAI key, spreadsheet ID/URL, credential paths
cp profile.example.json profile.json    # your details, used to auto-fill forms
```

Put your OAuth client file (Desktop app) at the path set in
`GOOGLE_CREDENTIALS_PATH`. The first run opens a browser to grant access and
saves a token at `GOOGLE_TOKEN_PATH`.

```bash
uv run streamlit run app.py     # chat UI
uv run python main.py           # or the terminal version
```

The tracker sheet needs a header row with at least **Company Name**,
**Status**, **Application Date** and **Interview Date**.

To fill forms behind a Google sign-in, sign in once by hand in Chrome using
the profile folder `~/.applytrack_ai/browser_profile` (Google blocks sign-in
while Playwright is driving the browser); the agent reuses that session.

## Stack

**Agent:** OpenAI Agents SDK, gpt-5-mini.
**Integrations:** Google Sheets, Drive and Gmail APIs (OAuth), Playwright.
**App:** Python 3.12, Streamlit, Pydantic, uv.

## Status

Runs locally against your own Google account; there is no hosted version.
There are no automated tests yet beyond a Google sign-in check.
