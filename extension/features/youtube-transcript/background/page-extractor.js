(function (root, factory) {
  const exports = factory(root);

  if (typeof module !== "undefined" && module.exports) {
    module.exports = exports;
  }

  root.ContextEngineYouTubeTranscriptPage = root.ContextEngineYouTubeTranscriptPage || {};
  Object.assign(root.ContextEngineYouTubeTranscriptPage, exports);
}(typeof globalThis !== "undefined" ? globalThis : self, function (root) {
  async function extractActiveTranscript(options) {
    const domain = root.ContextEngineYouTubeTranscriptDomain;
    if (!domain || typeof domain.extractTranscriptFromPageContext !== "function") {
      throw new Error("Context Engine YouTube transcript domain helpers are unavailable.");
    }

    return domain.extractTranscriptFromPageContext({
      win: root,
      doc: root.document,
      fetch: root.fetch.bind(root),
    }, options || {});
  }

  return {
    extractActiveTranscript,
  };
}));
