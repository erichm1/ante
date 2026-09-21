/* Administration panel on the profile page (administrators only): CRUD for users, access groups and
   departments, with every module switched on/off per group and per user.

   Talks to /api/admin-users|groups|departments|modules/. All text from the server (names, emails…) is
   put on the page with textContent — never innerHTML. */
(function () {
  'use strict';
  var root = document.getElementById('adminPanel');
  if (!root) return;
  var config = JSON.parse(document.getElementById('adminConfig').textContent || '{}');
  var state = { users: [], groups: [], departments: [], modules: [] };

  // ── tiny helpers ────────────────────────────────────────────────────────────
  function h(tag, attrs) {
    var el = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (v == null || v === false) return;
      if (k === 'text') el.textContent = v;
      else if (k === 'class') el.className = v;
      else if (k.slice(0, 2) === 'on') el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? '' : v);
    });
    for (var i = 2; i < arguments.length; i++) {
      var c = arguments[i];
      if (c == null || c === false) continue;
      (Array.isArray(c) ? c : [c]).forEach(function (n) { el.append(n.nodeType ? n : document.createTextNode(n)); });
    }
    return el;
  }
  function $(id) { return document.getElementById(id); }
  function byId(list, id) { return list.filter(function (x) { return x.id === id; })[0]; }
  function moduleLabel(key) { var m = state.modules.filter(function (x) { return x.key === key; })[0]; return m ? m.label : key; }

  function api(method, url, body) {
    return fetch(url, {
      method: method, credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    }).then(function (r) {
      if (r.status === 204) return null;
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) { var e = new Error(flatten(data) || ('Request failed (' + r.status + ')')); e.data = data; throw e; }
        return data;
      });
    });
  }
  // DRF errors are {field: [msgs]}, {detail: msg}, {non_field_errors: [...]} or a bare list.
  function flatten(d) {
    if (!d) return '';
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) return d.map(flatten).join(' ');
    return Object.keys(d).map(function (k) {
      var msg = flatten(d[k]);
      return (k === 'detail' || k === 'non_field_errors' || k === 'error') ? msg : k.replace(/_/g, ' ') + ': ' + msg;
    }).join(' · ');
  }
  function showError(msg) { var a = $('adminAlert'); a.textContent = msg; a.classList.remove('d-none'); }
  function clearError() { $('adminAlert').classList.add('d-none'); }
  function guarded(fn) { return function () { clearError(); return Promise.resolve().then(fn.bind(null, arguments[0])).catch(function (e) { showError(e.message); }); }; }

  function chip(text, cls, title) { return h('span', { class: 'status-chip ' + (cls || ''), title: title || null, text: text }); }

  // ── dialog ─────────────────────────────────────────────────────────────────
  // dialog({title, body, saveLabel, onSave}) — onSave returns a promise; a rejection is shown inside the dialog.
  function dialog(opts) {
    var err = h('div', { class: 'alert alert-danger py-2 d-none', role: 'alert' });
    var save = h('button', { type: 'button', class: 'btn btn-signal', text: opts.saveLabel || 'Save' });
    var el = h('div', { class: 'modal fade', tabindex: '-1' },
      h('div', { class: 'modal-dialog modal-dialog-scrollable modal-lg' },
        h('div', { class: 'modal-content' },
          h('div', { class: 'modal-header' }, h('h5', { class: 'modal-title', text: opts.title }), h('button', { type: 'button', class: 'btn-close', 'data-bs-dismiss': 'modal', 'aria-label': 'Close' })),
          h('div', { class: 'modal-body' }, err, opts.body),
          h('div', { class: 'modal-footer' }, h('button', { type: 'button', class: 'btn btn-outline-secondary', 'data-bs-dismiss': 'modal', text: 'Cancel' }), save))));
    document.body.append(el);
    var modal = new bootstrap.Modal(el);
    el.addEventListener('hidden.bs.modal', function () { modal.dispose(); el.remove(); });
    save.addEventListener('click', function () {
      err.classList.add('d-none');
      save.disabled = true;
      Promise.resolve().then(opts.onSave).then(function () { modal.hide(); }).catch(function (e) {
        err.textContent = e.message; err.classList.remove('d-none'); el.querySelector('.modal-body').scrollTop = 0;
      }).then(function () { save.disabled = false; });
    });
    modal.show();
    var first = el.querySelector('input:not([type=hidden]),select,textarea');
    el.addEventListener('shown.bs.modal', function () { if (first) first.focus(); });
    return modal;
  }

  function field(label, control, help, id) {
    return h('div', { class: 'mb-3' }, h('label', { class: 'form-label', for: id || null, text: label }), control, help ? h('div', { class: 'form-text', text: help }) : null);
  }
  function input(id, value, attrs) {
    var el = h('input', Object.assign({ class: 'form-control', id: id, autocomplete: 'off' }, attrs || {}));
    el.value = value == null ? '' : value;
    return el;
  }
  function toggle(id, label, checked, help) {
    var box = h('input', { class: 'form-check-input', type: 'checkbox', role: 'switch', id: id });
    box.checked = !!checked;
    return { box: box, el: h('div', { class: 'form-check form-switch mb-2' }, box, h('label', { class: 'form-check-label', for: id, text: label }), help ? h('div', { class: 'form-text mt-0', text: help }) : null) };
  }

  // ── module switchboards ────────────────────────────────────────────────────
  // Group: one on/off switch per module.
  function groupSwitches(selected) {
    var boxes = {};
    var list = h('div', { class: 'perm-grid' }, state.modules.map(function (m) {
      var t = toggle('gm_' + m.key, m.label, selected.indexOf(m.key) !== -1, m.description);
      boxes[m.key] = t.box;
      return h('div', { class: 'perm-cell' }, h('i', { class: m.icon + ' perm-icon' }), t.el);
    }));
    function setAll(on) { Object.keys(boxes).forEach(function (k) { boxes[k].checked = on; }); }
    return {
      el: h('div', {}, h('div', { class: 'd-flex gap-2 mb-2' },
        h('button', { type: 'button', class: 'btn btn-sm btn-outline-secondary', text: 'All on', onclick: function () { setAll(true); } }),
        h('button', { type: 'button', class: 'btn btn-sm btn-outline-secondary', text: 'All off', onclick: function () { setAll(false); } })), list),
      read: function () { return Object.keys(boxes).filter(function (k) { return boxes[k].checked; }); },
    };
  }

  // User: per module Inherit / On / Off, plus a live "effective access" readout.
  function userSwitches(user, groupBoxes) {
    var value = {};       // key -> 'inherit' | 'on' | 'off'
    var rows = {};
    var adminBox = null;  // set by the caller once it exists
    state.modules.forEach(function (m) {
      value[m.key] = user.denied_modules.indexOf(m.key) !== -1 ? 'off' : user.allowed_modules.indexOf(m.key) !== -1 ? 'on' : 'inherit';
    });

    function fromGroups() {
      var got = {};
      groupBoxes().forEach(function (id) { var g = byId(state.groups, id); if (g) g.modules.forEach(function (k) { got[k] = true; }); });
      return got;
    }
    function refresh() {
      var got = fromGroups();
      var isAdmin = adminBox && adminBox.checked;
      state.modules.forEach(function (m) {
        var v = value[m.key];
        var effective = isAdmin || v === 'on' || (v !== 'off' && got[m.key]);
        var r = rows[m.key];
        r.status.textContent = isAdmin ? 'Admin — always on' : effective ? (v === 'on' ? 'On (set for this user)' : 'On via group') : (v === 'off' ? 'Off (set for this user)' : 'Off');
        r.status.className = 'perm-status ' + (effective ? 'is-on' : 'is-off');
        r.btns.forEach(function (b) { b.disabled = !!isAdmin; b.classList.toggle('active', b.dataset.v === v); b.setAttribute('aria-pressed', b.dataset.v === v ? 'true' : 'false'); });
      });
    }
    var el = h('div', { class: 'perm-list' }, state.modules.map(function (m) {
      var btns = [['inherit', 'Inherit'], ['on', 'On'], ['off', 'Off']].map(function (o) {
        return h('button', { type: 'button', class: 'btn btn-sm btn-outline-secondary perm-' + o[0], 'data-v': o[0], text: o[1], 'aria-pressed': 'false',
          onclick: function () { value[m.key] = o[0]; refresh(); } });
      });
      var status = h('span', { class: 'perm-status' });
      rows[m.key] = { btns: btns, status: status };
      return h('div', { class: 'perm-row' },
        h('div', { class: 'perm-name' }, h('i', { class: m.icon + ' perm-icon' }), h('span', {}, h('b', { text: m.label }), h('br'), h('small', { class: 'text-dim', text: m.description }))),
        h('div', { class: 'perm-ctl' }, h('div', { class: 'btn-group', role: 'group', 'aria-label': m.label }, btns), status));
    }));
    return {
      el: el, refresh: refresh, bindAdmin: function (box) { adminBox = box; box.addEventListener('change', refresh); },
      read: function () {
        return {
          allowed_modules: state.modules.map(function (m) { return m.key; }).filter(function (k) { return value[k] === 'on'; }),
          denied_modules: state.modules.map(function (m) { return m.key; }).filter(function (k) { return value[k] === 'off'; }),
        };
      },
    };
  }

  // ── data ───────────────────────────────────────────────────────────────────
  function load() {
    return Promise.all([api('GET', '/api/admin-modules/'), api('GET', '/api/admin-groups/'), api('GET', '/api/admin-departments/'), api('GET', '/api/admin-users/')])
      .then(function (r) { state.modules = r[0]; state.groups = r[1]; state.departments = r[2]; state.users = r[3]; render(); })
      .catch(function (e) { showError(e.message); });
  }
  function reload() { clearError(); return load(); }

  // ── rendering ──────────────────────────────────────────────────────────────
  function render() {
    root.querySelector('[data-count="users"]').textContent = state.users.length;
    root.querySelector('[data-count="groups"]').textContent = state.groups.length;
    root.querySelector('[data-count="departments"]').textContent = state.departments.length;
    fillFilters();
    renderUsers(); renderGroups(); renderDepartments();
  }
  function fillFilters() {
    var d = $('auDept'), g = $('auGroup'), dv = d.value, gv = g.value;
    d.replaceChildren(h('option', { value: '', text: 'Any' }));
    state.departments.forEach(function (x) { d.append(h('option', { value: x.id, text: x.name })); });
    g.replaceChildren(h('option', { value: '', text: 'Any' }));
    state.groups.forEach(function (x) { g.append(h('option', { value: x.id, text: x.name })); });
    d.value = dv; g.value = gv;
  }

  function actions(onEdit, onDelete, deleteDisabledTitle) {
    return h('td', { class: 'text-end text-nowrap' },
      h('button', { type: 'button', class: 'btn btn-sm btn-outline-secondary me-1', title: 'Edit', 'aria-label': 'Edit', onclick: guarded(onEdit) }, h('i', { class: 'bi-pencil' })),
      h('button', { type: 'button', class: 'btn btn-sm btn-outline-danger', title: deleteDisabledTitle || 'Delete', 'aria-label': 'Delete', disabled: !!deleteDisabledTitle, onclick: guarded(onDelete) }, h('i', { class: 'bi-trash' })));
  }
  function empty(cols, text) { return h('tr', {}, h('td', { colspan: cols, class: 'text-dim text-center py-4', text: text })); }

  function renderUsers() {
    var q = $('auSearch').value.trim().toLowerCase(), dept = $('auDept').value, grp = $('auGroup').value;
    var rows = state.users.filter(function (u) {
      if (dept && String(u.department) !== dept) return false;
      if (grp && u.groups.map(String).indexOf(grp) === -1) return false;
      return !q || [u.username, u.first_name, u.last_name, u.email].join(' ').toLowerCase().indexOf(q) !== -1;
    });
    $('auRows').replaceChildren.apply($('auRows'), rows.length ? rows.map(function (u) {
      var full = (u.first_name + ' ' + u.last_name).trim();
      var d = byId(state.departments, u.department);
      var isAdmin = u.is_staff || u.is_superuser;
      return h('tr', { class: u.is_active ? '' : 'text-dim' },
        h('td', {}, h('div', { class: 'fw-medium', text: u.username }), full ? h('div', { class: 'small text-dim', text: full }) : null, u.email ? h('div', { class: 'small text-dim', text: u.email }) : null,
          h('div', { class: 'mt-1 d-flex gap-1 flex-wrap' }, u.is_superuser ? chip('Superuser', 'ok') : isAdmin ? chip('Admin', 'ok') : null, !u.is_active ? chip('Inactive', 'err') : null, u.is_you ? chip('You') : null)),
        h('td', { text: d ? d.name : '—' }),
        h('td', {}, u.groups.length ? h('div', { class: 'd-flex gap-1 flex-wrap' }, u.groups.map(function (id) { var g = byId(state.groups, id); return chip(g ? g.name : '#' + id); })) : '—'),
        h('td', {}, h('span', { title: u.effective_modules.map(moduleLabel).join(', ') || 'No modules', text: isAdmin ? 'All (' + state.modules.length + ')' : u.effective_modules.length + ' of ' + state.modules.length })),
        actions(function () { return editUser(u); }, function () { return removeUser(u); }, u.is_you ? 'You can’t delete your own account' : null));
    }) : [empty(5, 'No users match.')]);
  }
  function renderGroups() {
    $('agRows').replaceChildren.apply($('agRows'), state.groups.length ? state.groups.map(function (g) {
      return h('tr', {},
        h('td', {}, h('div', { class: 'fw-medium', text: g.name }), g.description ? h('div', { class: 'small text-dim', text: g.description }) : null, g.is_default ? h('div', { class: 'mt-1' }, chip('Default for new users', 'ok')) : null),
        h('td', {}, g.modules.length ? h('div', { class: 'd-flex gap-1 flex-wrap' }, g.modules.map(function (k) { return chip(moduleLabel(k)); })) : h('span', { class: 'text-dim', text: 'None' })),
        h('td', { text: g.member_count }),
        actions(function () { return editGroup(g); }, function () { return removeGroup(g); }));
    }) : [empty(4, 'No groups yet.')]);
  }
  function renderDepartments() {
    $('adRows').replaceChildren.apply($('adRows'), state.departments.length ? state.departments.map(function (d) {
      return h('tr', {}, h('td', { class: 'fw-medium', text: d.name }), h('td', { class: 'text-dim', text: d.description || '—' }), h('td', { text: d.member_count }),
        actions(function () { return editDepartment(d); }, function () { return removeDepartment(d); }));
    }) : [empty(4, 'No departments yet.')]);
  }

  // ── departments ────────────────────────────────────────────────────────────
  function editDepartment(d) {
    var name = input('dpName', d && d.name, { maxlength: 100 }), desc = input('dpDesc', d && d.description, { maxlength: 255 });
    dialog({
      title: d ? 'Edit department' : 'New department', saveLabel: d ? 'Save' : 'Create',
      body: h('div', {}, field('Name', name, null, 'dpName'), field('Description', desc, 'Optional.', 'dpDesc')),
      onSave: function () {
        var body = { name: name.value, description: desc.value };
        return (d ? api('PATCH', '/api/admin-departments/' + d.id + '/', body) : api('POST', '/api/admin-departments/', body)).then(reload);
      },
    });
  }
  function removeDepartment(d) {
    return confirmModal('Delete the department “' + d.name + '”? ' + (d.member_count ? d.member_count + ' member(s) will simply be left without a department.' : '')).then(function (yes) {
      if (yes) return api('DELETE', '/api/admin-departments/' + d.id + '/').then(reload);
    });
  }

  // ── groups ─────────────────────────────────────────────────────────────────
  function editGroup(g) {
    var name = input('grName', g && g.name, { maxlength: 100 }), desc = input('grDesc', g && g.description, { maxlength: 255 });
    var def = toggle('grDefault', 'Add to every newly created user', g && g.is_default, 'Existing users are not affected.');
    var sw = groupSwitches(g ? g.modules : []);
    dialog({
      title: g ? 'Edit group' : 'New group', saveLabel: g ? 'Save' : 'Create',
      body: h('div', {}, field('Name', name, null, 'grName'), field('Description', desc, 'Optional.', 'grDesc'), def.el,
        h('div', { class: 'form-label mt-3', text: 'Modules this group switches on' }), sw.el),
      onSave: function () {
        var body = { name: name.value, description: desc.value, is_default: def.box.checked, modules: sw.read() };
        return (g ? api('PATCH', '/api/admin-groups/' + g.id + '/', body) : api('POST', '/api/admin-groups/', body)).then(reload);
      },
    });
  }
  function removeGroup(g) {
    return confirmModal('Delete the group “' + g.name + '”? ' + (g.member_count ? g.member_count + ' member(s) will lose the access it gave them.' : '')).then(function (yes) {
      if (yes) return api('DELETE', '/api/admin-groups/' + g.id + '/').then(reload);
    });
  }

  // ── users ──────────────────────────────────────────────────────────────────
  function editUser(u) {
    var isNew = !u;
    var user = u || { username: '', first_name: '', last_name: '', email: '', is_active: true, is_staff: false, is_superuser: false, department: null, groups: [], allowed_modules: [], denied_modules: [], is_you: false };
    var username = input('usName', user.username, { maxlength: 150 });
    var first = input('usFirst', user.first_name, { maxlength: 150 }), last = input('usLast', user.last_name, { maxlength: 150 });
    var email = input('usEmail', user.email, { type: 'email' });
    var pw = input('usPw', '', { type: 'password', autocomplete: 'new-password', placeholder: isNew ? '' : 'Leave blank to keep the current one' });
    var active = toggle('usActive', 'Active — may sign in', user.is_active, user.is_you ? 'You can’t deactivate your own account.' : null);
    var staff = toggle('usStaff', 'Administrator', user.is_staff || user.is_superuser, user.is_you ? 'You can’t remove your own administrator access.' : 'Sees every module and manages users, groups and departments.');
    var su = toggle('usSu', 'Superuser', user.is_superuser, config.iAmSuperuser ? 'Full Django-admin rights as well.' : 'Only a superuser can change this.');
    if (user.is_you) { active.box.disabled = true; staff.box.disabled = true; }
    if (!config.iAmSuperuser) su.box.disabled = true;
    su.box.addEventListener('change', function () { if (su.box.checked) staff.box.checked = true; });

    var dept = h('select', { class: 'form-select', id: 'usDept' }, h('option', { value: '', text: '— none —' }), state.departments.map(function (d) { return h('option', { value: d.id, text: d.name }); }));
    dept.value = user.department == null ? '' : String(user.department);

    var groupBoxes = {};
    var groupList = state.groups.length ? state.groups.map(function (g) {
      var t = toggle('usG' + g.id, g.name, user.groups.indexOf(g.id) !== -1, g.modules.length ? g.modules.map(moduleLabel).join(', ') : 'No modules');
      groupBoxes[g.id] = t.box;
      t.box.addEventListener('change', function () { perms.refresh(); });
      return t.el;
    }) : [h('div', { class: 'text-dim small', text: 'No groups yet — create one in the Groups tab.' })];
    var perms = userSwitches(user, function () { return Object.keys(groupBoxes).filter(function (id) { return groupBoxes[id].checked; }).map(Number); });
    perms.bindAdmin(staff.box);
    perms.refresh();

    dialog({
      title: isNew ? 'New user' : 'Edit ' + user.username, saveLabel: isNew ? 'Create user' : 'Save',
      body: h('div', {},
        h('div', { class: 'row g-3' },
          h('div', { class: 'col-md-6' }, field('Username', username, null, 'usName')),
          h('div', { class: 'col-md-6' }, field(isNew ? 'Password' : 'New password', pw, null, 'usPw')),
          h('div', { class: 'col-md-6' }, field('First name', first, null, 'usFirst')),
          h('div', { class: 'col-md-6' }, field('Last name', last, null, 'usLast')),
          h('div', { class: 'col-md-6' }, field('Email', email, null, 'usEmail')),
          h('div', { class: 'col-md-6' }, field('Department', dept, null, 'usDept'))),
        active.el, staff.el, su.el,
        h('div', { class: 'form-label mt-3', text: 'Groups' }), h('div', { class: 'mb-3' }, groupList),
        h('div', { class: 'form-label', text: 'Modules — on / off for this user' }),
        h('div', { class: 'form-text mb-2', text: 'Inherit uses what the user’s groups give. On or Off overrides that for this user only; Off always wins.' }),
        perms.el),
      onSave: function () {
        var body = Object.assign({
          username: username.value, first_name: first.value, last_name: last.value, email: email.value,
          is_active: active.box.checked, is_staff: staff.box.checked || su.box.checked, is_superuser: su.box.checked,
          department: dept.value ? Number(dept.value) : null,
          groups: Object.keys(groupBoxes).filter(function (id) { return groupBoxes[id].checked; }).map(Number),
        }, perms.read());
        if (pw.value) body.password = pw.value;
        if (!config.iAmSuperuser) delete body.is_superuser;              // the server refuses a non-superuser sending it
        return (isNew ? api('POST', '/api/admin-users/', body) : api('PATCH', '/api/admin-users/' + user.id + '/', body)).then(reload);
      },
    });
  }
  function removeUser(u) {
    return confirmModal('Delete the user “' + u.username + '”? This cannot be undone. (To keep their history but stop them signing in, deactivate them instead.)').then(function (yes) {
      if (yes) return api('DELETE', '/api/admin-users/' + u.id + '/').then(reload);
    });
  }

  // ── tabs & wiring ──────────────────────────────────────────────────────────
  root.querySelectorAll('[data-admin-tab]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      root.querySelectorAll('[data-admin-tab]').forEach(function (b) { b.classList.toggle('active', b === btn); });
      root.querySelectorAll('[data-admin-pane]').forEach(function (p) { p.classList.toggle('d-none', p.dataset.adminPane !== btn.dataset.adminTab); });
      clearError();
    });
  });
  $('auSearch').addEventListener('input', renderUsers);
  $('auDept').addEventListener('change', renderUsers);
  $('auGroup').addEventListener('change', renderUsers);
  $('auNew').addEventListener('click', guarded(function () { return editUser(null); }));
  $('agNew').addEventListener('click', guarded(function () { return editGroup(null); }));
  $('adNew').addEventListener('click', guarded(function () { return editDepartment(null); }));

  load();
})();
