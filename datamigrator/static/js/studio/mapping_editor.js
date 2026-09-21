/* MappingEditor — the field-wiring canvas (what /mappings/<id>/?tab=canvas
   was) plus everything that used to be the Raw / Runs / Connections tabs,
   folded into the results panel underneath:

     canvas          entity boxes, drag a field's teal dot onto an amber dot
     Field mappings  the old Raw tab: per-pair operation + every wire's transform
     Runs            this mapping's run history (+ retry)
     Step metrics    per-entity-pair progress of the selected run
     Logging         the selected run's log

   Runs are live: while any run of this mapping is pending/running we poll the
   slim run list once a second and re-fetch the selected run in full. */
(function () {
  'use strict';
  const S = window.Studio;
  const h = S.h;

  const DEFAULT_POS = 40;               // Entity.canvas_x/y default = "never placed"
  const COL_X = { source: 40, target: 520 };
  const FIELD_TYPES = ['string', 'number', 'integer', 'boolean', 'object', 'array', 'date'];
  const METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'];
  const STATUS_RANK = { failed: 4, running: 3, pending: 2, success: 1 };

  const uid = (mappingId, side, kind, id) => `m${mappingId}:${side}:${kind}:${id}`;

  function describeRule(rule) {
    const op = rule.op;
    if (op === 'uppercase') return 'Uppercase';
    if (op === 'lowercase') return 'Lowercase';
    if (op === 'trim') return 'Trim whitespace';
    if (op === 'map') {
      const cases = (rule.cases || []).map(c => `"${c.from}" → "${c.to}"`).join(', ');
      return `Map values: ${cases || '(none set)'}${rule.default_mode === 'value' ? `, else "${rule.default_value}"` : ', else keep original'}`;
    }
    if (op === 'default_if_empty') return `Default to "${rule.value}" if empty`;
    return op || 'Unknown rule';
  }
  const hasTransform = fm => !!((fm.transform_rules && fm.transform_rules.length) || fm.transform);
  const transformSummary = fm => [...(fm.transform_rules || []).map(describeRule), fm.transform ? `Custom: ${fm.transform}` : null].filter(Boolean).join('\nthen ') || 'No transformation';

  class MappingEditor extends S.CanvasEditor {
    static kind = 'mapping';

    constructor(id) {
      super(id);
      this.runLabel = 'Run migration';
      this.hasRunOptions = true;
      this.mapping = null;
      this.pairs = [];
      this.runs = [];
      this.selectedRun = null;        // full run (with logs + step_statuses)
      this.timer = null;
      this.lastSample = null;
      this.rate = 0;
      this.connFm = new Map();        // jsPlumb connection -> field mapping
      this.samples = {};              // run id -> [{ w, f }] one point per poll, for the live sparkline
      this.preview = null;            // last /preview/ response
      this.previewStale = true;
      this.previewLoading = false;
      this.previewPair = null;        // entity_mapping id shown in the Data tab
      this.previewLimit = 10;
      this.previewView = 'table';     // or 'json'
      this.acceptMimes = [];
      this.surface.classList.add('kind-mapping');
      this.buildResults();
    }

    // ── Data ─────────────────────────────────────────────────────────────
    async load() {
      const [mapping, pairs] = await Promise.all([
        S.api(`/api/mappings/${this.id}/`),
        S.apiList(`/api/entity-mappings/?mapping=${this.id}`),
      ]);
      this.mapping = mapping;
      this.pairs = pairs;
      this.setTitle(mapping.name);
      this.dataReady = true;
      this.rerender();
      this.renderMapTab();
      await this.loadRuns();
      if (!this.selectedRun && this.runs.length) await this.selectRun(this.runs[0].id, { keepTab: true });
      if (this.isActive()) this.startPollingIfNeeded();
      this.changed();
      if (S.active === this) S.renderPalette();   // the palette depends on data that just arrived
    }

    isActive() { return this.runs.some(r => r.status === 'running' || (r.status === 'pending' && !r.scheduled_at)); }

    async reloadStructure() {
      this.pairs = await S.apiList(`/api/entity-mappings/?mapping=${this.id}`);
      this.rerender();
      this.renderMapTab();
      this.renderMetrics();
      S.refreshTree();
    }

    statusText() {
      if (!this.mapping) return '';
      const dest = (this.mapping.destination_connections_detail || []).map(d => d.name).join(', ') || 'no destinations';
      const run = this.selectedRun;
      const live = run && (run.status === 'running') ? `  ·  run #${run.id} ${run.requests_made} req · ${this.rate.toFixed(1)} rq/s` : '';
      return `${this.mapping.source_connection_name} → ${dest}${live}`;
    }

    canRun() { return !this.running && !!this.mapping; }

    // ── Canvas ───────────────────────────────────────────────────────────
    entityNode(side, entityId) { return this.surface.querySelector(`[data-node="${side}:${entityId}"]`); }

    render() {
      const jsp = this.jsp;
      this.connFm.clear();
      this.fieldEntity = { source: {}, target: {} };
      this.pairByEntities = {};

      const uniq = list => [...new Map(list.map(e => [e.id, e])).values()];
      const sources = uniq(this.pairs.map(p => p.source_entity_detail));
      const targets = uniq(this.pairs.map(p => p.target_entity_detail));
      this.pairs.forEach(p => { this.pairByEntities[`${p.source_entity}-${p.target_entity}`] = p; });

      jsp.bind('connection', (info, ev) => { if (ev) this.onWireDrawn(info); });
      jsp.bind('click', (conn, ev) => this.onWireClick(conn, ev));

      jsp.batch(() => {
        [['source', sources], ['target', targets]].forEach(([side, list]) => {
          let nextY = 40;
          list.forEach(ent => {
            const placed = ent.canvas_x !== DEFAULT_POS || ent.canvas_y !== DEFAULT_POS;
            const x = placed ? ent.canvas_x : COL_X[side];
            const y = placed ? ent.canvas_y : nextY;
            nextY += 56 + ent.fields.length * 27 + 32;   // header + rows + gap
            this.addEntityBox(ent, side, x, y);
          });
        });
        this.pairs.forEach(p => p.field_mappings.forEach(fm => this.addWire(fm)));
      });

      this.emptyHint(this.pairs.length ? '' : (
        'No entity pairs yet.<br>Use <b>Design → Entity pair</b> (or the button in the palette) to place a source and a target entity, then wire their fields.'));
      this.banner(
        h('i', { class: 'bi-diagram-2' }),
        h('span', {}, h('span', { class: 'mono', text: this.mapping.source_connection_name }), ' → ',
          (this.mapping.destination_connections_detail || []).map((d, i) => [i ? ', ' : '', h('span', { class: 'mono', text: d.name })]).flat()),
        h('span', { text: '· drag teal → amber dots to map · click a wire for options' }));
      this.applyRunStatuses();
    }

    addEntityBox(ent, side, x, y) {
      const jsp = this.jsp;
      const node = h('div', {
        class: `st-node st-entity side-${side}`, dataset: { node: `${side}:${ent.id}` },
        oncontextmenu: e => {
          e.preventDefault();
          S.menu(e.clientX, e.clientY, [
            { head: `${ent.name} · ${ent.connection_name}` },
            { label: 'Edit entity and fields…', icon: 'bi-pencil', onClick: () => this.editEntity(ent.id) },
            { label: 'Edit mapping…', icon: 'bi-diagram-2', onClick: () => this.properties() },
          ]);
        },
      },
        h('div', { class: 'st-entity-head' },
          h('span', { class: 'mono', text: ent.name }),
          h('span', { class: 'd-flex align-items-center gap-2' },
            h('small', { text: ent.connection_name }),
            h('button', { title: 'Edit entity and its fields', onclick: () => this.editEntity(ent.id) }, h('i', { class: 'bi-pencil' })))),
        ent.fields.map(f => {
          const dot = h('div', { class: `st-dotp ${side === 'target' ? 'target' : ''}`, dataset: { field: f.id } });
          const label = h('span', {}, h('span', { class: 'fname', text: f.name }), f.required ? h('span', { class: 'req', text: '*' }) : null, ' ', h('span', { class: 'ftype', text: f.field_type }));
          return h('div', { class: 'st-field' }, side === 'target' ? [dot, label] : [label, dot]);
        }));
      this.placeNode(node, x, y, {
        onMoved: (nx, ny) => S.api(`/api/entities/${ent.id}/position/`, { method: 'PATCH', body: { canvas_x: Math.round(nx), canvas_y: Math.round(ny) } }).catch(() => {}),
      });

      ent.fields.forEach(f => {
        const dot = node.querySelector(`.st-dotp[data-field="${f.id}"]`);
        this.fieldEntity[side][f.id] = ent.id;
        jsp.addEndpoint(dot, {
          uuid: uid(this.id, side, 'f', f.id),
          anchor: 'Center',
          isSource: side === 'source',
          isTarget: side === 'target',
          maxConnections: -1,               // a source field can fan out to several targets
          endpoint: ['Dot', { radius: 5 }],
          paintStyle: { fill: side === 'source' ? '#2f9c94' : '#d98a2b' },
          connectorClass: 'st-map-wire',
        });
      });
    }

    addWire(fm) {
      const conn = this.jsp.connect({
        uuids: [uid(this.id, 'source', 'f', fm.source_field), uid(this.id, 'target', 'f', fm.target_field)],
        overlays: hasTransform(fm) ? [['Label', { label: 'ƒ', location: 0.5, cssClass: 'st-fx', id: 'fx' }]] : [],
      });
      if (!conn) return;
      this.connFm.set(conn, fm);
      if (hasTransform(fm)) { const o = conn.getOverlay('fx'); if (o && o.canvas) o.canvas.title = transformSummary(fm); }
    }

    fieldIdsOf(conn) {
      const [s, t] = conn.endpoints.map(e => Number(e.getUuid().split(':').pop()));
      return { s, t };
    }

    async onWireDrawn(info) {
      const { s, t } = this.fieldIdsOf(info.connection);
      const pair = this.pairByEntities[`${this.fieldEntity.source[s]}-${this.fieldEntity.target[t]}`];
      if (!pair) {
        this.jsp.deleteConnection(info.connection);
        S.toast('Those two fields belong to entities that are not paired — add that entity pair first.', 'err');
        return;
      }
      try {
        const fm = await S.api('/api/field-mappings/', { method: 'POST', body: { entity_mapping: pair.id, source_field: s, target_field: t } });
        pair.field_mappings.push(fm);
        this.connFm.set(info.connection, fm);
        this.renderMapTab();
      } catch (e) {
        this.jsp.deleteConnection(info.connection);
        S.fail(e);
      }
    }

    onWireClick(conn, ev) {
      const fm = this.connFm.get(conn);
      if (!fm) return;
      S.menu(ev.clientX, ev.clientY, [
        { head: `${fm.source_field_name} → ${fm.target_field_name}` },
        { label: 'Edit transform…', icon: 'bi-magic', onClick: () => this.editTransform(fm) },
        { label: 'Remove mapping', icon: 'bi-trash', danger: true, onClick: () => this.removeWire(fm) },
      ]);
    }

    async removeWire(fm) {
      if (!(await confirmModal('Remove this field mapping?'))) return;
      try {
        await S.api(`/api/field-mappings/${fm.id}/`, { method: 'DELETE' });
        this.pairs.forEach(p => { p.field_mappings = p.field_mappings.filter(x => x.id !== fm.id); });
        this.rerender();
        this.renderMapTab();
      } catch (e) { S.fail(e); }
    }

    helpSections() {
      return [{ title: 'This mapping', items: [
        [['Drag'], 'from a teal dot (a source field) to an amber dot (a target field) to map it — one source can feed several targets'],
        [['Click'], 'a wire to edit its transform or remove it'],
        [['✎'], 'on an entity header to edit the entity and its fields'],
        [['Design'], 'tab → Entity pair adds another source and destination'],
        [['Run'], 'starts a migration — boxes turn green or red and show what flowed through'],
      ] }];
    }

    // ── Design palette ───────────────────────────────────────────────────
    renderPalette(box) {
      box.append(
        h('div', { class: 'st-pal-group', text: 'Mapping' }),
        S.paletteItem('bi-columns-gap', 'Entity pair', 'source → target', null, null, () => this.addPairDialog()),
        h('div', { class: 'st-pal-hint', text: 'Places one source entity and one destination entity on the canvas, so their fields can be wired together. A source entity can be paired with several destinations.' }),
        h('div', { class: 'st-pal-group', text: 'Manage entities' }),
        ...[{ id: this.mapping && this.mapping.source_connection, name: this.mapping && this.mapping.source_connection_name }]
          .concat(((this.mapping && this.mapping.destination_connections_detail) || []))
          .filter(c => c.id)
          .map(c => h('a', { class: 'st-pal-item', style: 'cursor:pointer;text-decoration:none;color:inherit', href: `/connections/${c.id}/entities/`, target: '_blank', rel: 'noopener' },
            h('i', { class: 'bi-database' }), c.name, h('small', {}, h('i', { class: 'bi-box-arrow-up-right' })))),
      );
    }

    async addPairDialog() {
      const m = this.mapping;
      const dests = m.destination_connections_detail || [];
      if (!dests.length) { S.toast('This mapping has no destination connections yet — add one under Properties.', 'err'); return; }
      let sourceEntities = [];
      try { sourceEntities = await S.apiList(`/api/entities/?connection=${m.source_connection}`); } catch (e) { S.fail(e); return; }
      if (!sourceEntities.length) { S.toast(`No entities discovered on ${m.source_connection_name} yet — discover them from its connection page.`, 'err'); return; }

      const opt = (v, t) => `<option value="${v}">${S.esc(t)}</option>`;
      const dlg = S.dialog({
        title: 'Add entity pair',
        body: h('div', {},
          h('div', { class: 'mb-3' }, h('label', { class: 'form-label' }, 'Source entity ', h('span', { class: 'text-dim', text: `(${m.source_connection_name})` })),
            h('select', { class: 'form-select', id: 'ap_src', html: sourceEntities.map(e => opt(e.id, e.name)).join('') })),
          h('div', { class: 'mb-3' }, h('label', { class: 'form-label', text: 'Destination connection' }),
            h('select', { class: 'form-select', id: 'ap_conn', html: dests.map(d => opt(d.id, d.name)).join('') })),
          h('div', { class: 'mb-3' }, h('label', { class: 'form-label', text: 'Destination entity' }), h('select', { class: 'form-select', id: 'ap_tgt', disabled: true })),
          h('div', {}, h('label', { class: 'form-label', text: 'Operation' }),
            h('select', { class: 'form-select', id: 'ap_method', html: METHODS.map(x => `<option ${x === 'POST' ? 'selected' : ''}>${x}</option>`).join('') }),
            h('div', { class: 'form-text', text: "HTTP verb used to write each record to the destination entity's endpoint." }))),
        actions: [{ label: 'Cancel' }, {
          label: 'Add to canvas', primary: true, onClick: async d => {
            const tgt = d.$('#ap_tgt').value;
            if (!tgt) { d.error('Pick a destination entity.'); return false; }
            await S.api('/api/entity-mappings/', { method: 'POST', body: { mapping: this.id, source_entity: d.$('#ap_src').value, target_entity: tgt, write_method: d.$('#ap_method').value } });
            await this.reloadStructure();
          },
        }],
      });
      const loadTargets = async () => {
        const sel = dlg.$('#ap_tgt');
        sel.disabled = true;
        sel.innerHTML = '<option value="">Loading…</option>';
        try {
          const ents = await S.apiList(`/api/entities/?connection=${dlg.$('#ap_conn').value}`);
          sel.innerHTML = ents.length ? ents.map(e => opt(e.id, e.name)).join('') : '<option value="">No entities discovered for this connection</option>';
          sel.disabled = !ents.length;
        } catch (e) { dlg.error(e.message); }
      };
      dlg.$('#ap_conn').addEventListener('change', loadTargets);
      loadTargets();
    }

    // ── Entity editor (name / endpoint / fields) ─────────────────────────
    async editEntity(entityId) {
      let ent;
      try { ent = await S.api(`/api/entities/${entityId}/`); } catch (e) { S.fail(e); return; }
      const removed = [];
      const rows = h('div', {});
      const addRow = f => {
        const row = h('div', { class: 'st-dlg-row', dataset: { fieldId: f.id || '' } },
          h('input', { class: 'form-control form-control-sm', placeholder: 'Field name', value: f.name || '' }),
          h('select', { class: 'form-select form-select-sm', style: 'max-width:130px', html: FIELD_TYPES.map(t => `<option ${t === (f.field_type || 'string') ? 'selected' : ''}>${t}</option>`).join('') }),
          h('label', { class: 'd-flex gap-1 align-items-center small text-nowrap' }, h('input', { type: 'checkbox', class: 'form-check-input mt-0', checked: !!f.required }), 'required'),
          h('button', { type: 'button', class: 'btn btn-sm btn-outline-danger', html: '&times;', onclick: () => { if (f.id) removed.push(f.id); row.remove(); } }));
        rows.append(row);
      };
      ent.fields.forEach(addRow);

      S.dialog({
        title: `Edit entity — ${ent.name}`, size: 'modal-lg',
        body: h('div', {},
          h('div', { class: 'mb-3' }, h('label', { class: 'form-label', text: 'Name' }), h('input', { class: 'form-control', id: 'ee_name', value: ent.name })),
          h('div', { class: 'mb-3' }, h('label', { class: 'form-label', text: 'Endpoint path' }), h('input', { class: 'form-control mono', id: 'ee_path', value: ent.endpoint_path || '' })),
          h('div', { class: 'd-flex justify-content-between align-items-center mb-2' }, h('label', { class: 'form-label mb-0', text: 'Fields' }),
            h('button', { type: 'button', class: 'btn btn-sm btn-outline-secondary', text: '+ Add field', onclick: () => addRow({}) })),
          rows),
        actions: [{ label: 'Cancel' }, {
          label: 'Save', primary: true, onClick: async d => {
            const name = d.$('#ee_name').value.trim();
            if (!name) { d.error('Name is required.'); return false; }
            await S.api(`/api/entities/${entityId}/`, { method: 'PATCH', body: { name, endpoint_path: d.$('#ee_path').value.trim() } });
            for (const row of rows.children) {
              const [nameIn, typeSel, reqIn] = [row.querySelector('input'), row.querySelector('select'), row.querySelector('input[type=checkbox]')];
              const fname = nameIn.value.trim();
              if (!fname) continue;
              const fid = row.dataset.fieldId;
              await S.api(fid ? `/api/fields/${fid}/` : '/api/fields/', {
                method: fid ? 'PATCH' : 'POST', body: { name: fname, field_type: typeSel.value, required: reqIn.checked, entity: entityId },
              });
            }
            for (const fid of removed) await S.api(`/api/fields/${fid}/`, { method: 'DELETE' });
            await this.reloadStructure();
          },
        }],
      });
    }

    // ── Transform rules editor ───────────────────────────────────────────
    editTransform(fm) {
      const rules = JSON.parse(JSON.stringify(fm.transform_rules || []));
      const list = h('div', {});
      const advanced = h('textarea', { class: 'form-control mono small', rows: '2', placeholder: 'e.g. value.upper() or value[:10]' });
      advanced.value = fm.transform || '';

      const ruleRow = (rule, idx) => {
        const op = rule.op || 'uppercase';
        const params = h('div', { class: 'mt-2' });
        if (op === 'map') {
          (rule.cases = rule.cases || []).forEach((c, ci) => params.append(h('div', { class: 'st-dlg-row' },
            h('input', { class: 'form-control form-control-sm', placeholder: 'From (e.g. simples)', value: c.from || '', oninput: e => { c.from = e.target.value; } }),
            h('span', { class: 'text-dim', text: '→' }),
            h('input', { class: 'form-control form-control-sm', placeholder: 'To (e.g. S)', value: c.to || '', oninput: e => { c.to = e.target.value; } }),
            h('button', { type: 'button', class: 'btn btn-sm btn-outline-danger', html: '&times;', onclick: () => { rule.cases.splice(ci, 1); redraw(); } }))));
          params.append(
            h('button', { type: 'button', class: 'btn btn-sm btn-outline-secondary', text: '+ Add value', onclick: () => { rule.cases.push({ from: '', to: '' }); redraw(); } }),
            h('div', { class: 'st-dlg-row mt-2' }, h('label', { class: 'small text-dim', text: 'If no match:' }),
              h('select', {
                class: 'form-select form-select-sm', style: 'width:auto', onchange: e => { rule.default_mode = e.target.value; redraw(); },
                html: `<option value="keep" ${rule.default_mode !== 'value' ? 'selected' : ''}>Keep original value</option><option value="value" ${rule.default_mode === 'value' ? 'selected' : ''}>Use a fallback value</option>`,
              }),
              rule.default_mode === 'value' ? h('input', { class: 'form-control form-control-sm', style: 'width:auto', placeholder: 'Fallback value', value: rule.default_value || '', oninput: e => { rule.default_value = e.target.value; } }) : null));
        } else if (op === 'default_if_empty') {
          params.append(h('input', { class: 'form-control form-control-sm', placeholder: 'Value to use when empty', value: rule.value || '', oninput: e => { rule.value = e.target.value; } }));
        }
        const labels = { uppercase: 'Uppercase', lowercase: 'Lowercase', trim: 'Trim whitespace', map: 'Map specific values', default_if_empty: 'Default if empty' };
        return h('div', { class: 'border rounded p-2 mb-2' },
          h('div', { class: 'd-flex align-items-center gap-2' },
            h('select', {
              class: 'form-select form-select-sm', style: 'width:auto', onchange: e => { rules[idx] = { op: e.target.value }; redraw(); },
              html: Object.entries(labels).map(([v, t]) => `<option value="${v}" ${v === op ? 'selected' : ''}>${t}</option>`).join(''),
            }),
            h('button', { type: 'button', class: 'btn btn-sm btn-outline-danger ms-auto', text: 'Remove', onclick: () => { rules.splice(idx, 1); redraw(); } })),
          params);
      };
      const redraw = () => list.replaceChildren(...(rules.length ? rules.map(ruleRow) : [h('p', { class: 'text-dim small', text: 'No rules yet — add one below.' })]));
      redraw();

      S.dialog({
        title: `Transform — ${fm.source_field_name} → ${fm.target_field_name}`, size: 'modal-lg',
        body: h('div', {},
          h('p', { class: 'text-dim small', text: 'Rules run in order, top to bottom, on the value read from the source field.' }),
          list,
          h('button', { type: 'button', class: 'btn btn-sm btn-outline-secondary', text: '+ Add rule', onclick: () => { rules.push({ op: 'uppercase' }); redraw(); } }),
          h('div', { class: 'mt-4' },
            h('label', { class: 'form-label' }, 'Advanced: custom expression ', h('span', { class: 'text-dim', text: '(optional)' })),
            advanced,
            h('div', { class: 'form-text' }, 'A Python expression on ', h('span', { class: 'mono', text: 'value' }), ', applied after the rules above.'))),
        actions: [{ label: 'Cancel' }, {
          label: 'Save', primary: true, onClick: async () => {
            const updated = await S.api(`/api/field-mappings/${fm.id}/`, { method: 'PATCH', body: { transform_rules: rules, transform: advanced.value } });
            Object.assign(fm, updated);
            this.rerender();
            this.renderMapTab();
          },
        }],
      });
    }

    // ── Properties / delete ──────────────────────────────────────────────
    properties() {
      const m = this.mapping;
      const conns = S.tree.connections.filter(c => c.id !== m.source_connection);
      const inUse = new Set(this.pairs.map(p => p.target_entity_detail.connection));
      const chosen = new Set(m.destination_connections);
      S.dialog({
        title: 'Mapping properties',
        body: h('div', {},
          h('div', { class: 'mb-3' }, h('label', { class: 'form-label', text: 'Name' }), h('input', { class: 'form-control', id: 'mp_name', value: m.name })),
          h('div', { class: 'mb-3' }, h('label', { class: 'form-label', text: 'Description' }), h('textarea', { class: 'form-control', id: 'mp_desc', rows: '2', text: m.description || '' })),
          h('div', { class: 'mb-2' }, h('label', { class: 'form-label', text: 'Integration origin' }), h('div', { class: 'mono', text: m.source_connection_name }),
            h('div', { class: 'form-text', text: "The origin can't change — its entities are already placed on the canvas." })),
          h('label', { class: 'form-label', text: 'Integration destinations' }),
          h('div', { class: 'st-checklist' }, conns.map(c => h('label', { class: 'd-flex gap-2 align-items-center' },
            h('input', { type: 'checkbox', class: 'form-check-input mt-0', value: c.id, checked: chosen.has(c.id), disabled: inUse.has(c.id) }),
            c.name, inUse.has(c.id) ? h('span', { class: 'text-dim small', text: '(has entity pairs)' }) : null)))),
        actions: [{ label: 'Cancel' }, {
          label: 'Save', primary: true, onClick: async d => {
            const name = d.$('#mp_name').value.trim();
            if (!name) { d.error('Name is required.'); return false; }
            const dests = [...d.el.querySelectorAll('.st-checklist input')].filter(i => i.checked || i.disabled && inUse.has(Number(i.value))).map(i => Number(i.value));
            this.mapping = await S.api(`/api/mappings/${this.id}/`, { method: 'PATCH', body: { name, description: d.$('#mp_desc').value, destination_connections: dests } });
            this.setTitle(this.mapping.name);
            this.rerender();
            S.renderPalette();
            this.changed();
            S.refreshTree();
          },
        }],
      });
    }

    destroy() { super.destroy(); this.stopPolling(); }

    // ── Running ──────────────────────────────────────────────────────────
    canKill() { return this.isActive(); }
    killMessage() { return `Kill the running migration of "${this.mapping.name}"? It stops between records — records already written stay written.`; }
    async kill() {
      const r = await S.api(`/api/mappings/${this.id}/cancel/`, { method: 'POST' });
      S.toast(r.cancelled ? 'Stopping — the run ends at its next check…' : 'Nothing was running.', 'ok');
      await this.loadRuns();
      this.startPollingIfNeeded();
      S.refreshTree();
    }

    // One run's own Kill / Cancel (a queued or scheduled run can be cancelled before it ever starts).
    async killRun(runId) {
      try {
        await S.api(`/api/runs/${runId}/cancel/`, { method: 'POST' });
        await this.loadRuns();
        if (this.selectedRun && this.selectedRun.id === runId) await this.selectRun(runId, { keepTab: true });
        this.startPollingIfNeeded();
        S.refreshTree();
      } catch (e) { S.fail(e); }
    }

    async run() { await this.startRun({}); }
    runOptions() { S.runOptionsDialog({ title: 'Run options', fields: ['schedule', 'rate', 'files'], onStart: opts => this.startRun(opts) }); }

    async startRun({ scheduled_at, rate_limit_per_second, files = [] }) {
      let resp;
      if (files.length > 1) {
        const fd = new FormData();
        fd.append('mapping_id', this.id);
        if (rate_limit_per_second) fd.append('rate_limit_per_second', rate_limit_per_second);
        files.forEach(f => fd.append('input_files', f));
        resp = await S.api('/api/runs/trigger-batch/', { method: 'POST', form: fd });
      } else if (files.length === 1) {
        const fd = new FormData();
        fd.append('mapping_id', this.id);
        if (scheduled_at) fd.append('scheduled_at', scheduled_at);
        if (rate_limit_per_second) fd.append('rate_limit_per_second', rate_limit_per_second);
        fd.append('input_file', files[0]);
        resp = await S.api('/api/runs/trigger/', { method: 'POST', form: fd });
      } else {
        const body = { mapping_id: this.id };
        if (scheduled_at) body.scheduled_at = scheduled_at;
        if (rate_limit_per_second) body.rate_limit_per_second = rate_limit_per_second;
        resp = await S.api('/api/runs/trigger/', { method: 'POST', body });
      }
      const first = Array.isArray(resp) ? resp[0] : resp;
      if (Array.isArray(resp)) S.toast(`Started a batch of ${resp.length} runs — they run one after another.`, 'ok');
      else if (first.status === 'pending' && first.scheduled_at) S.toast(`Run #${first.id} scheduled for ${new Date(first.scheduled_at).toLocaleString()}.`, 'ok');
      await this.loadRuns();
      await this.selectRun(first.id);
      this.startPollingIfNeeded();
      S.refreshTree();
    }

    startPollingIfNeeded() {
      this.running = this.isActive();
      this.changed();
      if (this.running && !this.timer) {
        this.timer = setInterval(() => this.poll(), 1100);
      }
    }
    stopPolling() { if (this.timer) { clearInterval(this.timer); this.timer = null; } }

    async poll() {
      try {
        await this.loadRuns();
        const sel = this.selectedRun;
        if (sel && (sel.status === 'running' || sel.status === 'pending' || this.runs.find(r => r.id === sel.id && r.status !== sel.status))) {
          const before = this.lastSample;
          const fresh = await S.api(`/api/runs/${sel.id}/`);
          const now = Date.now();
          this.rate = before && before.id === fresh.id && now > before.t ? Math.max(0, (fresh.requests_made - before.n) / ((now - before.t) / 1000)) : 0;
          this.lastSample = { id: fresh.id, n: fresh.requests_made, t: now };
          const trail = this.samples[fresh.id] = this.samples[fresh.id] || [];
          trail.push({ w: fresh.records_written, f: fresh.records_failed });
          if (trail.length > 120) trail.shift();
          this.setRun(fresh);
        }
      } catch (e) { /* transient — keep polling */ }
      if (!this.isActive()) {
        const wasRunning = this.running;
        this.stopPolling();
        this.running = false;
        this.rate = 0;
        this.renderMetrics();
        this.changed();
        S.refreshTree();
        if (wasRunning && this.selectedRun) {
          S.toast(`Run #${this.selectedRun.id} ${this.selectedRun.status}.`, ['success', 'cancelled'].includes(this.selectedRun.status) ? 'ok' : 'err');
        }
      }
    }

    async loadRuns() {
      this.runs = await S.apiList(`/api/runs/?mapping=${this.id}&slim=1`);
      this.renderRuns();
    }

    async selectRun(runId, { keepTab } = {}) {
      try { this.setRun(await S.api(`/api/runs/${runId}/`)); } catch (e) { S.fail(e); return; }
      if (!keepTab) this.tabs.select('metrics');
      this.renderRuns();
    }

    setRun(run) {
      this.selectedRun = run;
      this.renderMetrics();
      this.renderLog();
      this.renderRuns();
      this.applyRunStatuses();
      this.changed();
    }

    // Decorate the canvas with the selected run: box colour by the worst status among the
    // pairs a box takes part in, an in/out strip under each box (what was read / written /
    // failed), and moving dashes on the wires of any pair that is running right now.
    applyRunStatuses() {
      if (!this.surface) return;
      this.surface.querySelectorAll('.st-entity').forEach(n => n.removeAttribute('data-status'));
      this.surface.querySelectorAll('.st-entity-stat').forEach(n => n.remove());
      const run = this.selectedRun;
      this.connFm.forEach((fm, conn) => { const el = conn.connector && conn.connector.canvas; if (el) el.classList.remove('flowing'); });
      if (!run) return;

      const worst = {};
      const totals = {};
      const bump = (key, status) => { if ((STATUS_RANK[status] || 0) > (STATUS_RANK[worst[key]] || 0)) worst[key] = status; };
      const tally = key => (totals[key] = totals[key] || { read: 0, written: 0, failed: 0 });
      const runningPairs = new Set();
      (run.step_statuses || []).forEach(ss => {
        const pair = this.pairs.find(p => p.id === ss.entity_mapping);
        if (!pair) return;
        const src = `source:${pair.source_entity}`, tgt = `target:${pair.target_entity}`;
        bump(src, ss.status); bump(tgt, ss.status);
        // A source is read once per run however many targets it feeds, so take the max, not the sum.
        const t = tally(src); t.read = Math.max(t.read, ss.records_read);
        const u = tally(tgt); u.read += ss.records_read; u.written += ss.records_written; u.failed += ss.records_failed;
        if (ss.status === 'running') runningPairs.add(ss.entity_mapping);
      });

      const attr = { failed: 'failed', running: 'running', success: 'success' };
      Object.entries(worst).forEach(([key, st]) => {
        const [side, id] = key.split(':');
        const node = this.entityNode(side, id);
        if (!node) return;
        if (attr[st]) node.setAttribute('data-status', attr[st]);
        const t = totals[key];
        node.append(side === 'source'
          ? h('div', { class: 'st-entity-stat' }, h('div', { class: 'st-stat-line' }, h('span', { text: 'in' }), h('span', {}, h('b', { text: S.viz.compact(t.read) }), ' read')))
          : h('div', { class: 'st-entity-stat' }, S.viz.meter({ good: t.written, bad: t.failed, total: t.read }),
            h('div', { class: 'st-stat-line', style: 'margin-top:3px' }, h('span', { text: 'out' }),
              h('span', {}, h('b', { text: S.viz.compact(t.written) }), ' written',
                t.failed ? h('span', { class: 'fail' }, ' · ', h('i', { class: 'bi-exclamation-triangle-fill' }), ' ', h('b', { text: S.viz.compact(t.failed) }), ' failed') : null))));
      });

      this.connFm.forEach((fm, conn) => {
        const el = conn.connector && conn.connector.canvas;
        if (el) el.classList.toggle('flowing', runningPairs.has(fm.entity_mapping));
      });
    }

    // ── Results panel ────────────────────────────────────────────────────
    buildResults() {
      this.tabs = S.makeTabs([
        { id: 'map', label: 'Field mappings' }, { id: 'data', label: 'Data preview' }, { id: 'runs', label: 'Runs' },
        { id: 'metrics', label: 'Step metrics' }, { id: 'log', label: 'Logging' },
      ]);
      this.tabs.onSelect = id => { if (id === 'data') this.ensurePreview(); };
      this.resultsEl.replaceChildren(this.tabs.root);
    }

    // The old "Raw" tab: per pair, its operation and every wire's transform.
    renderMapTab() {
      const page = this.tabs.page('map');
      if (!this.pairs.length) { page.replaceChildren(h('div', { class: 'st-empty', text: 'No entity pairs yet — add one from the Design tab.' })); this.tabs.setNote(''); this.invalidatePreview(); return; }
      const rows = [];
      this.pairs.forEach(p => {
        rows.push(h('tr', { class: 'st-group' },
          h('td', { colspan: '3' }, h('b', { text: p.source_entity_detail.name }), ' → ', h('b', { text: p.target_entity_detail.name }),
            h('span', { class: 'text-dim ms-2', text: `${p.source_entity_detail.connection_name} → ${p.target_entity_detail.connection_name}` })),
          h('td', {}, h('select', {
            class: 'form-select form-select-sm', style: 'width:auto', title: 'HTTP verb used to write each record',
            onchange: async e => { try { await S.api(`/api/entity-mappings/${p.id}/`, { method: 'PATCH', body: { write_method: e.target.value } }); p.write_method = e.target.value; } catch (err) { S.fail(err); e.target.value = p.write_method; } },
            html: METHODS.map(x => `<option ${x === p.write_method ? 'selected' : ''}>${x}</option>`).join(''),
          })),
          h('td', { class: 'st-actions' }, h('button', { class: 'st-mini danger', text: 'Remove pair', onclick: () => this.removePair(p) }))));
        if (!p.field_mappings.length) rows.push(h('tr', {}, h('td', { colspan: '5', class: 'text-dim', text: 'No field mappings yet — drag a teal dot onto an amber dot on the canvas.' })));
        p.field_mappings.forEach(fm => rows.push(h('tr', {},
          h('td', { class: 'mono', text: fm.source_field_name }),
          h('td', { class: 'text-dim', text: '→' }),
          h('td', { class: 'mono', text: fm.target_field_name }),
          h('td', {}, hasTransform(fm) ? h('span', { class: 'st-tag ok', title: transformSummary(fm), text: `ƒ ${(fm.transform_rules || []).length + (fm.transform ? 1 : 0)}` }) : h('span', { class: 'text-dim', text: '—' }),
            fm.transform ? h('span', { class: 'mono text-dim ms-2', text: fm.transform }) : null),
          h('td', { class: 'st-actions' },
            h('button', { class: 'st-mini', text: 'Transform', onclick: () => this.editTransform(fm) }), ' ',
            h('button', { class: 'st-mini danger', html: '&times;', title: 'Remove', onclick: () => this.removeWire(fm) })))));
      });
      page.replaceChildren(h('table', { class: 'st-table' },
        h('thead', {}, h('tr', {}, h('th', { text: 'Source field', style: 'width:32%' }), h('th', { class: 'w-arrow' }), h('th', { text: 'Target field', style: 'width:32%' }), h('th', { text: 'Transform / operation' }), h('th', {}))),
        h('tbody', {}, rows)));
      const fields = this.pairs.reduce((n, p) => n + p.field_mappings.length, 0);
      this.tabs.setNote(`${this.pairs.length} pair(s) · ${fields} field mapping(s)`);
      this.invalidatePreview();      // pairs, wires and transforms all feed the preview
    }

    // ── Data preview: input → mapping → output ───────────────────────────
    // A read-only dry run (GET /api/mappings/<id>/preview/): a sample of each pair's source
    // next to the exact payloads the field mappings would send. Nothing is written.
    invalidatePreview() {
      this.previewStale = true;
      if (this.tabs.current === 'data') this.ensurePreview();
    }

    async ensurePreview(force) {
      if (this.previewLoading || (!force && this.preview && !this.previewStale)) return;
      if (!this.pairs.length) { this.renderData(); return; }
      this.previewLoading = true;
      this.previewError = null;
      this.renderData();                     // keeps the previous frame, dimmed, while refetching
      try {
        this.preview = await S.api(`/api/mappings/${this.id}/preview/?limit=${this.previewLimit}`);
        this.previewStale = false;
      } catch (e) { this.previewError = e.message; }
      this.previewLoading = false;
      this.renderData();
    }

    renderData() {
      const page = this.tabs.page('data');
      if (!this.pairs.length) { page.replaceChildren(h('div', { class: 'st-empty', text: 'No entity pairs yet — add one from the Design tab to preview its data.' })); return; }
      const pairs = this.preview ? this.preview.pairs : [];
      const entry = pairs.find(p => p.entity_mapping === this.previewPair) || pairs[0];
      if (entry) this.previewPair = entry.entity_mapping;

      const bar = h('div', { class: 'st-data-bar' },
        pairs.length > 1 ? h('select', {
          class: 'form-select form-select-sm', title: 'Entity pair',
          onchange: e => { this.previewPair = Number(e.target.value); this.renderData(); },
          html: pairs.map(p => `<option value="${p.entity_mapping}" ${p.entity_mapping === this.previewPair ? 'selected' : ''}>${S.esc(p.source_entity)} → ${S.esc(p.target_entity)}</option>`).join(''),
        }) : null,
        h('select', {
          class: 'form-select form-select-sm', title: 'Sample size',
          onchange: e => { this.previewLimit = Number(e.target.value); this.ensurePreview(true); },
          html: [5, 10, 25].map(n => `<option value="${n}" ${n === this.previewLimit ? 'selected' : ''}>${n} rows</option>`).join(''),
        }),
        h('button', { class: 'st-mini', disabled: this.previewLoading ? '' : null, onclick: () => this.ensurePreview(true) }, h('i', { class: 'bi-arrow-clockwise' }), ' Refresh'),
        h('span', { class: 'spacer' }),
        h('span', {}, h('i', { class: 'bi-shield-check me-1' }), 'Dry run — reads a sample and applies your transforms; nothing is written.'));

      if (this.previewError) { page.replaceChildren(bar, h('div', { class: 'st-error-box' }, h('i', { class: 'bi-exclamation-triangle-fill' }), `Couldn't build the preview: ${this.previewError}`)); return; }
      if (!entry) { page.replaceChildren(bar, h('div', { class: 'st-empty', text: 'Reading a sample…' })); return; }
      this.tabs.setNote(`${entry.sample_size} of ${entry.total} source record(s)`);

      // ---- input side
      const mapped = new Set(entry.mapped_source);
      const inHead = h('tr', {}, entry.source_columns.map(c => h('th', {
        class: mapped.has(c) ? '' : 'unmapped', title: mapped.has(c) ? 'Mapped — sent to the target' : 'Not mapped — dropped, never sent',
      }, mapped.has(c) ? h('span', { class: 'used' }) : null, c)));
      const inBody = entry.rows.map((row, i) => h('tr', { dataset: { i } },
        entry.source_columns.map(c => h('td', { class: mapped.has(c) ? '' : 'unmapped' }, S.viz.cell(row[c])))));

      // ---- output side (table of fields, or the raw request bodies)
      const chg = entry.changed;
      const outHead = h('tr', {}, entry.target_columns.map(c => h('th', {}, c, h('small', { text: `← ${entry.source_of[c]}` }))));
      const outBody = entry.output.map((row, i) => h('tr', { dataset: { i } },
        entry.target_columns.map(c => h('td', {
          class: chg[i].includes(c) ? 'chg' : '',
          title: chg[i].includes(c) ? `Changed by a transform — was ${JSON.stringify(entry.rows[i][entry.source_of[c]])}` : null,
        }, S.viz.cell(row[c])))));

      const side = (role, name, connection, metaText, extraHead, content) => h('div', { class: 'st-side' },
        h('div', { class: 'st-side-head' }, h('span', { class: 'role', text: role }), h('span', { class: 'name', text: name }),
          h('span', { class: 'text-dim', text: connection }), extraHead, h('span', { class: 'meta', text: metaText })),
        content);
      const table = (head, body) => h('div', { class: 'st-side-scroll' }, h('table', { class: 'st-dtable' }, h('thead', {}, head), h('tbody', {}, body)));

      const input = side('Input', entry.source_entity, entry.source_connection, `${entry.sample_size} of ${entry.total}`, null,
        entry.error
          ? h('div', { class: 'st-error-box' }, h('i', { class: 'bi-exclamation-triangle-fill' }), `Couldn't read the source: ${entry.error}`)
          : table(inHead, inBody));

      const toggle = h('span', { class: 'st-seg' }, ['table', 'json'].map(v => h('button', {
        class: this.previewView === v ? 'on' : '', text: v === 'table' ? 'Fields' : 'JSON',
        onclick: () => { this.previewView = v; this.renderData(); },
      })));
      const output = side('Output', entry.target_entity, entry.target_connection, `${entry.write_method} ${entry.target_endpoint || '(no endpoint set)'}`, toggle,
        !entry.target_columns.length
          ? h('div', { class: 'st-error-box', text: 'No field mappings yet — nothing would be sent for this pair.' })
          : this.previewView === 'json'
            ? h('pre', { class: 'st-pre', text: JSON.stringify(entry.payloads, null, 2) })
            : table(outHead, outBody));

      const flow = h('div', { class: `st-dataflow ${this.previewLoading ? 'stale' : ''}` },
        input, h('div', { class: 'st-arrow' }, h('i', { class: 'bi-arrow-right' }), h('small', { text: 'map' })), output);
      // Rows line up one-to-one, so hovering a row lights up its counterpart on the other side.
      flow.addEventListener('mouseover', e => {
        const tr = e.target.closest('tr[data-i]');
        flow.querySelectorAll('tr.hl').forEach(r => r.classList.remove('hl'));
        if (tr) flow.querySelectorAll(`tr[data-i="${tr.dataset.i}"]`).forEach(r => r.classList.add('hl'));
      });
      page.replaceChildren(bar, flow);
    }

    async removePair(p) {
      if (!(await confirmModal(`Remove the pair ${p.source_entity_detail.name} → ${p.target_entity_detail.name} and its ${p.field_mappings.length} field mapping(s)?`))) return;
      try {
        await S.api(`/api/entity-mappings/${p.id}/`, { method: 'DELETE' });
        await this.reloadStructure();
      } catch (e) { S.fail(e); }
    }

    renderRuns() {
      S.viz.hideTip();      // the columns under the pointer are about to be replaced
      const page = this.tabs.page('runs');
      if (!this.runs.length) { page.replaceChildren(h('div', { class: 'st-empty', text: "This mapping hasn't run yet — press Run migration." })); return; }
      page.replaceChildren(this.syncDashboard(), h('table', { class: 'st-table' },
        h('thead', {}, h('tr', {}, ['Run', 'Status', 'Read', 'Written', 'Failed', 'Requests', 'Started', 'Duration', ''].map((t, i) => h('th', { class: i >= 2 && i <= 5 ? 'num' : '', text: t })))),
        h('tbody', {}, this.runs.map(r => h('tr', { class: `st-clickable ${this.selectedRun && this.selectedRun.id === r.id ? 'selected' : ''}`, onclick: () => this.selectRun(r.id) },
          h('td', { class: 'mono', text: `#${r.id}${r.retry_of ? ` ↺${r.retry_of}` : ''}` }),
          h('td', {}, S.chip(r.status, r.status === 'pending' && r.scheduled_at ? `scheduled ${S.fmtDateTime(r.scheduled_at)}` : r.status)),
          h('td', { class: 'num', text: r.records_read }), h('td', { class: 'num', text: r.records_written }),
          h('td', { class: 'num', text: r.records_failed }), h('td', { class: 'num', text: r.requests_made }),
          h('td', { class: 'mono', text: S.fmtDateTime(r.started_at) }),
          h('td', { class: 'mono', text: r.status === 'pending' ? '—' : S.duration(r.started_at, r.finished_at) }),
          h('td', { class: 'st-actions', onclick: e => e.stopPropagation() },
            r.status === 'pending' || r.status === 'running'
              ? h('button', { class: 'st-mini danger', text: r.status === 'running' ? 'Kill' : 'Cancel', title: r.status === 'running' ? 'Stop this run' : 'Cancel this run before it starts', onclick: () => this.killRun(r.id) }) : null, ' ',
            (r.status === 'failed' || r.status === 'cancelled') && !r.retry_resolved ? h('button', { class: 'st-mini', text: r.status === 'cancelled' ? 'Retry' : 'Retry failed', onclick: () => this.retry(r.id) }) : null, ' ',
            h('a', { class: 'st-mini', href: `/jobs/runs/${r.id}/`, target: '_blank', rel: 'noopener', text: 'Detail' }), ' ',
            h('a', { class: 'st-mini', href: `/jobs/runs/${r.id}/snapshot/`, target: '_blank', rel: 'noopener', text: 'Pipeline' })))))));
      this.tabs.setNote(`${this.runs.length} run(s)`);
    }

    // Sync health at a glance: headline tiles + one column per recent run (oldest → newest).
    syncDashboard() {
      const recent = this.runs.slice(0, 20);
      const finished = recent.filter(r => r.status === 'success' || r.status === 'failed');
      const okCount = finished.filter(r => r.status === 'success').length;
      const secs = finished.map(r => (new Date(r.finished_at) - new Date(r.started_at)) / 1000).filter(x => x >= 0);
      const avg = secs.length ? secs.reduce((a, b) => a + b, 0) / secs.length : null;
      const fmt = sec => sec < 60 ? `${Math.round(sec)}s` : `${Math.floor(sec / 60)}m ${Math.round(sec % 60)}s`;
      const last = this.runs[0];
      const ago = S.ago(last.started_at);

      const items = recent.slice().reverse().map(r => ({
        id: r.id, label: `#${r.id}`, good: r.records_written, bad: r.records_failed,
        rest: Math.max(0, r.records_read - r.records_written - r.records_failed), tickBad: r.status === 'failed',
        tip: {
          title: `Run #${r.id} · ${r.status === 'pending' && r.scheduled_at ? 'scheduled' : r.status}`,
          value: `${S.viz.compact(r.records_written)} written`,
          rows: [['Read', S.viz.compact(r.records_read)], ['Failed', S.viz.compact(r.records_failed)],
            ['Duration', r.status === 'pending' ? '—' : S.duration(r.started_at, r.finished_at)], ['Started', S.fmtDateTime(r.started_at)]],
        },
      }));

      return h('div', { class: 'st-dash' },
        S.viz.kpis([
          { label: 'Last sync', value: ago === 'now' ? 'just now' : `${ago} ago`, sub: S.chip(last.status, last.status === 'pending' && last.scheduled_at ? 'scheduled' : last.status) },
          { label: 'Success rate', value: finished.length ? `${Math.round((okCount / finished.length) * 100)}%` : '—', sub: `${okCount} of ${finished.length} finished` },
          { label: 'Records written', value: S.viz.compact(recent.reduce((n, r) => n + r.records_written, 0)), sub: `last ${recent.length} run${recent.length > 1 ? 's' : ''}` },
          { label: 'Avg duration', value: avg == null ? '—' : fmt(avg), sub: `over ${secs.length} run${secs.length === 1 ? '' : 's'}` },
        ]),
        h('div', {}, h('div', { class: 'st-dash-title', text: 'Records per run — click a column to inspect it' }),
          S.viz.bars(items, { selectedId: this.selectedRun && this.selectedRun.id, onSelect: id => this.selectRun(id), legend: { good: 'Written', bad: 'Failed', rest: 'Not written' } })));
    }

    async retry(runId) {
      try {
        const run = await S.api(`/api/runs/${runId}/retry/`, { method: 'POST' });
        await this.loadRuns();
        await this.selectRun(run.id);
        this.startPollingIfNeeded();
        S.refreshTree();
      } catch (e) { S.fail(e); }
    }

    renderMetrics() {
      const page = this.tabs.page('metrics');
      const run = this.selectedRun;
      if (!run) { page.replaceChildren(h('div', { class: 'st-empty', text: 'Select a run to see per-entity progress.' })); return; }
      // Writes expected so far: each pair reads its source once (a fan-out source feeds several pairs).
      const expected = (run.step_statuses || []).reduce((n, ss) => n + ss.records_read, 0) || run.records_read;
      const trail = this.samples[run.id] || [];
      const summary = h('div', { class: 'st-summary' },
        S.chip(run.status), h('span', {}, 'Run ', h('b', { text: `#${run.id}` })),
        h('span', {}, 'read ', h('b', { text: run.records_read })), h('span', {}, 'written ', h('b', { text: run.records_written })),
        h('span', {}, 'failed ', h('b', { text: run.records_failed })), h('span', {}, 'requests ', h('b', { text: run.requests_made })),
        run.status === 'running' ? h('span', {}, h('b', { text: this.rate.toFixed(1) }), ' rq/s') : null,
        h('span', { class: 'st-meter-cell', style: 'flex:1 1 140px;max-width:260px' }, S.viz.meter({ good: run.records_written, bad: run.records_failed, total: expected })),
        trail.length >= 2 ? h('span', { class: 'st-live' }, S.viz.spark(trail.map(x => x.w), { title: `Records written over the last ${trail.length} polls` }), h('span', { class: 'text-dim', text: `${S.viz.compact(run.records_written)} written` })) : null,
        run.rate_limit_per_second ? h('span', { class: 'text-dim', text: `capped at ${Math.round(run.rate_limit_per_second * 60 * 100) / 100}/min` }) : null,
        h('span', { class: 'text-dim', text: S.duration(run.started_at, run.finished_at) }));
      const rows = (run.step_statuses || []).map(ss => {
        const pair = this.pairs.find(p => p.id === ss.entity_mapping);
        return h('tr', {},
          h('td', {}, h('span', { class: `st-dot ${ss.status}`, style: 'display:inline-block;margin-right:8px' }), pair ? `${pair.source_entity_detail.name} → ${pair.target_entity_detail.name}` : `pair #${ss.entity_mapping} (removed)`),
          h('td', {}, S.chip(ss.status)),
          h('td', { class: 'num', text: ss.records_read }), h('td', { class: 'num', text: ss.records_written }), h('td', { class: 'num', text: ss.records_failed }),
          h('td', { class: 'st-meter-cell' }, S.viz.meter({ good: ss.records_written, bad: ss.records_failed, total: ss.records_read })),
          h('td', { class: 'mono', text: ss.started_at ? S.duration(ss.started_at, ss.finished_at) : '—' }),
          h('td', { class: 'text-dim', text: ss.error_message || '' }));
      });
      page.replaceChildren(summary, rows.length
        ? h('table', { class: 'st-table' }, h('thead', {}, h('tr', {}, ['Entity pair', 'Status', 'Read', 'Written', 'Failed', 'Progress', 'Time', 'Error'].map((t, i) => h('th', { class: i >= 2 && i <= 4 ? 'num' : '', text: t })))), h('tbody', {}, rows))
        : h('div', { class: 'st-empty', text: run.status === 'pending' ? 'Waiting to start…' : 'No entity pair was processed by this run.' }));
    }

    renderLog() {
      const page = this.tabs.page('log');
      const run = this.selectedRun;
      const logs = run ? run.logs || [] : [];
      if (!logs.length) { page.replaceChildren(h('div', { class: 'st-empty', text: run ? 'No log lines for this run.' : 'Select a run to see its log.' })); return; }
      const shown = logs.slice(-500);
      const stick = page.parentElement && page.parentElement.scrollTop + page.parentElement.clientHeight >= page.parentElement.scrollHeight - 30;
      page.replaceChildren(
        logs.length > shown.length ? h('div', { class: 'st-empty', text: `Showing the last ${shown.length} of ${logs.length} lines — see the run's detail page for the full log.` }) : null,
        h('div', { class: 'st-log' }, shown.map(l => h('div', { class: `lv-${l.level}` }, h('span', { class: 't', text: S.fmtTime(l.created_at) }), l.message))));
      if (stick && page.parentElement) page.parentElement.scrollTop = page.parentElement.scrollHeight;
    }
  }

  S.editorClasses.mapping = MappingEditor;
})();
