# BIRD evaluation

The launch number is the share of BIRD dev questions whose answer flips under a plausible alternative reading of a column or value. Everything here is reproducible from `eval/bird/`.

## Setup

BIRD dev (1534 questions, 11 SQLite databases, June 2024 release) is downloaded to `data/bird/raw/dev_20240627/`. Each question has a natural-language question, an "evidence" hint explaining the ambiguous columns, and a gold SQL. The evidence is stripped: the baseline agent and Fork never see it.

## Baseline agent

`eval/bird/baseline.py`. One model call per question. The prompt is the DDL of every table in the database, three sample rows per table, and the question. No evidence, no retries, no self-correction. The model is `claude-sonnet-5-5` unless `FORK_MODEL` is set. This is deliberately not a state-of-the-art agent: the number Fork measures is about the agent's silent choices, not its SQL skill, and the catch rate needs a baseline that is sometimes wrong.

## Sample

`eval/bird/sample.py --n 300 --seed 0` takes a stratified sample, proportional per database with a floor of 10, giving 299 questions. The full set is `--all`.

## Metrics

`eval/bird/run.py` runs `fork.check` over each baseline SQL and writes one JSON line per question plus a summary. Over questions where the gold SQL and the baseline SQL both run:

- **Flip rate**: share with at least one fork, i.e. a plausible alternative reading changes the answer. Answers are compared as row multisets with relative tolerance 1e-6, ordered only when the query has a top-level ORDER BY. A branch that errors where the base did not also counts as a flip.
- **Catch rate**: among questions the baseline got wrong (baseline result differs from gold result), share where some fork's alternative branch returns exactly the gold result. This is strict: the fork must contain the right answer, not merely point at the right column.
- **Noise rate**: among forks raised on questions the baseline got right, share an LLM judge calls implausible. The judge sees the question, the agent's SQL, the two readings, and the evidence rows. A 50-fork random sample is written to `*.labels.csv` for a human to label; the judge's agreement with those labels is reported alongside.

Gate thresholds from the plan: flip rate under 5% stops the project; above 15% with noise under a third is a launch post; in between, tune the reading generator for one more week.

## Running

```
cp .env.example .env   # add ANTHROPIC_API_KEY
python eval/bird/sample.py --n 300 --seed 0
python eval/bird/baseline.py --questions eval/bird/out/sample300_seed0.json
python eval/bird/run.py --questions eval/bird/out/sample300_seed0.json
```

Outputs land in `eval/bird/out/` (gitignored): `baseline.jsonl`, `run_<sample>.jsonl`, `run_<sample>.summary.json`, `run_<sample>.labels.csv`.
