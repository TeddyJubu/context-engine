"use strict";

const API = "http://127.0.0.1:11811";
let startTs = null;

// ── Tab switching ────────────────────────────────────────────────────────────

document.querySelectorAll(".nav-item").forEach(item => {
  item.addEventListener("click", () => {
    document.querySelectorAll(".nav-item").forEach(n => n.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
    item.classList.add("active");
    document.getElementById("tab-" + item.dataset.tab).classList.add("active");
  });
});

// ── Health polling ───────────────────────────────────────────────────────────

async function pollHealth() {
  const dot    = document.getElementById("status-dot");
  const text   = document.getElementById("status-text");
  const banner = document.getElementById("reconnect-banner");
  try {
    const r = await fetch(API + "/health");
    if (!r.ok) throw new Error("HTTP " + r.status);
    const data = await r.json();
    if (!startTs) startTs = Date.now();
    dot.className = "dot green";
    text.textContent = "Running";
    banner.classList.remove("visible");
    document.getElementById("model-name").textContent = data.model || "-";
    refreshStats();
  } catch (_) {
    dot.className = "dot red";
    text.textContent = "Not responding";
    banner.classList.add("visible");
  }
  updateUptime();
}

function updateUptime() {
  const el = document.getElementById("uptime-text");
  if (!startTs) { el.textContent = ""; return; }
  const s   = Math.floor((Date.now() - startTs) / 1000);
  const h   = Math.floor(s / 3600);
  const m   = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  el.textContent = "up " + (h ? h + "h " : "") + (m ? m + "m " : "") + sec + "s";
}

setInterval(pollHealth, 5000);
setInterval(updateUptime, 1000);

// ── Collections ──────────────────────────────────────────────────────────────

async function refreshStats() {
  try {
    const r     = await fetch(API + "/collections");
    const colls = await r.json();
    const total = colls.reduce((sum, c) => sum + (c.doc_count > 0 ? c.doc_count : 0), 0);
    document.getElementById("stat-collections").textContent = colls.length;
    document.getElementById("stat-docs").textContent        = total;
    renderCollections(colls);
  } catch (_) { /* health poller handles the banner */ }
}

function renderCollections(colls) {
  const list = document.getElementById("coll-list");
  const sub  = document.getElementById("coll-subtitle");
  sub.textContent = colls.length + " collection" + (colls.length !== 1 ? "s" : "");

  // Build rows via DOM — never inject collection names as raw HTML
  list.replaceChildren();
  colls.forEach(c => {
    const row       = document.createElement("div");
    row.className   = "coll-row";

    const nameSpan  = document.createElement("span");
    nameSpan.className   = "coll-name";
    nameSpan.textContent = "📁 " + c.name;

    const right     = document.createElement("span");
    right.style.cssText = "display:flex;align-items:center;gap:10px;";

    const countSpan = document.createElement("span");
    countSpan.className   = "coll-count";
    countSpan.textContent = c.doc_count >= 0 ? c.doc_count + " docs" : "-";

    const delBtn    = document.createElement("button");
    delBtn.className   = "btn btn-danger";
    delBtn.textContent = "x";
    delBtn.addEventListener("click", () => App.deleteCollection(c.name));

    right.appendChild(countSpan);
    right.appendChild(delBtn);
    row.appendChild(nameSpan);
    row.appendChild(right);
    list.appendChild(row);
  });
}

document.getElementById("new-coll-btn").addEventListener("click", () => App.newCollection());

const App = {
  async newCollection() {
    const name = prompt("Collection name:");
    if (!name || !name.trim()) return;
    try {
      const r = await fetch(API + "/collections", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim() }),
      });
      if (!r.ok) throw new Error(await r.text());
      await refreshStats();
    } catch (e) {
      alert("Failed to create collection: " + e.message);
      await refreshStats();
    }
  },

  async deleteCollection(name) {
    if (!confirm('Delete collection "' + name + '"? This cannot be undone.')) return;
    try {
      const r = await fetch(API + "/collections/" + encodeURIComponent(name), { method: "DELETE" });
      if (!r.ok) throw new Error(await r.text());
      await refreshStats();
    } catch (e) {
      alert("Failed to delete: " + e.message);
      await refreshStats();
    }
  },
};

// ── Settings — Open at Login (pywebview bridge) ───────────────────────────────

function initSettings() {
  // window.pywebview.api is only safe to call after the pywebviewready event
  window.pywebview.api.get_open_at_login()
    .then(resp => {
      if (resp && typeof resp.enabled === "boolean") {
        document.getElementById("login-toggle").checked = resp.enabled;
      }
    })
    .catch(() => {
      // Bridge unavailable or Python side not ready — leave toggle in default state
    });

  document.getElementById("login-toggle").addEventListener("change", e => {
    window.pywebview.api.set_open_at_login(e.target.checked).catch(err => {
      alert("Could not update login item: " + err);
      e.target.checked = !e.target.checked;
    });
  });
}

window.addEventListener("pywebviewready", initSettings);

// ── Init ─────────────────────────────────────────────────────────────────────
pollHealth();
