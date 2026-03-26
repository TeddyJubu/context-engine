(function (root, factory) {
  const exports = factory();

  if (typeof module !== "undefined" && module.exports) {
    module.exports = exports;
  }

  root.ContextEngineYouTubeTranscriptShared = root.ContextEngineYouTubeTranscriptShared || {};
  root.ContextEngineYouTubeTranscriptShared.url = exports;
  Object.assign(root.ContextEngineYouTubeTranscriptShared, exports);
}(typeof globalThis !== "undefined" ? globalThis : self, function () {
  const SUPPORTED_HOSTS = new Set([
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
  ]);

  function extractVideoId(input) {
    if (!input) return null;

    const trimmed = String(input).trim();
    if (/^[a-zA-Z0-9_-]{11}$/.test(trimmed)) {
      return trimmed;
    }

    try {
      const url = new URL(trimmed);
      const hostname = url.hostname.toLowerCase();

      if (!SUPPORTED_HOSTS.has(hostname)) {
        return null;
      }

      if (hostname === "youtu.be") {
        return cleanVideoId(url.pathname.slice(1));
      }

      if (url.pathname === "/watch") {
        return cleanVideoId(url.searchParams.get("v"));
      }

      if (url.pathname.startsWith("/embed/")) {
        return cleanVideoId(url.pathname.split("/")[2]);
      }

      if (url.pathname.startsWith("/shorts/")) {
        return cleanVideoId(url.pathname.split("/")[2]);
      }
    } catch {}

    const match = trimmed.match(
      /(?:youtube\.com\/watch\?v=|youtube\.com\/embed\/|youtube\.com\/shorts\/|youtu\.be\/)([a-zA-Z0-9_-]{11})/,
    );

    return cleanVideoId(match && match[1]);
  }

  function isSupportedYouTubeUrl(input) {
    return Boolean(extractVideoId(input));
  }

  function cleanVideoId(value) {
    if (!value) return null;
    const trimmed = String(value).trim();
    return /^[a-zA-Z0-9_-]{11}$/.test(trimmed) ? trimmed : null;
  }

  return {
    extractVideoId,
    isSupportedYouTubeUrl,
  };
}));
