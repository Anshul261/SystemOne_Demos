# d1 experiment results

| File | Run | Data |
|---|---|---|
| `stamps_3b.json`, `stamps_omni.json` | `probe_stamps.py`, first stamp probe | First synthetic set (54 clean documents, 9 classes), since regenerated |
| `omni_choices.json` | `probe_omni_choices.py`, omni question wording | First synthetic set |
| `real_3b.json`, `real_omni.json` | `probe_real.py` | StaVer validation split and SIFTA (Hugging Face) |
| `calib_3b.json`, `calib_omni.json`, `*.log` | `calibrate.py` | `stamps_calib` (calibration) and `stamps_test` (test), 110 documents each |
| `flow_eval.json` | `evaluate_flow.py`, the demo's decision code on the test split | `stamps_test` |

The demo's thresholds come from the `calib_*` files. The first-set files record how the
question design evolved. They can't be regenerated, because `make_stamps.py` now produces
the newer, harder data. Generated images live in the Hugging Face buckets
`AnshulRaj/systemone-d1-eval-data` and `AnshulRaj/systemone-d1-results`.
