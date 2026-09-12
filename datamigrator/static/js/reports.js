/* templates/reports/detail.html's builder: native HTML5 drag-and-drop
   (no existing DnD pattern in this codebase to reuse — the canvas's
   jsPlumb is for drawing connectors, not for "drag a chip into a list",
   so this introduces plain draggable/dragover/drop fresh) for two things:
   dragging an Entity from the palette onto the canvas adds a section;
   dragging one of that entity's Fields onto its section adds/reorders a
   selected column. Every mutation re-fetches the report + preview from
   the API rather than patching local DOM state, so the page never drifts
   out of sync with what the CSV export would actually contain. */

let entitiesData = [];
let reportState = null;

function initReportDetail() {
  entitiesData = JSON.parse(document.getElementById('entities-data').textContent);
  renderEntityPalette();
  loadReport();
}

// ---- Palette (left panel) --------------------------------------------------

function renderEntityPalette() {
  const box = document.getElementById('entityPalette');
  if (!entitiesData.length) {
    box.innerHTML = '<p class="text-dim small mb-0">No entities discovered yet — add one from a connection\'s "Discover entities" tab first.</p>';
    return;
  }
  box.innerHTML = entitiesData.map(entity => `
    <div class="report-entity-group">
      <div class="report-entity-header" draggable="true" ondragstart="onEntityDragStart(event, ${entity.id})">
        <span>⠿</span> ${escapeHtml(entity.name)} <span class="text-dim">(${escapeHtml(entity.connection_name)})</span>
      </div>
      <div>
        ${entity.fields.map(f => `<span class="report-chip" draggable="true" ondragstart="onFieldDragStart(event, ${entity.id}, ${f.id}, '${escapeAttr(f.name)}')">${escapeHtml(f.name)}</span>`).join('') || '<span class="text-dim small">no fields</span>'}
      </div>
    </div>`).join('');
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function escapeAttr(s) {
  return String(s == null ? '' : s).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
}

function entityById(id) {
  return entitiesData.find(e => e.id === id);
}
function fieldById(entity, fieldId) {
  return entity ? entity.fields.find(f => f.id === fieldId) : null;
}

// ---- Drag sources -----------------------------------------------------------

function onEntityDragStart(event, entityId) {
  event.dataTransfer.setData('application/json', JSON.stringify({ kind: 'entity', entityId }));
}

function onFieldDragStart(event, entityId, fieldId, fieldName) {
  event.dataTransfer.setData('application/json', JSON.stringify({ kind: 'field', entityId, fieldId, fieldName }));
}

function onSelectedChipDragStart(event, sectionId, fieldId) {
  event.stopPropagation();
  event.dataTransfer.setData('application/json', JSON.stringify({ kind: 'reorder', sectionId, fieldId }));
}

// ---- Canvas (drop an entity to add a section) ------------------------------

function onCanvasDragOver(event) {
  event.preventDefault();
  document.getElementById('reportCanvas').classList.add('drag-over');
}
function onCanvasDragLeave(event) {
  document.getElementById('reportCanvas').classList.remove('drag-over');
}

async function onCanvasDrop(event) {
  event.preventDefault();
  document.getElementById('reportCanvas').classList.remove('drag-over');
  const payload = readPayload(event);
  if (!payload || payload.kind !== 'entity') return;
  await fetch(`/api/reports/${window.REPORT_ID}/sections/`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ entity_id: payload.entityId, field_ids: [] }),
  });
  loadReport();
}

function readPayload(event) {
  try {
    return JSON.parse(event.dataTransfer.getData('application/json'));
  } catch (e) {
    return null;
  }
}

// ---- Sections (right panel) ------------------------------------------------

async function loadReport() {
  const resp = await fetch(`/api/reports/${window.REPORT_ID}/`);
  reportState = await resp.json();
  renderSections();
  loadPreview();
}

function renderSections() {
  const sections = reportState.sections || [];
  document.getElementById('sectionCount').textContent = sections.length ? `${sections.length} section${sections.length === 1 ? '' : 's'}` : '';
  document.getElementById('emptyCanvasHint').classList.toggle('d-none', sections.length > 0);

  document.getElementById('sectionsList').innerHTML = sections.map((section, i) => `
    <div class="report-section">
      <div class="d-flex justify-content-between align-items-center mb-2">
        <div>
          <strong>${escapeHtml(section.entity_name)}</strong>
          <span class="text-dim small">— drag fields from the palette below</span>
        </div>
        <div>
          <button class="btn btn-sm btn-outline-secondary" onclick="moveSection(${section.id}, 'up')" ${i === 0 ? 'disabled' : ''}>↑</button>
          <button class="btn btn-sm btn-outline-secondary" onclick="moveSection(${section.id}, 'down')" ${i === sections.length - 1 ? 'disabled' : ''}>↓</button>
          <button class="btn btn-sm btn-outline-danger" onclick="removeSection(${section.id})">Remove</button>
        </div>
      </div>
      <div class="report-dropzone" ondragover="onSectionDragOver(event)" ondragleave="onSectionDragLeave(event)" ondrop="onSectionDrop(event, ${section.id}, ${section.entity})">
        ${section.field_ids.length
          ? section.field_ids.map((fid, idx) => `
              <span class="report-chip selected" draggable="true"
                    ondragstart="onSelectedChipDragStart(event, ${section.id}, ${fid})"
                    ondragover="onChipDragOver(event)"
                    ondrop="onChipDrop(event, ${section.id}, ${fid})">
                ${escapeHtml(section.field_names[idx] || '?')}
                <span class="chip-remove" onclick="removeFieldFromSection(${section.id}, ${fid})">&times;</span>
              </span>`).join('')
          : '<span class="text-dim small">Drop fields here to add columns.</span>'}
      </div>
    </div>`).join('');
}

// ---- Section drop zone (add a field, or reorder within the same section) --

function onSectionDragOver(event) {
  event.preventDefault();
  event.currentTarget.classList.add('drag-over');
}
function onSectionDragLeave(event) {
  event.currentTarget.classList.remove('drag-over');
}

async function onSectionDrop(event, sectionId, sectionEntityId) {
  event.preventDefault();
  event.stopPropagation();
  event.currentTarget.classList.remove('drag-over');
  const payload = readPayload(event);
  if (!payload) return;

  if (payload.kind === 'field') {
    if (payload.entityId !== sectionEntityId) {
      alert(`"${payload.fieldName}" belongs to a different entity than this section.`);
      return;
    }
    const section = reportState.sections.find(s => s.id === sectionId);
    if (section.field_ids.includes(payload.fieldId)) return; // already selected
    await patchSectionFields(sectionId, [...section.field_ids, payload.fieldId]);
  } else if (payload.kind === 'reorder' && payload.sectionId === sectionId) {
    // Dropped on empty space within its own section (not on another chip) — move to the end.
    const section = reportState.sections.find(s => s.id === sectionId);
    const rest = section.field_ids.filter(fid => fid !== payload.fieldId);
    await patchSectionFields(sectionId, [...rest, payload.fieldId]);
  }
}

// Dropping directly on another selected chip inserts before that chip instead
// of at the end — precise reordering, not just "move to end".
function onChipDragOver(event) {
  event.preventDefault();
  event.stopPropagation();
}

async function onChipDrop(event, sectionId, targetFieldId) {
  event.preventDefault();
  event.stopPropagation();
  const payload = readPayload(event);
  if (!payload) return;

  const section = reportState.sections.find(s => s.id === sectionId);
  if (payload.kind === 'reorder' && payload.sectionId === sectionId) {
    if (payload.fieldId === targetFieldId) return;
    const rest = section.field_ids.filter(fid => fid !== payload.fieldId);
    const targetIdx = rest.indexOf(targetFieldId);
    rest.splice(targetIdx, 0, payload.fieldId);
    await patchSectionFields(sectionId, rest);
  } else if (payload.kind === 'field') {
    if (payload.entityId !== section.entity) {
      alert(`"${payload.fieldName}" belongs to a different entity than this section.`);
      return;
    }
    if (section.field_ids.includes(payload.fieldId)) return;
    const targetIdx = section.field_ids.indexOf(targetFieldId);
    const newIds = [...section.field_ids];
    newIds.splice(targetIdx, 0, payload.fieldId);
    await patchSectionFields(sectionId, newIds);
  }
}

async function patchSectionFields(sectionId, fieldIds) {
  const resp = await fetch(`/api/report-sections/${sectionId}/`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ field_ids: fieldIds }),
  });
  if (!resp.ok) {
    alert((await resp.json()).error || 'Could not update this section.');
  }
  loadReport();
}

function removeFieldFromSection(sectionId, fieldId) {
  const section = reportState.sections.find(s => s.id === sectionId);
  patchSectionFields(sectionId, section.field_ids.filter(fid => fid !== fieldId));
}

async function removeSection(sectionId) {
  if (!(await confirmModal('Remove this section from the report?'))) return;
  await fetch(`/api/report-sections/${sectionId}/`, { method: 'DELETE' });
  loadReport();
}

async function moveSection(sectionId, direction) {
  await fetch(`/api/report-sections/${sectionId}/move/`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ direction }),
  });
  loadReport();
}

// ---- Preview ----------------------------------------------------------------

async function loadPreview() {
  const resp = await fetch(`/api/reports/${window.REPORT_ID}/preview/`);
  const sections = await resp.json();
  const box = document.getElementById('previewArea');

  const withColumns = sections.filter(s => s.columns.length);
  if (!withColumns.length) {
    box.innerHTML = '<p class="text-dim small mb-0">Add a section with at least one column to see a preview.</p>';
    return;
  }

  box.innerHTML = withColumns.map(section => `
    <div class="mb-3">
      <div class="d-flex justify-content-between align-items-center mb-1">
        <strong class="small">${escapeHtml(section.entity_name)}</strong>
        <span class="text-dim small">${section.total_records} record${section.total_records === 1 ? '' : 's'} total${section.total_records > section.rows.length ? ` — showing first ${section.rows.length}` : ''}</span>
      </div>
      <div style="overflow-x: auto;">
        <table class="table table-sm mb-0">
          <thead><tr>${section.columns.map(c => `<th>${escapeHtml(c)}</th>`).join('')}</tr></thead>
          <tbody>
            ${section.rows.length
              ? section.rows.map(row => `<tr>${row.map(v => `<td class="mono small">${escapeHtml(v)}</td>`).join('')}</tr>`).join('')
              : `<tr><td colspan="${section.columns.length}" class="text-dim small">No records.</td></tr>`}
          </tbody>
        </table>
      </div>
    </div>`).join('');
}
