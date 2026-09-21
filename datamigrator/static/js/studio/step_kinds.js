/* Step kinds — everything the chain editor needs to know about *what a step is*:
   its label/icon, a one-line summary for the canvas card, the dialog form for
   adding/editing it, and how a run's result for it is described and expanded.

   Kinds (mirrors chains/models.py CallChainStep.KIND_CHOICES):
     http    a request — with an optional timeout, and optionally async (start a job, poll until done)
     wait    pause for N seconds
     next    follow a response's "next page" pointer, repeating a GET, and collect every page's items
     find    search a list an earlier step produced for the item where a field matches
     check   assert something about an earlier HTTP response's headers
     file    read the first rows of an uploaded CSV/XLSX (and preview them right in the dialog)
   All of them can `capture` values from their result for later steps ({{name}}). */
(function () {
  'use strict';
  const S = window.Studio;
  const h = S.h;

  const METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'];
  const NAME_RE = /^[A-Za-z_][A-Za-z0-9_-]*$/;

  const K = S.stepKinds = {};

  K.meta = {
    http:  { label: 'HTTP call', icon: null },
    wait:  { label: 'Wait', icon: 'bi-stopwatch' },
    next:  { label: 'Next page', icon: 'bi-skip-forward-fill' },
    find:  { label: 'Find in list', icon: 'bi-search' },
    check: { label: 'Check header', icon: 'bi-shield-check' },
    file:  { label: 'File preview', icon: 'bi-file-earmark-spreadsheet' },
  };

  const FIND_OPS = [['equals', 'equals'], ['not_equals', 'does not equal'], ['contains', 'contains'], ['starts_with', 'starts with'],
    ['matches', 'matches regex'], ['gt', 'is greater than'], ['lt', 'is less than']];
  const CHECK_OPS = [['exists', 'exists'], ['missing', 'is missing'], ['equals', 'equals'], ['not_equals', 'does not equal'],
    ['contains', 'contains'], ['starts_with', 'starts with'], ['matches', 'matches regex']];
  const opLabel = (ops, v) => (ops.find(o => o[0] === v) || [v, v])[1];

  K.verbBadge = m => h('span', { class: `verb-badge verb-${m.toLowerCase()}`, text: m });

  // The badge on a card / result row: the verb for a request, else the kind's icon + name.
  K.badge = (kind, method) => kind === 'http' || !kind
    ? K.verbBadge(method || 'GET')
    : h('span', { class: `st-kind-badge k-${kind}` }, h('i', { class: K.meta[kind].icon }), K.meta[kind].label);

  // ── One-line descriptions ────────────────────────────────────────────────
  K.summary = (s, fileNames) => {
    const p = s.params || {};
    switch (s.kind) {
      case 'wait': return `pause ${p.seconds}s`;
      case 'next': return `follow next of ${p.from_step} · ≤ ${p.max_pages} pages`;
      case 'find': return `${p.source} where ${p.match_field || 'item'} ${opLabel(FIND_OPS, p.operator)} ${p.value || ''}`.trim();
      case 'check': return `${p.header} ${opLabel(CHECK_OPS, p.operator)}${p.value && !['exists', 'missing'].includes(p.operator) ? ' ' + p.value : ''} · in ${p.from_step}`;
      case 'file': return `${(fileNames && fileNames[p.entity]) || 'file #' + p.entity} · first ${p.limit} rows`;
      default: return s.path;
    }
  };

  // The same for a run's result (what actually happened).
  K.resultSummary = r => {
    const d = r.detail || {};
    switch (r.kind) {
      case 'wait': return `waited ${d.waited}s`;
      case 'next': return d.pages ? `${d.pages} page(s) · ${d.items} item(s)${d.more ? ' · more pages left' : ''}` : '';
      case 'find': return d.searched != null ? `${d.matched} of ${d.searched} matched (${d.criteria})` : '';
      case 'check': return d.header ? `${d.header}: ${d.actual == null ? '(absent)' : d.actual}` : '';
      case 'file': return d.entity ? `${d.entity} · ${d.rows_shown} of ${d.count} rows` : '';
      default: return r.resolved_path;
    }
  };

  // error → the step stopped the chain; warn → a check that failed but was told to carry on; cancelled → killed.
  K.CANCELLED = 'Cancelled by user.';
  K.resultState = r => {
    if (r.error === K.CANCELLED) return 'cancelled';     // the Kill button — not something that went wrong
    if (r.error) return 'error';
    if (r.kind === 'http' && (!r.status_code || r.status_code >= 400)) return 'error';
    if (r.kind === 'check' && r.detail && r.detail.passed === false) return 'warn';
    return 'success';
  };

  // ── Dialog forms ─────────────────────────────────────────────────────────
  const field = (label, id, value, o = {}) => h('div', { class: o.col || 'col-md-6' },
    h('label', { class: 'form-label', for: id, text: label }),
    h('input', { class: `form-control form-control-sm ${o.mono === false ? '' : 'mono'}`, id, value: value == null ? '' : value, placeholder: o.ph || '', type: o.type || 'text', step: o.step, min: o.min, max: o.max }),
    o.help ? h('div', { class: 'form-text', text: o.help }) : null);
  const selectField = (label, id, options, selected, o = {}) => h('div', { class: o.col || 'col-md-6' },
    h('label', { class: 'form-label', for: id, text: label }),
    h('select', { class: 'form-select form-select-sm', id, html: options.map(([v, t]) => `<option value="${S.esc(v)}" ${v === selected ? 'selected' : ''}>${S.esc(t)}</option>`).join('') }),
    o.help ? h('div', { class: 'form-text', text: o.help }) : null);
  const $ = (id) => document.getElementById(id);

  function capturesBlock(initial) {
    const captures = (initial || []).map(c => ({ ...c }));
    const list = h('div', {});
    const redraw = () => list.replaceChildren(...(captures.length ? captures.map((c, i) => h('div', { class: 'st-dlg-row' },
      h('input', { class: 'form-control form-control-sm mono', style: 'max-width:180px', placeholder: 'variable_name', value: c.name, oninput: e => { c.name = e.target.value; } }),
      h('span', { class: 'text-dim', text: '←' }),
      h('input', { class: 'form-control form-control-sm mono', placeholder: 'json.path — blank = whole result, items.*.id = every id', value: c.path, oninput: e => { c.path = e.target.value; } }),
      h('button', { type: 'button', class: 'btn btn-sm btn-outline-danger', html: '&times;', onclick: () => { captures.splice(i, 1); redraw(); } }))) : [h('p', { class: 'text-dim small mb-1', text: 'No captures yet.' })]));
    redraw();
    return {
      el: h('fieldset', {}, h('legend', { text: 'Captures — values later steps can use as {{name}}' }), list,
        h('button', { type: 'button', class: 'btn btn-sm btn-outline-secondary', text: '+ Add capture', onclick: () => { captures.push({ name: '', path: '' }); redraw(); } })),
      read: () => captures.filter(c => c.name.trim()),
    };
  }

  // A name/value list editor (request headers, query params). Values may use {{placeholders}}.
  function pairsBlock(title, hint, initial, { namePh, valuePh, datalist } = {}) {
    const rows = (initial || []).map(r => ({ ...r }));
    const list = h('div', {});
    const redraw = () => list.replaceChildren(...(rows.length ? rows.map((r, i) => h('div', { class: 'st-dlg-row' },
      h('input', { class: 'form-control form-control-sm mono', style: 'max-width:200px', placeholder: namePh, list: datalist, value: r.name, oninput: e => { r.name = e.target.value; } }),
      h('span', { class: 'text-dim', text: '=' }),
      h('input', { class: 'form-control form-control-sm mono', placeholder: valuePh, value: r.value, oninput: e => { r.value = e.target.value; } }),
      h('button', { type: 'button', class: 'btn btn-sm btn-outline-danger', html: '&times;', onclick: () => { rows.splice(i, 1); redraw(); } }))) : [h('p', { class: 'text-dim small mb-1', text: hint })]));
    redraw();
    return {
      el: h('fieldset', {}, h('legend', { text: title }), list,
        h('button', { type: 'button', class: 'btn btn-sm btn-outline-secondary', text: '+ Add', onclick: () => { rows.push({ name: '', value: '' }); redraw(); } })),
      read: () => rows.filter(r => r.name.trim() || String(r.value).trim()).map(r => ({ name: r.name.trim(), value: r.value })),
    };
  }
  const COMMON_HEADERS = ['Accept', 'Authorization', 'Content-Type', 'X-Api-Key', 'X-Request-Id', 'If-None-Match', 'User-Agent', 'Idempotency-Key'];

  // Suggestions for "which list?" — click a chip to fill the field.
  function sourceChips(steps, before, target) {
    const suggest = s => s.kind === 'next' ? `${s.name}.items` : s.kind === 'file' ? `${s.name}.rows` : s.kind === 'find' ? s.name : `${s.name}.`;
    const chips = steps.filter(s => s.kind !== 'wait' && (!before || s.order < before.order))
      .map(s => h('button', { type: 'button', class: 'st-chip', title: `Search ${suggest(s)}`, onclick: () => { const el = $(target); el.value = suggest(s); el.focus(); }, text: s.name }));
    return chips.length ? h('div', { class: 'st-chips' }, h('span', { class: 'text-dim small', text: 'Earlier steps:' }), ...chips) : null;
  }

  // ctx: { kind, step (or null), preset, steps (the steps that run BEFORE this one), fileEntities: () => Promise<[entity]>,
  //        connections (optional: when given, a request step also asks which connection it calls — used by a
  //        plan's function blocks, which have no chain-wide connection), nextIndex (optional: number for the default name) }
  // → { nodes, read() } — read() returns the API payload or throws an Error with a user-facing message.
  K.form = function (ctx) {
    const { kind, step, steps } = ctx;
    const isNew = !step;
    const s = step || {};
    const p = s.params || {};
    const upstream = steps.filter(x => x.kind === 'http' && (!step || x.order < step.order));
    const defaultUpstream = p.from_step || (upstream.length ? upstream[upstream.length - 1].name : '');
    const nameField = field('Name', 'sd_name', isNew ? `${kind === 'http' ? 'step' : kind}_${ctx.nextIndex || steps.length + 1}` : s.name, { col: 'col-md-4', help: 'Later steps refer to this step by name.' });
    const captures = kind === 'wait' ? null : capturesBlock(s.captures);
    const finish = (extra) => {
      const name = $('sd_name').value.trim();
      if (!NAME_RE.test(name)) throw new Error('Name must start with a letter or underscore and contain only letters, digits, _ or -.');
      return { name, kind, ...(captures ? { captures: captures.read() } : {}), ...extra };
    };
    const needUpstream = (what) => {
      if (!upstream.length) throw new Error(`${what} reads an earlier HTTP call — add one before this step first.`);
    };
    const upstreamNote = () => upstream.length ? null : h('div', { class: 'alert alert-warning py-2 small', text: 'This step reads the response of an earlier HTTP call, and there is none yet — add one before it.' });
    const helpBox = text => h('div', { class: 'text-dim small mb-2', html: text });

    // ---------------------------------------------------------------- wait
    if (kind === 'wait') {
      return {
        nodes: [helpBox('Pauses the chain — handy between calls that need time to settle, or to stay under a rate limit. The run blocks while it waits, so it is capped at <b>300 seconds</b>.'),
          h('div', { class: 'row g-2' }, nameField, field('Seconds to wait', 'sd_seconds', p.seconds ?? 5, { type: 'number', step: '0.5', min: '0', max: '300', mono: false, col: 'col-md-3' }))],
        read: () => finish({ params: { seconds: parseFloat($('sd_seconds').value) } }),
      };
    }

    // ---------------------------------------------------------------- next
    if (kind === 'next') {
      const getSteps = upstream.filter(x => x.method === 'GET');
      const sel = getSteps.map(x => [x.name, `${x.name}  (${x.path})`]);
      return {
        nodes: [helpBox('Repeats an earlier <b>GET</b> call, following the “next” pointer in each response, until there is no next page or the page limit is hit. ' +
          'The result is <span class="mono">{{name.items}}</span> (every item from every page), <span class="mono">{{name.pages}}</span> and <span class="mono">{{name.more}}</span>. ' +
          'Links are only followed on this connection\'s own host.'),
          getSteps.length ? null : h('div', { class: 'alert alert-warning py-2 small', text: 'Needs an earlier GET step to page through — add one first.' }),
          h('div', { class: 'row g-2' }, nameField,
            selectField('Page through', 'sd_from', sel, defaultUpstream, { col: 'col-md-5' }),
            field('Max pages', 'sd_max', p.max_pages ?? 5, { type: 'number', min: '1', max: '100', mono: false, col: 'col-md-3', help: 'Including the first page.' }),
            field('Next pointer in the response', 'sd_next_path', p.next_path || '', { ph: 'links.next  or  next_cursor', help: 'Dotted path to a link/URL or a cursor token.' }),
            field('Items in each page', 'sd_items_path', p.items_path || '', { ph: 'data  or  items', help: 'Blank if the response itself is the list.' }),
            field('Cursor parameter', 'sd_cursor', p.cursor_param || '', { ph: 'cursor  (blank if the pointer is a link)', help: 'Only when the pointer is a token to send as ?param=…' })),
          captures.el],
        read: () => { if (!getSteps.length) throw new Error('Add an earlier GET step to page through first.'); return finish({ params: { from_step: $('sd_from').value, next_path: $('sd_next_path').value.trim(), items_path: $('sd_items_path').value.trim(), cursor_param: $('sd_cursor').value.trim(), max_pages: parseInt($('sd_max').value, 10) } }); },
      };
    }

    // ---------------------------------------------------------------- find
    if (kind === 'find') {
      return {
        nodes: [helpBox('Searches a list an earlier step produced for the item whose field matches, and returns that item — then capture what you need from it, e.g. ' +
          '<span class="mono">product_id ← id</span>, for the next call.'),
          h('div', { class: 'row g-2' }, nameField,
            field('List to search', 'sd_source', p.source || '', { ph: 'list_items.items', col: 'col-md-8', help: 'Step name, then the dotted path to the list. A next-page step\'s list is name.items; a file preview\'s is name.rows.' }),
            h('div', { class: 'col-12' }, sourceChips(steps, step, 'sd_source')),
            field('Field of each item', 'sd_match_field', p.match_field || '', { ph: 'sku  or  attributes.code', col: 'col-md-4', help: 'Blank compares the item itself.' }),
            selectField('Operator', 'sd_operator', FIND_OPS, p.operator || 'equals', { col: 'col-md-3' }),
            field('Value', 'sd_value', p.value || '', { ph: '{{wanted_sku}}', col: 'col-md-5', help: 'Variables allowed.' }),
            selectField('Keep', 'sd_pick', [['first', 'the first match'], ['all', 'every match (a list)']], p.pick || 'first', { col: 'col-md-4' }),
            selectField('If nothing matches', 'sd_on_missing', [['fail', 'stop the chain with an error'], ['null', 'carry on with an empty result']], p.on_missing || 'fail', { col: 'col-md-8' })),
          captures.el],
        read: () => finish({ params: { source: $('sd_source').value.trim(), match_field: $('sd_match_field').value.trim(), operator: $('sd_operator').value, value: $('sd_value').value, pick: $('sd_pick').value, on_missing: $('sd_on_missing').value } }),
      };
    }

    // --------------------------------------------------------------- check
    if (kind === 'check') {
      const valueRow = field('Expected value', 'sd_value', p.value || '', { ph: 'application/json', col: 'col-md-6', help: 'Ignored for exists / is missing. Variables allowed.' });
      const opSel = selectField('Header', 'sd_operator', CHECK_OPS, p.operator || 'exists', { col: 'col-md-4' });
      const syncValue = () => { const off = ['exists', 'missing'].includes($('sd_operator').value); $('sd_value').disabled = off; };
      setTimeout(() => { const el = $('sd_operator'); if (el) { el.addEventListener('change', syncValue); syncValue(); } }, 0);
      return {
        nodes: [helpBox('Checks a response <b>header</b> of an earlier HTTP call — e.g. Content-Type, a rate-limit counter, or that a Location header came back. ' +
          'The header name is case-insensitive. The header\'s value is available as <span class="mono">{{name.value}}</span> (capture it below to reuse it).'),
          upstreamNote(),
          h('div', { class: 'row g-2' }, nameField,
            selectField('Read the response of', 'sd_from', upstream.map(x => [x.name, `${x.name}  (${x.method} ${x.path})`]), defaultUpstream, { col: 'col-md-8' }),
            field('Header name', 'sd_header', p.header || '', { ph: 'Content-Type', col: 'col-md-4' }), opSel, valueRow,
            selectField('If the check fails', 'sd_on_fail', [['fail', 'stop the chain with an error'], ['warn', 'mark the step with a warning and carry on']], p.on_fail || 'fail', { col: 'col-md-12' })),
          captures.el],
        read: () => { needUpstream('This header check'); return finish({ params: { from_step: $('sd_from').value, header: $('sd_header').value.trim(), operator: $('sd_operator').value, value: $('sd_value').value, on_fail: $('sd_on_fail').value } }); },
      };
    }

    // ---------------------------------------------------------------- file
    if (kind === 'file') {
      const box = h('div', {});
      const previewBox = h('div', { class: 'st-side-scroll mt-2', style: 'max-height:230px' });
      const status = h('div', { class: 'text-dim small mt-1' });
      const entitySel = h('select', { class: 'form-select form-select-sm', id: 'sd_entity', disabled: true, html: '<option value="">Loading files…</option>' });
      const limit = field('Rows to read', 'sd_limit', p.limit ?? 10, { type: 'number', min: '1', max: '100', mono: false, col: 'col-md-3', help: 'Up to 100.' });

      const preview = async () => {
        previewBox.replaceChildren(); status.textContent = '';
        const id = entitySel.value;
        if (!id) return;
        status.textContent = 'Reading the file…';
        try {
          const d = await S.api(`/api/entities/${id}/file-preview/?limit=${Math.min(Math.max(parseInt($('sd_limit').value, 10) || 10, 1), 100)}`);
          previewBox.replaceChildren(K.rowsTable(d.columns, d.rows));
          status.textContent = `Showing ${d.rows.length} of ${d.count} rows · ${d.columns.length} columns`;
        } catch (e) { status.textContent = ''; previewBox.replaceChildren(h('div', { class: 'st-error-box' }, h('i', { class: 'bi-exclamation-triangle-fill' }), e.message)); }
      };
      Promise.resolve(ctx.fileEntities()).then(list => {
        entitySel.innerHTML = list.length
          ? '<option value="">Choose a file…</option>' + list.map(e => `<option value="${e.id}" ${e.id === p.entity ? 'selected' : ''}>${S.esc(e.name)}  (${S.esc(e.connection_name)})</option>`).join('')
          : '<option value="">No uploaded files yet</option>';
        entitySel.disabled = !list.length;
        if (entitySel.value) preview();
      }).catch(() => { entitySel.innerHTML = '<option value="">Could not load files</option>'; });
      entitySel.addEventListener('change', preview);
      setTimeout(() => { const l = $('sd_limit'); if (l) l.addEventListener('change', preview); }, 0);

      box.append(helpBox('Reads the first rows of an uploaded CSV / Excel file and makes them available to later steps — ' +
        '<span class="mono">{{name.rows}}</span>, <span class="mono">{{name.columns}}</span>, <span class="mono">{{name.count}}</span> — e.g. search them with a <b>Find in list</b> step. ' +
        'Upload files from a connection\'s “From file” tab.'),
        h('div', { class: 'row g-2' }, nameField,
          h('div', { class: 'col-md-5' }, h('label', { class: 'form-label', for: 'sd_entity', text: 'File' }), entitySel), limit),
        h('div', { class: 'mt-2 d-flex align-items-center gap-2' }, h('button', { type: 'button', class: 'btn btn-sm btn-outline-secondary', onclick: preview }, h('i', { class: 'bi-eye me-1' }), 'Preview'), status),
        previewBox, captures.el);
      return {
        nodes: [box],
        read: () => { if (!entitySel.value) throw new Error('Choose a file to read.'); return finish({ params: { entity: Number(entitySel.value), limit: parseInt($('sd_limit').value, 10) } }); },
      };
    }

    // ---------------------------------------------------------------- http
    const preset = ctx.preset || {};
    const defaults = { method: preset.method || 'GET', path: '', body: '', headers: [], query_params: [], is_async: !!preset.async, async_poll_path: '', async_poll_method: 'GET', async_condition_path: '', async_condition_value: '', async_interval_seconds: 2, async_timeout_seconds: 60, async_result_path: '', timeout_seconds: null };
    const h_ = isNew ? defaults : s;
    const headersBlock = pairsBlock('Headers', 'No extra headers. Add one to send it with this request (e.g. Authorization: Bearer {{token}}).', h_.headers,
      { namePh: 'Header-Name', valuePh: 'value or {{placeholder}}', datalist: 'st-common-headers' });
    const paramsBlock = pairsBlock('Query parameters', 'No query parameters. Add one to send ?name=value with this request (e.g. limit = 50).', h_.query_params,
      { namePh: 'name', valuePh: 'value or {{placeholder}}' });
    const headerNames = h('datalist', { id: 'st-common-headers' }, COMMON_HEADERS.map(n => h('option', { value: n })));
    const asyncField = (label, id, val, o = {}) => field(label, id, val, o);
    const asyncBox = h('div', { class: `row g-2 mt-1 ${h_.is_async ? '' : 'd-none'}` },
      asyncField('Poll path', 'sd_poll_path', h_.async_poll_path, { ph: '/exports/{{name.job_id}}/status', col: 'col-md-8' }),
      selectField('Poll method', 'sd_poll_method', METHODS.map(m => [m, m]), h_.async_poll_method, { col: 'col-md-4' }),
      asyncField('Condition path', 'sd_cond_path', h_.async_condition_path, { ph: 'status' }),
      asyncField('Condition value', 'sd_cond_value', h_.async_condition_value, { ph: 'completed' }),
      asyncField('Interval (s)', 'sd_interval', h_.async_interval_seconds, { type: 'number', step: '0.5', mono: false }),
      asyncField('Timeout (s)', 'sd_async_timeout', h_.async_timeout_seconds, { type: 'number', step: '1', mono: false, help: 'How long to keep polling before giving up.' }),
      asyncField('Result path (optional)', 'sd_result_path', h_.async_result_path, { ph: '/exports/{{name.job_id}}/download', col: 'col-12' }));
    return {
      nodes: [
        h('div', { class: 'row g-2' },
          nameField,
          ctx.connections ? h('div', { class: 'col-md-4' }, h('label', { class: 'form-label', for: 'sd_connection', text: 'Connection' }),
            h('select', { class: 'form-select form-select-sm', id: 'sd_connection', html: '<option value="">Choose a connection…</option>' + ctx.connections.map(c => `<option value="${c.id}" ${c.id === h_.connection ? 'selected' : ''}>${S.esc(c.name)}</option>`).join('') }),
            h('div', { class: 'form-text', text: 'The API this request calls.' })) : null,
          selectField('Method', 'sd_method', METHODS.map(m => [m, m]), h_.method, { col: 'col-md-2' }),
          field('Path', 'sd_path', h_.path, { ph: '/customers/{{create_customer.id}}', col: ctx.connections ? 'col-md-6' : 'col-md-4' }),
          field('Timeout (s)', 'sd_timeout', h_.timeout_seconds ?? '', { type: 'number', step: '1', min: '0.5', mono: false, col: 'col-md-2', ph: '30', help: 'Give up after…' }),
          h('div', { class: 'col-12' }, h('label', { class: 'form-label' }, 'Body ', h('span', { class: 'text-dim', text: '(optional JSON — placeholders allowed)' })),
            h('textarea', { class: 'form-control form-control-sm mono', id: 'sd_body', rows: '3', placeholder: '{"customer_id": {{create_customer.id}}}', text: h_.body || '' }))),
        h('div', { class: 'row g-3' }, h('div', { class: 'col-lg-6' }, headersBlock.el, headerNames), h('div', { class: 'col-lg-6' }, paramsBlock.el)),
        captures.el,
        h('fieldset', {},
          h('legend', { text: 'Asynchronous step' }),
          h('label', { class: 'd-flex gap-2 align-items-center' }, h('input', { type: 'checkbox', class: 'form-check-input mt-0', id: 'sd_async', checked: !!h_.is_async, onchange: e => asyncBox.classList.toggle('d-none', !e.target.checked) }),
            'This call only starts a job — poll until it finishes'),
          asyncBox)],
      read: () => {
        const path = $('sd_path').value.trim();
        const body = $('sd_body').value.trim();
        if (!path) throw new Error('Path is required.');
        if (ctx.connections && !$('sd_connection').value) throw new Error('Choose the connection this request calls.');
        if (body) { try { JSON.parse(body.replace(/\{\{[^}]*\}\}/g, 'null')); } catch (e) { throw new Error('Body must be valid JSON (placeholders aside).'); } }
        const timeout = $('sd_timeout').value.trim();
        return finish({
          method: $('sd_method').value, path, body, timeout_seconds: timeout === '' ? null : parseFloat(timeout),
          headers: headersBlock.read(), query_params: paramsBlock.read(),
          ...(ctx.connections ? { connection: Number($('sd_connection').value) } : {}),
          is_async: $('sd_async').checked,
          async_poll_path: $('sd_poll_path').value.trim(), async_poll_method: $('sd_poll_method').value,
          async_condition_path: $('sd_cond_path').value.trim(), async_condition_value: $('sd_cond_value').value.trim(),
          async_interval_seconds: $('sd_interval').value, async_timeout_seconds: $('sd_async_timeout').value,
          async_result_path: $('sd_result_path').value.trim(),
        });
      },
    };
  };

  // ── Result rendering ─────────────────────────────────────────────────────
  K.rowsTable = (columns, rows) => h('table', { class: 'st-dtable' },
    h('thead', {}, h('tr', {}, columns.map(c => h('th', { text: c })))),
    h('tbody', {}, rows.map(r => h('tr', {}, columns.map(c => h('td', {}, S.viz.cell(r[c])))))));

  const kv = (label, node) => [h('div', { class: 'st-kv-label', text: label }), node];
  const pre = value => h('pre', { class: 'st-pre', text: typeof value === 'string' ? value : JSON.stringify(value, null, 2) });

  // The expanded row under a step result.
  K.resultDetail = r => {
    const d = r.detail || {};
    const out = [];
    if (r.error) out.push(h('div', { class: 'text-danger small mb-1', text: `Error: ${r.error}` }));

    if (r.kind === 'check' && d.header) {
      out.push(...kv('Header check', h('table', { class: 'st-table', style: 'max-width:520px' }, h('tbody', {},
        [['Read from', d.from_step], ['Header', d.header], ['Condition', `${opLabel(CHECK_OPS, d.operator)}${d.expected ? ' “' + d.expected + '”' : ''}`],
          ['Actual value', d.actual == null ? '(header not present)' : d.actual], ['Result', d.passed ? 'passed' : 'failed']]
          .map(([k, v]) => h('tr', {}, h('td', { class: 'text-dim', text: k }), h('td', { class: 'mono', text: v })))))));
    } else if (r.kind === 'find' && d.criteria) {
      out.push(h('div', { class: 'small mb-1' }, `Searched ${d.searched} item(s) in `, h('span', { class: 'mono', text: d.source }), ` for `, h('span', { class: 'mono', text: d.criteria }), ` — ${d.matched} matched.`));
      if (r.response_json != null) out.push(...kv('Result', pre(r.response_json)));
    } else if (r.kind === 'file' && r.response_json && r.response_json.rows) {
      out.push(h('div', { class: 'small mb-1', text: `${r.response_json.entity}: showing ${r.response_json.rows.length} of ${r.response_json.count} rows` }),
        h('div', { class: 'st-side-scroll', style: 'max-height:220px' }, K.rowsTable(r.response_json.columns, r.response_json.rows)));
    } else if (r.kind === 'next' && d.pages) {
      out.push(h('div', { class: 'small mb-1' }, `${d.pages} page(s) fetched from `, h('span', { class: 'mono', text: d.from_step }), ` → ${d.items} item(s).`,
        d.more ? h('span', { class: 'text-danger ms-1', text: `Stopped at the ${d.max_pages}-page limit — more pages exist.` }) : null));
      if (r.response_json) out.push(...kv('Items', pre((r.response_json.items || []).slice(0, 50))));
    } else if (r.kind === 'wait') {
      out.push(h('div', { class: 'small', text: `Paused for ${d.waited}s.` }));
    } else {
      if (r.poll_attempts) out.push(h('div', { class: 'text-dim small mb-1', text: `Polled ${r.poll_attempts} time${r.poll_attempts > 1 ? 's' : ''} before completing.` }));
      if (r.resolved_body) out.push(...kv('Request body', pre(r.resolved_body)));
      const sent = (title, obj) => Object.keys(obj || {}).length && out.push(...kv(title, h('table', { class: 'st-table', style: 'max-width:640px' }, h('tbody', {},
        Object.entries(obj).map(([k, v]) => h('tr', {}, h('td', { class: 'mono', text: k }), h('td', { class: 'mono text-dim', text: v })))))));
      sent('Request headers (secrets masked)', d.request_headers);
      sent('Query parameters', d.request_params);
      const headers = Object.entries(r.response_headers || {});
      if (headers.length) out.push(...kv('Response headers', h('table', { class: 'st-table', style: 'max-width:640px' }, h('tbody', {}, headers.map(([k, v]) => h('tr', {}, h('td', { class: 'mono', text: k }), h('td', { class: 'mono text-dim', text: v })))))));
      if (r.response_json != null) out.push(...kv(`Response (available to later steps as ${r.name})`, pre(r.response_json)));
    }
    if (r.captured_variables && Object.keys(r.captured_variables).length) out.push(...kv('Variables captured here', pre(r.captured_variables)));
    return out;
  };
})();
