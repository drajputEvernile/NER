# Member verification

OCR chart pages, then rule-based + NER member verification. Run every command from the repo root with the repo `.venv`.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

## Layout

| Folder | Role |
|---|---|
| `Azure_OCR/` | Azure Document Intelligence → blob OCR JSON |
| `Docling_OCR/` | Local Docling OCR → `Data/output/...` |
| `Member_Verification/` | Verification (reads OCR JSON from blob only) |
| `Models/` | NER weights + `model_downloader/` |
| `azure_blob/` | Shared blob helpers |

Blob path prefixes live only in `.env` (`AZURE_OCR_RAW_STORAGE_PREFIX`, `AZURE_OCR_STORAGE_WRITE_PREFIX`).

## Download NER models

```powershell
$env:HF_HUB_DISABLE_XET='1'
.\.venv\Scripts\python.exe Models\model_downloader\__main__.py
```

Individual models:

```powershell
.\.venv\Scripts\python.exe Models\model_downloader\gliner_large_v2_1.py
.\.venv\Scripts\python.exe Models\model_downloader\gliner_medium_v2_1.py
.\.venv\Scripts\python.exe Models\model_downloader\gliner_low.py
```

## Run local Docling OCR

```powershell
.\.venv\Scripts\python.exe Docling_OCR\run.py
```

Output: `Data/output/{RecordId}/Docling_OCR_Output/{RecordId}.json`

## Run Azure OCR (blob)

```powershell
.\.venv\Scripts\python.exe Azure_OCR\run.py
```

Reads images from `AZURE_OCR_RAW_STORAGE_PREFIX`, writes OCR JSON to `AZURE_OCR_STORAGE_WRITE_PREFIX`.
On start it tallies folders/pages into `Azure_OCR/azure_ocr_progress.json` (queue + progress), then processes **one record / one page at a time** (sequential).
Pages are saved sorted by `fileName` ascending (`pageNumber` = 1..N).

## Run member verification

Reads OCR from blob (`AZURE_OCR_STORAGE_WRITE_PREFIX`).

```powershell
.\.venv\Scripts\python.exe Member_Verification\run.py
```

Selected docs only (page count ≤ `MAX_PAGES` in `.env`):

```powershell
.\.venv\Scripts\python.exe Member_Verification\run_selected.py
```

Outputs:

- `{MV_OUTPUT_PATH}/{RecordId}/member_verification/{model}.csv`
- `{NER_OUTPUT_PATH}/{RecordId}/ner/{model}.csv`

Default roots: `E:\Projects\NER\Data\output`

## NER toggles (`.env`)

```
GLINER_LARGE=true
GLINER_MEDIUM=true
GLINER_LOW=true
```
