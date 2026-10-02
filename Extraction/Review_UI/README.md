# Extraction Review UI

React + FastAPI front end for reviewing what a run extracted. How it fits the system:
[../../HOW_IT_WORKS.md](../../HOW_IT_WORKS.md) (sections 6 to 8). How to start it: [START.md](START.md).

- **Home**: metrics bar and the table of runs (accuracy of KV_Extraction and Heading_Detector, model
  version, time) with Review and Rerun actions.
- **Batch review**: the original page image, the OCR text and one review section per field. The
  reviewer ticks or crosses each extracted value; a wrong one gets one of four reasons (key-value
  fields) or two (headings). Words are selected on the image or in the OCR text, never typed.
- Reviews are written into the run's `KV_Extraction.xlsx` (a few seconds after the last change);
  there is no other review file.

Backend: `backend/app.py` (reads and writes through `Training/labels.py`). Frontend: `frontend/src/`.
