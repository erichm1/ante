/* Create / edit / duplicate / delete a mapping from the classic pages (list, canvas list, mapping page).
   Talks to /api/mappings/ (see mappings/views.py::MappingViewSet). The data it needs is in two json_script blocks
   rendered by mappings/_crud_modal.html. All server text goes on the page with textContent. */
(function () {
  'use strict';
  var modalEl = document.getElementById('mappingModal');
  if (!modalEl) return;
  var mappings = JSON.parse(document.getElementById('mappings-data').textContent || '[]');
  var connections = JSON.parse(document.getElementById('connections-data').textContent || '[]');
  var modal = new bootstrap.Modal(modalEl);
  var editing = null;                       // the mapping being edited, or null when creating

  function $(id) { return document.getElementById(id); }
  function byId(id) { return mappings.filter(function (m) { return m.id === id; })[0]; }

  function flatten(d) {
    if (!d) return '';
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) return d.map(flatten).join(' ');
    return Object.keys(d).map(function (k) {
      var msg = flatten(d[k]);
      return (k === 'detail' || k === 'error' || k === 'non_field_errors') ? msg : k.replace(/_/g, ' ') + ': ' + msg;
    }).join(' · ');
  }
  function api(method, url, body) {
    return fetch(url, {
      method: method, credentials: 'same-origin', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    }).then(function (r) {
      if (r.status === 204) return null;
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) throw new Error(flatten(data) || ('Request failed (' + r.status + ')'));
        return data;
      });
    });
  }
  function showError(msg) { var e = $('mappingError'); e.textContent = msg; e.classList.remove('d-none'); }
  function clearError() { $('mappingError').classList.add('d-none'); }

  function option(value, text) { var o = document.createElement('option'); o.value = value; o.textContent = text; return o; }

  /* Fill the dialog for a new mapping (m = null) or an existing one. */
  function openDialog(m) {
    editing = m;
    clearError();
    $('mappingModalTitle').textContent = m ? 'Edit mapping' : 'New mapping';
    $('mp_save').textContent = m ? 'Save' : 'Create';
    $('mp_name').value = m ? m.name : '';
    $('mp_description').value = m ? m.description || '' : '';

    var source = $('mp_source');
    source.replaceChildren.apply(source, connections.map(function (c) { return option(c.id, c.name); }));
    if (m) source.value = String(m.source_connection);
    var locked = !!(m && m.entity_pairs_count > 0);
    source.disabled = locked;
    $('mp_source_locked').classList.toggle('d-none', !locked);

    var box = $('mp_destinations');
    var chosen = m ? m.destination_connections : [];
    var inUse = m ? m.destinations_in_use : [];
    box.replaceChildren.apply(box, connections.map(function (c) {
      var id = 'mpd_' + c.id;
      var input = document.createElement('input');
      input.type = 'checkbox'; input.className = 'form-check-input mt-0'; input.id = id; input.value = c.id;
      input.checked = chosen.indexOf(c.id) !== -1;
      input.disabled = inUse.indexOf(c.id) !== -1;
      var label = document.createElement('label');
      label.className = 'd-flex gap-2 align-items-center'; label.htmlFor = id;
      label.append(input, document.createTextNode(c.name));
      if (input.disabled) { var s = document.createElement('span'); s.className = 'text-dim small'; s.textContent = '(has entity pairs)'; label.append(s); }
      return label;
    }));
    $('mp_save').disabled = !connections.length;
    if (!connections.length) showError('Create a connection first — a mapping needs an origin system.');
    modal.show();
  }

  function save() {
    clearError();
    var name = $('mp_name').value.trim();
    if (!name) { showError('A name is required.'); return; }
    var origin = Number($('mp_source').value);
    var destinations = Array.prototype.filter.call($('mp_destinations').querySelectorAll('input'), function (i) { return i.checked; }).map(function (i) { return Number(i.value); });
    var body = { name: name, description: $('mp_description').value, destination_connections: destinations };
    if (!editing || !$('mp_source').disabled) body.source_connection = origin;
    var btn = $('mp_save');
    btn.disabled = true;
    (editing ? api('PATCH', '/api/mappings/' + editing.id + '/', body) : api('POST', '/api/mappings/', body))
      .then(function (saved) {
        if (editing) location.reload(); else location.href = '/mappings/' + saved.id + '/?tab=canvas';
      })
      .catch(function (e) { showError(e.message); btn.disabled = false; });
  }

  function remove(id) {
    var m = byId(id);
    if (!m) return;
    var msg = 'Delete the mapping “' + m.name + '”? Its ' + m.entity_pairs_count + ' entity pair(s), field mappings and ' + m.runs_count + ' run(s) are deleted too, and it is removed from any plan that uses it. This can\'t be undone.';
    confirmModal(msg).then(function (yes) {
      if (!yes) return;
      api('DELETE', '/api/mappings/' + id + '/').then(function () {
        // Deleting the mapping you are looking at leaves nothing to show: go back to the list.
        if (location.pathname.replace(/\/$/, '') === '/mappings/' + id) location.href = '/mappings/'; else location.reload();
      }).catch(function (e) { alert(e.message); });
    });
  }

  function duplicate(id) {
    api('POST', '/api/mappings/' + id + '/duplicate/', {}).then(function (copy) {
      location.href = '/mappings/' + copy.id + '/?tab=canvas';
    }).catch(function (e) { alert(e.message); });
  }

  $('mp_save').addEventListener('click', save);
  modalEl.addEventListener('keydown', function (e) { if (e.key === 'Enter' && e.target.id === 'mp_name') { e.preventDefault(); save(); } });
  document.addEventListener('click', function (e) {
    var t = e.target.closest('[data-mapping-edit],[data-mapping-delete],[data-mapping-duplicate],#newMappingBtn');
    if (!t) return;
    if (t.id === 'newMappingBtn') openDialog(null);
    else if (t.hasAttribute('data-mapping-edit')) openDialog(byId(Number(t.getAttribute('data-mapping-edit'))));
    else if (t.hasAttribute('data-mapping-delete')) remove(Number(t.getAttribute('data-mapping-delete')));
    else duplicate(Number(t.getAttribute('data-mapping-duplicate')));
  });
})();
