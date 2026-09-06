let jsp;
const fieldMappingByPair = {};    // "sourceFieldId-targetFieldId" -> field_mapping id
const entityMappingByPair = {};   // "sourceEntityId-targetEntityId" -> entity_mapping id
const sourceFieldToEntity = {};   // fieldId -> entityId (source side)
const targetFieldToEntity = {};   // fieldId -> entityId (target side)
const DEFAULT_POS = 40;

function el(tag, cls, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html !== undefined) e.innerHTML = html;
  return e;
}

function fieldLabel(field) {
  return el('span', '', `<span class="mono">${field.name}</span> <span class="field-type">${field.field_type}</span>`);
}

function renderEntityBox(entity, side, x, y) {
  const box = el('div', 'entity-box');
  box.id = `entity-${side}-${entity.id}`;
  box.style.left = x + 'px';
  box.style.top = y + 'px';
  box.appendChild(el('div', 'entity-header', `<span class="schema-name">${entity.name}</span><small>${entity.connection_name}</small>`));

  entity.fields.forEach(field => {
    const row = el('div', 'field-row');
    const dot = el('div', `field-endpoint${side === 'target' ? ' target' : ''}`);
    dot.id = side === 'source' ? `dot-src-${field.id}` : `dot-tgt-${field.id}`;
    if (side === 'target') {
      row.appendChild(dot);
      row.appendChild(fieldLabel(field));
    } else {
      row.appendChild(fieldLabel(field));
      row.appendChild(dot);
    }
    box.appendChild(row);
  });

  document.getElementById('canvasSurface').appendChild(box);
  return box;
}

function addEndpoint(dotEl, uuid, isSource) {
  jsp.addEndpoint(dotEl, {
    uuid,
    anchor: 'Center',
    isSource,
    isTarget: !isSource,
    maxConnections: -1,  // a source field can fan out to several destination fields
    endpoint: ['Dot', { radius: 5 }],
    paintStyle: { fill: isSource ? '#2f9c94' : '#d98a2b', stroke: 'transparent' },
    connectorStyle: { stroke: '#2f9c94', strokeWidth: 2 },
  });
}

function layoutPosition(entity, index, side) {
  if (entity.canvas_x !== DEFAULT_POS || entity.canvas_y !== DEFAULT_POS) {
    return { x: entity.canvas_x, y: entity.canvas_y };
  }
  return { x: side === 'source' ? 40 : 820, y: 40 + index * 240 };
}

function persistPosition(entityId, x, y) {
  fetch(`/api/entities/${entityId}/position/`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ canvas_x: Math.round(x), canvas_y: Math.round(y) }),
  });
}

function createFieldMapping(sourceFieldId, targetFieldId, jsConnection) {
  const key = `${sourceFieldId}-${targetFieldId}`;
  const sourceEntityId = sourceFieldToEntity[sourceFieldId];
  const targetEntityId = targetFieldToEntity[targetFieldId];
  const emId = entityMappingByPair[`${sourceEntityId}-${targetEntityId}`];

  if (!emId) {
    alert('These two fields are not on entities paired on this canvas — add that entity pair first.');
    jsp.deleteConnection(jsConnection);
    return;
  }

  fetch('/api/field-mappings/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ entity_mapping: emId, source_field: sourceFieldId, target_field: targetFieldId }),
  }).then(async resp => {
    if (resp.ok) {
      const body = await resp.json();
      fieldMappingByPair[key] = body.id;
    } else {
      jsp.deleteConnection(jsConnection);
      alert('Could not save that field mapping.');
    }
  });
}

function initCanvas(data) {
  window.CANVAS_DATA = data;

  // "Add entity pair" modal: source entity comes from the single origin connection.
  const sourceSel = document.getElementById('pair_source');
  data.source_entities.forEach(e => sourceSel.appendChild(new Option(e.name, e.id)));

  // Destination connection is one of the mapping's declared destinies; its entities
  // are fetched on demand once a connection is picked (see loadDestinationEntities).
  const destConnSel = document.getElementById('pair_dest_connection');
  (data.mapping.destination_connections_detail || []).forEach(c => destConnSel.appendChild(new Option(c.name, c.id)));
  destConnSel.addEventListener('change', loadDestinationEntities);

  // Build the render lists (and lookup maps) from what's already on the canvas.
  // Source/target entities can repeat across entity_mappings (that's how one
  // source entity fans out to several destination entities) so dedupe by id.
  const sourceEntitiesById = {};
  const targetEntitiesById = {};
  data.entity_mappings.forEach(em => {
    sourceEntitiesById[em.source_entity_detail.id] = em.source_entity_detail;
    targetEntitiesById[em.target_entity_detail.id] = em.target_entity_detail;
    entityMappingByPair[`${em.source_entity}-${em.target_entity}`] = em.id;
    em.field_mappings.forEach(fm => { fieldMappingByPair[`${fm.source_field}-${fm.target_field}`] = fm.id; });
  });

  jsp = jsPlumb.getInstance({
    Container: 'canvasSurface',
    Connector: ['Bezier', { curviness: 60 }],
    PaintStyle: { stroke: '#2f9c94', strokeWidth: 2 },
  });

  jsp.batch(() => {
    Object.values(sourceEntitiesById).forEach((entity, i) => {
      const pos = layoutPosition(entity, i, 'source');
      const box = renderEntityBox(entity, 'source', pos.x, pos.y);
      entity.fields.forEach(f => {
        sourceFieldToEntity[f.id] = entity.id;
        addEndpoint(document.getElementById(`dot-src-${f.id}`), `src-field-${f.id}`, true);
      });
      jsp.draggable(box, { grid: [10, 10], stop: params => persistPosition(entity.id, params.pos[0], params.pos[1]) });
    });

    Object.values(targetEntitiesById).forEach((entity, i) => {
      const pos = layoutPosition(entity, i, 'target');
      const box = renderEntityBox(entity, 'target', pos.x, pos.y);
      entity.fields.forEach(f => {
        targetFieldToEntity[f.id] = entity.id;
        addEndpoint(document.getElementById(`dot-tgt-${f.id}`), `tgt-field-${f.id}`, false);
      });
      jsp.draggable(box, { grid: [10, 10], stop: params => persistPosition(entity.id, params.pos[0], params.pos[1]) });
    });

    data.entity_mappings.forEach(em => {
      em.field_mappings.forEach(fm => {
        jsp.connect({ uuids: [`src-field-${fm.source_field}`, `tgt-field-${fm.target_field}`] });
      });
    });
  });

  jsp.bind('connection', (info, originalEvent) => {
    if (!originalEvent) return; // programmatic connect (initial render) — already persisted
    const sourceFieldId = parseInt(info.sourceEndpoint.getUuid().replace('src-field-', ''), 10);
    const targetFieldId = parseInt(info.targetEndpoint.getUuid().replace('tgt-field-', ''), 10);
    createFieldMapping(sourceFieldId, targetFieldId, info.connection);
  });

  jsp.bind('click', conn => {
    const sourceFieldId = parseInt(conn.endpoints[0].getUuid().replace('src-field-', ''), 10);
    const targetFieldId = parseInt(conn.endpoints[1].getUuid().replace('tgt-field-', ''), 10);
    const key = `${sourceFieldId}-${targetFieldId}`;
    const fmId = fieldMappingByPair[key];
    if (!fmId || !confirm('Remove this field mapping?')) return;
    fetch(`/api/field-mappings/${fmId}/`, { method: 'DELETE' }).then(resp => {
      if (resp.ok) { jsp.deleteConnection(conn); delete fieldMappingByPair[key]; }
    });
  });
}

async function loadDestinationEntities() {
  const connId = document.getElementById('pair_dest_connection').value;
  const destEntitySel = document.getElementById('pair_dest_entity');

  if (!connId) {
    destEntitySel.innerHTML = '<option value="">Pick a destination connection first</option>';
    destEntitySel.disabled = true;
    return;
  }

  destEntitySel.disabled = true;
  destEntitySel.innerHTML = '<option value="">Loading…</option>';
  const resp = await fetch(`/api/entities/?connection=${connId}`);
  const data = await resp.json();
  const entities = data.results || data;

  destEntitySel.innerHTML = '';
  if (!entities.length) {
    destEntitySel.innerHTML = '<option value="">No entities discovered yet for this connection</option>';
    return;
  }
  entities.forEach(e => destEntitySel.appendChild(new Option(e.name, e.id)));
  destEntitySel.disabled = false;
}

function addEntityPair() {
  const errorBox = document.getElementById('pairError');
  errorBox.classList.add('d-none');

  const targetEntity = document.getElementById('pair_dest_entity').value;
  if (!targetEntity) {
    errorBox.textContent = 'Pick a destination connection and entity first.';
    errorBox.classList.remove('d-none');
    return;
  }

  fetch('/api/entity-mappings/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      mapping: window.MAPPING_ID,
      source_entity: document.getElementById('pair_source').value,
      target_entity: targetEntity,
    }),
  }).then(async resp => {
    if (resp.ok) {
      window.location.reload();
    } else {
      errorBox.textContent = JSON.stringify(await resp.json());
      errorBox.classList.remove('d-none');
    }
  });
}

let pollTimer = null;

function runMigration() {
  const btn = document.getElementById('runMigrationBtn');
  btn.disabled = true;
  btn.textContent = 'Starting…';

  fetch('/api/runs/trigger/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mapping_id: window.MAPPING_ID }),
  }).then(async resp => {
    const body = await resp.json();
    if (!resp.ok) {
      alert(body.error || 'Migration failed to start.');
      btn.disabled = false;
      btn.textContent = 'Run migration';
      return;
    }
    btn.textContent = 'Running…';
    startRunPolling(body.id);
  });
}

function startRunPolling(runId) {
  const statsBox = document.getElementById('runStats');
  const statusChip = document.getElementById('runStatusChip');
  const requestsEl = document.getElementById('statRequests');
  const rateEl = document.getElementById('statRate');
  statsBox.classList.remove('d-none');

  let lastCount = 0;
  let lastTime = Date.now();

  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    const resp = await fetch(`/api/runs/${runId}/`);
    if (!resp.ok) return;
    const run = await resp.json();

    const now = Date.now();
    const elapsedSeconds = (now - lastTime) / 1000;
    const delta = run.requests_made - lastCount;
    const rate = elapsedSeconds > 0 ? delta / elapsedSeconds : 0;

    requestsEl.textContent = run.requests_made;
    rateEl.textContent = rate.toFixed(1);
    statusChip.textContent = run.status;
    statusChip.className = 'status-chip ' + (run.status === 'success' ? 'ok' : run.status === 'failed' ? 'err' : 'warn');

    lastCount = run.requests_made;
    lastTime = now;

    if (run.status !== 'running' && run.status !== 'pending') {
      clearInterval(pollTimer);
      pollTimer = null;
      rateEl.textContent = '0.0';

      const btn = document.getElementById('runMigrationBtn');
      btn.disabled = false;
      btn.textContent = 'Run migration';

      if (!document.getElementById('viewRunLink')) {
        const link = document.createElement('a');
        link.id = 'viewRunLink';
        link.href = `/jobs/runs/${runId}/`;
        link.className = 'small ms-2';
        link.textContent = 'View full log →';
        statsBox.appendChild(link);
      }
    }
  }, 800);
}
