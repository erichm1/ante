/* Generic key/value list editor, shared by every "custom headers" / "custom
   params" checkbox+editor pair across the app (integration install form, Add
   connection modal, Edit connection modal) — keyed by a `prefix` string so
   several instances can coexist on one page without stepping on each other. */

const kvEditorState = {};

function escapeKvAttr(s) {
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function renderKvRows(prefix) {
  const box = document.getElementById(`${prefix}_list`);
  if (!box) return;
  const rows = kvEditorState[prefix] || [];
  box.innerHTML = rows.length ? rows.map((r, i) => `
    <div class="d-flex gap-2 align-items-center mb-1">
      <input type="text" class="form-control form-control-sm" placeholder="Name" value="${escapeKvAttr(r.key)}" oninput="updateKvRow('${prefix}', ${i}, 'key', this.value)">
      <input type="text" class="form-control form-control-sm" placeholder="Value" value="${escapeKvAttr(r.value)}" oninput="updateKvRow('${prefix}', ${i}, 'value', this.value)">
      <button type="button" class="btn btn-sm btn-outline-danger" onclick="removeKvRow('${prefix}', ${i})">&times;</button>
    </div>`).join('') : '<p class="text-dim small mb-1">None yet.</p>';
}

function addKvRow(prefix) {
  kvEditorState[prefix] = kvEditorState[prefix] || [];
  kvEditorState[prefix].push({ key: '', value: '' });
  renderKvRows(prefix);
}

function removeKvRow(prefix, idx) {
  kvEditorState[prefix].splice(idx, 1);
  renderKvRows(prefix);
}

function updateKvRow(prefix, idx, field, value) {
  kvEditorState[prefix][idx][field] = value;
}

// Seeds the editor from an existing {key: value} object (e.g. when opening
// Edit Connection) — replaces whatever rows this prefix already had.
function setKvRows(prefix, obj) {
  kvEditorState[prefix] = Object.entries(obj || {}).map(([key, value]) => ({ key, value: String(value) }));
  renderKvRows(prefix);
}

// Collects the current rows back into a plain {key: value} object, dropping
// any row whose name is still blank.
function getKvObject(prefix) {
  const obj = {};
  (kvEditorState[prefix] || []).forEach(r => {
    if (r.key.trim()) obj[r.key.trim()] = r.value;
  });
  return obj;
}

// Shows/hides the editor block tied to a "use custom X" checkbox.
function toggleKvSection(checkbox, sectionId) {
  document.getElementById(sectionId).classList.toggle('d-none', !checkbox.checked);
}
