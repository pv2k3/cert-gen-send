"use strict";

/* ---------- helpers ---------- */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const res = await fetch("/api" + path, opts);
  let data = null;
  try { data = await res.json(); } catch (_) { /* empty body */ }
  if (!res.ok) {
    let msg = data && data.detail;
    if (Array.isArray(msg)) msg = msg.map((m) => m.msg).join("; ");
    throw new Error(msg || `Request failed (${res.status})`);
  }
  return data;
}

function toast(msg, isErr = false) {
  const el = document.createElement("div");
  el.className = "toast" + (isErr ? " err" : "");
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), isErr ? 7000 : 3500);
}
const guard = (fn) => async (...a) => { try { return await fn(...a); } catch (e) { toast(e.message, true); } };

function openLightbox(src) {
  const lb = $("#lightbox");
  lb.innerHTML = `<img alt="Certificate" src="${esc(src)}">`;
  lb.hidden = false;
}
$("#lightbox").addEventListener("click", () => { $("#lightbox").hidden = true; });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#lightbox").hidden = true; });

/* ---------- state & routing ---------- */
const STEPS = [
  { id: "data", label: "1 · Data" },
  { id: "template", label: "2 · Template" },
  { id: "design", label: "3 · Design" },
  { id: "preview", label: "4 · Preview" },
  { id: "review", label: "5 · Review" },
  { id: "email", label: "6 · Email" },
  { id: "send", label: "7 · Send" },
];
let P = null;          // current project: { id, state }
let renderToken = 0;   // guards against stale async renders
let poller = null;     // active polling timer

function reachable(st) {
  const c = st.certificates;
  const certs = c.pending + c.approved + c.rejected;
  return {
    data: true,
    template: st.recipients > 0 && st.columns.length > 0,
    design: st.recipients > 0 && st.columns.length > 0 && st.has_template,
    preview: st.has_template && st.fields > 0 && st.columns.length > 0,
    review: certs > 0,
    email: c.approved > 0,
    send: c.approved > 0 && st.email_configured,
  };
}

const go = (stepId) => { location.hash = `#/p/${P.id}/${stepId}`; };
window.addEventListener("hashchange", () => route());

async function route() {
  clearInterval(poller);
  const token = ++renderToken;
  const m = location.hash.match(/^#\/p\/([\w-]+)(?:\/(\w+))?/);
  const view = $("#view");
  try {
    if (location.hash === "#/settings") {
      P = null;
      $("#crumb").textContent = "/ Settings";
      await viewSettings(view, token);
      return;
    }
    if (!m) {
      P = null;
      $("#crumb").textContent = "";
      await viewHome(view, token);
      return;
    }
    const id = m[1];
    const state = await api("GET", `/projects/${id}/state`);
    if (token !== renderToken) return;
    P = { id, state };
    $("#crumb").textContent = "/ " + state.name;
    const reach = reachable(state);
    let step = m[2];
    if (!STEPS.some((s) => s.id === step)) step = state.step;
    if (!STEPS.some((s) => s.id === step) || !reach[step]) {
      step = [...STEPS].reverse().find((s) => reach[s.id] && s.id !== "send")?.id || "data";
      if (m[2] && m[2] !== step) toast("Finish the earlier steps first.");
    }
    api("PUT", `/projects/${id}/step`, { step }).catch(() => {});

    view.innerHTML = `
      <ol class="steps">${STEPS.map((s) =>
        `<li><button data-step="${s.id}" ${s.id === step ? 'aria-current="step"' : ""} ${reach[s.id] ? "" : "disabled"}>${s.label}</button></li>`).join("")}</ol>
      <section id="step"></section>`;
    $$(".steps button", view).forEach((b) => b.addEventListener("click", () => go(b.dataset.step)));
    await STEP_VIEWS[step]($("#step"), token);
  } catch (e) {
    if (token === renderToken) view.innerHTML = `<div class="banner bad">${esc(e.message)}</div><p><a href="#/">Back to projects</a></p>`;
  }
}

/* ---------- home ---------- */
async function viewHome(view, token) {
  const projects = await api("GET", "/projects/");
  if (token !== renderToken) return;
  view.innerHTML = `
    <div class="row spread"><h1>Projects</h1></div>
    <form id="newp" class="card row" style="margin:1rem 0">
      <input type="text" name="name" placeholder="New project / event name" required maxlength="80" style="flex:1;min-width:220px" aria-label="Project name">
      <button class="primary">Create project</button>
    </form>
    ${projects.length ? `<div class="grid-cards">${projects.map((p) => `
      <div class="card stack">
        <div><h3>${esc(p.name)}</h3><span class="muted small">${p.recipients} recipients</span></div>
        <div class="row">
          <a class="btn primary" href="#/p/${esc(p.id)}">Open</a>
          <button class="danger ghost" data-del="${esc(p.id)}" data-name="${esc(p.name)}">Delete</button>
        </div>
      </div>`).join("")}</div>` : `<div class="empty">No projects yet. Create one for your first event.</div>`}`;
  $("#newp").addEventListener("submit", guard(async (e) => {
    e.preventDefault();
    const name = new FormData(e.target).get("name");
    const p = await api("POST", "/projects/", { name });
    location.hash = `#/p/${p.id}/data`;
  }));
  $$("[data-del]").forEach((b) => b.addEventListener("click", guard(async () => {
    if (!confirm(`Delete "${b.dataset.name}" and all its data, certificates and send history? This cannot be undone.`)) return;
    await api("DELETE", `/projects/${b.dataset.del}`);
    route();
  })));
}

/* ---------- shared bits ---------- */
function dropZone(label, accept, onFile) {
  const el = document.createElement("label");
  el.className = "drop";
  el.style.display = "block";
  el.innerHTML = `<div><b>${esc(label)}</b></div><div class="muted small">Click to choose or drop a file here</div>
    <input type="file" accept="${esc(accept)}" hidden>`;
  const input = $("input", el);
  input.addEventListener("change", () => input.files[0] && onFile(input.files[0]));
  el.addEventListener("dragover", (e) => { e.preventDefault(); el.classList.add("over"); });
  el.addEventListener("dragleave", () => el.classList.remove("over"));
  el.addEventListener("drop", (e) => { e.preventDefault(); el.classList.remove("over"); e.dataTransfer.files[0] && onFile(e.dataTransfer.files[0]); });
  return el;
}
const upload = (path, file) => { const fd = new FormData(); fd.append("file", file); return api("POST", path, fd); };
const navButtons = (back, next, nextLabel = "Continue") => `
  <div class="row spread" style="margin-top:1.5rem">
    ${back ? `<button id="back">← Back</button>` : "<span></span>"}
    ${next ? `<button id="next" class="primary">${esc(nextLabel)} →</button>` : ""}
  </div>`;

/* ---------- step 1: data ---------- */
async function stepData(root, token) {
  const d = await api("GET", `/p/${P.id}/data?limit=10`);
  if (token !== renderToken) return;
  const hasData = d.total > 0;
  const selected = new Set(d.selected.length ? d.selected : d.columns);

  root.innerHTML = `
    <div class="stack">
      <div><h2>Upload your Excel file</h2>
        <p class="muted">The first sheet is used. Row 1 must contain the column names.${hasData ? " Uploading a new file replaces the recipients and clears generated certificates and send history." : ""}</p></div>
      <div id="dz"></div>
      <div id="tbl"></div>
    </div>`;
  $("#dz").appendChild(dropZone(hasData ? "Replace Excel file (.xlsx)" : "Choose Excel file (.xlsx)", ".xlsx,.xlsm", guard(async (file) => {
    if (hasData && P.state.certificates.pending + P.state.certificates.approved + P.state.certificates.rejected + P.state.sent > 0 &&
        !confirm("Replacing the data clears all generated certificates and send history. Continue?")) return;
    const r = await upload(`/p/${P.id}/excel`, file);
    toast(`Loaded ${r.row_count} rows`);
    route();
  })));
  if (!hasData) return;

  $("#tbl").innerHTML = `
    <div class="row spread"><h3>${d.total} recipients · choose the columns you want to use</h3>
      <div class="row"><button id="all">Select all</button><button id="none">None</button></div></div>
    <div class="table-wrap" style="margin-top:.5rem"><table><thead><tr>
      ${d.columns.map((c) => `<th data-c="${esc(c)}"><label class="check"><input type="checkbox" data-col="${esc(c)}"> ${esc(c)}</label></th>`).join("")}
    </tr></thead><tbody>
      ${d.preview.map((r) => `<tr>${d.columns.map((c) => `<td data-c="${esc(c)}">${esc(r[c])}</td>`).join("")}</tr>`).join("")}
    </tbody></table></div>
    <p class="muted small">Showing the first ${d.preview.length} rows.</p>
    ${navButtons(false, true)}`;

  const sync = () => {
    $$("input[data-col]").forEach((cb) => { cb.checked = selected.has(cb.dataset.col); });
    $$("[data-c]").forEach((el) => el.classList.toggle("off", !selected.has(el.dataset.c)));
    $("#next").disabled = selected.size === 0;
  };
  $$("input[data-col]").forEach((cb) => cb.addEventListener("change", () => {
    cb.checked ? selected.add(cb.dataset.col) : selected.delete(cb.dataset.col); sync();
  }));
  $("#all").onclick = () => { d.columns.forEach((c) => selected.add(c)); sync(); };
  $("#none").onclick = () => { selected.clear(); sync(); };
  $("#next").onclick = guard(async () => {
    await api("PUT", `/p/${P.id}/columns`, { selected: d.columns.filter((c) => selected.has(c)) });
    go("template");
  });
  sync();
}

/* ---------- step 2: template ---------- */
async function stepTemplate(root, token) {
  let tpl = null;
  try { tpl = await api("GET", `/p/${P.id}/template`); } catch (_) { /* none yet */ }
  if (token !== renderToken) return;
  root.innerHTML = `
    <div class="stack">
      <div><h2>Upload the certificate image</h2>
        <p class="muted">PNG or JPG. Use a high resolution image (for example 2000px wide) so the certificates print sharply. Replacing it clears generated certificates.</p></div>
      <div id="dz"></div>
      ${tpl ? `<div class="card"><p class="muted small">${tpl.width ? `${tpl.width} × ${tpl.height}px` : esc(tpl.filename)}</p>
        <img alt="Certificate template" src="/api/p/${esc(P.id)}/template/image?t=${Date.now()}" style="max-width:100%;max-height:60vh;display:block;margin:auto"></div>` : ""}
    </div>
    ${navButtons(true, !!tpl)}`;
  $("#dz").appendChild(dropZone(tpl ? "Replace template image" : "Choose template image", ".png,.jpg,.jpeg", guard(async (file) => {
    const c = P.state.certificates;
    if (c.pending + c.approved + c.rejected > 0 && !confirm("Replacing the template clears all generated certificates. Continue?")) return;
    await upload(`/p/${P.id}/template`, file);
    route();
  })));
  $("#back").onclick = () => go("data");
  if (tpl) $("#next").onclick = () => go("design");
}

/* ---------- step 3: design (placement + typography) ---------- */
async function stepDesign(root, token) {
  const [tpl, data, saved, fonts] = await Promise.all([
    api("GET", `/p/${P.id}/template`), api("GET", `/p/${P.id}/data?limit=50`),
    api("GET", `/p/${P.id}/fields`), api("GET", `/p/${P.id}/fonts`),
  ]);
  if (token !== renderToken) return;
  if (!tpl.width) { root.innerHTML = `<div class="banner bad">The template could not be read as an image. Upload a valid PNG or JPG.</div>${navButtons(true, false)}`; $("#back").onclick = () => go("template"); return; }

  const cols = data.selected;
  const W = tpl.width, H = tpl.height;
  const fields = saved.filter((f) => cols.includes(f.column_name)).map((f) => ({ ...f, bold: !!f.bold }));
  let active = cols[0];
  let dirty = false;
  let sampleId = data.recipient_ids[0];
  const nameOf = (r) => r[cols[0]] || "";

  const preferred = fonts.find((f) => /^(georgia|times new roman|arial)$/i.test(f.family) && /regular/i.test(f.style)) || fonts[0];
  const fontOptions = () => {
    const byFamily = new Map();
    fonts.forEach((f) => { if (!byFamily.has(f.family)) byFamily.set(f.family, []); byFamily.get(f.family).push(f); });
    return [...byFamily].map(([fam, list]) =>
      `<optgroup label="${esc(fam)}">${list.map((f) => `<option value="${esc(f.path)}" data-fam="${esc(f.family)}">${esc(fam)} ${esc(f.style)}</option>`).join("")}</optgroup>`).join("");
  };
  const byCol = (c) => fields.find((f) => f.column_name === c);

  root.innerHTML = `
    <div class="design">
      <div class="stack">
        <div><h2>Place and style the fields</h2>
          <p class="muted small">Pick a field, then <b>click the certificate</b> to place it. Drag a marker to fine-tune. The point you click is where the text is anchored (centre of the text height).</p></div>
        <div class="fieldlist" id="flist"></div>
        <div class="card" id="panel"></div>
        <label class="field"><span>Preview with recipient</span>
          <select id="sample">${data.recipient_ids.map((id, i) => `<option value="${id}">${esc(nameOf(data.preview[i]) || "Recipient " + (i + 1))}</option>`).join("")}</select></label>
        <div class="row"><label class="btn">Upload a font (.ttf / .otf)<input type="file" id="fontfile" accept=".ttf,.otf" hidden></label></div>
      </div>
      <div class="stack">
        <div class="stage" id="stage"><img id="pimg" alt="Certificate preview" src="/api/p/${esc(P.id)}/template/image?t=${Date.now()}"><div id="markers"></div></div>
        <p class="muted small" id="pstatus">${W} × ${H}px</p>
      </div>
    </div>
    ${navButtons(true, true, "Continue to preview")}`;

  const flist = $("#flist"), panel = $("#panel"), stage = $("#stage"), markers = $("#markers"), pimg = $("#pimg");

  const placedFields = () => fields.map((f) => ({
    column_name: f.column_name, x: Math.round(f.x), y: Math.round(f.y), font_family: f.font_family || null, font_file: f.font_file || null,
    size: Number(f.size) || null, color: f.color || "#000000", bold: !!f.bold, align: f.align || "center",
    max_width: f.max_width ? Number(f.max_width) : null,
  }));

  let reqNo = 0;
  const refresh = debounce(async () => {
    const my = ++reqNo;
    if (!fields.length) { pimg.src = `/api/p/${P.id}/template/image`; return; }
    try {
      const r = await api("POST", `/p/${P.id}/preview`, { recipient_id: sampleId, fields: placedFields() });
      if (my === reqNo) { pimg.src = r.image; $("#pstatus").textContent = `${W} × ${H}px`; }
    } catch (e) { if (my === reqNo) $("#pstatus").textContent = e.message; }
  }, 150);

  const drawMarkers = () => {
    markers.innerHTML = fields.map((f) => `<div class="marker ${f.column_name === active ? "active" : ""}" data-col="${esc(f.column_name)}"
      style="left:${(f.x / W) * 100}%;top:${(f.y / H) * 100}%"><span>${esc(f.column_name)}</span></div>`).join("");
  };

  const drawList = () => {
    flist.innerHTML = cols.map((c) => `<button class="fieldbtn" data-col="${esc(c)}" aria-pressed="${c === active}">
      <span>${esc(c)}</span><span class="badge ${byCol(c) ? "approved" : ""}">${byCol(c) ? "on certificate" : "not added"}</span></button>`).join("");
    $$("button", flist).forEach((b) => b.addEventListener("click", () => { active = b.dataset.col; drawAll(); }));
  };

  const addField = (c, x, y) => {
    const idx = cols.indexOf(c);
    const f = {
      column_name: c, x: x ?? Math.round(W / 2), y: y ?? Math.round(H * (0.4 + 0.15 * idx)),
      font_family: preferred?.family || null, font_file: preferred?.path || null,
      size: Math.max(12, Math.round(H / 14)), color: "#000000", bold: false, align: "center", max_width: null,
    };
    fields.push(f); dirty = true;
    return f;
  };

  const drawPanel = () => {
    const f = byCol(active);
    if (!f) {
      panel.innerHTML = `<h3>${esc(active)}</h3><p class="muted small">This column is not on the certificate.</p>
        <button id="add" class="primary">Add to certificate</button>`;
      $("#add").onclick = () => { addField(active); drawAll(); refresh(); };
      return;
    }
    panel.innerHTML = `<div class="row spread"><h3>${esc(active)}</h3><button id="rm" class="danger ghost">Remove</button></div>
      <div class="controls">
        <label class="field wide"><span>Font</span><select id="f-font">${fontOptions()}</select></label>
        <label class="field"><span>Size (px)</span><input type="number" id="f-size" min="6" max="2000" value="${f.size}"></label>
        <label class="field"><span>Colour</span><input type="color" id="f-color" value="${esc(f.color || "#000000")}"></label>
        <div class="field wide"><span>Alignment around the point</span>
          <div class="seg" style="margin-top:.25rem">${["left", "center", "right"].map((a) => `<button data-align="${a}" aria-pressed="${(f.align || "center") === a}">${a}</button>`).join("")}</div></div>
        <label class="check"><input type="checkbox" id="f-bold" ${f.bold ? "checked" : ""}> Bold</label>
        <label class="field"><span>Wrap width (px, optional)</span><input type="number" id="f-wrap" min="0" value="${f.max_width || ""}" placeholder="no wrap"></label>
      </div>`;
    const sel = $("#f-font");
    if (f.font_file && [...sel.options].some((o) => o.value === f.font_file)) sel.value = f.font_file;
    const set = (patch) => { Object.assign(f, patch); dirty = true; refresh(); };
    sel.onchange = () => set({ font_file: sel.value, font_family: sel.selectedOptions[0].dataset.fam });
    $("#f-size").oninput = (e) => set({ size: Number(e.target.value) || 12 });
    $("#f-color").oninput = (e) => set({ color: e.target.value });
    $("#f-bold").onchange = (e) => set({ bold: e.target.checked });
    $("#f-wrap").oninput = (e) => set({ max_width: Number(e.target.value) || null });
    $$("[data-align]", panel).forEach((b) => b.onclick = () => { set({ align: b.dataset.align }); drawPanel(); });
    $("#rm").onclick = () => { fields.splice(fields.indexOf(f), 1); dirty = true; drawAll(); refresh(); };
  };

  const drawAll = () => { drawList(); drawPanel(); drawMarkers(); };

  // Pointer position -> image pixel coordinates.
  const toImage = (e) => {
    const r = stage.getBoundingClientRect();
    return [Math.min(W, Math.max(0, Math.round(((e.clientX - r.left) / r.width) * W))),
            Math.min(H, Math.max(0, Math.round(((e.clientY - r.top) / r.height) * H)))];
  };
  let dragging = null;
  stage.addEventListener("pointerdown", (e) => {
    const m = e.target.closest(".marker");
    if (m) {
      dragging = byCol(m.dataset.col); active = dragging.column_name;
      stage.setPointerCapture(e.pointerId); drawList(); drawPanel(); drawMarkers();
      return;
    }
    const [x, y] = toImage(e);
    const f = byCol(active) || addField(active);
    f.x = x; f.y = y; dirty = true;
    drawAll(); refresh();
  });
  stage.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    [dragging.x, dragging.y] = toImage(e); dirty = true;
    drawMarkers();
  });
  const endDrag = () => { if (dragging) { dragging = null; refresh(); } };
  stage.addEventListener("pointerup", endDrag);
  stage.addEventListener("pointercancel", endDrag);

  $("#sample").onchange = (e) => { sampleId = Number(e.target.value); refresh(); };
  $("#fontfile").onchange = guard(async (e) => {
    if (!e.target.files[0]) return;
    await upload(`/p/${P.id}/fonts`, e.target.files[0]);
    toast("Font uploaded. Reloading the font list…");
    route();
  });
  $("#back").onclick = () => go("template");
  $("#next").onclick = guard(async () => {
    if (!fields.length) { toast("Add at least one field to the certificate.", true); return; }
    const c = P.state.certificates;
    if (dirty || P.state.fields === 0) {
      if (c.pending + c.approved + c.rejected > 0 &&
          !confirm("Changing the design clears the generated certificates and their approvals. Continue?")) return;
      await api("PUT", `/p/${P.id}/fields`, placedFields());
    }
    go("preview");
  });

  drawAll();
  refresh();
}

/* ---------- step 4: sample previews + generate ---------- */
async function stepPreview(root, token) {
  const data = await api("GET", `/p/${P.id}/data?limit=1`);
  const r = await api("POST", `/p/${P.id}/preview`, { recipient_ids: data.sample_ids });
  if (token !== renderToken) return;
  const c = P.state.certificates, existing = c.pending + c.approved + c.rejected;
  root.innerHTML = `
    <div class="stack">
      <div><h2>Check a few samples</h2>
        <p class="muted">Rendered with real values from the first, middle and last rows. If anything looks off, go back and adjust the design.</p></div>
      <div class="samples">${r.images.map((src) => `<img alt="Sample certificate" src="${src}" class="card" style="padding:.4rem;cursor:zoom-in" data-src>`).join("")}</div>
      ${existing ? `<div class="banner">${existing} certificates are already generated. Generating again replaces them and resets all approvals.</div>` : ""}
      <div id="gen"></div>
    </div>
    <div class="row spread" style="margin-top:1.5rem">
      <button id="back">← Back to design</button>
      <div class="row">
        ${existing ? `<button id="skip">Keep existing → Review</button>` : ""}
        <button id="generate" class="primary">Generate all ${data.total} certificates</button>
      </div>
    </div>`;
  $$("[data-src]").forEach((img) => img.addEventListener("click", () => openLightbox(img.src)));
  $("#back").onclick = () => go("design");
  if ($("#skip")) $("#skip").onclick = () => go("review");
  $("#generate").onclick = guard(async () => {
    if (existing && !confirm("Replace the existing certificates and reset all approvals?")) return;
    await api("POST", `/p/${P.id}/generate`);
    $("#generate").disabled = true; if ($("#skip")) $("#skip").disabled = true;
    const poll = async () => {
      const s = await api("GET", `/p/${P.id}/generate/status`);
      if (token !== renderToken) return;
      $("#gen").innerHTML = `<div class="card stack"><b>${s.status === "failed" ? "Generation failed" : "Generating certificates…"}</b>
        <div class="progress"><div style="width:${s.progress}%"></div></div>
        <span class="muted small">${s.done} of ${s.total}</span>
        ${s.error ? `<div class="banner bad">${esc(s.error)}</div>` : ""}</div>`;
      if (s.status === "failed") { $("#generate").disabled = false; clearInterval(poller); return; }
      if (s.status === "complete") { clearInterval(poller); toast("Certificates generated"); go("review"); }
    };
    poller = setInterval(guard(poll), 800);
    poll();
  });
}

/* ---------- step 5: review & approve ---------- */
async function stepReview(root, token) {
  const q = { status: "", q: "", page: 1 };
  const sel = new Set();
  let cols = [];
  try { cols = (await api("GET", `/p/${P.id}/data?limit=1`)).selected; } catch (_) { /* ignore */ }

  root.innerHTML = `
    <div class="stack">
      <div><h2>Review the certificates</h2>
        <p class="muted">Click a certificate to enlarge it. Only <b>approved</b> certificates are sent.</p></div>
      <div class="row" id="counts"></div>
      <div class="row">
        <select id="fstatus" style="width:auto" aria-label="Filter by status">
          <option value="">All statuses</option><option value="pending">Pending</option><option value="approved">Approved</option><option value="rejected">Rejected</option></select>
        <input type="text" id="fq" placeholder="Search name, email…" style="max-width:260px" aria-label="Search">
        <span class="muted small" id="selcount"></span>
      </div>
      <div class="row">
        <button id="selpage">Select page</button><button id="selnone">Clear selection</button>
        <button id="approve" class="primary">Approve selected</button><button id="reject">Reject selected</button>
        <span style="flex:1"></span>
        <button id="approveall">Approve all matching</button><button id="rejectall">Reject all matching</button>
      </div>
      <div class="certs" id="grid"></div>
      <div class="row" style="justify-content:center" id="pager"></div>
    </div>
    ${navButtons(true, true, "Continue to email")}`;

  let current = { certificates: [], total: 0, counts: {} };
  const load = async () => {
    const p = new URLSearchParams({ page: q.page, page_size: 24 });
    if (q.status) p.set("status", q.status);
    if (q.q) p.set("q", q.q);
    current = await api("GET", `/p/${P.id}/certificates?${p}`);
    if (token !== renderToken) return;
    const c = current.counts;
    $("#counts").innerHTML = ["pending", "approved", "rejected"].map((s) => `<span class="badge ${s}">${c[s] || 0} ${s}</span>`).join("");
    $("#next").disabled = !(c.approved > 0);
    drawGrid();
  };
  const label = (row) => {
    const d = JSON.parse(row.data);
    return cols.map((c) => d[c]).filter(Boolean)[0] || `#${row.recipient_id}`;
  };
  const drawGrid = () => {
    $("#grid").innerHTML = current.certificates.length ? current.certificates.map((c) => `
      <div class="thumb ${sel.has(c.id) ? "sel" : ""}" data-id="${c.id}">
        <img loading="lazy" alt="Certificate for ${esc(label(c))}" src="/api/p/${esc(P.id)}/certificates/${c.id}/thumb" data-full="/api/p/${esc(P.id)}/certificates/${c.id}/image">
        <div class="meta"><label class="check name"><input type="checkbox" ${sel.has(c.id) ? "checked" : ""}> <span class="name">${esc(label(c))}</span></label>
          <span class="badge ${c.status}">${c.status}</span></div></div>`).join("") : `<div class="empty" style="grid-column:1/-1">No certificates match.</div>`;
    $$(".thumb", $("#grid")).forEach((el) => {
      const id = Number(el.dataset.id);
      $("input", el).addEventListener("change", (e) => { e.target.checked ? sel.add(id) : sel.delete(id); drawGrid(); });
      $("img", el).addEventListener("click", () => openLightbox($("img", el).dataset.full));
    });
    $("#selcount").textContent = sel.size ? `${sel.size} selected` : "";
    const pages = Math.max(1, Math.ceil(current.total / current.page_size));
    $("#pager").innerHTML = `<button id="pp" ${q.page <= 1 ? "disabled" : ""}>← Prev</button><span class="muted">Page ${q.page} of ${pages} · ${current.total} matching</span><button id="pn" ${q.page >= pages ? "disabled" : ""}>Next →</button>`;
    $("#pp").onclick = () => { q.page--; load(); };
    $("#pn").onclick = () => { q.page++; load(); };
  };

  const setStatus = guard(async (status, all) => {
    if (all) {
      if (!confirm(`${status === "approved" ? "Approve" : "Reject"} all ${current.total} matching certificates?`)) return;
      await api("PATCH", `/p/${P.id}/certificates`, { all: true, status, filter: { status: q.status || undefined, q: q.q || undefined } });
    } else {
      if (!sel.size) { toast("Select some certificates first."); return; }
      await api("PATCH", `/p/${P.id}/certificates`, { ids: [...sel], status });
    }
    sel.clear();
    P.state = await api("GET", `/projects/${P.id}/state`);
    await load();
  });
  $("#approve").onclick = () => setStatus("approved", false);
  $("#reject").onclick = () => setStatus("rejected", false);
  $("#approveall").onclick = () => setStatus("approved", true);
  $("#rejectall").onclick = () => setStatus("rejected", true);
  $("#selpage").onclick = () => { current.certificates.forEach((c) => sel.add(c.id)); drawGrid(); };
  $("#selnone").onclick = () => { sel.clear(); drawGrid(); };
  $("#fstatus").onchange = (e) => { q.status = e.target.value; q.page = 1; guard(load)(); };
  $("#fq").oninput = debounce((e) => { q.q = e.target.value.trim(); q.page = 1; guard(load)(); }, 300);
  $("#back").onclick = () => go("preview");
  $("#next").onclick = () => go("email");
  await load();
}

/* ---------- step 6: email content ---------- */
const DEFAULT_HTML = `<div style="font-family:Arial,sans-serif;max-width:560px;margin:auto">
  <h2>Congratulations, {{name}}!</h2>
  <p>Thank you for taking part. Your certificate is attached to this email.</p>
  <p>Best regards,<br>The Team</p>
</div>`;

async function stepEmail(root, token) {
  const [data, s] = await Promise.all([api("GET", `/p/${P.id}/data?limit=50`), api("GET", `/p/${P.id}/email`)]);
  if (token !== renderToken) return;
  const guess = s.email_column || data.columns.find((c) => /e-?mail/i.test(c)) || data.columns[0];
  const firstCol = data.selected[0] || data.columns[0];
  let sampleId = data.recipient_ids[0];

  root.innerHTML = `
    ${s.resend_configured ? "" : `<div class="banner bad" style="margin-bottom:1rem">No mail provider is set up. Add one in <a href="#/settings">Settings</a> before sending.</div>`}
    <div class="split">
      <div class="stack">
        <h2>Email content</h2>
        <div class="row" style="align-items:flex-start">
          <label class="field" style="flex:1;min-width:180px"><span>Column with the email address</span>
            <select id="e-col">${data.columns.map((c) => `<option ${c === guess ? "selected" : ""}>${esc(c)}</option>`).join("")}</select></label>
          <label class="field" style="flex:1;min-width:180px"><span>Send with (<a href="#/settings">manage</a>)</span>
            <select id="e-acct">${s.accounts.map((a) => `<option value="${esc(a.id)}" ${a.id === s.account ? "selected" : ""}>${esc(a.name)} · ${esc(a.from_email)}</option>`).join("")}</select></label>
        </div>
        <div class="row" style="align-items:flex-start">
          <label class="field" style="flex:1;min-width:180px"><span>From name (optional)</span><input type="text" id="e-from" value="${esc(s.from_name)}" placeholder="Event Team"></label>
          <label class="field" style="flex:1;min-width:180px"><span>Reply-to (optional)</span><input type="email" id="e-reply" value="${esc(s.reply_to)}" placeholder="you@example.com"></label>
        </div>
        <label class="field"><span>Subject</span><input type="text" id="e-subject" value="${esc(s.subject || "Your certificate")}"></label>
        <div class="field"><span>Attach as</span>
          <div class="row" style="margin-top:.25rem">${[["pdf", "PDF"], ["png", "PNG image"], ["both", "PDF and PNG"]].map(([v, l]) =>
            `<label class="check"><input type="radio" name="fmt" value="${v}" ${s.attach_format === v ? "checked" : ""}> ${l}</label>`).join("")}</div></div>
        <div class="field"><span>Insert a placeholder</span><div class="chips" style="margin-top:.25rem">
          ${data.columns.map((c) => `<button class="chip" data-ph="${esc(c)}" type="button">{{${esc(c)}}}</button>`).join("")}</div></div>
        <label class="field"><span>HTML body (paste your HTML here)</span><textarea id="e-html" spellcheck="false">${esc(s.html || DEFAULT_HTML.replace("{{name}}", `{{${firstCol}}}`))}</textarea></label>
      </div>
      <div class="stack">
        <h2>Preview</h2>
        <label class="field"><span>Preview as</span>
          <select id="e-sample">${data.recipient_ids.map((id, i) => `<option value="${id}">${esc(data.preview[i]?.[firstCol] || "Recipient " + (i + 1))}</option>`).join("")}</select></label>
        <div id="e-err"></div>
        <div><span class="muted small">Subject:</span> <b id="e-psub"></b></div>
        <iframe class="mail" id="e-frame" sandbox="" title="Email preview"></iframe>
      </div>
    </div>
    ${navButtons(true, true, "Save and review send plan")}`;

  const body = () => ({ subject: $("#e-subject").value, html: $("#e-html").value });
  const preview = debounce(guard(async () => {
    const r = await api("POST", `/p/${P.id}/email/preview`, { ...body(), recipient_id: sampleId });
    if (token !== renderToken) return;
    $("#e-err").innerHTML = r.error ? `<div class="banner bad">${esc(r.error)}. Use one of the column names exactly (case does not matter).</div>` : "";
    $("#e-psub").textContent = r.subject;
    $("#e-frame").srcdoc = r.error ? "" : r.html;
  }), 300);

  $("#e-subject").oninput = preview;
  $("#e-html").oninput = preview;
  $("#e-sample").onchange = (e) => { sampleId = Number(e.target.value); preview(); };
  $$("[data-ph]").forEach((b) => b.onclick = () => {
    const ta = $("#e-html"), tag = `{{${b.dataset.ph}}}`;
    ta.setRangeText(tag, ta.selectionStart, ta.selectionEnd, "end"); ta.focus(); preview();
  });
  $("#back").onclick = () => go("review");
  $("#next").onclick = guard(async () => {
    const fmt = $('input[name="fmt"]:checked')?.value || "pdf";
    await api("PUT", `/p/${P.id}/email`, {
      ...body(), email_column: $("#e-col").value, attach_format: fmt,
      from_name: $("#e-from").value, reply_to: $("#e-reply").value,
      account: $("#e-acct")?.value || "",
    });
    P.state = await api("GET", `/projects/${P.id}/state`);
    go("send");
  });
  preview();
}

/* ---------- step 7: send plan, confirm, deliver ---------- */
async function stepSend(root, token) {
  let plan;
  try { plan = await api("GET", `/p/${P.id}/send/plan`); }
  catch (e) { root.innerHTML = `<div class="banner bad">${esc(e.message)}</div>${navButtons(true, false)}`; $("#back").onclick = () => go("email"); return; }
  if (token !== renderToken) return;
  const status0 = await api("GET", `/p/${P.id}/send/status`);

  const fmtLabel = { pdf: "PDF", png: "PNG", both: "PDF + PNG" }[plan.attachment_format] || plan.attachment_format;
  const acctSelect = () => plan.accounts.length > 1 ? `<label class="field" style="max-width:360px"><span>Send with</span>
    <select class="acct">${plan.accounts.map((a) => `<option value="${esc(a.id)}" ${a.id === plan.account ? "selected" : ""}>${esc(a.name)} · ${esc(a.from_email)}</option>`).join("")}</select></label>` : "";
  const pickedAccount = () => ($("#progress .acct") || $(".acct"))?.value || undefined;
  const blockers = [];
  if (!plan.resend_configured) blockers.push("No mail provider is set up. Add one in Settings first.");
  if (plan.template_error) blockers.push("Email template problem: " + plan.template_error);
  if (plan.to_send === 0 && !status0.running) blockers.push("There is nothing left to send.");

  root.innerHTML = `
    <div class="stack">
      <h2>Send plan</h2>
      <div class="stats">
        <div class="stat"><b>${plan.to_send}</b>emails to send</div>
        <div class="stat"><b>${plan.approved_count}</b>approved certificates</div>
        <div class="stat"><b>${plan.already_sent}</b>already sent</div>
        <div class="stat"><b>${plan.invalid_recipients.length}</b>invalid emails (skipped)</div>
        <div class="stat"><b>${plan.duplicates.length}</b>duplicate addresses</div>
      </div>
      <div class="card stack">
        <div><span class="muted small">Subject</span><div><b>${esc(plan.subject)}</b></div></div>
        <div class="row"><span class="muted small">Attachment</span><b>${fmtLabel}</b>
          <span class="muted small">Address column</span><b>${esc(plan.email_column)}</b>
          <span class="muted small">Estimated time</span><b>~${Math.max(1, Math.round(plan.estimated_duration_sec / 60 * 10) / 10)} min</b></div>
        ${plan.duplicates.length ? `<div class="banner">Duplicate addresses will each receive one email per certificate: ${plan.duplicates.slice(0, 5).map(esc).join(", ")}${plan.duplicates.length > 5 ? "…" : ""}</div>` : ""}
        ${plan.invalid_recipients.length ? `<details><summary>${plan.invalid_recipients.length} recipients without a valid email will be skipped</summary>
          <div class="table-wrap"><table><tbody>${plan.invalid_recipients.slice(0, 50).map((r) => `<tr><td>${esc(Object.values(r).join(" · "))}</td></tr>`).join("")}</tbody></table></div></details>` : ""}
        ${blockers.map((b) => `<div class="banner bad">${esc(b)}</div>`).join("")}
      </div>
      <div id="confirm" class="card stack" ${(blockers.length && !status0.running) || status0.running || status0.results.length || status0.remaining < plan.to_send ? "hidden" : ""}>
        <label class="check"><input type="checkbox" id="ok"> I have checked the certificates and the email, and I want to send ${plan.to_send} emails now.</label>
        ${acctSelect()}
        <div><button id="send" class="primary" disabled>Send ${plan.to_send} emails</button></div>
      </div>
      <div id="progress"></div>
    </div>
    ${navButtons(true, false)}`;
  $("#back").onclick = () => go("email");

  let total = 0;
  const poll = async () => {
    const s = await api("GET", `/p/${P.id}/send/status`);
    if (token !== renderToken) return;
    total = Math.max(total, s.remaining);
    const done = total ? Math.round(((total - s.remaining) / total) * 100) : 100;
    const { sent, failed, skipped } = s.summary;
    $("#progress").innerHTML = (s.results.length || s.running) ? `
      <div class="card stack">
        <div class="row spread"><b>${s.running ? "Sending…" : "Finished"}</b>
          <span><span class="badge sent">${sent} sent</span> <span class="badge failed">${failed} failed</span> <span class="badge skipped">${skipped} skipped</span></span></div>
        <div class="progress"><div style="width:${s.running ? done : 100}%"></div></div>
        ${(s.notes || []).map((n) => `<div class="banner">${esc(n)}</div>`).join("")}
        ${!s.running && s.remaining ? `<div class="row">${acctSelect()}<button id="resume" class="primary">Continue sending (${s.remaining} left)</button></div>` : ""}
        ${!s.running && failed ? `<div class="row">${acctSelect()}
          <label class="check"><input type="checkbox" id="retry-ok"> Resend the ${failed} failed email${failed > 1 ? "s" : ""}</label>
          <button id="retry" disabled>Resend ${failed} failed</button></div>` : ""}
        <div class="table-wrap" style="max-height:360px"><table><thead><tr><th>Email</th><th>Status</th><th>Details</th><th></th></tr></thead><tbody>
          ${s.results.map((r) => `<tr><td>${esc(r.email)}</td><td><span class="badge ${r.status}">${r.status}</span></td><td>${esc(r.error || r.sent_at || "")}</td>
            <td>${r.status === "failed" && !s.running ? `<button data-resend="${r.recipient_id}">Resend</button>` : ""}</td></tr>`).join("")}
        </tbody></table></div></div>` : "";
    if ($("#retry")) {
      $("#retry-ok").onchange = (e) => { $("#retry").disabled = !e.target.checked; };
      $("#retry").onclick = guard(async () => { const account = $("#retry").parentElement.querySelector(".acct")?.value; if (account) plan.account = account; await api("POST", `/p/${P.id}/send/retry-failed`, { account }); startPolling(); });
    }
    $$("[data-resend]").forEach((b) => b.onclick = guard(async () => {
      const account = pickedAccount(); if (account) plan.account = account;
      await api("POST", `/p/${P.id}/send/retry-failed`, { account, recipient_id: Number(b.dataset.resend) }); startPolling();
    }));
    if ($("#resume")) $("#resume").onclick = guard(async () => {
      const account = $("#resume").parentElement.querySelector(".acct")?.value; if (account) plan.account = account;
      await api("POST", `/p/${P.id}/send`, { confirm: true, account }); startPolling();
    });
    if (!s.running) clearInterval(poller);
  };
  const startPolling = () => { clearInterval(poller); poller = setInterval(guard(poll), 1500); poll(); };

  $("#ok").onchange = (e) => { $("#send").disabled = !e.target.checked; };
  $("#send").onclick = guard(async () => {
    $("#send").disabled = true; $("#ok").disabled = true;
    const account = pickedAccount(); if (account) plan.account = account;
    await api("POST", `/p/${P.id}/send`, { confirm: true, account });
    startPolling();
  });
  if (status0.running) startPolling(); else poll();
}

const STEP_VIEWS = { data: stepData, template: stepTemplate, design: stepDesign, preview: stepPreview, review: stepReview, email: stepEmail, send: stepSend };

route();
