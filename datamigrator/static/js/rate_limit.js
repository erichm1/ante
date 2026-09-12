/* Every rate-limit input across the app (connections, Create run, Plan
   default/step/execute-options) is entered and displayed as requests per
   MINUTE — that's how most API docs publish their limits — but stored and
   enforced as requests/second everywhere in the backend (Connection/
   MigrationRun/MigrationPlan/PlanStep.rate_limit_per_second, throttled by
   jobs/engine.py). These two helpers are the only place that conversion
   happens, so every page does it the same way. */

function rpsToRpm(perSecond) {
  if (perSecond === null || perSecond === undefined || perSecond === '') return '';
  return Math.round(parseFloat(perSecond) * 60 * 100) / 100;
}

function rpmToRps(perMinute) {
  if (perMinute === null || perMinute === undefined || perMinute === '') return null;
  return parseFloat(perMinute) / 60;
}
