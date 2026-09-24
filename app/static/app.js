/* Person Intel Agent — image-first UI.
 * Security: all server/remote text is rendered with textContent; links are
 * restricted to http(s); images only from data: URIs or this server's API. */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = { config: null, current: null, events: null, stageCounts: {} };

// ------------------------------------------------------------------ helpers
function safeUrl(url) { return typeof url === "string" && /^https?:\/\//i.test(url) ? url : null; }
function safeImg(src) {
  return typeof src === "string" && (src.startsWith("data:image/") || src.startsWith("/api/")) ? src : null;
}
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = value;
    else if (key === "href") { const u = safeUrl(value); if (u) { el.href = u; el.rel = "noopener noreferrer nofollow"; el.target = "_blank"; } }
    else if (key === "src") { const u = safeImg(value); if (u) el.src = u; }
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key === "style") el.setAttribute("style", value);
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}
function link(url, label) { return safeUrl(url) ? h("a", { href: url, text: label || url }) : h("span", { text: label || url || "—" }); }
function toast(message) {
  const t = $("#toast"); t.textContent = message; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 4000);
}
function fmtDate(iso) { try { return new Date(iso).toLocaleString(); } catch { return iso; } }
function pretty(v) { return String(v || "").replaceAll("_", " "); }

async function api(path, options = {}, retry = true) {
  const resp = await fetch(path, { credentials: "same-origin", ...options });
  if (resp.status === 401 && retry) { await askToken(); return api(path, options, false); }
  let body = null;
  const type = resp.headers.get("content-type") || "";
  if (type.includes("application/json")) body = await resp.json();
  if (!resp.ok) {
    const detail = body && body.detail ? (typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail)) : resp.statusText;
    throw new Error(detail);
  }
  return body;
}
function askToken() {
  return new Promise((resolve) => {
    const dialog = $("#token-dialog");
    const form = $("#token-form");
    form.onsubmit = async (e) => {
      e.preventDefault();
      try {
        await fetch("/api/auth/token", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token: form.token.value }), credentials: "same-origin" });
      } finally { dialog.close(); resolve(); }
    };
    dialog.showModal();
  });
}

// ------------------------------------------------------------------ config
async function loadConfig() {
  try {
    const cfg = await api("/api/config");
    state.config = cfg;
    $("#version").textContent = "v" + cfg.version;
    $("#max-mb").textContent = cfg.limits.max_upload_mb;
    const p = cfg.providers;
    const parts = [];
    const rev = p.reverse_image.filter((x) => x.configured).map((x) => x.name);
    parts.push(rev.length ? `Reverse image: ${rev.join(", ")}` : "Reverse image: not configured (set GOOGLE_VISION_* / TINEYE_*)");
    parts.push(`Web search: ${p.web_search.filter((x) => x.configured).map((x) => x.name).join(", ") || "none"}`);
    parts.push(p.face_matching.available ? `Face matching: ${p.face_matching.model}` : "Face matching: unavailable");
    $("#provider-status").textContent = parts.join(" · ");
    $("#about-model").textContent = p.face_matching.model || "ArcFace";
    const b = cfg.face_bands;
    $("#about-bands").textContent = `Face bands (${b.metric}): VERY_HIGH ${b.VERY_HIGH}, HIGH ${b.HIGH}, MEDIUM ${b.MEDIUM}, LOW ${b.LOW}, NO_MATCH ${b.NO_MATCH}. ${b.note}`;
    const r = cfg.retention;
    $("#about-retention").textContent = `Retention: uploaded reference images are deleted after ${r.reference_image_hours} h, reference face embeddings after ${r.reference_embedding_hours} h, candidate face crops after ${r.face_thumbnail_days} days. No permanent face database is built.`;
  } catch (e) { $("#provider-status").textContent = "Could not load configuration: " + e.message; }
}

// ------------------------------------------------------------------ upload
function hintsFromForm() {
  const form = $("#hints-form");
  const data = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    const v = el.value.trim();
    if (v) data[el.name] = el.type === "number" ? Number(v) : v;
  }
  return data;
}
function fillForm(hints) {
  const form = $("#hints-form");
  for (const el of form.elements) {
    if (!el.name) continue;
    const v = hints ? hints[el.name] : null;
    el.value = Array.isArray(v) ? v.join(", ") : v ?? "";
  }
}

async function handleFiles(fileList) {
  const files = Array.from(fileList || []).slice(0, 10);
  if (!files.length) return;
  $("#upload-errors").hidden = true;
  $("#setup-status").textContent = "Analysing photo…";
  const fd = new FormData();
  files.forEach((f) => fd.append("images", f));
  try {
    let resp;
    if (state.current && state.current.status === "DRAFT") {
      resp = await api(`/api/investigations/${state.current.id}/reference-images`, { method: "POST", body: fd });
      await loadInvestigation(state.current.id, { keepView: true });
    } else {
      resp = await api("/api/investigations", { method: "POST", body: fd });
      setCurrent(resp.investigation);
    }
    showUploadErrors(resp.upload_errors);
    $("#setup-status").textContent = "";
  } catch (e) {
    showUploadErrors([{ file: files[0].name, detail: e.message }]);
    $("#setup-status").textContent = "";
  }
}
function showUploadErrors(errors) {
  const box = $("#upload-errors");
  box.replaceChildren();
  (errors || []).forEach((err) => box.append(h("div", { text: `${err.file ? err.file + ": " : ""}${err.detail}` })));
  box.hidden = !(errors && errors.length);
}

function renderReferences(inv) {
  const box = $("#references");
  box.replaceChildren();
  const refs = inv ? inv.reference_images : [];
  box.hidden = !refs.length;
  refs.forEach((ref) => box.append(referenceCard(inv, ref, inv.status === "DRAFT")));
}

function referenceCard(inv, ref, editable) {
  const wrap = h("div", { class: "ref-img" });
  if (ref.image_available) {
    wrap.append(h("img", { src: `/api/investigations/${inv.id}/reference-images/${ref.id}/image`, alt: "Reference photo" }));
    ref.faces.forEach((face) => {
      const b = face.bbox;
      const box = h("button", {
        class: "facebox" + (face.id === ref.selected_face_id ? " selected" : ""), type: "button",
        title: editable ? "Select as target face" : face.id,
        style: `left:${(b.x / ref.width) * 100}%;top:${(b.y / ref.height) * 100}%;width:${(b.w / ref.width) * 100}%;height:${(b.h / ref.height) * 100}%`,
        onclick: editable ? () => selectFace(inv, ref, face.id) : null,
      }, h("span", { text: face.id === ref.selected_face_id ? "target" : face.id }));
      wrap.append(box);
    });
  } else {
    wrap.append(h("div", { class: "muted small", style: "padding:1rem;color:#ccc", text: "Image removed by retention policy" }));
  }
  const faces = h("div", { class: "faces" }, ref.faces.map((face) =>
    h("img", { class: "face-thumb" + (face.id === ref.selected_face_id ? " selected" : ""), src: face.thumbnail,
      alt: face.id, title: editable ? `Select ${face.id} as target` : face.id,
      onclick: editable ? () => selectFace(inv, ref, face.id) : null })));
  const q = ref.quality;
  return h("div", { class: "ref" }, wrap,
    h("div", { class: "row between wrap", style: "margin-top:.5rem" },
      h("span", {}, "Quality: ", h("span", { class: `quality q-${q.label}`, text: q.label }),
        h("span", { class: "muted small", text: ` · ${ref.width}×${ref.height} · ${ref.faces.length} face(s)` })),
      editable ? h("button", { class: "btn small danger", type: "button", text: "Remove", onclick: () => removeReference(inv, ref) }) : null),
    ref.faces.length > 1 ? h("p", { class: "small", text: "Several faces detected — click the target person." }) : null,
    faces,
    ref.warnings.length ? h("ul", { class: "issues" }, ref.warnings.map((w) => h("li", { text: w }))) : null);
}

async function selectFace(inv, ref, faceId) {
  try {
    await api(`/api/investigations/${inv.id}/reference-images/${ref.id}/face`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ face_id: faceId }) });
    await loadInvestigation(inv.id, { keepView: true });
  } catch (e) { toast(e.message); }
}
async function removeReference(inv, ref) {
  try {
    await api(`/api/investigations/${inv.id}/reference-images/${ref.id}`, { method: "DELETE" });
    await loadInvestigation(inv.id, { keepView: true });
  } catch (e) { toast(e.message); }
}

// ------------------------------------------------------------------ run
async function startSearch() {
  const hints = hintsFromForm();
  const btn = $("#start-btn");
  btn.disabled = true;
  try {
    if (!state.current || state.current.status !== "DRAFT") {
      if (!Object.keys(hints).length) { toast("Upload a photo or enter at least one identity hint."); return; }
      const fd = new FormData();
      Object.entries(hints).forEach(([k, v]) => fd.append(k, v));
      const resp = await api("/api/investigations", { method: "POST", body: fd });
      state.current = resp.investigation;
    } else {
      await api(`/api/investigations/${state.current.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hints: normaliseHints(hints) }) });
    }
    await api(`/api/investigations/${state.current.id}/run`, { method: "POST" });
    location.hash = "inv=" + state.current.id;
    showProgress(state.current.id);
  } catch (e) { toast(e.message); }
  finally { btn.disabled = false; }
}
function normaliseHints(hints) {
  const out = { ...hints };
  for (const key of ["usernames", "emails", "known_urls"]) if (out[key]) out[key] = out[key].split(/[,;\n]/).map((s) => s.trim()).filter(Boolean);
  return out;
}

const STAGES = [
  { key: "image", label: "Reference image & target face", events: ["IMAGE_VALIDATED", "FACE_DETECTED", "REFERENCE_EMBEDDING_CREATED"] },
  { key: "reverse", label: "Reverse image search", events: ["REVERSE_IMAGE_SEARCH_STARTED", "REVERSE_IMAGE_RESULT_FOUND"] },
  { key: "discovery", label: "Parameter-assisted discovery", events: ["CANDIDATE_SEARCH_STARTED"] },
  { key: "pages", label: "Candidate pages", events: ["CANDIDATE_PAGE_DISCOVERED", "TEXT_EVIDENCE_FOUND"] },
  { key: "images", label: "Candidate images & face comparison", events: ["CANDIDATE_IMAGE_DOWNLOADED", "FACE_MATCH_FOUND"] },
  { key: "fusion", label: "Evidence fusion & ranking", events: ["CANDIDATE_UPDATED"] },
  { key: "done", label: "Finished", events: ["INVESTIGATION_COMPLETED", "INVESTIGATION_FAILED"] },
];

function showProgress(id) {
  $("#setup").hidden = true; $("#results").hidden = true; $("#progress").hidden = false;
  $("#progress-state").textContent = "RUNNING";
  state.stageCounts = {};
  $("#stages").replaceChildren(...STAGES.map((s) => h("li", { dataset: { stage: s.key } }, h("span", { text: s.label }), h("span", { class: "detail" }))));
  $("#event-log").replaceChildren();
  if (state.events) state.events.close();
  const es = new EventSource(`/api/investigations/${id}/events`);
  state.events = es;
  es.addEventListener("progress", (msg) => onEvent(JSON.parse(msg.data)));
  es.addEventListener("end", () => { es.close(); loadInvestigation(id); });
  es.onerror = () => { if (es.readyState === EventSource.CLOSED) setTimeout(() => loadInvestigation(id), 1000); };
}

function onEvent(ev) {
  $("#event-log").prepend(h("li", { text: `${new Date(ev.at).toLocaleTimeString()} ${ev.type} — ${ev.message}` }));
  const index = STAGES.findIndex((s) => s.events.includes(ev.type));
  if (index >= 0) {
    $$("#stages li").forEach((li, i) => {
      if (i < index) { li.classList.remove("active"); li.classList.add("done"); }
      if (i === index) li.classList.add(ev.type === "INVESTIGATION_COMPLETED" ? "done" : "active");
    });
    const key = STAGES[index].key;
    const counts = (state.stageCounts[key] = state.stageCounts[key] || {});
    counts[ev.type] = (counts[ev.type] || 0) + 1;
    const li = $(`#stages li[data-stage="${key}"] .detail`);
    const detail = {
      reverse: () => `${counts.REVERSE_IMAGE_RESULT_FOUND || 0} provider result set(s)`,
      pages: () => `${counts.CANDIDATE_PAGE_DISCOVERED || 0} page(s), identity hints matched on ${counts.TEXT_EVIDENCE_FOUND || 0}`,
      images: () => `${counts.CANDIDATE_IMAGE_DOWNLOADED || 0} image(s) analysed, ${counts.FACE_MATCH_FOUND || 0} face match(es)`,
      fusion: () => `${counts.CANDIDATE_UPDATED || 0} candidate(s)`,
    }[key];
    li.textContent = detail ? detail() : ev.message;
  }
  if (ev.type === "WARNING") $$("#stages li.active").forEach((li) => li.classList.add("warn"));
  if (ev.type === "INVESTIGATION_FAILED") $("#progress-state").textContent = "FAILED";
}

// ------------------------------------------------------------------ load / render
async function loadInvestigation(id, { keepView = false } = {}) {
  try {
    const resp = await api(`/api/investigations/${id}`);
    setCurrent(resp.investigation, keepView);
  } catch (e) { toast(e.message); }
}

function setCurrent(inv, keepView = false) {
  state.current = inv;
  fillForm(inv.hints);
  if (inv.status === "DRAFT") {
    $("#setup").hidden = false; $("#progress").hidden = true; $("#results").hidden = true;
    renderReferences(inv);
    location.hash = "inv=" + inv.id;
    return;
  }
  if (inv.status === "RUNNING" || inv.status === "QUEUED") { if (!keepView) showProgress(inv.id); return; }
  renderResults(inv);
}

function newSearch() {
  if (state.events) state.events.close();
  state.current = null;
  fillForm(null);
  renderReferences(null);
  showUploadErrors([]);
  $("#setup").hidden = false; $("#progress").hidden = true; $("#results").hidden = true;
  history.replaceState(null, "", location.pathname);
}

function signal(label, value) {
  const v = value || "UNKNOWN";
  return [h("dt", { text: label }), h("dd", { class: `v-${v}`, text: pretty(v) })];
}

function referenceThumb(inv) {
  for (const ref of inv.reference_images || []) {
    const face = ref.faces.find((f) => f.id === ref.selected_face_id);
    if (face && face.thumbnail) return face.thumbnail;
  }
  return null;
}

function renderResults(inv) {
  const r = inv.result;
  $("#setup").hidden = true; $("#progress").hidden = true; $("#results").hidden = false;
  $("#result-title").textContent = inv.title + ` · ${inv.status}`;
  $("#conclusion").textContent = r ? r.conclusion : inv.error || "No results.";
  $("#result-warnings").replaceChildren(...(r ? r.warnings : []).map((w) => h("div", { text: w })));
  if (inv.error) $("#result-warnings").append(h("div", { text: inv.error }));
  $("#result-refs").replaceChildren(...(inv.reference_images || []).map((ref) => referenceCard(inv, ref, false)));
  const refThumb = referenceThumb(inv);
  const list = $("#candidate-list");
  list.replaceChildren();
  const candidates = r ? r.candidates : [];
  if (!candidates.length) list.append(h("div", { class: "card", text: "No strong candidate found — nothing is fabricated when evidence is missing." }));
  candidates.forEach((c) => list.append(candidateCard(inv, c, refThumb)));
  renderReverse(r); renderPages(r); renderLeads(r); renderProviders(r); renderNotes(inv);
}

function candidateCard(inv, c, refThumb) {
  const a = c.assessment;
  const face = c.best_face;
  return h("button", { class: "candidate", type: "button", onclick: () => openCandidate(inv, c) },
    h("div", { class: "cand-head" }, h("span", { class: "cand-name", text: `${c.rank}. ${c.display_name}` }),
      h("span", { class: `level level-${a.level}`, text: pretty(a.level) + " EVIDENCE" })),
    h("div", { class: "compare" },
      refThumb ? h("img", { src: refThumb, alt: "Target face" }) : h("div", { class: "noface", text: "no target face" }),
      h("span", { class: "muted", text: "↔" }),
      face && face.candidate_thumbnail ? h("img", { src: face.candidate_thumbnail, alt: "Candidate face" })
        : h("div", { class: "noface", text: ["EXACT", "PARTIAL", "MODIFIED"].includes(a.image_occurrence)
          ? "same photo as reference" : "no face compared" })),
    h("dl", { class: "signals" },
      signal("Face similarity", a.face_band || "none"),
      signal("Image occurrence", a.image_occurrence || "none"),
      signal("Name", a.name_match), signal("Location", a.location_match), signal("Username", a.username_match),
      h("dt", { text: "Independent sources" }), h("dd", { text: String(a.independent_sources) })),
    h("div", { class: "platforms" }, (c.platforms.length ? c.platforms : c.domains.slice(0, 4)).map((p) => h("span", { class: "chip", text: p }))),
    h("span", { class: "view-link", text: "View evidence →" }));
}

async function openCandidate(inv, c) {
  const dialog = $("#candidate-dialog");
  $("#cd-title").textContent = `${c.rank}. ${c.display_name}`;
  const body = $("#cd-body");
  const a = c.assessment;
  const face = c.best_face;
  const refThumb = referenceThumb(inv);
  const dims = a.dimensions;
  body.replaceChildren(...[
    h("p", {}, h("span", { class: `level level-${a.level}`, text: pretty(a.level) + " EVIDENCE" }),
      h("span", { class: "muted small", text: " Overall evidence is a descriptive rule-based level, not a probability." })),
    face ? h("div", { class: "card" },
      h("h3", { text: "Face comparison (observation)" }),
      h("div", { class: "compare" },
        refThumb ? h("img", { src: refThumb, alt: "Target face" }) : null, h("span", { text: "↔" }),
        face.candidate_thumbnail ? h("img", { src: face.candidate_thumbnail, alt: "Candidate face" }) : null,
        h("dl", { class: "signals" },
          h("dt", { text: "Face similarity" }), h("dd", { class: `v-${face.match_band}`, text: pretty(face.match_band) }),
          h("dt", { text: "Model" }), h("dd", { text: face.model }),
          h("dt", { text: "Cosine similarity" }), h("dd", { text: face.cosine_similarity.toFixed(3) }),
          h("dt", { text: "Cosine distance" }), h("dd", { text: face.distance.toFixed(3) }),
          h("dt", { text: "References compared" }), h("dd", { text: String(face.references_compared) }))),
      h("p", { class: "small" }, "Candidate image: ", link(face.candidate_image_url)),
      h("p", { class: "small muted", text: "Bands are heuristic descriptions of model similarity. Look-alikes exist; face similarity alone never confirms identity." }))
      : null,
    h("h3", { text: "Why this candidate was ranked" }),
    h("ul", {}, a.reasons.map((x) => h("li", { text: x }))),
    a.caveats.length ? h("div", {}, h("h3", { text: "Caveats" }), h("ul", {}, a.caveats.map((x) => h("li", { text: x })))) : null,
    h("h3", { text: "Signal summary" }),
    h("dl", { class: "signals" },
      signal("Face similarity", a.face_band || "none"), signal("Image occurrence", a.image_occurrence || "none"),
      signal("Name", a.name_match), signal("Location", a.location_match), signal("Username", a.username_match),
      signal("Email", a.email_match), signal("Employer", a.employer_match), signal("Education", a.education_match)),
    h("h3", { text: "Sub-scores (0–1, for ranking only)" }),
    h("div", { class: "dims" }, Object.entries(dims).map(([k, v]) => h("div", { class: "dim" },
      h("div", { text: `${pretty(k)}: ${v.toFixed(2)}` }), h("div", { class: "bar" }, h("i", { style: `width:${Math.round(v * 100)}%` }))))),
    h("h3", { text: "Sources" }),
    h("ul", {}, c.urls.map((u) => h("li", {}, link(u)))),
    c.cluster_reasons.length ? h("div", {}, h("h3", { text: "Why these sources were grouped" }),
      h("ul", {}, c.cluster_reasons.map((x) => h("li", { text: x })))) : null,
    h("h3", { text: "Evidence chain (observations)" }),
    h("ul", { class: "evidence" }, c.evidence.map((ev) => h("li", { class: `s-${ev.strength}` },
      h("div", { class: "row between wrap" }, h("span", { class: "ev-type", text: `${ev.type} · ${ev.strength}` }),
        h("span", { class: "muted small", text: `${ev.provider || ""} ${ev.retrieved_at ? fmtDate(ev.retrieved_at) : ""}` })),
      h("div", { text: ev.observation }),
      ev.text ? h("div", { class: "small muted", text: `Expected “${ev.text.expected}” · observed “${ev.text.observed}”` }) : null,
      ev.source_url ? h("div", { class: "small" }, link(ev.source_url)) : null))),
  ].filter(Boolean));
  dialog.showModal();
  try {
    const detail = await api(`/api/investigations/${inv.id}/candidates/${c.id}`);
    const pages = detail.pages || [];
    if (pages.length) body.append(h("h3", { text: "Extracted page facts" }), h("table", { class: "list" },
      h("tr", {}, ["Page", "Profile name", "Handles", "Locations", "Organisations"].map((t) => h("th", { text: t }))),
      pages.map((p) => h("tr", {}, h("td", {}, link(p.url, p.domain)), h("td", { text: p.profile.profile_name || "—" }),
        h("td", { text: p.profile.usernames.join(", ") || "—" }), h("td", { text: p.profile.locations.join(", ") || "—" }),
        h("td", { text: p.profile.organizations.join(", ") || "—" })))));
  } catch { /* optional */ }
}

function table(headers, rows) {
  return h("table", { class: "list" }, h("tr", {}, headers.map((t) => h("th", { text: t }))), rows);
}
function renderReverse(r) {
  const panel = $("#tab-reverse");
  const rows = (r ? r.reverse_image_results : []).map((x) => h("tr", {},
    h("td", { text: x.provider }), h("td", { class: `v-${x.match_type}`, text: pretty(x.match_type) }),
    h("td", {}, x.page_url ? link(x.page_url) : "—"), h("td", {}, x.image_url ? link(x.image_url, "image") : "—")));
  const entities = r && (r.web_entities.length || r.best_guess_labels.length)
    ? h("div", { class: "card" }, h("h3", { text: "Provider labels (not identity evidence)" }),
      h("p", { class: "small", text: [...r.best_guess_labels.map((l) => "Best guess: " + l), ...r.web_entities.map((e) => e.description)].join(" · ") }))
    : null;
  panel.replaceChildren(...[entities, rows.length ? table(["Provider", "Match type", "Page", "Image"], rows)
    : h("div", { class: "card muted", text: "No reverse image results (provider not configured, no matches, or image-less search)." })].filter(Boolean));
}
function renderPages(r) {
  const rows = (r ? r.pages : []).map((p) => h("tr", {},
    h("td", {}, link(p.url, p.domain)), h("td", { text: p.title || "—" }), h("td", { text: p.origins.map(pretty).join(", ") }),
    h("td", { text: p.is_image_only ? "image" : p.profile.fetched ? "fetched" : (p.profile.fetch_error || "not fetched") }),
    h("td", { text: String(p.images.length) }), h("td", { text: String(p.images.reduce((n, i) => n + i.faces.length, 0)) })));
  $("#tab-pages").replaceChildren(rows.length ? table(["Page", "Title", "Found via", "Status", "Images", "Faces"], rows)
    : h("div", { class: "card muted", text: "No pages." }));
}
function renderLeads(r) {
  const rows = (r ? r.leads : []).map((l) => h("tr", {}, h("td", { text: l.classification }), h("td", { text: l.value }),
    h("td", { text: l.note || "" }), h("td", {}, l.source_url ? link(l.source_url) : "—")));
  $("#tab-leads").replaceChildren(h("p", { class: "small muted", text: "Leads are identifiers and hypotheses to follow up — generated values are not discoveries." }),
    rows.length ? table(["Classification", "Value", "Note", "Source"], rows) : h("div", { class: "card muted", text: "No leads." }));
}
function renderProviders(r) {
  if (!r) { $("#tab-providers").replaceChildren(); return; }
  const s = r.stats;
  const rows = r.provider_runs.map((p) => h("tr", {}, h("td", { text: p.provider }), h("td", { text: p.stage }),
    h("td", { text: p.outcome }), h("td", { text: String(p.result_count) }), h("td", { text: String(p.duration_ms) }),
    h("td", { text: p.cache_hit ? "hit" : "" }), h("td", { text: p.detail || "" }), h("td", { text: p.error || "" })));
  $("#tab-providers").replaceChildren(
    h("p", { class: "small", text: `Pages ${s.pages_discovered} (fetched ${s.pages_fetched}) · images downloaded ${s.images_downloaded} (dedup ${s.images_deduplicated}) · faces ${s.faces_detected} · comparisons ${s.face_comparisons} · candidates ${s.candidate_count} · ${s.duration_ms} ms · cache ${s.cache_hits}/${s.cache_misses} hit/miss` }),
    table(["Provider", "Stage", "Outcome", "Results", "ms", "Cache", "Detail", "Error"], rows));
}
function renderNotes(inv) {
  $("#tag-list").replaceChildren(...inv.tags.map((t) => h("span", { class: "chip" }, t, " ",
    h("button", { class: "btn ghost small", type: "button", text: "✕", "aria-label": "Remove tag", onclick: () => removeTag(t) }))));
  $("#note-list").replaceChildren(...inv.notes.map((n) => h("li", {}, h("span", { class: "muted small", text: fmtDate(n.created_at) + " " }), n.text,
    h("button", { class: "btn ghost small", type: "button", text: "✕", "aria-label": "Delete note", onclick: () => deleteNote(n.id) }))));
}
async function removeTag(tag) {
  await api(`/api/investigations/${state.current.id}/tags/${encodeURIComponent(tag)}`, { method: "DELETE" });
  loadInvestigation(state.current.id);
}
async function deleteNote(id) {
  await api(`/api/investigations/${state.current.id}/notes/${id}`, { method: "DELETE" });
  loadInvestigation(state.current.id);
}

// ------------------------------------------------------------------ history
async function loadHistory() {
  const q = $("#history-search").value.trim();
  try {
    const data = await api("/api/investigations?limit=100" + (q ? "&q=" + encodeURIComponent(q) : ""));
    $("#history-list").replaceChildren(...data.investigations.map((inv) => h("li", { onclick: () => { toggleHistory(false); loadInvestigation(inv.id); } },
      h("div", { class: "row between" }, h("strong", { text: inv.title }), h("span", { class: "pill", text: inv.status })),
      h("div", { class: "small muted", text: `${fmtDate(inv.created_at)} · ${inv.reference_images} photo(s) · ${inv.candidate_count} candidate(s)` }),
      inv.top_candidate ? h("div", { class: "small", text: `Top: ${inv.top_candidate.name} (${pretty(inv.top_candidate.level)})` }) : null,
      inv.tags.length ? h("div", { class: "tags" }, inv.tags.map((t) => h("span", { class: "chip", text: t }))) : null,
      h("button", { class: "btn small danger", type: "button", text: "Delete", onclick: (e) => { e.stopPropagation(); deleteInvestigation(inv.id); } }))));
  } catch (e) { toast(e.message); }
}
async function deleteInvestigation(id) {
  if (!confirm("Delete this investigation and its stored data?")) return;
  try {
    await api(`/api/investigations/${id}`, { method: "DELETE" });
    if (state.current && state.current.id === id) newSearch();
    loadHistory();
  } catch (e) { toast(e.message); }
}
function toggleHistory(open) {
  const drawer = $("#history");
  drawer.hidden = !open;
  $("#history-btn").setAttribute("aria-expanded", String(open));
  if (open) loadHistory();
}

// ------------------------------------------------------------------ wiring
function init() {
  loadConfig();
  const dz = $("#dropzone");
  const input = $("#file-input");
  input.addEventListener("change", () => { handleFiles(input.files); input.value = ""; });
  dz.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
  ["dragenter", "dragover"].forEach((t) => dz.addEventListener(t, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((t) => dz.addEventListener(t, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
  dz.addEventListener("drop", (e) => handleFiles(e.dataTransfer.files));
  $("#start-btn").addEventListener("click", startSearch);
  $("#new-btn").addEventListener("click", newSearch);
  $("#history-btn").addEventListener("click", () => toggleHistory($("#history").hidden));
  $("#history-close").addEventListener("click", () => toggleHistory(false));
  $("#history-search").addEventListener("input", () => { clearTimeout(loadHistory._t); loadHistory._t = setTimeout(loadHistory, 250); });
  $("#about-btn").addEventListener("click", () => $("#about-dialog").showModal());
  $$("dialog [data-close]").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close()));
  $("#cd-close").addEventListener("click", () => $("#candidate-dialog").close());
  $$(".tab").forEach((tab) => tab.addEventListener("click", () => {
    $$(".tab").forEach((t) => t.classList.toggle("active", t === tab));
    $$(".tabpanel").forEach((p) => (p.hidden = p.id !== "tab-" + tab.dataset.tab));
  }));
  $$("[data-export]").forEach((b) => b.addEventListener("click", () => {
    if (state.current) window.location.href = `/api/investigations/${state.current.id}/export?format=${b.dataset.export}`;
  }));
  $("#tag-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const tag = e.target.tag.value.trim();
    if (!tag || !state.current) return;
    try {
      await api(`/api/investigations/${state.current.id}/tags`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tag }) });
      e.target.reset(); loadInvestigation(state.current.id);
    } catch (err) { toast(err.message); }
  });
  $("#note-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = e.target.text.value.trim();
    if (!text || !state.current) return;
    try {
      await api(`/api/investigations/${state.current.id}/notes`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
      e.target.reset(); loadInvestigation(state.current.id);
    } catch (err) { toast(err.message); }
  });
  const match = /inv=([a-f0-9]+)/.exec(location.hash);
  if (match) loadInvestigation(match[1]);
  // Remove the caching service worker registered by older versions.
  if (navigator.serviceWorker) navigator.serviceWorker.getRegistrations().then((rs) => rs.forEach((r) => r.unregister())).catch(() => {});
}
document.addEventListener("DOMContentLoaded", init);
