(function (root, factory) {
  const exports = factory();

  if (typeof module !== "undefined" && module.exports) {
    module.exports = exports;
  }

  root.ContextEngineYouTubeTranscriptDomain = root.ContextEngineYouTubeTranscriptDomain || {};
  Object.assign(root.ContextEngineYouTubeTranscriptDomain, exports);
}(typeof globalThis !== "undefined" ? globalThis : self, function () {
  function normalizeTranscriptToAddPayload(result, collection) {
    if (!result || !result.success) {
      throw new Error("normalizeTranscriptToAddPayload expects a successful transcript result.");
    }

    return {
      text: result.transcript,
      collection,
      source: result.url,
      source_type: "youtube_transcript",
      metadata: {
        videoId: result.videoId,
        title: result.title,
        url: result.url,
        language: result.language,
        isGenerated: Boolean(result.isGenerated),
        method: result.method,
      },
      tags: ["youtube-transcript"],
    };
  }

  return {
    normalizeTranscriptToAddPayload,
  };
}));
