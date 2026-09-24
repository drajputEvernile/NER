# Start Manual Review UI

From the repo root (`E:\Projects\NER`):

## Start backend

```powershell
.\.venv\Scripts\python.exe -m uvicorn Review_UI.backend.app:app --app-dir KV_Extraction --host 127.0.0.1 --port 5175 --reload
```

## Start frontend

```powershell
cd KV_Extraction\Review_UI\frontend
npm run dev
```

Then open http://127.0.0.1:5174

## Master Data: select records (optional CLI)

Eligible = folders under `Raw_Input` with ≤ `Master_Data_Max_Pages` (config). Writes `Data/Master_Data/selected_records.json`.

```powershell
.\.venv\Scripts\python.exe -m Master_Data_Builder.select_records -N 20
```

(Or use **Select records** inside the Master Data Builder UI.)
