"""Daily job: fit models and write dashboard data.

v5  Overdue-alert model
----
The type-mix forecast (v4) always predicted "Other" and was not actionable.
This version replaces it with equipment-level overdue alerts:

1. Per equipment family per mill, compute the historical gap distribution
   (days between E/M breakdowns involving that family).
2. Compare the current gap (days since last breakdown) to the 75th-percentile
   gap. If the current gap exceeds p75, the family is flagged OVERDUE.
3. A risk score (current_gap / p75_gap) ranks families by urgency.

Stage 2 (equipment prediction) is kept: pooled CatBoost blended 50/50 with
recency-weighted frequency, scored on E/M days only.

Usage: python forecast.py breakdown.csv reason_master.csv ../data"""
import sys, os, json, warnings, datetime as dt, numpy as np, pandas as pd
warnings.filterwarnings('ignore'); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import features as FE
from catboost import CatBoostClassifier

SRC, DROP, OUT = sys.argv[1:4]
HL, BLEND = 120, 0.5
SEEDS = (0, 1, 2, 3, 4)          # the ranker is an average over these seeds, which is steadier than one fit
VERSION = 'v6 overdue alerts + per-family equipment ranker (top-3) + cause analysis'
TOP_K = 3
OTHER = {'VRM-1': 'VRM-2', 'VRM-2': 'VRM-1'}
META = ['target_date', 'state_t', 'y', 'known', 'valid', 'primary', 'all_reasons', 'origin']

raw_all = pd.read_csv(SRC)
raw = FE.load_breakdowns(SRC)
uni = FE.load_reason_universe(DROP)
daily = FE.build_daily(raw, uni)
TD = FE.type_daily(raw, daily)
TC = FE.TYPE_CLASSES
last = daily['VRM-1'].index.max()

ROWS = {(m, h): FE.make_rows(daily[m], h, False, 'equip', daily[OTHER[m]]).assign(mill_v2=int(m == 'VRM-2'), mill=m) for m in FE.MILLS for h in FE.HORIZONS}
ALL = [c for c in ROWS[('VRM-1', 1)].columns if c not in META + ['mill']]

def recency_freq(df, classes, now):
    ev = df[(df.y == 1) & df.primary.notna()]
    w = 0.5 ** ((now - ev.target_date).dt.days / HL)
    c = ev.assign(w=w).groupby('primary').w.sum().reindex(classes).fillna(0) + 0.5
    return (c / c.sum()).values

# ----------------------------------------------------------------
# EQUIPMENT RANKER (v6): one row per (day, equipment family) instead of one
# multiclass row per day. Each row carries that family's own history — days
# since it last failed, its recent counts, its mean gap and a smoothed hazard —
# so the model learns per-family timing rather than a single label per day.
# ----------------------------------------------------------------
FAM_FEATS = ['since', 'n7', 'n30', 'n90', 'n365', 'rate', 'hz', 'mean_gap', 'gap_ratio', 'nev']
_fam_cache = {}

def fam_history(mill, fam, t):
    """Features for one family at origin t, from events up to and including t."""
    key = (mill, fam, t)
    if key in _fam_cache: return _fam_cache[key]
    t = pd.Timestamp(t); d = EV_DATES[mill].get(fam)
    if d is None or not len(d) or d[0] > t:
        r = dict(since=365.0, n7=0, n30=0, n90=0, n365=0, rate=0.0, hz=0.0, mean_gap=365.0, gap_ratio=0.0, nev=0)
    else:
        d = d[d <= t]
        age = (t - pd.DatetimeIndex(d)).days.values; since = float(age.min())
        gaps = np.diff(d).astype('timedelta64[D]').astype(int) if len(d) > 1 else np.array([])
        mg = float(gaps.mean()) if len(gaps) else 365.0
        hz = 0.0
        if len(gaps) >= 3:          # conditional chance of failing tomorrow given the current gap
            surv = gaps[gaps > since]; prior = 1 - np.exp(-1 / max(mg, 1))
            hz = ((surv <= since + 1).sum() + 2 * prior) / (len(surv) + 2)
        span = max((t - pd.Timestamp(d.min())).days, 1)
        r = dict(since=since, n7=int((age <= 7).sum()), n30=int((age <= 30).sum()), n90=int((age <= 90).sum()),
                 n365=int((age <= 365).sum()), rate=len(d) / span, hz=float(hz), mean_gap=mg,
                 gap_ratio=since / mg if mg > 0 else 0.0, nev=len(d))
    _fam_cache[key] = r; return r

def pair_rows(origins, mill, classes):
    """One row per (origin day, family) for the given origins."""
    recs = [{**{'f_' + k: v for k, v in fam_history(mill, f, t).items()}, 'fam': f, 'mill': mill}
            for t in origins for f in classes]
    return pd.DataFrame(recs)

PAIR_COLS = ['f_' + k for k in FAM_FEATS] + ['fam', 'mill']

def fit_stage2(tr, now):
    """Fit the per-family ranker. Returns predict(rows, mill) -> probability per family."""
    lab = tr[(tr.y == 1) & tr.all_reasons.map(lambda a: isinstance(a, list) and len(a) > 0)]
    classes = sorted({f for a in lab.all_reasons for f in a})
    X, Y = [], []
    for mm in FE.MILLS:
        g = lab[lab.mill == mm]
        if not len(g): continue
        X.append(pair_rows(g.origin.tolist(), mm, classes))
        Y.append(np.array([int(f in a) for a in g.all_reasons for f in classes]))
    X = pd.concat(X, ignore_index=True); Y = np.concatenate(Y)
    models = [CatBoostClassifier(iterations=250, depth=5, learning_rate=0.06, loss_function='Logloss',
                                 verbose=0, thread_count=2, random_seed=s, cat_features=['fam', 'mill']).fit(X[PAIR_COLS], Y)
              for s in SEEDS]
    def predict(rows, mill):
        origins = pd.to_datetime(rows['origin']).tolist()
        T = pair_rows(origins, mill, classes)
        p = np.mean([m.predict_proba(T[PAIR_COLS])[:, 1] for m in models], axis=0).reshape(len(origins), len(classes))
        s = p.sum(axis=1, keepdims=True)
        return np.divide(p, s, out=np.full_like(p, 1 / len(classes)), where=s > 0)
    return predict, classes

iso = lambda d: str(pd.Timestamp(d).date())

# ----------------------------------------------------------------
# OVERDUE ALERTS: per equipment family, compute gap stats
# ----------------------------------------------------------------
em_raw = raw[raw['Breakdown Type'].isin(['Electrical Breakdown', 'Mechanical Breakdown'])].copy()
em_raw['family'] = em_raw['Breakdwon Name'].map(FE.map_equip)
em_raw['cause'] = em_raw['Reason'].map(FE.map_cause)
EV_DATES = {m: {f: np.sort(pd.to_datetime(g.Date.unique())) for f, g in em_raw[em_raw['Mill Name'] == m].groupby('family')} for m in FE.MILLS}
today = last  # use last data date as reference

MIN_EVENTS = 3  # need at least 3 events to compute meaningful gap stats

def compute_overdue_alerts(mill):
    """Compute overdue alerts for one mill."""
    mg = em_raw[em_raw['Mill Name'] == mill]
    families = mg['family'].value_counts()
    alerts = []
    for fam in families.index:
        fg = mg[mg['family'] == fam].sort_values('Date')
        dates = fg['Date'].drop_duplicates().sort_values()
        n_events = len(dates)
        last_date = dates.iloc[-1]
        days_since = (today - last_date).days
        first_date = dates.iloc[0]

        if n_events >= MIN_EVENTS:
            gaps = dates.diff().dt.days.dropna()
            median_gap = float(gaps.median())
            p75_gap = float(gaps.quantile(0.75))
            mean_gap = float(gaps.mean())
            max_gap = float(gaps.max())
            risk_score = round(days_since / p75_gap, 2) if p75_gap > 0 else 0
            overdue = days_since > p75_gap
        else:
            median_gap = p75_gap = mean_gap = max_gap = None
            risk_score = 0
            overdue = False

        # Breakdown type split for this family
        fg_types = mg[mg['family'] == fam]['Breakdown Type'].value_counts()
        e_count = int(fg_types.get('Electrical Breakdown', 0))
        m_count = int(fg_types.get('Mechanical Breakdown', 0))

        # Top causes for this family
        fg_causes = mg[mg['family'] == fam]['cause'].value_counts()
        top_causes = [dict(cause=c, count=int(n)) for c, n in fg_causes.head(3).items()]

        alerts.append(dict(
            family=fam,
            total_events=n_events,
            electrical_events=e_count,
            mechanical_events=m_count,
            last_date=iso(last_date),
            days_since=days_since,
            median_gap=round(median_gap, 1) if median_gap is not None else None,
            p75_gap=round(p75_gap, 1) if p75_gap is not None else None,
            mean_gap=round(mean_gap, 1) if mean_gap is not None else None,
            max_gap=round(max_gap, 1) if max_gap is not None else None,
            risk_score=risk_score,
            overdue=overdue,
            top_causes=top_causes
        ))

    # Sort: overdue first (by risk_score desc), then non-overdue by risk_score desc
    alerts.sort(key=lambda a: (-int(a['overdue']), -a['risk_score']))
    return alerts

def compute_type_alerts(mill):
    """Compute overdue alerts per breakdown type (E and M separately)."""
    mg = em_raw[em_raw['Mill Name'] == mill]
    type_alerts = []
    for bt, label in [('Electrical Breakdown', 'Electrical'), ('Mechanical Breakdown', 'Mechanical')]:
        bg = mg[mg['Breakdown Type'] == bt]
        dates = bg['Date'].drop_duplicates().sort_values()
        if len(dates) >= MIN_EVENTS:
            gaps = dates.diff().dt.days.dropna()
            last_date = dates.iloc[-1]
            days_since = (today - last_date).days
            median_gap = float(gaps.median())
            p75_gap = float(gaps.quantile(0.75))
            risk_score = round(days_since / p75_gap, 2) if p75_gap > 0 else 0
            overdue = days_since > p75_gap
            type_alerts.append(dict(
                type=label,
                total_events=len(dates),
                last_date=iso(last_date),
                days_since=days_since,
                median_gap=round(median_gap, 1),
                p75_gap=round(p75_gap, 1),
                risk_score=risk_score,
                overdue=overdue
            ))
    return type_alerts

def compute_cause_profile(mill):
    """For each equipment family in a mill, compute the cause distribution."""
    mg = em_raw[em_raw['Mill Name'] == mill]
    profiles = {}
    for fam in mg['family'].unique():
        fg = mg[mg['family'] == fam]
        cause_counts = fg['cause'].value_counts()
        total = cause_counts.sum()
        profiles[fam] = [dict(cause=c, count=int(n), pct=round(n/total, 2)) for c, n in cause_counts.items()]
    return profiles


# ----------------------------------------------------------------
# FORECAST D+1..D+5 (equipment families only, for E/M days)
# ----------------------------------------------------------------
allr = pd.concat([r[r.valid] for r in ROWS.values()])
p2f, classes = fit_stage2(allr, last + pd.Timedelta(days=1))
now = last + pd.Timedelta(days=1)
cause_profiles = {m: compute_cause_profile(m) for m in FE.MILLS}
fcs = []
for m in FE.MILLS:
    of = FE.origin_features(daily[m]).iloc[[-1]].copy()
    o = FE.origin_features(daily[OTHER[m]]).iloc[-1]
    for k_, v in dict(x_ev_days_7=o.ev_days_7, x_ev_days_30=o.ev_days_30, x_lag_ev_0=o.lag_ev_0, x_lag_ev_1=o.lag_ev_1, x_since_ev=o.since_ev, x_util_min_7=o.util_min_7).items():
        of[k_] = v
    of['mill_v2'] = int(m == 'VRM-2')
    for h in FE.HORIZONS:
        tgt = last + pd.Timedelta(days=h)
        x = of.copy(); x['h'] = h; x['tgt_dow'] = tgt.dayofweek; x['tgt_month'] = tgt.month; x['tgt_weekend'] = int(tgt.dayofweek >= 5)
        x['origin'] = last
        P = p2f(x, m)[0]; top = np.argsort(-P)[:TOP_K]
        fam_list = []
        for i in top:
            fam_name = classes[i]
            causes = cause_profiles.get(m, {}).get(fam_name, [])[:2]
            fam_list.append(dict(family=fam_name, conditional=round(float(P[i]), 3),
                                 likely_causes=[c['cause'] for c in causes]))
        fcs.append(dict(mill=m, horizon=h, forecast_date=iso(tgt), weekday=tgt.strftime('%a'),
                        families=fam_list))

# ----------------------------------------------------------------
# OVERDUE ALERTS per mill
# ----------------------------------------------------------------
overdue_data = {}
for m in FE.MILLS:
    overdue_data[m] = dict(
        equipment=compute_overdue_alerts(m),
        types=compute_type_alerts(m),
        cause_profiles=cause_profiles[m]
    )

# ----------------------------------------------------------------
# CONTEXT for the dashboard (daily history, last 60 days)
# ----------------------------------------------------------------
ctx = {}
for m in FE.MILLS:
    d = daily[m]; t = TD[m]; r60 = d.tail(60); r90 = t.tail(90); r90d = d.tail(90)
    fam = r90d[[c for c in r90d.columns if c.startswith('grp_')]].sum().rename(lambda c: c[4:]).sort_values(ascending=False)
    fam = fam[fam > 0]
    ctx[m] = dict(
        daily=[dict(date=iso(i), electrical=int(t.loc[i, 'n_Electrical']), mechanical=int(t.loc[i, 'n_Mechanical']),
                    other=int(t.loc[i, 'n_Other']), state=rw.state) for i, rw in r60.iterrows()],
        families_90d=[dict(family=k, events=int(v)) for k, v in fam.items()],
        causes_90d=[dict(cause=c, events=int(n)) for c, n in em_raw[(em_raw['Mill Name']==m) & (em_raw['Date']>=d.index[-90])]['cause'].value_counts().items()],
        mix_90d={k: int((r90['n_' + k] > 0).sum()) for k in TC},
        event_days_90d=int(r90.evt.sum()),
        breakdowns_90d=int(r90d.n_ev.sum()),
        shutdown_days_90d=int((r90d.state == 'SHUTDOWN').sum()),
        gap_days_30d=int((d.tail(30).state == 'DATA_GAP').sum()))

# ----------------------------------------------------------------
# BACKTEST: last 90 days, 1-day-ahead equipment prediction (E/M days only)
# ----------------------------------------------------------------
start = last - pd.Timedelta(days=89); bt_rows = []; bt = {}
tr_all = pd.concat([x[x.valid & (x.target_date < start)] for x in ROWS.values()])
p2b, cb = fit_stage2(tr_all, start)
base = tr_all[(tr_all.y == 1) & tr_all.primary.notna()].primary.value_counts(normalize=True).reindex(cb).fillna(0).values
for m in FE.MILLS:
    t = TD[m]; win = t[(t.index >= start) & (t.index <= last) & (t.evt > 0)]
    r = ROWS[(m, 1)]; te = r[r.valid & (r.target_date >= start)].set_index('target_date')
    P = p2b(te, m); pos = {dt_: i for i, dt_ in enumerate(te.index)}
    hits = bhits = n_em = 0
    for dte, rw in win.iterrows():
        acts = [k for k in TC if rw['n_' + k] > 0]
        pred, act_f, hit = None, [], None
        if dte in pos and any(k != 'Other' for k in acts):
            top = np.argsort(-P[pos[dte]])[:TOP_K]; pred = [cb[j] for j in top]
            act_f = te.loc[dte, 'all_reasons'] if isinstance(te.loc[dte, 'all_reasons'], list) else []
            if act_f:
                n_em += 1; hit = bool(any(a in pred for a in act_f)); hits += hit
                bhits += bool(any(a in [cb[j] for j in np.argsort(-base)[:TOP_K]] for a in act_f))
        bt_rows.append(dict(date=iso(dte), mill=m, actual_types=acts, predicted_families=pred, actual_families=act_f, top2_hit=hit))
    n = len(win)
    bt[m] = dict(breakdown_days=int(n), em_days_scored=int(n_em),
                 top2_hit_rate=round(hits / n_em, 3) if n_em else None,
                 baseline_top2_hit_rate=round(bhits / n_em, 3) if n_em else None)
bt_rows.sort(key=lambda x: (x['date'], x['mill']), reverse=True)

# ----------------------------------------------------------------
# PREDICTION HISTORY
# ----------------------------------------------------------------
hp = os.path.join(OUT, 'prediction_history.json')
hist = json.load(open(hp)) if os.path.exists(hp) else []
# Keep only v5-format entries (have 'families' key at top level or are v5)
hist = [e for e in hist if 'predicted' in e or 'families' in e]
run = dt.datetime.now(dt.timezone.utc).date().isoformat()
key = lambda e: (e.get('run_date', ''), e.get('mill', ''), e.get('horizon', 0))
new = {key(e): e for e in hist}
for f in fcs:
    new[(run, f['mill'], f['horizon'])] = dict(
        run_date=run, data_through=iso(last), mill=f['mill'], horizon=f['horizon'],
        forecast_date=f['forecast_date'],
        predicted=[x['family'] for x in f['families']],
        actual_status='pending', actual_families=[], equip_hit=None)
for e in new.values():
    d = daily[e['mill']]; fd = pd.Timestamp(e.get('forecast_date', '2000-01-01'))
    if fd <= last and fd in d.index:
        st = d.loc[fd, 'state']
        act_f = d.loc[fd, 'all_equip'] if isinstance(d.loc[fd, 'all_equip'], list) else []
        e['actual_families'] = act_f
        if st in ('SHUTDOWN', 'DATA_GAP'):
            e['actual_status'] = 'excluded'
        elif act_f:
            e['actual_status'] = 'breakdown'
            if 'predicted' in e and e['predicted']:
                e['equip_hit'] = bool(any(a in e['predicted'] for a in act_f))
        else:
            e['actual_status'] = 'no_em'
hist = sorted(new.values(), key=lambda e: (e.get('run_date', ''), e.get('mill', ''), e.get('horizon', 0)))[-1200:]
resolved = [e for e in hist if e.get('equip_hit') is not None]

# ----------------------------------------------------------------
# WRITE OUTPUT
# ----------------------------------------------------------------
meta = dict(
    generated_at=dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
    data_through=iso(last), model_version=VERSION,
    rows_in_sheet=int(len(raw_all.dropna(how='all'))),
    duplicate_rows_removed=int(len(raw_all.dropna(how='all')) - len(raw) - int((~raw_all.dropna(how='all')['Mill Name'].isin(FE.MILLS)).sum())))

json.dump(dict(meta=meta, forecasts=fcs, overdue=overdue_data, context=ctx),
          open(os.path.join(OUT, 'predictions.json'), 'w'), indent=1)
json.dump(dict(meta=dict(window=f'{iso(start)} to {iso(last)}', horizon_days=1,
               note='Out-of-sample: fitted only on data before the window; only E/M breakdown days scored for equipment.'),
               summary=bt, rows=bt_rows),
          open(os.path.join(OUT, 'backtest.json'), 'w'), indent=1)
json.dump(hist, open(hp, 'w'), indent=1)

n_overdue = sum(1 for m in FE.MILLS for a in overdue_data[m]['equipment'] if a['overdue'])
print(f'v5 forecast written | data through {iso(last)} | {n_overdue} overdue equipment alerts')
print(f'backtest: {json.dumps(bt)}')
print(f'tracked forecasts resolved: {len(resolved)}')
