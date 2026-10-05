# bench/entities: labelled entity-resolution pairs

* `build_pairs.py` builds `items/dev.jsonl`, `items/test.jsonl` and `items/checksums.json` (stratified, deterministic).
* `eval_resolver.py --split dev|test [--errors] [--json out]` scores the lexical resolver: precision, recall, false-merge
  rate per threshold with Wilson 95% intervals. It refuses to run if a split's checksum differs.
* `results/` holds the committed outputs (`dev.txt`, `test.txt`, `*.json`).

Rules were tuned on dev. **The test split was scored once, after the final rule change** (`results/test.txt`); do not
tune against it. If you change `src/palimem/entities/resolver.py`, rebuild nothing: add pairs only as new files or a new
version, and report test again as a new, dated result. The labels were written by one author with LLM assistance; some are
arguable (`J. Smith` / `Jane Smith`) and are kept as written. See `docs/ENTITIES.md` section 7 for the numbers and limits.
