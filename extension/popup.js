// Context Engine — Popup Script

const API = "http://localhost:11811";

const statusDot = document.getElementById("status-dot");
const collSelect = document.getElementById("collection-select");
const newCollBtn = document.getElementById("new-collection-btn");
const newCollForm = document.getElementById("new-collection-form");
const newCollInput = document.getElementById("new-collection-input");
const createCollBtn = document.getElementById("create-collection-btn");
const addPageBtn = document.getElementById("add-page-btn");
const addSelBtn = document.getElementById("add-selection-btn");
const crawlUrl = document.getElementById("crawl-url");
const crawlMax = document.getElementById("crawl-max");
const crawlBtn = document.getElementById("crawl-btn");
const crawlStatus = document.getElementById("crawl-status");
const messageEl = document.getElementById("message");

let serverOnline = false;

function showMessage(text, type = "success") {
  messageEl.textContent = text;
  messageEl.className = type;
  setTimeout(() => { messageEl.className = "hidden"; }, 3000);
}

async function checkServer() {
  try {
    const r = await fetch(`${API}/health`);
    if (r.ok) {
      serverOnline = true;
      statusDot.className = "dot dot-green";
      addPageBtn.disabled = false;
      addSelBtn.disabled = false;
      crawlBtn.disabled = false;
      return true;
    }
  } catch {}
  serverOnline = false;
  statusDot.className = "dot dot-red";
  return false;
}

async function loadCollections() {
  try {
    const r = await fetch(`${API}/collections`);
    const data = await r.json();
    collSelect.innerHTML = "";
    if (data.length === 0) {
      collSelect.innerHTML = '<option value="default">default</option>';
    } else {
      for (const c of data) {
        const opt = document.createElement("option");
        opt.value = c.name;
        opt.textContent = `${c.name} (${c.doc_count})`;
        collSelect.appendChild(opt);
      }
    }
    // Restore last selection
    const stored = await chrome.storage.local.get("activeCollection");
    if (stored.activeCollection) {
      collSelect.value = stored.activeCollection;
    }
  } catch {}
}

collSelect.addEventListener("change", () => {
  chrome.storage.local.set({ activeCollection: collSelect.value });
});

newCollBtn.addEventListener("click", () => {
  newCollForm.classList.toggle("hidden");
  newCollInput.focus();
});

createCollBtn.addEventListener("click", async () => {
  const name = newCollInput.value.trim();
  if (!name) return;
  try {
    await fetch(`${API}/collections`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    newCollInput.value = "";
    newCollForm.classList.add("hidden");
    await loadCollections();
    collSelect.value = name.toLowerCase().replace(/ /g, "-");
    chrome.storage.local.set({ activeCollection: collSelect.value });
    showMessage(`Collection "${collSelect.value}" created`);
  } catch (e) {
    showMessage("Failed to create collection", "error");
  }
});

addPageBtn.addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  try {
    const response = await chrome.tabs.sendMessage(tab.id, { action: "extract_page" });
    if (!response || !response.text) {
      showMessage("No text extracted from page", "error");
      return;
    }
    addPageBtn.disabled = true;
    addPageBtn.textContent = "Adding...";
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
    addPageBtn.disabled = false;
    addPageBtn.textContent = "Add this page";
  }
});

addSelBtn.addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  try {
    const response = await chrome.tabs.sendMessage(tab.id, { action: "extract_selection" });
    if (!response || !response.text) {
      showMessage("No text selected", "error");
      return;
    }
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
  }
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

crawlBtn.addEventListener("click", async () => {
  const url = crawlUrl.value.trim();
  if (!url) return;
  crawlBtn.disabled = true;
  crawlBtn.textContent = "Starting...";
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
    crawlStatus.classList.remove("hidden");
    crawlStatus.textContent = "Crawling...";

    // Poll for progress
    const poll = setInterval(async () => {
      try {
        const sr = await fetch(`${API}/crawl/${taskId}`);
        const st = await sr.json();
        crawlStatus.textContent = `${st.status} — ${st.pages_crawled}/${st.pages_total} pages`;
        if (st.status === "done" || st.status.startsWith("error")) {
          clearInterval(poll);
          crawlBtn.disabled = false;
          crawlBtn.textContent = "Crawl";
          await loadCollections();
          if (st.status === "done") {
            showMessage(`Crawled ${st.pages_crawled} pages`);
          } else {
            showMessage(st.status, "error");
          }
        }
      } catch {
        clearInterval(poll);
        crawlBtn.disabled = false;
        crawlBtn.textContent = "Crawl";
      }
    }, 2000);
  } catch (e) {
    showMessage("Crawl failed: " + e.message, "error");
    crawlBtn.disabled = false;
    crawlBtn.textContent = "Crawl";
  }
});

// Init
(async () => {
  if (await checkServer()) {
    await loadCollections();
  }
})();
