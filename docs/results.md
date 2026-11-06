# BIRD results log

Sample: `eval/bird/out/sample300_seed0.json`, 299 questions stratified over the 11 dev databases, seed 0. Baseline: claude-sonnet-5-5, DDL + 3 sample rows, no evidence. Reading proposer and judge: claude-sonnet-5-5.

## Run 1, 6 Nov 2026 (prior floor 0.15)

| metric | value |
|---|---|
| baseline accuracy | 61.9% |
| flip rate | 14.4% (43/299) |
| flip rate on wrong answers | 18.4% |
| flip rate on right answers | 11.9% |
| catch rate, strict (fork branch reproduces gold) | 1.8% (2/114) |
| noise rate, LLM judge, forks on right answers | 50% (13/26) |
| forks by kind | column_choice 22, value_literal 26 |
| dependencies surviving the LLM gate, mean per question | 0.24 |

Against the plan's thresholds: flip rate in the tune-and-remeasure band (5 to 15%); noise above the one-third bar.

What the wrong answers were wrong about (114): scalar value differs 43, different column count 24, different row count 28, extra or missing columns with the same rows 9, same shape different values 8, baseline empty 2. Roughly a third of baseline errors are projection mistakes a reading cannot fix, which caps the strict catch rate.

Noise patterns in the 13 rejected forks: 4 were invented code reversals ("'+' means non-carcinogenic") with no data support; 8 sat at a prior of 0.15 to 0.17 with hedged descriptions ("the question says X, but..."). Plausible forks mostly sat at 0.2 or above.

Recall misses seen on wrong answers: two date columns with dissimilar names (`First Date` vs `Description`), plural/singular column pairs (`types` vs `originalType`), and duplicate handling (`COUNT(type)` vs `COUNT(DISTINCT type)`, which is a v0.2 reading kind).

Changes for run 2: prior floor 0.2; proposer told not to reverse codes without evidence and not to hedge; any two date columns in a table are column-choice candidates; plural stemming in name similarity; loose catch metric added (a fork names a column or literal the gold SQL uses and the baseline does not).

## Run 2, 6 Nov 2026 (prior floor 0.2, tuned proposer)

| metric | value |
|---|---|
| flip rate | 13.0% (39/299) |
| flip rate on wrong answers | 15.8% |
| flip rate on right answers | 11.4% |
| catch rate, strict | 3.5% (4/114) |
| catch rate, strict, on value or row-count mismatches only | 5.1% (4/79) |
| noise rate, LLM judge | 41.7% (10/24) |
| forks by kind | column_choice 25, value_literal 23 |

Two of the four strict catches came from the run-2 additions: the dropped-filter reading (question 29, the agent's invented grade filter) and the date-column choice (question 1162, `First Date` vs `Description`).

### Prior floor sweep (run-2 data, no re-run)

A fork survives the floor if any alternative reading has at least that prior.

| floor | flip rate | flip on wrong | noise (judge) | forks on right answers | strict catch |
|---|---|---|---|---|---|
| 0.20 | 13.0% | 15.8% | 42% (10/24) | 24 | 3.5% |
| 0.25 | 8.0% | 10.5% | 27% (4/15) | 15 | 2.6% |
| 0.30 | 7.4% | 9.6% | 21% (3/14) | 14 | 2.6% |
| 0.35 | 4.3% | 4.4% | 10% (1/10) | 10 | 0.9% |

The proposer's prior is a strictness dial. No setting clears both bars at once: the permissive end gives 13% flip at 42% noise, the strict end gives 8% flip at 27% noise. The honest launch framing is a pair of numbers at two strictness levels, with the human-labelled noise rate next to each.

The judge is also the weak instrument here. Several forks it rejected are the kind a reviewer would want surfaced (blue eyes including 'Yellow/Blue'; 'names' as superhero name vs real name; 'September Meeting' vs all September events). The 50-fork human label sheet from run 2 settles whether the judge is too strict: `eval/bird/out/run2_sample300_seed0.labels.csv`.

### Recall analysis, run 2

Of 79 wrong answers that differ from gold by value or row count, Fork caught 4. Reading the other 75:

- **Duplicate handling dominates.** `SELECT DISTINCT` vs not, `COUNT(x)` vs `COUNT(DISTINCT x)`, `LIKE '%Flying%'` vs `= 'Flying'` on comma-separated columns, and ties under `LIMIT 1` account for roughly a third of them (questions 210, 343, 354, 376, 406, 444, 448, 452, 483, 681, 802, 805 in the sample). This is the "duplicates and boundaries" reading kind deferred from v0; it is the largest single recall lever left.
- **BIRD evidence thresholds.** "Positive comment refers to score > 60", "elder user refers to Age > 65", "teenage is 13 to 18". No reading of the schema recovers an arbitrary threshold; these are irreducible without the hint and should be excluded from the catch denominator in the write-up.
- **Join path and aggregation shape.** Different join routes (postHistory vs posts), percentage formulas, LEFT vs INNER join. Out of scope for a reading checker.
