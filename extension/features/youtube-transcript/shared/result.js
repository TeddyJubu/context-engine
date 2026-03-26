(function (root, factory) {
  const exports = factory();

  if (typeof module !== "undefined" && module.exports) {
    module.exports = exports;
  }

  root.ContextEngineYouTubeTranscriptShared = root.ContextEngineYouTubeTranscriptShared || {};
  root.ContextEngineYouTubeTranscriptShared.results = exports;
  Object.assign(root.ContextEngineYouTubeTranscriptShared, exports);
}(typeof globalThis !== "undefined" ? globalThis : self, function () {
  const TRANSCRIPT_FAILURE_CODES = Object.freeze([
    "invalid_url",
    "rate_limited",
    "bot_challenge",
    "login_required",
    "no_captions",
    "unplayable",
    "upstream_error",
  ]);

  function createTranscriptSuccessResult(fields) {
    return {
      success: true,
      ...fields,
    };
  }

  function createTranscriptFailureResult(videoId, code, error, retryable, extras) {
    return {
      videoId: videoId || "",
      success: false,
      error,
      code,
      retryable: Boolean(retryable),
      ...(extras || {}),
    };
  }

  return {
    TRANSCRIPT_FAILURE_CODES,
    createTranscriptFailureResult,
    createTranscriptSuccessResult,
  };
}));
