// Context Engine — Popup Script

const API = "http://localhost:11811";

const statusBadge = document.getElementById("status-badge");
const statusLabel = document.getElementById("status-label");
const offlineBanner = document.getElementById("offline-banner");
const retryBtn = document.getElementById("retry-btn");
const collSelect = document.getElementById("collection-select");
const collectionRow = document.getElementById("collection-row");
const emptyState = document.getElementById("empty-state");
const newCollBtn = document.getElementById("new-collection-btn");
const newCollForm = document.getElementById("new-collection-form");
const newCollInput = document.getElementById("new-collection-input");
const collInputError = document.getElementById("coll-input-error");
const createCollBtn = document.getElementById("create-collection-btn");
const addPageBtn = document.getElementById("add-page-btn");
const addSelBtn = document.getElementById("add-selection-btn");
const crawlUrl = document.getElementById("crawl-url");
const crawlUrlError = document.getElementById("crawl-url-error");
const crawlMax = document.getElementById("crawl-max");
const crawlBtn = document.getElementById("crawl-btn");
const crawlProgress = document.getElementById("crawl-progress");
const crawlProgressText = document.getElementById("crawl-progress-text");
const crawlProgressPct = document.getElementById("crawl-progress-pct");
const crawlProgressBar = document.getElementById("crawl-progress-bar");
const toastContainer = document.getElementById("toast-container");
const cardCollection = document.getElementById("card-collection");
const cardActions = document.getElementById("card-actions");
const cardCrawl = document.getElementById("card-crawl");

let serverOnline = false;

// ===== SVG Icon Helpers =====

const icons = {
  check: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>',
  x: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
  warn: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
};

// ===== Toast System =====

function showMessage(text, type = "success") {
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;

  const iconEl = document.createElement("span");
  iconEl.className = "toast-icon";
  iconEl.innerHTML = type === "success" ? icons.check : icons.warn;

  const textEl = document.createElement("span");
  textEl.className = "toast-text";
  textEl.textContent = text;

  toast.appendChild(iconEl);
  toast.appendChild(textEl);

  if (type === "error") {
    const closeBtn = document.createElement("button");
    closeBtn.className = "toast-close";
    closeBtn.innerHTML = icons.x;
    closeBtn.addEventListener("click", () => dismissToast(toast));
    toast.appendChild(closeBtn);
  }

  toastContainer.appendChild(toast);

  if (type === "success") {
    setTimeout(() => dismissToast(toast), 4000);
  }
}

function dismissToast(toast) {
  if (!toast.parentNode) return;
  toast.classList.add("toast-out");
  toast.addEventListener("animationend", () => toast.remove());
}

// ===== Server Status =====

function setOnline(online) {
  serverOnline = online;
  if (online) {
    statusBadge.className = "status-badge status-online";
    statusLabel.textContent = "Connected";
    offlineBanner.classList.add("hidden");
    cardCollection.classList.remove("disabled-card");
    cardActions.classList.remove("disabled-card");
    cardCrawl.classList.remove("disabled-card");
    addPageBtn.disabled = false;
    addSelBtn.disabled = false;
    crawlBtn.disabled = false;
  } else {
    statusBadge.className = "status-badge status-offline";
    statusLabel.textContent = "Offline";
    offlineBanner.classList.remove("hidden");
    cardCollection.classList.add("disabled-card");
    cardActions.classList.add("disabled-card");
    cardCrawl.classList.add("disabled-card");
    addPageBtn.disabled = true;
    addSelBtn.disabled = true;
    crawlBtn.disabled = true;
  }
}

async function checkServer() {
  try {
    const r = await fetch(`${API}/health`);
    if (r.ok) {
      setOnline(true);
      return true;
    }
  } catch {}
  setOnline(false);
  return false;
}

retryBtn.addEventListener("click", async () => {
  retryBtn.disabled = true;
  retryBtn.style.opacity = ".5";
  const online = await checkServer();
  if (online) await loadCollections();
  retryBtn.disabled = false;
  retryBtn.style.opacity = "";
});

// ===== Collections =====

async function loadCollections() {
  try {
    const r = await fetch(`${API}/collections`);
    const data = await r.json();
    collSelect.innerHTML = "";
    if (data.length === 0) {
      collSelect.innerHTML = '<option value="default">default</option>';
      emptyState.classList.remove("hidden");
      collectionRow.classList.add("hidden");
    } else {
      emptyState.classList.add("hidden");
      collectionRow.classList.remove("hidden");
      for (const c of data) {
        const opt = document.createElement("option");
        opt.value = c.name;
        opt.textContent = `${c.name} (${c.doc_count})`;
        collSelect.appendChild(opt);
      }
    }
    const stored = await chrome.storage.local.get("activeCollection");
    if (stored.activeCollection) {
      collSelect.value = stored.activeCollection;
    }
  } catch {}
}

collSelect.addEventListener("change", () => {
  chrome.storage.local.set({ activeCollection: collSelect.value });
});

// ===== New Collection =====

let collFormOpen = false;

newCollBtn.addEventListener("click", () => {
  collFormOpen = !collFormOpen;
  if (collFormOpen) {
    newCollForm.classList.remove("collapsed");
    newCollForm.classList.add("expanded");
    newCollBtn.classList.add("active");
    newCollInput.focus();
  } else {
    closeCollForm();
  }
});

function closeCollForm() {
  collFormOpen = false;
  newCollForm.classList.remove("expanded");
  newCollForm.classList.add("collapsed");
  newCollBtn.classList.remove("active");
  collInputError.classList.add("hidden");
  newCollInput.classList.remove("input-invalid");
}

newCollInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") createCollBtn.click();
  if (e.key === "Escape") closeCollForm();
});

newCollInput.addEventListener("input", () => {
  if (newCollInput.value.trim()) {
    collInputError.classList.add("hidden");
    newCollInput.classList.remove("input-invalid");
  }
});

createCollBtn.addEventListener("click", async () => {
  const name = newCollInput.value.trim();
  if (!name) {
    collInputError.classList.remove("hidden");
    newCollInput.classList.add("input-invalid");
    newCollInput.focus();
    return;
  }
  try {
    await fetch(`${API}/collections`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    newCollInput.value = "";
    closeCollForm();
    await loadCollections();
    collSelect.value = name.toLowerCase().replace(/ /g, "-");
    chrome.storage.local.set({ activeCollection: collSelect.value });
    showMessage(`Collection "${collSelect.value}" created`);
  } catch (e) {
    showMessage("Failed to create collection", "error");
  }
});

// ===== Button Loading State Helpers =====

function setButtonLoading(btn, loading, originalLabel) {
  const label = btn.querySelector(".btn-label");
  const spinner = btn.querySelector(".spinner");
  if (loading) {
    btn.disabled = true;
    if (label) label.textContent = "Adding\u2026";
    if (spinner) spinner.classList.remove("hidden");
    btn.querySelector("svg:first-of-type")?.classList.add("hidden");
  } else {
    btn.disabled = false;
    if (label) label.textContent = originalLabel;
    if (spinner) spinner.classList.add("hidden");
    btn.querySelector("svg:first-of-type")?.classList.remove("hidden");
  }
}

// ===== Add Page =====

addPageBtn.addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  try {
    const response = await chrome.tabs.sendMessage(tab.id, { action: "extract_page" });
    if (!response || !response.text) {
      showMessage("No text extracted from page", "error");
      return;
    }
    setButtonLoading(addPageBtn, true);
    const r = await fetch(`${API}/add`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: response.text,
        collection: collSelect.value || "default",
        source: response.url,
        tags: ["page"],
      }),
    });
    const data = await r.json();
    showMessage(`Added ${data.added || 0} chunks`);
    await loadCollections();
  } catch (e) {
    showMessage("Failed: " + e.message, "error");
  } finally {
    setButtonLoading(addPageBtn, false, "Add this page");
  }
});

// ===== Add Selection =====

addSelBtn.addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  try {
    const response = await chrome.tabs.sendMessage(tab.id, { action: "extract_selection" });
    if (!response || !response.text) {
      showMessage("No text selected", "error");
      return;
    }
    setButtonLoading(addSelBtn, true);
    const r = await fetch(`${API}/add`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: response.text,
        collection: collSelect.value || "default",
        source: response.url,
        tags: ["selection"],
      }),
    });
    const data = await r.json();
    showMessage(`Added ${data.added || 0} chunks`);
    await loadCollections();
  } catch (e) {
    showMessage("Failed: " + e.message, "error");
  } finally {
    setButtonLoading(addSelBtn, false, "Add selection");
  }
});

// ===== Crawl URL Validation =====

function isValidUrl(str) {
  try {
    const u = new URL(str);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
}

crawlUrl.addEventListener("input", () => {
  const val = crawlUrl.value.trim();
  if (val && !isValidUrl(val)) {
    crawlUrlError.classList.remove("hidden");
    crawlUrl.classList.add("input-invalid");
  } else {
    crawlUrlError.classList.add("hidden");
    crawlUrl.classList.remove("input-invalid");
  }
});

crawlUrl.addEventListener("keydown", (e) => {
  if (e.key === "Enter") crawlBtn.click();
});

// Pre-fill crawl URL with current tab
chrome.tabs.query({ active: true, currentWindow: true }, ([tab]) => {
  if (tab && tab.url) {
    try {
      const u = new URL(tab.url);
      crawlUrl.value = u.origin + u.pathname;
    } catch {}
  }
});

// ===== Crawl =====

crawlBtn.addEventListener("click", async () => {
  const url = crawlUrl.value.trim();
  if (!url) return;
  if (!isValidUrl(url)) {
    crawlUrlError.classList.remove("hidden");
    crawlUrl.classList.add("input-invalid");
    return;
  }

  const label = crawlBtn.querySelector(".btn-label");
  const spinner = crawlBtn.querySelector(".spinner");
  const svgIcon = crawlBtn.querySelector("svg:first-of-type");

  crawlBtn.disabled = true;
  if (label) label.textContent = "Starting\u2026";
  if (spinner) spinner.classList.remove("hidden");
  if (svgIcon) svgIcon.classList.add("hidden");

  try {
    const r = await fetch(`${API}/crawl`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url,
        collection: collSelect.value || "default",
        max_pages: parseInt(crawlMax.value) || 50,
      }),
    });
    const data = await r.json();
    const taskId = data.task_id;

    crawlProgress.classList.remove("hidden");
    crawlProgressText.textContent = "Crawling\u2026";
    crawlProgressPct.textContent = "0%";
    crawlProgressBar.style.width = "0%";
    crawlProgressBar.classList.add("pulsing");

    const poll = setInterval(async () => {
      try {
        const sr = await fetch(`${API}/crawl/${taskId}`);
        const st = await sr.json();
        const crawled = st.pages_crawled || 0;
        const total = st.pages_total || 1;
        const pct = Math.round((crawled / total) * 100);

        crawlProgressText.textContent = `${crawled}/${total} pages`;
        crawlProgressPct.textContent = `${pct}%`;
        crawlProgressBar.style.width = `${pct}%`;

        if (st.status === "done" || st.status.startsWith("error")) {
          clearInterval(poll);
          crawlProgressBar.classList.remove("pulsing");

          crawlBtn.disabled = false;
          if (label) label.textContent = "Crawl";
          if (spinner) spinner.classList.add("hidden");
          if (svgIcon) svgIcon.classList.remove("hidden");

          await loadCollections();
          if (st.status === "done") {
            crawlProgressBar.style.width = "100%";
            crawlProgressPct.textContent = "100%";
            showMessage(`Crawled ${st.pages_crawled} pages`);
            setTimeout(() => {
              crawlProgress.classList.add("hidden");
            }, 3000);
          } else {
            showMessage(st.status, "error");
            crawlProgress.classList.add("hidden");
          }
        }
      } catch {
        clearInterval(poll);
        crawlBtn.disabled = false;
        if (label) label.textContent = "Crawl";
        if (spinner) spinner.classList.add("hidden");
        if (svgIcon) svgIcon.classList.remove("hidden");
        crawlProgress.classList.add("hidden");
      }
    }, 2000);
  } catch (e) {
    showMessage("Crawl failed: " + e.message, "error");
    crawlBtn.disabled = false;
    if (label) label.textContent = "Crawl";
    if (spinner) spinner.classList.add("hidden");
    if (svgIcon) svgIcon.classList.remove("hidden");
  }
});

// ===== Init =====

(async () => {
  if (await checkServer()) {
    await loadCollections();
  }
})();
