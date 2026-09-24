# Master Data Builder

## Config (`Util/config.py`)

| Setting | Purpose |
|---|---|
| `Raw_Input` | Source record folders / page images |
| `OCR_Input` | OCR JSON for OCR Data tab |
| `Master_Data_Output` | Output folder (`selected_records.json`, `master_data.json`, `master_data.xlsx`) |
| `Master_Data_Max_Pages` | Max pages for eligibility (default 20) |

## Select records

Standalone function / CLI — lists Raw docs with ≤ max pages, keeps first **N**:

```powershell
.\.venv\Scripts\python.exe -m Master_Data_Builder.select_records -N 20
```

## UI

From Manual Review top bar → **Master Data Builder**.

- Files sidebar + image viewer
- Tabs: **OCR Data**, **Keys & Values**
- Key groups: Member DOB, Member ID, Member Name, Provider Name, DOS, E-Sign, Page No, Others
- Fields: Key + Value (E-Sign: Key + Value1 + Value2), Add another, Save

## Outputs

Under `Master_Data_Output`:

- `selected_records.json`
- `master_data.json`
- `master_data.xlsx` — one sheet per key group
