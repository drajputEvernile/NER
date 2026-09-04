# Member verification

OCR chart pages, then rule-based + NER member verification. Run every command from the repo root with the repo `.venv`.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

Fill Azure keys in `.env` only if you use Azure OCR. Copy `Data/Raw/system_input.csv` and put page images in `Data/Raw/{RecordId}/`.

Turn engines and NER models on or off in `.env`:

```
DOCLING_OCR=true
AZURE_OCR=false
GLINER_LARGE=true
GLINER_MEDIUM=true
GLINER_LOW=true
DISTILROBERTA_BASE_NER=true
```

## Run OCR

Uses Docling and/or Azure based on `.env`. Writes JSON and TXT under each record’s OCR output folder. Existing JSON files are reused.

```powershell
.\.venv\Scripts\python.exe "src\OCR\run.py"
```

## Run member verification

Runs OCR (cached pages skip conversion), then verification for every enabled NER model.

```powershell
.\.venv\Scripts\python.exe "src\Member Verification\run.py"
```

Same pipeline:

```powershell
.\.venv\Scripts\python.exe src\run.py
```

Outputs per record:

- `Data/output/{RecordId}/Docling_OCR_Output/`
- `Data/output/{RecordId}/Member_Verification_Output/{model}.csv`
- `Data/output/{RecordId}/ner_output/{model}.csv`

## Download NER models

Hugging Face is used only here. Inference stays local.

All four:

```powershell
$env:HF_HUB_DISABLE_XET='1'
.\.venv\Scripts\python.exe "src\Member Verification\extractors\ner_based\ner_models\model_downloader\__main__.py"
```

One model at a time:

```powershell
$env:HF_HUB_DISABLE_XET='1'
.\.venv\Scripts\python.exe "src\Member Verification\extractors\ner_based\ner_models\model_downloader\gliner_large_v2_1.py"
.\.venv\Scripts\python.exe "src\Member Verification\extractors\ner_based\ner_models\model_downloader\gliner_medium_v2_1.py"
.\.venv\Scripts\python.exe "src\Member Verification\extractors\ner_based\ner_models\model_downloader\gliner_low.py"
.\.venv\Scripts\python.exe "src\Member Verification\extractors\ner_based\ner_models\model_downloader\distilroberta_base_ner.py"
```

| Script | Local folder |
|---|---|
| `gliner_large_v2_1.py` | `src/Member Verification/extractors/ner_based/ner_models/models/gliner_large-v2.1/` |
| `gliner_medium_v2_1.py` | `src/Member Verification/extractors/ner_based/ner_models/models/gliner_medium-v2.1/` |
| `gliner_low.py` | `src/Member Verification/extractors/ner_based/ner_models/models/gliner_low/` |
| `distilroberta_base_ner.py` | `src/Member Verification/extractors/ner_based/ner_models/models/distilroberta-base-ner/` |

## Change paths

Paths are not in `.env`. Edit the `config.py` in the folder you are using. Values are relative to the repo root unless you pass an absolute path to `repo_path(...)`.

**OCR** — `src/OCR/config.py` and the matching engine file:

| Variable | File | Default |
|---|---|---|
| `RAW_Read_Path` | `src/OCR/config.py`, `src/OCR/Docling OCR/config.py`, `src/OCR/Azure OCR/config.py` | `Data/Raw` |
| `Docling_OCR_Output_path` | `src/OCR/config.py`, `src/OCR/Docling OCR/config.py` | `Data/output` |
| `Docling_OCR_Folder` | same | `Docling_OCR_Output` |
| `Azure_OCR_Output_path` | `src/OCR/config.py`, `src/OCR/Azure OCR/config.py` | `Data/output` |
| `Azure_OCR_Folder` | same | `Azure_OCR_Output` |

**Member verification** — `src/Member Verification/config.py`:

| Variable | Default |
|---|---|
| `RAW_Read_Path` | `Data/Raw` |
| `System_Input_path` | `Data/Raw/system_input.csv` |
| `OCR_Read_path` | `Data/output` |
| `OCR_Read_Folder` | `Docling_OCR_Output` |
| `MV_Output_path` | `Data/output` |
| `MV_Output_Folder` | `Member_Verification_Output` |
| `NER_Output_path` | `Data/output` |
| `NER_Output_Folder` | `ner_output` |

Example: point verification at Azure OCR text by setting `OCR_Read_Folder = "Azure_OCR_Output"`.

**NER weights** — `src/Member Verification/extractors/ner_based/config.py`:

| Variable | Default |
|---|---|
| `NER_MODELS_PATH` | `src/Member Verification/extractors/ner_based/ner_models/models` |

Keep folder names in the Docling, Azure, and member-verification configs in sync if you change them in more than one file.
