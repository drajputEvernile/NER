# NER

Document pipeline: split pages, OCR them with Docling, then run every person-name NER model.

Put PDF, TIF, TIFF files, or folders of already-split PNG/JPG/JPEG pages, in `Data/Raw/`.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

DPI and OCR scale are in `src/config.py` (`Image_DPI = 250`, `Image_Scale = 4000`).

## Download models

Downloads Docling OCR weights into `Models/Docling_OCR/` and all 10 NER checkpoints into `Models/NER/`. Skips anything already present.

```powershell
.\.venv\Scripts\python.exe -m src.model_downloader
```

Docling only:

```powershell
.\.venv\Scripts\python.exe -m src.model_downloader.Docling_downloader
```

One NER checkpoint only (example):

```powershell
.\.venv\Scripts\python.exe -m src.model_downloader.Gliner_medium_v2_1_downloader
```

Other NER downloaders live in `src/model_downloader/` (`Gliner_*`, `Bert_base_NER_downloader`, `Distilbert_NER_downloader`, `Distilbert_conll03_onnx_downloader`).

## Run the full pipeline

Checks/downloads models if needed, splits `Data/Raw/`, OCRs pages, then runs all NER models:

```powershell
.\.venv\Scripts\python.exe -m src
```

Outputs:

- `Data/Processed/<document>/splitted/` — page JPEGs
- `Data/Processed/<document>/ocr/` — page text, JSON, confidence CSV
- `Data/Processed/<document>/NER/<model_id>/<document>.csv` — person names and bounding-box polygons
- `Data/Processed/<document>/NER/comparison.csv` — per-model name counts

Optional arguments: `--input`, `--output`, `--dpi`, `--scale`.

## Run one stage

```powershell
.\.venv\Scripts\python.exe -m src.splitter
.\.venv\Scripts\python.exe -m src.ocr
.\.venv\Scripts\python.exe -m src.NER
```

NER defaults to every model. Limit to one checkpoint with `--model 02_gliner_medium-v2.1`.
