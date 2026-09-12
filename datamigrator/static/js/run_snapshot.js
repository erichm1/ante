/* Pipeline canvas for a single MigrationRun — same viewport/zoom/box
   approach as the chain run canvas, with source→target entity pairs laid
   out as rows and jsPlumb Bezier connectors carrying status + counters. */

const STATUS_COLOR = { pending: '#9aa5a9', running: '#d98a2b', success: '#2f9c94', failed: '#c1453a' };
const STATUS_ICON  = { pending: '⏳', running: '▶', success: '✅', failed: '❌' };

const SRC_X  = 40;
const TGT_X  = 480;
const ROW_H  = 190;
const BOX_W  = 210;
const TOP_Y  = 40;

const ZOOM_MIN = 0.25, ZOOM_MAX = 2, ZOOM_STEP = 1.2;
let currentZoom = 1, currentPanX = 0, currentPanY = 0;
let jsp;

/* {entity_mapping_id: jsPlumb connection} */
const connectorsByEM = {};
/* {entity_mapping_id: step data} — updated on every poll tick */
const stepsByEM = {};
/* {entity_mapping_id: em data} — for the detail panel */
const emById = {};

/* ── Zoom ─────────────────────────────────────────────────────────────────── */

function applyZoom(z) {
  currentZoom = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z));
  document.getElementById('snapshotSurface').style.transform =
    `scale(${currentZoom}) translate(${currentPanX}px, ${currentPanY}px)`;
  document.getElementById('zoomLevel').textContent = Math.round(currentZoom * 100) + '%';
  if (jsp) jsp.setZoom(currentZoom, true);
}
function zoomIn()  { applyZoom(currentZoom * ZOOM_STEP); }
function zoomOut() { applyZoom(currentZoom / ZOOM_STEP); }

function fitView() {
  const vp    = document.getElementById('snapshotViewport');
  const surf  = document.getElementById('snapshotSurface');
  const boxes = surf.querySelectorAll('.run-step-box');
  if (!boxes.length) { applyZoom(1); return; }

  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  boxes.forEach(b => {
    minX = Math.min(minX, b.offsetLeft);
    minY = Math.min(minY, b.offsetTop);
    maxX = Math.max(maxX, b.offsetLeft + b.offsetWidth);
    maxY = Math.max(maxY, b.offsetTop + b.offsetHeight);
  });

  const pad = 50;
  const cw  = (maxX - minX) + pad * 2;
  const ch  = (maxY - minY) + pad * 2;
  surf.style.width  = Math.max(cw, vp.clientWidth)  + 'px';
  surf.style.height = Math.max(ch, vp.clientHeight) + 'px';
  currentPanX = -minX + pad;
  currentPanY = -minY + pad;
  const zoom = Math.min(vp.clientWidth / cw, vp.clientHeight / ch);
  applyZoom(Math.min(Math.max(zoom, ZOOM_MIN), 1.0));
}

/* ── Helpers ──────────────────────────────────────────────────────────────── */

function escHtml(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function stepLabel(step) {
  const icon = STATUS_ICON[step.status] || '⏳';
  const r = step.records_read    || 0;
  const w = step.records_written || 0;
  const f = step.records_failed  || 0;
  const counts = `${r} read · ${w} written${f ? ' · <b style="color:#c1453a">' + f + ' failed</b>' : ''}`;
  return `${icon} ${step.status} &nbsp;·&nbsp; ${counts}`;
}

/* ── Box builder ──────────────────────────────────────────────────────────── */

function buildBox(entity, side, rowIdx, writeMethod, emId) {
  const x = side === 'source' ? SRC_X : TGT_X;
  const y = TOP_Y + rowIdx * ROW_H;

  const box = document.createElement('div');
  box.className = 'run-step-box';
  box.id = `rbox-${side}-${entity.id}`;
  box.dataset.emId = emId;
  box.style.left = x + 'px';
  box.style.top  = y + 'px';
  box.style.width = BOX_W + 'px';

  const methodBadge = (side === 'target' && writeMethod)
    ? `<span class="verb-badge verb-${escHtml(writeMethod.toLowerCase())}">${escHtml(writeMethod)}</span>`
    : '';

  box.innerHTML = `
    <div class="rsb-role">${side === 'source' ? 'Source' : 'Target'}</div>
    <div class="rsb-name mono">${escHtml(entity.name)}</div>
    <div class="rsb-conn">${escHtml(entity.connection_name)}</div>
    ${methodBadge ? `<div class="rsb-method">${methodBadge}</div>` : ''}
    <div class="rsb-fields">${entity.fields.length} field${entity.fields.length !== 1 ? 's' : ''}</div>
    <div class="rsb-counters" id="cnt-${side}-${entity.id}"></div>
  `;

  box.addEventListener('click', () => openDetail(emId));
  document.getElementById('snapshotSurface').appendChild(box);
  return box;
}

/* ── Init ─────────────────────────────────────────────────────────────────── */

function initSnapshot(data) {
  const surf = document.getElementById('snapshotSurface');
  const vp   = document.getElementById('snapshotViewport');

  /* Deduplicate entities that appear in multiple mappings */
  const srcRendered = {}, tgtRendered = {};

  /* Compute per-entity top position — each unique source/target entity gets
     its own row slot; if the same entity appears more than once it re-uses
     the same box (connected to multiple targets). */
  const srcRows = {}, tgtRows = {};
  let rowIdx = 0;
  data.entity_mappings.forEach(em => {
    if (!(em.source_entity in srcRows)) { srcRows[em.source_entity] = rowIdx; rowIdx++; }
  });
  rowIdx = 0;
  data.entity_mappings.forEach(em => {
    if (!(em.target_entity in tgtRows)) { tgtRows[em.target_entity] = rowIdx; rowIdx++; }
  });

  const totalRows = Math.max(Object.keys(srcRows).length, Object.keys(tgtRows).length);
  surf.style.width  = Math.max(TGT_X + BOX_W + 100, vp.clientWidth)  + 'px';
  surf.style.height = Math.max(TOP_Y + totalRows * ROW_H + 60, vp.clientHeight) + 'px';

  jsp = jsPlumb.getInstance({ Container: 'snapshotSurface' });

  jsp.batch(() => {
    data.entity_mappings.forEach(em => {
      const src = em.source_entity_detail;
      const tgt = em.target_entity_detail;
      emById[em.id] = em;

      if (!srcRendered[src.id]) {
        buildBox(src, 'source', srcRows[em.source_entity], null, em.id);
        srcRendered[src.id] = true;
      }
      if (!tgtRendered[tgt.id]) {
        buildBox(tgt, 'target', tgtRows[em.target_entity], em.write_method, em.id);
        tgtRendered[tgt.id] = true;
      }

      const initialStep = { status: 'pending', records_read: 0, records_written: 0, records_failed: 0 };
      const color = STATUS_COLOR.pending;

      const conn = jsp.connect({
        source: document.getElementById(`rbox-source-${src.id}`),
        target: document.getElementById(`rbox-target-${tgt.id}`),
        anchors: ['Right', 'Left'],
        connector: ['Bezier', { curviness: 60 }],
        paintStyle: { stroke: color, strokeWidth: 2 },
        endpointStyle: { fill: color, radius: 4 },
        overlays: [
          ['Arrow', { width: 10, length: 10, location: 1 }],
          ['Label', {
            label: stepLabel(initialStep),
            location: 0.5,
            cssClass: 'step-label',
            id: `lbl-${em.id}`,
          }],
        ],
      });

      connectorsByEM[em.id] = conn;
    });
  });
}

/* ── Live status update ───────────────────────────────────────────────────── */

function updateStepStatus(step) {
  stepsByEM[step.entity_mapping] = step;
  const conn = connectorsByEM[step.entity_mapping];
  if (!conn) return;

  const color = STATUS_COLOR[step.status] || STATUS_COLOR.pending;
  conn.setPaintStyle({ stroke: color, strokeWidth: 2 });

  const overlay = conn.getOverlay(`lbl-${step.entity_mapping}`);
  if (overlay) overlay.setLabel(stepLabel(step));

  /* Update border color on the target box to reflect outcome */
  const em  = emById[step.entity_mapping];
  if (!em) return;
  const tgt = document.getElementById(`rbox-target-${em.target_entity_detail.id}`);
  if (tgt) tgt.style.borderColor = color;
  const src = document.getElementById(`rbox-source-${em.source_entity_detail.id}`);
  if (src) src.style.borderColor = color;

  /* Counters inside the box */
  ['source','target'].forEach(side => {
    const entityId = side === 'source'
      ? em.source_entity_detail.id
      : em.target_entity_detail.id;
    const cnt = document.getElementById(`cnt-${side}-${entityId}`);
    if (!cnt) return;
    const r = step.records_read    || 0;
    const w = step.records_written || 0;
    const f = step.records_failed  || 0;
    if (r + w + f === 0 && step.status === 'pending') { cnt.innerHTML = ''; return; }
    cnt.innerHTML = `
      <span class="rsb-cnt-item rsb-cnt-read">${r} read</span>
      <span class="rsb-cnt-item rsb-cnt-written">${w} written</span>
      ${f ? `<span class="rsb-cnt-item rsb-cnt-failed">${f} failed</span>` : ''}
    `;
  });
}

/* ── Detail panel ─────────────────────────────────────────────────────────── */

function openDetail(emId) {
  document.querySelectorAll('.run-step-box').forEach(b => b.classList.remove('rsb-selected'));
  const em   = emById[emId];
  const step = stepsByEM[emId] || { status: 'pending', records_read: 0, records_written: 0, records_failed: 0 };
  if (!em) return;

  /* Highlight both boxes */
  const srcBox = document.getElementById(`rbox-source-${em.source_entity_detail.id}`);
  const tgtBox = document.getElementById(`rbox-target-${em.target_entity_detail.id}`);
  if (srcBox) srcBox.classList.add('rsb-selected');
  if (tgtBox) tgtBox.classList.add('rsb-selected');

  const color = STATUS_COLOR[step.status] || STATUS_COLOR.pending;
  const icon  = STATUS_ICON[step.status]  || '⏳';
  const r = step.records_read    || 0;
  const w = step.records_written || 0;
  const f = step.records_failed  || 0;

  document.getElementById('detailTitle').innerHTML =
    `${escHtml(em.source_entity_detail.name)} → ${escHtml(em.target_entity_detail.name)}
     <span class="status-chip ${step.status === 'success' ? 'ok' : step.status === 'failed' ? 'err' : 'warn'} ms-2">${step.status}</span>`;

  let html = `<div class="row g-3">
    <div class="col-md-4">
      <div class="text-dim small text-uppercase mb-2" style="letter-spacing:.06em">Source</div>
      <div class="fw-600 mono">${escHtml(em.source_entity_detail.name)}</div>
      <div class="text-dim small">${escHtml(em.source_entity_detail.connection_name)}</div>
      <div class="text-dim small mt-1">${em.source_entity_detail.fields.length} fields</div>
    </div>
    <div class="col-md-4">
      <div class="text-dim small text-uppercase mb-2" style="letter-spacing:.06em">Target</div>
      <div class="fw-600 mono">${escHtml(em.target_entity_detail.name)}</div>
      <div class="text-dim small">${escHtml(em.target_entity_detail.connection_name)}</div>
      <div class="d-flex align-items-center gap-2 mt-1">
        <span class="verb-badge verb-${escHtml(em.write_method.toLowerCase())}">${escHtml(em.write_method)}</span>
        <span class="text-dim small">${em.target_entity_detail.fields.length} fields</span>
      </div>
    </div>
    <div class="col-md-4">
      <div class="text-dim small text-uppercase mb-2" style="letter-spacing:.06em">Result</div>
      <div class="d-flex flex-column gap-1">
        <div class="d-flex justify-content-between small"><span class="text-dim">Records read</span><span class="mono fw-500">${r}</span></div>
        <div class="d-flex justify-content-between small"><span class="text-dim">Written</span><span class="mono fw-500" style="color:var(--trace)">${w}</span></div>
        ${f ? `<div class="d-flex justify-content-between small"><span class="text-dim">Failed</span><span class="mono fw-500" style="color:var(--danger)">${f}</span></div>` : ''}
        ${step.error_message ? `<div class="alert alert-danger py-2 small mt-2">${escHtml(step.error_message)}</div>` : ''}
      </div>
    </div>
  </div>`;

  document.getElementById('detailContent').innerHTML = html;
  const panel = document.getElementById('stepDetail');
  panel.style.display = '';
  panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function closeDetail() {
  document.getElementById('stepDetail').style.display = 'none';
  document.querySelectorAll('.run-step-box').forEach(b => b.classList.remove('rsb-selected'));
}
