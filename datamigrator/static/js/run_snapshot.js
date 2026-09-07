/* Read-only, live-updating rendering of a run's mapping canvas — same entity
   box layout as mappings/canvas.html's static/js/canvas.js, but each
   entity-pair connector is colored/labeled by that pair's RunStepStatus
   instead of being a draggable, per-field editable connection. This is the
   GitHub-Actions-style "pipeline" view: one edge per step, live status. */

const STATUS_ICON = { pending: "⏳", running: "▶", success: "✅", failed: "❌" };
const STATUS_COLOR = { pending: "#9aa5a9", running: "#d98a2b", success: "#2f9c94", failed: "#c1453a" };
const DEFAULT_POS = 40;

let jsp;
const connectorsByEntityMapping = {};

function el(tag, cls, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html !== undefined) e.innerHTML = html;
  return e;
}

function renderEntityBox(entity, side, x, y) {
  const box = el("div", "entity-box");
  box.id = `snapshot-entity-${side}-${entity.id}`;
  box.style.left = x + "px";
  box.style.top = y + "px";
  box.appendChild(el("div", "entity-header", `<span class="schema-name">${entity.name}</span><small>${entity.connection_name}</small>`));

  entity.fields.forEach(field => {
    const row = el("div", "field-row");
    row.appendChild(el("span", "", `<span class="mono">${field.name}</span> <span class="field-type">${field.field_type}</span>`));
    box.appendChild(row);
  });

  document.getElementById("snapshotSurface").appendChild(box);
  return box;
}

function layoutPosition(entity, index, side) {
  if (entity.canvas_x !== DEFAULT_POS || entity.canvas_y !== DEFAULT_POS) {
    return { x: entity.canvas_x, y: entity.canvas_y };
  }
  return { x: side === "source" ? 40 : 820, y: 40 + index * 240 };
}

function initSnapshot(data) {
  const sourceEntitiesById = {};
  const targetEntitiesById = {};
  data.entity_mappings.forEach(em => {
    sourceEntitiesById[em.source_entity_detail.id] = em.source_entity_detail;
    targetEntitiesById[em.target_entity_detail.id] = em.target_entity_detail;
  });

  jsp = jsPlumb.getInstance({ Container: "snapshotSurface" });

  jsp.batch(() => {
    Object.values(sourceEntitiesById).forEach((entity, i) => {
      const pos = layoutPosition(entity, i, "source");
      renderEntityBox(entity, "source", pos.x, pos.y);
    });
    Object.values(targetEntitiesById).forEach((entity, i) => {
      const pos = layoutPosition(entity, i, "target");
      renderEntityBox(entity, "target", pos.x, pos.y);
    });

    data.entity_mappings.forEach(em => {
      const conn = jsp.connect({
        source: document.getElementById(`snapshot-entity-source-${em.source_entity}`),
        target: document.getElementById(`snapshot-entity-target-${em.target_entity}`),
        anchors: ["Right", "Left"],
        connector: ["Bezier", { curviness: 60 }],
        paintStyle: { stroke: STATUS_COLOR.pending, strokeWidth: 3 },
        overlays: [["Label", { label: `${STATUS_ICON.pending} pending`, location: 0.5, cssClass: "step-label", id: `label-${em.id}` }]],
        endpoint: ["Dot", { radius: 4 }],
      });
      connectorsByEntityMapping[em.id] = conn;
    });
  });
}

function updateStepStatus(step) {
  const conn = connectorsByEntityMapping[step.entity_mapping];
  if (!conn) return;
  conn.setPaintStyle({ stroke: STATUS_COLOR[step.status], strokeWidth: 3 });
  const overlay = conn.getOverlay(`label-${step.entity_mapping}`);
  if (overlay) overlay.setLabel(`${STATUS_ICON[step.status]} ${step.status} (${step.records_written}/${step.records_read})`);
}
