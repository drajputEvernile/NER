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

Like Azure OCR, the run first tallies the records into a queue
(`Member_Verification/mv_progress.json`) and then works through it **one record
at a time**. Rows are staged in `Member_Verification/mv_staging/` as records
finish, so stopping and rerunning resumes where it left off.

Output is written **batch-wise** at the end of the run: one folder named after
the end timestamp of the operation, under the single `MV_OUTPUT_PATH` root, with
one CSV per model covering every record in the batch.

- `{MV_OUTPUT_PATH}/{YYYYMMDD_HHMMSS}/member_verification_{model}.csv`
- `{MV_OUTPUT_PATH}/{YYYYMMDD_HHMMSS}/ner_{model}.csv`

Default root: `E:\Projects\NER\Data\output`

There are **no per-record folders**: each CSV holds every record in the batch,
one row per page. Both files lead with `RecordId` (the NER CSV also carries
`Page_No`) so rows stay traceable now that records share a file. Each run
writes its own timestamped folder, so earlier batches are never overwritten.

One pair of CSVs is written per enabled model, so with all three NER toggles on
a batch folder holds six files:

```
20260909_221419/
  member_verification_gliner_large.csv    ner_gliner_large.csv
  member_verification_gliner_medium.csv   ner_gliner_medium.csv
  member_verification_gliner_low.csv      ner_gliner_low.csv
```

## Accept / reject rules

Each page lands in one bucket (`Member_Verification/Rules/what_if_rules.py`):

| Bucket | Meaning | Counts against the document |
|---|---|---|
| `Verified` | expected member matched | no |
| `Wrong_Member` | a patient-name context names someone else | yes |
| `Not_Verified` | nothing detected (or the right name without corroboration) | no |

The whole document is rejected once the wrong-member pages reach **5 pages or
10% of the pages, whichever comes first**. A page that failed verification
because nothing was detected is not considered. Delete / move / split handling
is not implemented.

Because a wrong-member page can reject a whole chart, that signal is only taken
from a patient-name context (`Rules/wrong_member_rules.py`): the people NER
recognised in the sentence around a patient-name key. If none of them verifies
as the expected member, the page carries a wrong member. An attending
physician or signer who shares the member's surname is never counted, because
they sit outside the name sentence. Only when NER recognises nobody does a
key-anchored token read stand in, so the rules still work with no model loaded.

## Key sentences

`extractors/ner_based/keys.py` cuts a real sentence out of the page around each
key ("Patient Name: Robert Smith") and NER reads that. Sentences end at a
newline, a run of spaces (OCR's column separator), a full stop or a pipe, and
when a key ends its line the value underneath is pulled in, so all of these
give NER the same clean input:

| Page text | Sentence NER reads |
|---|---|
| `Patient Name: Maria Garcia  DOB: 1/15/1980` | `Patient Name: Maria Garcia` |
| `Patient Name:` ⏎ `  Maria Garcia` | `Patient Name: Maria Garcia` |
| `PATIENT NAME` ⏎ `Maria Garcia` | `PATIENT NAME Maria Garcia` |
| `Pt Name: Maria Garcia \| DOB: 1/15/1980` | `Pt Name: Maria Garcia` |

The name, DOB and member-ID NER passes all read these sentences. A name with no
key anywhere on the page is still found by the rule-based whole-page scan.

## NER toggles (`.env`)

```
GLINER_LARGE=true
GLINER_MEDIUM=true
GLINER_LOW=true
```
