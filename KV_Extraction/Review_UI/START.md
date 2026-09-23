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
