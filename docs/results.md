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

## Run 2

Pending.
