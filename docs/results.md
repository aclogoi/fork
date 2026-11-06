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

## Run 3, 6 Nov 2026 (duplicates reading kind added, prior floor 0.2)

New dependency kind `duplicates`: `SELECT DISTINCT` toggled in both directions, `COUNT(col)` vs `COUNT(DISTINCT col)`, and `ORDER BY x LIMIT 1` expanded to every row tied for first. Value literals also gained `LIKE '%v%'` vs `= 'v'` in both directions, proposed only when the data shows the other form matters (other cells hold the value with more text, or the value also occurs on its own).

| metric | value |
|---|---|
| flip rate | 30.8% (92/299) |
| flip rate on wrong answers | 40.4% |
| catch rate, strict | 13.2% (15/114) |
| catch rate, loose | 24.6% |
| noise rate, LLM judge | 57.1% (28/49) |
| forks by kind | duplicates 57, value_literal 29, column_choice 21 |

The duplicates kind is the recall lever the run-2 analysis predicted: strict catch went from 4 to 15, and 6 of the 12 duplicate-handling misses listed under run 2 are now caught (the other 6 need a different column or join). It also brought the noise: 18 of the 28 rejected forks were duplicates forks, and 12 of those 18 were one shape, *removing* the agent's `SELECT DISTINCT`. The repeats that exposes come from join fan-out (a molecule once per atom, a patient once per lab record), which is never a reading of the question. The judge was right on all 12.

Changes for run 4: the DISTINCT-removal direction is no longer a dependency; a tie expansion counts as a flip only when the tied rows differ from the first row (a count repeated eight times is the same answer).

## Run 4, 6 Nov 2026 (duplicates without DISTINCT removal, prior floor 0.2)

| metric | value |
|---|---|
| flip rate | 24.4% (73/299) |
| flip rate on wrong answers | 32.5% |
| flip rate on right answers | 19.5% |
| catch rate, strict | 9.6% (11/114) |
| catch rate, strict, on value or row-count mismatches only | 13.9% |
| catch rate, loose | 17.5% |
| noise rate, LLM judge | 45.9% (17/37) |
| forks by kind | value_literal 29, column_choice 24, duplicates 26 |
| dependencies surviving the LLM gate, mean per question | 0.50 |

Strict catches by reading: column substitution 3, `COUNT(DISTINCT)` to `COUNT` 3, added DISTINCT 1, `LIKE` to equals 1, the rest through interaction branches.

Noise by reading on correct answers (implausible / judged): column substitution 5/10, added DISTINCT 2/8, `LIKE`-contains 3/6, tie expansion 2/3, dropped filter 2/3, literal replacement 2/2. Of the 17 rejected forks, 12 carry a prior of exactly 0.2, the floor: the proposer hugs the floor when it is unsure.

### Prior floor sweep (run-4 data, no re-run)

The catch column here compares summarised answers and understates the exact figure (9.6% at 0.2).

| floor | flip rate | flip on wrong | noise (judge) | forks on right answers | catch (approx.) |
|---|---|---|---|---|---|
| 0.20 | 23.7% | 32.5% | 46% (17/37) | 37 | 7.0% |
| 0.25 | 16.1% | 22.8% | 25% (6/24) | 24 | 4.4% |
| 0.30 | 12.7% | 18.4% | 17% (3/18) | 18 | 4.4% |
| 0.35 | 7.4% | 11.4% | 11% (1/9) | 9 | 3.5% |

At a floor of 0.25 both gate bars clear on the judge's numbers: flip above 15%, noise under a third. That is now the default `min_prior`. Run 5 measures it directly rather than by post-filtering run 4.

Rejected forks a reviewer might still want: 'Business' matching 'Business Administration' (prior 0.45), blue eyes including 'Yellow / Blue' (0.22). The human label sheet for run 4 is `eval/bird/out/run4_sample300_seed0.labels.csv`.

## Run 5, 6 Nov 2026 (prior floor 0.25, the new default)

Same code as run 4 with the default floor raised to 0.25, measured directly.

| metric | value |
|---|---|
| flip rate | 17.1% (51/299) |
| flip rate on wrong answers | 24.6% |
| flip rate on right answers | 12.4% |
| catch rate, strict | 8.8% (10/114) |
| catch rate, strict, on value or row-count mismatches only | 12.7% |
| catch rate, loose | 11.4% |
| noise rate, LLM judge | 37.5% (9/24) |
| forks by kind | duplicates 22, value_literal 21, column_choice 12 |
| dependencies surviving the LLM gate, mean per question | 0.37 |
| mean check time | 2.0 s |

Against the plan's thresholds: flip rate clears 15%. Noise is 9 of 24 on the judge, just above the one-third bar; the run-4 post-filter at the same floor was 6 of 24. At this sample size the judge's verdict on three forks separates the two readings, so the human label sheet decides: `eval/bird/out/run5_sample300_seed0.labels.csv`.

The nine rejected forks: two tie expansions on "7th highest" / "333rd highest" questions (`LIMIT 1 OFFSET n`, where the alternative returns every row tied at that rank), two added DISTINCTs on list questions, two `LIKE`-contains readings ('Business' covering 'Business Administration' at prior 0.5; blue eyes covering 'Yellow / Blue'), one column substitution, one code set, one cross-table id reading. The two `LIKE` readings are the kind a reviewer would want surfaced.

Across runs 2 to 5 the duplicates kind moved strict catch from 4 to 10 wrong answers at the stricter floor, and from 4 to 11 at the old one.

### Summary across runs (299-question sample)

| run | change | floor | flip | noise (judge) | catch strict |
|---|---|---|---|---|---|
| 1 | first measurement | 0.15 | 14.4% | 50% | 1.8% |
| 2 | tuned proposer, date columns, dropped filters | 0.20 | 13.0% | 42% | 3.5% |
| 3 | duplicates kind, both DISTINCT directions | 0.20 | 30.8% | 57% | 13.2% |
| 4 | no DISTINCT removal, same-row ties collapse | 0.20 | 24.4% | 46% | 9.6% |
| 5 | run 4 code at floor 0.25 | 0.25 | 17.1% | 38% | 8.8% |
