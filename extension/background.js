// Context Engine — Service Worker (background)

importScripts(
  "config.js",
  "features/youtube-transcript/shared/result.js",
  "features/youtube-transcript/shared/url.js",
  "features/youtube-transcript/domain/normalize.js",
  "features/youtube-transcript/background/service.js",
);

let API_BASE = CONTEXT_ENGINE_CONFIG.API_BASE;
let AUTH_HEADER = CONTEXT_ENGINE_CONFIG.AUTH_HEADER;
let AUTH_TOKEN = CONTEXT_ENGINE_CONFIG.AUTH_TOKEN;

const authTokenReady = new Promise((resolve) => {
  chrome.storage.local.get(["authToken"], (data) => {
    AUTH_TOKEN = data.authToken || AUTH_TOKEN;
    resolve();
  });
});

chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName === "local" && changes.authToken) {
    AUTH_TOKEN = changes.authToken.newValue || "";
  }
});

function authHeaders(extra = {}) {
  return {
    ...extra,
    [AUTH_HEADER]: AUTH_TOKEN,
  };
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action !== "youtube_transcript:add_active_tab") {
    return false;
  }

  authTokenReady
    .then(() => ContextEngineYouTubeTranscriptBackground.addActiveTabTranscript({
      apiBase: API_BASE,
      authHeaders,
      collection: message.collection,
      tabId: message.tabId,
      url: message.url,
    }))
    .then(sendResponse)
    .catch((error) => {
      const videoId = ContextEngineYouTubeTranscriptShared.extractVideoId(message.url || "") || "";
      sendResponse({
        success: false,
        transcriptResult: ContextEngineYouTubeTranscriptShared.createTranscriptFailureResult({
          videoId,
          code: "upstream_error",
          error: error instanceof Error ? error.message : String(error),
          retryable: true,
          method: "background-message",
        }),
      });
    });

  return true;
});

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
  await authTokenReady;
  const collection = await getActiveCollection();

  if (info.menuItemId === "add-selection" && info.selectionText) {
    try {
      const resp = await fetch(`${API_BASE}/add`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
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
          headers: authHeaders({ "Content-Type": "application/json" }),
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
