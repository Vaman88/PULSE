# Prototype 1 artifact guide

This directory contains the earlier classifier training script
(`prototype1.py`), its saved checkpoint (`prototype1.pt`), a cached FDA
embedding file (`fda_record_embeddings.json.gz`). The current API uses
Prototype 2 instead.

For how the classifier was trained and how to call its `predict` function,
see [approach.md](approach.md). Retraining needs the omitted
`data/train_test_dataset/` source, label, and article-embedding files.
