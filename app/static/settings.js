"use strict";

/* ---------- settings: mail providers (uses helpers from app.js) ---------- */
async function viewSettings(view, token) {
  const data = await api("GET", "/settings/mail");
  if (token !== renderToken) return;
  // Working copy. Secrets typed here live in `key` / `password`; stored ones only show as hints.
  let list = data.providers.map((p) => ({ ...p }));
  const uid = () => Math.random().toString(36).slice(2, 10);
  const isNew = (id) => String(id).startsWith("new-");

  // Copy what is typed in the form back into `list` (call before any re-render).
  const sync = () => $$("[data-p]", view).forEach((card) => {
    const p = list.find((x) => x.id === card.dataset.p);
    if (!p) return;
    $$("[data-f]", card).forEach((el) => { p[el.dataset.f] = el.type === "checkbox" ? el.checked : el.value; });
    if (p.type === "resend") $$("[data-k]", card).forEach((row) => {
      const k = p.keys.find((x) => x.id === row.dataset.k);
      k.label = $("[data-kl]", row).value;
      k.key = $("[data-kv]", row).value;
    });
  });

  const keyRow = (k) => `<div class="row" data-k="${esc(k.id)}">
      <input type="text" data-kl value="${esc(k.label)}" placeholder="Label" style="flex:1;min-width:120px" aria-label="Key label">
      <input type="text" data-kv value="${esc(k.key || "")}" placeholder="${k.hint ? "saved: " + esc(k.hint) + " (blank keeps it)" : "re_..."}" style="flex:3;min-width:220px" autocomplete="off" aria-label="API key">
      <button class="danger ghost" data-rmk="${esc(k.id)}" type="button">Remove</button></div>`;

  const card = (p, i) => `<div class="card stack" data-p="${esc(p.id)}">
      <div class="row spread">
        <h3>${p.type === "resend" ? "Resend" : "SMTP"} ${p.ready === false ? '<span class="badge failed">incomplete</span>' : ""}</h3>
        <span class="row"><button data-up="${esc(p.id)}" type="button" ${i === 0 ? "disabled" : ""} aria-label="Move up">↑</button>
          <button data-down="${esc(p.id)}" type="button" ${i === list.length - 1 ? "disabled" : ""} aria-label="Move down">↓</button>
          <button class="danger" data-rm="${esc(p.id)}" type="button">Delete</button></span>
      </div>
      <div class="row" style="align-items:flex-start">
        <label class="field" style="flex:1;min-width:180px"><span>Name</span><input type="text" data-f="name" value="${esc(p.name)}"></label>
        <label class="field" style="flex:1;min-width:180px"><span>From address (must be allowed by the provider)</span><input type="text" data-f="from_email" value="${esc(p.from_email)}" placeholder="certificates@yourdomain.com"></label>
      </div>
      ${p.type === "resend" ? `
        <div class="stack"><span class="muted small">API keys, used in order. When one reaches its limit or is rejected, sending moves to the next key.</span>
          ${p.keys.map(keyRow).join("")}
          <div><button data-addk="${esc(p.id)}" type="button">Add key</button></div></div>` : `
        <div class="row" style="align-items:flex-start">
          <label class="field" style="flex:2;min-width:180px"><span>Host</span><input type="text" data-f="host" value="${esc(p.host || "")}" placeholder="smtp.gmail.com"></label>
          <label class="field" style="flex:1;min-width:90px"><span>Port</span><input type="number" data-f="port" value="${esc(p.port ?? 587)}"></label>
          <label class="field" style="flex:1;min-width:140px"><span>Security</span><select data-f="security">
            ${[["starttls", "STARTTLS (587)"], ["ssl", "SSL/TLS (465)"], ["none", "None (25)"]].map(([v, l]) => `<option value="${v}" ${p.security === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
        </div>
        <div class="row" style="align-items:flex-start">
          <label class="field" style="flex:1;min-width:180px"><span>Username</span><input type="text" data-f="username" value="${esc(p.username || "")}" autocomplete="off"></label>
          <label class="field" style="flex:1;min-width:180px"><span>Password / app password</span><input type="password" data-f="password" value="${esc(p.password || "")}" placeholder="${p.has_password ? "saved (blank keeps it)" : ""}" autocomplete="new-password"></label>
        </div>`}
      <label class="check"><input type="checkbox" data-f="fallback_to_next" ${p.fallback_to_next ? "checked" : ""} ${i === list.length - 1 ? "disabled" : ""}> When this provider can't send any more, continue with the next one in this list</label>
      <div class="row"><input type="email" data-test-to placeholder="Send a test to…" style="flex:1;min-width:200px" aria-label="Test address">
        <button data-test="${esc(p.id)}" type="button" ${isNew(p.id) ? "disabled" : ""} title="${isNew(p.id) ? "Save first" : ""}">Send test</button></div>
      <div data-result></div>
    </div>`;

  const draw = () => {
    view.innerHTML = `
      <div class="row spread"><h1>Mail settings</h1><a href="#/">Back to projects</a></div>
      <p class="muted">Providers are used in the order below. A project picks where to start; with “continue with the next one” ticked, sending carries on down the list when a provider hits its limit. Keys and passwords are stored on this computer in the data folder.</p>
      <div class="stack">${list.length ? list.map(card).join("") : '<div class="empty">No providers yet. Add Resend or SMTP below.</div>'}</div>
      <div class="row" style="margin-top:1rem">
        <button id="add-resend" type="button">Add Resend</button><button id="add-smtp" type="button">Add SMTP</button>
        <span style="flex:1"></span><button id="save" class="primary" type="button">Save settings</button></div>`;
    const find = (id) => list.find((p) => p.id === id);
    const act = (sel, attr, fn) => $$(sel, view).forEach((b) => b.addEventListener("click", guard(async () => { sync(); await fn(b.dataset[attr], b); })));
    act("[data-rm]", "rm", (id) => { if (!confirm(`Delete "${find(id).name}"?`)) return; list = list.filter((p) => p.id !== id); draw(); });
    act("[data-up]", "up", (id) => { const i = list.findIndex((p) => p.id === id); [list[i - 1], list[i]] = [list[i], list[i - 1]]; draw(); });
    act("[data-down]", "down", (id) => { const i = list.findIndex((p) => p.id === id); [list[i + 1], list[i]] = [list[i], list[i + 1]]; draw(); });
    act("[data-addk]", "addk", (id) => { const p = find(id); p.keys.push({ id: uid(), label: `key ${p.keys.length + 1}`, key: "" }); draw(); });
    act("[data-rmk]", "rmk", (kid, b) => { const p = find(b.closest("[data-p]").dataset.p); p.keys = p.keys.filter((k) => k.id !== kid); draw(); });
    act("[data-test]", "test", async (id, b) => {
      const c = b.closest("[data-p]"), to = $("[data-test-to]", c).value;
      const out = $("[data-result]", c);
      out.innerHTML = '<span class="muted small">Sending…</span>';
      const r = await api("POST", `/settings/mail/${id}/test`, { to });
      out.innerHTML = r.ok ? `<div class="banner ok">Sent. Check the inbox of ${esc(to)}.</div>` : `<div class="banner bad">${esc(r.error)}</div>`;
    });
    $("#add-resend").onclick = () => { sync(); list.push({ id: "new-" + uid(), type: "resend", name: `Resend ${list.length + 1}`, from_email: "", keys: [{ id: uid(), label: "key 1", key: "" }], fallback_to_next: true }); draw(); };
    $("#add-smtp").onclick = () => { sync(); list.push({ id: "new-" + uid(), type: "smtp", name: `SMTP ${list.length + 1}`, from_email: "", host: "", port: 587, security: "starttls", username: "", password: "", fallback_to_next: false }); draw(); };
    $("#save").onclick = guard(async () => {
      sync();
      const body = list.map((p) => ({ ...p, id: isNew(p.id) ? undefined : p.id }));
      const saved = await api("PUT", "/settings/mail", { providers: body });
      list = saved.providers.map((p) => ({ ...p }));
      toast("Mail settings saved.");
      draw();
    });
  };
  draw();
}
