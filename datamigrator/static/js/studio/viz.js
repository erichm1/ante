/* Studio viz — the small set of data-visualisation pieces the editors share:
     S.viz.kpis      a row of stat tiles
     S.viz.bars      stacked column history (one column per run)
     S.viz.meter     a thin proportion bar (good / bad / not-yet)
     S.viz.spark     a tiny throughput line
     S.viz.cell      how a data value is drawn in the preview tables

   Colour roles (defined in studio.css, validated with the dataviz skill's
   validate_palette against both panel surfaces — CVD ΔE 12.3, contrast ≥ 3:1):
     --viz-good  records written / steps ok        --viz-bad   failed
     --viz-rest  read-but-not-written / not run    (neutral, recedes)
   Text never wears a series colour: labels and values use ink tokens and a
   coloured swatch beside them carries identity. Every chart has a legend, a
   hover/focus tooltip, and its numbers are also in the table next to it. */
(function () {
  'use strict';
  const S = window.Studio;
  const h = S.h;
  const NS = 'http://www.w3.org/2000/svg';
  const svg = (tag, attrs) => {
    const e = document.createElementNS(NS, tag);
    Object.entries(attrs || {}).forEach(([k, v]) => e.setAttribute(k, v));
    return e;
  };

  const viz = S.viz = {};

  // 1,284 · 12.9K · 4.2M
  viz.compact = n => {
    n = Number(n) || 0;
    if (n >= 1e6) return `${+(n / 1e6).toFixed(1)}M`;
    if (n >= 1e4) return `${+(n / 1e3).toFixed(1)}K`;
    return n.toLocaleString((window.anteI18n ? window.anteI18n.locale() : []));
  };

  // ── Tooltip ──────────────────────────────────────────────────────────────
  // One shared element. Content is built with text nodes only: run/step names
  // and API values are untrusted data.
  let tipEl = null;
  viz.hideTip = () => { if (tipEl) tipEl.style.display = 'none'; };
  // spec: { title, value, rows: [[label, value], …] }; anchor: a pointer event or an element.
  viz.showTip = (anchor, spec) => {
    if (!tipEl) { tipEl = h('div', { class: 'st-tip', role: 'tooltip' }); document.body.append(tipEl); }
    tipEl.replaceChildren(
      h('div', { class: 'st-tip-title', text: spec.title }),
      spec.value ? h('div', { class: 'st-tip-value', text: spec.value }) : null,
      ...(spec.rows || []).map(([k, v]) => h('div', { class: 'st-tip-row' }, h('span', { text: k }), h('b', { text: v }))));
    tipEl.style.display = 'block';
    const r = tipEl.getBoundingClientRect();
    let x, y;
    if (anchor.clientX != null) { x = anchor.clientX + 14; y = anchor.clientY - r.height - 10; }
    else { const a = anchor.getBoundingClientRect(); x = a.left + a.width / 2 - r.width / 2; y = a.top - r.height - 8; }
    tipEl.style.left = Math.max(6, Math.min(x, innerWidth - r.width - 6)) + 'px';
    tipEl.style.top = Math.max(6, y < 6 ? y + r.height + 24 : y) + 'px';
  };

  // ── Stat tiles ───────────────────────────────────────────────────────────
  // tiles: [{ label, value, sub (string | node) }]. Sentence-case label, proportional-figure value.
  viz.kpis = tiles => h('div', { class: 'st-kpis' }, tiles.map(t => h('div', { class: 'st-kpi' },
    h('div', { class: 'st-kpi-label', text: t.label }),
    h('div', { class: 'st-kpi-value', text: t.value }),
    h('div', { class: 'st-kpi-sub' }, t.sub == null ? '' : t.sub))));

  // ── Meter ────────────────────────────────────────────────────────────────
  // Proportion of `total`: good and bad segments, the remainder is the track. Zero
  // total draws an empty track. Segments are separated by a 2px surface gap.
  viz.meter = ({ good = 0, bad = 0, total = 0 }) => {
    const t = Math.max(total, good + bad, 1);
    const seg = (cls, v) => v > 0 ? h('span', { class: `seg ${cls}`, style: `width:${(v / t) * 100}%` }) : null;
    return h('div', { class: 'st-meter', role: 'img', 'aria-label': `${good} written, ${bad} failed of ${total}`, title: `${good} written · ${bad} failed · ${total} read` },
      seg('good', good), seg('bad', bad));
  };

  // ── Sparkline ────────────────────────────────────────────────────────────
  // values: numbers over time (e.g. cumulative records written per poll). 2px line,
  // ~10% area wash, 8px end dot with a 2px surface ring; the caller labels the end value.
  viz.spark = (values, { width = 150, height = 34, title = '' } = {}) => {
    const pad = 5;
    const max = Math.max(...values, 1);
    const x = i => pad + (i / Math.max(values.length - 1, 1)) * (width - pad * 2);
    const y = v => height - pad - (v / max) * (height - pad * 2);
    const pts = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`);
    const root = svg('svg', { class: 'st-spark', width, height, viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': title });
    root.append(
      svg('polygon', { class: 'area', points: `${x(0)},${height - pad} ${pts.join(' ')} ${x(values.length - 1)},${height - pad}` }),
      svg('polyline', { class: 'line', points: pts.join(' ') }),
      svg('circle', { class: 'dot', cx: x(values.length - 1), cy: y(values[values.length - 1]), r: 4 }));
    if (title) root.append(svg('title'));
    if (title) root.lastChild.textContent = title;
    return root;
  };

  // ── Stacked column history ───────────────────────────────────────────────
  // items: [{ id, label, good, bad, rest, tip: { title, value, rows } }] oldest → newest.
  // opts:  { selectedId, onSelect(id), legend: { good, bad, rest }, height }
  // Columns are ≤ 24px, the outer end of each stack has a 4px round, segments are
  // separated by a 2px gap, and only the first/last x labels are drawn — the
  // tooltip and the table beside the chart carry every value.
  viz.bars = (items, opts = {}) => {
    const legend = opts.legend || { good: 'Written', bad: 'Failed', rest: 'Not written' };
    const max = Math.max(1, ...items.map(i => i.good + i.bad + i.rest));
    const pct = v => `${(v / max) * 94}%`;   // 6% headroom so the top grid label never touches a column

    const bar = it => {
      const total = it.good + it.bad + it.rest;
      const segs = [['good', it.good], ['bad', it.bad], ['rest', it.rest]].filter(([, v]) => v > 0);
      const top = segs.length - 1;
      const els = total === 0
        // Nothing was read (failed early, scheduled…): a 3px tick keeps the run visible.
        ? [h('span', { class: `seg ${it.tickBad ? 'bad' : 'rest'} tick` })]
        : segs.map(([cls, v], i) => h('span', { class: `seg ${cls} ${i === top ? 'top' : ''}`, style: `height:${pct(v)}` }));
      const b = h('button', {
        type: 'button', class: `st-bar ${opts.selectedId === it.id ? 'sel' : ''}`,
        'aria-label': `${it.tip.title}: ${it.tip.value}`,
        onpointermove: e => viz.showTip(e, it.tip), onpointerleave: viz.hideTip,
        onfocus: () => viz.showTip(b, it.tip), onblur: viz.hideTip,
        onclick: () => opts.onSelect && opts.onSelect(it.id),
      }, els);
      return b;
    };

    const key = (cls, text) => h('span', { class: 'st-key' }, h('i', { class: `swatch ${cls}` }), text);
    return h('div', { class: 'st-chart' },
      h('div', { class: 'st-plot', style: `height:${opts.height || 104}px` },
        h('div', { class: 'st-gl top' }, h('span', { class: 'tick', text: viz.compact(max) })),
        h('div', { class: 'st-gl base' }, h('span', { class: 'tick', text: '0' })),
        h('div', { class: 'st-bar-row' }, items.map(bar))),
      h('div', { class: 'st-xaxis' }, h('span', { text: items.length ? items[0].label : '' }), h('span', { text: items.length > 1 ? items[items.length - 1].label : '' })),
      h('div', { class: 'st-legend' }, key('good', legend.good), key('bad', legend.bad), legend.rest ? key('rest', legend.rest) : null));
  };

  // ── Preview table cells ──────────────────────────────────────────────────
  viz.cell = value => {
    if (value === null || value === undefined) return h('span', { class: 'st-null', text: 'null' });
    if (value === '') return h('span', { class: 'st-null', text: '""' });
    const full = typeof value === 'object' ? JSON.stringify(value) : String(value);
    const shown = full.length > 60 ? full.slice(0, 57) + '…' : full;
    return h('span', { class: typeof value === 'number' ? 'st-num' : '', title: full.length > 60 || typeof value === 'object' ? full : null, text: shown });
  };
})();
