/* PlanEditor — a plan drawn like a Pentaho job: START → entries → END, where
   every entry is a mapping run or a chain execution, joined by hops. Steps run
   sequentially in `order`, one at a time (plans/executor.py).

   Double-click an entry to open the mapping/chain it runs in its own tab —
   the same drill-down Spoon gives a job entry that calls a transformation.

   This is also where a *customized run* is built, by drag and drop:
     - drag a mapping, one of its entity pairs (run just that pair), a chain, a Wait, or any
       *function block* — a request, async job, next page, find in list, file preview, check header —
       from the Design tab (or a mapping/chain from the explorer) onto the canvas. Function blocks
       share one context, so a find can search what an earlier request returned;
     - drop it on a step to insert it before that step, on empty space to add it at the end;
     - drag a step onto another step to reorder — the target lights up, and the step takes its place.
   Plans stay editable until they are executing/scheduled, so a finished run can be reshaped and
   executed again. While a plan executes we poll it and update the nodes in place; the toolbar's
   Kill button stops it (cancel between steps, mid-wait, and inside the running mapping/chain). */
(function () {
  'use strict';
  const S = window.Studio;
  const h = S.h;

  const COLS = 4;
  const SLOT_W = 340;
  const SLOT_H = 170;
  const FIRST_X = 216;

  const K = S.stepKinds;

  // status → what the node/table should show
  // planStatus matters for a step that started but never finished: while the plan executes that is "running";
  // once the plan has stopped it was interrupted (killed), not still going.
  function stepState(step, planStatus) {
    const interrupted = { status: 'cancelled', label: 'cancelled' };
    if (step.kind === 'wait') {
      if (step.finished_at) return { status: 'success', label: 'done' };
      if (step.started_at) return planStatus === 'executing' ? { status: 'running', label: 'waiting' } : interrupted;
      return { status: null, label: 'not started' };
    }
    if (step.kind === 'function') {
      if (step.result) {
        const st = K.resultState(step.result);
        return { status: st === 'error' ? 'failed' : st, label: { error: 'failed', warn: 'warning', cancelled: 'cancelled', success: 'done' }[st] };
      }
      if (step.finished_at) return { status: 'success', label: 'done' };
      if (step.started_at) return planStatus === 'executing' ? { status: 'running', label: 'running' } : interrupted;
      return { status: null, label: 'not started' };
    }
    const r = step.chain ? step.chain_run : step.run;
    if (!r) return { status: null, label: 'not started' };
    if (r.status === 'cancelled') return { status: 'cancelled', label: 'cancelled' };
    if (step.chain) {
      if (!r.finished_at) return { status: 'running', label: 'running' };
      return { status: r.status === 'success' ? 'success' : 'failed', label: r.status };
    }
    if (r.status === 'running' || r.status === 'pending') return { status: 'running', label: r.status };
    return { status: r.status === 'success' ? 'success' : 'failed', label: r.status };
  }

  const fmtSeconds = n => n < 60 ? `${+n.toFixed(1)}s` : n < 3600 ? `${Math.floor(n / 60)}m${n % 60 ? ` ${Math.round(n % 60)}s` : ''}` : `${+(n / 3600).toFixed(1)}h`;
  const isWait = s => s.kind === 'wait';
  const isFn = s => s.kind === 'function';
  const noDoc = s => isWait(s) || isFn(s);        // steps that aren't a mapping/chain document

  class PlanEditor extends S.CanvasEditor {
    static kind = 'plan';

    constructor(id) {
      super(id);
      this.runLabel = 'Execute plan';
      this.hasRunOptions = true;
      this.acceptMimes = ['application/x-ante-doc'];
      this.plan = null;
      this.timer = null;
      this.nodeRefs = {};
      this.buildResults();
    }

    // ── Data ─────────────────────────────────────────────────────────────
    get steps() { return this.plan ? this.plan.steps : []; }
    // Steps can change whenever the plan isn't in flight (a finished run can be reshaped and run again).
    get editable() { return !!this.plan && !['executing', 'scheduled'].includes(this.plan.status); }
    get executing() { return !!this.plan && this.plan.status === 'executing'; }

    async load() {
      this.plan = await S.api(`/api/plans/${this.id}/`);
      this.setTitle(this.plan.name);
      this.dataReady = true;
      this.rerender();
      this.renderResults();
      this.syncPolling();
      this.changed();
      if (S.active === this) S.renderPalette();   // the palette depends on data that just arrived
    }

    async reload({ resetLayout } = {}) {
      if (resetLayout) this.forgetPositions();
      this.plan = await S.api(`/api/plans/${this.id}/`);
      this.rerender();
      this.renderResults();
      this.syncPolling();
      this.changed();
      S.refreshTree();
    }

    statusText() {
      if (!this.plan) return '';
      const p = this.plan;
      return `${p.status}  ·  ${this.steps.length} step(s)${p.status === 'scheduled' && p.scheduled_at ? `  ·  runs ${S.fmtDateTime(p.scheduled_at)}` : ''}`;
    }
    canRun() { return !!this.plan && this.steps.length > 0 && !['executing', 'scheduled'].includes(this.plan.status); }
    onTreeChanged() {
      // A step whose mapping/chain was deleted elsewhere vanishes server-side; pull the fresh plan once.
      const missing = this.steps.some(s => !noDoc(s) && !S.treeItem(s.chain ? 'chain' : 'mapping', s.chain || s.mapping));
      if (missing && this.dataReady && !this.reloading && S.tree.mappings.length + S.tree.chains.length) {
        this.reloading = true;
        this.reload({ resetLayout: true }).catch(() => {}).finally(() => { this.reloading = false; });
        return;
      }
      this.decorateNodes();
      this.renderResults();
      if (S.active === this) S.renderPalette();
    }

    // ── Canvas ───────────────────────────────────────────────────────────
    render() {
      const jsp = this.jsp;
      const saved = this.loadPositions();
      const at = (id, dx, dy) => { const p = saved[id]; return p ? { x: p[0], y: p[1] } : { x: dx, y: dy }; };
      this.nodeRefs = {};

      const start = h('div', {
        class: 'st-node st-start', dataset: { node: 'start' }, title: 'Double-click to edit the plan',
        ondblclick: () => this.properties(),
        oncontextmenu: e => { e.preventDefault(); S.menu(e.clientX, e.clientY, [
          { head: this.plan.name },
          { label: 'Edit plan…', icon: 'bi-pencil', onClick: () => this.properties() },
          { label: 'Delete plan…', icon: 'bi-trash', danger: true, onClick: () => this.remove() },
        ]); },
      },
        h('i', { class: 'bi-play-circle' }), h('span', { class: 'st-start-label', text: 'Start' }), h('span', { class: 'st-start-sub', text: this.plan.name }));
      const end = h('div', { class: 'st-node st-start st-end', dataset: { node: 'end' } },
        h('div', { class: 'st-node-state' }, h('i')),
        h('i', { class: 'bi-flag' }), h('span', { class: 'st-start-label', text: 'Done' }));

      jsp.batch(() => {
        const sp = at('start', 40, 40);
        this.placeNode(start, sp.x, sp.y, { onMoved: (x, y) => this.savePosition('start', x, y) });

        const nodes = this.steps.map((s, i) => {
          const pos = at(`step:${s.id}`, FIRST_X + (i % COLS) * SLOT_W, 40 + Math.floor(i / COLS) * SLOT_H);
          const node = isFn(s) ? this.functionNode(s, i) : this.stepNode(s, i);
          this.placeNode(node, pos.x, pos.y, {
            onDrag: (x, y) => this.highlightDropTarget(s, x, y),
            onMoved: (x, y) => this.dropStep(s, x, y),
          });
          return node;
        });

        const n = this.steps.length;
        const ep = at('end', FIRST_X + (n % COLS) * SLOT_W, 40 + Math.floor(n / COLS) * SLOT_H);
        this.placeNode(end, ep.x, ep.y, { onMoved: (x, y) => this.savePosition('end', x, y) });

        const all = [start, ...nodes, end];
        all.forEach((node, i) => {
          if (i === all.length - 1) return;
          jsp.connect({
            source: node, target: all[i + 1], anchors: ['Right', 'Left'], endpoint: 'Blank',
            connector: ['Flowchart', { cornerRadius: 10, stub: [24, 24], gap: 0 }], cssClass: 'st-hop',
            overlays: [['Arrow', { location: 1, width: 11, length: 11 }]],
          });
        });
      });

      this.decorateNodes();
      this.emptyHint(this.steps.length ? '' : (this.editable
        ? 'Nothing here yet.<br>Drag a <b>mapping</b>, an <b>entity pair</b>, a <b>chain</b>, a <b>Wait</b> or a <b>function</b> (request, find, check…) from the <b>Design</b> tab onto the canvas to build this run.'
        : 'This plan has no steps.'));
      this.banner(
        h('i', { class: 'bi-list-ol' }), h('span', { class: 'mono', text: this.plan.name }), S.chip(this.plan.status),
        h('span', { text: this.editable ? '· drop blocks here · drag a step onto another to reorder' : '· steps are locked while the plan is executing or scheduled' }));
    }

    stepNode(s, i) {
      const wait = isWait(s);
      const isChain = !!s.chain;
      const kind = wait ? 'wait' : isChain ? 'chain' : 'mapping';
      const name = this.stepName(s);
      const tool = (icon, title, fn, extra, disabled) => h('button', { title, class: extra || '', disabled: disabled ? '' : null, onclick: e => { e.stopPropagation(); fn(); } }, h('i', { class: icon }));
      const pairsChoosable = kind === 'mapping' && (s.pairs_total || 0) > 1;

      const sub = h('div', { class: 'st-card-sub' });
      const tags = h('div', { class: 'st-card-tags' });
      const node = h('div', {
        class: `st-node st-card kind-${kind}`, dataset: { node: `step:${s.id}` },
        title: wait ? 'Double-click to change how long it waits' : 'Double-click to open', ondblclick: () => this.openStep(s),
        oncontextmenu: e => { e.preventDefault(); S.menu(e.clientX, e.clientY, [
          { head: `${name} · ${kind}` },
          ...(wait ? [{ label: 'Change wait time…', icon: 'bi-stopwatch', onClick: () => this.waitDialog(s) }] : [
            { label: 'Open', icon: 'bi-box-arrow-in-right', onClick: () => this.openStep(s) },
            { label: `Edit ${kind}…`, icon: 'bi-pencil', onClick: () => this.editTarget(s) }]),
          ...(pairsChoosable && this.editable ? [{ label: 'Choose entity pairs…', icon: 'bi-funnel', onClick: () => this.pairsDialog(s) }] : []),
          ...(kind === 'mapping' && this.editable ? [{ label: 'Rate limit…', icon: 'bi-speedometer2', onClick: () => this.rateDialog(s) }] : []),
          ...(this.editable && i > 0 ? [{ label: 'Run earlier', icon: 'bi-arrow-left', onClick: () => this.move(s, 'up') }] : []),
          ...(this.editable && i < this.steps.length - 1 ? [{ label: 'Run later', icon: 'bi-arrow-right', onClick: () => this.move(s, 'down') }] : []),
          ...(this.editable ? [{ label: wait ? 'Remove wait' : 'Remove from plan', icon: 'bi-x-lg', onClick: () => this.removeStep(s) }] : []),
          ...(wait ? [] : [{ label: `Delete ${kind}…`, icon: 'bi-trash', danger: true, onClick: () => this.deleteTarget(s) }]),
        ]); },
      },
        h('div', { class: 'st-node-state' }, h('i')),
        h('div', { class: 'st-card-head' },
          h('i', { class: `st-icon ${wait ? 'bi-stopwatch' : isChain ? S.icons.chain : S.icons.mapping}` }),
          h('span', { class: 'st-card-title', text: name }), h('span', { class: 'st-card-order', text: `#${s.order}` })),
        sub, tags,
        h('div', { class: 'st-node-tools' },
          tool('bi-chevron-left', 'Run earlier', () => this.move(s, 'up'), '', !this.editable || i === 0),
          tool('bi-chevron-right', 'Run later', () => this.move(s, 'down'), '', !this.editable || i === this.steps.length - 1),
          tool('bi-pencil', wait ? 'Change how long it waits' : `Edit this ${kind}`, () => wait ? this.waitDialog(s) : this.editTarget(s), '', wait && !this.editable),
          wait ? null : tool('bi-box-arrow-up-right', 'Open', () => this.openStep(s)),
          pairsChoosable ? tool('bi-funnel', 'Run only some entity pairs', () => this.pairsDialog(s), '', !this.editable) : null,
          kind === 'mapping' ? tool('bi-speedometer2', 'Rate limit for this step', () => this.rateDialog(s), '', !this.editable) : null,
          tool('bi-x-lg', wait ? 'Remove this wait' : `Remove this step from the plan (keeps the ${kind})`, () => this.removeStep(s), '', !this.editable),
          wait ? null : tool('bi-trash', `Delete the ${kind} itself`, () => this.deleteTarget(s), 'danger')));
      this.nodeRefs[s.id] = { node, sub, tags, title: node.querySelector('.st-card-title') };
      return node;
    }

    // A function block: one step of the plan's shared inline chain (request, next page, find, check, file preview…).
    functionNode(s, i) {
      const f = s.function;
      const tool = (icon, title, fn, extra, disabled) => h('button', { title, class: extra || '', disabled: disabled ? '' : null, onclick: e => { e.stopPropagation(); fn(); } }, h('i', { class: icon }));
      const label = f.kind === 'http' ? `${f.method} ${f.name}` : `${K.meta[f.kind].label}: ${f.name}`;
      const sub = h('div', { class: 'st-card-sub' });
      const tags = h('div', { class: 'st-card-tags' });
      const node = h('div', {
        class: `st-node st-card kind-chain step-${f.kind}`, dataset: { node: `step:${s.id}`, kind: f.kind },
        title: 'Double-click to edit', ondblclick: () => this.functionDialog(s),
        oncontextmenu: e => { e.preventDefault(); S.menu(e.clientX, e.clientY, [
          { head: label },
          ...(this.editable ? [{ label: 'Edit block…', icon: 'bi-pencil', onClick: () => this.functionDialog(s) }] : []),
          ...(this.editable && i > 0 ? [{ label: 'Run earlier', icon: 'bi-arrow-left', onClick: () => this.move(s, 'up') }] : []),
          ...(this.editable && i < this.steps.length - 1 ? [{ label: 'Run later', icon: 'bi-arrow-right', onClick: () => this.move(s, 'down') }] : []),
          ...(this.editable ? [{ label: 'Remove block', icon: 'bi-x-lg', danger: true, onClick: () => this.removeStep(s) }] : []),
        ]); },
      },
        h('div', { class: 'st-node-state' }, h('i')),
        h('div', { class: 'st-card-head' }, K.badge(f.kind, f.method), h('span', { class: 'st-card-title', text: f.name }), h('span', { class: 'st-card-order', text: `#${s.order}` })),
        sub, tags,
        h('div', { class: 'st-node-tools' },
          tool('bi-chevron-left', 'Run earlier', () => this.move(s, 'up'), '', !this.editable || i === 0),
          tool('bi-pencil', 'Edit this block', () => this.functionDialog(s), '', !this.editable),
          tool('bi-chevron-right', 'Run later', () => this.move(s, 'down'), '', !this.editable || i === this.steps.length - 1),
          tool('bi-x-lg', 'Remove this block', () => this.removeStep(s), 'danger', !this.editable)));
      this.nodeRefs[s.id] = { node, sub, tags, title: node.querySelector('.st-card-title') };
      return node;
    }

    // Fills in everything that depends on live data (subtitle from the explorer
    // tree, run status/counts) — cheap enough to run every poll tick.
    decorateNodes() {
      this.steps.forEach(s => {
        const ref = this.nodeRefs[s.id];
        if (!ref) return;
        if (isFn(s)) { this.decorateFunction(s, ref); return; }
        const wait = isWait(s);
        const isChain = !!s.chain;
        const doc = wait ? null : isChain ? S.tree.chains.find(c => c.id === s.chain) : S.tree.mappings.find(m => m.id === s.mapping);
        ref.title.textContent = this.stepName(s);
        ref.sub.textContent = wait ? 'pause before the next step'
          : doc ? (isChain ? `chain · on ${doc.connection}` : `${doc.source} → ${doc.destinations.join(', ') || '—'}`) : (isChain ? 'chain' : 'mapping');

        const st = stepState(s, this.plan && this.plan.status);
        this.setNodeStatus(ref.node, st.status);
        const tags = [h('span', { class: `st-tag ${st.status === 'success' ? 'ok' : st.status === 'failed' ? 'err' : st.status === 'running' ? 'warn' : ''}`, text: st.label })];
        if (!wait && !isChain) {
          if ((s.entity_mapping_ids || []).length) {
            const names = (doc ? doc.pair_list : []).filter(p => s.entity_mapping_ids.includes(p.id)).map(p => `${p.source} → ${p.target}`);
            tags.push(h('span', { class: 'st-tag warn', title: names.join('\n') || 'a subset of the mapping\'s entity pairs', text: `${s.entity_mapping_ids.length} of ${s.pairs_total} pairs` }));
          }
          if (s.run && s.run.status !== 'pending') tags.push(h('span', { class: 'st-tag', title: 'records read / written / failed', text: `${s.run.records_read}/${s.run.records_written}/${s.run.records_failed}` }));
          if (s.rate_limit_per_second) tags.push(h('span', { class: 'st-tag', title: 'step rate limit', text: `≤${Math.round(s.rate_limit_per_second * 60)}/min` }));
        }
        ref.tags.replaceChildren(...tags);
      });

      const end = this.surface.querySelector('[data-node="end"]');
      const p = this.plan && this.plan.status;
      this.setNodeStatus(end, p === 'completed' ? 'success' : p === 'failed' ? 'failed' : p === 'cancelled' ? 'cancelled' : p === 'executing' ? 'running' : null);
    }

    decorateFunction(s, ref) {
      const f = s.function;
      ref.title.textContent = f.name;
      ref.sub.textContent = (f.kind === 'http' && f.connection_name ? `${f.connection_name} · ` : '') + K.summary(f, this.fileNames);
      ref.sub.title = ref.sub.textContent;
      const st = stepState(s, this.plan && this.plan.status);
      this.setNodeStatus(ref.node, st.status);
      const tags = [h('span', { class: `st-tag ${st.status === 'success' ? 'ok' : st.status === 'failed' ? 'err' : st.status === 'running' || st.status === 'warn' ? 'warn' : ''}`, title: s.result && s.result.error ? s.result.error : null, text: st.label })];
      if (f.kind === 'http') {
        if (f.is_async) tags.push(h('span', { class: 'st-tag warn', text: '⏳ async' }));
        if (f.timeout_seconds) tags.push(h('span', { class: 'st-tag', text: `⏱ ${f.timeout_seconds}s` }));
        if ((f.headers || []).length) tags.push(h('span', { class: 'st-tag', title: f.headers.map(x => x.name).join('\n'), text: `${f.headers.length} header${f.headers.length > 1 ? 's' : ''}` }));
        if ((f.query_params || []).length) tags.push(h('span', { class: 'st-tag', title: f.query_params.map(x => `${x.name}=${x.value}`).join('\n'), text: `?${f.query_params.length} param${f.query_params.length > 1 ? 's' : ''}` }));
      }
      if ((f.captures || []).length) tags.push(h('span', { class: 'st-tag ok', title: f.captures.map(c => `${c.name} ← ${c.path || '.'}`).join('\n'), text: `↳ ${f.captures.length} capture${f.captures.length > 1 ? 's' : ''}` }));
      ref.tags.replaceChildren(...tags);
    }

    openStep(s) {
      if (isFn(s)) { if (this.editable) this.functionDialog(s); return; }
      if (isWait(s)) { if (this.editable) this.waitDialog(s); return; }
      S.openDoc(s.chain ? 'chain' : 'mapping', s.chain || s.mapping);
    }

    // A step's target name: the live explorer copy wins, so renaming a mapping/chain shows up here at once.
    stepName(s) {
      if (isWait(s)) return `Wait ${fmtSeconds(s.wait_seconds)}`;
      if (isFn(s)) return s.function.name;
      const doc = S.treeItem(s.chain ? 'chain' : 'mapping', s.chain || s.mapping);
      return doc ? doc.name : (s.chain ? s.chain_name : s.mapping_name);
    }

    // The mapping/chain a step runs is a document of its own: edit or delete it right from the step.
    editTarget(s) { if (isFn(s)) this.functionDialog(s); else if (isWait(s)) this.waitDialog(s); else S.editDoc(s.chain ? 'chain' : 'mapping', s.chain || s.mapping); }
    async deleteTarget(s) {
      // Deleting the target removes every plan step pointing at it (this plan's included), so redraw.
      if (await S.deleteDoc(s.chain ? 'chain' : 'mapping', s.chain || s.mapping)) await this.reload({ resetLayout: true });
    }

    // ── Drag and drop: reorder + insert ──────────────────────────────────
    // The step node (other than `except`) whose box contains the canvas point (cx, cy), if any.
    stepAt(cx, cy, except) {
      return this.steps.find(t => {
        if (except && t.id === except.id) return false;
        const n = this.nodeRefs[t.id] && this.nodeRefs[t.id].node;
        return n && cx >= n.offsetLeft && cx <= n.offsetLeft + n.offsetWidth && cy >= n.offsetTop && cy <= n.offsetTop + n.offsetHeight;
      });
    }

    // While dragging a step: light up the step it would swap places with.
    highlightDropTarget(step, x, y) {
      if (!this.editable) return;
      const n = this.nodeRefs[step.id].node;
      const target = this.stepAt(x + n.offsetWidth / 2, y + n.offsetHeight / 2, step);
      Object.values(this.nodeRefs).forEach(r => r.node.classList.toggle('drop-target', !!target && r.node === this.nodeRefs[target.id].node));
    }

    // Released: on another step → reorder; anywhere else → just remember the position.
    async dropStep(step, x, y) {
      Object.values(this.nodeRefs).forEach(r => r.node.classList.remove('drop-target'));
      const n = this.nodeRefs[step.id].node;
      const target = this.editable ? this.stepAt(x + n.offsetWidth / 2, y + n.offsetHeight / 2, step) : null;
      if (!target) { this.savePosition(`step:${step.id}`, x, y); return; }
      // The dragged step takes the target's place: dragging forward lands after it, backward before it.
      const ids = this.steps.map(t => t.id);
      const from = ids.indexOf(step.id);
      const to = ids.indexOf(target.id);
      ids.splice(from, 1);
      ids.splice(ids.indexOf(target.id) + (from < to ? 1 : 0), 0, step.id);
      try {
        this.plan = await S.api(`/api/plans/${this.id}/reorder/`, { method: 'POST', body: { order: ids } });
        this.forgetPositions();
        await this.reload();
      } catch (e) { S.fail(e); await this.reload(); }     // redraw either way so the node snaps back
    }

    async move(step, direction) {
      try { await S.api(`/api/plan-steps/${step.id}/move/`, { method: 'POST', body: { direction } }); await this.reload({ resetLayout: true }); }
      catch (e) { S.fail(e); }
    }
    async removeStep(step) {
      if (!(await confirmModal('Remove this step from the plan?'))) return;
      try { await S.api(`/api/plan-steps/${step.id}/`, { method: 'DELETE' }); await this.reload({ resetLayout: true }); }
      catch (e) { S.fail(e); }
    }

    // kind: 'mapping' | 'chain' | 'wait'. opts: { pairs (mapping: only these entity pair ids), seconds (wait), position }.
    async addStep(kind, docId, opts = {}) {
      if (!this.editable) { S.toast(`This plan is ${this.plan.status} — steps can't change right now.`, 'err'); return; }
      const mode = this.plan.execution_mode;
      if (kind === 'chain' && mode === 'simple') { S.toast('This plan only runs mappings (simple mode).', 'err'); return; }
      if (kind === 'mapping' && mode === 'chain') { S.toast('This plan only runs chains (chain mode).', 'err'); return; }
      if (kind === 'wait' && mode !== 'mixed') { S.toast('Wait steps need a mixed plan.', 'err'); return; }
      const body = kind === 'wait' ? { wait_seconds: opts.seconds }
        : kind === 'chain' ? { chain_id: docId }
        : { mapping_id: docId, ...(opts.pairs && opts.pairs.length ? { entity_mapping_ids: opts.pairs } : {}) };
      if (opts.position) body.position = opts.position;
      try {
        await S.api(`/api/plans/${this.id}/steps/`, { method: 'POST', body });
        this.forgetPositions();       // the new block joins the automatic layout in its right place
        await this.reload();
      } catch (e) { S.fail(e); }
    }

    // Dropped from the Design tab / explorer: onto a step → insert before it; elsewhere → append.
    onDrop(mime, payload, x, y) {
      if (!payload || !['mapping', 'chain', 'wait', 'function'].includes(payload.kind)) return;
      const before = this.stepAt(x, y);
      const position = before ? before.order : null;
      if (payload.kind === 'wait') this.waitDialog(null, position);
      else if (payload.kind === 'function') this.functionDialog(null, payload.preset, position);
      else this.addStep(payload.kind, payload.id, { pairs: payload.pairs, position });
    }

    fileEntities() {
      if (!this.fileEntityList) {
        this.fileEntityList = S.apiList('/api/entities/').then(list => list.filter(e => e.has_file)).catch(() => []);
        this.fileEntityList.then(list => { this.fileNames = Object.fromEntries(list.map(e => [e.id, e.name])); });
      }
      return this.fileEntityList;
    }

    // Add (step = null) or edit a function block. The form is the same one a chain uses (step_kinds.js); the
    // "earlier steps" it offers are the blocks that come before this one IN THE PLAN, since that is the order
    // they run in — and a request block also asks which connection it calls.
    functionDialog(step, preset = {}, position) {
      const kind = step ? step.function.kind : preset.kind;
      const inline = this.steps.filter(x => isFn(x));
      const earlier = inline.filter(x => step ? x.order < step.order : (position ? x.order < position : true)).map(x => ({ ...x.function, order: x.order }));
      const form = K.form({
        kind, step: step ? { ...step.function, order: step.order } : null, preset, steps: earlier,
        connections: S.tree.connections, fileEntities: () => this.fileEntities(), nextIndex: inline.length + 1,
      });
      S.dialog({
        title: step ? `Edit block — ${step.function.name}` : `Add a function — ${K.meta[kind].label}`, size: 'modal-lg',
        body: h('div', {}, form.nodes),
        actions: [{ label: 'Cancel' }, {
          label: step ? 'Save' : 'Add to run', primary: true, onClick: async d => {
            let payload;
            try { payload = form.read(); } catch (e) { d.error(e.message); return false; }
            if (step) { await S.api(`/api/plan-steps/${step.id}/`, { method: 'PATCH', body: { function: payload } }); await this.reload(); }
            else {
              await S.api(`/api/plans/${this.id}/steps/`, { method: 'POST', body: { function: payload, ...(position ? { position } : {}) } });
              this.forgetPositions();
              await this.reload();
            }
          },
        }],
      });
    }

    // Add (step = null) or change a pause.
    waitDialog(step, position) {
      const input = h('input', { type: 'number', min: '0', max: '3600', step: '1', class: 'form-control', id: 'wd_secs', value: step ? step.wait_seconds : 30 });
      const chip = n => h('button', { type: 'button', class: 'st-chip', text: fmtSeconds(n), onclick: () => { input.value = n; } });
      S.dialog({
        title: step ? 'Change wait' : 'Add a wait',
        body: h('div', {},
          h('p', { class: 'text-dim small', text: 'The plan pauses this long before starting the next step — e.g. to let a system finish processing, or to stay under a rate limit.' }),
          h('label', { class: 'form-label', for: 'wd_secs', text: 'Seconds to wait' }), input,
          h('div', { class: 'st-chips mt-2' }, h('span', { class: 'text-dim small', text: 'Quick:' }), [5, 30, 60, 300, 900].map(chip)),
          h('div', { class: 'form-text', text: 'Up to 3600 seconds (an hour).' })),
        actions: [{ label: 'Cancel' }, {
          label: step ? 'Save' : 'Add wait', primary: true, onClick: async d => {
            const seconds = parseFloat(input.value);
            if (!(seconds >= 0 && seconds <= 3600)) { d.error('Enter a number of seconds between 0 and 3600.'); return false; }
            if (step) { await S.api(`/api/plan-steps/${step.id}/`, { method: 'PATCH', body: { wait_seconds: seconds } }); await this.reload(); }
            else await this.addStep('wait', null, { seconds, position });
          },
        }],
      });
    }

    // Run only some of a mapping's entity pairs in this step.
    pairsDialog(step) {
      const doc = S.treeItem('mapping', step.mapping);
      const pairs = doc ? doc.pair_list : [];
      const chosen = new Set((step.entity_mapping_ids || []).length ? step.entity_mapping_ids : pairs.map(p => p.id));
      const boxes = pairs.map(p => h('label', { class: 'd-flex gap-2 align-items-center' },
        h('input', { type: 'checkbox', class: 'form-check-input mt-0', value: p.id, checked: chosen.has(p.id) }),
        h('span', { class: 'mono', text: `${p.source} → ${p.target}` })));
      S.dialog({
        title: `Entity pairs — ${this.stepName(step)}`,
        body: h('div', {},
          h('p', { class: 'text-dim small', text: 'Tick the pairs this step should run. Unticked pairs are skipped in this plan (the mapping itself is unchanged).' }),
          h('div', { class: 'st-checklist' }, boxes.length ? boxes : h('span', { class: 'text-dim small', text: 'This mapping has no entity pairs.' }))),
        actions: [{ label: 'Cancel' }, {
          label: 'Save', primary: true, onClick: async d => {
            const ids = [...d.el.querySelectorAll('.st-checklist input:checked')].map(i => Number(i.value));
            if (!ids.length) { d.error('Tick at least one pair.'); return false; }
            await S.api(`/api/plan-steps/${step.id}/`, { method: 'PATCH', body: { entity_mapping_ids: ids } });
            await this.reload();
          },
        }],
      });
    }

    rateDialog(step) {
      S.dialog({
        title: `Rate limit — ${this.stepName(step)}`,
        body: h('div', {},
          h('label', { class: 'form-label' }, 'Requests/minute ', h('span', { class: 'text-dim', text: '(optional)' })),
          h('input', { type: 'number', min: '0.1', step: '1', class: 'form-control', id: 'rl_rpm', placeholder: 'plan default', value: step.rate_limit_per_second ? Math.round(step.rate_limit_per_second * 60 * 100) / 100 : '' }),
          h('div', { class: 'form-text', text: "Blank falls back to the plan's default rate limit." })),
        actions: [{ label: 'Cancel' }, {
          label: 'Save', primary: true, onClick: async d => {
            const v = d.$('#rl_rpm').value;
            await S.api(`/api/plan-steps/${step.id}/`, { method: 'PATCH', body: { rate_limit_per_second: v ? parseFloat(v) / 60 : null } });
            await this.reload();
          },
        }],
      });
    }

    // ── Design palette ───────────────────────────────────────────────────
    renderPalette(box) {
      const mode = this.plan ? this.plan.execution_mode : 'mixed';
      const mime = 'application/x-ante-doc';
      if (this.plan && !this.editable) {
        box.append(h('div', { class: 'st-pal-hint', text: `This plan is ${this.plan.status} — its steps are locked until it has finished.` }));
        return;
      }
      if (mode !== 'chain') {
        box.append(h('div', { class: 'st-pal-group', text: 'Mappings' }));
        if (!S.tree.mappings.length) box.append(h('div', { class: 'st-pal-hint', text: 'No mappings yet.' }));
        S.tree.mappings.forEach(m => {
          const pairsBox = h('div', { class: 'st-pal-pairs', hidden: true },
            m.pair_list.map(pr => S.paletteItem('bi-columns-gap', `${pr.source} → ${pr.target}`, null, mime, { kind: 'mapping', id: m.id, pairs: [pr.id] }, () => this.addStep('mapping', m.id, { pairs: [pr.id] }))));
          const row = S.paletteItem(S.icons.mapping, m.name, m.pair_list.length > 1 ? `${m.pair_list.length} pairs` : null, mime, { kind: 'mapping', id: m.id }, () => this.addStep('mapping', m.id));
          if (m.pair_list.length > 1) {
            const caret = h('button', { type: 'button', class: 'st-pal-caret', title: 'Show its entity pairs — drag one to run just that pair', 'aria-expanded': 'false' }, h('i', { class: 'bi-chevron-right' }));
            caret.addEventListener('click', e => {
              e.stopPropagation();
              pairsBox.hidden = !pairsBox.hidden;
              caret.setAttribute('aria-expanded', String(!pairsBox.hidden));
              caret.firstChild.className = pairsBox.hidden ? 'bi-chevron-right' : 'bi-chevron-down';
            });
            row.prepend(caret);
          }
          box.append(row, pairsBox);
        });
      }
      if (mode !== 'simple') {
        box.append(h('div', { class: 'st-pal-group', text: 'Chains' }));
        if (!S.tree.chains.length) box.append(h('div', { class: 'st-pal-hint', text: 'No chains yet.' }));
        S.tree.chains.forEach(c => box.append(S.paletteItem(S.icons.chain, c.name, null, mime, { kind: 'chain', id: c.id }, () => this.addStep('chain', c.id))));
      }
      if (mode === 'mixed') {
        const fn = (icon, label, sub, preset) => S.paletteItem(icon, label, sub, mime, { kind: 'function', preset }, () => this.functionDialog(null, preset));
        const kindIcon = k => h('i', { class: K.meta[k].icon });
        box.append(
          h('div', { class: 'st-pal-group', text: 'Requests' }),
          ...['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map(m => fn(K.verbBadge(m), '', 'request', { kind: 'http', method: m })),
          h('div', { class: 'st-pal-group', text: 'Flow' }),
          S.paletteItem('bi-stopwatch', 'Wait', 'pause', mime, { kind: 'wait' }, () => this.waitDialog(null)),
          fn(h('i', { class: 'bi-arrow-repeat' }), 'Async job', 'start & poll', { kind: 'http', method: 'POST', async: true }),
          fn(kindIcon('next'), 'Next page', 'pagination', { kind: 'next' }),
          h('div', { class: 'st-pal-group', text: 'Data' }),
          fn(kindIcon('find'), 'Find in list', 'lookup', { kind: 'find' }),
          fn(kindIcon('file'), 'File preview', 'csv / xlsx', { kind: 'file' }),
          h('div', { class: 'st-pal-group', text: 'Checks' }),
          fn(kindIcon('check'), 'Check header', 'assert', { kind: 'check' }));
      }
      box.append(h('div', { class: 'st-pal-hint', text: 'Drag a block onto the canvas — drop it on a step to insert it before that step, or on empty space to add it at the end. Click a block to append it. Expand a mapping (›) to run just one of its entity pairs. Function blocks share data: a Find can search what an earlier request returned, and a failed block stops the run.' }));
    }

    helpSections() {
      const locked = this.plan && !this.editable;
      return [{
        title: 'Build this run', items: locked ? [[['Locked'], `the plan is ${this.plan.status}; you can rearrange it once it has finished`]] : [
          [['Drag'], 'a mapping, one of its entity pairs, a chain or a Wait from the Design tab onto the canvas'],
          [['Drop on a step'], 'to insert before it — on empty space to add at the end'],
          [['Drag a step'], 'onto another step to reorder: it takes that place'],
          [['Functions'], 'requests, async jobs, next page, find in list, file preview and header checks — they pass data to one another, in run order'],
          [['Double-click'], 'a step to open its mapping or chain (a Wait or function: edit it)'],
          [['Hover'], 'a step for edit, entity pairs, rate limit, remove and delete'],
          [['Execute'], 'runs the steps one after another — Options adds scheduling'],
          [['Kill'], 'stops a running plan: it halts before the next step (and mid-wait); the current step is told to stop too'],
        ],
      }];
    }

    // ── Properties / delete ──────────────────────────────────────────────
    properties() {
      const p = this.plan;
      const locked = !this.editable;
      S.dialog({
        title: 'Plan properties',
        body: h('div', {},
          locked ? h('div', { class: 'alert alert-secondary py-2 small', text: `This plan is ${p.status} — it can be edited again once it has finished.` }) : null,
          h('div', { class: 'mb-3' }, h('label', { class: 'form-label', text: 'Name' }), h('input', { class: 'form-control', id: 'pp_name', value: p.name, disabled: locked })),
          h('div', { class: 'mb-3' }, h('label', { class: 'form-label', text: 'Description' }), h('textarea', { class: 'form-control', id: 'pp_desc', rows: '2', disabled: locked, text: p.description || '' })),
          h('div', {}, h('label', { class: 'form-label' }, 'Default rate limit ', h('span', { class: 'text-dim', text: '(optional, requests/minute)' })),
            h('input', { type: 'number', min: '0.1', step: '1', class: 'form-control', id: 'pp_rate', disabled: locked, placeholder: 'unthrottled', value: p.rate_limit_per_second ? Math.round(p.rate_limit_per_second * 60 * 100) / 100 : '' }),
            h('div', { class: 'form-text', text: "Applies to every mapping step that doesn't set its own." }))),
        actions: locked ? [{ label: 'Close' }] : [{ label: 'Cancel' }, {
          label: 'Save', primary: true, onClick: async d => {
            const name = d.$('#pp_name').value.trim();
            if (!name) { d.error('Name is required.'); return false; }
            const rate = d.$('#pp_rate').value;
            await S.api(`/api/plans/${this.id}/`, { method: 'PATCH', body: { name, description: d.$('#pp_desc').value, rate_limit_per_second: rate ? parseFloat(rate) / 60 : null } });
            await this.reload();
            this.setTitle(this.plan.name);
          },
        }],
      });
    }

    remove() {
      if (this.executing) { S.toast("Can't delete a plan while it's executing.", 'err'); return; }
      return S.deleteDoc(this.kind, this.id);
    }

    destroy() { super.destroy(); this.stopPolling(); }

    // ── Running ──────────────────────────────────────────────────────────
    // The Kill button: stop an executing plan (it halts before the next step, and mid-wait; the mapping run /
    // chain / function block in progress is told to stop too) — or cancel a scheduled one.
    canKill() { return !!this.plan && ['executing', 'scheduled'].includes(this.plan.status); }
    killMessage() {
      return this.plan.status === 'scheduled'
        ? `Cancel the scheduled run of "${this.plan.name}"?`
        : `Kill "${this.plan.name}"? It stops before the next step and the current one is told to stop. Records already written stay written; a request already on the wire can't be recalled.`;
    }
    async kill() {
      this.plan = await S.api(`/api/plans/${this.id}/cancel/`, { method: 'POST' });
      this.rerender();
      this.renderResults();
      this.syncPolling();
      this.changed();
      S.refreshTree();
      S.toast(this.plan.status === 'executing' ? 'Stopping — waiting for the current step to wind down…' : `Plan ${this.plan.status}.`, 'ok');
    }

    async run() { await this.execute({}); }
    runOptions() {
      S.runOptionsDialog({
        title: 'Execute plan', fields: ['schedule', 'rate'],
        rateRpm: this.plan.rate_limit_per_second ? Math.round(this.plan.rate_limit_per_second * 60 * 100) / 100 : null,
        onStart: opts => this.execute(opts),
      });
    }

    async execute({ scheduled_at, rate_limit_per_second }) {
      const body = {};
      if (scheduled_at) body.scheduled_at = scheduled_at;
      if (rate_limit_per_second) body.rate_limit_per_second = rate_limit_per_second;
      this.plan = await S.api(`/api/plans/${this.id}/execute/`, { method: 'POST', body });
      this.rerender();
      this.renderResults();
      this.syncPolling();
      this.changed();
      S.refreshTree();
      S.toast(this.plan.status === 'scheduled' ? `Plan scheduled for ${S.fmtDateTime(this.plan.scheduled_at)}.` : 'Plan executing…', 'ok');
    }

    // Poll while executing (fast) or scheduled (slow — it flips to executing on its own).
    syncPolling() {
      this.stopPolling();
      const st = this.plan && this.plan.status;
      this.running = st === 'executing';
      if (st !== 'executing' && st !== 'scheduled') return;
      const tick = async () => {
        const before = this.plan.status;
        try { this.plan = await S.api(`/api/plans/${this.id}/`); } catch (e) { if (e.status === 404) { this.timer = null; return; } /* else transient */ }
        if (this.plan.status !== before) {
          // Structure/status change (e.g. scheduled → executing, or finished): full redraw.
          this.rerender();
          if (before === 'executing') {
            S.toast(`Plan ${this.plan.status}.`, this.plan.status === 'completed' ? 'ok' : this.plan.status === 'cancelled' ? 'ok' : 'err');
            S.refreshTree();
          }
        } else {
          this.decorateNodes();
        }
        this.renderResults();
        this.running = this.plan.status === 'executing';
        this.changed();
        if (this.plan.status === 'executing' || this.plan.status === 'scheduled') this.timer = setTimeout(tick, this.plan.status === 'executing' ? 1200 : 5000);
        else this.timer = null;
      };
      this.timer = setTimeout(tick, st === 'executing' ? 800 : 5000);
    }
    stopPolling() { if (this.timer) { clearTimeout(this.timer); this.timer = null; } }

    // ── Results panel ────────────────────────────────────────────────────
    buildResults() {
      this.tabs = S.makeTabs([{ id: 'steps', label: 'Steps' }]);
      this.resultsEl.replaceChildren(this.tabs.root);
    }

    renderResults() {
      const page = this.tabs.page('steps');
      const p = this.plan;
      if (!p) return;
      // Records moved by the mapping steps so far (chain steps have no record counts).
      const totals = { runs: 0, read: 0, written: 0, failed: 0 };
      this.steps.forEach(s => { if (!noDoc(s) && !s.chain && s.run) { totals.runs++; totals.read += s.run.records_read; totals.written += s.run.records_written; totals.failed += s.run.records_failed; } });
      const summary = h('div', { class: 'st-summary' },
        S.chip(p.status), h('span', { class: 'text-dim', text: p.execution_mode === 'mixed' ? 'mappings and chains, sequential' : `${p.execution_mode} mode, sequential` }),
        p.scheduled_at && p.status === 'scheduled' ? h('span', {}, 'runs ', h('b', { text: S.fmtDateTime(p.scheduled_at) })) : null,
        p.rate_limit_per_second ? h('span', { class: 'text-dim', text: `default ≤ ${Math.round(p.rate_limit_per_second * 60 * 100) / 100}/min` }) : null,
        totals.runs ? h('span', { class: 'st-meter-cell', style: 'flex:1 1 140px;max-width:260px' }, S.viz.meter({ good: totals.written, bad: totals.failed, total: totals.read })) : null,
        totals.runs ? h('span', {}, h('b', { text: S.viz.compact(totals.written) }), ' written · ', h('b', { text: S.viz.compact(totals.failed) }), ' failed ', h('span', { class: 'text-dim', text: `of ${S.viz.compact(totals.read)} read` })) : null);

      const rows = this.steps.flatMap(s => {
        const wait = isWait(s);
        const fn = isFn(s);
        const isChain = !!s.chain;
        const st = stepState(s, this.plan && this.plan.status);
        const run = wait || fn ? null : isChain ? s.chain_run : s.run;
        const icon = wait ? 'bi-stopwatch' : isChain ? S.icons.chain : S.icons.mapping;
        const label = wait ? 'wait' : isChain ? 'chain' : 'mapping';
        const subset = !wait && !fn && !isChain && (s.entity_mapping_ids || []).length ? h('span', { class: 'st-tag warn ms-2', text: `${s.entity_mapping_ids.length} of ${s.pairs_total} pairs` }) : null;
        const stateCell = h('td', {}, st.status ? S.chip(st.status === 'failed' ? 'failed' : st.status, st.label) : h('span', { class: 'text-dim', text: st.label }));
        if (fn) {
          // A function block: what it did, expandable to the full request/response/detail like a chain step.
          const detail = h('tr', { class: 'd-none' }, h('td', { colspan: '8' }, s.result ? K.resultDetail(s.result) : h('span', { class: 'text-dim small', text: 'Not run yet.' })));
          return [h('tr', { class: 'st-clickable', onclick: () => detail.classList.toggle('d-none') },
            h('td', { class: 'mono', text: s.order }),
            h('td', {}, K.badge(s.function.kind, s.function.method), h('span', { class: 'ms-2', text: s.function.name }),
              s.result ? h('span', { class: 'text-dim ms-2 mono small', text: K.resultSummary(s.result) }) : null),
            stateCell, h('td'), h('td'), h('td'), h('td'), h('td')), detail];
        }
        return [h('tr', { class: wait && !this.editable ? '' : 'st-clickable', onclick: () => this.openStep(s) },
          h('td', { class: 'mono', text: s.order }),
          h('td', {}, h('i', { class: `${icon} me-2 text-dim` }), this.stepName(s), h('span', { class: 'text-dim ms-2', text: label }), subset),
          stateCell,
          h('td', { class: 'num', text: !wait && !isChain && s.run ? s.run.records_read : '' }),
          h('td', { class: 'num', text: !wait && !isChain && s.run ? s.run.records_written : '' }),
          h('td', { class: 'num', text: !wait && !isChain && s.run ? s.run.records_failed : '' }),
          h('td', { class: 'st-meter-cell' }, !wait && !isChain && s.run ? S.viz.meter({ good: s.run.records_written, bad: s.run.records_failed, total: s.run.records_read }) : null),
          h('td', { class: 'st-actions', onclick: e => e.stopPropagation() }, run
            ? h('a', { class: 'st-mini', target: '_blank', rel: 'noopener', text: `Run #${run.id}`, href: isChain ? `/chains/${s.chain}/runs/${run.id}/` : `/jobs/runs/${run.id}/` })
            : null))];
      });
      page.replaceChildren(summary, rows.length
        ? h('table', { class: 'st-table' }, h('thead', {}, h('tr', {}, ['#', 'Step', 'Status', 'Read', 'Written', 'Failed', 'Progress', ''].map((t, i) => h('th', { class: i >= 3 && i <= 5 ? 'num' : '', text: t })))), h('tbody', {}, rows))
        : h('div', { class: 'st-empty', text: 'No steps yet.' }));
      this.tabs.setNote(`${this.steps.length} step(s)`);
    }
  }

  S.editorClasses.plan = PlanEditor;
})();
