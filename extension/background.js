// Context Engine — Service Worker (background)

const API_BASE = "http://localhost:11811";

// Create context menus on install
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "add-selection",
    title: "Add selection to Context Engine",
    contexts: ["selection"],
  });
  chrome.contextMenus.create({
    id: "add-page",
    title: "Add page to Context Engine",
    contexts: ["page"],
  });
});

// Get active collection from storage, default to "default"
async function getActiveCollection() {
  const data = await chrome.storage.local.get("activeCollection");
  return data.activeCollection || "default";
}

// Handle context menu clicks
chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const collection = await getActiveCollection();

  if (info.menuItemId === "add-selection" && info.selectionText) {
    try {
      const resp = await fetch(`${API_BASE}/add`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: info.selectionText,
          collection,
          source: tab.url,
          tags: ["selection"],
        }),
      });
      const data = await resp.json();
      console.log("Context Engine: added selection", data);
    } catch (e) {
      console.error("Context Engine: failed to add selection", e);
    }
  }

  if (info.menuItemId === "add-page") {
    try {
      const [result] = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => {
          const clone = document.body.cloneNode(true);
          clone.querySelectorAll("script, style, nav, footer, header, aside, noscript, svg, iframe").forEach(el => el.remove());
          const content = clone.querySelector("main") || clone.querySelector("article") || clone;
          return (content.innerText || content.textContent || "").trim();
        },
      });
      if (result.result) {
        const resp = await fetch(`${API_BASE}/add`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            text: result.result,
            collection,
            source: tab.url,
            tags: ["page"],
          }),
        });
        const data = await resp.json();
        console.log("Context Engine: added page", data);
      }
    } catch (e) {
      console.error("Context Engine: failed to add page", e);
    }
  }
});
