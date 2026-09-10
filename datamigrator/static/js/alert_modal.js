/* Replaces the browser's native alert() with the app's own centered
   Bootstrap modal, everywhere in one place — every existing `alert(...)`
   call across canvas.js, connections.js, chains/plans/mappings' inline
   scripts, etc. picks this up automatically, no call site needs editing.
   Same idea as csrf.js's global fetch() patch. Requires #globalAlertModal
   (see base.html) and Bootstrap's JS bundle to already be loaded. */
(function () {
  const originalAlert = window.alert;

  window.alert = function (message) {
    const modalEl = document.getElementById('globalAlertModal');
    if (!modalEl || typeof bootstrap === 'undefined') {
      originalAlert(message);
      return;
    }
    document.getElementById('globalAlertModalBody').textContent = String(message);

    // alert() is sometimes called while another modal is already open (e.g.
    // a save failing inside an Edit modal) — Bootstrap stacks modals fine,
    // but reuse the existing instance rather than leaking a new one per call.
    const instance = bootstrap.Modal.getOrCreateInstance(modalEl);
    instance.show();
  };
})();
