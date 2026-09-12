/* Shared connection CRUD used from both the App Store's "Installed" tab
   (templates/integrations/list.html) and a connection's own detail page
   (templates/connections/detail.html) — create, edit, and delete all just
   drive connections.ConnectionViewSet (a plain ModelViewSet, full CRUD
   already), the only new part here is the UI. Custom headers/params use the
   shared kv_editor.js (prefixes 'c' for create, 'ec' for edit) — sent only
   when their "use custom ..." checkbox is on, per use_custom_headers/
   use_custom_params on the Connection model. */

let editingConnectionId = null;

async function createConnection() {
  const errorBox = document.getElementById('createError');
  errorBox.classList.add('d-none');

  let authConfig = {};
  const raw = document.getElementById('c_auth_config').value.trim();
  if (raw) {
    try { authConfig = JSON.parse(raw); }
    catch (e) { errorBox.textContent = 'Auth config must be valid JSON.'; errorBox.classList.remove('d-none'); return; }
  }

  const resp = await fetch('/api/connections/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name: document.getElementById('c_name').value,
      base_url: document.getElementById('c_base_url').value,
      auth_type: document.getElementById('c_auth_type').value,
      auth_config: authConfig,
      use_custom_headers: document.getElementById('c_use_custom_headers').checked,
      custom_headers: getKvObject('c_headers'),
      use_custom_params: document.getElementById('c_use_custom_params').checked,
      custom_params: getKvObject('c_params'),
      rate_limit_per_second: rpmToRps(document.getElementById('c_rate_limit').value),
    }),
  });

  if (resp.ok) {
    window.location.reload();
  } else {
    const body = await resp.json().catch(() => ({}));
    errorBox.textContent = JSON.stringify(body);
    errorBox.classList.remove('d-none');
  }
}

async function openEditConnectionModal(id) {
  editingConnectionId = id;
  const errorBox = document.getElementById('editConnectionError');
  errorBox.classList.add('d-none');

  const resp = await fetch(`/api/connections/${id}/`);
  const connection = await resp.json();
  document.getElementById('ec_name').value = connection.name;
  document.getElementById('ec_base_url').value = connection.base_url;
  document.getElementById('ec_auth_type').value = connection.auth_type;
  document.getElementById('ec_auth_config').value = JSON.stringify(connection.auth_config || {}, null, 2);
  document.getElementById('ec_rate_limit').value = rpsToRpm(connection.rate_limit_per_second);

  document.getElementById('ec_use_custom_headers').checked = connection.use_custom_headers;
  setKvRows('ec_headers', connection.custom_headers);
  toggleKvSection(document.getElementById('ec_use_custom_headers'), 'ec_headers_section');

  document.getElementById('ec_use_custom_params').checked = connection.use_custom_params;
  setKvRows('ec_params', connection.custom_params);
  toggleKvSection(document.getElementById('ec_use_custom_params'), 'ec_params_section');

  new bootstrap.Modal(document.getElementById('editConnectionModal')).show();
}

async function saveConnectionEdit() {
  const errorBox = document.getElementById('editConnectionError');
  errorBox.classList.add('d-none');

  let authConfig = {};
  const raw = document.getElementById('ec_auth_config').value.trim();
  if (raw) {
    try { authConfig = JSON.parse(raw); }
    catch (e) { errorBox.textContent = 'Auth config must be valid JSON.'; errorBox.classList.remove('d-none'); return; }
  }

  const resp = await fetch(`/api/connections/${editingConnectionId}/`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name: document.getElementById('ec_name').value,
      base_url: document.getElementById('ec_base_url').value,
      auth_type: document.getElementById('ec_auth_type').value,
      auth_config: authConfig,
      use_custom_headers: document.getElementById('ec_use_custom_headers').checked,
      custom_headers: getKvObject('ec_headers'),
      use_custom_params: document.getElementById('ec_use_custom_params').checked,
      custom_params: getKvObject('ec_params'),
      rate_limit_per_second: rpmToRps(document.getElementById('ec_rate_limit').value),
    }),
  });

  if (resp.ok) {
    window.location.reload();
  } else {
    const body = await resp.json().catch(() => ({}));
    errorBox.textContent = JSON.stringify(body);
    errorBox.classList.remove('d-none');
  }
}

async function deleteConnection(id, name, redirectTo) {
  const ok = await confirmModal(
    `Delete connection "${name}"? This can't be undone — its entities, and any ` +
    `mapping where it's the source (with that mapping's runs), get deleted too.`
  );
  if (!ok) return;

  const resp = await fetch(`/api/connections/${id}/`, { method: 'DELETE' });
  if (resp.ok) {
    // Default to the current full URL (path + query) so e.g. deleting from
    // the App Store's Installed tab (?tab=installed) doesn't bounce back to Catalog.
    window.location.href = redirectTo || window.location.href;
  } else {
    alert('Could not delete this connection.');
  }
}
