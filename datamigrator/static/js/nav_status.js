/* Navbar Status dot: polls /home/nav-status/ and recolors it — green (no events), yellow (something in
   roughly the last hour) or red (an incident is open or still being worked). The template already renders
   the right color on first paint (home.context_processors.nav_status); this just keeps it live afterwards,
   the same way notifications.js keeps the bell's unread count live. */
(function () {
  'use strict';
  var dot = document.getElementById('navStatusDot');
  if (!dot) return;
  var POLL_MS = 60000;
  // Same wording as the status page's own banner / new status-page-only phrases — see i18n/sources/17_status_page.txt.
  var TITLES = { green: 'All systems operational', yellow: 'Some issues in the last hour', red: 'An incident is open' };

  function render(level) {
    if (!TITLES[level]) return;
    dot.className = 'nav-status-dot ' + level;
    dot.title = TITLES[level];
  }

  function poll() {
    if (document.hidden) return;
    fetch('/home/nav-status/', { credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { if (data) render(data.level); })
      .catch(function () { /* offline / server restarting: try again next tick */ });
  }

  setInterval(poll, POLL_MS);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) poll(); });
})();
