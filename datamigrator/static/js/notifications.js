/* Navbar bell: polls /notifications/feed/ for the unread count and the latest few notifications.
   On a wide screen the bell opens a dropdown; on a phone it is a plain link to the full list.
   Titles and messages come from the server as plain text and are only ever set with textContent. */
(function () {
  'use strict';
  var bell = document.getElementById('notifBell');
  if (!bell) return;
  var badge = document.getElementById('notifBadge');
  var panel = document.getElementById('notifPanel');
  var list = document.getElementById('notifItems');
  var readAll = document.getElementById('notifReadAll');
  var desktop = window.matchMedia('(min-width: 768px)');
  var POLL_MS = 30000;

  function ago(iso) {
    var s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 60) return 'just now';
    if (s < 3600) return Math.floor(s / 60) + ' min ago';
    if (s < 86400) return Math.floor(s / 3600) + ' h ago';
    return Math.floor(s / 86400) + ' d ago';
  }

  function render(data) {
    var n = data.unread || 0;
    badge.hidden = n === 0;
    badge.textContent = n > 99 ? '99+' : String(n);
    bell.setAttribute('aria-label', n ? 'Notifications, ' + n + ' unread' : 'Notifications');
    readAll.hidden = n === 0;
    list.replaceChildren();
    if (!data.items.length) {
      var empty = document.createElement('div');
      empty.className = 'notif-empty';
      empty.textContent = 'Nothing yet — you will be told when runs, chains and plans finish.';
      list.append(empty);
      return;
    }
    data.items.forEach(function (it) {
      var a = document.createElement('a');
      a.href = it.url;
      a.className = 'notif-item' + (it.read ? '' : ' unread');
      var dot = document.createElement('span');
      dot.className = 'notif-dot ' + it.level;
      dot.title = it.outcome;
      var body = document.createElement('span');
      body.className = 'notif-body';
      var title = document.createElement('span');
      title.className = 'notif-title';
      title.textContent = it.title;
      var meta = document.createElement('span');
      meta.className = 'notif-msg';
      meta.textContent = (it.message ? it.message + ' · ' : '') + ago(it.created_at);
      body.append(title, meta);
      a.append(dot, body);
      list.append(a);
    });
  }

  function poll() {
    if (document.hidden) return Promise.resolve();
    return fetch('/notifications/feed/', { credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { if (data) render(data); })
      .catch(function () { /* offline / server restarting: try again next tick */ });
  }

  function setOpen(open) {
    panel.hidden = !open;
    bell.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) poll();
  }

  bell.addEventListener('click', function (e) {
    if (!desktop.matches) return;          // phones: follow the link to the full page
    e.preventDefault();
    setOpen(panel.hidden);
  });
  document.addEventListener('click', function (e) {
    if (!panel.hidden && !document.getElementById('notifWrap').contains(e.target)) setOpen(false);
  });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !panel.hidden) { setOpen(false); bell.focus(); } });

  readAll.addEventListener('click', function () {
    fetch('/notifications/read/', { method: 'POST', credentials: 'same-origin' }).then(poll);
  });

  poll();
  setInterval(poll, POLL_MS);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) poll(); });
})();
