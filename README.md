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
| `Member_Verification/` | Verification (reads OCR JSON from blob, or locally) |
| `Member_Verification/Models/` | NER weights, `catalog.py` + `model_downloader/` |
| `azure_blob/` | Shared blob helpers |

Blob path prefixes live only in `.env` (`AZURE_OCR_RAW_STORAGE_PREFIX`, `AZURE_OCR_STORAGE_WRITE_PREFIX`).

## Download NER models

Weights live next to the catalog in `Member_Verification/Models/`, and that is
the only place the runtime looks for them.

```powershell
$env:HF_HUB_DISABLE_XET='1'
.\.venv\Scripts\python.exe Member_Verification\Models\model_downloader\__main__.py
```

Individual models:

```powershell
.\.venv\Scripts\python.exe Member_Verification\Models\model_downloader\gliner_large_v2_1.py
.\.venv\Scripts\python.exe Member_Verification\Models\model_downloader\gliner_medium_v2_1.py
.\.venv\Scripts\python.exe Member_Verification\Models\model_downloader\gliner_low.py
```

Each model is downloaded, checked for completeness, then **loaded and asked to
read a test sentence** -- a snapshot can finish with every file present and
still not load. Anything that fails prints its full traceback and the command
exits non-zero.

Verify what is already on disk without re-downloading:

```powershell
.\.venv\Scripts\python.exe Member_Verification\Models\model_downloader\__main__.py --check
```

A verification run also loads every enabled model before it touches the queue,
so a bad checkpoint stops the run instead of quietly detecting nothing.

### Tokenizer warning on load

transformers 5 warns `incorrect regex pattern ... will lead to incorrect
tokenization` and suggests `fix_mistral_regex=True` whenever a local config
carries no `transformers_version`, because it cannot then rule out a Mistral
tokenizer. The DeBERTa-v3 encoders ship without that field, so it fired on
every model load even though `model_type` is `deberta-v2` -- and taking the
advice would install a Mistral pre-tokenizer and corrupt tokenization for
real. `relink_local_paths` now fills the field in (only when `model_type`
proves the model is not Mistral, so a genuine Mistral tokenizer still warns),
which lets transformers skip the check itself. Nothing is suppressed.

## Python version

Any Python from **3.12** upward works; `scipy` and `numpy` set the floor. The
pipeline is exercised on 3.14, and 3.13 is the better-supported target of the
two (`torch.jit.script` warns on 3.14+). Nothing here needs a specific minor
version.

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

Against OCR JSON on disk instead of blob (same pipeline, no storage account
needed — this is how to exercise the app locally):

```powershell
.\.venv\Scripts\python.exe Member_Verification\run_local.py
.\.venv\Scripts\python.exe Member_Verification\run_local.py --records Test1,Test2
.\.venv\Scripts\python.exe Member_Verification\run_local.py --root Data\output --mode selected
```

It reads `{root}/{RecordId}/Azure_OCR_Output/{RecordId}.json`, falling back to
`Docling_OCR_Output/` and then `{root}/{RecordId}.json`.

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

The batch folder is written **when the queue finishes**. Stop a run part-way
and there is no folder yet -- the rows for the records that did finish sit in
`Member_Verification/mv_staging/`, and the log says so on exit. Rerun to carry
on and the batch is written then.

Any failure stops the run and logs the full traceback: a model that will not
load, a blob read that keeps failing, a bad record. Nothing is swallowed and
turned into "detected nothing".

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

Because a wrong-member page rejects a whole chart, that signal comes from one
place only (`Rules/wrong_member_rules.py`): the people the NER model recognised
in the sentence around a patient-name key. If none of them verifies as the
expected member, the page carries a wrong member; if the model recognises
nobody, the page is not considered. Nothing reads names by token scanning --
that cannot tell a person from prose, and it read `Patient Health
Questionnaire` as a member. An attending physician or signer who shares the
member's surname is never counted either, because they sit outside the name
sentence.

Hits the model labels "person" are still filtered before they count as a
member: non-name words are trimmed off the ends (`FIN: Benjamin Benjamin` ->
`Benjamin Benjamin`) and a hit is dropped when one survives inside it, so
`the patient`, `my medical assistant` and `patient or family` never reach the
decision.

## Two layers per page

Every page goes through the rules first and only escalates when they come up
empty:

1. **Rules, over the whole page** — name, DOB and member ID are searched for
   across the full page text. A page whose details the rules match costs no
   model time at all.
2. **NER, over a key sentence** — only for a field the rules missed. The
   sentence around that field's key is cut out of the page and the model reads
   it. A page that failed verification also escalates here, to find out whether
   another member is named on it.

`Detection_Source_Name` / `_DOB` / `_MemberID` record which layer produced each
value, and `ner_key_source_*` records the key the sentence was built from.

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
