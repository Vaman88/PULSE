# Synthetic-signal alignment audit method

`audit_signal_alignment.py` examined whether dated signal columns varied
near event tags in a synthetic pharmacy sales series. It compared event
timing with available signal dates, checked near-static and duplicate
columns, and inspected the relationship of constructed tags to the sales
generator. The associated input tables were in the excluded research
`data/` tree, so this audit cannot be rerun from the local copy alone.

This diagnostic belongs to the archived synthetic-sales branch. It is not
part of Prototype 2 training or the current API path.
