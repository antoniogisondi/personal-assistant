"""The /setup page: connect services without touching files or the API docs.

A small, dependency-free page. It never embeds secrets; it asks for the access token once and
keeps it in the browser's localStorage. All dynamic text is written with textContent (no
innerHTML), and a per-request CSP nonce allows only this page's own inline script/style.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_PAGE = """<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GSOI - Collega i tuoi servizi</title>
<style nonce="__NONCE__">
:root{--bg:#f6f7f9;--fg:#15171a;--mut:#5b6470;--card:#fff;--bd:#dde1e6;--ac:#2457d6;--ok:#157f3b;--er:#b3261e}
@media (prefers-color-scheme:dark){:root{--bg:#121417;--fg:#eceef1;--mut:#9aa3ae;--card:#1b1e23;--bd:#2c3037;--ac:#7ea3ff;--ok:#5fd18a;--er:#ff8a80}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}
main{max-width:40rem;margin:0 auto;padding:1.5rem 1rem 4rem}
h1{font-size:1.4rem;margin:.2rem 0 1.2rem}h2{font-size:1.05rem;margin:0 0 .3rem}
.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:1rem 1.1rem;margin:0 0 1rem}
.mut{color:var(--mut);font-size:.9rem}.row{display:flex;gap:.6rem;flex-wrap:wrap;align-items:center;margin-top:.7rem}
label{display:block;font-size:.85rem;color:var(--mut);margin-top:.7rem}
input{width:100%;padding:.55rem .65rem;border-radius:8px;border:1px solid var(--bd);background:var(--bg);color:var(--fg);font:inherit}
button{padding:.55rem .9rem;border-radius:8px;border:1px solid var(--bd);background:var(--card);color:var(--fg);font:inherit;cursor:pointer}
button.primary{background:var(--ac);border-color:var(--ac);color:#fff}button:disabled{opacity:.5;cursor:default}
.pill{display:inline-block;padding:.1rem .6rem;border-radius:99px;font-size:.8rem;border:1px solid var(--bd)}
.ok{color:var(--ok)}.er{color:var(--er)}code{background:var(--bg);padding:.1rem .35rem;border-radius:5px;word-break:break-all}
.hidden{display:none}
</style></head><body><main>
<h1>Collega i tuoi servizi</h1>

<section class="card" id="auth">
  <h2>1. Chiave di accesso</h2>
  <p class="mut">È il valore di <code>GSOI_API_TOKEN</code>. Resta solo in questo browser.</p>
  <label for="token">Chiave di accesso</label>
  <input id="token" type="password" autocomplete="off" spellcheck="false">
  <div class="row"><button class="primary" id="save-token">Continua</button><span id="auth-msg" class="mut"></span></div>
</section>

<section class="card hidden" id="google">
  <h2>Google (Gmail e Calendar) <span class="pill" id="g-status">...</span></h2>
  <p class="mut" id="g-help"></p>

  <div id="g-connect" class="hidden"><div class="row">
    <button class="primary" id="g-start">Collega Google</button>
  </div></div>

  <div id="g-done" class="hidden">
    <p class="ok">Account collegato.</p>
    <div class="row"><button id="g-stop">Scollega</button></div>
  </div>

  <p id="g-msg" class="mut" role="status"></p>

  <details id="g-dev">
    <summary class="mut">Impostazioni per lo sviluppatore</summary>
    <p class="mut">Serve solo a chi installa o crea l'assistente, una volta sola: i tuoi utenti non
    vedono né inseriscono nulla di questo. Incolla le credenziali dell'applicazione Google
    (<a href="https://github.com/antoniogisondi/personal-assistant/blob/main/docs/google-setup.md" target="_blank" rel="noopener noreferrer">guida</a>).
    Come "URI di reindirizzamento autorizzato" usa:</p>
    <p><code id="g-redirect"></code></p>
    <label for="g-cid">Client ID</label><input id="g-cid" autocomplete="off" spellcheck="false">
    <label for="g-sec">Client Secret</label><input id="g-sec" type="password" autocomplete="off" spellcheck="false">
    <div class="row"><button class="primary" id="g-save">Salva</button>
    <button id="g-unconfig" class="hidden">Rimuovi credenziali salvate</button></div>
  </details>
</section>
</main>
<script nonce="__NONCE__">
(function () {
  var $ = function (id) { return document.getElementById(id); };
  var KEY = "gsoi_token", timer = null;
  function token() { try { return localStorage.getItem(KEY) || ""; } catch (e) { return ""; } }
  function say(id, text, cls) { var el = $(id); el.textContent = text; el.className = cls || "mut"; }
  function show(id, on) { $(id).classList.toggle("hidden", !on); }

  async function api(method, path, body) {
    var res = await fetch(path, {
      method: method,
      headers: Object.assign({ "Authorization": "Bearer " + token() },
        body ? { "Content-Type": "application/json" } : {}),
      body: body ? JSON.stringify(body) : undefined
    });
    var data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (!res.ok) {
      var msg = data && ((data.error && data.error.message) || data.detail);
      if (Array.isArray(msg)) { msg = msg.map(function (d) { return d.msg; }).join(". "); }
      var err = new Error(typeof msg === "string" && msg ? msg : "Errore " + res.status);
      err.status = res.status; throw err;
    }
    return data;
  }

  function render(c) {
    $("g-redirect").textContent = c.redirect_uri;
    show("g-connect", c.configured && !c.connected);
    show("g-done", c.connected);
    show("g-unconfig", c.config_source === "app");
    var label = !c.configured ? "non disponibile" : c.connected ? "collegato" : c.status === "needs_reauth" ? "da ricollegare" : "pronto";
    var pill = $("g-status"); pill.textContent = label; pill.className = "pill " + (c.connected ? "ok" : "");
    $("g-help").textContent = !c.configured
      ? "Google non è ancora stato attivato su questa installazione. Chiedi a chi ti ha fornito l'assistente di completare la configurazione."
      : c.connected ? "L'assistente può leggere le email, il calendario e preparare bozze. Invii e nuovi eventi richiedono sempre la tua conferma."
      : "Premi il pulsante: si apre Google e scegli cosa consentire.";
    if (c.connected && timer) { clearInterval(timer); timer = null; say("g-msg", ""); }
  }

  async function refresh() {
    try {
      var list = await api("GET", "/v1/connections");
      show("google", true); say("auth-msg", "Accesso eseguito.", "ok mut");
      render(list[0]);
    } catch (e) {
      show("google", false);
      say("auth-msg", e.status === 401 ? "Chiave non valida." : e.message, "er mut");
    }
  }

  $("save-token").onclick = function () {
    try { localStorage.setItem(KEY, $("token").value.trim()); } catch (e) {}
    $("token").value = ""; refresh();
  };
  $("g-save").onclick = async function () {
    try {
      render(await api("PUT", "/v1/connections/google/config",
        { client_id: $("g-cid").value, client_secret: $("g-sec").value }));
      $("g-sec").value = ""; say("g-msg", "Credenziali salvate.", "ok mut");
    } catch (e) { say("g-msg", e.message, "er mut"); }
  };
  $("g-start").onclick = async function () {
    try {
      var r = await api("POST", "/v1/connections/google/start");
      window.open(r.auth_url, "_blank", "noopener");
      say("g-msg", "Completa l'autorizzazione nella scheda di Google...", "mut");
      if (timer) clearInterval(timer);
      timer = setInterval(refresh, 2000);
    } catch (e) { say("g-msg", e.message, "er mut"); }
  };
  $("g-stop").onclick = async function () {
    try { render(await api("DELETE", "/v1/connections/google")); say("g-msg", "Account scollegato.", "mut"); }
    catch (e) { say("g-msg", e.message, "er mut"); }
  };
  $("g-unconfig").onclick = async function () {
    try { render(await api("DELETE", "/v1/connections/google/config")); say("g-msg", "", "mut"); }
    catch (e) { say("g-msg", e.message, "er mut"); }
  };
  if (token()) refresh();
})();
</script></body></html>
"""


@router.get("/setup", response_class=HTMLResponse, include_in_schema=False)
async def setup_page() -> HTMLResponse:
    nonce = secrets.token_urlsafe(16)
    response = HTMLResponse(_PAGE.replace("__NONCE__", nonce))
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; "
        f"script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response
