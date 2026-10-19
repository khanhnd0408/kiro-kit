# ML / DL rigor checklist

Use for any modelling decision or result claim. Fill as JSON for `score_decision.py`. A "yes" needs evidence (file:line, command output, run id).

| ID | Question | Weight | Blocker |
|----|----------|--------|---------|
| M1 | Is the target, unit of prediction and decision it supports defined? | 3 | yes |
| M2 | Is the split by the right unit (time, user, group) so no leakage from future or from the same entity? | 3 | yes |
| M3 | Was every preprocessing step (scaling, imputation, encoding, feature selection) fit on training data only? | 3 | yes |
| M4 | Is there a trivial baseline (majority class, mean, last value, linear model) with its score? | 3 | |
| M5 | Is the metric chosen for the decision cost (not accuracy on imbalanced data)? | 3 | |
| M6 | Is uncertainty reported: cross-validation spread, bootstrap interval, or repeated seeds? | 3 | |
| M7 | Is the improvement over the baseline larger than the noise (paired test or non-overlapping interval)? | 3 | |
| M8 | Are results from at least 3 seeds (deep learning) with mean and spread? | 2 | |
| M9 | Was the test set used once, after all choices were made? | 3 | yes |
| M10 | Were class balance, missing values and outliers examined and handled on purpose? | 2 | |
| M11 | Are errors inspected by slice (segment, time, rare classes), with the worst slice reported? | 2 | |
| M12 | Is the run reproducible: seed, data version, code commit, environment recorded? | 2 | |
| M13 | For deep learning: training and validation curves checked for over/underfitting, and learning rate / early stopping justified? | 2 | |
| M14 | Is calibration checked when probabilities are used for decisions? | 2 | |
| M15 | Is inference cost (latency, memory) measured against the requirement? | 1 | |
| M16 | Is drift monitoring defined (input distribution, prediction distribution, delayed labels)? | 1 | |

Statistical habits:
- Report an interval for every metric. `score_decision.py wilson K N` covers proportions (accuracy, pass rate). For other metrics use a bootstrap with at least 1000 resamples and say so.
- A p-value is not an effect size. Give both, and say which test and why it fits (paired or unpaired, parametric or not).
- Multiple comparisons: if you tried many variants, the best one is optimistic. Say how many were tried.
- Report the confidence level only as printed by the script.
