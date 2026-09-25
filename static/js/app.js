/* BhuVerify prototype console.
 *
 * Vanilla JS + hash router, no build step and no external dependencies, so the
 * app runs unchanged inside the sandboxed preview iframe (which has no network
 * access) and on a restricted government intranet.
 */
(function () {
  "use strict";

  const API = "/api/v1";
  const state = {
    token: localStorage.getItem("bhuverify_token") || "",
    user: null,
    system: null,
    pending: [],
    route: { name: "dashboard", param: null },
  };

  // ---------------------------------------------------------------- utils
  const $ = (sel, root) => (root || document).querySelector(sel);
  const el = (tag, attrs, children) => {
    const node = document.createElement(tag);
    if (attrs) for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") node.className = v;
      else if (k === "html") node.innerHTML = v;
      else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
      else if (v !== null && v !== undefined) node.setAttribute(k, v);
    }
    (children || []).forEach((c) => {
      if (c === null || c === undefined || c === false) return;
      node.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
    });
    return node;
  };
  const esc = (s) => String(s === null || s === undefined ? "" : s);
  const num = (v, d) => (v === null || v === undefined || isNaN(v) ? (d === undefined ? "—" : d) : Number(v).toLocaleString("en-IN", { maximumFractionDigits: 2 }));
  const pct = (v, d) => (v === null || v === undefined ? (d || "—") : Number(v).toFixed(1) + "%");

  function toast(message, isError) {
    const node = el("div", { class: "toast" + (isError ? " err" : "") }, [message]);
    document.body.appendChild(node);
    setTimeout(() => node.remove(), 3400);
  }

  function timeAgo(iso) {
    if (!iso) return "—";
    const then = new Date(iso).getTime();
    if (isNaN(then)) return "—";
    const secs = Math.round((Date.now() - then) / 1000);
    if (secs < 60) return "just now";
    const mins = Math.round(secs / 60);
    if (mins < 60) return mins + "m ago";
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return hrs + "h ago";
    const days = Math.round(hrs / 24);
    if (days < 30) return days + "d ago";
    return new Date(iso).toLocaleDateString("en-IN");
  }
  function stamp(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return isNaN(d.getTime()) ? "—" : d.toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
  }

  async function api(path, options) {
    const opts = Object.assign({ headers: {} }, options || {});
    if (state.token) opts.headers["Authorization"] = "Bearer " + state.token;
    if (opts.body && !(opts.body instanceof FormData)) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    let res;
    try {
      res = await fetch(API + path, opts);
    } catch (err) {
      throw new Error("Network error contacting the API: " + err.message);
    }
    if (res.status === 401) { signOut(true); throw new Error("Session expired. Please sign in again."); }
    if (res.status === 204) return {};
    const text = await res.text();
    let data = {};
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (err) {
        // Proxy / gateway HTML pages (502/504 on long requests) are not JSON.
        // Surface the status, not a parser SyntaxError.
        throw new Error(
          "Server did not return JSON (HTTP " + res.status + "). " +
          (res.status >= 500
            ? "The request probably timed out upstream - try queue mode instead of synchronous processing."
            : "Unexpected response: " + text.slice(0, 120))
        );
      }
    }
    if (!res.ok) throw new Error(data.detail || ("HTTP " + res.status));
    return data;
  }

  function band(conf) {
    const v = Number(conf || 0);
    if (v >= 90) return "high";
    if (v >= 70) return "medium";
    return "low";
  }
  function confBadge(value) {
    const b = band(value);
    return el("span", { class: "conf" }, [
      el("span", { class: "conf-bar" }, [el("span", { class: "conf-fill " + b, style: "width:" + Math.max(2, Math.min(100, value || 0)) + "%" })]),
      el("span", { class: "small mono" }, [pct(value)]),
    ]);
  }
  function tag(text, cls) {
    return el("span", { class: "tag " + (cls || String(text).toLowerCase().replace(/[^a-z0-9_]/g, "_")) }, [esc(text)]);
  }
  function severityTag(sev) { return tag(sev || "none", sev || "neutral"); }
  function statusTag(s) { return tag(String(s || "").replace(/_/g, " "), s); }

  // ---------------------------------------------------------------- auth
  function renderLogin() {
    const root = $("#app");
    root.innerHTML = "";
    root.appendChild(document.importNode($("#tpl-login").content, true));

    $("#login-form").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const form = ev.target;
      const box = $("#login-error");
      box.hidden = true;
      try {
        const data = await api("/auth/login", {
          method: "POST",
          body: { username: form.username.value.trim(), password: form.password.value },
        });
        state.token = data.token;
        state.user = data.user;
        localStorage.setItem("bhuverify_token", data.token);
        // Mirror the token into a cookie: <img> tags (document previews)
        // cannot send Authorization headers, but the API also accepts the
        // bhuverify_token cookie (see security._extract_token).
        document.cookie = "bhuverify_token=" + data.token + "; Path=/; SameSite=Lax";
        await bootShell();
      } catch (err) {
        box.textContent = err.message;
        box.hidden = false;
      }
    });

    api("/auth/demo-accounts").then((data) => {
      const grid = $("#demo-grid");
      if (!grid) return;
      (data.accounts || []).forEach((acc) => {
        grid.appendChild(el("button", {
          class: "demo-chip", type: "button",
          onclick: () => {
            $("#login-form").username.value = acc.username;
            $("#login-form").password.value = acc.password;
            $("#login-form").requestSubmit();
          },
        }, [el("b", {}, [acc.username]), el("span", {}, [acc.role.replace(/_/g, " ")])]));
      });
    }).catch(() => {});

    api("/health", {}).catch(() => null);
    fetch(API + "/system/status").then((r) => (r.ok ? r.json() : null)).then((data) => {
      const note = $("#engine-note");
      if (!note || !data) return;
      const ocr = data.ocr || {};
      note.innerHTML =
        "OCR engine: <b>" + esc(ocr.tesseract_available ? "Tesseract " + esc(ocr.tesseract_version) : "unavailable") +
        "</b> · languages <b>" + esc((ocr.installed_languages || []).join(", ")) +
        "</b><br>Cadastral layer: <b>" + esc((data.gis || {}).feature_count || 0) +
        "</b> polygons · cross-database adapters: <b>" + esc((data.cross_db || {}).mode || "?") + "</b>";
    }).catch(() => {});
  }

  function signOut(silent) {
    if (state.token && !silent) api("/auth/logout", { method: "POST" }).catch(() => {});
    state.token = "";
    state.user = null;
    localStorage.removeItem("bhuverify_token");
    document.cookie = "bhuverify_token=; Path=/; Max-Age=0";
    renderLogin();
    if (!silent) toast("Signed out.");
  }

  // ---------------------------------------------------------------- shell
  const NAV = [
    { name: "dashboard", label: "MIS Dashboard", glyph: "\u25A6", perm: "dashboard:read" },
    { name: "upload", label: "Upload Records", glyph: "\u2B06", perm: "documents:upload" },
    { name: "queue", label: "Processing Queue", glyph: "\u2261", perm: "documents:read" },
    { name: "review", label: "Review Queue", glyph: "\u2713", perm: "records:read" },
    { name: "records", label: "Record Search", glyph: "\u2315", perm: "search:use" },
    { name: "map", label: "Cadastral Map", glyph: "\u25C8", perm: "map:read" },
    { name: "rules", label: "Validation Rules", glyph: "\u2696", perm: "records:read" },
    { name: "learning", label: "Correction Data", glyph: "\u21BB", perm: "dashboard:read" },
    { name: "audit", label: "Audit Trail", glyph: "\u2637", perm: "audit:read" },
    { name: "system", label: "System & Users", glyph: "\u2699", perm: "system:read" },
  ];

  function allowed(item) {
    if (!state.user) return false;
    const perms = state.user.permissions || [];
    return !item.perm || perms.indexOf(item.perm) !== -1 || perms.indexOf("records:read") !== -1 && item.name === "review";
  }

  async function bootShell() {
    try {
      state.user = await api("/auth/me");
    } catch (err) {
      state.token = "";
      localStorage.removeItem("bhuverify_token");
      document.cookie = "bhuverify_token=; Path=/; Max-Age=0";
      renderLogin();
      return;
    }
    const root = $("#app");
    root.innerHTML = "";
    root.appendChild(document.importNode($("#tpl-shell").content, true));

    $("#user-name").textContent = state.user.full_name;
    $("#user-role").textContent = state.user.role_label;
    $("#user-avatar").textContent = (state.user.full_name || "?").split(" ").map((p) => p[0]).slice(0, 2).join("");
    $("#scope-note").textContent = "Scope: " + (state.user.district_scope || "—") + " · role " + state.user.role;
    $("#logout-btn").addEventListener("click", () => signOut(false));
    $("#drawer-close").addEventListener("click", () => { $("#drawer").hidden = true; });
    $("#notif-btn").addEventListener("click", openNotifications);

    const nav = $("#nav");
    nav.innerHTML = "";
    NAV.filter(allowed).forEach((item) => {
      const btn = el("button", {
        class: "nav-item", "data-route": item.name,
        onclick: () => { location.hash = "#/" + item.name; },
      }, [el("span", { class: "glyph" }, [item.glyph]), el("span", {}, [item.label])]);
      if (item.name === "review") btn.appendChild(el("span", { class: "count", id: "nav-pending" }, ["0"]));
      nav.appendChild(btn);
    });

    try {
      state.system = await api("/system/status");
      const s = state.system;
      $("#sys-pill").textContent =
        "Tesseract " + (s.ocr.tesseract_version || "—") + " · " +
        (s.ocr.installed_languages || []).join("/") + " · " +
        (s.gis.feature_count || 0) + " polygons";
      $("#sys-pill").title = JSON.stringify(s.performance_budgets);
    } catch (err) { /* pill is decorative */ }

    window.addEventListener("hashchange", route);
    route();
    refreshCounts();
    setInterval(refreshCounts, 15000);
  }

  async function refreshCounts() {
    if (!state.user) return;
    try {
      const data = await api("/records/review-queue?limit=200");
      state.pending = data.queue || [];
      const node = $("#nav-pending");
      if (node) node.textContent = String(state.pending.length);
    } catch (err) { /* ignore */ }
  }

  const VIEWS = {
    dashboard: viewDashboard,
    upload: viewUpload,
    queue: viewQueue,
    // "#/review" lists the queue; "#/review/<record_id>" opens the record.
    review: (param) => (param ? viewRecordDetail(param) : viewReviewList()),
    records: viewRecords,
    map: viewMap,
    rules: viewRules,
    learning: viewLearning,
    audit: viewAudit,
    system: viewSystem,
  };

  function route() {
    const hash = location.hash.replace(/^#\/?/, "");
    const [name, param] = hash.split("/");
    const target = name && VIEWS[name] && NAV.some((n) => n.name === name && allowed(n)) ? name : "dashboard";
    state.route = { name: target, param: param ? decodeURIComponent(param) : null };
    if (state.refreshTimer) { clearInterval(state.refreshTimer); state.refreshTimer = null; }

    document.querySelectorAll(".nav-item").forEach((b) => {
      b.classList.toggle("active", b.getAttribute("data-route") === target);
    });
    const content = $("#content");
    content.innerHTML = "";
    content.appendChild(el("div", { class: "empty" }, ["Loading…"]));
    Promise.resolve(VIEWS[target](state.route.param))
      .then((node) => { content.innerHTML = ""; content.appendChild(node); })
      .catch((err) => {
        content.innerHTML = "";
        content.appendChild(errorPanel(err));
      });
  }

  function errorPanel(err) {
    return el("div", { class: "panel" }, [
      el("div", { class: "panel-body" }, [
        el("h3", {}, ["Something went wrong"]),
        el("p", { class: "muted" }, [err.message || String(err)]),
        el("div", { class: "row", style: "margin-top:12px" }, [
          el("button", { class: "btn", onclick: () => route() }, ["Retry"]),
        ]),
      ]),
    ]);
  }

  function panel(title, sub, bodyNodes, actions) {
    return el("div", { class: "panel" }, [
      el("div", { class: "panel-head" }, [
        el("div", {}, [
          el("div", { class: "panel-title" }, [title]),
          sub ? el("div", { class: "panel-sub", html: sub }) : null,
        ]),
        actions ? el("div", { class: "row" }, actions) : null,
      ]),
      el("div", { class: "panel-body" }, bodyNodes),
    ]);
  }

  function statCard(label, value, foot, tone) {
    return el("div", { class: "panel stat" + (tone ? " is-" + tone : "") }, [
      el("div", { class: "stat-label" }, [label]),
      el("div", { class: "stat-value" }, [esc(value)]),
      foot ? el("div", { class: "stat-foot" }, [foot]) : null,
    ]);
  }

  function barRow(label, value, max, cls) {
    const width = max > 0 ? Math.max(2, (value / max) * 100) : 0;
    return el("div", { class: "bar-row" }, [
      el("div", { class: "bar-label", title: label }, [label]),
      el("div", { class: "bar-track" }, [el("div", { class: "bar-fill " + (cls || ""), style: "width:" + width + "%" })]),
      el("div", { class: "bar-val" }, [num(value)]),
    ]);
  }

  // ------------------------------------------------------- MIS dashboard
  async function viewDashboard() {
    const [data, alerts] = await Promise.all([
      api("/dashboard"),
      api("/dashboard/alerts").catch(() => ({ pending_beyond_threshold: [] })),
    ]);
    $("#page-title").textContent = "MIS Dashboard";
    $("#page-sub").textContent =
      "Digitization and validation progress · " + data.scope.district + ", " + data.scope.state + " (prototype scope)";

    const wrap = el("div", { class: "grid" });

    wrap.appendChild(el("div", { class: "grid cols-4" }, [
      statCard("Documents uploaded", num(data.documents.uploaded), num(data.documents.processed) + " processed", null),
      statCard("Pending verification", num(data.records.pending_review), num(data.records.escalated) + " escalated", data.records.pending_review ? "warn" : "good"),
      statCard("Approved records", num(data.records.approved), pct(data.progress.review_completion_pct) + " review completion", "good"),
      statCard("Open discrepancies", num(data.validation.discrepancies_open), num(data.validation.high_and_critical) + " high/critical", data.validation.high_and_critical ? "bad" : "good"),
    ]));

    wrap.appendChild(el("div", { class: "grid cols-4" }, [
      statCard("Avg extraction confidence", pct(data.extraction.average_record_confidence),
        num(data.extraction.low_confidence_records) + " record(s) below 70%",
        data.extraction.average_record_confidence >= 80 ? "good" : "warn"),
      statCard("GIS-linked records", num(data.extraction.gis_linked_records), pct(data.extraction.gis_link_rate_pct) + " of records", null),
      statCard("Avg pipeline latency", (data.performance.average_pipeline_latency_ms / 1000).toFixed(2) + " s",
        "budget " + (data.performance.budget_ms / 1000) + " s per document",
        data.performance.within_budget ? "good" : "bad"),
      statCard("Corrections captured", num(data.learning.corrections_captured),
        num(data.learning.distinct_fields_corrected) + " distinct fields", null),
    ]));

    // rule-wise discrepancies
    const ruleCounts = {};
    (data.validation.rule_wise || []).forEach((r) => {
      ruleCounts[r.rule_id] = (ruleCounts[r.rule_id] || 0) + r.count;
    });
    const ruleEntries = Object.entries(ruleCounts).sort((a, b) => b[1] - a[1]);
    const maxRule = ruleEntries.length ? ruleEntries[0][1] : 0;
    const rulesBody = ruleEntries.length
      ? el("div", {}, ruleEntries.map(([rule, count]) =>
          barRow(rule, count, maxRule, rule === "BR-3" ? "bad" : "")))
      : el("div", { class: "empty" }, ["No discrepancies recorded yet."]);

    wrap.appendChild(el("div", { class: "grid cols-2" }, [
      panel("Discrepancies by business rule",
        "Every finding names the rule that produced it, so reviewers see the cause rather than a bare flag.",
        [rulesBody]),
      panel("Digitization progress by tehsil",
        "Prototype scope is single-district (" + esc(data.scope.district) + "); the pilot tier adds state-wise rollups.",
        [
          (data.progress.by_tehsil || []).length
            ? el("div", {}, data.progress.by_tehsil.slice(0, 10).map((t) =>
                barRow(t.tehsil, t.records, Math.max(...data.progress.by_tehsil.map((x) => x.records)))))
            : el("div", { class: "empty" }, ["No records yet."]),
        ]),
    ]));

    // severity split + document status
    const sev = data.validation.by_severity || {};
    const statuses = data.documents.by_status || {};
    wrap.appendChild(el("div", { class: "grid cols-3" }, [
      panel("Findings by severity", null, [
        el("div", {}, [
          barRow("critical", sev.critical || 0, Math.max(1, sev.critical || 0, sev.high || 0), "bad"),
          barRow("high", sev.high || 0, Math.max(1, sev.critical || 0, sev.high || 0), "bad"),
          barRow("medium", sev.medium || 0, Math.max(1, sev.medium || 0), "warn"),
          barRow("low", sev.low || 0, Math.max(1, sev.low || 0), ""),
        ]),
      ]),
      panel("Document pipeline status", null, [
        el("div", {}, Object.keys(statuses).length
          ? Object.entries(statuses).map(([k, v]) => barRow(k.replace(/_/g, " "), v, Math.max(...Object.values(statuses))))
          : [el("div", { class: "empty" }, ["No documents yet."])]),
      ]),
      panel("Records needing attention",
        alerts.pending_beyond_threshold.length
          ? "Pending longer than " + alerts.threshold_hours + " hours."
          : "Nothing overdue.",
        [
          alerts.pending_beyond_threshold.length
            ? el("div", {}, alerts.pending_beyond_threshold.slice(0, 6).map((a) =>
                el("div", { class: "check" }, [
                  el("span", { class: "check-sys" }, [a.record_id]),
                  el("span", { class: "check-detail" }, ["khasra " + a.khasra_no + " · " + a.village + " · " + a.hours_pending + "h pending"]),
                ])))
            : el("div", { class: "empty" }, ["Review queue is current."]),
        ]),
    ]));

    wrap.appendChild(panel("How to read this screen", null, [
      el("p", { class: "small muted" }, [
        "BhuVerify automates digitization and validation but never approves a record by itself. ",
        "Every figure above is computed live from the repository - upload a new document and the counts move. ",
        "Records reach the verified repository only after a reviewer approves them, and every AI output and human ",
        "correction is written to an immutable, hash-chained audit trail (see the Audit Trail tab).",
      ]),
    ]));

    return wrap;
  }

  // ------------------------------------------------------------- upload
  async function viewUpload() {
    $("#page-title").textContent = "Upload Records";
    $("#page-sub").textContent = "Bulk ingest scanned registers, PDFs and cadastral map images (FR-1).";

    let files = [];
    const list = el("div", { class: "file-list" });
    const drop = el("div", { class: "drop" }, [
      el("b", {}, ["Drop scanned land records here"]),
      el("span", {}, ["PDF, JPEG, PNG, TIFF or BMP · up to 25 MB each · duplicates are skipped by SHA-256 hash"]),
      el("div", { style: "margin-top:12px" }, [
        el("button", { class: "btn", type: "button", onclick: () => fileInput.click() }, ["Choose files"]),
      ]),
    ]);
    const fileInput = el("input", { type: "file", multiple: "", accept: ".pdf,.jpg,.jpeg,.png,.tif,.tiff,.bmp", class: "sr-only" });
    fileInput.addEventListener("change", () => { addFiles(Array.from(fileInput.files || [])); fileInput.value = ""; });

    ["dragenter", "dragover"].forEach((evt) => drop.addEventListener(evt, (e) => { e.preventDefault(); drop.classList.add("over"); }));
    ["dragleave", "drop"].forEach((evt) => drop.addEventListener(evt, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
    drop.addEventListener("drop", (e) => addFiles(Array.from((e.dataTransfer || {}).files || [])));

    function addFiles(incoming) {
      incoming.forEach((f) => { if (!files.some((x) => x.name === f.name && x.size === f.size)) files.push(f); });
      renderList();
    }
    function renderList() {
      list.innerHTML = "";
      files.forEach((f, index) => {
        list.appendChild(el("div", { class: "file-row" }, [
          el("span", { class: "name" }, [f.name]),
          el("span", { class: "size" }, [(f.size / 1024).toFixed(0) + " KB"]),
          el("button", { class: "btn btn-sm rm", onclick: () => { files.splice(index, 1); renderList(); } }, ["Remove"]),
        ]));
      });
    }

    const langSel = el("select", {}, [
      ["eng+hin+ori", "Auto - English + Hindi + Odia (recommended)"],
      ["eng", "English only"],
      ["hin", "Hindi only"],
      ["ori", "Odia only"],
    ].map(([v, label]) => el("option", { value: v }, [label])));
    const typeSel = el("select", {}, ["ror", "register_page", "mutation_record", "cadastral_map"].map((t) => el("option", { value: t }, [t])));
    const syncChk = el("input", { type: "checkbox", style: "width:auto;margin:0" });
    const batchInput = el("input", { placeholder: "e.g. Balarampur batch 1" });
    const result = el("div", {});

    const submit = el("button", { class: "btn btn-primary", onclick: run }, ["Process documents"]);

    async function run() {
      if (!files.length) return toast("Choose at least one file first.", true);
      const form = new FormData();
      files.forEach((f) => form.append("files", f));
      form.append("language", langSel.value);
      form.append("doc_type", typeSel.value);
      form.append("batch_label", batchInput.value || "");
      form.append("process_sync", syncChk.checked ? "true" : "false");

      submit.disabled = true;
      submit.textContent = "Processing…";
      result.innerHTML = "";
      try {
        const data = await api("/documents", { method: "POST", body: form });
        files = [];
        renderList();
        toast("Batch " + data.batch_id + ": " + data.accepted.length + " accepted, " + data.skipped_duplicates.length + " duplicate(s) skipped.");
        if (data.processing_mode === "queued") {
          // async path: take the user to the live queue so they watch it finish
          location.hash = "#/queue";
          return;
        }
        result.appendChild(uploadResult(data));
        refreshCounts();
      } catch (err) {
        result.appendChild(el("div", { class: "login-error" }, [err.message]));
      } finally {
        submit.disabled = false;
        submit.textContent = "Process documents";
      }
    }

    return el("div", { class: "grid" }, [
      panel("New batch upload", null, [
        drop, fileInput, list,
        el("div", { class: "upload-opts" }, [
          el("label", {}, ["Document language", langSel]),
          el("label", {}, ["Document type", typeSel]),
          el("label", {}, ["Batch label", batchInput]),
          el("label", { style: "min-width:auto;display:flex;gap:8px;align-items:center" }, [syncChk, "process synchronously"]),
          submit,
        ]),
        el("p", { class: "small muted", style: "margin-top:12px" }, [
          "Queue mode (recommended, especially on hosted deployments) processes in the background - ",
          "watch progress on the Processing Queue page. Synchronous mode runs the full pipeline inside ",
          "the upload request and can exceed the hosting proxy timeout on large/slow documents.",
        ]),
      ]),
      result,
      await recentBatches(),
    ]);
  }

  function uploadResult(data) {
    const rows = (data.documents || []).map((d) => el("tr", { class: "clickable", onclick: () => { location.hash = "#/queue/" + d.doc_id; } }, [
      el("td", { class: "mono" }, [d.doc_id]),
      el("td", {}, [d.original_filename]),
      el("td", {}, [statusTag(d.status)]),
      el("td", { class: "num" }, [pct(d.ocr_mean_confidence)]),
      el("td", { class: "num" }, [d.record_id ? d.record_id : "—"]),
    ]));
    return panel("Batch " + data.batch_id,
      data.accepted.length + " accepted · " + data.skipped_duplicates.length + " duplicate(s) skipped · mode " + data.processing_mode,
      [
        rows.length
          ? el("table", {}, [
              el("thead", {}, [el("tr", {}, ["Document", "File", "Status", "OCR conf.", "Record"].map((h) => el("th", {}, [h])))]),
              el("tbody", {}, rows),
            ])
          : el("div", { class: "empty" }, ["Every file in this batch was a duplicate of an existing document."]),
        (data.skipped_duplicates || []).length
          ? el("div", { class: "small muted", style: "margin-top:10px" }, [
              "Skipped as duplicates: " + data.skipped_duplicates.map((s) => s.filename + " = " + s.existing_doc_id).join("; "),
            ])
          : null,
      ]);
  }

  async function recentBatches() {
    const data = await api("/documents/batches");
    const rows = (data.batches || []).slice(0, 8).map((b) => el("tr", {}, [
      el("td", { class: "mono" }, [b.batch_id]),
      el("td", {}, [b.label]),
      el("td", { class: "num" }, [num(b.file_count)]),
      el("td", { class: "num" }, [num(b.skipped_duplicates)]),
      el("td", {}, Array.from(b.statuses || []).map((s) => statusTag(s))),
      el("td", {}, [timeAgo(b.created_at)]),
    ]));
    return panel("Recent batches", null, [
      rows.length
        ? el("table", {}, [
            el("thead", {}, [el("tr", {}, ["Batch", "Label", "Files", "Dupes", "Status", "Uploaded"].map((h) => el("th", {}, [h])))]),
            el("tbody", {}, rows),
          ])
        : el("div", { class: "empty" }, ["No batches yet."]),
    ]);
  }

  // ------------------------------------------------------ processing queue
  async function viewQueue(docId) {
    $("#page-title").textContent = "Processing Queue";
    $("#page-sub").textContent = "Live pipeline state for every ingested document (FR-1, FR-2, FR-3).";

    const [docs, queue] = await Promise.all([api("/documents?limit=60"), api("/documents/queue")]);

    if (docId) {
      const doc = await api("/documents/" + docId);
      return el("div", { class: "grid" }, [documentDetail(doc), await recentDocsTable(docs)]);
    }

    // live refresh while anything is still moving through the pipeline
    const inFlight = (docs.documents || []).some((d) => (
      ["queued", "preprocessing", "ocr_running", "extracting", "validating"].indexOf(d.status) !== -1
    ));
    if (inFlight && !state.refreshTimer) {
      state.refreshTimer = setInterval(() => {
        if ((location.hash || "#/queue") === "#/queue") route();
      }, 5000);
    }

    return el("div", { class: "grid" }, [
      el("div", { class: "grid cols-4" }, [
        statCard("Queued", num(queue.queue_length), queue.worker_alive ? "worker running" : "worker stopped", queue.worker_alive ? "good" : "warn"),
        statCard("Completed", num((docs.documents || []).filter((d) => d.status === "completed").length), null, "good"),
        statCard("Needs review", num((docs.documents || []).filter((d) => d.status === "needs_review").length), null, "warn"),
        statCard("Failed", num((docs.documents || []).filter((d) => d.status === "failed").length), null, null),
      ]),
      await recentDocsTable(docs),
    ]);
  }

  async function recentDocsTable(docs) {
    const rows = (docs.documents || []).map((d) => el("tr", {
      class: "clickable", onclick: () => { location.hash = "#/queue/" + d.doc_id; },
    }, [
      el("td", { class: "mono" }, [d.doc_id]),
      el("td", {}, [d.original_filename]),
      el("td", {}, [statusTag(d.status)]),
      el("td", {}, [el("div", { class: "progress", style: "width:90px" }, [el("div", { style: "width:" + d.progress_pct + "%" })])]),
      el("td", { class: "num" }, [pct(d.ocr_mean_confidence)]),
      el("td", { class: "num" }, [d.total_latency_ms ? (d.total_latency_ms / 1000).toFixed(1) + "s" : "—"]),
      el("td", {}, [timeAgo(d.created_at)]),
    ]));
    return panel("Documents", (docs.documents || []).length + " in repository", [
      rows.length
        ? el("table", {}, [
            el("thead", {}, [el("tr", {}, ["Document", "File", "Status", "Progress", "OCR", "Pipeline", "Uploaded"].map((h) => el("th", {}, [h])))]),
            el("tbody", {}, rows),
          ])
        : el("div", { class: "empty" }, ["No documents uploaded yet."]),
    ]);
  }

  function documentDetail(doc) {
    const stats = doc.preprocessing_stats || {};
    const layout = (doc.layout || {}).counts || {};
    const tabs = el("div", { class: "doc-tabs" });
    const frame = el("div", {});
    const variants = [
      ["original", "Original scan"],
      ["enhanced", "CV enhanced"],
      ["preview", "Detected regions"],
    ];
    function show(variant) {
      Array.from(tabs.children).forEach((c, i) => c.classList.toggle("active", variants[i][0] === variant));
      frame.innerHTML = "";
      frame.appendChild(el("img", {
        src: API + "/documents/" + doc.doc_id + "/media/" + variant,
        alt: variant + " view of " + doc.original_filename,
        onerror: (e) => { e.target.replaceWith(el("div", { class: "empty" }, ["This variant is not available yet."])); },
      }));
    }
    variants.forEach(([key, label]) => tabs.appendChild(el("button", { class: "doc-tab", onclick: () => show(key) }, [label])));
    show(doc.enhanced_path || doc.preview_path ? "enhanced" : "original");

    return el("div", { class: "grid cols-2" }, [
      panel(doc.original_filename, doc.doc_id + " · " + doc.doc_type + " · language " + doc.ocr_language, [
        el("div", { class: "doc-viewer" }, [tabs, frame]),
      ]),
      panel("Pipeline result", null, [
        el("dl", { class: "kv" }, [
          el("dt", {}, ["Status"]), el("dd", {}, [statusTag(doc.status)]),
          el("dt", {}, ["OCR engine"]), el("dd", {}, [doc.ocr_engine || "—"]),
          el("dt", {}, ["OCR confidence"]), el("dd", {}, [confBadge(doc.ocr_mean_confidence)]),
          el("dt", {}, ["Words read"]), el("dd", {}, [num(doc.ocr_word_count)]),
          el("dt", {}, ["OCR time"]), el("dd", {}, [num(doc.ocr_latency_ms) + " ms"]),
          el("dt", {}, ["Total pipeline"]), el("dd", {}, [num(doc.total_latency_ms) + " ms (budget 10,000 ms)"]),
          el("dt", {}, ["Deskew applied"]), el("dd", {}, [stats.deskew_angle_deg ? stats.deskew_angle_deg + "°" : "none needed"]),
          el("dt", {}, ["Contrast gain"]), el("dd", {}, [pct(stats.contrast_gain_pct, "0%") + " (CLAHE)"]),
          el("dt", {}, ["Regions detected"]), el("dd", {}, [
            Object.keys(layout).length
              ? el("div", { class: "pill-row" }, Object.entries(layout).filter(([, v]) => v).map(([k, v]) => tag(k + " × " + v, "neutral")))
              : "none",
          ]),
          el("dt", {}, ["Record"]), el("dd", {}, [
            doc.record_id
              ? el("a", { href: "#/review/" + doc.record_id, class: "mono" }, [doc.record_id])
              : "not created",
          ]),
        ]),
        el("h4", { style: "margin:16px 0 6px" }, ["Raw OCR text"]),
        el("pre", { class: "mono small", style: "background:#fbfcfd;border:1px solid var(--line-2);border-radius:8px;padding:10px;white-space:pre-wrap;max-height:220px;overflow:auto" }, [doc.ocr_text || "(none)"]),
        (doc.audit_trail || []).length ? timeline(doc.audit_trail.slice(0, 10)) : null,
      ], reprocessActions(doc)),
    ]);
  }

  function reprocessActions(doc) {
    // Same-file retry without re-uploading: reruns the pipeline on the
    // stored bytes (picks up extractor fixes). Upload of identical bytes
    // is still dedup-skipped unless the previous attempt died.
    if ((state.user.permissions || []).indexOf("documents:upload") === -1) return [];
    return [el("button", {
      class: "btn",
      onclick: async (ev) => {
        const btn = ev.target;
        btn.disabled = true;
        btn.textContent = "Reprocessing…";
        try {
          await api("/documents/" + doc.doc_id + "/process", { method: "POST" });
          toast("Reprocessing started - watch the queue.");
          route();
        } catch (err) {
          toast(err.message, true);
          btn.disabled = false;
          btn.textContent = "Reprocess";
        }
      },
    }, ["Reprocess"])];
  }

  function timeline(entries) {
    return el("div", { style: "margin-top:16px" }, [
      el("h4", { style: "margin-bottom:8px" }, ["Audit trail"]),
      el("div", { class: "timeline" }, (entries || []).map((e) => el("div", { class: "tl-item" }, [
        el("div", { class: "tl-head" }, [
          el("span", { class: "tl-action" }, [e.action.replace(/_/g, " ")]),
          el("span", { class: "tl-actor" }, [e.actor]),
          el("span", { class: "tl-time" }, [stamp(e.timestamp)]),
        ]),
        e.detail ? el("div", { class: "tl-detail" }, [e.detail]) : null,
        (e.old_value || e.new_value)
          ? el("div", { class: "tl-diff" }, [
              e.old_value ? el("span", { class: "tl-old" }, [e.field_name ? e.field_name + ": " : "", e.old_value]) : null,
              e.new_value ? el("span", { class: "tl-new" }, [e.field_name ? e.field_name + " → " : "", e.new_value]) : null,
            ])
          : null,
      ]))),
    ]);
  }

  // -------------------------------------------------------- review queue
  async function viewReviewList() {
    $("#page-title").textContent = "Review Queue";
    $("#page-sub").textContent = "Records awaiting human verification, worst findings first (FR-10).";

    const data = await api("/records/review-queue?limit=100");
    const rows = (data.queue || []).map((r) => el("tr", {
      class: "clickable", onclick: () => { location.hash = "#/review/" + r.record_id; },
    }, [
      el("td", { class: "mono" }, [r.record_id]),
      el("td", {}, [r.owner_name || "—"]),
      el("td", {}, ["khasra " + (r.khasra_no || "—")]),
      el("td", {}, [r.village || "—"]),
      el("td", {}, [r.tehsil || "—"]),
      el("td", { class: "num" }, [r.area_hectare ? r.area_hectare.toFixed(4) + " ha" : "—"]),
      el("td", {}, [confBadge(r.record_confidence)]),
      el("td", {}, [severityTag(r.highest_severity)]),
      el("td", { class: "num" }, [num(r.discrepancy_count)]),
      el("td", {}, [r.gis_linked ? tag("GIS linked", "ok") : tag("no polygon", "neutral")]),
    ]));

    return el("div", { class: "grid" }, [
      panel((data.count || 0) + " record(s) pending",
        "Approval is always a human decision. Records with a critical finding cannot be approved until it is resolved.",
        [
          rows.length
            ? el("table", {}, [
                el("thead", {}, [el("tr", {}, ["Record", "Owner", "Plot", "Village", "Tehsil", "Area", "Confidence", "Severity", "Findings", "GIS"].map((h) => el("th", {}, [h])))]),
                el("tbody", {}, rows),
              ])
            : el("div", { class: "empty" }, ["The review queue is empty. Upload a document to populate it."]),
        ]),
    ]);
  }

  async function viewRecordDetail(recordId) {
    const record = await api("/records/" + recordId);
    $("#page-title").textContent = "Record " + record.record_id;
    $("#page-sub").textContent =
      (record.owner_name || "Unknown owner") + " · khasra " + (record.khasra_no || "—") +
      " · " + (record.village || "—") + ", " + (record.tehsil || "—");

    const wrap = el("div", { class: "grid" });

    wrap.appendChild(el("div", { class: "grid cols-4" }, [
      statCard("Record confidence", pct(record.record_confidence),
        (record.fields || []).filter((f) => f.is_low_confidence).length + " low-confidence field(s)",
        record.record_confidence >= 80 ? "good" : "warn"),
      statCard("Status", String(record.status).replace(/_/g, " "), record.document_type || "—", null),
      statCard("Findings", num(record.discrepancy_count), record.highest_severity ? "worst: " + record.highest_severity : "none",
        record.discrepancy_count ? "bad" : "good"),
      statCard("Cadastral link", record.gis_linked ? "linked" : "not linked",
        record.geometry ? "Δ area " + record.geometry.area_delta_pct + "%" : "no polygon in sample layer", null),
    ]));

    // left: document image, right: fields + findings
    const docId = record.doc_id;
    const tabs = el("div", { class: "doc-tabs" });
    const frame = el("div", {});
    const variants = [["original", "Source scan"], ["enhanced", "Enhanced"], ["preview", "Regions"]];
    function show(variant) {
      Array.from(tabs.children).forEach((c, i) => c.classList.toggle("active", variants[i][0] === variant));
      frame.innerHTML = "";
      frame.appendChild(el("img", {
        src: API + "/documents/" + docId + "/media/" + variant, alt: variant,
        onerror: (e) => e.target.replaceWith(el("div", { class: "empty" }, ["Variant unavailable."])),
      }));
    }
    variants.forEach(([key, label]) => tabs.appendChild(el("button", { class: "doc-tab", onclick: () => show(key) }, [label])));
    show("original");

    const fieldsNode = fieldEditor(record);
    const canEdit = (state.user.permissions || []).indexOf("records:correct") !== -1;
    const canVerify = (state.user.permissions || []).indexOf("records:verify") !== -1;

    const actions = [];
    if (canEdit) {
      actions.push(el("button", {
        class: "btn", onclick: () => { saveCorrections(record, fieldsNode); },
      }, ["Save corrections"]));
    }
    if (canVerify) {
      actions.push(el("button", { class: "btn btn-primary", onclick: () => verify(record, "approve") }, ["Approve"]));
      actions.push(el("button", { class: "btn btn-warn", onclick: () => verify(record, "escalate") }, ["Escalate"]));
      actions.push(el("button", { class: "btn btn-danger", onclick: () => verify(record, "reject") }, ["Reject"]));
    }

    wrap.appendChild(el("div", { class: "review-split" }, [
      panel("Source document", docId + " · original always shown beside the extracted values", [
        el("div", { class: "doc-viewer" }, [tabs, frame]),
        record.geometry ? el("div", { style: "margin-top:12px" }, [miniMap(record.geometry, null)]) : null,
      ]),
      el("div", { class: "grid" }, [
        panel("Extracted fields",
          "Green = reviewer corrected · amber = below 70% confidence and needs a human eye.",
          [fieldsNode], actions),
        findingsPanel(record),
        crossDbPanel(record),
        docSpecificPanel(record),
      ]),
    ]));

    wrap.appendChild(el("div", { class: "grid cols-2" }, [
      panel("Audit history for this record", "Every AI output and every human correction, hash-chained.", [
        timeline(record.audit_trail || []),
      ]),
      panel("Duplicate scan", "Repository-wide check for conflicting identifiers and near-duplicate owner names (FR-7).", [
        (record.duplicates || []).length
          ? el("div", {}, record.duplicates.map((d) => el("div", { class: "check" }, [
              el("span", { class: "check-sys" }, [d.identifier]),
              el("div", {}, [
                el("div", { class: "check-detail" }, [
                  d.type.replace(/_/g, " ") + " · " + (d.owner || "—") + " · " + d.record_id,
                ]),
                el("div", { class: "check-ref" }, [
                  "similarity " + (d.similarity !== undefined ? pct(d.similarity * 100) : "—") + " · severity " + d.severity,
                ]),
              ]),
            ])))
          : el("div", { class: "empty" }, ["No duplicate or near-duplicate records found."]),
      ]),
    ]));

    return wrap;
  }

  function docSpecificPanel(record) {
    // Odisha Form 39-A style identifiers, person list and directional
    // boundary. Renders nothing when the document type carries none.
    const rows = [];
    [["Khewat No.", record.khewat_no], ["Khatiyan No.", record.khatiyan_no],
     ["Tehsil No.", record.tehsil_no]].forEach(([label, val]) => {
      if (val) rows.push(el("div", { class: "check" }, [
        el("span", { class: "check-sys" }, [label]),
        el("div", {}, [el("div", { class: "check-detail mono" }, [val])]),
      ]));
    });
    (record.owners || []).forEach((o, i) => {
      rows.push(el("div", { class: "check" }, [
        el("span", { class: "check-sys" }, ["Person " + (i + 1)]),
        el("div", {}, [
          el("div", { class: "check-detail" }, [o.name || "—"]),
          el("div", { class: "check-ref" }, [
            o.relation_type ? o.relation_type + ": " + (o.relation_name || "—") : "relation not stated",
          ]),
        ]),
      ]));
    });
    const boundary = record.boundary || {};
    ["north", "south", "east", "west"].forEach((dir) => {
      if (boundary[dir]) rows.push(el("div", { class: "check" }, [
        el("span", { class: "check-sys" }, ["Boundary " + dir]),
        el("div", {}, [el("div", { class: "check-detail" }, [boundary[dir]])]),
      ]));
    });
    if (!rows.length) return null;
    return panel("Document-specific fields",
      "Identifiers and person list from the source form (never mapped into Khata/Khasra).",
      [el("div", {}, rows)]);
  }

  function fieldEditor(record) {    const canEdit = (state.user.permissions || []).indexOf("records:correct") !== -1;
    const box = el("div", {});
    (record.fields || []).forEach((f) => {
      // Empty rows are rendered too (flagged "missing"): a field the AI
      // failed to read is exactly the one a reviewer must type in.
      const missing = !f.value && !f.normalized_value && !f.corrected_value;
      const input = el("input", { value: f.corrected_value || f.normalized_value || f.value || "", "data-field": f.field_name });
      if (!canEdit) input.setAttribute("disabled", "disabled");
      if (missing) input.setAttribute("placeholder", "missing — type value here");
      const cls = f.corrected_value ? "corrected" : (missing ? "missing" : (f.is_low_confidence ? "low" : ""));
      box.appendChild(el("div", { class: "field-row " + cls }, [
        el("div", { class: "field-label" }, [f.label || f.field_name]),
        el("div", { class: "field-value" }, [input]),
        el("div", { class: "field-meta" }, [
          el("span", { class: "field-src", title: f.evidence_text || "" }, [missing ? "missing" : f.source]),
          missing ? el("span", { class: "tag medium" }, ["needs value"]) : confBadge(f.confidence),
        ]),
      ]));
    });
    return box;
  }

  async function saveCorrections(record, fieldsNode) {
    const corrections = {};
    fieldsNode.querySelectorAll("input[data-field]").forEach((input) => {
      const name = input.getAttribute("data-field");
      const original = (record.fields.find((f) => f.field_name === name) || {});
      const was = String(original.normalized_value || original.value || "");
      if (String(input.value).trim() !== was) corrections[name] = String(input.value).trim();
    });
    if (!Object.keys(corrections).length) return toast("No changes to save.");
    try {
      await api("/records/" + record.record_id, { method: "PATCH", body: { corrections, comment: "Reviewer verification" } });
      toast("Saved " + Object.keys(corrections).length + " correction(s) to the training dataset.");
      refreshCounts();
      route();
    } catch (err) { toast(err.message, true); }
  }

  async function verify(record, decision) {
    const comment = window.prompt(
      decision === "approve" ? "Approve this record? Add an optional note."
        : decision === "reject" ? "Reason for rejection:" : "Reason for escalation:",
      "",
    );
    if (comment === null) return;
    try {
      await api("/records/" + record.record_id + "/verify", { method: "POST", body: { decision, comment } });
      toast("Record " + decision + "d.");
      refreshCounts();
      location.hash = "#/review";
    } catch (err) { toast(err.message, true); }
  }

  function findingsPanel(record) {
    const items = (record.discrepancies || []).map((d) => el("div", { class: "disc sev-" + d.severity }, [
      el("div", { class: "disc-head" }, [
        el("span", { class: "disc-rule" }, [d.rule_id]),
        el("span", { class: "disc-name" }, [d.rule_name]),
        severityTag(d.severity),
        tag(d.status.replace(/_/g, " "), "neutral"),
      ]),
      el("p", { class: "disc-msg" }, [d.message]),
      el("div", { class: "disc-detail" }, [
        d.expected ? el("div", {}, [el("b", {}, ["Expected"]), esc(d.expected)]) : null,
        d.actual ? el("div", {}, [el("b", {}, ["Actual"]), esc(d.actual)]) : null,
      ].filter(Boolean)),
      d.recommended_action ? el("div", { class: "disc-action" }, ["Suggested action: " + d.recommended_action]) : null,
    ]));
    return panel("Validation findings",
      "Named business rules BR-1 to BR-10, each with severity, conflicting values and evidence references.",
      items.length ? items : [el("div", { class: "empty" }, ["No discrepancies. All ten business rules passed."])]);
  }

  function crossDbPanel(record) {
    const rows = (record.cross_db_checks || []).map((c) => el("div", { class: "check" }, [
      el("span", { class: "check-sys" }, [c.source_system]),
      el("div", {}, [
        el("div", { class: "row" }, [tag(c.match_status.replace(/_/g, " "), c.match_status), tag(c.mode, "neutral")]),
        el("div", { class: "check-detail", style: "margin-top:4px" }, [c.detail]),
        el("div", { class: "check-ref mono" }, [c.external_reference]),
      ]),
    ]));
    return panel("Cross-database verification",
      "Prototype adapters are deterministic mocks with the same contract as the live government APIs (FR-8).",
      rows.length ? rows : [el("div", { class: "empty" }, ["No cross-database checks recorded."])]);
  }

  // ------------------------------------------------------- record search
  async function viewRecords(query) {
    $("#page-title").textContent = "Record Search";
    $("#page-sub").textContent = "Search digitized records by owner, plot identifier or administrative unit (FR-11).";

    const searchInput = el("input", { placeholder: "Owner, khasra, khata, village, tehsil, record id…", value: query || "" });
    const statusSel = el("select", {}, [
      ["", "any status"], ["pending_review", "pending review"], ["approved", "approved"],
      ["rejected", "rejected"], ["escalated", "escalated"],
    ].map(([v, l]) => el("option", { value: v }, [l])));
    const ruleSel = el("select", {}, [["", "any rule"]].concat(
      ["BR-1", "BR-2", "BR-3", "BR-4", "BR-5", "BR-6", "BR-7", "BR-8", "BR-9", "BR-10"].map((r) => [r, r]))
      .map(([v, l]) => el("option", { value: v }, [l])));
    const lowChk = el("input", { type: "checkbox", style: "width:auto;margin:0" });
    const results = el("div", {});

    async function run() {
      const params = new URLSearchParams();
      if (searchInput.value.trim()) params.set("q", searchInput.value.trim());
      if (statusSel.value) params.set("status", statusSel.value);
      if (ruleSel.value) params.set("discrepancy_type", ruleSel.value);
      if (lowChk.checked) params.set("low_confidence_only", "true");
      params.set("limit", "100");
      results.innerHTML = "";
      results.appendChild(el("div", { class: "empty" }, ["Searching…"]));
      try {
        const data = await api("/records?" + params.toString());
        results.innerHTML = "";
        results.appendChild(searchResults(data));
      } catch (err) {
        results.innerHTML = "";
        results.appendChild(el("div", { class: "login-error" }, [err.message]));
      }
    }

    searchInput.addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });

    const filters = panel("Search", null, [
      el("div", { class: "filters" }, [
        el("label", { style: "flex:1;min-width:240px" }, ["Free text", searchInput]),
        el("label", {}, ["Status", statusSel]),
        el("label", {}, ["Failed rule", ruleSel]),
        el("label", { style: "min-width:auto;display:flex;gap:8px;align-items:center" }, [lowChk, "low confidence only"]),
        el("button", { class: "btn btn-primary", onclick: run }, ["Search"]),
      ]),
    ]);

    await run();
    return el("div", { class: "grid" }, [filters, results]);
  }

  function searchResults(data) {
    const rows = (data.records || []).map((r) => el("tr", {
      class: "clickable", onclick: () => { location.hash = "#/review/" + r.record_id; },
    }, [
      el("td", { class: "mono" }, [r.record_id]),
      el("td", {}, [r.owner_name || "—"]),
      el("td", {}, [(r.khasra_no || "—") + " / khata " + (r.khata_no || "—")]),
      el("td", {}, [(r.village || "—") + ", " + (r.tehsil || "—")]),
      el("td", { class: "num" }, [r.area_hectare ? r.area_hectare.toFixed(4) + " ha" : "—"]),
      el("td", {}, [r.land_classification || "—"]),
      el("td", {}, [confBadge(r.record_confidence)]),
      el("td", {}, [statusTag(r.status)]),
      el("td", {}, [r.highest_severity ? severityTag(r.highest_severity) : tag("clean", "ok")]),
    ]));
    return panel((data.count || 0) + " result(s)",
      "returned in " + num(data.elapsed_ms) + " ms · budget " + num(data.budget_ms) + " ms",
      rows.length
        ? el("table", {}, [
            el("thead", {}, [el("tr", {}, ["Record", "Owner", "Plot", "Location", "Area", "Classification", "Confidence", "Status", "Findings"].map((h) => el("th", {}, [h])))]),
            el("tbody", {}, rows),
          ])
        : el("div", { class: "empty" }, ["No records matched those filters."]),
    );
  }

  // ------------------------------------------------------------ the map
  async function viewMap(plotKey) {
    $("#page-title").textContent = "Cadastral Map";
    $("#page-sub").textContent = "Text records linked to cadastral polygons by khasra / survey number (FR-9).";

    const [layer, records] = await Promise.all([
      api("/map/layer"),
      api("/records?limit=200"),
    ]);

    const linkedByPlot = {};
    (records.records || []).forEach((r) => {
      if (r.gis_linked) linkedByPlot[(r.khasra_no || "").trim()] = r;
    });

    const mismatches = (records.records || []).filter((r) => r.gis_linked && r.highest_severity === "high");
    const detail = await plotDetail(plotKey, linkedByPlot);
    const wrap = el("div", { class: "grid" }, [
      el("div", { class: "grid cols-4" }, [
        statCard("Polygons in layer", num(layer.summary.feature_count), layer.summary.crs, null),
        statCard("Indexed identifiers", num(layer.summary.indexed_keys), "khasra / survey / plot", null),
        statCard("Linked records", num((records.records || []).filter((r) => r.gis_linked).length),
          "of " + (records.count || 0) + " digitized", "good"),
        statCard("Villages covered", num((layer.summary.villages || []).length), (layer.summary.villages || []).slice(0, 3).join(", "), null),
      ]),
      panel("Sample cadastral layer",
        "Click a plot to see the linked record. Red outlines mark plots whose recorded area disagrees with the polygon (BR-9).",
        [el("div", { class: "map-wrap" }, [
          svgMap(layer.features || [], linkedByPlot, plotKey),
          el("div", { class: "map-legend" }, [
            el("div", {}, [el("span", { class: "map-swatch", style: "background:#cfe0d8" }), "cadastral plot"]),
            el("div", {}, [el("span", { class: "map-swatch", style: "background:#14795f" }), "selected / linked record"]),
            el("div", {}, [el("span", { class: "map-swatch", style: "background:#f0c2cc" }), "area mismatch (BR-9)"]),
          ]),
        ])]),
      detail,
    ]);
    return wrap;
  }

  function svgMap(features, linkedByPlot, activeKey) {
    const W = 900, H = 460, pad = 26;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    features.forEach((f) => {
      ((f.geometry || {}).coordinates || [[]])[0].forEach(([x, y]) => {
        minX = Math.min(minX, x); maxX = Math.max(maxX, x);
        minY = Math.min(minY, y); maxY = Math.max(maxY, y);
      });
    });
    if (!isFinite(minX)) return el("div", { class: "empty" }, ["No geometry in the layer."]);
    const spanX = Math.max(maxX - minX, 1e-6), spanY = Math.max(maxY - minY, 1e-6);
    const scale = Math.min((W - pad * 2) / spanX, (H - pad * 2) / spanY);
    const proj = (x, y) => [pad + (x - minX) * scale, H - pad - (y - minY) * scale];

    const svgNS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    svg.setAttribute("role", "img");

    // graticule so the map reads as a cadastral sheet
    for (let i = 0; i <= 6; i++) {
      const line = document.createElementNS(svgNS, "line");
      const x = pad + ((W - pad * 2) / 6) * i;
      line.setAttribute("x1", x); line.setAttribute("y1", pad / 2);
      line.setAttribute("x2", x); line.setAttribute("y2", H - pad / 2);
      line.setAttribute("stroke", "#dde6e2"); line.setAttribute("stroke-width", "1");
      svg.appendChild(line);
    }

    features.forEach((f) => {
      const props = f.properties || {};
      const ring = ((f.geometry || {}).coordinates || [[]])[0];
      const points = ring.map(([x, y]) => proj(x, y).map((v) => v.toFixed(1)).join(",")).join(" ");
      const record = linkedByPlot[String(props.khasra_no || "").trim()];
      const poly = document.createElementNS(svgNS, "polygon");
      poly.setAttribute("points", points);
      let cls = "plot";
      if (props.plot_key === activeKey) cls += " active";
      else if (record && record.highest_severity === "high") cls += " mismatch";
      poly.setAttribute("class", cls);
      const title = document.createElementNS(svgNS, "title");
      title.textContent =
        props.plot_key + " · khasra " + props.khasra_no + " · " + props.area_ha + " ha\n" +
        (record ? "Record " + record.record_id + " · " + record.owner_name + " · " + record.status.replace(/_/g, " ")
                : "No digitized record linked");
      poly.appendChild(title);
      poly.addEventListener("click", () => { location.hash = "#/map/" + encodeURIComponent(props.plot_key); });
      svg.appendChild(poly);

      const cx = ring.reduce((s, p) => s + proj(p[0], p[1])[0], 0) / ring.length;
      const cy = ring.reduce((s, p) => s + proj(p[0], p[1])[1], 0) / ring.length;
      const label = document.createElementNS(svgNS, "text");
      label.setAttribute("x", cx); label.setAttribute("y", cy);
      label.setAttribute("text-anchor", "middle");
      label.setAttribute("class", "plot-label");
      label.textContent = props.khasra_no;
      svg.appendChild(label);
    });
    return svg;
  }

  async function plotDetail(plotKey, linkedByPlot) {
    if (!plotKey) {
      return panel("Plot detail", "Select a polygon on the map.", [
        el("div", { class: "empty" }, ["No plot selected."]),
      ]);
    }
    let data;
    try {
      data = await api("/map/plot/" + encodeURIComponent(plotKey) + "/geometry");
    } catch (err) {
      return panel("Plot detail", null, [el("div", { class: "login-error" }, [err.message])]);
    }
    const p = data.properties || {};
    return panel("Plot " + plotKey, "matched from the cadastral layer · area computed from the polygon", [
      el("dl", { class: "kv" }, [
        el("dt", {}, ["Khasra / survey"]), el("dd", {}, [(p.khasra_no || "—") + " / " + (p.survey_no || "—")]),
        el("dt", {}, ["Village, tehsil"]), el("dd", {}, [(p.village || "—") + ", " + (p.tehsil || "—")]),
        el("dt", {}, ["Polygon area"]), el("dd", {}, [data.computed_area_ha.toFixed(4) + " ha (computed from geometry)"]),
        el("dt", {}, ["Layer area"]), el("dd", {}, [(p.area_ha || "—") + " ha (as published)"]),
        el("dt", {}, ["Map sheet"]), el("dd", {}, [p.map_sheet || "—"]),
        el("dt", {}, ["Linked record"]), el("dd", {}, [
          data.linked_record
            ? el("a", { href: "#/review/" + data.linked_record.record_id, class: "mono" }, [
                data.linked_record.record_id + " · " + data.linked_record.owner_name,
              ])
            : el("span", { class: "muted" }, ["none"]),
        ]),
      ]),
      el("h4", { style: "margin:14px 0 6px" }, ["Neighbouring plots (within 400 m)"]),
      (data.neighbours || []).length
        ? el("div", {}, data.neighbours.map((n) => el("div", { class: "check" }, [
            el("span", { class: "check-sys" }, [n.plot_key]),
            el("span", { class: "check-detail" }, ["khasra " + n.khasra_no + " · " + (n.owner || "—") + " · " + n.distance_m + " m"]),
          ])))
        : el("div", { class: "empty" }, ["No neighbouring plots indexed."]),
    ]);
  }

  function miniMap(geometry, activeKey) {
    const features = [{ geometry: geometry, properties: { plot_key: activeKey, khasra_no: "" } }];
    return el("div", {}, [
      el("h4", { style: "margin-bottom:6px" }, ["Linked cadastral polygon"]),
      el("div", { class: "map-wrap" }, [svgMap(features, {}, activeKey)]),
    ]);
  }

  // ------------------------------------------------------------ rules
  async function viewRules() {
    $("#page-title").textContent = "Validation Rules";
    $("#page-sub").textContent = "The ten business rules, with a live dry-run against an ad-hoc record (FR-6).";

    const [meta, counts] = await Promise.all([
      api("/records/meta/rules"),
      api("/dashboard").catch(() => ({ validation: { rule_wise: [] } })),
    ]);
    const countsByRule = {};
    ((counts.validation || {}).rule_wise || []).forEach((r) => {
      countsByRule[r.rule_id] = (countsByRule[r.rule_id] || 0) + r.count;
    });

    const cards = (meta.rules || []).map((r) => el("div", { class: "rule-card" }, [
      el("div", { class: "row" }, [
        el("b", {}, [r.id]),
        tag(r.default_severity, r.default_severity),
        el("span", { class: "spacer" }),
        tag((countsByRule[r.id] || 0) + " finding(s)", countsByRule[r.id] ? "medium" : "ok"),
      ]),
      el("p", {}, [r.name + " — " + r.purpose]),
    ]));

    // dry-run form
    const fields = {
      owner_name: "Prafulla Kumar Sahoo", khasra_no: "118/2", khata_no: "204",
      village: "Balarampur", tehsil: "Khordha Sadar", district: "Khordha",
      area: "1.14 acre", land_classification: "Irrigated land",
      mutation_date: "12/03/2019", registration_no: "1234 of 2019",
      previous_owner: "Basanta Kumar Sahoo", new_owner: "Prafulla Kumar Sahoo",
    };
    const inputs = {};
    const formBody = el("div", { class: "grid cols-2" }, Object.keys(fields).map((k) => {
      const input = el("input", { value: fields[k] });
      inputs[k] = input;
      return el("label", { class: "small" }, [k.replace(/_/g, " "), input]);
    }));
    const out = el("div", {});

    async function run() {
      const payload = {};
      Object.keys(inputs).forEach((k) => { payload[k] = { value: inputs[k].value, normalized_value: inputs[k].value, confidence: 85 }; });
      out.innerHTML = "";
      out.appendChild(el("div", { class: "empty" }, ["Running rules…"]));
      try {
        const data = await api("/records/validate", { method: "POST", body: { fields: payload } });
        out.innerHTML = "";
        out.appendChild(panel("Dry-run result",
          "Executed in " + num(data.total_latency_ms) + " ms · passed: " + data.passed.join(", ") + " · failed: " + (data.failed.join(", ") || "none"),
          [
            el("div", { class: "row", style: "margin-bottom:10px" }, [
              tag("worst: " + (data.highest_severity || "none"), data.highest_severity || "ok"),
            ]),
            (data.discrepancies || []).length
              ? el("div", {}, data.discrepancies.map((d) => el("div", { class: "disc sev-" + d.severity }, [
                  el("div", { class: "disc-head" }, [
                    el("span", { class: "disc-rule" }, [d.rule_id]),
                    el("span", { class: "disc-name" }, [d.rule_name]),
                    severityTag(d.severity),
                  ]),
                  el("p", { class: "disc-msg" }, [d.message]),
                ])))
              : el("div", { class: "empty" }, ["All rules passed for this input."]),
            el("h4", { style: "margin:14px 0 6px" }, ["Rule-by-rule runtime"]),
            el("table", {}, [
              el("thead", {}, [el("tr", {}, ["Rule", "Name", "Result", "Findings", "ms"].map((h) => el("th", {}, [h])))]),
              el("tbody", {}, data.rule_runtime.map((r) => el("tr", {}, [
                el("td", { class: "mono" }, [r.rule_id]),
                el("td", {}, [r.rule_name]),
                el("td", {}, [r.passed ? tag("pass", "ok") : tag("fail", "high")]),
                el("td", { class: "num" }, [num(r.findings)]),
                el("td", { class: "num" }, [num(r.latency_ms, 0)]),
              ]))),
            ]),
          ]));
      } catch (err) {
        out.innerHTML = "";
        out.appendChild(el("div", { class: "login-error" }, [err.message]));
      }
    }

    return el("div", { class: "grid" }, [
      panel("Business rule catalogue",
        "Rules are declarative and independently testable; each finding names its rule so a reviewer can act on the cause.",
        [el("div", { class: "grid cols-2" }, cards)]),
      panel("Rule dry-run",
        "Runs BR-1 to BR-10 against an ad-hoc field set without persisting anything - the same endpoint integrating systems call.",
        [formBody, el("div", { class: "row", style: "margin-top:12px" }, [
          el("button", { class: "btn btn-primary", onclick: run }, ["Run validation rules"]),
        ])]),
      out,
    ]);
  }

  // --------------------------------------------------------- learning
  async function viewLearning() {
    $("#page-title").textContent = "Correction Data";
    $("#page-sub").textContent = "Reviewer corrections captured as labeled training data for the next fine-tuning run (FR-16).";

    const data = await api("/records/meta/corrections");
    const perField = Object.entries(data.per_field || {});
    const maxCount = perField.length ? Math.max(...perField.map(([, v]) => v)) : 0;

    const rows = (data.samples || []).map((c) => el("tr", {}, [
      el("td", {}, [c.field_name.replace(/_/g, " ")]),
      el("td", {}, [el("span", { class: "tl-old" }, [c.ai_value || "(empty)"])]),
      el("td", {}, [el("span", { class: "tl-new" }, [c.corrected_value])]),
      el("td", { class: "num" }, [pct(c.ai_confidence)]),
      el("td", {}, [c.language]),
      el("td", { class: "mono" }, [c.source_ref]),
      el("td", {}, [timeAgo(c.created_at)]),
    ]));

    return el("div", { class: "grid" }, [
      el("div", { class: "grid cols-3" }, [
        statCard("Corrections captured", num(data.count), "labeled AI → human pairs", "good"),
        statCard("Distinct fields", num(perField.length), perField.map(([k]) => k).slice(0, 3).join(", ") || "—", null),
        statCard("Next step", "fine-tune", "OCR/NER refresh on this dataset", null),
      ]),
      panel("Corrections per field", null, [
        perField.length
          ? el("div", {}, perField.sort((a, b) => b[1] - a[1]).map(([k, v]) => barRow(k.replace(/_/g, " "), v, maxCount)))
          : el("div", { class: "empty" }, ["No corrections captured yet. Correct a field in the review screen to create one."]),
      ]),
      panel("Labeled dataset", data.usage, [
        rows.length
          ? el("table", {}, [
              el("thead", {}, [el("tr", {}, ["Field", "AI value", "Human value", "AI conf.", "Lang", "Source", "When"].map((h) => el("th", {}, [h])))]),
              el("tbody", {}, rows),
            ])
          : el("div", { class: "empty" }, ["Empty."]),
      ]),
    ]);
  }

  // ------------------------------------------------------------ audit
  async function viewAudit() {
    $("#page-title").textContent = "Audit Trail";
    $("#page-sub").textContent = "Append-only, hash-chained log of every AI decision and human action (FR-12).";

    const [stats, entries] = await Promise.all([
      api("/audit/stats").catch(() => null),
      api("/audit?limit=120"),
    ]);

    const chain = (stats || {}).chain || {};
    const byAction = Object.entries((stats || {}).by_action || {}).sort((a, b) => b[1] - a[1]);
    const maxAction = byAction.length ? byAction[0][1] : 0;

    const rows = (entries.entries || []).map((e) => el("tr", {}, [
      el("td", { class: "mono" }, [e.log_id]),
      el("td", {}, [e.action.replace(/_/g, " ")]),
      el("td", {}, [e.actor]),
      el("td", { class: "mono" }, [e.entity_id || "—"]),
      el("td", {}, [e.field_name || "—"]),
      el("td", {}, [
        e.old_value || e.new_value
          ? el("div", { class: "tl-diff" }, [
              e.old_value ? el("span", { class: "tl-old" }, [e.old_value]) : null,
              e.new_value ? el("span", { class: "tl-new" }, [e.new_value]) : null,
            ])
          : (e.detail || "—"),
      ]),
      el("td", { class: "num" }, [e.ai_confidence ? pct(e.ai_confidence) : "—"]),
      el("td", {}, [stamp(e.timestamp)]),
    ]));

    return el("div", { class: "grid" }, [
      el("div", { class: "grid cols-4" }, [
        statCard("Audit events", num((stats || {}).total_events), "100% of reviewer actions", "good"),
        statCard("Corrections logged", num((stats || {}).corrections_logged), "old → new value retained", null),
        statCard("Chain integrity", chain.chain_intact ? "intact" : "BROKEN",
          chain.entries_checked + " entries verified", chain.chain_intact ? "good" : "bad"),
        statCard("Head hash", (chain.head_hash || "").slice(0, 10) + "…", "SHA-256 chain", null),
      ]),
      el("div", { class: "grid cols-2" }, [
        panel("Events by action type", null, [
          byAction.length
            ? el("div", {}, byAction.map(([k, v]) => barRow(k.replace(/_/g, " "), v, maxAction)))
            : el("div", { class: "empty" }, ["No events yet."]),
        ]),
        panel("Tamper evidence", null, [
          el("p", { class: "small" }, [
            "Each entry stores the SHA-256 of the previous entry, so altering any historical row breaks the chain. ",
            "The verification pass recomputes every hash on demand.",
          ]),
          el("div", { class: "hash", style: "margin-top:8px" }, ["head: " + (chain.head_hash || "—")]),
          el("div", { class: "row", style: "margin-top:10px" }, [
            el("button", {
              class: "btn", onclick: async () => {
                const result = await api("/audit/verify");
                toast(result.chain_intact
                  ? "Chain intact across " + result.entries_checked + " entries."
                  : "Chain BROKEN at entry " + result.broken_at_log_id, !result.chain_intact);
              },
            }, ["Re-verify chain now"]),
          ]),
        ]),
      ]),
      panel("Audit log", (entries.count || 0) + " most recent entries", [
        rows.length
          ? el("table", {}, [
              el("thead", {}, [el("tr", {}, ["Log ID", "Action", "Actor", "Entity", "Field", "Change", "AI conf.", "When"].map((h) => el("th", {}, [h])))]),
              el("tbody", {}, rows),
            ])
          : el("div", { class: "empty" }, ["No audit events yet."]),
      ]),
    ]);
  }

  // ----------------------------------------------------------- system
  async function viewSystem() {
    $("#page-title").textContent = "System & Users";
    $("#page-sub").textContent = "Runtime engines, integration adapters, roles and demo controls (FR-14, FR-17).";

    const [status, users, roles] = await Promise.all([
      api("/system/status"),
      api("/system/users").catch(() => ({ users: [] })),
      api("/auth/roles"),
    ]);

    const wrap = el("div", { class: "grid" });
    wrap.appendChild(el("div", { class: "grid cols-4" }, [
      statCard("Documents", num(status.counts.documents), "in repository", null),
      statCard("Records", num(status.counts.records), num(status.counts.discrepancies) + " findings", null),
      statCard("Audit events", num(status.counts.audit_events), "immutable log", "good"),
      statCard("Worker", status.queue.worker_alive ? "running" : "stopped", status.queue.queue_length + " queued",
        status.queue.worker_alive ? "good" : "warn"),
    ]));

    wrap.appendChild(el("div", { class: "grid cols-3" }, [
      panel("OCR / HTR engine", null, [
        el("dl", { class: "kv" }, [
          el("dt", {}, ["Tesseract"]), el("dd", {}, [status.ocr.tesseract ? status.ocr.tesseract_version : "unavailable"]),
          el("dt", {}, ["Installed langs"]), el("dd", {}, [(status.ocr.installed_languages || []).join(", ") || "—"]),
          el("dt", {}, ["Enabled langs"]), el("dd", {}, [(status.ocr.enabled_languages || []).join(", ")]),
          el("dt", {}, ["HTR pipeline"]), el("dd", {}, [status.ocr.htr_pipeline]),
          el("dt", {}, ["Upgrade path"]), el("dd", {}, [status.ocr.pilot_upgrade_path]),
        ]),
      ]),
      panel("GIS layer", null, [
        el("dl", { class: "kv" }, [
          el("dt", {}, ["File"]), el("dd", {}, [status.gis.layer_file]),
          el("dt", {}, ["Features"]), el("dd", {}, [num(status.gis.feature_count)]),
          el("dt", {}, ["Indexed keys"]), el("dd", {}, [num(status.gis.indexed_keys)]),
          el("dt", {}, ["CRS"]), el("dd", {}, [status.gis.crs]),
          el("dt", {}, ["Area method"]), el("dd", {}, [status.gis.area_projection]),
        ]),
      ]),
      panel("Confidence & budgets", null, [
        el("dl", { class: "kv" }, [
          el("dt", {}, ["High from"]), el("dd", {}, [pct(status.confidence_bands.high_from)]),
          el("dt", {}, ["Medium from"]), el("dd", {}, [pct(status.confidence_bands.medium_from)]),
          el("dt", {}, ["Low below"]), el("dd", {}, [pct(status.confidence_bands.low_below) + " → human review"]),
          el("dt", {}, ["OCR budget"]), el("dd", {}, [status.performance_budgets.ocr_extraction_s + " s / document"]),
          el("dt", {}, ["Validation budget"]), el("dd", {}, [status.performance_budgets.validation_s + " s / record"]),
          el("dt", {}, ["Search budget"]), el("dd", {}, [status.performance_budgets.search_s + " s"]),
        ]),
      ]),
    ]));

    wrap.appendChild(panel("Cross-database adapters",
      esc((status.cross_db || {}).note || ""),
      [el("table", {}, [
        el("thead", {}, [el("tr", {}, ["Adapter", "Purpose", "Mode"].map((h) => el("th", {}, [h])))]),
        el("tbody", {}, ((status.cross_db || {}).adapters || []).map((a) => el("tr", {}, [
          el("td", { class: "mono" }, [a.name]),
          el("td", {}, [a.purpose]),
          el("td", {}, [tag(a.mode, a.mode === "mock" ? "medium" : "ok")]),
        ]))),
      ])]));

    wrap.appendChild(el("div", { class: "grid cols-2" }, [
      panel("Users and roles", (users.users || []).length + " seeded accounts", [
        el("table", {}, [
          el("thead", {}, [el("tr", {}, ["User", "Name", "Role", "Scope"].map((h) => el("th", {}, [h])))]),
          el("tbody", {}, (users.users || []).map((u) => el("tr", {}, [
            el("td", { class: "mono" }, [u.username]),
            el("td", {}, [u.full_name]),
            el("td", {}, [u.role_label]),
            el("td", {}, [u.district_scope || "—"]),
          ]))),
        ]),
      ]),
      panel("Role permissions (RBAC)", "Prototype uses seeded accounts; production swaps in department SSO + MFA.", [
        el("div", { class: "grid" }, (roles.roles || []).map((r) => el("div", { class: "rule-card" }, [
          el("b", {}, [r.label]),
          el("div", { class: "pill-row", style: "margin-top:6px" }, (r.permissions || []).map((p) => tag(p, "neutral"))),
        ]))),
      ]),
    ]));

    const canReseed = (state.user.permissions || []).indexOf("system:reseed") !== -1;
    wrap.appendChild(panel("Demo controls", null, [
      el("div", { class: "row" }, [
        el("a", { class: "btn", href: "/docs", target: "_blank" }, ["OpenAPI documentation"]),
        el("a", { class: "btn", href: API + "/dashboard/export.csv", target: "_blank" }, ["Export MIS CSV"]),
        canReseed
          ? el("button", {
              class: "btn btn-danger", onclick: async () => {
                if (!window.confirm("Wipe all data and rebuild the demo dataset? This re-runs the full pipeline on all six sample documents.")) return;
                toast("Reseeding…");
                try {
                  const result = await api("/system/reseed", { method: "POST" });
                  toast("Reseeded " + (result.report.documents || []).length + " documents.");
                  refreshCounts();
                  route();
                } catch (err) { toast(err.message, true); }
              },
            }, ["Reseed demo dataset"])
          : null,
      ]),
      el("p", { class: "small muted", style: "margin-top:10px" }, [
        "Reseeding drops the SQLite database, regenerates the sample documents and cadastral layer, and re-runs ",
        "preprocessing, OCR/HTR, extraction, validation and GIS linking for each document.",
      ]),
    ]));

    return wrap;
  }

  // ------------------------------------------------------ notifications
  async function openNotifications() {
    const drawer = $("#drawer");
    const body = $("#drawer-body");
    drawer.hidden = false;
    body.innerHTML = "";
    body.appendChild(el("div", { class: "empty" }, ["Loading…"]));
    try {
      const data = await api("/system/notifications");
      body.innerHTML = "";
      if (!(data.notifications || []).length) {
        body.appendChild(el("div", { class: "empty" }, ["No notifications."]));
        return;
      }
      data.notifications.forEach((n) => {
        body.appendChild(el("div", { class: "notif " + n.status }, [
          el("div", { class: "row" }, [tag(n.level, n.level === "high" ? "high" : "neutral")]),
          el("h4", {}, [n.title]),
          el("p", {}, [n.message]),
          el("div", { class: "row", style: "margin-top:6px" }, [
            el("span", { class: "when" }, [timeAgo(n.created_at)]),
            el("span", { class: "spacer" }),
            n.status === "unread"
              ? el("button", {
                  class: "btn btn-sm", onclick: async () => {
                    await api("/system/notifications/" + n.id + "/read", { method: "POST" });
                    openNotifications(); refreshBadge();
                  },
                }, ["Mark read"])
              : null,
            n.link ? el("a", { class: "btn btn-sm", href: n.link }, ["Open"]) : null,
          ]),
        ]));
      });
    } catch (err) {
      body.innerHTML = "";
      body.appendChild(el("div", { class: "login-error" }, [err.message]));
    }
  }

  async function refreshBadge() {
    try {
      const data = await api("/system/notifications");
      const badge = $("#notif-badge");
      if (!badge) return;
      badge.textContent = String(data.unread || 0);
      badge.hidden = !data.unread;
    } catch (err) { /* ignore */ }
  }

  // ------------------------------------------------------------- boot
  async function boot() {
    if (state.token) { await bootShell(); refreshBadge(); }
    else renderLogin();
  }
  boot();
})();
