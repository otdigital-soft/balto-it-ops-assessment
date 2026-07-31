# Balto IT Ops Assessment — Deploy & Submit Checklist

You have **48 hours** from the email. This repo already contains Tasks 1–5 content. You still need live Google links + a PDF.

## Blockers only you can complete

1. **Google account** — run the Form provisioner and bind the agent.
2. **LLM API key** — Gemini or OpenAI for Task 1C + Task 2 welcome messages.
3. **Export PDF** from the write-up (Google Doc recommended so Mermaid can be screenshots/images).
4. **Upload** via Frontier’s submission link.

## Deploy Task 1 (≈20 minutes)

1. Go to [script.google.com](https://script.google.com) → New project.
2. Paste `task1/create_form.gs` → run `createBaltoITRequestForm` → Authorize.
3. Copy `FORM_URL` and `SHEET_URL` from **Executions → Logs** into the write-up Artifact index.
4. Open the Sheet → Extensions → Apps Script → paste `task1/new_tool_eval_agent.gs`.
5. Project Settings → Script properties:
   - `GEMINI_API_KEY` = your key
   - `LLM_PROVIDER` = `gemini`
   - Optional: `SLACK_WEBHOOK_URL`
6. Triggers → Add trigger → `onITFormSubmit` → From form → On form submit.
7. Run `debugRunSampleLinear` once → screenshot Doc + `AgentRuns` row.
8. Form sharing: Anyone with link can **respond**. Sheet/Doc: Anyone with link **Viewer**.

## Run Task 2 with a real LLM (≈5 minutes)

```powershell
cd task2
pip install -r requirements.txt
$env:OPENAI_API_KEY = "sk-..."   # or GEMINI_API_KEY / ANTHROPIC_API_KEY
$env:LLM_PROVIDER = "openai"     # openai | gemini | anthropic
python onboard.py --out sample_output.txt
```

Paste a snippet of real welcome output into the PDF (or link the gist).

## Publish code links

Create a **public GitHub Gist or repo** with `task1/`, `task2/`, `task3/` and paste URLs into the Artifact index so reviewers can open them in Incognito.

## Build the PDF

**Recommended:** Copy `submission/Balto_IT_Ops_Assessment.md` into a Google Doc.

- Render Mermaid diagrams via [mermaid.live](https://mermaid.live) → paste screenshots under 1B.
- Paste live Form/Sheet/Doc/Gist URLs.
- File → Download → **PDF**.

Or: Word paste + export PDF. Only PDF is accepted by Frontier.

## Before upload

- [ ] Incognito can open every link without requesting access  
- [ ] Time log at top  
- [ ] 1C prompt is inline text  
- [ ] Task 2 run output attached/linked  
- [ ] Assumptions labeled  
- [ ] Filename like `Ryan_Balto_IT_Ops_Assessment.pdf`
