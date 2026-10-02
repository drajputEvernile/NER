# Start the Extraction Review UI

From the repo root (`E:\Projects\NER\NER`):

## Start backend

```powershell
.\.venv\Scripts\python.exe -m uvicorn Review_UI.backend.app:app --app-dir Extraction --host 127.0.0.1 --port 3000 --reload
```

## Start frontend

```powershell
cd Extraction\Review_UI\frontend
npm run dev
```

Then open http://127.0.0.1:3001. The home page lists every `KV_Run_*` run that has a
`KV_Extraction.xlsx` under `Run_Output` (`{Output_Root}/Runs`). Keep that workbook closed in Excel
while reviewing: the UI writes the reviews into it.

## Create a run

```powershell
.\.venv\Scripts\python.exe Extraction\run.py --fresh
```

Reruns of an existing run can be started from the home page (Rerun button) or with
`run.py --fresh --records-from KV_Run_... --model-version v003`.
