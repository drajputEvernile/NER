"""Train a new version: one LightGBM model per field (plus a heading level model) from a dataset.

    python Training/train.py [--dataset ds_x] [--min-groups 30] [--description "..."]

Trains on the train split only (dataset.split: about 1 record in 5 is held out for testing,
by record), tunes each field's threshold on out-of-fold scores grouped by record (KV: the
key-value pair accuracy of the pairs at or above it; headings: line F1),
then scores v0 and the new version on the test split (and on the train split, marked as
optimistic). A field with fewer than --min-groups reviewed page-fields, or without both true
and false candidates, gets no model and keeps the rules in this version.

Output: {config.Model_Registry}/vNNN/ (next free number), see Training/model.py. Everything
runs on CPU in seconds to minutes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from Training.dataset import load, split
from Training.evaluate import _metrics, evaluate_frame, report
from Training.labels import FIELDS, HEADING_FIELDS
from Training.model import (
    CATEGORICAL,
    LEVEL_POSITIVE,
    NUMERIC,
    Version,
    categories_of,
    matrix,
    prepare,
    select,
)
from Util import config

PARAMS: dict[str, Any] = {
    "objective": "binary",
    "learning_rate": 0.05,
    "num_leaves": 15,
    "feature_fraction": 0.9,
    "bagging_fraction": 0.9,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "min_data_per_group": 5,
    "cat_smooth": 10,
    "verbose": -1,
    "seed": 7,
    "num_threads": 0,
}
ROUNDS = 200
THRESHOLDS = [round(t, 2) for t in np.arange(0.05, 0.96, 0.05)]


def _fit(x: pd.DataFrame, y: np.ndarray):
    import lightgbm as lgb

    params = {**PARAMS, "min_data_in_leaf": int(max(2, min(20, len(y) // 10)))}
    return lgb.train(params, lgb.Dataset(x, label=y, categorical_feature=CATEGORICAL, free_raw_data=False), ROUNDS)


def _objective(rows: pd.DataFrame, chosen: np.ndarray, field: str) -> float:
    """What the threshold maximizes: key-value pair accuracy (KV) or line-level F1 (headings)."""
    heading = field in HEADING_FIELDS
    metrics = _metrics(rows, pd.Series(chosen, index=rows.index), rows["detail"].astype(str), heading)
    if heading:
        p, r = (metrics["precision"] or 0.0), (metrics["recall"] or 0.0)
        return 2 * p * r / (p + r) if p + r else 0.0
    return metrics["accuracy"] or 0.0


def _taken(rows: pd.DataFrame, probs: np.ndarray, field: str, threshold: float) -> np.ndarray:
    """What the run keeps at this threshold: every KV pair at or above it (the extracted pairs
    accuracy counts), the selected lines for headings."""
    if field in HEADING_FIELDS:
        return select(rows["group_id"], probs, True, threshold)
    return probs >= threshold


def _threshold(rows: pd.DataFrame, x: pd.DataFrame, y: np.ndarray, field: str) -> tuple[float, str]:
    """Best threshold on out-of-fold probabilities (folds by record); 0.5 when there are too few records."""
    from sklearn.model_selection import GroupKFold

    records = rows["record_id"].to_numpy()
    n_folds = min(5, len(set(records)))
    if n_folds < 2:
        return 0.5, "default (one record: no out-of-fold scores)"
    oof = np.zeros(len(rows))
    for fit_idx, val_idx in GroupKFold(n_splits=n_folds).split(x, y, records):
        if len(set(y[fit_idx])) < 2:
            oof[val_idx] = y[fit_idx].mean()
            continue
        oof[val_idx] = _fit(x.iloc[fit_idx], y[fit_idx]).predict(x.iloc[val_idx])
    scored = [(_objective(rows, _taken(rows, oof, field, t), field), -abs(t - 0.5), t) for t in THRESHOLDS]
    best = max(scored)
    return best[2], f"out-of-fold over {n_folds} record folds (objective {best[0]:.1f})"


def train_field(rows: pd.DataFrame, field: str, min_groups: int) -> tuple[dict[str, Any], Any, Any, dict]:
    """(report, ranker, level model, categories) for one field's training rows."""
    candidates = prepare(rows)
    groups = rows["group_id"].nunique()
    info: dict[str, Any] = {"groups": int(groups), "candidates": int(len(candidates))}
    y = candidates["label"].to_numpy().astype(int) if not candidates.empty else np.array([])
    if groups < min_groups:
        return {**info, "trained": False, "reason": f"{groups} reviewed page-fields, needs {min_groups}"}, None, None, {}
    if len(set(y)) < 2:
        return {**info, "trained": False, "reason": "needs both true and false candidates"}, None, None, {}
    categories = categories_of(candidates)
    x = matrix(candidates, categories)
    threshold, how = _threshold(candidates, x, y, field)
    ranker = _fit(x, y)
    gain = ranker.feature_importance(importance_type="gain")
    top = sorted(zip(x.columns, gain), key=lambda item: -item[1])[:10]
    info.update(
        trained=True,
        positives=int(y.sum()),
        threshold=threshold,
        threshold_from=how,
        top_features=[{"feature": name, "gain": round(float(value), 2)} for name, value in top if value > 0],
    )
    level = None
    if field in HEADING_FIELDS:
        positives = candidates[(candidates["label"] == 1) & (candidates["true_level"] != "")]
        levels = (positives["true_level"] == LEVEL_POSITIVE).astype(int).to_numpy()
        if len(positives) >= min_groups and len(set(levels)) == 2:
            level = _fit(matrix(positives, categories), levels)
            info["level_model"] = f"trained on {len(positives)} true headings"
        else:
            info["level_model"] = "not trained (too few true headings with both levels); rules' level kept"
    return info, ranker, level, categories


def _next_version() -> str:
    root = Path(config.Model_Registry)
    numbers = [int(path.name[1:]) for path in root.glob("v[0-9][0-9][0-9]") if path.is_dir()] if root.is_dir() else []
    return f"v{max(numbers, default=0) + 1:03d}"


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=config.REPO_ROOT, check=False
        ).stdout.strip()
    except OSError:
        return ""


def train(dataset: str | None, min_groups: int, description: str) -> Path:
    frame, ds_manifest, ds_folder = load(dataset)
    train_rows = split(frame, "train")
    test_rows = split(frame, "test")
    if train_rows.empty:
        raise SystemExit(f"No reviewed pages in the train split of {ds_folder.name}.")

    version = _next_version()
    folder = Path(config.Model_Registry) / version
    fields: dict[str, Any] = {}
    thresholds: dict[str, float] = {}
    categories: dict[str, Any] = {}
    boosters: dict[str, tuple[Any, Any]] = {}
    for field in FIELDS:
        rows = train_rows[train_rows["field"] == field]
        if rows.empty:
            fields[field] = {"groups": 0, "trained": False, "reason": "no reviewed pages"}
            continue
        info, ranker, level, cats = train_field(rows, field, min_groups)
        fields[field] = info
        if ranker is not None:
            thresholds[field] = info["threshold"]
            categories[field] = cats
            boosters[field] = (ranker, level)

    if not boosters:
        print(json.dumps(fields, indent=2))
        raise SystemExit(f"No field has enough reviewed pages to train (--min-groups {min_groups}); nothing saved.")

    folder.mkdir(parents=True, exist_ok=True)
    for field, (ranker, level) in boosters.items():
        ranker.save_model(str(folder / f"ranker_{field}.txt"))
        if level is not None:
            level.save_model(str(folder / f"level_{field}.txt"))
    (folder / "features.json").write_text(
        json.dumps({"numeric": NUMERIC, "categorical": CATEGORICAL, "categories": categories}, indent=2), encoding="utf-8"
    )
    (folder / "thresholds.json").write_text(json.dumps(thresholds, indent=2), encoding="utf-8")

    model = Version(version)
    metrics = {
        "test": evaluate_frame(test_rows, model) if not test_rows.empty else None,
        "train_optimistic": evaluate_frame(train_rows, model),
    }
    (folder / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    manifest = {
        "version": version,
        "description": description or f"LightGBM ranker for {', '.join(boosters)}; other fields use the rules.",
        "trained_at": datetime.now().isoformat(timespec="seconds"),
        "dataset": ds_folder.name,
        "dataset_pages": ds_manifest.get("pages"),
        "train_records": sorted(set(train_rows["record_id"])),
        "test_records": sorted(set(test_rows["record_id"])),
        "min_groups": min_groups,
        "params": {**PARAMS, "rounds": ROUNDS},
        "fields_trained": sorted(boosters),
        "fields": fields,
        "git_commit": _git_commit(),
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"{version}: trained {', '.join(sorted(boosters))}")
    for field, info in fields.items():
        if not info.get("trained"):
            print(f"  {field:22s} rules kept: {info.get('reason')}")
    if metrics["test"]:
        print("test split:")
        print(report(metrics["test"]))
    else:
        print("test split: no reviewed test records yet (metrics.json has train-split numbers only, optimistic)")
    print(folder)
    return folder


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", help="dataset folder (default: the latest ds_*)")
    parser.add_argument("--min-groups", type=int, default=30, help="reviewed page-fields a field needs for a model")
    parser.add_argument("--description", default="")
    args = parser.parse_args()
    train(args.dataset, args.min_groups, args.description)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
