/* Every destructive-action confirmation in the app (delete X, remove Y)
   used the browser's native confirm() — this replaces that with the same
   centered Bootstrap modal pattern as alert_modal.js's window.alert
   override. Unlike alert(), confirm() can't be monkeypatched the same way:
   the browser's confirm() blocks and returns a boolean synchronously,
   which a real modal (non-blocking by nature) can never do — so this is a
   new async helper (returns a Promise<boolean>) rather than an override,
   and every call site does `if (!(await confirmModal(...))) return;`
   instead of `if (!confirm(...)) return;`. */
(function () {
  window.confirmModal = function (message) {
    return new Promise((resolve) => {
      const modalEl = document.getElementById('globalConfirmModal');
      if (!modalEl || typeof bootstrap === 'undefined') {
        resolve(window.confirm(message));
        return;
      }
      document.getElementById('globalConfirmModalBody').textContent = String(message);
      const yesBtn = document.getElementById('globalConfirmModalYes');
      const instance = bootstrap.Modal.getOrCreateInstance(modalEl);

      let confirmed = false;
      const onYes = () => { confirmed = true; instance.hide(); };
      const onHidden = () => {
        yesBtn.removeEventListener('click', onYes);
        modalEl.removeEventListener('hidden.bs.modal', onHidden);
        resolve(confirmed);
      };

      yesBtn.addEventListener('click', onYes);
      modalEl.addEventListener('hidden.bs.modal', onHidden);
      instance.show();
    });
  };
})();
