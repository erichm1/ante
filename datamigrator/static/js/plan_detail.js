/* templates/plans/detail.html's page logic: edit the plan / a step's rate
   limit while it's still a draft, add/remove/reorder steps, kick off
   execution (now or scheduled), and — while executing — poll the plan and
   update both the page's step rows AND a "pop-in" live snapshot modal, the
   same way jobs/run_polling.js's pollRuns() does for a single run, just
   generalized to "poll one plan, update N step rows in two places." */

let planPollTimer = null;
let editingStepId = null;

function reload() {
  window.location.reload();
}

// ---- Editing (draft only) --------------------------------------------------

async function savePlanEdit() {
  const errorBox = document.getElementById('editPlanError');
  errorBox.classList.add('d-none');

  const payload = {
    name: document.getElementById('ep_name').value.trim(),
    description: document.getElementById('ep_description').value,
  };
  payload.rate_limit_per_second = rpmToRps(document.getElementById('ep_rate_limit').value);

  if (!payload.name) {
    errorBox.textContent = 'Name is required.';
    errorBox.classList.remove('d-none');
    return;
  }

  const resp = await fetch(`/api/plans/${window.PLAN_ID}/`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  });
  if (resp.ok) {
    reload();
  } else {
    errorBox.textContent = JSON.stringify(await resp.json());
    errorBox.classList.remove('d-none');
  }
}

function openEditStepModal(stepId, currentRateLimit) {
  editingStepId = stepId;
  document.getElementById('editStepError').classList.add('d-none');
  document.getElementById('es_rate_limit').value = rpsToRpm(currentRateLimit);
  new bootstrap.Modal(document.getElementById('editStepModal')).show();
}

async function saveStepEdit() {
  const errorBox = document.getElementById('editStepError');
  errorBox.classList.add('d-none');

  const resp = await fetch(`/api/plan-steps/${editingStepId}/`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rate_limit_per_second: rpmToRps(document.getElementById('es_rate_limit').value) }),
  });
  if (resp.ok) {
    reload();
  } else {
    errorBox.textContent = JSON.stringify(await resp.json());
    errorBox.classList.remove('d-none');
  }
}

async function addStep() {
  const errorBox = document.getElementById('addStepError');
  errorBox.classList.add('d-none');

  let payload;
  if (window.PLAN_MODE === 'chain') {
    payload = { chain_id: document.getElementById('as_chain').value };
  } else {
    payload = { mapping_id: document.getElementById('as_mapping').value };
    const rateLimit = rpmToRps(document.getElementById('as_rate_limit').value);
    if (rateLimit) payload.rate_limit_per_second = rateLimit;
  }

  const resp = await fetch(`/api/plans/${window.PLAN_ID}/steps/`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  });
  if (resp.ok) {
    reload();
  } else {
    errorBox.textContent = JSON.stringify(await resp.json());
    errorBox.classList.remove('d-none');
  }
}

async function deletePlan() {
  if (!(await confirmModal(`Delete plan "${window.PLAN_NAME}"? This can't be undone.`))) return;
  fetch(`/api/plans/${window.PLAN_ID}/`, { method: 'DELETE' }).then(async resp => {
    if (resp.ok) {
      window.location.href = '/plans/';
    } else {
      const body = await resp.json();
      alert(body.error || 'Could not delete this plan.');
    }
  });
}

async function removeStep(stepId) {
  if (!(await confirmModal('Remove this step from the plan?'))) return;
  const resp = await fetch(`/api/plan-steps/${stepId}/`, { method: 'DELETE' });
  if (resp.ok) reload();
}

async function moveStep(stepId, direction) {
  const resp = await fetch(`/api/plan-steps/${stepId}/move/`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ direction }),
  });
  if (resp.ok) reload();
}

// ---- Execute ----------------------------------------------------------------

function executePlanNow() {
  runExecute({});
}

function executePlanWithOptions() {
  const payload = {};
  const scheduleRaw = document.getElementById('ex_schedule_at').value;
  if (scheduleRaw) payload.scheduled_at = new Date(scheduleRaw).toISOString();
  const rateLimitRps = rpmToRps(document.getElementById('ex_rate_limit').value);
  if (rateLimitRps) payload.rate_limit_per_second = rateLimitRps;
  runExecute(payload, 'executeError', 'executeOptionsModal');
}

function runExecute(payload, errorBoxId, modalId) {
  const errorBox = errorBoxId ? document.getElementById(errorBoxId) : null;
  if (errorBox) errorBox.classList.add('d-none');

  fetch(`/api/plans/${window.PLAN_ID}/execute/`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }).then(async resp => {
    const body = await resp.json();
    if (!resp.ok) {
      const message = body.error || JSON.stringify(body);
      if (errorBox) {
        errorBox.textContent = message;
        errorBox.classList.remove('d-none');
      } else {
        alert(message);
      }
      return;
    }
    if (modalId) bootstrap.Modal.getInstance(document.getElementById(modalId))?.hide();

    if (body.status === 'executing') {
      showSnapshotModal(body);
      startPolling();
    } else {
      // Scheduled for later — nothing running yet, just reflect the new state.
      reload();
    }
  });
}

// ---- Live snapshot modal ("pop" onto the screen the moment execution starts) --

function stepExecution(step) {
  // Whichever of run (simple mode)/chain_run (chain mode) is actually set —
  // a step only ever has one, matching the plan's execution_mode.
  return step.run || step.chain_run;
}

function stepStatusChipClass(step) {
  const exec = stepExecution(step);
  return exec ? statusChipClass(exec.status) : '';
}

function renderSnapshotStep(step) {
  const exec = stepExecution(step);
  const label = exec ? exec.status : 'not started';
  return `
    <div class="d-flex align-items-center justify-content-between border rounded px-3 py-2" id="snapshot-step-${step.id}">
      <div class="d-flex align-items-center gap-2">
        <span class="mono text-dim small">#${step.order}</span>
        <span>${step.mapping_name || step.chain_name}</span>
      </div>
      <span class="status-chip ${stepStatusChipClass(step)}" id="snapshot-status-${step.id}">${label}</span>
    </div>`;
}

function showSnapshotModal(plan) {
  document.getElementById('snapshot-plan-name').textContent = plan.name || window.PLAN_NAME;
  document.getElementById('snapshotSteps').innerHTML = plan.steps.map(renderSnapshotStep).join('');
  new bootstrap.Modal(document.getElementById('planSnapshotModal')).show();
}

function updateSnapshotModal(plan) {
  const modalEl = document.getElementById('planSnapshotModal');
  if (!modalEl.classList.contains('show')) return;
  plan.steps.forEach(step => {
    const chip = document.getElementById(`snapshot-status-${step.id}`);
    if (!chip) return;
    const exec = stepExecution(step);
    chip.textContent = exec ? exec.status : 'not started';
    chip.className = 'status-chip ' + stepStatusChipClass(step);
  });
}

// ---- Shared polling: drives the page's step rows AND the snapshot modal ------

function updatePlanDetail(plan) {
  const statusChip = document.getElementById('plan-status');
  statusChip.textContent = plan.status;
  statusChip.className = 'status-chip ' + (
    plan.status === 'completed' ? 'ok' : plan.status === 'failed' ? 'err'
    : (plan.status === 'executing' || plan.status === 'scheduled') ? 'warn' : ''
  );

  plan.steps.forEach(step => {
    const chip = document.getElementById(`step-status-${step.id}`);
    const exec = stepExecution(step);
    if (!chip || !exec) return;
    chip.textContent = exec.status;
    chip.className = 'status-chip ' + statusChipClass(exec.status);
  });

  updateSnapshotModal(plan);
}

function startPolling() {
  if (planPollTimer) return; // already running
  planPollTimer = setInterval(async () => {
    const resp = await fetch(`/api/plans/${window.PLAN_ID}/`);
    if (!resp.ok) return;
    const plan = await resp.json();
    updatePlanDetail(plan);
    if (plan.status !== 'executing') {
      clearInterval(planPollTimer);
      planPollTimer = null;
      setTimeout(reload, 1200); // let the final status land visually before refreshing controls/links
    }
  }, 2000);
}

async function initPlanDetail() {
  if (window.PLAN_STATUS !== 'executing') return;
  // Landed on (or refreshed into) a plan that's already mid-execution —
  // pop the snapshot open here too, not just right after clicking Execute.
  const resp = await fetch(`/api/plans/${window.PLAN_ID}/`);
  if (resp.ok) showSnapshotModal(await resp.json());
  startPolling();
}

/* ---- Drag-and-drop step reordering --------------------------------------- */
(function () {
  const tbody = document.getElementById('stepsBody');
  if (!tbody) return;
  let dragging = null;

  tbody.addEventListener('dragstart', function (e) {
    const row = e.target.closest('tr[data-step-id]');
    if (!row) return;
    dragging = row;
    row.classList.add('dnd-dragging');
    e.dataTransfer.effectAllowed = 'move';
  });

  tbody.addEventListener('dragover', function (e) {
    e.preventDefault();
    const row = e.target.closest('tr[data-step-id]');
    if (!row || row === dragging) return;
    tbody.querySelectorAll('tr').forEach(r => r.classList.remove('dnd-over'));
    row.classList.add('dnd-over');
    const rows = Array.from(tbody.querySelectorAll('tr[data-step-id]'));
    const fromIdx = rows.indexOf(dragging);
    const toIdx   = rows.indexOf(row);
    if (fromIdx < toIdx) row.after(dragging);
    else row.before(dragging);
  });

  tbody.addEventListener('dragleave', function (e) {
    const row = e.target.closest('tr[data-step-id]');
    if (row) row.classList.remove('dnd-over');
  });

  tbody.addEventListener('dragend', async function () {
    if (!dragging) return;
    dragging.classList.remove('dnd-dragging');
    tbody.querySelectorAll('tr').forEach(r => r.classList.remove('dnd-over'));

    const finalId  = parseInt(dragging.dataset.stepId, 10);
    const rows     = Array.from(tbody.querySelectorAll('tr[data-step-id]'));
    const finalIdx = rows.indexOf(dragging);
    /* Determine original 1-based order from the #-cell (second td after the handle) */
    const orderCell = dragging.querySelector('td:nth-child(2)');
    const myOrigOrder = parseInt(orderCell ? orderCell.textContent : '0', 10);
    const delta = finalIdx - (myOrigOrder - 1);
    const direction = delta > 0 ? 'down' : 'up';
    const steps = Math.abs(delta);

    dragging = null;
    if (steps === 0) return;

    for (let i = 0; i < steps; i++) {
      const resp = await fetch(`/api/plan-steps/${finalId}/move/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ direction }),
      });
      if (!resp.ok) break;
    }
    reload();
  });
}());
