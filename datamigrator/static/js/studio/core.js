/* Studio core — shared helpers, the document (tab) manager, the explorer tree
   and the toolbar. Editors (mapping/chain/plan) live in their own files and
   register themselves on Studio.editorClasses; boot.js wires the DOM up last.

   Nothing here talks to a private endpoint: every document is edited through
   the same REST API the classic pages use (/api/mappings, /api/chains,
   /api/plans, /api/runs, …) — the only Studio-specific one is /studio/tree/,
   which feeds the explorer. */
(function () {
  'use strict';

  const S = window.Studio = {
    editorClasses: {},
    docs: new Map(),      // key ("mapping:3") -> editor instance
    active: null,         // the active editor
    tree: { mappings: [], chains: [], plans: [], connections: [], recent_runs: [] },
    icons: { mapping: 'bi-diagram-2', chain: 'bi-link-45deg', plan: 'bi-list-ol', connection: 'bi-hdd-network' },
    kindLabel: { mapping: 'Mapping', chain: 'Chain', plan: 'Plan' },
  };

  // ── Small helpers ───────────────────────────────────────────────────────

  S.h = function (tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') e.className = v;
      else if (k === 'dataset') Object.assign(e.dataset, v);
      else if (k === 'html') e.innerHTML = v;
      else if (k === 'text') e.textContent = v;
      else if (k.startsWith('on') && typeof v === 'function') e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? '' : v);
    }
    kids.flat(Infinity).forEach(c => { if (c != null && c !== false) e.append(c); });
    return e;
  };
  const h = S.h;

  S.esc = s => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  S.store = {
    get(key, fallback) { try { const v = localStorage.getItem(key); return v == null ? fallback : JSON.parse(v); } catch (e) { return fallback; } },
    set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* private mode etc. — state just won't persist */ } },
  };

  S.errMsg = d => {
    if (!d) return '';
    if (typeof d === 'string') return d;
    if (d.error) return d.error;
    if (d.detail) return d.detail;
    return Object.entries(d).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(' ') : v}`).join(' · ');
  };

  // opts: { method, body (JSON), form (FormData) }. Throws Error(message) on non-2xx.
  S.api = async function (url, opts = {}) {
    const init = { method: opts.method || 'GET', headers: {} };
    if (opts.form) init.body = opts.form;
    else if (opts.body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.body); }
    const resp = await fetch(url, init);
    const text = await resp.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
    if (!resp.ok) {
      const err = new Error(S.errMsg(data) || `Request failed (${resp.status})`);
      err.status = resp.status;
      err.data = data;
      err.permissionDenied = !!(data && data.permission_denied);     // the server's "you lack that module" 403
      throw err;
    }
    return data;
  };

  // Follows DRF pagination (`next`) so callers get the full list.
  S.apiList = async function (url) {
    let out = [];
    let next = url;
    while (next) {
      const d = await S.api(next);
      if (Array.isArray(d)) return out.concat(d);
      out = out.concat(d.results || []);
      next = d.next ? (u => u.pathname + u.search)(new URL(d.next, location.origin)) : null;
    }
    return out;
  };

  S.toast = function (message, kind, ms) {
    let box = document.querySelector('.st-toasts');
    if (!box) { box = h('div', { class: 'st-toasts' }); document.body.append(box); }
    const t = h('div', { class: `st-toast ${kind || ''}`, text: message });
    box.append(t);
    setTimeout(() => t.remove(), ms || (kind === 'err' ? 6500 : 3200));
  };
  // A missing permission is a warning, not an error: nothing is broken, the user just isn't allowed.
  S.warn = message => S.toast(message, 'warn', 7000);
  S.fail = e => (e && e.permissionDenied ? S.warn(e.message) : S.toast(e && e.message ? e.message : String(e), 'err'));

  // Which modules the signed-in user has (the tree endpoint reports them; administrators have all).
  const MODULE_LABEL = { mappings: 'Mappings', jobs: 'Jobs (runs)', plans: 'Plans', chains: 'Chains', studio: 'Studio', appstore: 'App Store & connections' };
  S.can = key => { const m = S.tree && S.tree.modules; return !m || m.includes(key); };
  // Guard for an entry point: true when the user may proceed, otherwise shows the warning and returns false.
  S.need = (...keys) => {
    const missing = keys.filter(k => !S.can(k));
    if (!missing.length) return true;
    S.warn(`You don't have permission to use ${missing.map(k => MODULE_LABEL[k] || k).join(' or ')}. Ask an administrator to enable it for your account.`);
    return false;
  };

  S.fmtTime = iso => iso ? new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—';
  S.fmtDateTime = iso => iso ? new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—';
  S.duration = (a, b) => {
    if (!a) return '—';
    const sec = Math.max(0, Math.round(((b ? new Date(b) : new Date()) - new Date(a)) / 1000));
    return sec < 60 ? `${sec}s` : `${Math.floor(sec / 60)}m ${sec % 60}s`;
  };
  S.ago = iso => {
    const sec = Math.round((Date.now() - new Date(iso)) / 1000);
    if (sec < 45) return 'now';
    if (sec < 3600) return `${Math.round(sec / 60)}m`;
    if (sec < 86400) return `${Math.round(sec / 3600)}h`;
    return `${Math.round(sec / 86400)}d`;
  };

  const CHIP = { success: 'ok', completed: 'ok', ok: 'ok', failed: 'err', error: 'err' };
  S.chip = (status, label) => h('span', { class: `status-chip ${status === 'draft' || status === 'not started' ? '' : (CHIP[status] || 'warn')}`, text: label || status });

  // ── Dialog (Bootstrap modal built on demand) ────────────────────────────
  // actions: [{ label, cls, primary, onClick(dlg) }] — onClick may be async and
  // returns false to keep the dialog open (e.g. validation failed).
  S.dialog = function ({ title, body, size, actions }) {
    const errBox = h('div', { class: 'alert alert-danger d-none py-2 small' });
    const bodyEl = h('div', { class: 'modal-body st-dlg' }, errBox, typeof body === 'string' ? h('div', { html: body }) : body);
    const footer = h('div', { class: 'modal-footer' });
    const el = h('div', { class: 'modal fade', tabindex: '-1' },
      h('div', { class: `modal-dialog modal-dialog-centered ${size || ''}` },
        h('div', { class: 'modal-content' },
          h('div', { class: 'modal-header' },
            h('h5', { class: 'modal-title', text: title }),
            h('button', { type: 'button', class: 'btn-close', 'data-bs-dismiss': 'modal' })),
          bodyEl, footer)));
    document.body.append(el);
    const modal = new bootstrap.Modal(el);
    const dlg = {
      el, body: bodyEl, modal,
      close() { modal.hide(); },
      error(msg) { errBox.textContent = msg || ''; errBox.classList.toggle('d-none', !msg); },
      $(sel) { return el.querySelector(sel); },
    };
    let primaryBtn = null;
    (actions || [{ label: 'Close', cls: 'btn-outline-secondary' }]).forEach(a => {
      const btn = h('button', { type: 'button', class: `btn ${a.cls || (a.primary ? 'btn-signal' : 'btn-outline-secondary')}`, text: a.label });
      if (a.primary) primaryBtn = btn;
      btn.addEventListener('click', async () => {
        if (!a.onClick) { dlg.close(); return; }
        dlg.error('');
        btn.disabled = true;
        try {
          if ((await a.onClick(dlg)) !== false) dlg.close();
        } catch (e) {
          dlg.error(e.message || String(e));
        } finally {
          btn.disabled = false;
        }
      });
      footer.append(btn);
    });
    el.addEventListener('keydown', e => {
      if (e.key === 'Enter' && primaryBtn && e.target.matches('input:not([type=checkbox]):not([type=file])')) { e.preventDefault(); primaryBtn.click(); }
    });
    el.addEventListener('hidden.bs.modal', () => { modal.dispose(); el.remove(); });
    el.addEventListener('shown.bs.modal', () => { const f = el.querySelector('input:not([type=checkbox]):not([type=file]), textarea'); if (f && !f.dataset.noFocus) f.focus(); });
    modal.show();
    return dlg;
  };

  // ── Context menu ────────────────────────────────────────────────────────
  let openMenu = null;
  S.closeMenu = () => { if (openMenu) { openMenu.remove(); openMenu = null; } };
  // items: [{ label, icon, danger, onClick }] or { head: 'text' }
  S.menu = function (x, y, items) {
    S.closeMenu();
    const m = h('div', { class: 'st-menu' }, items.map(it => it.head
      ? h('div', { class: 'st-menu-head', text: it.head })
      : h('button', { class: it.danger ? 'danger' : '', onclick: () => { S.closeMenu(); it.onClick(); } },
        it.icon ? h('i', { class: it.icon }) : null, it.label)));
    document.body.append(m);
    const r = m.getBoundingClientRect();
    m.style.left = Math.max(4, Math.min(x, innerWidth - r.width - 4)) + 'px';
    m.style.top = Math.max(4, Math.min(y, innerHeight - r.height - 4)) + 'px';
    openMenu = m;
  };
  document.addEventListener('mousedown', e => { if (openMenu && !openMenu.contains(e.target)) S.closeMenu(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') S.closeMenu(); });

  // ── Tab strip used inside the results panel ─────────────────────────────
  // pages: [{ id, label }] → { root, page(id) -> element, select(id), setNote(text) }
  S.makeTabs = function (pages) {
    const note = h('span', { class: 'st-rtab-note' });
    const bar = h('div', { class: 'st-rtab-bar' });
    const body = h('div', { class: 'st-rtab-body' });
    const els = {};
    const btns = {};
    pages.forEach(p => {
      els[p.id] = h('div', { class: 'st-rtab-page' });
      btns[p.id] = h('button', { text: p.label, onclick: () => api.select(p.id) });
      bar.append(btns[p.id]);
      body.append(els[p.id]);
    });
    bar.append(note);
    const api = {
      root: h('div', { class: 'st-rtabs' }, bar, body),
      page: id => els[id],
      current: pages[0].id,
      onSelect: null,        // optional (id) => void, e.g. to load a tab's data lazily
      select(id) {
        api.current = id;
        if (api.onSelect) api.onSelect(id);
        pages.forEach(p => { btns[p.id].classList.toggle('active', p.id === id); els[p.id].classList.toggle('active', p.id === id); });
      },
      setNote(t) { note.textContent = t || ''; },
    };
    api.select(pages[0].id);
    return api;
  };

  // ── Connector logos ─────────────────────────────────────────────────────
  // Every mapping / chain / plan / connection row — and the tab of any open one — is
  // drawn with the App Store icons of the systems it touches (an uploaded logo, else
  // the integration's Bootstrap icon). A mapping reads origin → destination(s).
  S.logo = l => {
    const box = h('span', { class: 'st-logo', title: l.name });
    const icon = () => h('i', { class: l.icon || 'bi-hdd-network' });
    // An uploaded logo that no longer loads (moved/deleted file) falls back to the icon class.
    box.append(l.image ? h('img', { src: l.image, alt: '', loading: 'lazy', onerror: e => e.target.replaceWith(icon()) }) : icon());
    return box;
  };

  // item: a tree row ({ logos, logos_to }); opts.kind for the fallback icon; opts.status for a corner badge.
  S.logoCluster = function (item, opts = {}) {
    const from = (item && item.logos) || [];
    const to = (item && item.logos_to) || [];
    const kids = [];
    if (!from.length && !to.length) kids.push(h('span', { class: 'st-logo generic' }, h('i', { class: S.icons[opts.kind] || 'bi-file-earmark' })));
    from.slice(0, 4).forEach(l => kids.push(S.logo(l)));
    if (to.length) {
      kids.push(h('i', { class: 'bi-arrow-right st-logo-arrow' }));
      to.slice(0, 2).forEach(l => kids.push(S.logo(l)));
      if (to.length > 2) kids.push(h('span', { class: 'st-logo-more', title: to.slice(2).map(l => l.name).join(', '), text: `+${to.length - 2}` }));
    }
    if (opts.status && opts.status !== 'draft') kids.push(h('span', { class: `st-dot badge ${opts.status}`, title: opts.status }));
    return h('span', { class: 'st-logos' }, kids);
  };

  const COLLECTION = { mapping: 'mappings', chain: 'chains', plan: 'plans' };
  S.treeItem = (kind, id) => (S.tree[COLLECTION[kind]] || []).find(d => d.id === Number(id));

  // ── Editing / deleting from anywhere (sidebar, tab, canvas node) ───────
  // Edit = open the document and show its properties dialog.
  S.editDoc = async function (kind, id) {
    const ed = await S.openDoc(kind, id);
    if (ed) ed.properties();
  };

  // Delete with a confirmation that says what else goes with it. Resolves true if deleted.
  S.deleteDoc = async function (kind, id) {
    const item = S.treeItem(kind, id);
    const name = item ? item.name : `#${id}`;
    const used = item && item.plan_steps || 0;
    const alsoPlans = used ? ` It is used by ${used} plan step${used > 1 ? 's' : ''}, which will be removed too.` : '';
    const what = {
      mapping: `mapping "${name}", with its entity pairs, field mappings and run history`,
      chain: `chain "${name}", with its steps and run history`,
      plan: `plan "${name}" and its steps (the mappings and chains it runs are kept)`,
    }[kind];
    if (!(await confirmModal(`Delete ${what}?${alsoPlans} This can't be undone.`))) return false;
    try {
      await S.api(`/api/${COLLECTION[kind]}/${id}/`, { method: 'DELETE' });
    } catch (e) { S.fail(e); return false; }
    const key = `${kind}:${id}`;
    if (S.docs.has(key)) S.closeDoc(key);
    await S.refreshTree();
    S.toast(`Deleted ${kind} "${name}".`, 'ok');
    return true;
  };

  // The actions every document row / tab offers.
  S.docMenu = function (e, kind, id) {
    e.preventDefault();
    e.stopPropagation();
    const item = S.treeItem(kind, id);
    S.menu(e.clientX, e.clientY, [
      { head: item ? item.name : `${S.kindLabel[kind]} #${id}` },
      { label: 'Open', icon: 'bi-box-arrow-in-right', onClick: () => S.openDoc(kind, id) },
      { label: `Edit ${kind}…`, icon: 'bi-pencil', onClick: () => S.editDoc(kind, id) },
      { label: `Delete ${kind}…`, icon: 'bi-trash', danger: true, onClick: () => S.deleteDoc(kind, id) },
    ]);
  };

  // ── Editor base class ───────────────────────────────────────────────────
  // Subclasses set `static kind`, implement load()/render, and may override
  // the hooks below. The base owns the tab title, the stage pane and the
  // toolbar-facing state (status text, run availability).
  S.Editor = class {
    constructor(id) {
      this.kind = new.target.kind;
      this.id = id;
      this.key = `${this.kind}:${id}`;
      this.title = `${S.kindLabel[this.kind]} #${id}`;
      this.pane = h('div', { class: 'st-pane' });
      // A stable host: editors swap their results tabs in and out of it as they load.
      this.resultsEl = h('div', { class: 'st-rhost' }, h('div', { class: 'st-empty', text: 'Loading…' }));
      this.canvas = null;          // CanvasEditor sets this — enables zoom buttons
      this.runLabel = 'Run';
      this.hasRunOptions = false;
      this.running = false;
    }
    async load() {}
    onShow() {}
    statusText() { return ''; }
    canRun() { return !this.running; }
    async run() {}
    async runOptions() {}
    // The Kill button: enabled while something of this document is running. kill() asks the server to stop it.
    canKill() { return false; }
    killMessage() { return 'Stop what is running?'; }
    async kill() {}
    renderPalette(container) { container.append(h('div', { class: 'st-pal-hint', text: 'Nothing to place on this canvas.' })); }
    properties() {}
    remove() { return S.deleteDoc(this.kind, this.id); }
    destroy() {}
    setTitle(t) { this.title = t; renderDocTabs(); }
    // Called by editors after any state change the toolbar/explorer should reflect.
    changed() { if (S.active === this) S.syncToolbar(); }
  };

  // ── Documents / tabs ────────────────────────────────────────────────────

  const docTabs = () => document.getElementById('stDocTabs');

  // Moves a tab next to another (before it, or after it when `after`); with no target it
  // goes to the end. S.docs is a Map, whose iteration order IS the tab order, so reordering
  // means rebuilding it — persistDocs() then saves that order for the next visit.
  S.moveDoc = function (key, targetKey, after) {
    if (!S.docs.has(key) || key === targetKey) return;
    const keys = [...S.docs.keys()].filter(k => k !== key);
    let at = targetKey ? keys.indexOf(targetKey) : keys.length;
    if (at < 0) at = keys.length;
    keys.splice(after && targetKey ? at + 1 : at, 0, key);
    const entries = keys.map(k => [k, S.docs.get(k)]);
    S.docs.clear();
    entries.forEach(([k, ed]) => S.docs.set(k, ed));
    renderDocTabs();
    persistDocs();
  };

  S.closeOthers = function (keepKey) { [...S.docs.keys()].filter(k => k !== keepKey).forEach(k => S.closeDoc(k)); };

  let draggingTab = null;
  let tabDnDBound = false;

  // Drag-and-drop is delegated from the strip (which outlives every re-render of its tabs).
  function bindDocTabDnD(box) {
    if (tabDnDBound) return;
    tabDnDBound = true;
    const clearMarks = () => box.querySelectorAll('.drop-before, .drop-after').forEach(t => t.classList.remove('drop-before', 'drop-after'));
    // Which tab, and which side of it, the pointer is over; null tab = the empty strip (→ end).
    const dropSpot = e => {
      const tab = e.target.closest('.st-doctab');
      if (!tab) return { tab: null, after: true };
      const r = tab.getBoundingClientRect();
      return { tab, after: e.clientX > r.left + r.width / 2 };
    };

    box.addEventListener('dragstart', e => {
      const tab = e.target.closest('.st-doctab');
      if (!tab) return;
      draggingTab = tab.dataset.key;
      e.dataTransfer.setData('application/x-ante-tab', draggingTab);
      e.dataTransfer.effectAllowed = 'move';
      requestAnimationFrame(() => tab.classList.add('dragging'));   // after the drag image is taken
    });
    box.addEventListener('dragover', e => {
      if (!draggingTab) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      clearMarks();
      const { tab, after } = dropSpot(e);
      const last = box.querySelector('.st-doctab:last-of-type');
      const mark = tab || last;
      if (mark && mark.dataset.key !== draggingTab) mark.classList.add(tab ? (after ? 'drop-after' : 'drop-before') : 'drop-after');
    });
    box.addEventListener('dragleave', e => { if (!box.contains(e.relatedTarget)) clearMarks(); });
    box.addEventListener('drop', e => {
      if (!draggingTab) return;
      e.preventDefault();
      const { tab, after } = dropSpot(e);
      const key = draggingTab;
      draggingTab = null;
      clearMarks();
      S.moveDoc(key, tab ? tab.dataset.key : null, after);
    });
    box.addEventListener('dragend', () => { draggingTab = null; clearMarks(); box.querySelectorAll('.dragging').forEach(t => t.classList.remove('dragging')); });
  }

  function tabMenu(e, ed) {
    e.preventDefault();
    const keys = [...S.docs.keys()];
    const i = keys.indexOf(ed.key);
    S.menu(e.clientX, e.clientY, [
      { head: ed.title },
      { label: `Edit ${ed.kind}…`, icon: 'bi-pencil', onClick: () => ed.properties() },
      { label: `Delete ${ed.kind}…`, icon: 'bi-trash', danger: true, onClick: () => ed.remove() },
      ...(i > 0 ? [{ label: 'Move left', icon: 'bi-arrow-left', onClick: () => S.moveDoc(ed.key, keys[i - 1], false) }] : []),
      ...(i < keys.length - 1 ? [{ label: 'Move right', icon: 'bi-arrow-right', onClick: () => S.moveDoc(ed.key, keys[i + 1], true) }] : []),
      { label: 'Close', icon: 'bi-x-lg', onClick: () => S.closeDoc(ed.key) },
      ...(keys.length > 1 ? [{ label: 'Close others', icon: 'bi-x-circle', onClick: () => S.closeOthers(ed.key) }] : []),
    ]);
  }

  function renderDocTabs() {
    const box = docTabs();
    if (!box) return;
    bindDocTabDnD(box);
    box.replaceChildren(...[...S.docs.values()].map(ed => h('div', {
      class: `st-doctab ${S.active === ed ? 'active' : ''}`, role: 'tab', title: `${S.kindLabel[ed.kind]}: ${ed.title} — drag to reorder`,
      draggable: 'true', dataset: { key: ed.key },
      onclick: () => S.activate(ed.key),
      onauxclick: e => { if (e.button === 1) S.closeDoc(ed.key); },
      oncontextmenu: e => tabMenu(e, ed),
    },
      S.logoCluster(S.treeItem(ed.kind, ed.id), { kind: ed.kind, status: ed.kind === 'plan' ? (S.treeItem('plan', ed.id) || {}).status : null }),
      h('span', { class: 'st-doctab-name', text: ed.title }),
      h('button', { class: 'st-x', title: 'Close', draggable: 'false', onclick: e => { e.stopPropagation(); S.closeDoc(ed.key); }, html: '&times;' }))));
    // With many tabs open, keep the active one visible.
    const active = box.querySelector('.st-doctab.active');
    if (active && active.scrollIntoView) active.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }

  function persistDocs() {
    S.store.set('ante_studio_state', { tabs: [...S.docs.keys()], active: S.active ? S.active.key : null });
    const url = new URL(location.href);
    if (S.active) url.searchParams.set('open', S.active.key); else url.searchParams.delete('open');
    history.replaceState(null, '', url);
  }

  // opts.run: a run id to preselect in the editor's results panel.
  S.openDoc = async function (kind, id, opts = {}) {
    const key = `${kind}:${id}`;
    let ed = S.docs.get(key);
    if (!ed) {
      const Cls = S.editorClasses[kind];
      if (!Cls) return null;
      ed = new Cls(Number(id));
      S.docs.set(key, ed);
      document.getElementById('stStage').append(ed.pane);
      S.activate(key);
      try {
        await ed.load();
      } catch (e) {
        S.docs.delete(key);
        ed.pane.remove();
        if (S.active === ed) S.active = null;
        S.activate([...S.docs.keys()].pop() || null);
        S.toast(e.status === 404 ? `${S.kindLabel[kind]} #${id} no longer exists.` : `Couldn't open ${kind} #${id}: ${e.message}`, 'err');
        return null;
      }
    } else {
      S.activate(key);
    }
    if (opts.run && ed.selectRun) ed.selectRun(opts.run);
    return ed;
  };

  S.activate = function (key) {
    const ed = key ? S.docs.get(key) : null;
    S.active = ed || null;
    S.docs.forEach(d => d.pane.classList.toggle('active', d === ed));
    document.getElementById('stWelcome').style.display = ed ? 'none' : '';
    const results = document.getElementById('stResults');
    results.replaceChildren(ed ? ed.resultsEl : h('div', { class: 'st-empty', text: 'Open a document to see its runs, logs and details here.' }));
    renderDocTabs();
    S.renderPalette();
    S.renderTree();
    S.syncToolbar();
    persistDocs();
    if (ed) requestAnimationFrame(() => ed.onShow());
  };

  S.closeDoc = function (key) {
    const ed = S.docs.get(key);
    if (!ed) return;
    ed.destroy();
    ed.pane.remove();
    const keys = [...S.docs.keys()];
    const idx = keys.indexOf(key);
    S.docs.delete(key);
    if (S.active === ed) {
      const rest = [...S.docs.keys()];
      S.activate(rest[Math.min(idx, rest.length - 1)] || null);
    } else {
      renderDocTabs();
      persistDocs();
    }
  };

  // ── Toolbar ─────────────────────────────────────────────────────────────

  S.syncToolbar = function () {
    const ed = S.active;
    const $ = id => document.getElementById(id);
    $('stRun').disabled = !ed || !ed.canRun();
    $('stRun').classList.toggle('st-busy', !!(ed && ed.running));
    $('stRunLabel').textContent = ed ? ed.runLabel : 'Run';
    $('stRunOpts').disabled = !ed || !ed.hasRunOptions || !ed.canRun();
    $('stKill').disabled = !ed || !ed.canKill();
    $('stKill').classList.toggle('st-busy', !!(ed && ed.killing));
    ['stReload', 'stProps', 'stDelete'].forEach(id => { $(id).disabled = !ed; });
    ['stZoomIn', 'stZoomOut', 'stZoomFit'].forEach(id => { $(id).disabled = !(ed && ed.canvas); });
    $('stZoomLevel').textContent = ed && ed.canvas ? Math.round(ed.canvas.zoom * 100) + '%' : '100%';
    $('stStatus').textContent = ed ? ed.statusText() : '';
  };

  S.renderPalette = function () {
    const box = document.getElementById('stPalette');
    box.replaceChildren();
    if (S.active) S.active.renderPalette(box);
    else box.append(h('div', { class: 'st-pal-hint', text: 'Open a mapping, chain or plan to see the building blocks you can place on its canvas.' }));
  };

  // ── Explorer ────────────────────────────────────────────────────────────

  let filterText = '';
  const collapsed = new Set(S.store.get('ante_studio_collapsed', []));

  S.setFilter = t => { filterText = (t || '').toLowerCase(); S.renderTree(); };

  let lastTreeJson = '';
  let lastTreeDraw = 0;
  S.refreshTree = async function () {
    try {
      const fresh = await S.api('/studio/tree/');
      const json = JSON.stringify(fresh);
      S.tree = fresh;
      // Redrawing the explorer swaps its DOM, which would cancel a drag started from
      // it — so only redraw when something changed (or the relative times went stale).
      if (json !== lastTreeJson || Date.now() - lastTreeDraw > 60000) {
        lastTreeJson = json;
        lastTreeDraw = Date.now();
        S.renderTree();
        renderDocTabs();      // tabs wear the same logos, so they follow renames and re-installs
      }
      S.docs.forEach(d => d.onTreeChanged && d.onTreeChanged());
    } catch (e) { /* transient (e.g. mid-navigation) — the next poll retries */ }
  };

  S.renderTree = function () {
    const root = document.getElementById('stTree');
    if (!root) return;
    const T = S.tree;
    const match = name => !filterText || String(name).toLowerCase().includes(filterText);

    const rowBtn = (icon, title, fn, cls) => h('button', { class: `st-row-btn ${cls || ''}`, title, onclick: e => { e.stopPropagation(); fn(); } }, h('i', { class: icon }));

    const docItem = (kind, d, meta, title, status) => {
      const key = `${kind}:${d.id}`;
      return h('div', {
        class: `st-item has-actions ${S.active && S.active.key === key ? 'active' : ''}`, title, draggable: 'true',
        ondragstart: e => { e.dataTransfer.setData('application/x-ante-doc', JSON.stringify({ kind, id: d.id })); e.dataTransfer.effectAllowed = 'copy'; },
        onclick: () => S.openDoc(kind, d.id),
        oncontextmenu: e => S.docMenu(e, kind, d.id),
      }, S.logoCluster(d, { kind, status }),
        h('span', { class: 'st-item-name', text: d.name }),
        h('span', { class: 'st-item-meta', text: meta }),
        h('span', { class: 'st-row-actions' },
          rowBtn('bi-pencil', `Edit ${kind}`, () => S.editDoc(kind, d.id)),
          rowBtn('bi-trash', `Delete ${kind}`, () => S.deleteDoc(kind, d.id), 'danger')));
    };

    // `module`: the module a section belongs to — without it the section stays, locked, so the user can see
    // what exists and why they can't use it.
    const section = (id, label, items, onAdd, module) => {
      const locked = !!module && !S.can(module);
      if (locked) { items = []; onAdd = null; }
      const sec = h('div', { class: `st-sec ${collapsed.has(id) ? 'collapsed' : ''} ${locked ? 'locked' : ''}` },
        h('div', {
          class: 'st-sec-head', onclick: () => {
            collapsed.has(id) ? collapsed.delete(id) : collapsed.add(id);
            S.store.set('ante_studio_collapsed', [...collapsed]);
            sec.classList.toggle('collapsed');
          },
        }, h('i', { class: 'bi-chevron-down st-caret' }), label,
          locked ? h('i', { class: 'bi-lock-fill st-lock', title: 'No access to this module' }) : null,
          h('span', { class: 'st-sec-count', text: items.length }),
          onAdd ? h('button', { class: 'st-sec-add', title: `New ${label.toLowerCase().replace(/s$/, '')}`, onclick: e => { e.stopPropagation(); onAdd(); } }, h('i', { class: 'bi-plus-lg' })) : null),
        h('div', { class: 'st-sec-body' }, items.length ? items : h('div', { class: 'st-empty', text: locked ? 'You don’t have access to this module. Ask an administrator to enable it.' : filterText ? 'No matches.' : 'Nothing here yet.' })));
      return sec;
    };

    // Starter templates come first: they are how you begin something new.
    const templatesSection = section('templates', 'Templates',
      S.templates.filter(t => match(t.title)).map(t => h('div', { class: 'st-item st-tpl-item', title: t.blurb, onclick: () => S.templateDialog(t) },
        h('span', { class: 'st-logo tpl' }, h('i', { class: t.icon })),
        h('span', { class: 'st-item-name', text: t.title }),
        h('span', { class: 'st-item-meta', text: S.templateKindLabel ? S.templateKindLabel[t.kind] : t.kind }))),
      () => S.templateGallery());

    root.replaceChildren(
      templatesSection,
      section('mappings', 'Mappings',
        T.mappings.filter(m => match(m.name)).map(m => docItem('mapping', m, `${m.pairs}`, `${m.source} → ${m.destinations.join(', ') || 'no destinations'}`)),
        () => S.newDoc('mapping'), 'mappings'),
      section('chains', 'Chains',
        T.chains.filter(c => match(c.name)).map(c => docItem('chain', c, `${c.steps}`, `on ${c.connection}`)),
        () => S.newDoc('chain'), 'chains'),
      section('plans', 'Plans',
        T.plans.filter(p => match(p.name)).map(p => docItem('plan', p, `${p.steps}`, `${p.status} · ${p.steps} step(s)`, p.status === 'draft' ? '' : p.status)),
        () => S.newDoc('plan'), 'plans'),
      section('runs', 'Recent runs',
        T.recent_runs.filter(r => match(r.doc_name)).map(r => h('div', {
          class: 'st-item', title: `${S.kindLabel[r.kind]} run #${r.run_id} — ${r.status}`,
          onclick: () => S.openDoc(r.kind, r.doc_id, { run: r.run_id }),
        }, S.logoCluster(r, { kind: r.kind, status: r.status }),
          h('span', { class: 'st-item-name', text: `${r.doc_name}` }),
          h('span', { class: 'st-item-meta', text: `#${r.run_id} · ${S.ago(r.started_at)}` }))).concat(
          // The classic list still has what the Studio doesn't: filters, paging, bulk retry/delete.
          [h('a', { class: 'st-item st-item-link', href: '/jobs/', title: 'Filter, page, bulk retry or delete runs' },
            h('i', { class: 'bi-list-ul' }), h('span', { class: 'st-item-name', text: 'All runs…' }), h('i', { class: 'bi-box-arrow-up-right st-item-meta' }))]),
        null, 'jobs'),
      section('connections', 'Connections',
        T.connections.filter(c => match(c.name)).map(c => h('a', {
          class: 'st-item st-item-link', href: `/connections/${c.id}/`, title: 'Open connection settings',
          // Connection settings belong to the App Store module: say so here instead of bouncing the user home.
          onclick: S.can('appstore') ? null : e => { e.preventDefault(); S.need('appstore'); },
        }, S.logoCluster(c, { kind: 'connection', status: c.connected ? null : 'warn' }),
          h('span', { class: 'st-item-name', text: c.name }),
          h('i', { class: S.can('appstore') ? 'bi-box-arrow-up-right st-item-meta' : 'bi-lock-fill st-item-meta' })))),
    );
  };

  // ── Creating documents ──────────────────────────────────────────────────

  S.newDoc = function (kind) {
    if (!S.need({ mapping: 'mappings', chain: 'chains', plan: 'plans' }[kind] || 'studio')) return;
    const conns = S.tree.connections;
    if ((kind === 'mapping' || kind === 'chain') && !conns.length) {
      S.dialog({
        title: `New ${kind}`,
        body: 'A connection is needed first — install an integration from the App Store and create a connection from it.',
        actions: [{ label: 'Close' }, { label: 'Open App Store', primary: true, onClick: () => { location.href = '/app-store/'; return false; } }],
      });
      return;
    }
    const optionsHtml = conns.map(c => `<option value="${c.id}">${S.esc(c.name)}</option>`).join('');

    if (kind === 'mapping') {
      const dlg = S.dialog({
        title: 'New mapping',
        body: h('div', {},
          h('div', { class: 'mb-3' }, h('label', { class: 'form-label', text: 'Name' }), h('input', { class: 'form-control', id: 'nm_name', placeholder: 'e.g. CRM to ERP sync' })),
          h('div', { class: 'mb-3' }, h('label', { class: 'form-label', text: 'Integration origin' }), h('select', { class: 'form-select', id: 'nm_source', html: optionsHtml })),
          h('div', {}, h('label', { class: 'form-label', text: 'Integration destinations' }),
            h('div', { class: 'st-checklist', html: conns.map(c => `<label class="d-flex gap-2 align-items-center"><input type="checkbox" class="form-check-input mt-0" value="${c.id}"> ${S.esc(c.name)}</label>`).join('') }),
            h('div', { class: 'form-text', text: 'Pick more than one to fan the same source out to several systems.' }))),
        actions: [{ label: 'Cancel' }, {
          label: 'Create', primary: true, onClick: async d => {
            const name = d.$('#nm_name').value.trim();
            if (!name) { d.error('Name is required.'); return false; }
            const created = await S.api('/api/mappings/', {
              method: 'POST', body: {
                name, source_connection: d.$('#nm_source').value,
                destination_connections: [...d.el.querySelectorAll('.st-checklist input:checked')].map(i => Number(i.value)),
              },
            });
            await S.refreshTree();
            S.openDoc('mapping', created.id);
          },
        }],
      });
      return dlg;
    }

    if (kind === 'chain') {
      return S.dialog({
        title: 'New chain',
        body: h('div', {},
          h('div', { class: 'mb-3' }, h('label', { class: 'form-label', text: 'Name' }), h('input', { class: 'form-control', id: 'nc_name', placeholder: 'e.g. Create customer + order' })),
          h('div', { class: 'mb-3' }, h('label', { class: 'form-label', text: 'Connection' }), h('select', { class: 'form-select', id: 'nc_conn', html: optionsHtml }),
            h('div', { class: 'form-text', text: "Every step calls this connection's API, using its configured auth." })),
          h('div', {}, h('label', { class: 'form-label' }, 'Description ', h('span', { class: 'text-dim', text: '(optional)' })), h('textarea', { class: 'form-control', id: 'nc_desc', rows: '2' }))),
        actions: [{ label: 'Cancel' }, {
          label: 'Create', primary: true, onClick: async d => {
            const name = d.$('#nc_name').value.trim();
            if (!name) { d.error('Name is required.'); return false; }
            const created = await S.api('/api/chains/', { method: 'POST', body: { name, connection: d.$('#nc_conn').value, description: d.$('#nc_desc').value } });
            await S.refreshTree();
            S.openDoc('chain', created.id);
          },
        }],
      });
    }

    return S.dialog({
      title: 'New plan',
      body: h('div', {},
        h('div', { class: 'mb-3' }, h('label', { class: 'form-label', text: 'Name' }), h('input', { class: 'form-control', id: 'np_name', placeholder: 'e.g. Full migration — night run' })),
        h('div', {}, h('label', { class: 'form-label' }, 'Description ', h('span', { class: 'text-dim', text: '(optional)' })), h('textarea', { class: 'form-control', id: 'np_desc', rows: '2' })),
        h('div', { class: 'form-text mt-2', text: 'A plan runs its steps one after another — each step can be a mapping or a chain, in any mix.' })),
      actions: [{ label: 'Cancel' }, {
        label: 'Create', primary: true, onClick: async d => {
          const name = d.$('#np_name').value.trim();
          if (!name) { d.error('Name is required.'); return false; }
          const created = await S.api('/api/plans/', { method: 'POST', body: { name, description: d.$('#np_desc').value, execution_mode: 'mixed' } });
          await S.refreshTree();
          S.openDoc('plan', created.id);
        },
      }],
    });
  };

  // Shared "Run options" dialog. fields: any of 'schedule', 'rate', 'files'.
  // Resolves the chosen values to onStart({ scheduled_at, rate_limit_per_second, files }).
  S.runOptionsDialog = function ({ title, fields, rateRpm, onStart, pickMapping }) {
    const body = h('div', {});
    if (pickMapping) body.append(h('div', { class: 'mb-3' },
      h('label', { class: 'form-label', for: 'ro_mapping' }, 'Mapping ', h('span', { class: 'text-danger', text: '*' })),
      h('select', { class: 'form-select', id: 'ro_mapping', html: '<option value="">Choose a mapping…</option>' + S.tree.mappings.map(m => `<option value="${m.id}">${S.esc(m.name)}  —  ${S.esc(m.source)} → ${S.esc(m.destinations.join(', ') || 'no destinations')}</option>`).join('') })));
    if (fields.includes('schedule')) body.append(h('div', { class: 'mb-3' },
      h('label', { class: 'form-label' }, 'Run at ', h('span', { class: 'text-dim', text: '(optional)' })),
      h('input', { type: 'datetime-local', class: 'form-control', id: 'ro_at' }),
      h('div', { class: 'form-text', text: 'Leave blank to start immediately.' })));
    if (fields.includes('rate')) body.append(h('div', { class: 'mb-3' },
      h('label', { class: 'form-label' }, 'Rate limit ', h('span', { class: 'text-dim', text: '(optional, requests/minute)' })),
      h('input', { type: 'number', min: '0.1', step: '1', class: 'form-control', id: 'ro_rate', placeholder: 'unthrottled', value: rateRpm == null ? '' : rateRpm }),
      h('div', { class: 'form-text', text: 'Source reads and destination writes combined.' })));
    if (fields.includes('files')) body.append(h('div', {},
      h('label', { class: 'form-label' }, 'Input file(s) ', h('span', { class: 'text-dim', text: '(optional)' })),
      h('input', { type: 'file', class: 'form-control', id: 'ro_files', accept: '.csv,.xlsx', multiple: true }),
      h('div', { class: 'form-text', text: "CSV/XLSX read instead of the source entity's stored file. More than one file runs as a batch, one run per file, in order (scheduling is ignored for a batch)." })));
    return S.dialog({
      title, body, actions: [{ label: 'Cancel' }, {
        label: 'Start', primary: true, onClick: async d => {
          const at = d.$('#ro_at') && d.$('#ro_at').value;
          const rate = d.$('#ro_rate') && d.$('#ro_rate').value;
          const files = d.$('#ro_files') ? [...d.$('#ro_files').files] : [];
          const mapping = pickMapping ? Number(d.$('#ro_mapping').value) : null;
          if (pickMapping && !mapping) { d.error('Choose the mapping to run.'); return false; }
          await onStart({
            mapping,
            // datetime-local carries no timezone; Date() reads it as local wall-clock time.
            scheduled_at: at ? new Date(at).toISOString() : null,
            rate_limit_per_second: rate ? parseFloat(rate) / 60 : null,
            files,
          });
        },
      }],
    });
  };

  // "Single run": run one mapping once, right now — or scheduled / throttled / on a file you attach —
  // without opening it first. The mapping then opens on its run so you can watch it.
  S.singleRun = function () {
    if (!S.need('mappings', 'jobs')) return;
    if (!S.tree.mappings.length) { S.toast('There are no mappings to run yet — create one first.', 'err'); return; }
    S.runOptionsDialog({
      title: 'Single run', pickMapping: true, fields: ['schedule', 'rate', 'files'],
      onStart: async ({ mapping, ...options }) => {
        const ed = await S.openDoc('mapping', mapping);
        if (ed) await ed.startRun(options);
      },
    });
  };

  // "Custom run": a run you assemble by drag and drop. It is a fresh plan on its own canvas, opened with
  // the Design tab in front — drag mappings, single entity pairs, chains and waits onto it, reorder them,
  // then Execute. (Being a plan, it can be saved, edited and run again.)
  S.newCustomRun = async function () {
    if (!S.need('plans')) return;
    const stamp = new Date().toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    try {
      const plan = await S.api('/api/plans/', { method: 'POST', body: { name: `Custom run · ${stamp}`, execution_mode: 'mixed' } });
      await S.refreshTree();
      await S.openDoc('plan', plan.id);
      const design = document.querySelector('.st-side-tabs [data-side="design"]');
      if (design) design.click();
      S.toast('Drag mappings, entity pairs, chains and waits onto the canvas to build your run.', 'ok', 6500);
    } catch (e) { S.fail(e); }
  };

  // ── Draggable / droppable helpers used by the editors ───────────────────

  // A Design-palette row. `icon` is a bootstrap-icons class or a ready-made node.
  // With a `mime` it can be dragged onto the canvas (carrying `payload`); with
  // mime = null it is click-only. Clicking always works.
  S.paletteItem = function (icon, label, sub, mime, payload, onClick) {
    return h('div', {
      class: 'st-pal-item', draggable: mime ? 'true' : null, title: mime ? 'Drag onto the canvas, or click' : 'Click to add',
      ondragstart: mime ? (e => { e.dataTransfer.setData(mime, JSON.stringify(payload)); e.dataTransfer.effectAllowed = 'copy'; }) : null,
      onclick: onClick,
    }, typeof icon === 'string' ? h('i', { class: icon }) : icon, label, sub ? h('small', { text: sub }) : null);
  };
})();
