"""Train and chronologically evaluate a small news + FDA disease classifier.

Run from the repository root with the ROCm environment:
    /root/venvs/pulse-rocm-venv/bin/python model/prototype1/prototype1.py
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import tempfile
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from transformers import AutoConfig, AutoModel, AutoTokenizer


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "train_test_dataset"
EMBEDDINGS = DATA / "embeddings"
OUT = Path(__file__).resolve().parent
MODEL_ID = "google/embeddinggemma-2"
PROMPT = "task: classification | query: "
DIM = 768
MAX_TOKENS = 256
CLASSES = 10
GROWTH_STAGES = (20, 40, 50, 60, 70)
FDA_EMBEDDINGS = OUT / "fda_record_embeddings.json.gz"
RESULTS = OUT / "results.json"
SUMMARY = OUT / "results.md"
CHECKPOINT = OUT / "prototype1.pt"
TARGETS = [
    "influenza_1w", "influenza_2w", "west_nile_1w", "west_nile_2w",
    "dengue_1w", "dengue_2w", "measles_1w", "measles_2w",
    "pertussis_1w", "pertussis_2w", "hepatitis_a_1w", "hepatitis_a_2w",
    "salmonella_1w", "salmonella_2w", "mumps_1w", "mumps_2w",
    "tuberculosis_1w", "tuberculosis_2w", "mpox_1w", "mpox_2w",
    "chikungunya_1w", "chikungunya_2w", "avian_influenza_1w",
    "avian_influenza_2w", "ev_d68_1w", "ev_d68_2w", "cholera_1w",
    "cholera_2w",
]


def clean(value: object) -> str:
    return " ".join(str(value or "").split())


def mean_vector(vectors: list[list[float]] | list[np.ndarray]) -> np.ndarray:
    if not vectors:
        return np.zeros(DIM, dtype=np.float32)
    vector = np.asarray(vectors, dtype=np.float32).mean(axis=0)
    return vector / max(float(np.linalg.norm(vector)), 1e-12)


def load_rows() -> list[dict]:
    rows = []
    for source in sorted(DATA.glob("*.json")):
        targets_path = DATA / "output" / source.name
        if not targets_path.is_file():
            raise FileNotFoundError(targets_path)
        inputs, labels = json.loads(source.read_text()), json.loads(targets_path.read_text())
        if set(inputs) != set(labels):
            raise ValueError(f"Input/target IDs differ in {source.name}")
        for sample_id, sample in inputs.items():
            outputs = labels[sample_id].get("outputs", {})
            if set(outputs) != set(TARGETS):
                raise ValueError(f"Unexpected targets for sample {sample_id}")
            values = [int(outputs[name]) for name in TARGETS]
            if any(value < 1 or value > CLASSES for value in values):
                raise ValueError(f"Target outside 1..{CLASSES} for sample {sample_id}")
            records = (((sample.get("inputs") or {}).get("fda_records") or {})
                       .get("drug_shortage_first_observed") or [])
            rows.append({
                "id": str(sample_id), "date": sample["date"],
                "news_count": len((sample.get("inputs") or {}).get("news") or []),
                "records": records, "targets": values,
            })
    rows.sort(key=lambda row: (row["date"], int(row["id"])))
    if not rows:
        raise ValueError("No input samples found")
    return rows


def load_news_vectors(rows: list[dict]) -> dict[str, np.ndarray]:
    expected = {row["id"]: row["news_count"] for row in rows}
    vectors: dict[str, np.ndarray] = {}
    for source in sorted(DATA.glob("*.json")):
        path = EMBEDDINGS / f"{source.stem}.json.gz"
        if not path.is_file():
            raise FileNotFoundError(path)
        with gzip.open(path, "rt", encoding="utf-8") as file:
            shard = json.load(file)
        if shard["metadata"].get("dimensions") != DIM:
            raise ValueError(f"Wrong vector size in {path.name}")
        for sample_id, sample in shard["samples"].items():
            if sample_id not in expected or len(sample["news"]) != expected[sample_id]:
                raise ValueError(f"Sample/article alignment mismatch: {sample_id}")
            article_vectors = []
            for index, item in enumerate(sample["news"]):
                if item["news_index"] != index:
                    raise ValueError(f"Article order mismatch: {sample_id}[{index}]")
                if item["embedding"] is not None:
                    article_vectors.append(item["embedding"])
            vectors[sample_id] = mean_vector(article_vectors)
        print(f"Loaded saved news vectors: {path.name}", flush=True)
        del shard
    if set(vectors) != set(expected):
        raise ValueError("The embedding shards do not cover every input sample")
    return vectors


def fda_text(record: dict) -> str:
    fields = [
        ("record type", record.get("record_kind")),
        ("medicine", record.get("generic_name")),
        ("supplier", record.get("supplier")),
        ("FDA posting date", record.get("fda_initial_posting_date")),
        ("record observed date", record.get("date_basis")),
    ]
    body = "; ".join(f"{name}: {clean(value)}" for name, value in fields if clean(value))
    return PROMPT + f"title: FDA drug shortage record | text: {body}" if body else ""


def fda_fingerprint(rows: list[dict]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(row["id"].encode())
        for index, record in enumerate(row["records"]):
            digest.update(f"{index}\0{fda_text(record)}\n".encode())
    return digest.hexdigest()


def load_fda_vectors(rows: list[dict], refresh: bool) -> dict[str, list[dict]]:
    fingerprint = fda_fingerprint(rows)
    if FDA_EMBEDDINGS.is_file() and not refresh:
        with gzip.open(FDA_EMBEDDINGS, "rt", encoding="utf-8") as file:
            cached = json.load(file)
        if (cached["metadata"].get("input_fingerprint") == fingerprint
                and cached["metadata"].get("model") == MODEL_ID):
            print(f"Loaded cached FDA record vectors: {FDA_EMBEDDINGS}", flush=True)
            return cached["samples"]

    texts = list(dict.fromkeys(
        fda_text(record) for row in rows for record in row["records"] if fda_text(record)
    ))
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    dtype = torch.float16 if device.type == "cuda" else torch.float32
    torch.set_num_threads(6)
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    config = AutoConfig.from_pretrained(MODEL_ID, vision_config=None, audio_config=None)
    model = AutoModel.from_pretrained(MODEL_ID, config=config, torch_dtype=dtype).eval().to(device)
    print(f"Embedding {len(texts):,} unique FDA records on {device}.", flush=True)

    encoded: dict[str, list[float]] = {}
    started = time.time()
    with torch.inference_mode():
        for start in range(0, len(texts), 32):
            batch = texts[start:start + 32]
            tokens = tokenizer(batch, return_tensors="pt", padding=True,
                               truncation=True, max_length=MAX_TOKENS)
            tokens = {key: value.to(device) for key, value in tokens.items()}
            hidden = model(**tokens).last_hidden_state
            mask = tokens["attention_mask"].unsqueeze(-1).to(hidden.dtype)
            vectors = F.normalize(
                ((hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)).float(),
                p=2, dim=1,
            ).cpu().tolist()
            encoded.update(zip(batch, vectors))
            done = min(start + len(batch), len(texts))
            if done % 320 < 32 or done == len(texts):
                print(f"FDA vectors: {done:,}/{len(texts):,} "
                      f"({done / max(time.time() - started, 1e-6):.1f}/s)", flush=True)
    del model, tokenizer

    samples = {}
    empty = 0
    for row in rows:
        records = []
        for index, record in enumerate(row["records"]):
            text = fda_text(record)
            vector = encoded.get(text) if text else None
            empty += vector is None
            records.append({
                "record_index": index,
                "record_id": record.get("record_id"),
                "embedding": vector,
            })
        samples[row["id"]] = {"date_raw": row["date"], "records": records}

    metadata = {
        "model": MODEL_ID, "dimensions": DIM, "max_tokens": MAX_TOKENS,
        "pooling": "attention-mask mean; L2 normalized",
        "task_prefix": PROMPT,
        "inference_dtype": str(dtype).replace("torch.", ""),
        "compute_device": "ROCm GPU" if torch.version.hip else str(device),
        "input_fingerprint": fingerprint,
        "unique_texts": len(texts), "record_count": sum(len(row["records"]) for row in rows),
        "empty_record_vectors": empty,
    }
    fd, temporary = tempfile.mkstemp(prefix=FDA_EMBEDDINGS.name, suffix=".tmp", dir=OUT)
    os.close(fd)
    try:
        with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=5) as file:
            json.dump({"metadata": metadata, "samples": samples}, file,
                      ensure_ascii=False, separators=(",", ":"))
        os.chmod(temporary, 0o644)
        os.replace(temporary, FDA_EMBEDDINGS)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"Saved FDA record vectors: {FDA_EMBEDDINGS}", flush=True)
    return samples


def make_features(rows: list[dict], news: dict[str, np.ndarray],
                  fda: dict[str, dict]) -> np.ndarray:
    width = DIM * 4 + 5
    features = np.zeros((len(rows), width), dtype=np.float32)
    last_fda = np.zeros(DIM, dtype=np.float32)
    last_date = None
    by_date: dict[str, list[str]] = {}
    for row in rows:
        by_date.setdefault(row["date"], []).append(row["id"])
    for i, row in enumerate(rows):
        current_records = fda[row["id"]]["records"]
        current = mean_vector([record["embedding"] for record in current_records
                               if record["embedding"] is not None])
        has_current = bool(current_records)
        has_prior = last_date is not None
        days = min((np.datetime64(row["date"]) - last_date).astype("timedelta64[D]").astype(int), 3650) if has_prior else 3650
        day = np.datetime64(row["date"])
        recent_ids = [sid for offset in (1, 2)
                      for sid in by_date.get(str(day - np.timedelta64(offset, "D")), [])]
        has_recent = bool(recent_ids)
        recent = mean_vector([news[sid] for sid in recent_ids])
        features[i, :DIM] = news[row["id"]]
        features[i, DIM:DIM * 2] = current
        features[i, DIM * 2:DIM * 3] = last_fda
        features[i, DIM * 3:DIM * 4] = recent
        features[i, DIM * 4:] = [float(has_current), float(has_prior),
                                 math.log1p(len(current_records)), days / 3650.0,
                                 float(has_recent)]
        if has_current:
            last_fda, last_date = current, np.datetime64(row["date"])
    return features


class Classifier(nn.Module):
    def __init__(self, width: int):
        super().__init__()
        self.layers = nn.Sequential(
            nn.Linear(width, 128), nn.ReLU(), nn.Dropout(0.15),
            nn.Linear(128, len(TARGETS) * CLASSES),
        )

    def forward(self, x):
        return self.layers(x).view(-1, len(TARGETS), CLASSES)


def predict(news_articles: list[str], fda_records: list[dict],
            previous_fda_records: list[dict] = (),
            days_since_previous_fda: int = 3650,
            recent_news_articles: list[str] = ()) -> dict[str, int]:
    """Predict the 28 ratings from article text and current/prior FDA records."""
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    dtype = torch.float16 if device.type == "cuda" else torch.float32
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    config = AutoConfig.from_pretrained(MODEL_ID, vision_config=None, audio_config=None)
    encoder = AutoModel.from_pretrained(MODEL_ID, config=config, torch_dtype=dtype).eval().to(device)

    def embed(texts: list[str]) -> np.ndarray:
        texts = [text for text in texts if text]
        if not texts:
            return np.zeros(DIM, dtype=np.float32)
        with torch.inference_mode():
            tokens = tokenizer(texts, return_tensors="pt", padding=True,
                               truncation=True, max_length=MAX_TOKENS)
            tokens = {key: value.to(device) for key, value in tokens.items()}
            hidden = encoder(**tokens).last_hidden_state
            mask = tokens["attention_mask"].unsqueeze(-1).to(hidden.dtype)
            vectors = F.normalize(
                ((hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)).float(), p=2, dim=1
            ).cpu().numpy()
        return mean_vector(vectors)

    news_texts = [PROMPT + f"title: none | text: {clean(article)}"
                  for article in news_articles if clean(article)]
    current = embed([fda_text(record) for record in fda_records])
    prior = embed([fda_text(record) for record in previous_fda_records])
    recent_texts = [PROMPT + f"title: none | text: {clean(article)}"
                    for article in recent_news_articles if clean(article)]
    features = np.concatenate((
        embed(news_texts), current, prior, embed(recent_texts),
        np.asarray([bool(fda_records), bool(previous_fda_records),
                    math.log1p(len(fda_records)),
                    min(max(days_since_previous_fda, 0), 3650) / 3650,
                    bool(recent_texts)], dtype=np.float32),
    )).astype(np.float32)[None, :]

    checkpoint = torch.load(CHECKPOINT, map_location="cpu", weights_only=True)
    if checkpoint["text_encoder"] != MODEL_ID or checkpoint["targets"] != TARGETS:
        raise ValueError("Checkpoint encoder or target order does not match this pipeline")
    model = Classifier(checkpoint["input_width"]).to(device)
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()
    with torch.inference_mode():
        values = model(torch.from_numpy(features).to(device)).argmax(-1)[0].cpu().numpy() + 1
    return dict(zip(TARGETS, map(int, values)))


def fit(x: np.ndarray, y: np.ndarray, train_start: int, train_end: int,
        device: torch.device) -> tuple[Classifier, int]:
    torch.manual_seed(731)
    np.random.seed(731)
    val_count = max(1, int((train_end - train_start) * 0.10))
    fit_ids = np.arange(train_start, train_end - val_count)
    val_ids = torch.as_tensor(np.arange(train_end - val_count, train_end),
                              dtype=torch.long, device=device)
    model = Classifier(x.shape[1]).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.001, weight_decay=0.01)
    xt, yt = torch.from_numpy(x).to(device), torch.from_numpy(y).to(device)
    best_loss, best_state, best_epoch, stale = float("inf"), None, 0, 0
    for epoch in range(1, 61):
        model.train()
        np.random.shuffle(fit_ids)
        for start in range(0, len(fit_ids), 128):
            ids = torch.as_tensor(fit_ids[start:start + 128], dtype=torch.long, device=device)
            optimizer.zero_grad(set_to_none=True)
            loss = F.cross_entropy(model(xt[ids]).reshape(-1, CLASSES), yt[ids].reshape(-1))
            loss.backward()
            optimizer.step()
        model.eval()
        with torch.inference_mode():
            val_loss = float(F.cross_entropy(
                model(xt[val_ids]).reshape(-1, CLASSES), yt[val_ids].reshape(-1)))
        if val_loss < best_loss - 1e-5:
            best_loss = val_loss
            best_state = {key: value.detach().cpu().clone()
                          for key, value in model.state_dict().items()}
            best_epoch, stale = epoch, 0
        else:
            stale += 1
        if stale >= 8:
            break
    if best_state is None:
        raise RuntimeError("Training did not produce a model")
    model.load_state_dict(best_state)
    return model.eval(), best_epoch


def score(model: Classifier, x: np.ndarray, y: np.ndarray, test_ids: np.ndarray,
          train_start: int, train_end: int, device: torch.device) -> tuple[dict, np.ndarray]:
    with torch.inference_mode():
        predictions = model(torch.from_numpy(x[test_ids]).to(device)).argmax(-1).cpu().numpy() + 1
    actual = y[test_ids] + 1
    majority = []
    for column in range(len(TARGETS)):
        counts = np.bincount(y[train_start:train_end, column] + 1, minlength=CLASSES + 1)
        majority.append(int(np.argmax(counts[1:]) + 1))
    baseline = np.asarray(majority)[None, :]
    per_target = (predictions == actual).mean(axis=0)
    return {
        "macro_variable_accuracy": float(per_target.mean()),
        "micro_variable_accuracy": float((predictions == actual).mean()),
        "majority_baseline_macro_accuracy": float((baseline == actual).mean(axis=0).mean()),
        "exact_sample_accuracy": float((predictions == actual).all(axis=1).mean()),
        "mean_absolute_error": float(np.abs(predictions - actual).mean()),
        "per_target_accuracy": dict(zip(TARGETS, map(float, per_target))),
    }, predictions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-fda-embeddings", action="store_true")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    rows = load_rows()
    first_fda = next((i for i, row in enumerate(rows) if row["records"]), None)
    if first_fda is None:
        raise ValueError("No FDA records found")
    post_fda = len(rows) - first_fda

    news = load_news_vectors(rows)
    fda = load_fda_vectors(rows, args.refresh_fda_embeddings)
    x = make_features(rows, news, fda)
    y = np.asarray([row["targets"] for row in rows], dtype=np.int64) - 1
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    stages = []
    final_model = final_predictions = final_test_ids = final_train_end = None

    print(f"Post-FDA rows: {post_fda:,}; classifier device={device}.", flush=True)
    for percent in GROWTH_STAGES:
        train_end = first_fda + math.ceil(post_fda * percent / 100)
        test_ids = np.arange(train_end, len(rows))
        model, best_epoch = fit(x, y, first_fda, train_end, device)
        metrics, predictions = score(model, x, y, test_ids, first_fda, train_end, device)
        stage = {
            "train_percent": percent, "test_percent": 100 - percent,
            "train_samples": train_end - first_fda, "test_samples": len(test_ids),
            "train_start_date": rows[first_fda]["date"],
            "train_end_date": rows[train_end - 1]["date"],
            "test_start_date": rows[train_end]["date"],
            "fda_backed_train_samples": sum(bool(row["records"]) for row in rows[first_fda:train_end]),
            "fda_train_records": sum(len(row["records"]) for row in rows[first_fda:train_end]),
            "best_epoch": best_epoch,
            "metrics": metrics,
        }
        stages.append(stage)
        print(f"{percent}:{100 - percent} train:test: {train_end - first_fda:,} train rows, "
              f"{stage['fda_backed_train_samples']} FDA dates, "
              f"macro accuracy={metrics['macro_variable_accuracy']:.4f}, "
              f"majority={metrics['majority_baseline_macro_accuracy']:.4f}", flush=True)
        if percent == 70:
            final_model, final_predictions = model, predictions
            final_test_ids, final_train_end = test_ids, train_end

    assert final_model is not None and final_predictions is not None
    assert final_test_ids is not None and final_train_end is not None
    torch.save({
        "model_state_dict": {key: value.cpu() for key, value in final_model.state_dict().items()},
        "input_width": int(x.shape[1]), "targets": TARGETS,
        "class_values": list(range(1, CLASSES + 1)), "text_encoder": MODEL_ID,
        "training_end_date": rows[final_train_end - 1]["date"],
    }, CHECKPOINT)
    predictions = []
    for index, predicted in zip(final_test_ids, final_predictions):
        predictions.append({
            "sample_id": rows[index]["id"], "date": rows[index]["date"],
            "predicted_outputs": dict(zip(TARGETS, map(int, predicted))),
            "actual_outputs": dict(zip(TARGETS, map(int, rows[index]["targets"]))),
        })

    total_fda = sum(len(row["records"]) for row in rows)
    fda_train = sum(len(row["records"]) for row in rows[first_fda:final_train_end])
    fda_test = sum(len(row["records"]) for row in rows[final_train_end:])
    result = {
        "run_date": time.strftime("%Y-%m-%d"), "model": MODEL_ID,
        "embedding_dimension": DIM, "news_vectors_source": str(EMBEDDINGS),
        "fda_vectors_file": str(FDA_EMBEDDINGS), "sample_count": len(rows),
        "news_items": sum(row["news_count"] for row in rows),
        "fda_record_count": total_fda, "first_fda_date": rows[first_fda]["date"],
        "first_fda_sample_id": rows[first_fda]["id"],
        "train_fda_records": fda_train, "test_fda_records": fda_test,
        "splits": stages, "final_split_train_percent": 70,
        "test_start_date": rows[final_train_end]["date"],
        "test_end_date": rows[-1]["date"], "test_count": len(final_test_ids),
        "test_predictions": predictions,
    }
    class_counts = {"actual": {}, "predicted": {}}
    for field in class_counts:
        for sample in predictions:
            key = f"{field}_outputs"
            for value in sample[key].values():
                label = str(value)
                class_counts[field][label] = class_counts[field].get(label, 0) + 1
    result["final_test_class_counts"] = class_counts
    RESULTS.write_text(json.dumps(result, indent=2), encoding="utf-8")
    lines = [
        "# Prototype 1 results", "",
        f"Training uses only rows from the first FDA-backed sample ({rows[first_fda]['date']}) onward. "
        f"The final 70:30 split holds out {len(final_test_ids):,} samples.",
        "Each split is chronological: the first share trains and the rest tests.", "",
        "| Train:test | Train samples | Test samples | FDA-backed train dates | FDA train records | Macro accuracy | Majority baseline | Exact sample match | MAE |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for stage in stages:
        metric = stage["metrics"]
        lines.append(
            f"| {stage['train_percent']}:{stage['test_percent']} | {stage['train_samples']:,} | {stage['test_samples']:,} | "
            f"{stage['fda_backed_train_samples']} | {stage['fda_train_records']:,} | "
            f"{metric['macro_variable_accuracy']:.3f} | "
            f"{metric['majority_baseline_macro_accuracy']:.3f} | "
            f"{metric['exact_sample_accuracy']:.3f} | {metric['mean_absolute_error']:.3f} |"
        )
    best_stage = max(stages, key=lambda stage: stage["metrics"]["macro_variable_accuracy"])
    final_metrics = next(stage["metrics"] for stage in stages if stage["train_percent"] == 70)
    missed_classes = [value for value in range(1, CLASSES + 1)
                      if not class_counts["predicted"].get(str(value), 0)]
    missed_note = (f" It made no test predictions for class {', '.join(map(str, missed_classes))}."
                   if missed_classes else "")
    lines += [
        "", "## What the scores show", "",
        f"The best macro accuracy was {best_stage['metrics']['macro_variable_accuracy']:.3f} "
        f"with a {best_stage['train_percent']}:{best_stage['test_percent']} train:test split. "
        f"With the final 70:30 split it was {final_metrics['macro_variable_accuracy']:.3f}, "
        f"versus a {final_metrics['majority_baseline_macro_accuracy']:.3f} majority baseline; "
        f"exact 28-field sample accuracy was {final_metrics['exact_sample_accuracy']:.3f}. "
        "Adding training data did not produce a steady improvement in this run." + missed_note,
        "",
    ]
    lines += [
        "", "## Limits", "",
        f"The full dataset has {total_fda:,} FDA records on "
        f"{sum(bool(row['records']) for row in rows)} sample dates. "
        f"Only {sum(bool(row['records']) for row in rows[first_fda:final_train_end])} FDA-backed dates "
        "occur in the final training split. Scores measure agreement with the existing 1–10 "
        "target fields; they do not demonstrate real-world outbreak warning performance.",
        "The majority baseline is included so the model can be judged against a simple predictor.",
        "",
    ]
    SUMMARY.write_text("\n".join(lines), encoding="utf-8")
    print(f"Saved {RESULTS}, {SUMMARY}, and {CHECKPOINT}.", flush=True)


if __name__ == "__main__":
    main()
