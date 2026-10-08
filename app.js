(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var tip = $('tip');
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function pct(x, d) { return (x * 100).toFixed(d || 0) + '%'; }
  function fmtDate(s) { var d = new Date(s + 'T00:00:00'); return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' }); }
  function showTip(ev, lines) {
    tip.replaceChildren.apply(tip, lines.map(function (l, i) { var d = el('div', null, l); if (i === 0) d.style.fontWeight = '600'; return d; }));
    tip.hidden = false; var x = Math.min(ev.clientX + 12, window.innerWidth - 250); tip.style.left = x + 'px'; tip.style.top = (ev.clientY + 14) + 'px';
  }
  function hideTip() { tip.hidden = true; }
  function load(name) { return fetch('data/' + name + '.json?_=' + Date.now()).then(function (r) { if (!r.ok) throw new Error(name); return r.json(); }); }

  // ---- Overdue Alerts ----
  function renderOverdue(p) {
    var root = $('overdue'); root.replaceChildren();
    ['VRM-1', 'VRM-2'].forEach(function (mill) {
      var od = p.overdue[mill];
      var panel = el('div', 'panel');
      var h = el('h2'); h.textContent = mill;
      var nOverdue = od.equipment.filter(function (a) { return a.overdue; }).length;
      var badge = el('span', 'rate');
      badge.textContent = nOverdue > 0 ? nOverdue + ' overdue' : 'All on schedule';
      badge.style.color = nOverdue > 0 ? 'var(--serious)' : 'var(--good)';
      badge.style.fontWeight = '600';
      h.appendChild(badge);
      panel.appendChild(h);

      // Type-level alerts (E/M overall)
      if (od.types && od.types.length) {
        var typeRow = el('div', 'type-alert');
        od.types.forEach(function (t) {
          var chip = el('div', 'type-chip' + (t.overdue ? ' overdue' : ''));
          chip.appendChild(el('span', 'label', t.type));
          chip.appendChild(el('span', 'val', t.days_since + 'd ago'));
          chip.appendChild(el('span', 'val', '(typical ≤' + t.p75_gap + 'd)'));
          if (t.overdue) chip.appendChild(el('span', 'od', '⚠ OVERDUE'));
          typeRow.appendChild(chip);
        });
        panel.appendChild(typeRow);
      }

      // Equipment alerts
      od.equipment.forEach(function (a) {
        var row = el('div', 'alert-row ' + (a.overdue ? 'overdue' : 'ok'));
        var left = el('div');
        left.appendChild(el('div', 'alert-fam', a.family));
        var detail = a.days_since + ' days since last breakdown';
        if (a.p75_gap != null) detail += ' · typical interval ≤' + a.p75_gap + 'd (median ' + a.median_gap + 'd)';
        detail += ' · ' + a.total_events + ' historical events';
        if (a.electrical_events > 0 || a.mechanical_events > 0) detail += ' (E:' + a.electrical_events + ' M:' + a.mechanical_events + ')';
        left.appendChild(el('div', 'alert-detail', detail));
        if (a.top_causes && a.top_causes.length) {
          left.appendChild(el('div', 'alert-detail cause-hint', 'Common causes: ' + a.top_causes.map(function(c) { return c.cause + ' (' + c.count + ')'; }).join(', ')));
        }

        // Risk meter
        if (a.p75_gap != null) {
          var meter = el('div', 'risk-meter');
          var fill = el('div', 'risk-fill ' + (a.risk_score >= 2 ? 'high' : a.risk_score >= 1 ? 'med' : 'low'));
          fill.style.width = Math.min(a.risk_score / 3 * 100, 100) + '%';
          meter.appendChild(fill);
          left.appendChild(meter);
        }
        row.appendChild(left);

        var right = el('div');
        if (a.p75_gap != null) {
          right.appendChild(el('div', 'alert-risk ' + (a.overdue ? 'overdue' : 'ok'), a.risk_score + '×'));
          right.appendChild(el('div', 'alert-label', a.overdue ? 'OVERDUE' : 'OK'));
        } else {
          right.appendChild(el('div', 'alert-risk ok', '–'));
          right.appendChild(el('div', 'alert-label', 'Too few events'));
        }
        row.appendChild(right);
        panel.appendChild(row);
      });

      root.appendChild(panel);
    });
  }

  // ---- Equipment Forecast D+1..D+5 ----
  function renderForecast(p) {
    var root = $('forecast'); root.replaceChildren();
    ['VRM-1', 'VRM-2'].forEach(function (mill) {
      var panel = el('div', 'panel'), h = el('h2', null, mill + ' — equipment forecast');
      h.appendChild(el('span', 'rate', 'If E/M breakdown occurs, top-3 most likely equipment'));
      panel.appendChild(h);
      p.forecasts.filter(function (f) { return f.mill === mill; }).forEach(function (f) {
        var d = el('div', 'day'), lab = el('div', 'dlabel');
        lab.appendChild(el('b', null, 'D+' + f.horizon));
        lab.appendChild(el('span', null, f.weekday + ' ' + fmtDate(f.forecast_date)));
        d.appendChild(lab);
        var fams = el('div', 'fams');
        f.families.forEach(function (x, i) {
          var c = el('div', 'fam');
          c.appendChild(el('span', 'rank', (i + 1) + '.'));
          c.appendChild(el('b', null, x.family));
          var info = pct(x.conditional) + ' likelihood';
          c.appendChild(el('small', null, info));
          if (x.likely_causes && x.likely_causes.length) {
            c.appendChild(el('small', 'cause-hint', 'Likely: ' + x.likely_causes.join(', ')));
          }
          fams.appendChild(c);
        });
        d.appendChild(fams);
        panel.appendChild(d);
      });
      root.appendChild(panel);
    });
  }

  // ---- History: stacked bars (E/M/Other per day) ----
  function renderHistory(p) {
    var root = $('history'); root.replaceChildren();
    ['VRM-1', 'VRM-2'].forEach(function (mill) {
      var days = p.context[mill].daily, W = 600, H = 150, L = 22, B = 18, T = 8, n = days.length, bw = (W - L) / n;
      var max = Math.max(3, Math.max.apply(null, days.map(function (d) { return d.electrical + d.mechanical + d.other; })));
      var wrap = el('div'); wrap.appendChild(el('h3', null, mill)); wrap.lastChild.style.cssText = 'font-size:14px;margin:0 0 4px';
      var NS = 'http://www.w3.org/2000/svg', svg = document.createElementNS(NS, 'svg');
      svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H); svg.setAttribute('role', 'img');
      svg.setAttribute('aria-label', mill + ' breakdowns per day by type, last 60 days');
      function node(t, a, txt) { var e = document.createElementNS(NS, t); for (var k in a) e.setAttribute(k, a[k]); if (txt != null) e.textContent = txt; svg.appendChild(e); return e; }
      var y = function (v) { return H - B - (v / max) * (H - B - T); };
      for (var g = 0; g <= max; g += max > 4 ? 2 : 1) { node('line', { x1: L, x2: W, y1: y(g), y2: y(g), class: 'axis' }); node('text', { x: L - 4, y: y(g) + 3, 'text-anchor': 'end' }, g); }
      days.forEach(function (d, i) {
        var x = L + i * bw + 1, w = Math.max(2, bw - 2), base = H - B;
        var title = [fmtDate(d.date), d.electrical + ' electrical, ' + d.mechanical + ' mechanical, ' + d.other + ' other'];
        if (d.state === 'SHUTDOWN') title.push('Planned shutdown'); else if (d.state === 'DATA_GAP') title.push('No log for this day');
        if (d.state === 'SHUTDOWN' || d.state === 'DATA_GAP') node('rect', { x: x, y: base - 3, width: w, height: 3, rx: 1, fill: 'var(--none)', class: 'bar' });
        if (d.electrical) { var he = base - y(d.electrical); node('rect', { x: x, y: base - he, width: w, height: he, rx: 2, fill: 'var(--e)', class: 'bar' }); base -= he + (d.mechanical || d.other ? 2 : 0); }
        if (d.mechanical) { var hm = H - B - y(d.mechanical); node('rect', { x: x, y: base - hm, width: w, height: hm, rx: 2, fill: 'var(--m)', class: 'bar' }); base -= hm + (d.other ? 2 : 0); }
        if (d.other) { var ho = H - B - y(d.other); node('rect', { x: x, y: base - ho, width: w, height: ho, rx: 2, fill: 'var(--o)', class: 'bar' }); }
        var hit = node('rect', { x: x - 1, y: T, width: w + 2, height: H - B - T, fill: 'transparent' });
        hit.addEventListener('mousemove', function (ev) { showTip(ev, title); }); hit.addEventListener('mouseleave', hideTip);
      });
      [0, Math.floor(n / 2), n - 1].forEach(function (i) { node('text', { x: L + i * bw + bw / 2, y: H - 4, 'text-anchor': i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle' }, fmtDate(days[i].date)); });
      wrap.appendChild(svg);
      var det = el('details'); det.appendChild(el('summary', 'hint', 'Show as table'));
      var t = el('table'), tb = el('tbody');
      var hd = el('tr'); ['Date', 'E', 'M', 'Other', 'Status'].forEach(function (h) { hd.appendChild(el('th', null, h)); }); t.appendChild(el('thead')).appendChild(hd);
      days.slice().reverse().forEach(function (d) { var r = el('tr'); [fmtDate(d.date), d.electrical, d.mechanical, d.other, d.state === 'SHUTDOWN' ? 'Shutdown' : d.state === 'DATA_GAP' ? 'No log' : 'OK'].forEach(function (c) { r.appendChild(el('td', null, String(c))); }); tb.appendChild(r); });
      t.appendChild(tb); var tw = el('div', 'tablewrap'); tw.style.maxHeight = '220px'; tw.appendChild(t); det.appendChild(tw); wrap.appendChild(det);
      root.appendChild(wrap);
    });
  }

  // ---- Equipment family bar chart (90 days) ----
  function renderFamilies(p) {
    var root = $('families'); root.replaceChildren();
    ['VRM-1', 'VRM-2'].forEach(function (mill) {
      var c = p.context[mill], wrap = el('div');
      wrap.appendChild(el('h3', null, mill + ' · ' + c.families_90d.reduce(function (a, r) { return a + r.events; }, 0) + ' E/M events'));
      wrap.lastChild.style.cssText = 'font-size:14px;margin:0 0 6px';
      var rows = c.families_90d.slice(0, 8), max = Math.max.apply(null, rows.map(function (r) { return r.events; }).concat([1]));
      rows.forEach(function (r) {
        var row = el('div', 'fambar'); row.appendChild(el('span', null, r.family));
        var tr = el('div', 'track'), f = el('div', 'fill'); f.style.width = (r.events / max * 100) + '%'; tr.appendChild(f); row.appendChild(tr);
        row.appendChild(el('span', 'n', String(r.events)));
        wrap.appendChild(row);
      });
      root.appendChild(wrap);
    });
  }

  // ---- Cause breakdown bar chart (90 days) ----
  function renderCauses(p) {
    var root = $('causes'); if (!root) return; root.replaceChildren();
    ['VRM-1', 'VRM-2'].forEach(function (mill) {
      var c = p.context[mill];
      if (!c.causes_90d || !c.causes_90d.length) return;
      var wrap = el('div');
      wrap.appendChild(el('h3', null, mill + ' · cause breakdown'));
      wrap.lastChild.style.cssText = 'font-size:14px;margin:0 0 6px';
      var rows = c.causes_90d.slice(0, 8), max = Math.max.apply(null, rows.map(function (r) { return r.events; }).concat([1]));
      rows.forEach(function (r) {
        var row = el('div', 'fambar'); row.appendChild(el('span', null, r.cause));
        var tr = el('div', 'track'), f = el('div', 'fill'); f.style.cssText = 'width:' + (r.events / max * 100) + '%;background:var(--m)'; tr.appendChild(f); row.appendChild(tr);
        row.appendChild(el('span', 'n', String(r.events)));
        wrap.appendChild(row);
      });
      root.appendChild(wrap);
    });
  }

  // ---- Backtest (equipment accuracy on E/M days) ----
  var RANGE = { from: '', to: '' };
  function inRange(d) { return (!RANGE.from || d >= RANGE.from) && (!RANGE.to || d <= RANGE.to); }
  function renderBacktest(b) {
    $('bt-note').textContent = b.meta.window + ' · ' + b.meta.note;
    var s = $('bt-summary'); s.replaceChildren();
    var full = !RANGE.from && !RANGE.to;
    var rows = b.rows.filter(function (r) { return inRange(r.date); });
    ['VRM-1', 'VRM-2'].forEach(function (m) {
      var x = b.summary[m], st = el('div', 'stat');
      var scored = rows.filter(function (r) { return r.mill === m && r.top2_hit !== null && r.top2_hit !== undefined; });
      var hits = scored.filter(function (r) { return r.top2_hit; }).length;
      st.appendChild(el('div', 'k', m + ' · equipment top-3 accuracy (' + scored.length + ' E/M days scored' + (full ? ' of ' + x.breakdown_days + ' breakdown days' : ' in selected dates') + ')'));
      st.appendChild(el('div', 'v', scored.length ? pct(hits / scored.length) + ' (' + hits + '/' + scored.length + ')' : 'n/a'));
      st.appendChild(el('div', 's', full ? 'vs ' + (x.baseline_top2_hit_rate == null ? 'n/a' : pct(x.baseline_top2_hit_rate)) + ' baseline (always pick the 3 most common families)' : 'Baseline is only computed for the full window'));
      s.appendChild(st);
    });
    var tb = document.querySelector('#bt-table tbody'); tb.replaceChildren();
    var shown = rows.filter(function (r) { return r.predicted_families != null; });
    if (!shown.length) { var er = el('tr'); var ec = el('td', 'na', 'No scored breakdown days in the selected dates.'); ec.colSpan = 6; er.appendChild(ec); tb.appendChild(er); }
    shown.forEach(function (r) {
      var tr = el('tr');
      tr.appendChild(el('td', null, fmtDate(r.date)));
      tr.appendChild(el('td', null, r.mill));
      tr.appendChild(el('td', null, r.actual_types.join(', ')));
      tr.appendChild(el('td', null, r.predicted_families ? r.predicted_families.join(', ') : '–'));
      tr.appendChild(el('td', null, r.actual_families ? r.actual_families.join(', ') : '–'));
      var hit = r.top2_hit;
      var txt = hit === true ? '✓ Hit' : hit === false ? '✗ Missed' : '–';
      tr.appendChild(el('td', hit === true ? 'ok' : hit === false ? 'miss' : 'na', txt));
      tb.appendChild(tr);
    });
  }

  // ---- Live tracking ----
  function renderTracking(hAll) {
    var h = hAll.filter(function (e) { return inRange(e.forecast_date); });
    var done = h.filter(function (e) { return e.equip_hit !== null && e.equip_hit !== undefined; });
    var hits = done.filter(function (e) { return e.equip_hit; }).length;
    $('track-summary').textContent = done.length
      ? 'Equipment prediction: ' + hits + ' of ' + done.length + ' scored E/M days had the right equipment in the top 3 (' + pct(hits / done.length) + ').'
      : (hAll.length ? 'No published forecasts in the selected dates.' : 'Tracking started with the first published forecast. Results appear here as each forecast day passes.');
    var tb = document.querySelector('#track-table tbody'); tb.replaceChildren();
    h.slice().sort(function (a, b) { return a.forecast_date < b.forecast_date ? 1 : a.forecast_date > b.forecast_date ? -1 : a.mill < b.mill ? -1 : 1; }).forEach(function (e) {
      var tr = el('tr');
      [fmtDate(e.forecast_date), e.mill, 'D+' + e.horizon, e.predicted ? e.predicted.join(', ') : '–', e.actual_families ? e.actual_families.join(', ') : '–'].forEach(function (c) { tr.appendChild(el('td', null, c)); });
      var o;
      if (e.actual_status === 'pending') o = ['na', 'Pending'];
      else if (e.actual_status === 'excluded') o = ['na', 'Excluded'];
      else if (e.actual_status === 'no_em') o = ['na', 'No E/M breakdown'];
      else if (e.equip_hit === true) o = ['ok', '✓ Hit'];
      else if (e.equip_hit === false) o = ['miss', '✗ Missed'];
      else o = ['na', 'E/M but no scored equip'];
      tr.appendChild(el('td', o[0], o[1])); tb.appendChild(tr);
    });
  }

  // ---- Date filter ----
  function setupFilter(b, h) {
    var from = $('f-from'), to = $('f-to');
    var dates = b.rows.map(function (r) { return r.date; }).concat(h.map(function (e) { return e.forecast_date; })).sort();
    var min = dates[0], max = dates[dates.length - 1];
    var end = b.rows.map(function (r) { return r.date; }).sort().pop();
    from.min = to.min = min; from.max = to.max = max;
    function apply() {
      RANGE.from = from.value; RANGE.to = to.value;
      if (RANGE.from && RANGE.to && RANGE.from > RANGE.to) { var t = RANGE.from; RANGE.from = to.value = RANGE.to; RANGE.to = from.value = t; }
      var lbl = $('f-label');
      lbl.textContent = (RANGE.from || RANGE.to) ? 'Showing ' + (RANGE.from ? fmtDate(RANGE.from) : 'start') + ' to ' + (RANGE.to ? fmtDate(RANGE.to) : 'latest') : 'Showing all dates (' + fmtDate(min) + ' to ' + fmtDate(max) + ')';
      renderBacktest(b); renderTracking(h);
    }
    function preset(days) {
      var d = new Date(end + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate() - (days - 1));
      from.value = days ? d.toISOString().slice(0, 10) : ''; to.value = days ? end : ''; apply();
    }
    from.addEventListener('change', apply); to.addEventListener('change', apply);
    [['f-7', 7], ['f-30', 30], ['f-all', 0]].forEach(function (p) { $(p[0]).addEventListener('click', function () { preset(p[1]); }); });
    apply();
  }

  // ---- Load and render ----
  Promise.all([load('predictions'), load('backtest'), load('prediction_history')]).then(function (r) {
    var p = r[0];
    $('meta').textContent = 'Data through ' + fmtDate(p.meta.data_through) + ' · updated ' + p.meta.generated_at + ' · ' + p.meta.model_version;
    renderOverdue(p);
    renderForecast(p);
    renderHistory(p);
    renderFamilies(p);
    renderCauses(p);
    setupFilter(r[1], r[2]);
    $('quality').textContent = p.meta.rows_in_sheet.toLocaleString() + ' rows read, ' + p.meta.duplicate_rows_removed.toLocaleString() + ' duplicate entries removed. VRM-1: ' + p.context['VRM-1'].gap_days_30d + ' day(s) with no log in the last 30; VRM-2: ' + p.context['VRM-2'].gap_days_30d + '.';
  }).catch(function (e) { $('meta').textContent = 'Could not load forecast data (' + e.message + '). Run the GitHub Action or open this page through GitHub Pages.'; });
})();
