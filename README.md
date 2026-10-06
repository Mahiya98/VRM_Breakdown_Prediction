# VRM breakdown outlook

Dashboard for the next 5 days of **electrical and mechanical breakdowns** on VRM-1 and VRM-2.

```
Google Sheet (tab "VRM Breakdown")
        |  GitHub Action, daily 06:30 BD time
        v
ml/fetch_sheet.py  ->  ml/forecast.py  ->  data/*.json  ->  index.html (GitHub Pages)
```

## What the model does
* Only Electrical Breakdown and Mechanical Breakdown rows are used. Duplicate entries are removed, shutdown days and days with no log are excluded from training.
* **Stage 1** estimates the chance of an E/M breakdown on each of D+1..D+5 (pooled XGBoost, Platt-calibrated).
* **Stage 2** ranks equipment families (Fan, Bag House, Belt Conveyor...) given a breakdown (CatBoost blended with a recency-weighted frequency). The family comes from the equipment recorded in each breakdown.
* Chosen after a walk-forward comparison of the six models in the design guide (frequency baseline, Logistic Regression, Random Forest, XGBoost, CatBoost, LightGBM).

## Honest limits
Breakdown history alone predicts *which equipment* better than chance but barely predicts *which day*. The chance shown stays close to each mill's usual rate. Sensor, run-hour or maintenance data would improve it. The dashboard's backtest and live-tracking sections show the real hit rate.

## Setup
1. **Settings > Secrets and variables > Actions**: add `SHEET_ID` (the long id in the sheet URL) and either
   * `GOOGLE_SERVICE_ACCOUNT_JSON` (recommended): create a Google service account, share the sheet with its email as *Viewer*, paste the key JSON; or
   * nothing else, if the sheet is shared as *Anyone with the link can view* (simpler, but anyone with the link can read the whole workbook).
2. **Settings > Pages**: deploy from branch `main`, folder `/ (root)`.
3. Run the workflow once from the **Actions** tab (*Daily forecast > Run workflow*).

The sheet id is kept in a secret so the public repo does not expose it.

## Run locally
```
pip install -r ml/requirements.txt
SHEET_ID=... python ml/fetch_sheet.py breakdown.csv
python ml/forecast.py breakdown.csv ml/reason_master.csv data
python -m http.server   # open http://localhost:8000
```
`ml/reason_master.csv` is the Dropdown Data tab (closed set of reasons).
