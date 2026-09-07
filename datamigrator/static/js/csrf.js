/* Once the app requires login (see accounts/middleware.py), DRF's
   SessionAuthentication starts enforcing CSRF on every unsafe request from
   an authenticated session (README's "Known scaffold limitations" flagged
   this as the follow-up). Rather than touch every fetch() call across
   canvas.js, run_polling.js, and every template's inline <script>, patch
   fetch() once, here, to attach X-CSRFToken to same-origin POST/PUT/PATCH/
   DELETE requests automatically. Loaded on every page via base.html. */
(function () {
  function getCookie(name) {
    const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
    return match ? decodeURIComponent(match[1]) : null;
  }

  const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
  const originalFetch = window.fetch;

  window.fetch = function (input, init = {}) {
    const method = (init.method || "GET").toUpperCase();
    if (UNSAFE_METHODS.has(method)) {
      const csrftoken = getCookie("csrftoken");
      if (csrftoken) {
        init.headers = new Headers(init.headers || {});
        if (!init.headers.has("X-CSRFToken")) init.headers.set("X-CSRFToken", csrftoken);
      }
    }
    return originalFetch(input, init);
  };
})();
