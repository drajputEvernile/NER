# Start the KV Extraction UI

From the repo root (`E:\Projects\NER`):

## Start backend

```powershell
.\.venv\Scripts\python.exe -m uvicorn Review_UI.backend.app:app --app-dir KV_Extraction --host 127.0.0.1 --port 3000 --reload
```

## Start frontend

```powershell
cd KV_Extraction\Review_UI\frontend
npm run dev
```

Then open http://127.0.0.1:3001 — the home page lists every `KV_Run_*` batch under `Run_Output` (`{Output_Root}/Runs`).

## Create a batch

```powershell
.\.venv\Scripts\python.exe KV_Extraction\run.py --fresh
```

Reruns of an existing batch can be started from the home page (Rerun button) or with
`run.py --fresh --records-from KV_Run_... --model-version v0`.
