# Start the Data Set Chooser

Set the paths and `N` in `Data Set Chooser/config.py` first (absolute paths only):

| Setting | Meaning |
|---|---|
| `Raw_Read_Path` | folder of records to choose from: `{Raw_Read_Path}/{RecordId}/{page images}` |
| `Selected_Path` | where selected records are copied: `{Selected_Path}/{RecordId}/{page images}` |
| `N` | only records with N or fewer pages are listed (`0` = no limit) |

From the repo root (`E:\Projects\NER\NER`):

## One process (serves the built UI)

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --app-dir "Data Set Chooser" --host 127.0.0.1 --port 3002
```

Open http://127.0.0.1:3002.

## UI changes (dev server)

Run the backend as above, then:

```powershell
cd "Data Set Chooser\frontend"
npm install      # first time only
npm run dev      # http://127.0.0.1:3003
npm run build    # rebuild dist/ for the one-process mode
```

## Using it

Pick a record on the left, page through its images, and press **Select for training**: the record's
page images are copied to `Selected_Path/{RecordId}`. A selected record shows **Selected · Remove**;
Remove deletes only those copies (never the raw images). Selection is read from the folder itself, so
restarting loses nothing.
