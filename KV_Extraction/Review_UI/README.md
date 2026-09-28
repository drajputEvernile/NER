# KV Extraction UI

Batches, manual review and extraction model versions.
See `START.md` to run it.

## Paths (`KV_Extraction/Util/config.py`)

| Setting | Purpose |
|---|---|
| `Run_Output` | `{Output_Root}/Runs`: every `KV_Run_*` folder under it is a batch on the home page |
| `Raw_Input` | Original page images (`{Raw_Input}/{RecordId}/{fileName}`) |
| `OCR_Input` | OCR JSON: OCR Data panel, word picking, NER export |
| `Model_Registry` | Trained extraction model versions (`vNNN/manifest.json`); v0 = rules |
| `Training_Data` | `{Output_Root}/Training`: datasets, splits, NER export (`ner/`), optional `record_sources.csv` |
| `Rerun_Logs` | `{Output_Root}/Rerun_Logs`: logs of reruns started from the home page |
| `Heading_Models` | Layout detector for headings (Heron), reviewed as the Headings section |

## Home page

- **Metric bar**: total runs, documents, pages, avg pages / document, avg time / page, and accuracy
  over every reviewed page-field of every batch.
- **Batches**: one row per run with documents, pages, reviewed pages, current accuracy, model
  version, start time and total time. Actions:
  - **Review** opens the batch review page (runs without a candidate log, made before the
    candidate log existed, can't be reviewed; rerun them).
  - **Rerun** asks which extraction model version to use and starts `run.py` on the same documents
    into a new batch. Only one run at a time; the page refreshes every 5 s while it runs.
    Its log goes to `Rerun_Logs`.
  - **Detailed view** shows accuracy per field for every run over the same documents, grouped by
    extraction model version.

Accuracy always uses the latest review of each page from any batch (matched on the page's OCR
text), so a rerun is scored against reviews made on the earlier batch without re-reviewing.

## Batch review page

Files sidebar, image viewer (overlay tabs per field, zoom, filmstrip), **OCR Data** panel and
**Review** panel side by side. The Review panel has one collapsible section per field:

- Tick / cross each candidate the rules found (extracted ones must be judged; a cross needs a reason).
  Candidates the rules rejected are always listed below the selected ones.
  Hovering a candidate outlines its key (orange) and value (blue) on the page.
- Reasons for a cross: **Wrong value** opens an optional correct-value box right under it (value
  only; saved as an added value with `for_candidate` and the candidate's key). **Belongs to
  another field** asks which field. **Should not be selected (another key was right)** is offered
  on a selected value when the page has other keys of the same field, and asks which of those keys
  should have been used (saved as `prefer_candidate` / `prefer_key*`). **Not a value** and **Wrong key & value** open nothing (for
  the latter, add the right value under Missed values). **Not a real key here** offers the word
  before / after the key; ticked words are added to `{Field}/key_blocklist.json` on save and stop
  that key from matching in later runs.
- **Add missed value**: type it, or click its words in the OCR panel or on the image (pick mode;
  Ctrl+click picks more than one word), and optionally pick the key words next to it.
- **Member ID type**: a ticked or added Member ID is labelled Member ID / MRN / SSN / Encounter
  number / Other ID, pre-set from its key (`Member_ID/id_types.json`); an added ID with no key
  needs the type picked. NER training uses one label per type.
- **Not on this page** when the field isn't there.
- Save per field. Reviews of the same page in another batch pre-fill the form.

Every value the run extracted (chip **Extracted**) needs a tick or a cross; **Selected** marks the
one chosen for the record's output. Accuracy counts key-value pairs: an extracted pair is **right**
when both its key and its value are correct, **wrong** when either is wrong, and a reviewed pair
the run did not extract (ticked near miss or added value) is **missed**. Accuracy = right ÷ (right
\+ wrong + missed). A field marked not on the page with nothing extracted counts as one right; a
correction typed under a crossed value is the same error, not a second miss.

Below the KV fields, under "Page structure", is a **Headings** section (Heron layout model) with its
own image tab. It works like a KV field:

- Tick each detected heading and confirm its **Level** (Heading / Subheading, pre-set to the
  model's guess). Near misses (low score, a KV key, page-header noise) are listed below.
- Reasons for a cross: **Not a heading**, **It's a KV key / field label**, **Noise**, and
  **Wrong text** (opens a box for the right heading text and its level).
- **Add missed heading**: type it or pick its words, and pick its level.
- **No headings on this page** when there are none.

Headings are scored on their own (line-level precision and recall, shown in the batch header) and
are not part of the KV accuracy.

## What a review saves

`{run}/review/labels.json` (schema in `KV_Extraction/Training/labels.py`), per page and field:
verdict and reason for each candidate, missed values with their OCR word indexes, not-present flag,
notes, reviewer and time, plus the derived truth values (headings also carry their level).
`{run}/review/manual_review.xlsx` is an Excel copy (Field_Accuracy, Heading_Accuracy, Page_Fields,
Candidates, Added) rewritten on every save.

These labels feed:

- accuracy on the home page and in the detailed view;
- ranker training (candidate verdicts joined to `candidates/{RecordId}.csv` by `candidate_id`);
- GLiNER fine-tuning: `python KV_Extraction/Training/ner_export.py` writes
  `{Training_Data}/ner/gliner_train_*.json` from pages where every NER field is reviewed.
