/* ChainEditor — a chain drawn as a flow: a START entry (the chain's
   connection) followed by one card per step (an HTTP call, a wait, a next-page loop, a find-in-list, a header check or a file preview — see step_kinds.js), joined by hops. Hops carry
   the names of the values the previous step captures for later steps to use
   as {{name}}. After a run every card wears a tick / cross, like Spoon does
   after executing a transformation.

   The step order IS the chain (steps run in `order`), so hops are always
   linear; to change the order use the ◀ ▶ buttons on a card (they call the
   same /move/ endpoint the classic page does). */
(function () {
  'use strict';
  const S = window.Studio;
  const h = S.h;

  const METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'];
  const COLS = 4;
  const SLOT_W = 340;
  const SLOT_H = 170;
  const FIRST_X = 216;                  // START sits to the left of the first card

  const K = S.stepKinds;
  const verbBadge = K.verbBadge;
  const resultState = K.resultState;
  const resultFailed = r => resultState(r) === 'error';

  class ChainEditor extends S.CanvasEditor {
    static kind = 'chain';

    constructor(id) {
      super(id);
      this.runLabel = 'Run chain';
      this.hasRunOptions = false;
      this.acceptMimes = ['application/x-ante-verb'];
      this.chain = null;
      this.steps = [];
      this.runs = [];
      this.selectedRun = null;
      this.fileEntityList = null;
      this.buildResults();
    }

    // ── Data ─────────────────────────────────────────────────────────────
    async load() {
      const [chain, runs] = await Promise.all([S.api(`/api/chains/${this.id}/`), S.api(`/api/chains/${this.id}/runs/`)]);
      this.chain = chain;
      this.steps = chain.steps;
      this.runs = runs;
      this.selectedRun = runs[0] || null;
      this.setTitle(chain.name);
      this.dataReady = true;
      this.rerender();
      this.renderAll();
      this.changed();
      // File-preview cards name their file; the list arrives in the background and redraws once.
      if (this.steps.some(x => x.kind === 'file')) this.fileEntities().then(() => this.rerender());
    }

    // Entities that have an uploaded file (cached for this editor; the file step's dialog uses it too).
    fileEntities() {
      if (!this.fileEntityList) {
        this.fileEntityList = S.apiList('/api/entities/').then(list => list.filter(e => e.has_file)).catch(() => []);
        this.fileEntityList.then(list => { this.fileNames = Object.fromEntries(list.map(e => [e.id, e.name])); });
      }
      return this.fileEntityList;
    }

    async reloadSteps({ resetLayout } = {}) {
      if (resetLayout) this.forgetPositions();
      this.chain = await S.api(`/api/chains/${this.id}/`);
      this.steps = this.chain.steps;
      this.rerender();
      this.renderAll();
      this.changed();
      S.refreshTree();
    }

    statusText() {
      if (!this.chain) return '';
      const run = this.selectedRun;
      return `${this.steps.length} step(s) on ${this.chain.connection_name}${run ? `  ·  last run #${run.id} ${run.status}` : ''}`;
    }
    canRun() { return !this.running && this.steps.length > 0; }
    onTreeChanged() { this.changed(); }

    // ── Canvas ───────────────────────────────────────────────────────────
    render() {
      const jsp = this.jsp;
      const saved = this.loadPositions();
      const results = this.resultsByName();

      const at = (nodeId, dx, dy) => { const p = saved[nodeId]; return p ? { x: p[0], y: p[1] } : { x: dx, y: dy }; };

      const startPos = at('start', 40, 40);
      const start = h('div', {
        class: 'st-node st-start', dataset: { node: 'start' }, title: 'Every step calls this connection — double-click to edit the chain',
        ondblclick: () => this.properties(),
        oncontextmenu: e => { e.preventDefault(); S.menu(e.clientX, e.clientY, [
          { head: this.chain.name },
          { label: 'Edit chain…', icon: 'bi-pencil', onClick: () => this.properties() },
          { label: 'Add step', icon: 'bi-plus-lg', onClick: () => this.stepDialog(null) },
          { label: 'Delete chain…', icon: 'bi-trash', danger: true, onClick: () => this.remove() },
        ]); },
      },
        h('i', { class: 'bi-plug' }), h('span', { class: 'st-start-label', text: 'Start' }), h('span', { class: 'st-start-sub', text: this.chain.connection_name }));

      jsp.batch(() => {
        this.placeNode(start, startPos.x, startPos.y, { onMoved: (x, y) => this.savePosition('start', x, y) });

        const nodes = this.steps.map((s, i) => {
          const pos = at(`step:${s.id}`, FIRST_X + (i % COLS) * SLOT_W, 40 + Math.floor(i / COLS) * SLOT_H);
          const node = this.stepNode(s, i);
          this.placeNode(node, pos.x, pos.y, { onMoved: (x, y) => this.savePosition(`step:${s.id}`, x, y) });
          const res = results[s.name];
          this.setNodeStatus(node, this.running ? 'running' : res ? resultState(res) : null);
          return node;
        });

        const hop = (from, to, label) => jsp.connect({
          source: from, target: to, anchors: ['Right', 'Left'], endpoint: 'Blank',
          connector: ['Flowchart', { cornerRadius: 10, stub: [24, 24], gap: 0 }],
          cssClass: 'st-hop',
          overlays: [['Arrow', { location: 1, width: 11, length: 11 }]].concat(label ? [['Label', { label, location: 0.5, cssClass: 'st-hop-label' }]] : []),
        });
        [start, ...nodes].forEach((node, i, all) => {
          if (i === all.length - 1) return;
          const caps = i === 0 ? [] : (this.steps[i - 1].captures || []).map(c => c.name);
          const label = caps.length ? `{{${caps[0]}${caps.length > 1 ? `, +${caps.length - 1}` : ''}}}` : '';
          hop(node, all[i + 1], label);
        });
      });

      this.emptyHint(this.steps.length ? '' : 'No steps yet.<br>Drag a step from <b>Design</b> onto the canvas (or click one) — start with an HTTP verb.');
      this.banner(
        h('i', { class: 'bi-link-45deg' }), h('span', { class: 'mono', text: this.chain.name }),
        h('span', { text: '· double-click a step to edit · steps run left → right' }));
    }

    stepNode(s, i) {
      const tags = [];
      if (s.kind === 'http') {
        if (s.is_async) tags.push(h('span', { class: 'st-tag warn', title: `Polls ${s.async_poll_path} until ${s.async_condition_path} = ${s.async_condition_value}`, text: '⏳ async' }));
        if (s.timeout_seconds) tags.push(h('span', { class: 'st-tag', title: `Gives up after ${s.timeout_seconds}s`, text: `⏱ ${s.timeout_seconds}s` }));
        if ((s.headers || []).length) tags.push(h('span', { class: 'st-tag', title: s.headers.map(x => x.name).join('\n'), text: `${s.headers.length} header${s.headers.length > 1 ? 's' : ''}` }));
        if ((s.query_params || []).length) tags.push(h('span', { class: 'st-tag', title: s.query_params.map(x => `${x.name}=${x.value}`).join('\n'), text: `?${s.query_params.length} param${s.query_params.length > 1 ? 's' : ''}` }));
        if (s.body) tags.push(h('span', { class: 'st-tag', text: 'body' }));
      } else if (s.kind === 'check') {
        tags.push(h('span', { class: 'st-tag', text: (s.params || {}).on_fail === 'warn' ? 'warns' : 'stops on fail' }));
      } else if (s.kind === 'find') {
        tags.push(h('span', { class: 'st-tag', text: (s.params || {}).pick === 'all' ? 'all matches' : 'first match' }));
      }
      if ((s.captures || []).length) tags.push(h('span', { class: 'st-tag ok', title: s.captures.map(c => `${c.name} ← ${c.path || '.'}`).join('\n'), text: `↳ ${s.captures.length} capture${s.captures.length > 1 ? 's' : ''}` }));

      const summary = K.summary(s, this.fileNames);
      const label = s.kind === 'http' ? `${s.method} ${s.name}` : `${K.meta[s.kind].label}: ${s.name}`;
      const tool = (icon, title, fn, extra) => h('button', { title, class: extra || '', onclick: e => { e.stopPropagation(); fn(); } }, h('i', { class: icon }));
      return h('div', {
        class: `st-node st-card kind-chain step-${s.kind}`, dataset: { node: `step:${s.id}`, kind: s.kind }, ondblclick: () => this.stepDialog(s),
        oncontextmenu: e => { e.preventDefault(); S.menu(e.clientX, e.clientY, [
          { head: label },
          { label: 'Edit step…', icon: 'bi-pencil', onClick: () => this.stepDialog(s) },
          ...(i > 0 ? [{ label: 'Run earlier', icon: 'bi-arrow-left', onClick: () => this.move(s, 'up') }] : []),
          ...(i < this.steps.length - 1 ? [{ label: 'Run later', icon: 'bi-arrow-right', onClick: () => this.move(s, 'down') }] : []),
          { label: 'Remove step', icon: 'bi-x-lg', danger: true, onClick: () => this.removeStep(s) },
        ]); },
      },
        h('div', { class: 'st-node-state' }, h('i')),
        h('div', { class: 'st-card-head' }, K.badge(s.kind, s.method), h('span', { class: 'st-card-title', text: s.name }), h('span', { class: 'st-card-order', text: `#${s.order}` })),
        h('div', { class: 'st-card-sub', title: summary, text: summary }),
        h('div', { class: 'st-card-tags' }, tags),
        h('div', { class: 'st-node-tools' },
          tool('bi-chevron-left', 'Run earlier', () => this.move(s, 'up')),
          tool('bi-pencil', 'Edit step', () => this.stepDialog(s)),
          tool('bi-chevron-right', 'Run later', () => this.move(s, 'down')),
          tool('bi-x-lg', 'Remove step', () => this.removeStep(s), 'danger')));
    }

    resultsByName() {
      const map = {};
      if (this.selectedRun) this.selectedRun.step_results.forEach(r => { map[r.name] = r; });
      return map;
    }

    applyStatuses() {
      const results = this.resultsByName();
      this.steps.forEach(s => {
        const node = this.surface.querySelector(`[data-node="step:${s.id}"]`);
        const res = results[s.name];
        this.setNodeStatus(node, this.running ? 'running' : res ? resultState(res) : null);
      });
    }

    async move(step, direction) {
      try { await S.api(`/api/chain-steps/${step.id}/move/`, { method: 'POST', body: { direction } }); await this.reloadSteps({ resetLayout: true }); }
      catch (e) { S.fail(e); }
    }

    async removeStep(step) {
      if (!(await confirmModal(`Remove step "${step.name}"?`))) return;
      try { await S.api(`/api/chain-steps/${step.id}/`, { method: 'DELETE' }); await this.reloadSteps({ resetLayout: true }); }
      catch (e) { S.fail(e); }
    }

    onDrop(mime, payload) { this.stepDialog(null, payload); }

    helpSections() {
      return [{ title: 'This chain', items: [
        [['Drag'], 'a step from the Design tab onto the canvas — or click it — to add it at the end'],
        [['Double-click'], 'a step to edit it; double-click START to edit the chain itself'],
        [['◀', '▶'], 'appear on a hovered step: run it earlier or later'],
        [['{{name}}'], 'inside a later step uses a value an earlier step captured'],
        [['Run'], 'executes the steps left to right — each gets a tick or a cross'],
      ] }];
    }

    // ── Design palette ───────────────────────────────────────────────────
    renderPalette(box) {
      const item = (icon, label, sub, payload) => S.paletteItem(icon, label, sub, 'application/x-ante-verb', payload, () => this.stepDialog(null, payload));
      const kindIcon = k => h('i', { class: K.meta[k].icon });
      box.append(
        h('div', { class: 'st-pal-group', text: 'Requests' }),
        ...METHODS.map(m => item(verbBadge(m), '', 'step', { kind: 'http', method: m })),
        h('div', { class: 'st-pal-group', text: 'Flow' }),
        item(kindIcon('wait'), 'Wait', 'pause', { kind: 'wait' }),
        item(h('i', { class: 'bi-arrow-repeat' }), 'Async job', 'start & poll', { kind: 'http', method: 'POST', async: true }),
        item(kindIcon('next'), 'Next page', 'pagination', { kind: 'next' }),
        h('div', { class: 'st-pal-group', text: 'Data' }),
        item(kindIcon('find'), 'Find in list', 'lookup', { kind: 'find' }),
        item(kindIcon('file'), 'File preview', 'csv / xlsx', { kind: 'file' }),
        h('div', { class: 'st-pal-group', text: 'Checks' }),
        item(kindIcon('check'), 'Check header', 'assert', { kind: 'check' }),
        h('div', { class: 'st-pal-hint' }, 'Drag a step onto the canvas, or click it. A later step can use an earlier step\'s result as ',
          h('span', { class: 'mono', text: '{{step_name.field}}' }), ', or a captured value as ', h('span', { class: 'mono', text: '{{name}}' }), '.'));
    }

    // ── Step dialog (add + edit) — the form itself lives in step_kinds.js ──
    stepDialog(step, preset = {}) {
      const kind = step ? step.kind : (preset.kind || 'http');
      const isNew = !step;
      const form = K.form({ kind, step, preset, steps: this.steps, fileEntities: () => this.fileEntities() });
      S.dialog({
        title: isNew ? `Add step — ${K.meta[kind].label}` : `Edit step — ${step.name}`, size: 'modal-lg',
        body: h('div', {}, form.nodes),
        actions: [{ label: 'Cancel' }, {
          label: isNew ? 'Add step' : 'Save', primary: true, onClick: async d => {
            let payload;
            try { payload = form.read(); } catch (e) { d.error(e.message); return false; }
            if (isNew) await S.api(`/api/chains/${this.id}/steps/`, { method: 'POST', body: payload });
            else await S.api(`/api/chain-steps/${step.id}/`, { method: 'PATCH', body: payload });
            await this.reloadSteps();
          },
        }],
      });
    }

    // ── Properties / delete ──────────────────────────────────────────────
    properties() {
      const c = this.chain;
      S.dialog({
        title: 'Chain properties',
        body: h('div', {},
          h('div', { class: 'mb-3' }, h('label', { class: 'form-label', text: 'Name' }), h('input', { class: 'form-control', id: 'cp_name', value: c.name })),
          h('div', { class: 'mb-3' }, h('label', { class: 'form-label', text: 'Connection' }),
            h('select', { class: 'form-select', id: 'cp_conn', html: S.tree.connections.map(x => `<option value="${x.id}" ${x.id === c.connection ? 'selected' : ''}>${S.esc(x.name)}</option>`).join('') })),
          h('div', {}, h('label', { class: 'form-label', text: 'Description' }), h('textarea', { class: 'form-control', id: 'cp_desc', rows: '2', text: c.description || '' }))),
        actions: [{ label: 'Cancel' }, {
          label: 'Save', primary: true, onClick: async d => {
            const name = d.$('#cp_name').value.trim();
            if (!name) { d.error('Name is required.'); return false; }
            this.chain = await S.api(`/api/chains/${this.id}/`, { method: 'PATCH', body: { name, connection: d.$('#cp_conn').value, description: d.$('#cp_desc').value } });
            this.setTitle(this.chain.name);
            this.rerender();
            this.changed();
            S.refreshTree();
          },
        }],
      });
    }

    // ── Running ──────────────────────────────────────────────────────────
    // A chain executes inside the request that started it, so the Kill goes to the chain (not to a run id):
    // that request then comes back with the run marked cancelled.
    canKill() { return this.running; }
    killMessage() { return `Kill "${this.chain.name}"? It stops between steps, mid-wait or mid-poll; a request already on the wire finishes (or times out) first.`; }
    async kill() {
      const r = await S.api(`/api/chains/${this.id}/cancel/`, { method: 'POST' });
      S.toast(r.cancelled ? 'Stopping…' : 'Nothing was running.', 'ok');
    }

    async run() {
      this.running = true;
      this.applyStatuses();
      this.changed();
      try {
        const run = await S.api(`/api/chains/${this.id}/execute/`, { method: 'POST' });
        await this.afterRun(run.id);
      } catch (e) { S.fail(e); }
      finally { this.running = false; this.applyStatuses(); this.changed(); S.refreshTree(); }
    }

    async retry(runId) {
      this.running = true;
      this.applyStatuses();
      this.changed();
      try {
        const run = await S.api(`/api/chains/${this.id}/retry-run/${runId}/`, { method: 'POST' });
        await this.afterRun(run.id);
      } catch (e) { S.fail(e); }
      finally { this.running = false; this.applyStatuses(); this.changed(); S.refreshTree(); }
    }

    async afterRun(runId) {
      this.runs = await S.api(`/api/chains/${this.id}/runs/`);
      this.selectedRun = this.runs.find(r => r.id === runId) || this.runs[0] || null;
      this.renderAll();
      this.tabs.select('steps');
      if (this.selectedRun) S.toast(`Run #${this.selectedRun.id} ${this.selectedRun.status}.`, ['success', 'cancelled'].includes(this.selectedRun.status) ? 'ok' : 'err');
    }

    async selectRun(runId) {
      if (!this.runs.some(r => r.id === runId)) this.runs = await S.api(`/api/chains/${this.id}/runs/`);
      this.selectedRun = this.runs.find(r => r.id === runId) || this.selectedRun;
      this.renderAll();
      this.tabs.select('steps');
    }

    // ── Results panel ────────────────────────────────────────────────────
    buildResults() {
      this.tabs = S.makeTabs([{ id: 'runs', label: 'Runs' }, { id: 'steps', label: 'Step results' }, { id: 'vars', label: 'Variables' }]);
      this.resultsEl.replaceChildren(this.tabs.root);
    }

    renderAll() { this.applyStatuses(); this.renderRuns(); this.renderSteps(); this.renderVars(); }

    renderRuns() {
      S.viz.hideTip();
      const page = this.tabs.page('runs');
      if (!this.runs.length) { page.replaceChildren(h('div', { class: 'st-empty', text: "This chain hasn't run yet — press Run chain." })); this.tabs.setNote(''); return; }
      page.replaceChildren(this.syncDashboard(), h('table', { class: 'st-table' },
        h('thead', {}, h('tr', {}, ['Run', 'Status', 'Steps', 'Started', 'Duration', ''].map(t => h('th', { text: t })))),
        h('tbody', {}, this.runs.map(r => {
          const ok = r.step_results.filter(x => !resultFailed(x)).length;
          return h('tr', { class: `st-clickable ${this.selectedRun && this.selectedRun.id === r.id ? 'selected' : ''}`, onclick: () => this.selectRun(r.id) },
            h('td', { class: 'mono', text: `#${r.id}` }), h('td', {}, S.chip(r.status)),
            h('td', { class: 'mono', text: `${ok}/${r.step_results.length} ok` }),
            h('td', { class: 'mono', text: S.fmtDateTime(r.started_at) }), h('td', { class: 'mono', text: S.duration(r.started_at, r.finished_at) }),
            h('td', { class: 'st-actions', onclick: e => e.stopPropagation() },
              r.status === 'failed' || r.status === 'cancelled' ? h('button', { class: 'st-mini', text: r.status === 'cancelled' ? 'Resume' : 'Retry from failed step', title: r.status === 'cancelled' ? 'Continue from where it was stopped, reusing the earlier steps\' results' : '', onclick: () => this.retry(r.id) }) : null, ' ',
              r.result_file_url ? h('a', { class: 'st-mini', href: r.result_file_url, target: '_blank', rel: 'noopener', text: 'JSON' }) : null, ' ',
              h('a', { class: 'st-mini', href: `/chains/${this.id}/runs/${r.id}/`, target: '_blank', rel: 'noopener', text: 'Detail' })));
        }))));
      this.tabs.setNote(`${this.runs.length} run(s)`);
    }

    // Run history at a glance: tiles + one column per recent run showing how far it got.
    syncDashboard() {
      const recent = this.runs.slice(0, 20);
      const okRuns = recent.filter(r => r.status === 'success').length;
      const secs = recent.filter(r => r.finished_at).map(r => (new Date(r.finished_at) - new Date(r.started_at)) / 1000).filter(x => x >= 0);
      const avg = secs.length ? secs.reduce((a, b) => a + b, 0) / secs.length : null;
      const fmt = sec => sec < 1 ? '<1s' : sec < 60 ? `${Math.round(sec)}s` : `${Math.floor(sec / 60)}m ${Math.round(sec % 60)}s`;
      const last = recent[0];
      const ago = S.ago(last.started_at);

      // The step that most often stops a run — the first place to look when a chain is flaky.
      const stops = {};
      recent.forEach(r => { const f = r.step_results.find(resultFailed); if (f) stops[f.name] = (stops[f.name] || 0) + 1; });
      const worst = Object.entries(stops).sort((a, b) => b[1] - a[1])[0];

      const items = recent.slice().reverse().map(r => {
        const bad = r.step_results.filter(resultFailed).length;
        const good = r.step_results.length - bad;
        return {
          id: r.id, label: `#${r.id}`, good, bad, rest: Math.max(0, this.steps.length - r.step_results.length), tickBad: r.status === 'failed',
          tip: {
            title: `Run #${r.id} · ${r.status}`, value: `${good} of ${this.steps.length} steps ok`,
            rows: [['Failed', String(bad)], ['Duration', r.finished_at ? S.duration(r.started_at, r.finished_at) : '—'], ['Started', S.fmtDateTime(r.started_at)]],
          },
        };
      });

      return h('div', { class: 'st-dash' },
        S.viz.kpis([
          { label: 'Last run', value: ago === 'now' ? 'just now' : `${ago} ago`, sub: S.chip(last.status) },
          { label: 'Success rate', value: `${Math.round((okRuns / recent.length) * 100)}%`, sub: `${okRuns} of ${recent.length} run${recent.length > 1 ? 's' : ''}` },
          { label: 'Avg duration', value: avg == null ? '—' : fmt(avg), sub: `over ${secs.length} run${secs.length === 1 ? '' : 's'}` },
          { label: 'Most failing step', value: worst ? worst[0] : 'none', sub: worst ? `stopped ${worst[1]} run${worst[1] > 1 ? 's' : ''}` : 'no failures' },
        ]),
        h('div', {}, h('div', { class: 'st-dash-title', text: 'Steps per run — click a column to inspect it' }),
          S.viz.bars(items, { selectedId: this.selectedRun && this.selectedRun.id, onSelect: id => this.selectRun(id), legend: { good: 'Steps ok', bad: 'Failed', rest: 'Not run' } })));
    }

    renderSteps() {
      const page = this.tabs.page('steps');
      const run = this.selectedRun;
      if (!run) { page.replaceChildren(h('div', { class: 'st-empty', text: 'Run the chain to see every step\'s request and response here.' })); return; }
      const rows = [];
      run.step_results.forEach(r => {
        const state = resultState(r);
        const detail = h('tr', { class: 'd-none' }, h('td', { colspan: '5' }, K.resultDetail(r)));
        const chip = state === 'cancelled' ? S.chip('cancelled')
          : state === 'error' ? S.chip('failed', r.error ? 'error' : r.status_code)
          : state === 'warn' ? S.chip('pending', 'warning')
          : S.chip('success', r.kind === 'http' ? r.status_code : 'ok');
        rows.push(h('tr', { class: 'st-clickable', onclick: () => detail.classList.toggle('d-none') },
          h('td', { class: 'mono', text: r.order }), h('td', { class: 'mono', text: r.name }), h('td', {}, K.badge(r.kind, r.method)),
          h('td', { class: 'mono', text: K.resultSummary(r) }), h('td', {}, chip)), detail);
      });
      page.replaceChildren(
        h('div', { class: 'st-summary' }, S.chip(run.status), h('span', {}, 'Run ', h('b', { text: `#${run.id}` })), h('span', { class: 'text-dim', text: 'click a row for the details' })),
        h('table', { class: 'st-table' }, h('thead', {}, h('tr', {}, ['#', 'Step', 'Type', 'Result', 'Status'].map(t => h('th', { text: t })))), h('tbody', {}, rows)));
    }

    renderVars() {
      const page = this.tabs.page('vars');
      const rows = [];
      if (this.selectedRun) this.selectedRun.step_results.forEach(r => Object.entries(r.captured_variables || {}).forEach(([k, v]) => rows.push(h('tr', {},
        h('td', { class: 'mono', text: `{{${k}}}` }), h('td', { class: 'mono', text: typeof v === 'string' ? v : JSON.stringify(v) }), h('td', { class: 'mono text-dim', text: r.name })))));
      page.replaceChildren(rows.length
        ? h('table', { class: 'st-table' }, h('thead', {}, h('tr', {}, ['Variable', 'Value', 'Captured from'].map(t => h('th', { text: t })))), h('tbody', {}, rows))
        : h('div', { class: 'st-empty', text: this.selectedRun ? 'This run captured no variables.' : 'Captured variables appear here after a run.' }));
    }
  }

  S.editorClasses.chain = ChainEditor;
})();
