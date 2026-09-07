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
  const rateLimitRaw = document.getElementById('ep_rate_limit').value;
  payload.rate_limit_per_second = rateLimitRaw ? rateLimitRaw : null;

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
  document.getElementById('es_rate_limit').value = currentRateLimit || '';
  new bootstrap.Modal(document.getElementById('editStepModal')).show();
}

async function saveStepEdit() {
  const errorBox = document.getElementById('editStepError');
  errorBox.classList.add('d-none');

  const rateLimitRaw = document.getElementById('es_rate_limit').value;
  const resp = await fetch(`/api/plan-steps/${editingStepId}/`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rate_limit_per_second: rateLimitRaw ? rateLimitRaw : null }),
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
  const payload = { mapping_id: document.getElementById('as_mapping').value };
  const rateLimit = document.getElementById('as_rate_limit').value;
  if (rateLimit) payload.rate_limit_per_second = rateLimit;

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

async function removeStep(stepId) {
  if (!confirm('Remove this step from the plan?')) return;
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
  const rateLimitRaw = document.getElementById('ex_rate_limit').value;
  if (rateLimitRaw) payload.rate_limit_per_second = rateLimitRaw;
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

function stepStatusChipClass(step) {
  return step.run ? statusChipClass(step.run.status) : '';
}

function renderSnapshotStep(step) {
  const label = step.run ? step.run.status : 'not started';
  return `
    <div class="d-flex align-items-center justify-content-between border rounded px-3 py-2" id="snapshot-step-${step.id}">
      <div class="d-flex align-items-center gap-2">
        <span class="mono text-dim small">#${step.order}</span>
        <span>${step.mapping_name}</span>
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
    chip.textContent = step.run ? step.run.status : 'not started';
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
    if (!chip || !step.run) return;
    chip.textContent = step.run.status;
    chip.className = 'status-chip ' + statusChipClass(step.run.status);
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
