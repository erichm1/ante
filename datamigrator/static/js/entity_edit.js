/* Shared Entity/Field CRUD — used by both the mapping canvas
   (templates/mappings/canvas.html, where an entity is edited in place on
   the board) and the standalone Entities page
   (templates/schemas/entities.html, the actual "manage entities" CRUD for
   a connection). Both pages provide the same #editEntityModal markup and
   set window.FIELD_TYPES before calling into this file, so the same
   open/save logic works identically in either place.

   Reused elsewhere: run_snapshot/run_list/plan_detail all *display* a
   field's field_type (see jobs/_route.html's entity_pairs, and
   EntitySerializer.fields nesting FieldSerializer) but only this file and
   the canvas actually edit it — the type shown everywhere else always
   comes from the same Field.field_type this modal writes. */

let editingEntityId = null;
let deletedFieldIds = [];

function fieldTypeOptions(selected) {
  return (window.FIELD_TYPES || []).map(([value, label]) =>
    `<option value="${value}" ${value === selected ? 'selected' : ''}>${label}</option>`
  ).join('');
}

function renderFieldEditRow(field) {
  return `
    <div class="d-flex gap-2 mb-2 align-items-center field-edit-row" data-field-id="${field.id || ''}">
      <input class="form-control form-control-sm" placeholder="Field name" value="${field.name || ''}">
      <select class="form-select form-select-sm" style="max-width: 150px;">${fieldTypeOptions(field.field_type || 'string')}</select>
      <div class="form-check mb-0 text-nowrap">
        <input type="checkbox" class="form-check-input" ${field.required ? 'checked' : ''}>
        <label class="form-check-label small">required</label>
      </div>
      <button type="button" class="btn btn-sm btn-outline-danger" onclick="removeFieldEditRow(this)">&times;</button>
    </div>`;
}

function addFieldEditRow() {
  document.getElementById('eeNoFields')?.remove();
  document.getElementById('ee_fields').insertAdjacentHTML('beforeend', renderFieldEditRow({}));
}

function removeFieldEditRow(button) {
  const row = button.closest('.field-edit-row');
  if (row.dataset.fieldId) deletedFieldIds.push(row.dataset.fieldId);
  row.remove();
}

async function openEditEntityModal(entityId) {
  editingEntityId = entityId;
  deletedFieldIds = [];
  document.getElementById('editEntityError').classList.add('d-none');

  const resp = await fetch(`/api/entities/${entityId}/`);
  const entity = await resp.json();
  document.getElementById('ee_name').value = entity.name;
  document.getElementById('ee_endpoint_path').value = entity.endpoint_path;
  document.getElementById('ee_fields').innerHTML = entity.fields.map(renderFieldEditRow).join('') ||
    '<p class="text-dim small mb-0" id="eeNoFields">No fields yet — add one below.</p>';

  new bootstrap.Modal(document.getElementById('editEntityModal')).show();
}

async function saveEntityEdit() {
  const errorBox = document.getElementById('editEntityError');
  errorBox.classList.add('d-none');

  const name = document.getElementById('ee_name').value.trim();
  const endpointPath = document.getElementById('ee_endpoint_path').value.trim();
  if (!name) {
    errorBox.textContent = 'Name is required.';
    errorBox.classList.remove('d-none');
    return;
  }

  const entityResp = await fetch(`/api/entities/${editingEntityId}/`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, endpoint_path: endpointPath }),
  });
  if (!entityResp.ok) {
    errorBox.textContent = JSON.stringify(await entityResp.json());
    errorBox.classList.remove('d-none');
    return;
  }

  for (const row of document.querySelectorAll('#ee_fields .field-edit-row')) {
    const fieldId = row.dataset.fieldId;
    const [nameInput, typeSelect] = row.querySelectorAll('input, select');
    const checkbox = row.querySelector('input[type="checkbox"]');
    const fieldName = nameInput.value.trim();
    if (!fieldName) continue;

    const payload = { name: fieldName, field_type: typeSelect.value, required: checkbox.checked, entity: editingEntityId };
    const url = fieldId ? `/api/fields/${fieldId}/` : '/api/fields/';
    const method = fieldId ? 'PATCH' : 'POST';
    const fieldResp = await fetch(url, {
      method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    });
    if (!fieldResp.ok) {
      errorBox.textContent = `Failed to save field "${fieldName}": ${JSON.stringify(await fieldResp.json())}`;
      errorBox.classList.remove('d-none');
      return;
    }
  }

  for (const fieldId of deletedFieldIds) {
    await fetch(`/api/fields/${fieldId}/`, { method: 'DELETE' });
  }

  window.location.reload();
}

// ---- Whole-entity create/delete (schemas/entities.html's own CRUD) --------

async function createEntity(connectionId) {
  const errorBox = document.getElementById('addEntityError');
  errorBox.classList.add('d-none');

  const name = document.getElementById('ae_name').value.trim();
  const endpointPath = document.getElementById('ae_endpoint_path').value.trim();
  if (!name) {
    errorBox.textContent = 'Name is required.';
    errorBox.classList.remove('d-none');
    return;
  }

  const resp = await fetch('/api/entities/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ connection: connectionId, name, endpoint_path: endpointPath, source: 'manual' }),
  });
  if (resp.ok) {
    window.location.reload();
  } else {
    errorBox.textContent = JSON.stringify(await resp.json());
    errorBox.classList.remove('d-none');
  }
}

async function deleteEntity(entityId, name) {
  const ok = await confirmModal(
    `Delete entity "${name}"? This can't be undone — its fields, and any entity ` +
    `pairing (in any mapping) that uses it as a source or target, get deleted too.`
  );
  if (!ok) return;
  const resp = await fetch(`/api/entities/${entityId}/`, { method: 'DELETE' });
  if (resp.ok) window.location.reload();
}
