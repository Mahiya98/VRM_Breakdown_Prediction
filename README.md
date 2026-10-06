# VRM breakdown outlook

Dashboard for the next 5 days of **Electrical, Mechanical & Other breakdowns** on VRM-1 and VRM-2.

```
Google Sheet (tab "VRM Breakdown")
        |  GitHub Action, daily 06:30 BD time
        v
ml/fetch_sheet.py  ->  ml/forecast.py  ->  data/*.json  ->  index.html (GitHub Pages)
```

## What the model does
* All breakdown types are used (Electrical, Mechanical, and Other — which includes utility outages, warehouse blocks, raw-material shortages, and in-process adjustments). Planned Down Time is excluded.
* **Type forecast** predicts the share of Electrical / Mechanical / Other for each of D+1…D+5 using a recency-weighted frequency mix (half-life 120 days) per mill. Walk-forward CV confirmed this beats XGBoost, CatBoost, Random Forest and Logistic Regression on this data.
* **Equipment forecast** ranks equipment families (Fan, Bag House, Belt Conveyor…) for Electrical and Mechanical days only (CatBoost blended 50/50 with recency-weighted frequency). "Other" breakdowns show no equipment.

## Honest limits
Breakdown history alone predicts *which type* well (~92% top-hit rate) and *which equipment* better than chance, but the daily type mix stays close to each mill's usual pattern. Sensor, run-hour or maintenance data would improve it. The dashboard's backtest and live-tracking sections show the real hit rates.

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
