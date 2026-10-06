"""Daily job: fit the selected model on all history and write the dashboard data.
Only days with at least one breakdown are modelled (no 'no breakdown' class). Every forecast day answers: which type?
Type    P(Electrical / Mechanical / Other | breakdown day): recency-weighted mix per mill (half-life 120 days). In walk-forward tests XGBoost,
        CatBoost, random forest and logistic regression did not beat this mix, so it is the selected model.
Stage 2 P(equipment family | electrical/mechanical breakdown): pooled CatBoost blended 50/50 with a recency-weighted frequency. Not needed for 'Other'.
Planned down time and shutdown days are not breakdowns; days with no log at all are excluded.
Usage: python forecast.py breakdown.csv reason_master.csv ../data"""
import sys, os, json, warnings, datetime as dt, numpy as np, pandas as pd
warnings.filterwarnings('ignore'); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import features as FE
from catboost import CatBoostClassifier
SRC, DROP, OUT = sys.argv[1:4]; HL, BLEND = 120, 0.5; VERSION = 'v4 type mix (E/M/Other) + CatBoost equipment'
OTHER = {'VRM-1': 'VRM-2', 'VRM-2': 'VRM-1'}
META = ['target_date', 'state_t', 'y', 'known', 'valid', 'primary', 'all_reasons', 'origin']

raw_all = pd.read_csv(SRC); raw = FE.load_breakdowns(SRC); uni = FE.load_reason_universe(DROP); daily = FE.build_daily(raw, uni); TD = FE.type_daily(raw, daily); TC = FE.TYPE_CLASSES
last = daily['VRM-1'].index.max()
ROWS = {(m, h): FE.make_rows(daily[m], h, False, 'equip', daily[OTHER[m]]).assign(mill_v2=int(m == 'VRM-2'), mill=m) for m in FE.MILLS for h in FE.HORIZONS}
ALL = [c for c in ROWS[('VRM-1', 1)].columns if c not in META + ['mill']]

def cat_model(): return CatBoostClassifier(iterations=80, depth=4, learning_rate=0.1, loss_function='MultiClass', verbose=0, thread_count=2, random_seed=0)
def recency_freq(df, classes, now):
    ev = df[(df.y == 1) & df.primary.notna()]; w = 0.5 ** ((now - ev.target_date).dt.days / HL)
    c = ev.assign(w=w).groupby('primary').w.sum().reindex(classes).fillna(0) + 0.5; return (c / c.sum()).values
def type_mix(m, until, now):
    """Recency-weighted share of Electrical / Mechanical / Other among breakdown occurrences before `until`."""
    t = TD[m][TD[m].index < until]; w = 0.5 ** ((now - t.index).days / HL)
    c = np.array([(t['n_' + k].gt(0) * w).sum() for k in TC]) + 0.5; return c / c.sum()
def fit_stage2(tr, now):
    t = tr[(tr.y == 1) & tr.primary.notna()]; classes = sorted(t.primary.unique()); code = {c: i for i, c in enumerate(classes)}
    m = cat_model().fit(t[ALL], t.primary.map(code).values); cls = np.asarray(m.classes_).astype(int).ravel(); rec = {mm: recency_freq(tr[tr.mill == mm], classes, now) for mm in FE.MILLS}
    def predict(X, mill):
        P = np.zeros((len(X), len(classes))); P[:, cls] = m.predict_proba(X[ALL]); return BLEND * P + (1 - BLEND) * np.tile(rec[mill], (len(X), 1))
    return predict, classes
iso = lambda d: str(pd.Timestamp(d).date())

# ---------------- forecast D+1..D+5 ----------------
allr = pd.concat([r[r.valid] for r in ROWS.values()]); p2f, classes = fit_stage2(allr, last + pd.Timedelta(days=1)); now = last + pd.Timedelta(days=1)
fcs = []
for m in FE.MILLS:
    of = FE.origin_features(daily[m]).iloc[[-1]].copy(); o = FE.origin_features(daily[OTHER[m]]).iloc[-1]
    for k_, v in dict(x_ev_days_7=o.ev_days_7, x_ev_days_30=o.ev_days_30, x_lag_ev_0=o.lag_ev_0, x_lag_ev_1=o.lag_ev_1, x_since_ev=o.since_ev, x_util_min_7=o.util_min_7).items(): of[k_] = v
    of['mill_v2'] = int(m == 'VRM-2'); mix = type_mix(m, now, now)
    for h in FE.HORIZONS:
        tgt = last + pd.Timedelta(days=h); x = of.copy(); x['h'] = h; x['tgt_dow'] = tgt.dayofweek; x['tgt_month'] = tgt.month; x['tgt_weekend'] = int(tgt.dayofweek >= 5)
        P = p2f(x, m)[0]; top = np.argsort(-P)[:2]
        fcs.append(dict(mill=m, horizon=h, forecast_date=iso(tgt), weekday=tgt.strftime('%a'), types=[dict(type=k, share=round(float(v), 3)) for k, v in zip(TC, mix)], top_type=TC[int(np.argmax(mix))],
                        families=[dict(family=classes[i], conditional=round(float(P[i]), 3)) for i in top]))

# ---------------- context for the dashboard ----------------
ctx = {}
for m in FE.MILLS:
    d = daily[m]; t = TD[m]; r60 = d.tail(60); r90 = t.tail(90); r90d = d.tail(90)
    fam = r90d[[c for c in r90d.columns if c.startswith('grp_')]].sum().rename(lambda c: c[4:]).sort_values(ascending=False); fam = fam[fam > 0]
    ctx[m] = dict(daily=[dict(date=iso(i), electrical=int(t.loc[i, 'n_Electrical']), mechanical=int(t.loc[i, 'n_Mechanical']), other=int(t.loc[i, 'n_Other']), state=rw.state) for i, rw in r60.iterrows()],
                  families_90d=[dict(family=k, events=int(v)) for k, v in fam.items()],
                  mix_90d={k: int((r90['n_' + k] > 0).sum()) for k in TC}, event_days_90d=int(r90.evt.sum()), breakdowns_90d=int(r90d.n_ev.sum()),
                  shutdown_days_90d=int((r90d.state == 'SHUTDOWN').sum()), gap_days_30d=int((d.tail(30).state == 'DATA_GAP').sum()))

# ---------------- out-of-sample backtest, last 90 days, 1 day ahead (breakdown days only) ----------------
start = last - pd.Timedelta(days=89); bt_rows = []; bt = {}
tr_all = pd.concat([x[x.valid & (x.target_date < start)] for x in ROWS.values()]); p2b, cb = fit_stage2(tr_all, start)
base = tr_all[(tr_all.y == 1) & tr_all.primary.notna()].primary.value_counts(normalize=True).reindex(cb).fillna(0).values
for m in FE.MILLS:
    mix = type_mix(m, start, start); t = TD[m]; win = t[(t.index >= start) & (t.index <= last) & (t.evt > 0)]
    r = ROWS[(m, 1)]; te = r[r.valid & (r.target_date >= start)].set_index('target_date'); P = p2b(te, m); pos = {dt_: i for i, dt_ in enumerate(te.index)}
    hits = bhits = n_em = 0; actual_mix = {k: int((win['n_' + k] > 0).sum()) for k in TC}; top_ok = 0
    for dte, rw in win.iterrows():
        acts = [k for k in TC if rw['n_' + k] > 0]; top_ok += int(TC[int(np.argmax(mix))] in acts)
        pred, act_f, hit = None, [], None
        if dte in pos and any(k != 'Other' for k in acts):
            top = np.argsort(-P[pos[dte]])[:2]; pred = [cb[j] for j in top]; act_f = te.loc[dte, 'all_reasons'] if isinstance(te.loc[dte, 'all_reasons'], list) else []
            if act_f:
                n_em += 1; hit = bool(any(a in pred for a in act_f)); hits += hit; bhits += bool(any(a in [cb[j] for j in np.argsort(-base)[:2]] for a in act_f))
        bt_rows.append(dict(date=iso(dte), mill=m, predicted_types=[dict(type=k, share=round(float(v), 3)) for k, v in zip(TC, mix)], actual_types=acts, predicted_families=pred, actual_families=act_f, top2_hit=hit))
    n = len(win); pm = {k: round(float(v), 3) for k, v in zip(TC, mix)}
    bt[m] = dict(breakdown_days=int(n), predicted_mix=pm, actual_mix={k: round(v / max(1, sum(actual_mix.values())), 3) for k, v in actual_mix.items()}, top_type_hit_rate=round(top_ok / n, 3) if n else None,
                 em_days_scored=int(n_em), top2_hit_rate=round(hits / n_em, 3) if n_em else None, baseline_top2_hit_rate=round(bhits / n_em, 3) if n_em else None)
bt_rows.sort(key=lambda x: (x['date'], x['mill']), reverse=True)

# ---------------- prediction history (forecast vs actual, filled in as days pass) ----------------
hp = os.path.join(OUT, 'prediction_history.json'); hist = [e for e in (json.load(open(hp)) if os.path.exists(hp) else []) if 'types' in e]
run = dt.datetime.now(dt.timezone.utc).date().isoformat(); key = lambda e: (e['run_date'], e['mill'], e['horizon'])
new = {key(e): e for e in hist}
for f in fcs: new[(run, f['mill'], f['horizon'])] = dict(run_date=run, data_through=iso(last), mill=f['mill'], horizon=f['horizon'], forecast_date=f['forecast_date'], types=f['types'], top_type=f['top_type'], predicted=[x['family'] for x in f['families']], actual_status='pending', actual_types=[], actual_families=[], top_type_hit=None)
for e in new.values():
    d = daily[e['mill']]; fd = pd.Timestamp(e['forecast_date'])
    if fd <= last and fd in d.index:
        tt = TD[e['mill']].loc[fd]; acts = [k for k in TC if tt['n_' + k] > 0]
        e['actual_types'] = acts; e['actual_status'] = 'breakdown' if acts else ('excluded' if d.loc[fd, 'state'] in ('SHUTDOWN', 'DATA_GAP') else 'none')
        e['actual_families'] = d.loc[fd, 'all_equip'] if isinstance(d.loc[fd, 'all_equip'], list) else []
        e['top_type_hit'] = bool(e['top_type'] in acts) if acts else None
hist = sorted(new.values(), key=lambda e: (e['run_date'], e['mill'], e['horizon']))[-1200:]
res = [e for e in hist if e['top_type_hit'] is not None]

meta = dict(generated_at=dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC'), data_through=iso(last), model_version=VERSION,
            rows_in_sheet=int(len(raw_all.dropna(how='all'))), duplicate_rows_removed=int(len(raw_all.dropna(how='all')) - len(raw) - int((~raw_all.dropna(how='all')['Mill Name'].isin(FE.MILLS)).sum())))
json.dump(dict(meta=meta, forecasts=fcs, context=ctx), open(os.path.join(OUT, 'predictions.json'), 'w'), indent=1)
json.dump(dict(meta=dict(window=f'{iso(start)} to {iso(last)}', horizon_days=1, note='Out-of-sample: fitted only on data before the window; only days with a breakdown are scored.'), summary=bt, rows=bt_rows), open(os.path.join(OUT, 'backtest.json'), 'w'), indent=1)
json.dump(hist, open(hp, 'w'), indent=1)
print('forecast written for', iso(last + pd.Timedelta(days=1)), 'to', iso(last + pd.Timedelta(days=5)), '| backtest', json.dumps(bt), '| tracked forecasts resolved:', len(res))
