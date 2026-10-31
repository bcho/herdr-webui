/* Privacy-safe diagnostics: never send error messages, stacks, drafts or URLs with queries. */
"use strict";
(() => {
  const routes = new Set(["/", "/api/tree", "/api/read", "/api/client-error", ...["send", "keys", "followup", "prompt", "focus", "split", "close", "spawn"].map(name => "/api/action/" + name)]);
  const routeOf = value => {
    try { const path = new URL(value, location.href).pathname; return routes.has(path) ? path : "(unknown)"; }
    catch (_) { return "(unknown)"; }
  };
  let refreshing = null;
  const currentToken = () => window.webuiCSRF?.() || document.querySelector('meta[name="csrf-token"]')?.content;
  async function deliver(body) {
    let token = currentToken();
    const send = () => fetch("/api/client-error", {method:"POST", cache:"no-store", credentials:"same-origin", headers:{"Content-Type":"application/json", "X-CSRF-Token":token || ""}, body, signal:AbortSignal.timeout(10000)});
    let response = await send();
    if (response.status === 403 && (await response.json()).code === "csrf_invalid") {
      if (!refreshing) refreshing = (async () => {
        const page = await fetch("/", {cache:"no-store", redirect:"error", signal:AbortSignal.timeout(10000)});
        if (!page.ok) throw new Error("Diagnostic session unavailable");
        return new DOMParser().parseFromString(await page.text(), "text/html").querySelector('meta[name="csrf-token"]')?.content;
      })().finally(() => {refreshing = null;});
      token = await refreshing;
      if (token) response = await send();
    }
    if (!response.ok) throw new Error("Diagnostic report unavailable");
  }
  window.reportWebUIError = (kind, route="/", response=null) => {
    const body = JSON.stringify({kind, route:routeOf(route), status:response?.status, request_id:response?.headers?.get("X-Request-ID")});
    // Use raw fetch, never the instrumented wrappers: failure must not recurse or replay an action.
    void deliver(body).catch(() => console.warn("Web UI diagnostic could not reach the server; no terminal action was retried."));
  };
  window.addEventListener("error", event => reportWebUIError(event instanceof ErrorEvent ? "javascript_error" : "resource_error"), true);
  window.addEventListener("unhandledrejection", () => reportWebUIError("unhandled_rejection"));
})();
