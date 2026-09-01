"""Local Hugging Face NER checkpoints for person/human names."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
NER_MODELS_DIR = REPO_ROOT / "Models" / "NER"
DEFAULT_PROCESSED = REPO_ROOT / "Data" / "Processed"

# kind: gliner | hf_token | onnx_conll
MODELS: list[dict] = [
    {
        "id": "01_gliner_small-v2.1",
        "repo": "urchade/gliner_small-v2.1",
        "kind": "gliner",
        "downloader": "Gliner_small_v2_1_downloader",
        "encoder_repo": "microsoft/deberta-v3-small",
    },
    {
        "id": "02_gliner_medium-v2.1",
        "repo": "urchade/gliner_medium-v2.1",
        "kind": "gliner",
        "downloader": "Gliner_medium_v2_1_downloader",
        "encoder_repo": "microsoft/deberta-v3-base",
    },
    {
        "id": "03_gliner_large-v2.1",
        "repo": "urchade/gliner_large-v2.1",
        "kind": "gliner",
        "downloader": "Gliner_large_v2_1_downloader",
        "encoder_repo": "microsoft/deberta-v3-large",
    },
    {
        "id": "04_gliner_multi-v2.1",
        "repo": "urchade/gliner_multi-v2.1",
        "kind": "gliner",
        "downloader": "Gliner_multi_v2_1_downloader",
        "encoder_repo": "microsoft/mdeberta-v3-base",
    },
    {
        "id": "05_gliner-bi-edge-v2.0",
        "repo": "knowledgator/gliner-bi-edge-v2.0",
        "kind": "gliner",
        "downloader": "Gliner_bi_edge_v2_0_downloader",
        "labels_encoder_repo": "sentence-transformers/all-MiniLM-L6-v2",
    },
    {
        "id": "06_gliner-bi-base-v2.0",
        "repo": "knowledgator/gliner-bi-base-v2.0",
        "kind": "gliner",
        "downloader": "Gliner_bi_base_v2_0_downloader",
        "labels_encoder_repo": "BAAI/bge-small-en-v1.5",
    },
    {
        "id": "07_gliner-bi-large-v2.0",
        "repo": "knowledgator/gliner-bi-large-v2.0",
        "kind": "gliner",
        "downloader": "Gliner_bi_large_v2_0_downloader",
        "labels_encoder_repo": "BAAI/bge-base-en-v1.5",
    },
    {
        "id": "08_bert-base-NER",
        "repo": "dslim/bert-base-NER",
        "kind": "hf_token",
        "downloader": "Bert_base_NER_downloader",
    },
    {
        "id": "09_distilbert-NER",
        "repo": "dslim/distilbert-NER",
        "kind": "hf_token",
        "downloader": "Distilbert_NER_downloader",
    },
    {
        "id": "10_distilbert-conll03-onnx",
        "repo": "onnx-community/distilbert-base-cased-finetuned-conll03-english-ONNX",
        "kind": "onnx_conll",
        "downloader": "Distilbert_conll03_onnx_downloader",
        "current": True,
    },
]


def model_by_id(model_id: str) -> dict:
    for spec in MODELS:
        if spec["id"] == model_id:
            return spec
    known = ", ".join(spec["id"] for spec in MODELS)
    raise KeyError(f"Unknown NER model {model_id!r}. Known: {known}")


def model_dir(spec: dict | str) -> Path:
    if isinstance(spec, str):
        spec = model_by_id(spec)
    return NER_MODELS_DIR / spec["id"]
