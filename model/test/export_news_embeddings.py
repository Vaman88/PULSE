"""Export item-level EmbeddingGemma 2 vectors, one resumable shard at a time.

Example:
  python model/test/export_news_embeddings.py --files 1996-01-22_to_2001-06-08.json

Each input range becomes a compressed JSON file with the same range name under
data/train_test_dataset/embeddings. A private JSONL checkpoint lets a shard
resume after interruption without mixing sample IDs or article positions.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sqlite3
import re
import tempfile
import time
from pathlib import Path

import torch
import torch.nn.functional as F
from transformers import AutoConfig, AutoModel, AutoTokenizer


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "train_test_dataset"
OUT = DATA / "embeddings"
MODEL_ID = "google/embeddinggemma-2"
PROMPT = "task: classification | query: "
DIM = 768
MAX_TOKENS = 256


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def article_text(article: object) -> str:
    if not isinstance(article, dict):
        return ""
    title, body = clean(article.get("title")), clean(article.get("body"))
    if not title and not body:
        return ""
    return PROMPT + f"title: {title or 'none'} | text: {body or title}"


def load_model(device: torch.device):
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    config = AutoConfig.from_pretrained(MODEL_ID, vision_config=None, audio_config=None)
    # The checkpoint defaults to bfloat16. FP16 is safer on the local ROCm iGPU;
    # float32 avoids slow BF16 fallback kernels on the ARM CPU host.
    dtype = torch.float16 if device.type == "cuda" else torch.float32
    model = AutoModel.from_pretrained(
        MODEL_ID, config=config, torch_dtype=dtype
    ).eval().to(device)
    return tokenizer, model


def encode(texts: list[str], tokenizer, model, device: torch.device,
           batch_size: int) -> list[list[float]]:
    result: list[list[float]] = []
    with torch.inference_mode():
        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            tokens = tokenizer(batch, return_tensors="pt", padding=True,
                               truncation=True, max_length=MAX_TOKENS)
            tokens = {key: value.to(device) for key, value in tokens.items()}
            hidden = model(**tokens).last_hidden_state
            mask = tokens["attention_mask"].unsqueeze(-1).to(hidden.dtype)
            vectors = F.normalize(
                ((hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)).float(),
                p=2, dim=1,
            ).cpu().tolist()
            result.extend(vectors)
    return result


def checkpoint_path(input_path: Path) -> Path:
    return OUT / ".work" / f"{input_path.stem}.jsonl"


def read_completed(path: Path) -> dict[str, dict]:
    completed: dict[str, dict] = {}
    if not path.exists():
        return completed
    with path.open("r", encoding="utf-8") as source:
        for line in source:
            try:
                sample = json.loads(line)
                completed[str(sample["sample_id"])] = sample
            except (json.JSONDecodeError, KeyError, TypeError):
                continue  # Ignore a possibly partial final line after a hard stop.
    return completed


def append_checkpoint(path: Path, sample: dict) -> None:
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(sample, ensure_ascii=False, separators=(",", ":")) + "\n")
        output.flush()
        os.fsync(output.fileno())


def write_shard(path: Path, source_name: str, samples: dict[str, dict],
                device: torch.device) -> None:
    payload = {
        "metadata": {
            "model": MODEL_ID,
            "dimensions": DIM,
            "pooling": "attention-mask mean; L2 normalized",
            "task_prefix": PROMPT,
            "max_tokens": MAX_TOKENS,
            "inference_dtype": "float16" if device.type == "cuda" else "float32",
            "compute_device": ("ROCm GPU" if torch.version.hip else "CUDA GPU")
            if device.type == "cuda" else "CPU",
            "source_file": source_name,
            "article_order": "news_index is the zero-based index in the source sample news list",
            "date_field": "date_raw preserves the source sample date value",
        },
        "samples": samples,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=5) as output:
            json.dump(payload, output, ensure_ascii=False, separators=(",", ":"))
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_outputs() -> None:
    total_samples = total_slots = total_vectors = total_empty = 0
    for source_path in sorted(DATA.glob("*.json")):
        output_path = OUT / f"{source_path.stem}.json.gz"
        if not output_path.is_file():
            raise FileNotFoundError(f"Missing embedding shard: {output_path.name}")
        source = json.loads(source_path.read_text(encoding="utf-8"))
        with gzip.open(output_path, "rt", encoding="utf-8") as file:
            exported = json.load(file)
        metadata, samples = exported["metadata"], exported["samples"]
        if metadata.get("model") != MODEL_ID or metadata.get("dimensions") != DIM:
            raise ValueError(f"Model metadata mismatch in {output_path.name}")
        if metadata.get("source_file") != source_path.name:
            raise ValueError(f"Wrong source file recorded in {output_path.name}")
        if set(samples) != {str(key) for key in source}:
            raise ValueError(f"Sample IDs differ in {output_path.name}")
        for sample_id, original in source.items():
            item = samples[str(sample_id)]
            news = (original.get("inputs") or {}).get("news") or []
            vectors = item.get("news") or []
            if item.get("date_raw") != original.get("date") or len(vectors) != len(news):
                raise ValueError(f"Date or article count mismatch: {sample_id}")
            for index, (article, entry) in enumerate(zip(news, vectors)):
                if entry.get("news_index") != index:
                    raise ValueError(f"Article order mismatch: {sample_id} news[{index}]")
                text = article_text(article)
                vector = entry.get("embedding")
                if not text:
                    if vector is not None:
                        raise ValueError(f"Unexpected vector for empty text: {sample_id}[{index}]")
                    total_empty += 1
                    continue
                if not isinstance(vector, list) or len(vector) != DIM:
                    raise ValueError(f"Invalid vector size: {sample_id}[{index}]")
                norm = sum(float(value) ** 2 for value in vector) ** 0.5
                if abs(norm - 1.0) > 0.002:
                    raise ValueError(f"Non-normalized vector ({norm:.5f}): {sample_id}[{index}]")
                total_vectors += 1
            total_slots += len(news)
        total_samples += len(source)
        print(f"Validated {output_path.name}: {len(source):,} samples, "
              f"{sum(len((s.get('inputs') or {}).get('news') or []) for s in source.values()):,} news slots",
              flush=True)
    print(f"VALID: {total_samples:,} samples, {total_slots:,} news slots, "
          f"{total_vectors:,} vectors, {total_empty:,} empty news entries.", flush=True)


def process_file(input_path: Path, tokenizer, model, device: torch.device,
                 batch_size: int, cache: sqlite3.Connection) -> tuple[int, int]:
    data = json.loads(input_path.read_text(encoding="utf-8"))
    checkpoint = checkpoint_path(input_path)
    completed = read_completed(checkpoint)
    pending = [(str(sample_id), sample) for sample_id, sample in data.items()
               if str(sample_id) not in completed]
    sample_texts: dict[str, list[str]] = {}
    unique_texts: dict[str, str] = {}
    for sample_id, sample in pending:
        texts = [article_text(article) for article in ((sample.get("inputs") or {}).get("news") or [])]
        sample_texts[sample_id] = texts
        for text in texts:
            if text:
                unique_texts.setdefault(hashlib.sha256(text.encode("utf-8")).hexdigest(), text)

    missing: list[tuple[str, str]] = []
    for key, text in unique_texts.items():
        row = cache.execute("SELECT text FROM vectors WHERE key=?", (key,)).fetchone()
        if row is None:
            missing.append((key, text))
        elif row[0] != text:
            raise RuntimeError("SHA-256 collision in item embedding cache")

    started = time.time()
    print(f"{input_path.name}: {len(data):,} samples; "
          f"{len(completed):,} already checkpointed; {len(unique_texts):,} unique texts; "
          f"{len(missing):,} uncached; {device}", flush=True)

    for start in range(0, len(missing), batch_size):
        batch = missing[start:start + batch_size]
        vectors = encode([text for _, text in batch], tokenizer, model, device, batch_size)
        cache.executemany(
            "INSERT OR IGNORE INTO vectors(key,text,vector) VALUES(?,?,?)",
            [(key, text, __import__("numpy").asarray(vector, dtype="float32").tobytes())
             for (key, text), vector in zip(batch, vectors)],
        )
        cache.commit()
        done = min(start + len(batch), len(missing))
        if done % (batch_size * 10) < batch_size or done == len(missing):
            elapsed = max(time.time() - started, 1e-6)
            print(f"  encoded {done:,}/{len(missing):,} uncached unique texts "
                  f"({done / elapsed:.2f}/s)", flush=True)

    for sample_number, (sample_id, sample) in enumerate(pending, 1):
        embedded_news = []
        for index, text in enumerate(sample_texts[sample_id]):
            vector = None
            if text:
                key = hashlib.sha256(text.encode("utf-8")).hexdigest()
                stored = cache.execute("SELECT vector FROM vectors WHERE key=?", (key,)).fetchone()
                if stored is None:
                    raise RuntimeError(f"Missing completed vector for {sample_id} news[{index}]")
                vector = __import__("numpy").frombuffer(stored[0], dtype="float32").tolist()
            embedded_news.append({"news_index": index, "embedding": vector})
        row = {"sample_id": sample_id, "date_raw": sample.get("date"), "news": embedded_news}
        append_checkpoint(checkpoint, row)
        completed[sample_id] = row
        if sample_number % 100 == 0 or sample_number == len(pending):
            print(f"  checkpointed {sample_number:,}/{len(pending):,} pending samples",
                  flush=True)

    ordered = {str(sample_id): completed[str(sample_id)] for sample_id in data}
    destination = OUT / f"{input_path.stem}.json.gz"
    write_shard(destination, input_path.name, ordered, device)
    return len(data), sum(len((sample.get("inputs") or {}).get("news") or [])
                          for sample in data.values())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--files", nargs="+", help="input dataset range filenames")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--stats-only", action="store_true")
    parser.add_argument("--manifest-name", default="manifest.json")
    parser.add_argument("--cache-name", default="item_vectors.sqlite3")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    torch.set_num_threads(max(1, args.threads))
    device = (torch.device("cuda") if args.device == "auto" and torch.cuda.is_available()
              else torch.device("cpu") if args.device == "auto"
              else torch.device(args.device))
    files = [DATA / name for name in args.files] if args.files else sorted(DATA.glob("*.json"))
    files = [path for path in files if path.is_file() and path.parent == DATA]
    if not files:
        raise SystemExit("No input JSON files selected")
    if args.stats_only:
        for path in files:
            data = json.loads(path.read_text(encoding="utf-8"))
            count = sum(len((sample.get("inputs") or {}).get("news") or [])
                        for sample in data.values())
            print(f"{path.name}\t{len(data)} samples\t{count} news slots")
        return
    if args.validate_only:
        validate_outputs()
        return
    if device.type == "cuda" and not torch.cuda.is_available():
        raise SystemExit("CUDA/ROCm device requested but unavailable")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / ".work").mkdir(exist_ok=True)
    cache = sqlite3.connect(OUT / ".work" / args.cache_name)
    cache.execute("PRAGMA journal_mode=WAL")
    cache.execute("CREATE TABLE IF NOT EXISTS vectors(key TEXT PRIMARY KEY, text TEXT NOT NULL, vector BLOB NOT NULL)")
    cache.commit()
    print(f"Loading {MODEL_ID} on {device}; batch={args.batch_size}, "
          f"CPU threads={args.threads}", flush=True)
    tokenizer, model = load_model(device)
    all_samples = all_articles = 0
    for path in files:
        samples, articles = process_file(path, tokenizer, model, device,
                                         args.batch_size, cache)
        all_samples += samples
        all_articles += articles
        print(f"Saved {OUT / (path.stem + '.json.gz')}", flush=True)
    manifest = {
        "model": MODEL_ID,
        "dimensions": DIM,
        "max_tokens": MAX_TOKENS,
        "completed_files": [path.name for path in files],
        "sample_count": all_samples,
        "news_slot_count": all_articles,
    }
    (OUT / args.manifest_name).write_text(json.dumps(manifest, indent=2) + "\n",
                                          encoding="utf-8")
    print(f"Done: {all_samples:,} samples, {all_articles:,} news slots.", flush=True)
    cache.close()


if __name__ == "__main__":
    main()
