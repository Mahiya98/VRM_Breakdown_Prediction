# VRM breakdown outlook

Dashboard for **equipment overdue alerts** and **equipment-family forecasts** on VRM-1 and VRM-2.

```
Google Sheet (tab "VRM Breakdown")
        |  GitHub Action, daily 06:30 BD time
        v
ml/fetch_sheet.py  ->  ml/forecast.py  ->  data/*.json  ->  index.html (GitHub Pages)
```

## What the model does

### Overdue alerts (statistical)
Each equipment family has a historical interval between E/M breakdowns. When the current gap (days since last failure) exceeds the 75th-percentile interval, that family is flagged **OVERDUE**. The risk score = current_gap / p75_gap — a score of 2.0× means the equipment has gone twice as long as usual without breaking.

### Equipment forecast (CatBoost ML)
For the next 5 days, a CatBoost model (blended 50/50 with recency-weighted frequency) ranks the two most likely equipment families *if* an E/M breakdown occurs. Backtest: VRM-1 66% top-2 hit rate (vs 44% baseline), VRM-2 48% (vs 22% baseline).

## Honest limits
Breakdown history alone can tell you *which equipment is overdue* and *which is most likely to fail next*, but it cannot predict *which specific day* a breakdown will happen. Sensor data (vibration, temperature, pressure, run hours) would enable true day-ahead prediction. The dashboard's backtest and live-tracking sections show the real hit rates.

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
