"""Phase 2-3: prepare daily calendar, label NO_E_M / E_M / SHUTDOWN / DATA_GAP, build lag + rolling + recurrence features.
All features for origin T use data up to and including T only; targets are about T+h (h = 1..5)."""
import re, difflib, numpy as np, pandas as pd

HORIZONS = [1, 2, 3, 4, 5]
MILLS = ['VRM-1', 'VRM-2']
cl = lambda s: re.sub(r'\s+', ' ', str(s)).strip().lower()
TYPE_RULES = [('Mill', r'^mill'), ('Fan', r'fan|fn'), ('Bucket Elevator', r'bucket|\bbe\b|481be'), ('Belt Conveyor', r'belt'),
              ('Weigh Feeder', r'weigh|511wf'), ('Main Motor', r'main motor'), ('Bag House/Filter', r'bag'), ('Compressor', r'compress'),
              ('Gearbox/Lube', r'gear|^lq|lube'), ('Hydraulics/Rollers', r'hs[a-z]{2}|hy|roller|rocker'),
              ('Feeders/Gates/Slides', r'feeder|gate|air slide|rf|rv|separator|silo'), ('HAG', r'hag')]
EQUIP_RULES = [('Bag House / Bag Filter', r'bag ?house|bag ?filter|bagfilter|502 b f|main bag'), ('Fan', r'fan|\bfn\b|fn ?-?\d'), ('HAG', r'\bhag\b'),
    ('Classifier / Separator', r'classifier|\bsr\b|sr ?-?\d|separator'), ('Main Motor', r'md ?140|main motor'),
    ('Hydraulics / Rollers', r'master roller|rocker|hsms|hslm|hssw|hy ?-? ?110|thrustpad|roller|hydr'), ('Gearbox / Lube', r'gear ?box|\blq ?\d|lubrication|gear lube'),
    ('Compressor', r'compress|sullair|copco'), ('Bucket Elevator', r'bucket|\bbe\b|\dbe\b|be ?\d{1,3}\b'), ('Belt Conveyor / Weigh Feeder', r'belt|\bbc ?-?\d|bc\d|weigh|\bwf\b|wf ?-?\d|techo'),
    ('Electrical Panel / Power', r'plc|vcb|capacitor|transformer|switchgear|\bacb\b|power plant|panel|pannel|dcs|vfd'), ('Mill (process / monitoring)', r'\bmill\b|512 ?rm|\brm ?-?\d|sprinkler|grinding aid|skip chamber|scrip'),
    ('Feeders / Gates / Dampers', r'rotary|\brf ?\d|rf\d|\brv\b|rv ?\d|\bdg ?\d|dg\d|\bld ?-?\d|damper|air slide|\bas ?\d|\bms ?-?\d|\bra ?\d|ra\d|mw-|gate|silo|hopper|chute|\bli ?-?\d')]
TYPES = [t for t, _ in EQUIP_RULES] + ['Other equipment']
def map_equip(name):
    n = re.sub(r'^[a-z]\d\d-', '', str(name).lower().strip())
    for t, p in EQUIP_RULES:
        if re.search(p, n): return t
    return 'Other equipment'
CAUSE_RULES = [('Safety sensor alarm (ZSS/BSS/motion/limit)', r'zss|bss|motion|limit|belt side|sensor|techo'), ('Vibration', r'vibrat'),
    ('High temperature', r'temp|rtd|heat'), ('Pressure / lube / oil / hydraulic', r'pressure|oil|lube|lubricat|hydra|\blq|counter|pump'),
    ('Jam / blockage', r'jam|choke|block|flash|flush|stuck'), ('Bag house DP / cycle off', r'bag ?house|bag ?filter|bagfilter|\bdp\b|cycle off'),
    ('Compressor / air', r'compress|air '), ('Control / PLC / communication', r'plc|automation|program|hmi|server|communication|remote|healthy|not ready|not ok|vfd|lrs|panel|software|time out|timeout'),
    ('Mechanical wear / damage', r'bush|broken|damage|joint|crack|coupling|bearing|displac|cylinder|torn|tron|leak|tube|nozz|canvas|gear ?box|pin|rubber|ruber|belt|bucket|change|fitting|align|shaft|seal|maintenance|calibration|check'),
    ('Power / electrical fault', r'power|vcb|acb|transformer|capacitor|earth|short|cable|switchgear|mcc|voltage|fuse|breaker|burn|carbon|motor'),
    ('Trip / overload / stop', r'trip|overload|over load|stop|emergency|not run|not start|fault|alarm|error')]
def map_cause(s):
    s = re.sub(r'^[a-z]\d\d-', '', str(s).lower().strip())
    for t, p in CAUSE_RULES:
        if re.search(p, s): return t
    return 'Other cause'

def map_type(s):
    s = str(s).lower().strip()
    for t, p in TYPE_RULES:
        if re.search(p, s): return t
    return 'Other'

def load_reason_universe(dropdown_csv):
    dd = pd.read_csv(dropdown_csv, header=None, skiprows=3, dtype=str)
    r = dd[[5, 6, 8]].dropna(subset=[8]).copy(); r.columns = ['code', 'type', 'reason']
    r = r[r.code.isin(['E01', 'M01'])].copy(); r['reason'] = r.reason.map(lambda s: re.sub(r'\s+', ' ', s).strip())
    r['key'] = r.reason.map(cl); r['group'] = r.type.map(map_type); r['cause'] = r.reason.map(map_cause)
    return r.drop_duplicates('key').reset_index(drop=True)

def load_breakdowns(csv):
    raw = pd.read_csv(csv).dropna(how='all')
    raw['Date'] = pd.to_datetime(raw['Date'], format='%m/%d/%Y', errors='coerce'); raw = raw[raw.Date.notna()].copy()
    raw = raw[~raw.duplicated(subset=[c for c in raw.columns if c != 'Remarks'])]        # remove double-entry copies
    for c in ['Mill Name', 'Breakdown Type']: raw[c] = raw[c].astype(str).str.strip()
    return raw[raw['Mill Name'].isin(MILLS)].copy()

def map_reasons(df, universe):
    """Closed-set rule: reasons must come from the dropdown. Fuzzy-map near matches, leave the rest unmapped (None)."""
    keys = list(universe.key); lookup = dict(zip(universe.key, universe.reason)); cache = {}
    def f(x):
        k = cl(x)
        if k in cache: return cache[k]
        if k in lookup: out = lookup[k]
        else:
            m = difflib.get_close_matches(k, keys, n=1, cutoff=0.88); out = lookup[m[0]] if m else None
        cache[k] = out; return out
    return df['Reason'].map(f)

def build_daily(raw, universe, shutdown_min=720):
    """One frame per mill on the full calendar with state labels and event indicators."""
    cal = pd.date_range(raw.Date.min(), raw.Date.max())
    em = raw[raw['Breakdown Type'].isin(['Electrical Breakdown', 'Mechanical Breakdown'])].copy()
    em['reason_m'] = map_reasons(em, universe); em['is_e'] = em['Breakdown Type'].str.startswith('E'); em['equip'] = em['Breakdwon Name'].map(map_equip)
    em['Downtime'] = em.Downtime.fillna(0)
    out = {}
    for m in MILLS:
        g = raw[raw['Mill Name'] == m]; e = em[em['Mill Name'] == m]
        d = pd.DataFrame(index=cal)
        d['any_record'] = g.groupby('Date').size().reindex(cal).fillna(0)
        d['n_ev'] = e.groupby('Date').size().reindex(cal).fillna(0)
        d['n_e'] = e[e.is_e].groupby('Date').size().reindex(cal).fillna(0); d['n_m'] = d.n_ev - d.n_e
        d['ev'] = (d.n_ev > 0).astype(int); d['e_day'] = (d.n_e > 0).astype(int); d['m_day'] = (d.n_m > 0).astype(int)
        d['planned_min'] = g[g['Breakdown Type'] == 'Planned Down Time'].groupby('Date').Downtime.sum().reindex(cal).fillna(0)
        d['util_min'] = g[g['Breakdown Type'] == 'Utility (Electricity)'].groupby('Date').Downtime.sum().reindex(cal).fillna(0)
        d['em_min'] = e.groupby('Date').Downtime.sum().reindex(cal).fillna(0)
        d['opc'] = (g[g['Product Criteria'].astype(str).str.strip() == 'OPC'].groupby('Date').size().reindex(cal).fillna(0) > 0).astype(int)
        d['other_ev'] = g[~g['Breakdown Type'].isin(['Electrical Breakdown', 'Mechanical Breakdown', 'Planned Down Time'])].groupby('Date').size().reindex(cal).fillna(0)
        # shutdown: >= 5 consecutive days with >= shutdown_min planned stop minutes
        f = (d.planned_min >= shutdown_min).astype(int); grp = (f != f.shift()).cumsum(); d['shutdown'] = False
        for _, x in f.groupby(grp):
            if x.iloc[0] == 1 and len(x) >= 5: d.loc[x.index, 'shutdown'] = True
        d['state'] = np.where(d.shutdown, 'SHUTDOWN', np.where(d.ev > 0, 'E_M_BREAKDOWN', np.where(d.any_record > 0, 'NO_E_M_BREAKDOWN', 'DATA_GAP')))
        # reasons per day for stage 2
        mm = e.dropna(subset=['reason_m'])
        d['primary_reason'] = mm.sort_values('Downtime', ascending=False).groupby('Date').reason_m.first().reindex(cal)
        d['all_reasons'] = mm.groupby('Date').reason_m.apply(lambda s: sorted(set(s))).reindex(cal)
        rc = dict(zip(universe.reason, universe.cause)); rg = dict(zip(universe.reason, universe.group))
        d['primary_cause'] = d.primary_reason.map(rc)
        d['primary_equip'] = e.sort_values('Downtime', ascending=False).groupby('Date').equip.first().reindex(cal)
        d['all_cause'] = mm.groupby('Date').reason_m.apply(lambda s: sorted({rc[x] for x in s})).reindex(cal)
        d['all_equip'] = e.groupby('Date').equip.apply(lambda s: sorted(set(s))).reindex(cal)
        for t in TYPES: d['grp_' + t] = e[e.equip == t].groupby('Date').size().reindex(cal).fillna(0)
        out[m] = d
    return out

def _since_last(x):
    last = pd.Series(np.where(x > 0, np.arange(len(x)), np.nan), index=x.index).ffill()
    return (pd.Series(np.arange(len(x)), index=x.index) - last).fillna(90).clip(upper=90)

def origin_features(d):
    """Features at origin T (inclusive of T). Calendar features of the target day are added per horizon in make_rows."""
    f = pd.DataFrame(index=d.index)
    for k in (0, 1, 2, 3, 6, 13, 27): f[f'lag_ev_{k}'] = d.ev.shift(k)
    for w in (3, 7, 14, 30, 60):
        f[f'ev_days_{w}'] = d.ev.rolling(w, min_periods=1).sum()
    for w in (7, 30):
        f[f'n_ev_{w}'] = d.n_ev.rolling(w, min_periods=1).sum(); f[f'e_days_{w}'] = d.e_day.rolling(w, min_periods=1).sum(); f[f'm_days_{w}'] = d.m_day.rolling(w, min_periods=1).sum()
        f[f'other_ev_{w}'] = d.other_ev.rolling(w, min_periods=1).sum()
    f['since_ev'] = _since_last(d.ev); f['since_e'] = _since_last(d.e_day); f['since_m'] = _since_last(d.m_day)
    f['planned_7'] = d.planned_min.rolling(7, min_periods=1).sum(); f['shutdown_14'] = d.shutdown.astype(int).rolling(14, min_periods=1).sum()
    f['since_shutdown'] = _since_last(d.shutdown.astype(int)); f['covered_30'] = (d.any_record > 0).astype(int).rolling(30, min_periods=1).mean()
    f['ev_rate_90'] = d.ev.rolling(90, min_periods=1).mean()
    for t in TYPES:
        c = d['grp_' + t]; f[f'g7_{t}'] = c.rolling(7, min_periods=1).sum(); f[f'g30_{t}'] = c.rolling(30, min_periods=1).sum(); f[f'gsince_{t}'] = _since_last(c)
    f['util_min_7'] = d.util_min.rolling(7, min_periods=1).sum(); f['util_min_30'] = d.util_min.rolling(30, min_periods=1).sum(); f['util_min_1'] = d.util_min
    f['em_min_7'] = d.em_min.rolling(7, min_periods=1).sum(); f['em_min_1'] = d.em_min; f['opc_14'] = d.opc.rolling(14, min_periods=1).sum()
    f['origin_dow'] = d.index.dayofweek
    f.columns = [re.sub(r'[^A-Za-z0-9_]+', '_', c) for c in f.columns]
    return f.fillna(0)

def make_rows(d, h, gap_as_zero=False, level='reason', other=None):
    """Rows for horizon h: one per origin T. Label uses day T+h. valid = target day is an operating, covered day."""
    F = origin_features(d).copy(); tgt = d.index + pd.Timedelta(days=h)
    if other is not None:   # cross-mill context: both mills share power and the cement-silo/warehouse chain
        o = origin_features(other); F['x_ev_days_7'] = o.ev_days_7; F['x_ev_days_30'] = o.ev_days_30; F['x_lag_ev_0'] = o.lag_ev_0; F['x_lag_ev_1'] = o.lag_ev_1; F['x_since_ev'] = o.since_ev; F['x_util_min_7'] = o.util_min_7
    F['h'] = h; F['tgt_dow'] = tgt.dayofweek; F['tgt_month'] = tgt.month; F['tgt_weekend'] = (tgt.dayofweek >= 5).astype(int)
    st = d.state.reindex(tgt).values
    F['target_date'] = tgt; F['state_t'] = st; F['y'] = (pd.Series(st) == 'E_M_BREAKDOWN').astype(int).values
    ok = ~pd.isna(st)
    F['known'] = ok
    bad = {'SHUTDOWN'} if gap_as_zero else {'SHUTDOWN', 'DATA_GAP'}
    F['valid'] = ok & ~pd.Series(st).isin(bad).values
    pc, ac = {'reason': ('primary_reason', 'all_reasons'), 'cause': ('primary_cause', 'all_cause'), 'equip': ('primary_equip', 'all_equip')}[level]
    F['primary'] = d[pc].reindex(tgt).values; F['all_reasons'] = d[ac].reindex(tgt).values
    F['origin'] = d.index
    return F[F.known].copy()

def make_window_rows(d, W=5):
    """Stage-1 alternative target: any E/M breakdown during D+1..D+W (valid if no shutdown in window and, unless an event occurred, no data gap)."""
    F = origin_features(d).copy(); idx = d.index
    ev = d.ev.values; st = d.state.values; n = len(d); y = np.full(n, np.nan); valid = np.zeros(n, bool)
    for i in range(n - W):
        w = slice(i + 1, i + W + 1); y[i] = ev[w].max(); stw = st[w]
        valid[i] = ('SHUTDOWN' not in stw) and (y[i] == 1 or 'DATA_GAP' not in stw)
    tgt = idx + pd.Timedelta(days=W)
    F['h'] = W; F['tgt_dow'] = (idx + pd.Timedelta(days=1)).dayofweek; F['tgt_month'] = tgt.month; F['tgt_weekend'] = 0
    F['target_date'] = tgt; F['state_t'] = ''; F['y'] = np.nan_to_num(y).astype(int); F['known'] = ~np.isnan(y); F['valid'] = valid & ~np.isnan(y)
    F['primary'] = None; F['all_reasons'] = None; F['origin'] = idx
    return F[F.known].copy()

# ---------------- breakdown TYPE view: Electrical / Mechanical / Other ----------------
TYPE_CLASSES = ['Electrical', 'Mechanical', 'Other']
def _tmap(t):
    t = str(t).strip()
    return 'Electrical' if t == 'Electrical Breakdown' else 'Mechanical' if t == 'Mechanical Breakdown' else None if t == 'Planned Down Time' else 'Other'
def type_daily(raw, daily):
    """Per mill and day: which breakdown types occurred. 'Other' = every unplanned stop that is not electrical/mechanical
    (others down time, utility, warehouse block, process setup, raw material). Planned down time and shutdown days are not breakdowns."""
    out = {}
    for m in MILLS:
        g = raw[raw['Mill Name'] == m].copy(); g['T'] = g['Breakdown Type'].map(_tmap); g = g[g['T'].notna()]
        cal = daily[m].index
        cnt = g.pivot_table(index='Date', columns='T', values='Breakdown Type', aggfunc='size').reindex(cal).reindex(columns=TYPE_CLASSES).fillna(0)
        t = pd.DataFrame(index=cal)
        for c in TYPE_CLASSES: t['n_' + c] = cnt[c].where(~daily[m].shutdown, 0)
        t['evt'] = (t[['n_' + c for c in TYPE_CLASSES]].sum(axis=1) > 0).astype(int)
        out[m] = t
    return out
