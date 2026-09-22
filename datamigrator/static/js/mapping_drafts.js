/* Auto-mapping on the classic mapping page: suggest field mappings as drafts, then accept or discard them.
   Talks to /api/mappings/<id>/auto-map|confirm-drafts|discard-drafts/ and /api/field-mappings/<id>/
   (see mappings/views.py). Drafts are left out of runs until they are accepted. */
(function () {
  'use strict';

  function flatten(d) {
    if (!d) return '';
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) return d.map(flatten).join(' ');
    return Object.keys(d).map(function (k) { return flatten(d[k]); }).join(' · ');
  }
  function post(url, body) {
    return fetch(url, {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body || {}),
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) throw new Error(flatten(data) || ('Request failed (' + r.status + ')'));
        return data;
      });
    });
  }
  function fail(e) { alert(e.message || String(e)); }

  function autoMap(id, button) {
    button.disabled = true;
    post('/api/mappings/' + id + '/auto-map/', { min_score: 60, only_unmapped: true, replace_drafts: true }).then(function (r) {
      // r.created counts the drafts saved over every entity pair of the mapping
      if (!r.created) { alert('No matching field names were found. Wire the fields by hand, or try Auto-map in the Studio with a looser setting.'); button.disabled = false; return; }
      window.location.href = '?tab=canvas';
    }).catch(function (e) { button.disabled = false; fail(e); });
  }

  document.addEventListener('click', function (e) {
    var t = e.target.closest('[data-automap],[data-drafts],[data-fm-accept],[data-fm-reject]');
    if (!t) return;
    if (t.hasAttribute('data-automap')) return autoMap(t.getAttribute('data-automap'), t);
    if (t.hasAttribute('data-drafts')) {
      var id = t.getAttribute('data-mapping');
      var action = t.getAttribute('data-drafts') === 'confirm' ? 'confirm-drafts' : 'discard-drafts';
      var go = function () { post('/api/mappings/' + id + '/' + action + '/').then(function () { window.location.reload(); }).catch(fail); };
      if (action === 'discard-drafts') { confirmModal('Discard all suggested field mappings?').then(function (yes) { if (yes) go(); }); } else go();
      return;
    }
    var fmId = t.getAttribute('data-fm-accept') || t.getAttribute('data-fm-reject');
    var req = t.hasAttribute('data-fm-accept')
      ? fetch('/api/field-mappings/' + fmId + '/', { method: 'PATCH', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: 'confirmed' }) })
      : fetch('/api/field-mappings/' + fmId + '/', { method: 'DELETE', credentials: 'same-origin' });
    req.then(function (r) { if (r.ok) window.location.reload(); else alert('Could not update that field mapping.'); }).catch(fail);
  });
})();
