/* Shared live-polling helpers for jobs/run_list.html and jobs/run_detail.html.
   Mirrors the polling shape already used by mappings/canvas.js's
   startRunPolling — one GET per run per tick against the read-only /api/runs/
   endpoint, driven from the browser rather than websockets/SSE since this
   scaffold has no async server infra. */

function statusChipClass(status) {
  if (status === "success") return "ok";
  if (status === "failed") return "err";
  return "warn"; // pending / running
}

/**
 * Polls a set of run ids until each settles (status leaves pending/running).
 * Calls onUpdate(run) every tick for runs still active, and onSettle(run)
 * exactly once per run when it stops being active. Stops itself once every
 * id has settled.
 */
function pollRuns(runIds, { onUpdate, onSettle, intervalMs = 2000 } = {}) {
  const pending = new Set(runIds);
  if (!pending.size) return null;

  const timer = setInterval(async () => {
    for (const id of Array.from(pending)) {
      let run;
      try {
        const resp = await fetch(`/api/runs/${id}/`);
        if (!resp.ok) continue;
        run = await resp.json();
      } catch (e) {
        continue;
      }
      if (run.status === "running" || run.status === "pending") {
        onUpdate && onUpdate(run);
      } else {
        pending.delete(id);
        onUpdate && onUpdate(run);
        onSettle && onSettle(run);
      }
    }
    if (!pending.size) clearInterval(timer);
  }, intervalMs);

  return timer;
}
