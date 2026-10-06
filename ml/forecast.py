"""Daily job: fit the selected two-stage model on all history and write the dashboard data.
Stage 1  P(electrical/mechanical breakdown on D+h): pooled XGBoost (all horizons, both mills) + Platt calibration.
Stage 2  P(equipment family | breakdown): pooled CatBoost blended 50/50 with a recency-weighted frequency (half-life 120 days).
Only electrical + mechanical breakdowns are modelled. Shutdown days and days with no log at all are excluded from training.
Usage: python forecast.py breakdown.csv reason_master.csv ../data"""
import sys, os, json, warnings, datetime as dt, numpy as np, pandas as pd
warnings.filterwarnings('ignore'); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import features as FE
import xgboost as xgb
from catboost import CatBoostClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
SRC, DROP, OUT = sys.argv[1:4]; HL, BLEND = 120, 0.5; VERSION = 'v3 pooled-XGBoost + CatBoost/recency blend'
OTHER = {'VRM-1': 'VRM-2', 'VRM-2': 'VRM-1'}
META = ['target_date', 'state_t', 'y', 'known', 'valid', 'primary', 'all_reasons', 'origin']

raw_all = pd.read_csv(SRC); raw = FE.load_breakdowns(SRC); uni = FE.load_reason_universe(DROP); daily = FE.build_daily(raw, uni)
last = daily['VRM-1'].index.max()
ROWS = {(m, h): FE.make_rows(daily[m], h, False, 'equip', daily[OTHER[m]]).assign(mill_v2=int(m == 'VRM-2'), mill=m) for m in FE.MILLS for h in FE.HORIZONS}
ALL = [c for c in ROWS[('VRM-1', 1)].columns if c not in META + ['mill']]

def xgb_model(): return xgb.XGBClassifier(n_estimators=200, max_depth=3, learning_rate=0.05, subsample=.8, colsample_bytree=.8, min_child_weight=5, eval_metric='logloss', n_jobs=2, random_state=0)
def cat_model(): return CatBoostClassifier(iterations=80, depth=4, learning_rate=0.1, loss_function='MultiClass', verbose=0, thread_count=2, random_seed=0)
def platt(pc, yc):
    z = lambda p: np.log(np.clip(p, 1e-4, 1 - 1e-4) / (1 - np.clip(p, 1e-4, 1 - 1e-4)))
    lr = LogisticRegression(C=1e6).fit(z(pc)[:, None], yc); return lambda p: lr.predict_proba(z(p)[:, None])[:, 1]
def recency_freq(df, classes, now):
    ev = df[(df.y == 1) & df.primary.notna()]; w = 0.5 ** ((now - ev.target_date).dt.days / HL)
    c = ev.assign(w=w).groupby('primary').w.sum().reindex(classes).fillna(0) + 0.5; return (c / c.sum()).values
def fit_stage1(tr):
    tr = tr.sort_values('target_date'); k = int(len(tr) * .8); a, b = tr.iloc[:k], tr.iloc[k:]
    m = xgb_model().fit(a[ALL], a.y); pc = m.predict_proba(b[ALL])[:, 1]; pl = platt(pc, b.y.values)
    return (lambda X: pl(m.predict_proba(X[ALL])[:, 1])), tuple(np.quantile(pl(pc), [1 / 3, 2 / 3]))
def fit_stage2(tr, now):
    t = tr[(tr.y == 1) & tr.primary.notna()]; classes = sorted(t.primary.unique()); code = {c: i for i, c in enumerate(classes)}
    m = cat_model().fit(t[ALL], t.primary.map(code).values); cls = np.asarray(m.classes_).astype(int).ravel(); rec = {mm: recency_freq(tr[tr.mill == mm], classes, now) for mm in FE.MILLS}
    def predict(X, mill):
        P = np.zeros((len(X), len(classes))); P[:, cls] = m.predict_proba(X[ALL]); return BLEND * P + (1 - BLEND) * np.tile(rec[mill], (len(X), 1))
    return predict, classes
iso = lambda d: str(pd.Timestamp(d).date())

# ---------------- forecast D+1..D+5 ----------------
allr = pd.concat([r[r.valid] for r in ROWS.values()]); p1f, cuts = fit_stage1(allr); p2f, classes = fit_stage2(allr, last + pd.Timedelta(days=1))
fcs = []
for m in FE.MILLS:
    of = FE.origin_features(daily[m]).iloc[[-1]].copy(); o = FE.origin_features(daily[OTHER[m]]).iloc[-1]
    for k_, v in dict(x_ev_days_7=o.ev_days_7, x_ev_days_30=o.ev_days_30, x_lag_ev_0=o.lag_ev_0, x_lag_ev_1=o.lag_ev_1, x_since_ev=o.since_ev, x_util_min_7=o.util_min_7).items(): of[k_] = v
    of['mill_v2'] = int(m == 'VRM-2')
    for h in FE.HORIZONS:
        tgt = last + pd.Timedelta(days=h); x = of.copy(); x['h'] = h; x['tgt_dow'] = tgt.dayofweek; x['tgt_month'] = tgt.month; x['tgt_weekend'] = int(tgt.dayofweek >= 5)
        p = float(p1f(x)[0]); P = p2f(x, m)[0]; top = np.argsort(-P)[:2]
        fcs.append(dict(mill=m, horizon=h, forecast_date=iso(tgt), weekday=tgt.strftime('%a'), em_probability=round(p, 3),
                        risk_level='Higher than usual' if p >= cuts[1] else 'Typical' if p >= cuts[0] else 'Lower than usual',
                        families=[dict(family=classes[i], conditional=round(float(P[i]), 3), unconditional=round(float(P[i]) * p, 3)) for i in top]))

# ---------------- context for the dashboard ----------------
ctx = {}
for m in FE.MILLS:
    d = daily[m]; r60 = d.tail(60); r90 = d.tail(90); ok = r90[~r90.state.isin(['SHUTDOWN', 'DATA_GAP'])]; ok365 = d.tail(365)[~d.tail(365).state.isin(['SHUTDOWN', 'DATA_GAP'])]
    fam = r90[[c for c in r90.columns if c.startswith('grp_')]].sum().rename(lambda c: c[4:]).sort_values(ascending=False); fam = fam[fam > 0]
    ctx[m] = dict(daily=[dict(date=iso(i), electrical=int(rw.n_e), mechanical=int(rw.n_m), state=rw.state) for i, rw in r60.iterrows()],
                  families_90d=[dict(family=k, events=int(v)) for k, v in fam.items()], rate_90d=round(float((ok.ev > 0).mean()), 3), rate_365d=round(float((ok365.ev > 0).mean()), 3),
                  breakdowns_90d=int(r90.n_ev.sum()), shutdown_days_90d=int((r90.state == 'SHUTDOWN').sum()), gap_days_30d=int((d.tail(30).state == 'DATA_GAP').sum()))

# ---------------- out-of-sample backtest, last 90 days, 1 day ahead ----------------
start = last - pd.Timedelta(days=89); bt_rows = []; bt = {}
for m in FE.MILLS:
    r = ROWS[(m, 1)]; tr = pd.concat([x[x.valid & (x.target_date < start)] for x in ROWS.values()]); te = r[r.valid & (r.target_date >= start)]
    if m == FE.MILLS[0]: p1b, _ = fit_stage1(tr); p2b, cb = fit_stage2(tr, start); base = tr[(tr.y == 1) & tr.primary.notna()].primary.value_counts(normalize=True).reindex(cb).fillna(0).values
    p = p1b(te); P = p2b(te, m); hits, bhits, n_ev = 0, 0, 0
    for i, (_, rw) in enumerate(te.iterrows()):
        top = np.argsort(-P[i])[:2]; pred = [cb[j] for j in top]; act = [a for a in (rw.all_reasons if isinstance(rw.all_reasons, list) else [])]; ev = bool(rw.y == 1)
        hit = bool(ev and any(a in pred for a in act)); bhit = bool(ev and any(a in [cb[j] for j in np.argsort(-base)[:2]] for a in act)); n_ev += ev; hits += hit; bhits += bhit
        bt_rows.append(dict(date=iso(rw.target_date), mill=m, em_probability=round(float(p[i]), 3), predicted=pred, actual_breakdown=ev, actual_families=act, top2_hit=hit if ev else None))
    bt[m] = dict(days=int(len(te)), breakdown_days=int(n_ev), top2_hit_rate=round(hits / n_ev, 3) if n_ev else None, baseline_top2_hit_rate=round(bhits / n_ev, 3) if n_ev else None,
                 roc_auc=round(float(roc_auc_score(te.y, p)), 3) if 0 < te.y.mean() < 1 else None, breakdown_rate=round(float(te.y.mean()), 3))
bt_rows.sort(key=lambda x: (x['date'], x['mill']), reverse=True)

# ---------------- prediction history (forecast vs actual, filled in as days pass) ----------------
hp = os.path.join(OUT, 'prediction_history.json'); hist = json.load(open(hp)) if os.path.exists(hp) else []
run = dt.datetime.now(dt.timezone.utc).date().isoformat(); key = lambda e: (e['run_date'], e['mill'], e['horizon'])
new = {key(e): e for e in hist}
for f in fcs: new[(run, f['mill'], f['horizon'])] = dict(run_date=run, data_through=iso(last), mill=f['mill'], horizon=f['horizon'], forecast_date=f['forecast_date'], em_probability=f['em_probability'], predicted=[x['family'] for x in f['families']], actual_status='pending', actual_families=[], top2_hit=None)
for e in new.values():
    d = daily[e['mill']]; fd = pd.Timestamp(e['forecast_date'])
    if fd <= last and fd in d.index:
        s = d.loc[fd, 'state']; e['actual_status'] = {'E_M_BREAKDOWN': 'breakdown', 'NO_E_M_BREAKDOWN': 'none'}.get(s, 'excluded')
        e['actual_families'] = d.loc[fd, 'all_equip'] if isinstance(d.loc[fd, 'all_equip'], list) else []
        e['top2_hit'] = bool(any(a in e['predicted'] for a in e['actual_families'])) if s == 'E_M_BREAKDOWN' else None
hist = sorted(new.values(), key=lambda e: (e['run_date'], e['mill'], e['horizon']))[-1200:]
res = [e for e in hist if e['top2_hit'] is not None]

meta = dict(generated_at=dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC'), data_through=iso(last), model_version=VERSION, risk_cutoffs=[round(float(c), 3) for c in cuts],
            rows_in_sheet=int(len(raw_all.dropna(how='all'))), duplicate_rows_removed=int(len(raw_all.dropna(how='all')) - len(raw) - int((~raw_all.dropna(how='all')['Mill Name'].isin(FE.MILLS)).sum())))
json.dump(dict(meta=meta, forecasts=fcs, context=ctx), open(os.path.join(OUT, 'predictions.json'), 'w'), indent=1)
json.dump(dict(meta=dict(window=f'{iso(start)} to {iso(last)}', horizon_days=1, note='Out-of-sample: models were fitted only on data before the window.'), summary=bt, rows=bt_rows), open(os.path.join(OUT, 'backtest.json'), 'w'), indent=1)
json.dump(hist, open(hp, 'w'), indent=1)
print('forecast written for', iso(last + pd.Timedelta(days=1)), 'to', iso(last + pd.Timedelta(days=5)), '| backtest', bt, '| tracked forecasts resolved:', len(res))
