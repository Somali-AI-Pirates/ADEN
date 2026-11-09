# Strict role refinement

Offline comparison against the archived selected baseline. Metrics use the same page evidence and 4,000-token context packer.

## holdout

| Arm | Scored | Changed contexts | Page recall@8 | Quote recall | Doc recall@8 |
|---|---:|---:|---:|---:|---:|
| baseline | 280 | 0 | 0.8777 | 0.8861 | 0.8911 |
| strict_roles | 280 | 32 | 0.8777 | 0.8861 | 0.8946 |
| strict_followup | 280 | 134 | 0.8667 | 0.8732 | 0.8818 |

## all_380

| Arm | Scored | Changed contexts | Page recall@8 | Quote recall | Doc recall@8 |
|---|---:|---:|---:|---:|---:|
| baseline | 378 | 0 | 0.8609 | 0.8658 | 0.8909 |
| strict_roles | 378 | 39 | 0.8616 | 0.8664 | 0.8942 |
| strict_followup | 378 | 195 | 0.8490 | 0.8547 | 0.8823 |

## old_340

| Arm | Scored | Changed contexts | Page recall@8 | Quote recall | Doc recall@8 |
|---|---:|---:|---:|---:|---:|
| baseline | 338 | 0 | 0.8577 | 0.8632 | 0.8868 |
| strict_roles | 338 | 39 | 0.8585 | 0.8640 | 0.8905 |
| strict_followup | 338 | 164 | 0.8444 | 0.8494 | 0.8772 |

## development

| Arm | Scored | Changed contexts | Page recall@8 | Quote recall | Doc recall@8 |
|---|---:|---:|---:|---:|---:|
| baseline | 29 | 0 | 0.7557 | 0.7385 | 0.8448 |
| strict_roles | 29 | 2 | 0.7557 | 0.7385 | 0.8448 |
| strict_followup | 29 | 15 | 0.7385 | 0.7299 | 0.8534 |

## official

| Arm | Scored | Changed contexts | Page recall@8 | Quote recall | Doc recall@8 |
|---|---:|---:|---:|---:|---:|
| baseline | 58 | 0 | 0.7615 | 0.7529 | 0.8664 |
| strict_roles | 58 | 7 | 0.7658 | 0.7572 | 0.8707 |
| strict_followup | 58 | 30 | 0.7371 | 0.7342 | 0.8549 |

## additional

| Arm | Scored | Changed contexts | Page recall@8 | Quote recall | Doc recall@8 |
|---|---:|---:|---:|---:|---:|
| baseline | 29 | 0 | 0.7672 | 0.7672 | 0.8879 |
| strict_roles | 29 | 5 | 0.7759 | 0.7759 | 0.8966 |
| strict_followup | 29 | 15 | 0.7356 | 0.7385 | 0.8563 |

## pilot

| Arm | Scored | Changed contexts | Page recall@8 | Quote recall | Doc recall@8 |
|---|---:|---:|---:|---:|---:|
| baseline | 40 | 0 | 0.8875 | 0.8875 | 0.9250 |
| strict_roles | 40 | 0 | 0.8875 | 0.8875 | 0.9250 |
| strict_followup | 40 | 31 | 0.8875 | 0.9000 | 0.9250 |
