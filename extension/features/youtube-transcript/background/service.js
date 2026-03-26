(function (root, factory) {
  const exports = factory(root);

  if (typeof module !== "undefined" && module.exports) {
    module.exports = exports;
  }

  root.ContextEngineYouTubeTranscriptBackground = root.ContextEngineYouTubeTranscriptBackground || {};
  Object.assign(root.ContextEngineYouTubeTranscriptBackground, exports);
}(typeof globalThis !== "undefined" ? globalThis : self, function (root) {
  const shared = root.ContextEngineYouTubeTranscriptShared || {};
  const domain = root.ContextEngineYouTubeTranscriptDomain || {};
  const createTranscriptFailureResult = shared.createTranscriptFailureResult;
  const extractVideoId = shared.extractVideoId;
  const isSupportedYouTubeUrl = shared.isSupportedYouTubeUrl;
  const normalizeTranscriptToAddPayload = domain.normalizeTranscriptToAddPayload;

  const INJECTED_FILES = [
    "features/youtube-transcript/shared/result.js",
    "features/youtube-transcript/shared/url.js",
    "features/youtube-transcript/domain/transcript.js",
    "features/youtube-transcript/background/page-extractor.js",
  ];

  function validateActiveTab(tab) {
    const url = tab && tab.url ? tab.url : "";
    const videoId = extractVideoId(url);
    if (!videoId || !isSupportedYouTubeUrl(url)) {
      return {
        ok: false,
        result: createTranscriptFailureResult(
          url,
          "invalid_url",
          "Open a supported YouTube video page to extract a transcript.",
          false,
        ),
      };
    }

    return {
      ok: true,
      videoId,
      url,
    };
  }

  async function addActiveTabTranscript(options) {
    const validation = validateActiveTab(options);
    if (!validation.ok) {
      return {
        success: false,
        transcriptResult: validation.result,
      };
    }

    const transcriptResult = await extractTranscriptFromTab(options.tabId, {
      url: validation.url,
    });

    if (!transcriptResult || !transcriptResult.success) {
      return {
        success: false,
        transcriptResult: transcriptResult || createTranscriptFailureResult(
          validation.videoId,
          "upstream_error",
          "Transcript extraction did not return a result.",
          true,
          { method: "background-injection" },
        ),
      };
    }

    try {
      const payload = normalizeTranscriptToAddPayload(
        transcriptResult,
        options.collection || "default",
      );

      const response = await fetch(options.apiBase + "/add", {
        method: "POST",
        headers: options.authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        return {
          success: false,
          transcriptResult: createTranscriptFailureResult(
            validation.videoId,
            "upstream_error",
            "Context Engine add request failed with HTTP " + response.status + ".",
            response.status >= 500,
            { method: "background-add" },
          ),
        };
      }

      return {
        success: true,
        transcriptResult,
        addResult: await response.json(),
      };
    } catch (error) {
      return {
        success: false,
        transcriptResult: createTranscriptFailureResult(
          validation.videoId,
          "upstream_error",
          error instanceof Error ? error.message : String(error),
          true,
          { method: "background-add" },
        ),
      };
    }
  }

  async function extractTranscriptFromTab(tabId, options) {
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        world: "MAIN",
        files: INJECTED_FILES,
      });

      const results = await chrome.scripting.executeScript({
        target: { tabId },
        world: "MAIN",
        args: [options || {}],
        func: async (settings) => {
          const api = globalThis.ContextEngineYouTubeTranscriptPage;
          if (!api || typeof api.extractActiveTranscript !== "function") {
            return {
              success: false,
              videoId: settings && settings.url ? settings.url : "",
              error: "YouTube transcript extractor did not initialize in the page context.",
              code: "upstream_error",
              retryable: true,
              method: "page-main-world",
            };
          }

          return api.extractActiveTranscript(settings);
        },
      });

      return results && results[0] ? results[0].result : null;
    } catch (error) {
      return createTranscriptFailureResult(
        options && options.url ? options.url : "",
        "upstream_error",
        error instanceof Error ? error.message : String(error),
        true,
        { method: "background-injection" },
      );
    }
  }

  return {
    addActiveTabTranscript,
    validateActiveTab,
  };
}));
