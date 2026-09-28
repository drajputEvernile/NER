"""Trained candidate selection, shared by train.py, evaluate.py and the pipeline.

One LightGBM binary model per field scores each candidate of a (page, field) group with the
probability that it is a true value. A single-value field takes its best candidate when that
probability reaches the field's threshold, else nothing; a multi-value field (Member ID,
headings) takes every candidate at or above it. Headings also get a level model (Heading vs
Subheading). A field without a model keeps the rules' choice. The rules still generate every
candidate: the model never invents a value.

Version folder {config.Model_Registry}/vNNN/:
    ranker_{field}.txt, level_{field}.txt   LightGBM boosters
    features.json                           feature columns and per-field category lists
    thresholds.json                         per-field minimum probability to select
    manifest.json, metrics.json             what it was trained on and how it scored
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from Heading.extract import DETECTORS
from Member_ID.id_types import guess_id_type
from Util import config

NUMERIC = [
    "rule_score", "rule_accepted", "rule_selected",
    "key_edit_distance", "key_weak", "key_cluster_size", "key_n_words",
    "key_x0", "key_y0", "key_x1", "key_y1",
    "value_found", "value_x0", "value_y0", "value_x1", "value_y1", "word_gap", "line_gap", "dx",
    "value_len", "value_n_words", "digit_ratio", "alpha_ratio", "upper_ratio", "has_comma", "has_initial",
    "value_is_date",
    "page_count", "page_frac", "n_keys_page", "n_trusted_keys_page", "n_field_keys", "n_field_candidates",
    "n_field_accepted", "page_value_count", "page_distinct_values",
    "record_value_pages", "record_value_share", "record_distinct_values",
    "heading_height_ratio", "heading_key_overlap", "heading_value_overlap", "heading_whole_line",
    "heading_line_count", "heading_ends_colon", "heading_tail_words",
    # derived within the (page, field) group
    "score_rank", "score_gap", "same_value_in_group",
]
CATEGORICAL = [
    "key", "key_match", "key_trusted_reason", "region", "relation", "rule_source", "detail_cat",
    "heading_key_field", "heading_position", "id_type",
]
MULTI_VALUE = frozenset({"member_id", *DETECTORS})
# Fields whose detail is a small category (DOS tier, provider profile, heading level), not a value.
_DETAIL_CATEGORY = frozenset({"dos", "provider_name", *DETECTORS})
LEVEL_POSITIVE = "Heading"


def version_dir(version: str) -> Path:
    return Path(config.Model_Registry) / version


def _truthy(series: pd.Series) -> pd.Series:
    return series.astype(str).isin({"1", "True", "true"})


def prepare(frame: pd.DataFrame) -> pd.DataFrame:
    """Candidate rows (placeholders dropped) with group id and derived group features."""
    frame = frame[~_truthy(frame["is_placeholder"])].copy()
    if frame.empty:
        return frame
    frame["group_id"] = (
        frame["record_id"].astype(str) + "|" + frame["page_number"].astype(str) + "|"
        + frame["file_name"].astype(str) + "|" + frame["field"].astype(str)
    )
    score = pd.to_numeric(frame["rule_score"], errors="coerce").fillna(0.0)
    frame["score_rank"] = score.groupby(frame["group_id"]).rank(ascending=False, method="min")
    frame["score_gap"] = score.groupby(frame["group_id"]).transform("max") - score
    norm = frame["value_norm"].astype(str)
    frame["same_value_in_group"] = norm.groupby([frame["group_id"], norm]).transform("size").where(norm != "", 0)
    frame["detail_cat"] = np.where(frame["field"].isin(_DETAIL_CATEGORY), frame["detail"].astype(str), "")
    if "id_type" not in frame.columns:
        frame["id_type"] = ""
    missing = (frame["field"] == "member_id") & (frame["id_type"].astype(str) == "")
    frame.loc[missing, "id_type"] = [
        guess_id_type(str(key) or str(text)) for text, key in zip(frame.loc[missing, "key_text"], frame.loc[missing, "key"])
    ]
    return frame


def categories_of(frame: pd.DataFrame) -> dict[str, list[str]]:
    return {column: sorted(set(frame[column].astype(str)) - {""}) for column in CATEGORICAL}


def matrix(
    frame: pd.DataFrame,
    categories: dict[str, list[str]],
    numeric: list[str] | None = None,
    categorical: list[str] | None = None,
) -> pd.DataFrame:
    """Model input: numeric columns as floats (blank = missing), categoricals on a fixed vocabulary.
    A trained version passes the columns it was trained with."""
    out = pd.DataFrame(index=frame.index)
    for column in numeric or NUMERIC:
        values = frame[column] if column in frame.columns else pd.Series("", index=frame.index)
        out[column] = pd.to_numeric(values.replace("", np.nan), errors="coerce").astype(float)
    for column in categorical or CATEGORICAL:
        values = frame[column].astype(str) if column in frame.columns else pd.Series("", index=frame.index)
        out[column] = pd.Categorical(values.where(values != "", None), categories=categories.get(column, []))
    return out


def select(groups: pd.Series, probs: np.ndarray, multi: bool, threshold: float) -> np.ndarray:
    """Which candidates a field takes: all above threshold (multi) or the best one if above it."""
    above = probs >= threshold
    if multi:
        return above
    best = pd.Series(probs, index=range(len(probs))).groupby(groups.to_numpy()).idxmax().to_numpy()
    mask = np.zeros(len(probs), dtype=bool)
    mask[best] = True
    return mask & above


@dataclass
class FieldModel:
    booster: Any
    threshold: float
    categories: dict[str, list[str]]
    level: Any = None


class Version:
    """A trained version loaded from the registry."""

    def __init__(self, name: str):
        import lightgbm as lgb

        folder = version_dir(name)
        features = json.loads((folder / "features.json").read_text(encoding="utf-8"))
        thresholds = json.loads((folder / "thresholds.json").read_text(encoding="utf-8"))
        self.name = name
        self.numeric: list[str] = features.get("numeric") or NUMERIC
        self.categorical: list[str] = features.get("categorical") or CATEGORICAL
        self.fields: dict[str, FieldModel] = {}
        for field, threshold in thresholds.items():
            path = folder / f"ranker_{field}.txt"
            if not path.is_file():
                continue
            level = folder / f"level_{field}.txt"
            self.fields[field] = FieldModel(
                booster=lgb.Booster(model_file=str(path)),
                threshold=float(threshold),
                categories=features["categories"][field],
                level=lgb.Booster(model_file=str(level)) if level.is_file() else None,
            )

    def score(self, frame: pd.DataFrame, field: str) -> tuple[np.ndarray, np.ndarray, list[str]]:
        """(probabilities, selected, levels) for prepared rows of one field. A candidate is
        extracted (accepted) when its probability reaches the threshold."""
        model = self.fields[field]
        x = matrix(frame, model.categories, self.numeric, self.categorical)
        probs = model.booster.predict(x)
        chosen = select(frame["group_id"], probs, field in MULTI_VALUE, model.threshold)
        levels = [""] * len(frame)
        if model.level is not None:
            levels = [LEVEL_POSITIVE if p >= 0.5 else "Subheading" for p in model.level.predict(x)]
        return probs, chosen, levels

    def apply(self, log: pd.DataFrame) -> pd.DataFrame:
        """The candidate log with model_score / model_accepted / model_selected / model_level
        filled for modelled fields."""
        log = log.copy()
        for column in ("model_score", "model_accepted", "model_selected", "model_level"):
            log[column] = ""
        for field, model in self.fields.items():
            rows = prepare(log[log["field"] == field])
            if rows.empty:
                continue
            probs, chosen, levels = self.score(rows, field)
            log.loc[rows.index, "model_score"] = [f"{p:.4f}" for p in probs]
            log.loc[rows.index, "model_accepted"] = ["1" if p >= model.threshold else "0" for p in probs]
            log.loc[rows.index, "model_selected"] = ["1" if c else "0" for c in chosen]
            log.loc[rows.index, "model_level"] = levels
        return log


@lru_cache(maxsize=4)
def load_version(name: str) -> Version:
    return Version(name)
