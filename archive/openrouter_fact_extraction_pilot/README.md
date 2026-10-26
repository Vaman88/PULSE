# Archived fact extraction pilot

`extract_candidate_facts.py` reads a review index, sends batches of
article excerpts to the configured OpenRouter model, validates literal
fact fields, and writes `candidate_facts.jsonl`. It requires an API key and
its expected `review_index.sqlite`, which is not in this copy.
`export_candidate_rows.py` exports candidate rows. The included JSONL
and compressed rows are archived pilot artifacts, not inputs to the current
FastAPI service or Prototype 2 inference.

No automatic path promotes these candidate facts into training labels.
