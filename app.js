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

  var TCLS = { Electrical: 'e', Mechanical: 'm', Other: 'o' };
  function mixBar(types) {
    var wrap = el('div'), bar = el('div', 'mixbar'); bar.setAttribute('role', 'img'); bar.setAttribute('aria-label', types.map(function (t) { return t.type + ' ' + pct(t.share); }).join(', '));
    types.forEach(function (t) { var i = el('i', TCLS[t.type]); i.style.width = (t.share * 100) + '%'; bar.appendChild(i); }); wrap.appendChild(bar);
    var tx = el('div', 'mixtxt'); types.forEach(function (t) { var s = el('span'); s.appendChild(el('i', 'sw ' + TCLS[t.type])); s.appendChild(document.createTextNode(t.type + ' ')); s.appendChild(el('b', null, pct(t.share))); tx.appendChild(s); }); wrap.appendChild(tx);
    return wrap;
  }
  function renderForecast(p) {
    var root = $('forecast'); root.replaceChildren();
    ['VRM-1', 'VRM-2'].forEach(function (mill) {
      var panel = el('div', 'panel'), h = el('h2', null, mill);
      h.appendChild(el('span', 'rate', 'last 90 days: ' + p.context[mill].event_days_90d + ' breakdown days'));
      panel.appendChild(h);
      p.forecasts.filter(function (f) { return f.mill === mill; }).forEach(function (f) {
        var d = el('div', 'day'), lab = el('div', 'dlabel'); lab.appendChild(el('b', null, 'D+' + f.horizon)); lab.appendChild(el('span', null, f.weekday + ' ' + fmtDate(f.forecast_date))); d.appendChild(lab);
        var ch = el('div'); ch.appendChild(mixBar(f.types)); d.appendChild(ch);
        var fams = el('div', 'fams'); f.families.forEach(function (x, i) {
          var c = el('div', 'fam'); c.appendChild(el('span', 'rank', (i + 1) + '.')); c.appendChild(el('b', null, x.family));
          c.appendChild(el('small', null, pct(x.conditional) + ' of electrical / mechanical breakdowns')); fams.appendChild(c);
        }); d.appendChild(fams); panel.appendChild(d);
      });
      root.appendChild(panel);
    });
  }

  function renderHistory(p) {
    var root = $('history'); root.replaceChildren();
    ['VRM-1', 'VRM-2'].forEach(function (mill) {
      var days = p.context[mill].daily, W = 600, H = 150, L = 22, B = 18, T = 8, n = days.length, bw = (W - L) / n;
      var max = Math.max(3, Math.max.apply(null, days.map(function (d) { return d.electrical + d.mechanical + d.other; })));
      var wrap = el('div'); wrap.appendChild(el('h3', null, mill)); wrap.lastChild.style.cssText = 'font-size:14px;margin:0 0 4px';
      var NS = 'http://www.w3.org/2000/svg', svg = document.createElementNS(NS, 'svg'); svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H); svg.setAttribute('role', 'img');
      svg.setAttribute('aria-label', mill + ' breakdowns per day by type, last 60 days');
      function node(t, a, txt) { var e = document.createElementNS(NS, t); for (var k in a) e.setAttribute(k, a[k]); if (txt != null) e.textContent = txt; svg.appendChild(e); return e; }
      var y = function (v) { return H - B - (v / max) * (H - B - T); };
      for (var g = 0; g <= max; g += max > 4 ? 2 : 1) { node('line', { x1: L, x2: W, y1: y(g), y2: y(g), class: 'axis' }); node('text', { x: L - 4, y: y(g) + 3, 'text-anchor': 'end' }, g); }
      days.forEach(function (d, i) {
        var x = L + i * bw + 1, w = Math.max(2, bw - 2), tot = d.electrical + d.mechanical + d.other, base = H - B, parts = [];
        var title = [fmtDate(d.date), d.electrical + ' electrical, ' + d.mechanical + ' mechanical, ' + d.other + ' other'];
        if (d.state === 'SHUTDOWN') title.push('Planned shutdown'); else if (d.state === 'DATA_GAP') title.push('No log for this day');
        if (d.state === 'SHUTDOWN' || d.state === 'DATA_GAP') parts.push(node('rect', { x: x, y: base - 3, width: w, height: 3, rx: 1, fill: 'var(--none)', class: 'bar' }));
        if (d.electrical) { var he = base - y(d.electrical); parts.push(node('rect', { x: x, y: base - he, width: w, height: he, rx: 2, fill: 'var(--e)', class: 'bar' })); base -= he + (d.mechanical || d.other ? 2 : 0); }
        if (d.mechanical) { var hm = H - B - y(d.mechanical); parts.push(node('rect', { x: x, y: base - hm, width: w, height: hm, rx: 2, fill: 'var(--m)', class: 'bar' })); base -= hm + (d.other ? 2 : 0); }
        if (d.other) { var ho = H - B - y(d.other); parts.push(node('rect', { x: x, y: base - ho, width: w, height: ho, rx: 2, fill: 'var(--o)', class: 'bar' })); }
        var hit = node('rect', { x: x - 1, y: T, width: w + 2, height: H - B - T, fill: 'transparent' });
        hit.addEventListener('mousemove', function (ev) { showTip(ev, title); }); hit.addEventListener('mouseleave', hideTip);
      });
      [0, Math.floor(n / 2), n - 1].forEach(function (i) { node('text', { x: L + i * bw + bw / 2, y: H - 4, 'text-anchor': i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle' }, fmtDate(days[i].date)); });
      wrap.appendChild(svg);
      var det = el('details'); det.appendChild(el('summary', 'hint', 'Show as table')); var t = el('table'), tb = el('tbody');
      var hd = el('tr'); ['Date', 'Electrical', 'Mechanical', 'Other', 'Status'].forEach(function (h) { hd.appendChild(el('th', null, h)); }); t.appendChild(el('thead')).appendChild(hd);
      days.slice().reverse().forEach(function (d) { var r = el('tr'); [fmtDate(d.date), d.electrical, d.mechanical, d.other, d.state === 'SHUTDOWN' ? 'Shutdown' : d.state === 'DATA_GAP' ? 'No log' : 'Operating'].forEach(function (c) { r.appendChild(el('td', null, String(c))); }); tb.appendChild(r); });
      t.appendChild(tb); var tw = el('div', 'tablewrap'); tw.style.maxHeight = '220px'; tw.appendChild(t); det.appendChild(tw); wrap.appendChild(det);
      root.appendChild(wrap);
    });
  }

  function renderFamilies(p) {
    var root = $('families'); root.replaceChildren();
    ['VRM-1', 'VRM-2'].forEach(function (mill) {
      var c = p.context[mill], wrap = el('div'); wrap.appendChild(el('h3', null, mill + ' · ' + c.families_90d.reduce(function (a, r) { return a + r.events; }, 0) + ' electrical / mechanical events')); wrap.lastChild.style.cssText = 'font-size:14px;margin:0 0 6px';
      var rows = c.families_90d.slice(0, 8), max = Math.max.apply(null, rows.map(function (r) { return r.events; }).concat([1]));
      rows.forEach(function (r) {
        var row = el('div', 'fambar'); row.appendChild(el('span', null, r.family)); var tr = el('div', 'track'), f = el('div', 'fill'); f.style.width = (r.events / max * 100) + '%'; tr.appendChild(f); row.appendChild(tr); row.appendChild(el('span', 'n', String(r.events))); wrap.appendChild(row);
      });
      root.appendChild(wrap);
    });
  }

  function mixText(types) { return types.map(function (t) { return t.type.charAt(0) + ' ' + pct(t.share); }).join(' · '); }
  function renderBacktest(b) {
    $('bt-note').textContent = b.meta.window + ' · ' + b.meta.note;
    var s = $('bt-summary'); s.replaceChildren();
    ['VRM-1', 'VRM-2'].forEach(function (m) {
      var x = b.summary[m], st = el('div', 'stat'); st.appendChild(el('div', 'k', m + ' · type shares: predicted vs actual (' + x.breakdown_days + ' breakdown days)'));
      var mix = function (o) { return ['Electrical', 'Mechanical', 'Other'].map(function (k) { return k.charAt(0) + ' ' + pct(o[k]); }).join(' · '); };
      st.appendChild(el('div', 'v', mix(x.predicted_mix))); st.appendChild(el('div', 's', 'Actual: ' + mix(x.actual_mix)));
      st.appendChild(el('div', 's', 'Equipment top-2 hit on electrical/mechanical days: ' + (x.top2_hit_rate == null ? 'n/a' : pct(x.top2_hit_rate)) + ' vs ' + (x.baseline_top2_hit_rate == null ? 'n/a' : pct(x.baseline_top2_hit_rate)) + ' for the usual suspects (' + x.em_days_scored + ' days)'));
      s.appendChild(st);
    });
    var tb = document.querySelector('#bt-table tbody'); tb.replaceChildren();
    b.rows.slice(0, 28).forEach(function (r) {
      var tr = el('tr'); tr.appendChild(el('td', null, fmtDate(r.date))); tr.appendChild(el('td', null, r.mill)); tr.appendChild(el('td', null, mixText(r.predicted_types))); tr.appendChild(el('td', null, r.actual_types.join(', ')));
      var txt = r.top2_hit === null || r.top2_hit === undefined ? '– Other only' : (r.top2_hit ? '✓ Hit: ' : '✗ Missed: ') + r.actual_families.join(', ');
      tr.appendChild(el('td', r.top2_hit === true ? 'ok' : r.top2_hit === false ? 'miss' : 'na', txt)); tb.appendChild(tr);
    });
  }

  function renderTracking(h) {
    var done = h.filter(function (e) { return e.top_type_hit !== null && e.top_type_hit !== undefined; }), hits = done.filter(function (e) { return e.top_type_hit; }).length;
    $('track-summary').textContent = done.length ? 'So far ' + hits + ' of ' + done.length + ' forecast days that had a breakdown included the predicted leading type (' + pct(hits / done.length) + ').' :
      'Tracking started with the first published forecast. Results appear here as each forecast day passes.';
    var tb = document.querySelector('#track-table tbody'); tb.replaceChildren();
    h.slice().sort(function (a, b) { return a.forecast_date < b.forecast_date ? 1 : a.forecast_date > b.forecast_date ? -1 : a.mill < b.mill ? -1 : 1; }).slice(0, 40).forEach(function (e) {
      var tr = el('tr'); [fmtDate(e.forecast_date), e.mill, 'D+' + e.horizon, mixText(e.types), e.predicted.join(', ')].forEach(function (c) { tr.appendChild(el('td', null, c)); });
      var o = e.actual_status === 'pending' ? ['na', 'Pending'] : e.actual_status === 'excluded' ? ['na', 'Not scored (shutdown or no log)'] : e.actual_status === 'none' ? ['na', 'No breakdown'] : [e.top_type_hit ? 'ok' : 'miss', (e.top_type_hit ? '✓ ' : '✗ ') + 'Actual: ' + e.actual_types.join(', ') + (e.actual_families.length ? ' (' + e.actual_families.join(', ') + ')' : '')];
      tr.appendChild(el('td', o[0], o[1])); tb.appendChild(tr);
    });
  }

  Promise.all([load('predictions'), load('backtest'), load('prediction_history')]).then(function (r) {
    var p = r[0]; $('meta').textContent = 'Data through ' + fmtDate(p.meta.data_through) + ' · updated ' + p.meta.generated_at + ' · ' + p.meta.model_version;
    renderForecast(p); renderHistory(p); renderFamilies(p); renderBacktest(r[1]); renderTracking(r[2]);
    $('quality').textContent = p.meta.rows_in_sheet.toLocaleString() + ' rows read, ' + p.meta.duplicate_rows_removed.toLocaleString() + ' duplicate entries removed. VRM-1: ' + p.context['VRM-1'].gap_days_30d + ' day(s) with no log in the last 30; VRM-2: ' + p.context['VRM-2'].gap_days_30d + '.';
  }).catch(function (e) { $('meta').textContent = 'Could not load forecast data (' + e.message + '). Run the GitHub Action or open this page through GitHub Pages.'; });
})();
