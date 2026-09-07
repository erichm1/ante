/* Shared connection CRUD used from both the App Store's "Installed" tab
   (templates/integrations/list.html) and a connection's own detail page
   (templates/connections/detail.html) — create, edit, and delete all just
   drive connections.ConnectionViewSet (a plain ModelViewSet, full CRUD
   already), the only new part here is the UI. */

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
  const ok = confirm(
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
