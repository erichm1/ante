/* Templates — starter recipes for the sidebar. The server (studio/templates.py)
   describes each one as metadata + a parameter schema; this file renders the
   gallery, builds the form from that schema, and hands the answers back to
   POST /studio/templates/<slug>/, which creates an ordinary mapping / chain / plan.

   Parameter types the form understands:
     text · textarea · select                      plain inputs
     connection                                    a connection picker
     entity      (+ connection_from, file_only)    an entity of the chosen connection
     entities                                      tick any number of entities, grouped by connection
     mapping                                       an existing mapping
     steps                                         an ordered list of mappings / chains (plans)
   Templates flagged `action` only *start* something: they open the chosen mapping
   and its run dialog rather than creating anything. */
(function () {
  'use strict';
  const S = window.Studio;
  const h = S.h;

  S.templates = [];
  const KIND_LABEL = { mapping: 'Migration', chain: 'Chain', plan: 'Plan', run: 'Run' };
  const KIND_ORDER = ['mapping', 'chain', 'plan', 'run'];
  const KIND_HELP = {
    mapping: 'Wire two systems together', chain: 'Script a sequence of API calls',
    plan: 'Run several things in order', run: 'Start something that already exists',
  };
  S.templateKindLabel = KIND_LABEL;

  S.loadTemplates = async function () {
    try { S.templates = (await S.api('/studio/templates/')).templates; } catch (e) { S.templates = []; }
    S.renderTree();
  };

  // ── Entities, fetched once per connection ────────────────────────────────
  const entityCache = new Map();
  const entitiesOf = async connectionId => {
    if (!entityCache.has(connectionId)) entityCache.set(connectionId, S.apiList(`/api/entities/?connection=${connectionId}`));
    return entityCache.get(connectionId);
  };
  // A template can be filled in right after the user discovers new entities elsewhere.
  S.forgetEntityCache = () => entityCache.clear();

  const opt = (value, text, selected) => `<option value="${S.esc(value)}" ${selected ? 'selected' : ''}>${S.esc(text)}</option>`;
  const placeholder = text => `<option value="">${S.esc(text)}</option>`;

  // ── Form builder ─────────────────────────────────────────────────────────
  // Returns { el, read() }: read() collects the answers (throws a user-facing Error when a
  // required one is missing). Fields that depend on another (entity ← connection) re-fill
  // themselves when it changes.
  function buildForm(template) {
    const controls = {};      // param name -> { el, read }
    const root = h('div', {});

    template.params.forEach(param => {
      const id = `tp_${param.name}`;
      let control;

      if (param.type === 'textarea') {
        const el = h('textarea', { class: 'form-control form-control-sm mono', id, rows: '3' });
        el.value = param.default || '';
        control = { el, read: () => el.value };

      } else if (param.type === 'select') {
        const opts = param.options.map(o => typeof o === 'string' ? { value: o, label: o } : o);
        const el = h('select', { class: 'form-select form-select-sm', id, html: opts.map(o => opt(o.value, o.label, o.value === param.default)).join('') });
        control = { el, read: () => el.value };

      } else if (param.type === 'connection') {
        const el = h('select', { class: 'form-select form-select-sm', id, html: placeholder('Choose a connection…') + S.tree.connections.map(c => opt(c.id, c.name)).join('') });
        control = { el, read: () => (el.value ? Number(el.value) : null) };

      } else if (param.type === 'mapping') {
        const el = h('select', { class: 'form-select form-select-sm', id, html: placeholder('Choose a mapping…') + S.tree.mappings.map(m => opt(m.id, m.name)).join('') });
        control = { el, read: () => (el.value ? Number(el.value) : null) };

      } else if (param.type === 'entity') {
        const el = h('select', { class: 'form-select form-select-sm', id, disabled: true, html: placeholder('Choose a connection first') });
        const fill = async () => {
          const parent = controls[param.connection_from];
          const connectionId = parent && parent.read();
          if (!connectionId) { el.innerHTML = placeholder('Choose a connection first'); el.disabled = true; return; }
          el.disabled = true;
          el.innerHTML = placeholder('Loading…');
          try {
            let list = await entitiesOf(connectionId);
            if (param.file_only) list = list.filter(e => e.has_file);
            el.innerHTML = list.length
              ? placeholder('Choose an entity…') + list.map(e => opt(e.id, `${e.name}  (${e.fields.length} fields)`)).join('')
              : placeholder(param.file_only ? 'No file-backed entities on this connection' : 'No entities discovered on this connection yet');
            el.disabled = !list.length;
          } catch (e) { el.innerHTML = placeholder('Could not load entities'); }
        };
        control = { el, read: () => (el.value ? Number(el.value) : null), fill };

      } else if (param.type === 'entities') {
        const el = h('div', { class: 'st-checklist', id }, h('div', { class: 'text-dim small', text: 'Loading entities…' }));
        (async () => {
          try {
            const groups = await Promise.all(S.tree.connections.map(async c => [c, await entitiesOf(c.id)]));
            const shown = groups.filter(([, list]) => list.length);
            el.replaceChildren(...(shown.length ? shown.flatMap(([c, list]) => [
              h('div', { class: 'st-check-group', text: c.name }),
              ...list.map(e => h('label', { class: 'd-flex gap-2 align-items-center' },
                h('input', { type: 'checkbox', class: 'form-check-input mt-0', value: e.id }), e.name, h('span', { class: 'text-dim small', text: `${e.fields.length} fields` }))),
            ]) : [h('div', { class: 'text-dim small', text: 'No entities discovered yet — discover some from a connection page.' })]));
          } catch (e) { el.textContent = 'Could not load entities.'; }
        })();
        control = { el, read: () => [...el.querySelectorAll('input:checked')].map(i => Number(i.value)) };

      } else if (param.type === 'steps') {
        const rows = h('div', {});
        const choices = [
          ...S.tree.mappings.map(m => ({ v: `mapping:${m.id}`, t: m.name, g: 'Mappings' })),
          ...S.tree.chains.map(c => ({ v: `chain:${c.id}`, t: c.name, g: 'Chains' })),
        ];
        const addRow = () => {
          const sel = h('select', { class: 'form-select form-select-sm',
            html: placeholder('Choose a mapping or chain…') + ['Mappings', 'Chains'].map(g => `<optgroup label="${g}">${choices.filter(c => c.g === g).map(c => opt(c.v, c.t)).join('')}</optgroup>`).join('') });
          const row = h('div', { class: 'st-dlg-row', dataset: { step: '1' } }, h('span', { class: 'st-step-n mono text-dim' }), sel,
            h('button', { type: 'button', class: 'btn btn-sm btn-outline-secondary', title: 'Move up', html: '&uarr;', onclick: () => { if (row.previousElementSibling) rows.insertBefore(row, row.previousElementSibling); number(); } }),
            h('button', { type: 'button', class: 'btn btn-sm btn-outline-secondary', title: 'Move down', html: '&darr;', onclick: () => { if (row.nextElementSibling) rows.insertBefore(row.nextElementSibling, row); number(); } }),
            h('button', { type: 'button', class: 'btn btn-sm btn-outline-danger', title: 'Remove', html: '&times;', onclick: () => { row.remove(); number(); } }));
          rows.append(row);
          number();
        };
        const number = () => [...rows.children].forEach((r, i) => { r.querySelector('.st-step-n').textContent = `${i + 1}.`; });
        const el = h('div', { id }, rows, h('button', { type: 'button', class: 'btn btn-sm btn-outline-secondary', text: '+ Add step', onclick: addRow }));
        if (!choices.length) rows.append(h('div', { class: 'text-dim small mb-1', text: 'No mappings or chains yet — create some first.' }));
        else addRow();
        control = {
          el,
          read: () => [...rows.querySelectorAll('[data-step]')].map(r => r.querySelector('select').value).filter(Boolean)
            .map(v => { const [kind, i] = v.split(':'); return { kind, id: Number(i) }; }),
        };

      } else {   // text
        const el = h('input', { class: 'form-control form-control-sm', id, placeholder: param.placeholder || '' });
        el.value = param.default || '';
        if (/path/.test(param.name)) el.classList.add('mono');
        control = { el, read: () => el.value.trim() };
      }

      controls[param.name] = control;
      root.append(h('div', { class: 'mb-3' },
        h('label', { class: 'form-label', for: id }, param.label, param.required ? h('span', { class: 'text-danger ms-1', text: '*' }) : null),
        control.el, param.help ? h('div', { class: 'form-text', text: param.help }) : null));
    });

    // Wire dependents (an entity list follows its connection picker).
    template.params.filter(p => p.type === 'entity').forEach(p => {
      const parent = controls[p.connection_from];
      if (parent) parent.el.addEventListener('change', () => controls[p.name].fill());
    });

    return {
      el: root,
      read() {
        const values = {};
        template.params.forEach(p => {
          const v = controls[p.name].read();
          const empty = v == null || v === '' || (Array.isArray(v) && !v.length);
          if (p.required && empty) throw new Error(`“${p.label}” is required.`);
          values[p.name] = v;
        });
        return values;
      },
    };
  }

  // ── The dialog for one template ──────────────────────────────────────────
  S.templateDialog = function (template) {
    const form = buildForm(template);
    const isAction = !!template.action;
    S.dialog({
      title: template.title, size: 'modal-lg',
      body: h('div', {},
        h('p', { class: 'text-dim small', text: template.blurb }),
        form.el,
        isAction ? null : h('div', { class: 'form-text', text: 'Everything created is an ordinary mapping, chain or plan — rename, rewire or delete it any time.' })),
      actions: [{ label: 'Cancel' }, {
        label: isAction ? (template.action === 'run' ? 'Run now' : 'Continue…') : 'Create', primary: true,
        onClick: async dlg => {
          let values;
          try { values = form.read(); } catch (e) { dlg.error(e.message); return false; }

          if (isAction) {
            const ed = await S.openDoc('mapping', values.mapping);
            if (ed) template.action === 'run' ? ed.run() : ed.runOptions();
            return;
          }
          const made = await S.api(`/studio/templates/${template.slug}/`, { method: 'POST', body: values });
          S.forgetEntityCache();
          await S.refreshTree();
          await S.openDoc(made.kind, made.id);
          S.toast(made.notes.join('  ·  '), 'ok', 7000);
        },
      }],
    });
  };

  // ── Gallery: every template, grouped ─────────────────────────────────────
  S.templateGallery = function () {
    const dlg = S.dialog({
      title: 'Start from a template', size: 'modal-lg',
      body: h('div', { class: 'st-tpl-gallery' }, KIND_ORDER.map(kind => {
        const items = S.templates.filter(t => t.kind === kind);
        if (!items.length) return null;
        return h('div', {},
          h('div', { class: 'st-tpl-group' }, h('b', { text: KIND_LABEL[kind] }), h('span', { text: KIND_HELP[kind] })),
          h('div', { class: 'st-tpl-grid' }, items.map(t => h('button', {
            type: 'button', class: 'st-tpl-card',
            onclick: () => { dlg.el.addEventListener('hidden.bs.modal', () => S.templateDialog(t), { once: true }); dlg.close(); },
          }, h('span', { class: 'st-tpl-icon' }, h('i', { class: t.icon })), h('span', { class: 'st-tpl-title', text: t.title }), h('span', { class: 'st-tpl-blurb', text: t.blurb })))));
      })),
    });
  };
})();
