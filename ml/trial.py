"""Trial bench: change the equipment groups, re-run the model, compare accuracy.

Usage: python trial.py breakdown.csv reason_master.csv [trials/*.json ...]

A trial file is JSON: {"name": "...", "ops": [...]} applied to the current EQUIP_RULES:
  {"split": "Fan", "into": [["Cooling Fan", "cooling"], ["ID Fan", "\\bid fan"]]}   (leftovers stay in "Fan")
  {"merge": ["HAG", "Classifier / Separator"], "as": "Mill auxiliaries"}
  {"drop": "Compressor"}                                                           (names fall to "Other equipment")
  {"rules": [["Name", "regex"], ...]}                                              (replace all rules)
Regexes match the lower-cased equipment name without its leading code (e.g. "o01-").

Every trial uses the same rolling-origin backtest: FOLDS windows of FOLD_DAYS days ending at the
last data date; each window is predicted by a model trained only on earlier data (1-day ahead,
E/M breakdown days only, same model as forecast.py). Results: results.json and printed table."""
import sys, os, re, json, glob, math, warnings, numpy as np, pandas as pd
warnings.filterwarnings('ignore'); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import features as FE
from catboost import CatBoostClassifier

SRC, DROP = sys.argv[1:3]; TRIALS = sys.argv[3:] or sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'trials', '*.json')))
HL, BLEND, TOP_K, FOLDS, FOLD_DAYS = 120, 0.5, 3, 4, 45
OTHER = {'VRM-1': 'VRM-2', 'VRM-2': 'VRM-1'}
META = ['target_date', 'state_t', 'y', 'known', 'valid', 'primary', 'all_reasons', 'origin']
BASE_RULES = list(FE.EQUIP_RULES)

def apply_ops(ops):
    rules = list(BASE_RULES)
    for o in ops:
        if 'rules' in o: rules = [tuple(x) for x in o['rules']]
        elif 'split' in o:
            i = [n for n, _ in rules].index(o['split']); rules[i:i] = [tuple(x) for x in o['into']]
        elif 'merge' in o:
            idx = [i for i, (n, _) in enumerate(rules) if n in o['merge']]
            pat = '|'.join(p for n, p in rules if n in o['merge'])
            rules = [r for i, r in enumerate(rules) if i not in idx[1:]]; rules[idx[0]] = (o['as'], pat)
        elif 'drop' in o: rules = [r for r in rules if r[0] != o['drop']]
    return rules

def cat_model():
    return CatBoostClassifier(iterations=80, depth=4, learning_rate=0.1, loss_function='MultiClass', verbose=0, thread_count=2, random_seed=0)

def wilson(k, n, z=1.96):
    if not n: return (None, None)
    p = k / n; d = 1 + z * z / n; c = p + z * z / (2 * n); w = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - w) / d, (c + w) / d)

raw = FE.load_breakdowns(SRC); uni = FE.load_reason_universe(DROP)

def run(rules):
    FE.EQUIP_RULES = rules; FE.TYPES = [t for t, _ in rules] + ['Other equipment']
    daily = FE.build_daily(raw, uni); last = daily['VRM-1'].index.max()
    ROWS = {(m, h): FE.make_rows(daily[m], h, False, 'equip', daily[OTHER[m]]).assign(mill_v2=int(m == 'VRM-2'), mill=m) for m in FE.MILLS for h in FE.HORIZONS}
    ALL = [c for c in ROWS[('VRM-1', 1)].columns if c not in META + ['mill']]
    h1 = {m: 0 for m in FE.MILLS}; res = dict(n=0, h1=0, h3=0, b3=0, folds=[], by_mill={m: dict(n=0, h3=0, b3=0) for m in FE.MILLS})
    for f in range(FOLDS):
        end = last - pd.Timedelta(days=f * FOLD_DAYS); start = end - pd.Timedelta(days=FOLD_DAYS - 1)
        tr = pd.concat([x[x.valid & (x.target_date < start)] for x in ROWS.values()])
        t = tr[(tr.y == 1) & tr.primary.notna()]; classes = sorted(t.primary.unique()); code = {c: i for i, c in enumerate(classes)}
        mdl = cat_model().fit(t[ALL], t.primary.map(code).values); cls = np.asarray(mdl.classes_).astype(int).ravel()
        base = t.primary.value_counts(normalize=True).reindex(classes).fillna(0).values
        fn = fh1 = fh3 = fb3 = 0
        for m in FE.MILLS:
            ev = tr[tr.mill == m]; ev = ev[(ev.y == 1) & ev.primary.notna()]
            w = 0.5 ** ((start - ev.target_date).dt.days / HL); c = ev.assign(w=w).groupby('primary').w.sum().reindex(classes).fillna(0) + 0.5; rec = (c / c.sum()).values
            r = ROWS[(m, 1)]; te = r[r.valid & (r.target_date >= start) & (r.target_date <= end) & (r.y == 1)]
            te = te[te.all_reasons.map(lambda a: isinstance(a, list) and len(a) > 0)]
            if not len(te): continue
            P = np.zeros((len(te), len(classes))); P[:, cls] = mdl.predict_proba(te[ALL]); P = BLEND * P + (1 - BLEND) * rec
            b3 = [classes[j] for j in np.argsort(-base)[:TOP_K]]
            for i, (_, row) in enumerate(te.iterrows()):
                order = np.argsort(-P[i]); acts = row.all_reasons
                h3 = any(a in [classes[j] for j in order[:TOP_K]] for a in acts); h1_ = classes[order[0]] in acts; hb = any(a in b3 for a in acts)
                fn += 1; fh1 += h1_; fh3 += h3; fb3 += hb
                bm = res['by_mill'][m]; bm['n'] += 1; bm['h3'] += h3; bm['b3'] += hb
        res['n'] += fn; res['h1'] += fh1; res['h3'] += fh3; res['b3'] += fb3
        res['folds'].append(dict(window=f'{start.date()}..{end.date()}', n=fn, hit3=round(fh3 / fn, 3) if fn else None, base3=round(fb3 / fn, 3) if fn else None))
    res['n_groups'] = len(FE.TYPES)
    return res

out = []
for path in TRIALS:
    spec = json.load(open(path)); rules = apply_ops(spec.get('ops', []))
    print(f"running {spec['name']} ({len(rules) + 1} groups) ...", flush=True)
    r = run(rules); lo, hi = wilson(r['h3'], r['n'])
    out.append(dict(name=spec['name'], groups=r['n_groups'], events=r['n'], hit1=round(r['h1'] / r['n'], 3), hit3=round(r['h3'] / r['n'], 3), hit3_ci=[round(lo, 3), round(hi, 3)],
                    baseline3=round(r['b3'] / r['n'], 3), lift3=round((r['h3'] - r['b3']) / r['n'], 3),
                    vrm1_hit3=round(r['by_mill']['VRM-1']['h3'] / max(1, r['by_mill']['VRM-1']['n']), 3), vrm2_hit3=round(r['by_mill']['VRM-2']['h3'] / max(1, r['by_mill']['VRM-2']['n']), 3), folds=r['folds'],
                    description=spec.get('description', '')))
cur = next((o for o in out if o['name'] == 'current'), out[0])
print(f"\n{'trial':<22}{'groups':>7}{'events':>7}{'top-1':>8}{'top-3':>8}{'  95% CI':<14}{'baseline3':>10}{'lift':>7}{'vs current':>11}")
for o in sorted(out, key=lambda o: -o['lift3']):
    print(f"{o['name']:<22}{o['groups']:>7}{o['events']:>7}{o['hit1']:>8.1%}{o['hit3']:>8.1%}  {o['hit3_ci'][0]:.0%}-{o['hit3_ci'][1]:.0%}".ljust(66) + f"{o['baseline3']:>10.1%}{o['lift3']:>+7.1%}{o['hit3'] - cur['hit3']:>+11.1%}")
print("\nlift = top-3 hit rate minus the 'always pick the 3 most common groups' baseline. More groups = a harder task, so judge by lift and by whether the CI overlaps 'current'.")
json.dump(dict(folds=FOLDS, fold_days=FOLD_DAYS, results=out), open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'trials', 'results.json'), 'w'), indent=1)
