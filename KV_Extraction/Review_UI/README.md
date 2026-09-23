# KV Extraction Manual Review UI

Same Advantmed visual language as the imaging pipeline, but **Manual Review only**.

## Paths (`KV_Extraction/Util/config.py`)

| Setting | Purpose |
|---|---|
| `Raw_Input` | Original page images (`{Raw_Input}/{RecordId}/{fileName}`) |
| `OCR_Input` | OCR JSON for the OCR Data tab |
| `Local_Output` | Parent folder of runs; used when `Review_Run` is `None` |
| `Review_Run` | Optional pin to one run folder, e.g. `...\KV_Output\KV_Run_20260923_131233` |

Set `Review_Run` to the folder that contains `extraction.xlsx` (and overlays). Set it to `None` to open the latest `KV_Run_*` under `Local_Output`. The top bar shows `KV Extraction - Manual Review - {run name}`.

Restart the API after changing config (or run uvicorn with `--reload`).

## Run

Terminal 1 — API (port 5175):

```text
.\.venv\Scripts\python.exe -m uvicorn Review_UI.backend.app:app --app-dir KV_Extraction --host 127.0.0.1 --port 5175 --reload
```

Terminal 2 — UI (port 5174):

```text
cd KV_Extraction\Review_UI\frontend
npm install
npm run dev
```

Open http://127.0.0.1:5174

## What it loads from a run folder

- `extraction.xlsx` (sheet `Extraction`) — values to review (read-only for reviews)
- `overlays/` — Overall / per-field image tabs
- `reviews.json` — saved review payloads (created on first save)
- `manual_review.xlsx` — written on save (separate from extraction.xlsx)

Images also come from `Raw_Input` (Original tab). OCR text comes from `OCR_Input`.

## How review output is shown / saved

1. **In the UI** — ✓ / ✗ per hit. Incorrect also requires **Reason For Incorrect** and **Actual Correct Value**. After Save: green tab dot + Reviewed banner.
2. **`reviews.json`** — hit id → `{ accuracy, reason, actual_value }`.
3. **`manual_review.xlsx`** — sheets for training:
   - `DOB`, `Member_ID`, `Member_Name`, `Provider_Name`, `E_Signature`
   - `Missed_Keys` — keys the reviewer says were missed on a page

   Field / image ribbon tabs only appear when that extractor has hits on the current page.
   **Missed Keys** is always available: extractor dropdown + key text, Add another, Save.
