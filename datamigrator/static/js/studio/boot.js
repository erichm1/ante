/* Studio boot — wires the static shell (toolbar, side tabs, splitter) to the
   document manager in core.js, then restores the last session's tabs (or the
   ?open=kind:id deep link the classic pages redirect to) — on screens wide enough for the
   Studio. Loaded last. */
(function () {
  'use strict';
  const S = window.Studio;
  const $ = id => document.getElementById(id);
  const studio = $('studio');

  // ── Toolbar ───────────────────────────────────────────────────────────────
  const guarded = fn => async () => { try { await fn(); } catch (e) { S.fail(e); } };

  // The editor only flips its own `running` flag once the server has answered, so a
  // double-click on Run would otherwise start two runs. Ignore clicks while one is in flight.
  let starting = false;
  $('stRun').addEventListener('click', guarded(async () => {
    if (starting || !S.active) return;
    starting = true;
    try { await S.active.run(); } finally { starting = false; }
  }));
  $('stRunOpts').addEventListener('click', guarded(() => S.active && S.active.runOptions()));

  // Kill: confirm, then ask the server to stop whatever this document has running. Stopping is cooperative —
  // the run winds down at its next check (between records / steps / mid-wait) and reports itself cancelled.
  $('stKill').addEventListener('click', guarded(async () => {
    const ed = S.active;
    if (!ed || !ed.canKill() || ed.killing) return;
    if (!(await confirmModal(ed.killMessage()))) return;
    ed.killing = true;
    S.syncToolbar();
    try { await ed.kill(); } finally { ed.killing = false; S.syncToolbar(); }
  }));
  $('stReload').addEventListener('click', guarded(async () => {
    const ed = S.active;
    if (!ed) return;
    // Reload = drop the editor and reopen it fresh from the server.
    const { kind, id } = ed;
    S.closeDoc(ed.key);
    await S.openDoc(kind, id);
    S.refreshTree();
  }));
  $('stProps').addEventListener('click', () => S.active && S.active.properties());
  $('stDelete').addEventListener('click', guarded(() => S.active && S.active.remove()));
  $('stZoomIn').addEventListener('click', () => S.zoomActive(c => c.zoomBy(1.2)));
  $('stZoomOut').addEventListener('click', () => S.zoomActive(c => c.zoomBy(1 / 1.2)));
  $('stZoomFit').addEventListener('click', () => S.zoomActive(c => c.fit()));

  document.querySelectorAll('[data-new]').forEach(el => el.addEventListener('click', () => S.newDoc(el.dataset.new)));
  document.querySelectorAll('[data-templates]').forEach(el => el.addEventListener('click', () => S.templateGallery()));

  // The toolbar scrolls horizontally on narrow screens (overflow-x:auto), which clips any
  // absolutely-positioned dropdown inside it — so "New" opens the body-level menu instead.
  $('stNew').addEventListener('click', () => {
    const r = $('stNew').getBoundingClientRect();
    S.menu(r.left, r.bottom + 4, [
      ...[['mapping', 'mappings'], ['chain', 'chains'], ['plan', 'plans']].map(([kind, module]) => (
        { label: `Blank ${kind}`, icon: S.can(module) ? S.icons[kind] : 'bi-lock-fill', onClick: () => S.newDoc(kind) })),
      { label: 'From a template…', icon: 'bi-magic', onClick: () => S.templateGallery() },
      { label: 'Single run…', icon: S.can('jobs') && S.can('mappings') ? 'bi-play-circle' : 'bi-lock-fill', onClick: () => S.singleRun() },
      { label: 'Custom run (drag & drop)…', icon: S.can('plans') ? 'bi-diagram-3' : 'bi-lock-fill', onClick: () => S.newCustomRun() },
    ]);
  });

  // ── Explorer: View / Design tabs, filter ──────────────────────────────────
  document.querySelectorAll('.st-side-tabs button').forEach(btn => btn.addEventListener('click', () => {
    document.querySelectorAll('.st-side-tabs button').forEach(b => b.classList.toggle('active', b === btn));
    $('stSideView').classList.toggle('d-none', btn.dataset.side !== 'view');
    $('stSideDesign').classList.toggle('d-none', btn.dataset.side !== 'design');
    S.store.set('ante_studio_side', btn.dataset.side);
  }));
  $('stFilter').addEventListener('input', e => S.setFilter(e.target.value));
  if (S.store.get('ante_studio_side', 'view') === 'design') document.querySelector('.st-side-tabs [data-side="design"]').click();

  // ── Results panel: resize + collapse ──────────────────────────────────────
  const setResultsHeight = px => studio.style.setProperty('--st-results-h', `${Math.round(px)}px`);
  const savedHeight = S.store.get('ante_studio_results_h', null);
  if (savedHeight) setResultsHeight(savedHeight);
  if (S.store.get('ante_studio_results_hidden', false)) studio.classList.add('results-hidden');

  $('stToggleResults').addEventListener('click', () => {
    const hidden = studio.classList.toggle('results-hidden');
    S.store.set('ante_studio_results_hidden', hidden);
  });

  $('stSplitter').addEventListener('mousedown', e => {
    e.preventDefault();
    const splitter = $('stSplitter');
    const work = document.querySelector('.st-work').getBoundingClientRect();
    splitter.classList.add('dragging');
    const move = ev => {
      // Results height = distance from the pointer to the bottom of the workspace,
      // clamped so neither the canvas nor the panel can be squashed to nothing.
      const px = Math.min(work.height - 160, Math.max(80, work.bottom - ev.clientY));
      setResultsHeight(px);
    };
    const up = () => {
      splitter.classList.remove('dragging');
      removeEventListener('mousemove', move);
      removeEventListener('mouseup', up);
      S.store.set('ante_studio_results_h', parseInt(getComputedStyle(studio).getPropertyValue('--st-results-h'), 10));
    };
    addEventListener('mousemove', move);
    addEventListener('mouseup', up);
  });

  // ── Start up ──────────────────────────────────────────────────────────────
  async function start() {
    await S.refreshTree();
    S.loadTemplates();          // not awaited: the sidebar section fills in as soon as it arrives

    const deepLink = new URL(location.href).searchParams.get('open');
    const saved = S.store.get('ante_studio_state', { tabs: [], active: null });
    const wanted = [...saved.tabs];
    if (deepLink && !wanted.includes(deepLink)) wanted.push(deepLink);
    const target = deepLink || saved.active;

    // Open every remembered tab, then bring the wanted one to the front.
    for (const key of wanted) {
      const [kind, id] = key.split(':');
      if (S.editorClasses[kind] && Number(id)) await S.openDoc(kind, Number(id));
    }
    if (target && S.docs.has(target)) S.activate(target);
    else S.syncToolbar();

    // Keep the explorer's statuses (plan state, recent runs) fresh while the tab is visible.
    setInterval(() => { if (!document.hidden) S.refreshTree(); }, 15000);
  }

  // The Studio is desktop-only (studio.css shows a notice below 768px instead), so on a phone we
  // load nothing — no tree, no tabs, no polling. Turning a tablet/phone sideways, or resizing a
  // browser window past the breakpoint, starts it then.
  const desktop = window.matchMedia('(min-width: 768px)');
  let started = false;
  const startOnce = () => { if (!started) { started = true; start(); } };
  if (desktop.matches) startOnce();
  else desktop.addEventListener('change', e => { if (e.matches) startOnce(); });
})();
