(function (root, factory) {
  const exports = factory(root);

  if (typeof module !== "undefined" && module.exports) {
    module.exports = exports;
  }

  root.ContextEngineYouTubeTranscriptUI = root.ContextEngineYouTubeTranscriptUI || {};
  Object.assign(root.ContextEngineYouTubeTranscriptUI, exports);
}(typeof globalThis !== "undefined" ? globalThis : self, function (root) {
  const shared = root.ContextEngineYouTubeTranscriptShared || {};
  const isSupportedYouTubeUrl = shared.isSupportedYouTubeUrl;

  function buildPanelState(tab, serverOnline) {
    const url = tab && tab.url ? tab.url : "";
    const supported = isSupportedYouTubeUrl(url);

    if (!serverOnline) {
      return {
        canExtract: false,
        hint: "Start the local server to add transcripts to Context Engine.",
        chipLabel: "Offline",
        chipTone: "muted",
        title: tab && tab.title ? tab.title : "",
        url,
      };
    }

    if (!supported) {
      return {
        canExtract: false,
        hint: "Open a YouTube video tab to enable transcript capture.",
        chipLabel: "Inactive",
        chipTone: "muted",
        title: "",
        url: "",
      };
    }

    return {
      canExtract: true,
      hint: "Capture captions from the active YouTube video into the selected collection.",
      chipLabel: "Ready",
      chipTone: "ready",
      title: tab && tab.title ? tab.title : "YouTube video",
      url,
    };
  }

  function renderPanelState(elements, state) {
    elements.button.disabled = !state.canExtract;
    elements.helper.textContent = state.hint;
    elements.status.textContent = state.chipLabel;
    elements.status.className = "youtube-chip youtube-chip-" + state.chipTone;

    if (state.title) {
      elements.meta.classList.remove("hidden");
      elements.title.textContent = state.title;
      elements.url.textContent = state.url;
    } else {
      elements.meta.classList.add("hidden");
      elements.title.textContent = "";
      elements.url.textContent = "";
    }
  }

  function formatResultMessage(response) {
    if (!response || !response.transcriptResult) {
      return "Transcript extraction failed.";
    }

    if (response.success) {
      const result = response.transcriptResult;
      const added = Number((response.addResult && response.addResult.added) || 0);
      if (added === 0) {
        return "No new transcript chunks were added from \"" + result.title + "\".";
      }
      const details = [result.language];
      details.push(result.isGenerated ? "auto-generated" : "manual");
      return "Added "
        + String(added)
        + " transcript chunks from \"" + result.title + "\" (" + details.join(", ") + ").";
    }

    switch (response.transcriptResult.code) {
      case "invalid_url":
        return "Open a supported YouTube video page first.";
      case "login_required":
        return "YouTube requires a signed-in session for this video.";
      case "no_captions":
        return "No captions are available for this video.";
      case "bot_challenge":
        return "YouTube presented a bot challenge while loading captions.";
      case "rate_limited":
        return "YouTube rate-limited transcript extraction. Try again in a moment.";
      case "unplayable":
        return "This YouTube video could not be played for transcript extraction.";
      default:
        return response.transcriptResult.error || "Transcript extraction failed.";
    }
  }

  return {
    buildPanelState,
    formatResultMessage,
    renderPanelState,
  };
}));
