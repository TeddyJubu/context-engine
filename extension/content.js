// Context Engine — Content Script
// Extracts page text or selection on demand

const STRIP_SELECTORS = "script, style, nav, footer, header, aside, noscript, svg, iframe, [role='navigation'], [role='banner'], [role='contentinfo']";

function extractPageText() {
  // Clone the body to avoid modifying the live DOM
  const clone = document.body.cloneNode(true);

  // Remove unwanted elements
  clone.querySelectorAll(STRIP_SELECTORS).forEach(el => el.remove());

  // Prefer main/article content
  const content = clone.querySelector("main") || clone.querySelector("article") || clone;
  const text = content.innerText || content.textContent || "";

  return {
    text: text.trim(),
    url: window.location.href,
    title: document.title,
  };
}

function extractSelection() {
  const sel = window.getSelection();
  return {
    text: sel ? sel.toString().trim() : "",
    url: window.location.href,
    title: document.title,
  };
}

// Listen for messages from popup or background
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === "extract_page") {
    sendResponse(extractPageText());
  } else if (msg.action === "extract_selection") {
    sendResponse(extractSelection());
  }
  return true; // keep channel open for async
});
