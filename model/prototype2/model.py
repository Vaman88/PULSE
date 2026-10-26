"""Private inference helpers for the saved three-day prototype 2 pipeline."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
import torch
from torch import nn

HERE = Path(__file__).resolve().parent
MODEL_PATH = HERE / "three_day_model.joblib"
DIM = 768
EMBED_MODEL_ID = "google/embeddinggemma-2"
EMBED_PREFIX = "task: classification | query: "
TARGETS = [
    "influenza_1w", "influenza_2w", "west_nile_1w", "west_nile_2w",
    "dengue_1w", "dengue_2w", "measles_1w", "measles_2w",
    "pertussis_1w", "pertussis_2w", "hepatitis_a_1w", "hepatitis_a_2w",
    "salmonella_1w", "salmonella_2w", "mumps_1w", "mumps_2w",
    "tuberculosis_1w", "tuberculosis_2w", "mpox_1w", "mpox_2w",
    "chikungunya_1w", "chikungunya_2w", "avian_influenza_1w",
    "avian_influenza_2w", "ev_d68_1w", "ev_d68_2w", "cholera_1w", "cholera_2w",
]


class RatingModel(nn.Module):
    def __init__(self, width: int):
        super().__init__()
        self.layers = nn.Sequential(
            nn.Linear(width, 128), nn.ReLU(), nn.Dropout(0.15),
            nn.Linear(128, len(TARGETS) * 10),
        )

    def forward(self, x):
        return self.layers(x).view(-1, len(TARGETS), 10)


@lru_cache(maxsize=1)
def load_model() -> dict:
    bundle = joblib.load(MODEL_PATH)
    disease, rating = bundle["disease_classifier"], bundle["rating_model"]
    if (bundle.get("format_version") != 1 or rating["window_past_days"] != 3
            or rating["targets"] != TARGETS
            or rating["frequency_labels"] != disease["labels"]
            or rating["text_encoder"] != EMBED_MODEL_ID
            or disease["embedding"]["model"] != EMBED_MODEL_ID):
        raise ValueError("Invalid three-day model artifact")
    return bundle


def article_text(article: dict) -> str:
    if not isinstance(article, dict):
        raise TypeError("Each article must be a title/body dictionary")
    def clean(value):
        return re.sub(r"\s+", " ", str(value or "")).strip()
    title, body = clean(article.get("title")), clean(article.get("body"))
    if not title and not body:
        raise ValueError("Each article needs a nonempty title or body")
    return EMBED_PREFIX + f"title: {title or 'none'} | text: {body or title}"


@lru_cache(maxsize=2)
def encoder(device_name: str):
    import transformers
    from packaging.version import Version
    from transformers import AutoConfig, AutoModel, AutoTokenizer

    if Version(transformers.__version__) < Version("5.20.2"):
        raise RuntimeError("EmbeddingGemma 2 requires Transformers 5.20.2 or newer")
    device = torch.device(device_name)
    torch.set_num_threads(max(1, min(8, torch.get_num_threads())))
    tokenizer = AutoTokenizer.from_pretrained(EMBED_MODEL_ID)
    config = AutoConfig.from_pretrained(EMBED_MODEL_ID, vision_config=None, audio_config=None)
    dtype = torch.float16 if device.type == "cuda" else torch.float32
    model = AutoModel.from_pretrained(EMBED_MODEL_ID, config=config, torch_dtype=dtype).eval().to(device)
    return tokenizer, model, device


def embed_articles(articles: list[dict], device_name: str) -> np.ndarray:
    import torch.nn.functional as F

    tokenizer, model, device = encoder(device_name)
    texts = [article_text(article) for article in articles]
    vectors = []
    with torch.inference_mode():
        for start in range(0, len(texts), 8):
            tokens = tokenizer(texts[start:start + 8], return_tensors="pt", padding=True,
                               truncation=True, max_length=256)
            tokens = {key: value.to(device) for key, value in tokens.items()}
            hidden = model(**tokens).last_hidden_state
            mask = tokens["attention_mask"].unsqueeze(-1).to(hidden.dtype)
            vectors.append(F.normalize(
                ((hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)).float(),
                p=2, dim=1,
            ).cpu().numpy())
    return np.concatenate(vectors).astype(np.float32, copy=False)


def classify_vectors(vectors: np.ndarray, classifier: dict) -> list[list[str]]:
    if vectors.ndim != 2 or vectors.shape[1] != DIM or not np.isfinite(vectors).all():
        raise ValueError("Expected a finite N by 768 embedding array")
    scaled = classifier["scaler"].transform(vectors)
    scores = np.column_stack([est.predict_proba(scaled)[:, 1]
                              for est in classifier["estimators"]])
    return [[label for j, label in enumerate(classifier["labels"])
             if row[j] >= classifier["thresholds"][j]] for row in scores]


def mean_vector(vectors) -> np.ndarray:
    if len(vectors) == 0:
        return np.zeros(DIM, dtype=np.float32)
    vector = np.asarray(vectors, dtype=np.float32).mean(axis=0)
    return vector / max(float(np.linalg.norm(vector)), 1e-12)


def fda_body(record: dict) -> str:
    fields = [
        ("record type", record.get("record_kind")),
        ("medicine", record.get("generic_name")),
        ("supplier", record.get("supplier")),
        ("FDA posting date", record.get("fda_initial_posting_date")),
        ("record observed date", record.get("date_basis")),
    ]
    return "; ".join(f"{name}: {' '.join(str(value or '').split())}"
                     for name, value in fields if str(value or "").strip())
