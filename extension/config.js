const CONTEXT_ENGINE_CONFIG = {
  API_BASE: "http://localhost:11811",
  AUTH_HEADER: "X-Context-Token",
  AUTH_TOKEN: "",  // Loaded from chrome.storage.local at runtime; set via extension popup
};

if (typeof self !== "undefined") {
  self.CONTEXT_ENGINE_CONFIG = CONTEXT_ENGINE_CONFIG;
}

if (typeof window !== "undefined") {
  window.CONTEXT_ENGINE_CONFIG = CONTEXT_ENGINE_CONFIG;
}
