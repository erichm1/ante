/* Full guided onboarding tour — shows once per authenticated user (keyed by
   username in localStorage). Page-aware: steps that belong to a specific page
   redirect automatically so the user gets a complete ride through every section
   of the system. */

(function () {
  'use strict';

  /* ---------- step definitions ------------------------------------------ */
  /* Each step may have:
     target  – CSS selector to spotlight (null → centered card)
     page    – URL prefix this step lives on; go() redirects there if needed
     cta     – custom CTA label (default "Next →" or "Done")
     ctaHref – navigate instead of advancing (used on the final "Done" step)  */
  var STEPS = [
    /* 0 — Welcome */
    {
      id:    'welcome',
      title: 'Welcome to Ante',
      body:  'Ante is a data migration platform — move data between REST APIs, CSV files, and spreadsheets. This tour walks you through the full system, page by page, in about two minutes.',
      target: null,
      cta:    'Start tour',
    },

    /* ── App Store ───────────────────────────────────────────────────────── */
    {
      id:    'nav-app-store',
      title: 'App Store',
      body:  'Start here. Browse 100+ pre-built connectors for REST APIs, CSV files, and spreadsheets. <strong>Install</strong> an integration to create an authenticated channel — a Connection — to that service.',
      target: '.nav-links a[href="/app-store/"]',
    },
    {
      id:    'app-store-tabs',
      title: 'Catalog vs Installed',
      body:  '<strong>Catalog</strong> lists every available integration. <strong>Installed</strong> shows the Connections you\'ve already set up. Start in Catalog, pick an integration, hit Install — then open it in Installed to configure credentials.',
      target: '.nav-tabs .nav-link:first-child',
      page:   '/app-store/',
    },
    {
      id:    'app-store-card',
      title: 'Integration cards',
      body:  'Each card is a pre-built connector. Click <strong>Install</strong> to add it to your workspace. After installing, open it from the Installed tab to create a Connection with your API credentials.',
      target: '.integration-card:first-child',
      page:   '/app-store/',
    },

    /* ── Mappings ────────────────────────────────────────────────────────── */
    {
      id:    'nav-mappings',
      title: 'Mappings',
      body:  'A Mapping pairs a source Connection with one or more destination Connections and defines which data entities (API endpoints, CSV sheets) to move between them.',
      target: '.nav-links a[href="/mappings/"]',
    },
    {
      id:    'mappings-new',
      title: 'Create your first mapping',
      body:  'Click <strong>New mapping</strong> to open the builder. You\'ll pick a source connection, one or more destination connections, and then choose which entities (API endpoints or file tabs) to synchronise.',
      target: '.btn-signal',
      page:   '/mappings/',
    },

    /* ── Canvas ──────────────────────────────────────────────────────────── */
    {
      id:    'nav-canvas',
      title: 'Canvas',
      body:  'The Canvas is the visual field-wiring editor inside a Mapping. Drag teal dots (source fields) onto amber dots (destination fields) to map individual columns. One source field can fan out to multiple destinations.',
      target: '.nav-links a[href="/mappings/canvas/"]',
    },
    {
      id:    'canvas-page',
      title: 'Open a Canvas',
      body:  'Click <strong>Open</strong> next to any Mapping to enter its visual field-wiring editor. There you\'ll see each entity as a card with its fields. Drag a teal source dot to an amber destination dot to map columns. One source can fan out to multiple destinations.',
      target: 'a.btn-outline-secondary',
      page:   '/mappings/canvas/',
    },

    /* ── Runs ────────────────────────────────────────────────────────────── */
    {
      id:    'nav-runs',
      title: 'Runs',
      body:  'Trigger a migration here: upload a CSV or XLSX file for file-backed connections, or kick off a live API pull for REST connections. Logs update in real time as records are processed.',
      target: '.nav-links a[href="/jobs/"]',
    },
    {
      id:    'runs-create',
      title: 'Trigger a migration',
      body:  'Click <strong>Create run</strong> to start a new migration. Choose a Mapping, then either upload a source file or trigger a live API pull. Each processed record is logged in the API call history.',
      target: 'button.btn-signal',
      page:   '/jobs/',
    },

    /* ── Plans ───────────────────────────────────────────────────────────── */
    {
      id:    'nav-plans',
      title: 'Plans',
      body:  'Plans bundle multiple Mappings into one ordered, sequential migration batch. Run them on demand or schedule them. Each step runs after the previous one completes, so you can chain dependent data moves.',
      target: '.nav-links a[href="/plans/"]',
    },
    {
      id:    'plans-new',
      title: 'Create a plan',
      body:  'Click <strong>New plan</strong> to create a batch. Add your Mappings as ordered steps, set an execution mode (sequential or chain), and run or schedule the whole thing from a single button.',
      target: 'button.btn-signal',
      page:   '/plans/',
    },

    /* ── Chains ──────────────────────────────────────────────────────────── */
    {
      id:    'nav-chains',
      title: 'Chains',
      body:  'Chains compose multi-step API call sequences where each step can use captured values from the previous response. Useful for create-then-link patterns — e.g. create a customer, capture their ID, then create an order against that ID.',
      target: '.nav-links a[href="/chains/"]',
    },
    {
      id:    'chains-new',
      title: 'Create a chain',
      body:  'Click <strong>New chain</strong> to start. Each chain step defines an HTTP call — method, path, body. Use <code>{{step_name.field}}</code> placeholders to forward a previous step\'s captured response data into the next step. Drag the handle column to reorder steps.',
      target: 'button.btn-signal',
      page:   '/chains/',
    },

    /* ── Reports ─────────────────────────────────────────────────────────── */
    {
      id:    'nav-reports',
      title: 'Reports',
      body:  'Reports combine data from multiple entities into a single combined CSV export. Build one by dragging entities onto the canvas, picking the columns you want, and previewing the output — then download or call the export API.',
      target: '.nav-links a[href="/reports/"]',
    },
    {
      id:    'reports-new',
      title: 'Build a report',
      body:  'Click <strong>New report</strong> to start. Inside the report, drag an entity from the left panel onto the canvas, then drag individual fields into each section to choose your columns. The preview updates live.',
      target: 'button.btn-signal',
      page:   '/reports/',
    },

    /* ── Logs ────────────────────────────────────────────────────────────── */
    {
      id:    'nav-logs',
      title: 'Logs',
      body:  'Every outbound API call made across all your Connections is recorded here — URL, method, status, response time, headers, and body. Think of it as Grafana Loki for your migrations.',
      target: '.nav-links a[href="/connections/logs/"]',
    },
    {
      id:    'logs-query',
      title: 'Query log streams',
      body:  'Type space-separated terms to filter: <code>status:>=400</code>, <code>method:POST</code>, <code>connection:olist</code>, <code>duration:>500</code>, <code>since:1h</code>, <code>error:true</code>. Terms are AND-combined; bare words search URL and body text.',
      target: 'input[name="q"]',
      page:   '/connections/logs/',
    },
    {
      id:    'logs-templates',
      title: 'Quick filter templates',
      body:  'Use these one-click filters to jump straight to the most common queries — recent errors, slow calls, POST-only requests — without having to type the syntax.',
      target: '#logTemplates',
      page:   '/connections/logs/',
    },

    /* ── Done ────────────────────────────────────────────────────────────── */
    {
      id:     'done',
      title:  'Ready to migrate',
      body:   'You\'ve seen the full system. Start by installing an integration in the App Store, set up a Connection, build a Mapping, wire fields in the Canvas, and trigger your first Run.',
      target:  null,
      cta:     'Open App Store',
      ctaHref: '/app-store/',
    },
  ];

  /* ---------- state ------------------------------------------------------ */
  var user   = (window.ANTE_USER || 'anon').replace(/[^a-zA-Z0-9_-]/g, '_');
  var K_DONE = 'ante_onb_done_' + user;
  var K_STEP = 'ante_onb_step_' + user;

  try { if (localStorage.getItem(K_DONE)) return; } catch (e) { return; }

  var step = 0;
  try { step = parseInt(localStorage.getItem(K_STEP) || '0', 10) || 0; } catch (e) {}
  if (step < 0 || step >= STEPS.length) step = 0;

  /* ---------- DOM refs --------------------------------------------------- */
  var overlay, card, beacon;

  /* ---------- build UI --------------------------------------------------- */
  function init() {
    /* If current step has a page requirement and we're on the wrong page,
       redirect first — the tour will resume when that page loads. */
    var s = STEPS[step];
    if (s.page && !window.location.pathname.startsWith(s.page)) {
      window.location.href = s.page;
      return;
    }

    overlay = el('div', { id: 'onbOverlay' });
    card    = el('div', { id: 'onbCard' });
    beacon  = el('div', { id: 'onbBeacon', 'aria-hidden': 'true' });
    document.body.appendChild(overlay);
    document.body.appendChild(card);
    document.body.appendChild(beacon);
    render();
    window.addEventListener('resize', reposition);
  }

  function el(tag, attrs) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    return e;
  }

  /* ---------- finish ------------------------------------------------------ */
  function finish(href) {
    try { localStorage.setItem(K_DONE, '1'); } catch (e) {}
    try { localStorage.removeItem(K_STEP); } catch (e) {}
    overlay.remove(); card.remove(); beacon.remove();
    window.removeEventListener('resize', reposition);
    if (href) window.location.href = href;
  }

  /* ---------- step navigation -------------------------------------------- */
  function go(n) {
    step = Math.max(0, Math.min(n, STEPS.length - 1));
    try { localStorage.setItem(K_STEP, String(step)); } catch (e) {}
    var s = STEPS[step];
    /* If the target step requires a different page, navigate there.
       The tour resumes automatically when that page loads. */
    if (s.page && !window.location.pathname.startsWith(s.page)) {
      window.location.href = s.page;
      return;
    }
    render();
  }

  /* ---------- render current step ---------------------------------------- */
  function render() {
    var s       = STEPS[step];
    var isFirst = step === 0;
    var isLast  = step === STEPS.length - 1;
    var centered = !s.target;

    /* Progress 0–100 (exclude welcome + done from percentage range) */
    var innerSteps = STEPS.length - 2;   // exclude index 0 (welcome) and last (done)
    var progress   = isFirst ? 0
                   : isLast  ? 100
                   : Math.round(((step - 1) / innerSteps) * 100);

    var backHtml = (!isFirst && !centered)
      ? '<button class="onb-btn onb-back" id="onbBack">← Back</button>'
      : '';
    var nextLabel = s.cta || (isLast ? 'Done' : 'Next →');
    var nextHtml  = '<button class="onb-btn onb-next" id="onbNext">' + nextLabel + '</button>';
    var skipHtml  = (!isLast)
      ? '<button class="onb-skip" id="onbSkip">Skip tour</button>'
      : '';

    card.className = centered ? 'onb-centered' : '';
    card.innerHTML =
      '<div class="onb-title">' + s.title + '</div>' +
      '<div class="onb-body">'  + s.body  + '</div>' +
      '<div class="onb-foot">'  + skipHtml +
        '<div class="onb-nav">' + backHtml + nextHtml + '</div>' +
      '</div>' +
      '<div class="onb-progress-wrap"><div class="onb-progress-bar" style="width:' + progress + '%"></div></div>';

    card.querySelector('#onbNext').onclick = function () {
      if (s.ctaHref)  { finish(s.ctaHref); }
      else if (isLast){ finish(null); }
      else            { go(step + 1); }
    };
    var bb = card.querySelector('#onbBack');
    if (bb) bb.onclick = function () { go(step - 1); };
    var sk = card.querySelector('#onbSkip');
    if (sk) sk.onclick = function () { finish(null); };

    reposition();
  }

  /* ---------- position overlay + card + beacon --------------------------- */
  function reposition() {
    var s      = STEPS[step];
    var target = s.target ? document.querySelector(s.target) : null;

    if (!target) {
      /* Centered welcome / done card — full-screen dim, no spotlight */
      overlay.style.clipPath = 'none';
      overlay.style.display  = 'block';
      beacon.style.display   = 'none';
      card.style.cssText     = 'left:50%;top:50%;transform:translate(-50%,-50%)';
      return;
    }

    var r   = target.getBoundingClientRect();
    var PAD = 10;
    var rx  = Math.round(r.left  - PAD);
    var ry  = Math.round(r.top   - PAD);
    var rw  = Math.round(r.width  + PAD * 2);
    var rh  = Math.round(r.height + PAD * 2);
    var vw  = window.innerWidth;
    var vh  = window.innerHeight;

    /* Clip-path "donut": outer rect clockwise, hole counter-clockwise.
       Non-zero winding rule: hole interior = CW(+1) + CCW(-1) = 0 → clipped. */
    overlay.style.display = 'block';
    overlay.style.clipPath = 'polygon(' +
      '0px 0px,' + vw + 'px 0px,' + vw + 'px ' + vh + 'px,0px ' + vh + 'px,0px 0px,' +
      rx + 'px ' + ry + 'px,' +
      rx + 'px ' + (ry + rh) + 'px,' +
      (rx + rw) + 'px ' + (ry + rh) + 'px,' +
      (rx + rw) + 'px ' + ry + 'px,' +
      rx + 'px ' + ry + 'px' +
    ')';

    /* Beacon at center of spotlight */
    beacon.style.display = 'block';
    beacon.style.left    = (r.left + r.width  / 2) + 'px';
    beacon.style.top     = (r.top  + r.height / 2) + 'px';

    /* Card positioning: prefer right of the target. If that would push the
       card off-screen, try left. If that also fails, center horizontally. */
    var CARD_W = 288;
    var GAP    = 20;
    var rightLeft = r.right + GAP;
    var leftLeft  = r.left - CARD_W - GAP;

    var cardLeft;
    if (rightLeft + CARD_W <= vw - 16) {
      cardLeft = rightLeft;
    } else if (leftLeft >= 16) {
      cardLeft = leftLeft;
    } else {
      /* Neither side fits — center the card horizontally and hide arrow */
      cardLeft = Math.max(16, Math.round(vw / 2 - CARD_W / 2));
    }

    card.style.transform = 'none';
    card.style.left      = cardLeft + 'px';

    var cardH    = card.offsetHeight || 220;
    var rawTop   = r.top + r.height / 2 - cardH / 2;
    var clampTop = Math.max(12, Math.min(rawTop, vh - cardH - 12));
    card.style.top = clampTop + 'px';

    /* Arrow y-position relative to card so it points at target center.
       Suppress arrow pseudo-elements when card is centered (no clear side). */
    if (cardLeft === rightLeft || cardLeft === leftLeft) {
      card.classList.remove('onb-no-arrow');
    } else {
      card.classList.add('onb-no-arrow');
    }
    var arrowTop = (r.top + r.height / 2) - clampTop;
    card.style.setProperty('--arrow-top', Math.round(arrowTop) + 'px');
  }

  /* ---------- boot ------------------------------------------------------- */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { setTimeout(init, 120); });
  } else {
    setTimeout(init, 120);
  }

}());
