"""Small exploratory embedding test for disease, outbreak relevance, and extent.

Reads 20 real articles from data/train_test_dataset and adds controlled synthetic
sentences. It writes only outbreak_embedding_results.json in this folder.
Requires PyTorch and Transformers 5.20.2 or newer.
"""
from __future__ import annotations

import gzip
import itertools
import json
from collections import defaultdict
from pathlib import Path

import torch
import torch.nn.functional as F
from transformers import AutoConfig, AutoModel, AutoTokenizer


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "train_test_dataset"
RESULTS = Path(__file__).with_name("outbreak_embedding_results.json")
MODEL_ID = "google/embeddinggemma-2"
ACTIVE_TEXT_PARAMETERS_M = 270
MAX_TOKENS = 2048

# Two dated WHO outbreak reports for each disease, selected by source, date,
# and disease text. Eight unrelated Arkansas local-news articles provide a
# real-world non-outbreak comparison group.
OUTBREAK_ARTICLES = [
    ("cholera", "2001-09-17", "cholera"),
    ("cholera", "2001-12-03", "cholera"),
    ("ebola", "2003-02-18", "ebola"),
    ("ebola", "2003-02-26", "ebola"),
    ("dengue", "2002-05-08", "dengue"),
    ("dengue", "2002-06-21", "dengue"),
    ("measles", "2019-10-22", "measles"),
    ("measles", "2019-11-27", "measles"),
    ("marburg", "2021-08-09", "marburg"),
    ("marburg", "2021-09-17", "marburg"),
    ("zika", "2015-10-21", "zika"),
    ("zika", "2021-10-14", "zika"),
]
GENERAL_ARTICLES = [
    ("2021-06-04", "Former CPD officer found guilty of DWI"),
    ("2019-09-12", "VOTE: Gentry play of the week"),
    ("2023-10-17", "Arkansas enacts trade war with China"),
    ("2023-09-27", "Mohawks host Marked Tree Indians"),
    ("2021-12-20", "Haas Hall Academy could be headed"),
    ("2022-09-08", "Big Smith To Take Over County Waste"),
    ("2018-07-04", "Bostain Wins All-State Honors"),
    ("2018-04-02", "Sinclair forces local news anchors"),
]

DISEASES = {
    "cholera": "cholera",
    "dengue": "dengue fever",
    "measles": "measles",
    "marburg": "Marburg virus disease",
    "ebola": "Ebola virus disease",
    "zika": "Zika virus disease",
}

DISEASE_NAME_SWAP_SENTENCES = {
    name: [
        f"A public health bulletin reports an outbreak of {disease} in District A, with cases increasing.",
        f"District A health officials say {disease} infections are spreading during an active outbreak.",
    ]
    for name, disease in DISEASES.items()
}

DISEASE_CONTEXT_SENTENCES = {
    "cholera": [
        "The report describes cholera, a waterborne bacterial infection linked to contaminated water and severe watery diarrhea.",
        "Officials are investigating cholera cases caused by Vibrio cholerae, with dehydration reported among patients.",
    ],
    "dengue": [
        "The report describes dengue, a mosquito-borne viral infection associated with high fever, rash, and severe joint pain.",
        "Officials are investigating dengue fever spread by Aedes mosquitoes, with residents reporting fever and muscle pain.",
    ],
    "measles": [
        "The report describes measles, a highly contagious airborne virus that causes fever and a widespread rash.",
        "Officials are investigating measles transmission between people, with fever and rash reported in infected children.",
    ],
    "marburg": [
        "The report describes Marburg virus disease, a filovirus infection that can cause severe hemorrhagic fever.",
        "Officials are investigating Marburg infections linked to contact with infected bodily fluids and severe bleeding illness.",
    ],
    "ebola": [
        "The report describes Ebola virus disease, a filovirus infection that can cause severe hemorrhagic fever.",
        "Officials are investigating Ebola transmission through contact with infected bodily fluids and severe bleeding illness.",
    ],
    "zika": [
        "The report describes Zika, a mosquito-borne virus that can cause fever and rash and poses pregnancy-related risks.",
        "Officials are investigating Zika virus spread by Aedes mosquitoes and possible congenital effects during pregnancy.",
    ],
}

RELEVANCE_SENTENCES = {
    "active_outbreak": (
        "Health authorities confirmed a dengue outbreak: 120 residents tested positive, "
        "with new cases reported in five neighborhoods."
    ),
    "incidental_mention": (
        "A newspaper crossword used the phrase dengue outbreak as a clue; the article is "
        "about a basketball game and reports no illness, cases, or health response."
    ),
    "unrelated_news": (
        "The home team won the basketball final after a close game and a late three-pointer."
    ),
}

INTENSITY_SENTENCES = {
    "low": [
        "Officials report a contained measles outbreak: 12 cases and no deaths in one neighborhood; reports are declining.",
        "A measles outbreak remains localized, with 12 infections, zero deaths, and fewer new reports in one neighborhood.",
    ],
    "medium": [
        "Officials report a growing measles outbreak: 1,200 cases and 12 deaths across 3 districts; reports are rising.",
        "A measles outbreak is expanding across 3 districts, with 1,200 infections, 12 deaths, and increasing reports.",
    ],
    "high": [
        "Officials report a rapidly worsening measles outbreak: 120,000 cases and 1,200 deaths across 30 districts; reports are rising sharply.",
        "A severe measles outbreak is spreading through 30 districts, with 120,000 infections, 1,200 deaths, and sharply increasing reports.",
    ],
}

EXTENT_SENTENCES = {
    "one_area": "Officials report 120 dengue cases and one death, all in a single neighborhood.",
    "several_districts": "Officials report 120 dengue cases and one death across three districts.",
    "many_regions": "Officials report 120 dengue cases and one death across five states and thirty counties.",
}


def load_dataset_articles() -> list[dict]:
    indexed: dict[tuple[int, int], dict] = {}
    for path in sorted(DATA.glob("????-??-??_to_????-??-??.json")):
        samples = json.loads(path.read_text(encoding="utf-8"))
        for sample_id, sample in samples.items():
            for article_index, article in enumerate(sample["inputs"]["news"]):
                indexed[(int(sample_id), article_index)] = {
                    "sample_id": int(sample_id),
                    "date": sample["date"],
                    "article_index": article_index,
                    "title": article["title"],
                    "body": article["body"],
                }

    provenance = {}
    with gzip.open(DATA / "news_provenance.jsonl.gz", "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            provenance[(row["sample_id"], row["article_index"])] = row

    for key, article in indexed.items():
        row = provenance.get(key, {})
        article["source"] = row.get("source", "")
        article["url"] = row.get("url", "")

    selected = []
    for disease, date, phrase in OUTBREAK_ARTICLES:
        matches = [
            article for article in indexed.values()
            if article["date"] == date
            and article["source"] == "who_don"
            and phrase in (article["title"] + " " + article["body"]).casefold()
        ]
        if len(matches) != 1:
            raise RuntimeError(f"Expected one WHO {disease} article for {date}; found {len(matches)}")
        article = dict(matches[0], label="outbreak", disease=disease)
        selected.append(article)

    for date, title_fragment in GENERAL_ARTICLES:
        matches = [
            article for article in indexed.values()
            if article["date"] == date
            and article["source"] == "arkansas_3dlnews"
            and title_fragment.casefold() in article["title"].casefold()
        ]
        if len(matches) != 1:
            raise RuntimeError(f"Expected one general-news article for {date}: {title_fragment}; found {len(matches)}")
        article = dict(matches[0], label="general_news", disease=None)
        selected.append(article)

    if len(selected) != 20 or len({(x["sample_id"], x["article_index"]) for x in selected}) != 20:
        raise RuntimeError("The curated real-article selection must contain 20 unique dataset records")
    return selected


def encode_texts(texts: list[str]) -> tuple[list[torch.Tensor], str]:
    # The vision and audio encoders are explicitly disabled: active text model = 270M.
    model_id = MODEL_ID
    tokenizer = AutoTokenizer.from_pretrained(model_id)
    config = AutoConfig.from_pretrained(model_id, vision_config=None, audio_config=None)
    model = AutoModel.from_pretrained(model_id, config=config).eval()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device)
    torch.set_num_threads(max(1, min(8, torch.get_num_threads())))

    vectors = []
    with torch.inference_mode():
        for text in texts:
            encoded = tokenizer(
                text,
                return_tensors="pt",
                truncation=True,
                max_length=MAX_TOKENS,
            )
            encoded = {key: value.to(device) for key, value in encoded.items()}
            token_vectors = model(**encoded).last_hidden_state
            mask = encoded["attention_mask"].unsqueeze(-1).to(token_vectors.dtype)
            pooled = (token_vectors * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)
            vectors.append(F.normalize(pooled.float(), p=2, dim=1)[0].cpu())
    return vectors, device


def cosine(a: torch.Tensor, b: torch.Tensor) -> float:
    return float(torch.dot(a, b))


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def pair_scores(items: list[dict], vectors: dict[str, torch.Tensor], label_key: str) -> dict:
    same, different = [], []
    for left, right in itertools.combinations(items, 2):
        score = cosine(vectors[left["key"]], vectors[right["key"]])
        (same if left[label_key] == right[label_key] else different).append(score)
    return {
        "mean_same_label_cosine": mean(same),
        "mean_different_label_cosine": mean(different),
        "same_minus_different_margin": mean(same) - mean(different) if same and different else None,
        "same_label_pairs": len(same),
        "different_label_pairs": len(different),
    }


def leave_one_out_centroid_accuracy(items: list[dict], vectors: dict[str, torch.Tensor], label_key: str) -> dict:
    correct = 0
    details = []
    labels = sorted({item[label_key] for item in items})
    for item in items:
        scores = {}
        for label in labels:
            candidates = [x for x in items if x[label_key] == label and x["key"] != item["key"]]
            if not candidates:
                continue
            centroid = F.normalize(torch.stack([vectors[x["key"]] for x in candidates]).mean(0), dim=0)
            scores[label] = cosine(vectors[item["key"]], centroid)
        predicted = max(scores, key=scores.get)
        correct += predicted == item[label_key]
        details.append({"id": item["key"], "actual": item[label_key], "predicted": predicted})
    return {"correct": correct, "total": len(items), "accuracy": correct / len(items), "predictions": details}


def main() -> None:
    real = load_dataset_articles()
    synthetic = []
    for disease, sentences in DISEASE_NAME_SWAP_SENTENCES.items():
        for index, sentence in enumerate(sentences, 1):
            synthetic.append({"key": f"synthetic_name_swap_{disease}_{index}", "group": "disease_name_swap", "disease": disease, "sentence": sentence})
    for disease, sentences in DISEASE_CONTEXT_SENTENCES.items():
        for index, sentence in enumerate(sentences, 1):
            synthetic.append({"key": f"synthetic_disease_context_{disease}_{index}", "group": "disease_context", "disease": disease, "sentence": sentence})
    synthetic.extend({"key": f"synthetic_relevance_{name}", "group": "relevance", "label": name, "sentence": sentence} for name, sentence in RELEVANCE_SENTENCES.items())
    for level, sentences in INTENSITY_SENTENCES.items():
        for index, sentence in enumerate(sentences, 1):
            synthetic.append({"key": f"synthetic_intensity_{level}_{index}", "group": "intensity", "level": level, "sentence": sentence})
    synthetic.extend({"key": f"synthetic_extent_{name}", "group": "extent", "level": name, "sentence": sentence} for name, sentence in EXTENT_SENTENCES.items())

    texts = []
    keys = []
    for article in real:
        keys.append(f"real_{article['sample_id']}_{article['article_index']}")
        texts.append(f"task: clustering | query: title: {article['title']} | text: {article['body']}")
    for item in synthetic:
        keys.append(item["key"])
        texts.append(f"task: clustering | query: {item['sentence']}")

    vectors_list, device = encode_texts(texts)
    vectors = dict(zip(keys, vectors_list))
    for item, key in zip(real, keys[:len(real)]):
        item["key"] = key

    real_disease = [x for x in real if x["label"] == "outbreak"]
    disease_pairs = {}
    by_disease: dict[str, list[dict]] = defaultdict(list)
    for item in real_disease:
        by_disease[item["disease"]].append(item)
    for disease, pair in by_disease.items():
        disease_pairs[disease] = {
            "titles": [x["title"] for x in pair],
            "cosine": cosine(vectors[pair[0]["key"]], vectors[pair[1]["key"]]),
        }

    real_outbreak = [x for x in real if x["label"] == "outbreak"]
    real_general = [x for x in real if x["label"] == "general_news"]
    outbreak_internal = [cosine(vectors[a["key"]], vectors[b["key"]]) for a, b in itertools.combinations(real_outbreak, 2)]
    general_internal = [cosine(vectors[a["key"]], vectors[b["key"]]) for a, b in itertools.combinations(real_general, 2)]
    cross_class = [cosine(vectors[a["key"]], vectors[b["key"]]) for a in real_outbreak for b in real_general]

    def synthetic_items(group: str) -> list[dict]:
        return [x for x in synthetic if x["group"] == group]

    disease_synthetic = synthetic_items("disease_name_swap")
    disease_context = synthetic_items("disease_context")
    disease_syn_scores = pair_scores(disease_synthetic, vectors, "disease")
    relevance = {x["label"]: x for x in synthetic_items("relevance")}
    intensity = synthetic_items("intensity")
    intensity_within = [
        cosine(vectors[a["key"]], vectors[b["key"]])
        for a, b in itertools.combinations(intensity, 2) if a["level"] == b["level"]
    ]
    intensity_between = [
        cosine(vectors[a["key"]], vectors[b["key"]])
        for a, b in itertools.combinations(intensity, 2) if a["level"] != b["level"]
    ]
    intensity_level = {level: [x for x in intensity if x["level"] == level] for level in ("low", "medium", "high")}
    extent = {x["level"]: x for x in synthetic_items("extent")}

    def mean_pair(left: list[dict], right: list[dict]) -> float:
        return mean([cosine(vectors[a["key"]], vectors[b["key"]]) for a in left for b in right])

    result = {
        "model": {
            "id": MODEL_ID,
            "active_text_parameters_millions": ACTIVE_TEXT_PARAMETERS_M,
            "embedding_dimensions": len(vectors_list[0]),
            "prompt": "task: clustering | query:",
            "max_tokens_per_text": MAX_TOKENS,
            "device": device,
            "text_only_configuration": {"vision_config": None, "audio_config": None},
        },
        "data": {
            "real_articles": 20,
            "real_outbreak_articles": len(real_outbreak),
            "real_general_news_articles": len(real_general),
            "synthetic_sentences": len(synthetic),
            "real_article_selection": [
                {k: article.get(k) for k in ("sample_id", "article_index", "date", "source", "title", "url", "label", "disease")}
                for article in real
            ],
        },
        "tests": {
            "real_disease_separation": {
                **pair_scores(real_disease, vectors, "disease"),
                "leave_one_out_disease_centroid": leave_one_out_centroid_accuracy(real_disease, vectors, "disease"),
                "same_disease_pairs": disease_pairs,
            },
            "real_outbreak_vs_general_news": {
                "mean_outbreak_to_outbreak_cosine": mean(outbreak_internal),
                "mean_general_to_general_cosine": mean(general_internal),
                "mean_outbreak_to_general_cosine": mean(cross_class),
                "leave_one_out_label_centroid": leave_one_out_centroid_accuracy(real, vectors, "label"),
                "interpretation_note": "Source and topic differ between groups, so this real-data comparison is descriptive; controlled synthetic comparisons reduce that confound.",
            },
            "synthetic_disease_name_swap": {
                **disease_syn_scores,
                "leave_one_out_disease_centroid": leave_one_out_centroid_accuracy(disease_synthetic, vectors, "disease"),
                "same_disease_pair_cosines": {
                    disease: cosine(vectors[pair[0]["key"]], vectors[pair[1]["key"]])
                    for disease, pair in ((d, [x for x in disease_synthetic if x["disease"] == d]) for d in DISEASES)
                },
            },
            "synthetic_disease_specific_context": {
                **pair_scores(disease_context, vectors, "disease"),
                "leave_one_out_disease_centroid": leave_one_out_centroid_accuracy(disease_context, vectors, "disease"),
            },
            "synthetic_outbreak_relevance": {
                "active_vs_incidental_disease_mention_cosine": cosine(vectors[relevance["active_outbreak"]["key"]], vectors[relevance["incidental_mention"]["key"]]),
                "active_vs_unrelated_news_cosine": cosine(vectors[relevance["active_outbreak"]["key"]], vectors[relevance["unrelated_news"]["key"]]),
                "incidental_vs_unrelated_news_cosine": cosine(vectors[relevance["incidental_mention"]["key"]], vectors[relevance["unrelated_news"]["key"]]),
            },
            "synthetic_intensity": {
                "mean_within_level_cosine": mean(intensity_within),
                "mean_between_level_cosine": mean(intensity_between),
                "low_to_medium_cosine": mean_pair(intensity_level["low"], intensity_level["medium"]),
                "medium_to_high_cosine": mean_pair(intensity_level["medium"], intensity_level["high"]),
                "low_to_high_cosine": mean_pair(intensity_level["low"], intensity_level["high"]),
            },
            "synthetic_geographic_extent": {
                "one_area_to_several_districts_cosine": cosine(vectors[extent["one_area"]["key"]], vectors[extent["several_districts"]["key"]]),
                "several_districts_to_many_regions_cosine": cosine(vectors[extent["several_districts"]["key"]], vectors[extent["many_regions"]["key"]]),
                "one_area_to_many_regions_cosine": cosine(vectors[extent["one_area"]["key"]], vectors[extent["many_regions"]["key"]]),
            },
        },
        "limitations": [
            "This is a small exploratory test, not a trained detector or a warning system.",
            "WHO outbreak reports and Arkansas local-news articles have different publishers and writing styles; that can help separate the real groups independently of outbreak relevance.",
            "Embedding similarity is not a calibrated probability, and a vector change does not prove that the model understood the cause of the change.",
            "The real disease groups contain only two reports per disease. Artificial examples isolate wording changes but are not a substitute for labeled evaluation data.",
            "Long article bodies are truncated at 2,048 tokens before embedding.",
        ],
    }
    RESULTS.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"results_file": str(RESULTS), "model": result["model"], "tests": result["tests"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
