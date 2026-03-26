(function (root, factory) {
  const exports = factory(root);

  if (typeof module !== "undefined" && module.exports) {
    module.exports = exports;
  }

  root.ContextEngineYouTubeTranscriptDomain = root.ContextEngineYouTubeTranscriptDomain || {};
  Object.assign(root.ContextEngineYouTubeTranscriptDomain, exports);
}(typeof globalThis !== "undefined" ? globalThis : self, function (root) {
  const shared = root.ContextEngineYouTubeTranscriptShared || {};
  const createTranscriptFailureResult = shared.createTranscriptFailureResult;
  const createTranscriptSuccessResult = shared.createTranscriptSuccessResult;
  const extractVideoId = shared.extractVideoId;

  const DEFAULT_LANGUAGES = ["en", "en-US"];
  const DEFAULT_CLIENT_VERSION = "2.20250312.01.00";
  const BOT_CHALLENGE_PATTERNS = ["g-recaptcha", "\"isbot\":true", "unusual traffic", "/sorry/"];

  class TranscriptExtractionError extends Error {
    constructor(code, message, options) {
      super(message);
      this.name = "TranscriptExtractionError";
      this.code = code;
      this.retryable = Boolean(options && options.retryable);
      this.method = options && options.method;
    }
  }

  async function extractTranscriptFromPageContext(context, options) {
    const settings = options || {};
    const url = settings.url || (context.win && context.win.location ? context.win.location.href : "");
    const videoId = extractVideoId(url);

    if (!videoId) {
      return createTranscriptFailureResult({
        videoId: url,
        code: "invalid_url",
        error: "Open a supported YouTube video page to extract a transcript.",
        retryable: false,
      });
    }

    const languages = settings.languages && settings.languages.length
      ? settings.languages
      : DEFAULT_LANGUAGES;

    try {
      return await extractTranscriptOrThrow(context, {
        languages,
        url,
        videoId,
      });
    } catch (error) {
      const normalized = normalizeTranscriptError(error);
      return createTranscriptFailureResult({
        videoId,
        code: normalized.code,
        error: normalized.message,
        retryable: normalized.retryable,
        ...(normalized.method ? { method: normalized.method } : {}),
      });
    }
  }

  async function extractTranscriptOrThrow(context, settings) {
    let lastError = null;
    const strategies = [
      {
        name: "page-player-response",
        run: async function () {
          const playerData = getPagePlayerResponse(context);
          if (!playerData) return null;
          return extractFromPlayerData(context, settings, playerData, "page-player-response");
        },
      },
      {
        name: "page-innertube",
        run: async function () {
          const html = await fetchWatchHtml(context, settings.url, "page-watch-bootstrap");
          const apiKey = extractInnertubeApiKeyFromHtml(html);
          if (!apiKey) {
            throw new TranscriptExtractionError(
              "upstream_error",
              "Could not discover the YouTube player bootstrap key.",
              { retryable: true, method: "page-watch-bootstrap" },
            );
          }

          const playerData = await fetchInnertubePlayerData(context, settings.videoId, apiKey, html);
          return extractFromPlayerData(context, settings, playerData, "page-innertube");
        },
      },
    ];

    for (let index = 0; index < strategies.length; index++) {
      const strategy = strategies[index];
      try {
        const result = await strategy.run();
        if (result) return result;
      } catch (error) {
        lastError = normalizeTranscriptError(error, strategy.name);
        if (!shouldContinueAfterFailure(lastError, index < strategies.length - 1)) {
          throw lastError;
        }
      }
    }

    throw lastError || new TranscriptExtractionError(
      "upstream_error",
      "Transcript extraction failed before a supported strategy completed.",
      { retryable: true, method: "page-extractor" },
    );
  }

  function getPagePlayerResponse(context) {
    const win = context.win || {};
    const playerResponse = win.ytInitialPlayerResponse;
    if (playerResponse && typeof playerResponse === "object") {
      return playerResponse;
    }

    const legacyPlayer = win.ytplayer
      && win.ytplayer.config
      && win.ytplayer.config.args
      && win.ytplayer.config.args.player_response;
    if (legacyPlayer) {
      try {
        return typeof legacyPlayer === "string" ? JSON.parse(legacyPlayer) : legacyPlayer;
      } catch {}
    }

    const scripts = context.doc && typeof context.doc.querySelectorAll === "function"
      ? context.doc.querySelectorAll("script")
      : [];

    for (const script of scripts) {
      const text = script && (script.textContent || script.innerText || "");
      if (!text || text.indexOf("ytInitialPlayerResponse") === -1) continue;

      const parsed = extractJsonFromHtml(text, "ytInitialPlayerResponse");
      if (parsed) return parsed;
    }

    return null;
  }

  async function fetchWatchHtml(context, url, method) {
    const response = await context.fetch(url, {
      credentials: "include",
      cache: "no-store",
    });

    if (response.status === 429) {
      throw new TranscriptExtractionError(
        "rate_limited",
        "YouTube rate-limited the transcript bootstrap request.",
        { retryable: true, method },
      );
    }

    if (!response.ok) {
      throw new TranscriptExtractionError(
        "upstream_error",
        "YouTube returned HTTP " + response.status + " while loading the watch page.",
        { retryable: response.status >= 500, method },
      );
    }

    const html = await response.text();
    if (looksLikeBotChallenge(html)) {
      throw new TranscriptExtractionError(
        "bot_challenge",
        "YouTube presented a bot challenge while preparing transcript extraction.",
        { retryable: true, method },
      );
    }

    return html;
  }

  async function fetchInnertubePlayerData(context, videoId, apiKey, html) {
    const response = await context.fetch(
      "https://www.youtube.com/youtubei/v1/player?key=" + encodeURIComponent(apiKey) + "&prettyPrint=false",
      {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          context: {
            client: {
              clientName: "WEB",
              clientVersion: getClientVersion(context, html),
              hl: "en",
              gl: "US",
            },
          },
          videoId,
        }),
      },
    );

    if (response.status === 429) {
      throw new TranscriptExtractionError(
        "rate_limited",
        "YouTube rate-limited the player request.",
        { retryable: true, method: "page-innertube" },
      );
    }

    if (!response.ok) {
      throw new TranscriptExtractionError(
        "upstream_error",
        "YouTube player data request returned HTTP " + response.status + ".",
        { retryable: response.status >= 500, method: "page-innertube" },
      );
    }

    return response.json();
  }

  async function extractFromPlayerData(context, settings, playerData, method) {
    const playability = playerData && playerData.playabilityStatus;
    const status = playability && playability.status;

    if (status && status !== "OK") {
      const reason = String((playability && playability.reason) || status);
      const lowered = reason.toLowerCase();

      if (status === "LOGIN_REQUIRED" || lowered.indexOf("sign in") !== -1) {
        throw new TranscriptExtractionError(
          "login_required",
          "YouTube requires a logged-in session for this video.",
          { method },
        );
      }

      if (lowered.indexOf("not a bot") !== -1 || lowered.indexOf("unusual traffic") !== -1) {
        throw new TranscriptExtractionError(
          "bot_challenge",
          "YouTube challenged this request as suspicious traffic.",
          { retryable: true, method },
        );
      }

      throw new TranscriptExtractionError(
        "unplayable",
        "Video is unplayable: " + reason,
        { method },
      );
    }

    const captionsData = playerData
      && playerData.captions
      && playerData.captions.playerCaptionsTracklistRenderer;
    const tracks = captionsData && captionsData.captionTracks ? captionsData.captionTracks : [];

    if (!tracks.length) {
      throw new TranscriptExtractionError(
        "no_captions",
        "No captions are available for this video.",
        { method },
      );
    }

    const track = selectTrack(tracks, settings.languages);
    if (!track) {
      throw new TranscriptExtractionError(
        "no_captions",
        "No suitable caption track was found for the requested languages.",
        { method },
      );
    }

    const transcript = await fetchCaptionText(context, track.baseUrl, method);
    const details = playerData.videoDetails || {};

    return createTranscriptSuccessResult({
      videoId: settings.videoId,
      title: details.title || settings.videoId,
      url: settings.url,
      transcript,
      language: track.languageCode || "unknown",
      isGenerated: track.kind === "asr",
      method,
    });
  }

  async function fetchCaptionText(context, baseUrl, method) {
    const response = await context.fetch(baseUrl, {
      credentials: "include",
      cache: "no-store",
    });

    if (response.status === 429) {
      throw new TranscriptExtractionError(
        "rate_limited",
        "YouTube rate-limited the caption download request.",
        { retryable: true, method },
      );
    }

    if (!response.ok) {
      throw new TranscriptExtractionError(
        "upstream_error",
        "YouTube caption download returned HTTP " + response.status + ".",
        { retryable: response.status >= 500, method },
      );
    }

    const xml = await response.text();
    const transcript = parseTranscriptXml(xml);
    if (!transcript) {
      throw new TranscriptExtractionError(
        "no_captions",
        "The selected caption track did not contain transcript text.",
        { method },
      );
    }

    return transcript;
  }

  function selectTrack(tracks, languages) {
    const preferredLanguages = Array.isArray(languages) ? languages : DEFAULT_LANGUAGES;

    for (const language of preferredLanguages) {
      const manual = tracks.find((track) => track.languageCode === language && track.kind !== "asr");
      if (manual) return manual;
    }

    for (const language of preferredLanguages) {
      const generated = tracks.find((track) => track.languageCode === language && track.kind === "asr");
      if (generated) return generated;
    }

    return tracks[0] || null;
  }

  function parseTranscriptXml(xml) {
    const lines = [];
    const regex = /<(?:text|p)\b([^>]*)>([\s\S]*?)<\/(?:text|p)>/g;
    let match;

    while ((match = regex.exec(xml)) !== null) {
      const attrs = match[1];
      const raw = match[2];
      if (!raw) continue;

      let startSeconds = 0;
      const startMatch = attrs.match(/\bstart="([^"]+)"/);
      if (startMatch) {
        startSeconds = parseFloat(startMatch[1]);
      } else {
        const timeMatch = attrs.match(/\bt="([^"]+)"/);
        if (timeMatch) {
          startSeconds = parseFloat(timeMatch[1]) / 1000;
        }
      }

      const text = decodeEntities(stripTags(raw)).replace(/\n/g, " ").trim();
      if (!text) continue;

      const mm = String(Math.floor(startSeconds / 60)).padStart(2, "0");
      const ss = String(Math.floor(startSeconds % 60)).padStart(2, "0");
      lines.push("[" + mm + ":" + ss + "] " + text);
    }

    return lines.join("\n");
  }

  function stripTags(value) {
    return value.replace(/<[^>]+>/g, "");
  }

  function decodeEntities(value) {
    return value
      .replace(/&amp;/g, "&")
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&quot;/g, "\"")
      .replace(/&#39;/g, "'")
      .replace(/&#(\d+);/g, function (_, code) {
        return String.fromCharCode(Number(code));
      })
      .replace(/&#x([0-9a-fA-F]+);/g, function (_, hex) {
        return String.fromCharCode(parseInt(hex, 16));
      });
  }

  function extractInnertubeApiKeyFromHtml(html) {
    const primary = html.match(/"INNERTUBE_API_KEY"\s*:\s*"([a-zA-Z0-9_-]+)"/);
    if (primary && primary[1]) {
      return primary[1];
    }

    const secondary = html.match(/"innertubeApiKey"\s*:\s*"([a-zA-Z0-9_-]+)"/);
    return secondary && secondary[1] ? secondary[1] : null;
  }

  function extractJsonFromHtml(html, varName) {
    const patterns = [
      new RegExp("var\\s+" + varName + "\\s*=\\s*"),
      new RegExp(varName + "\\s*=\\s*"),
    ];

    let startIndex = -1;
    for (const pattern of patterns) {
      const match = pattern.exec(html);
      if (match) {
        startIndex = match.index + match[0].length;
        break;
      }
    }

    if (startIndex === -1) return null;

    let depth = 0;
    let inString = false;
    let escaped = false;
    let begin = -1;

    for (let index = startIndex; index < html.length; index++) {
      const char = html[index];

      if (escaped) {
        escaped = false;
        continue;
      }

      if (inString) {
        if (char === "\\") {
          escaped = true;
        } else if (char === "\"") {
          inString = false;
        }
        continue;
      }

      if (char === "\"") {
        inString = true;
        continue;
      }

      if (char === "{") {
        if (depth === 0) {
          begin = index;
        }
        depth += 1;
      } else if (char === "}") {
        depth -= 1;
        if (depth === 0 && begin !== -1) {
          const raw = html.substring(begin, index + 1);
          const cleaned = raw.replace(/\\x([0-9a-fA-F]{2})/g, "\\u00$1");
          try {
            return JSON.parse(cleaned);
          } catch {
            return null;
          }
        }
      }
    }

    return null;
  }

  function looksLikeBotChallenge(html) {
    const lowered = String(html || "").toLowerCase();
    return BOT_CHALLENGE_PATTERNS.some((pattern) => lowered.indexOf(pattern) !== -1);
  }

  function getClientVersion(context, html) {
    const win = context.win || {};
    try {
      if (win.ytcfg && typeof win.ytcfg.get === "function") {
        const value = win.ytcfg.get("INNERTUBE_CONTEXT_CLIENT_VERSION");
        if (value) return String(value);
      }
    } catch {}

    const embedded = html && html.match(/"INNERTUBE_CONTEXT_CLIENT_VERSION"\s*:\s*"([^"]+)"/);
    return embedded && embedded[1] ? embedded[1] : DEFAULT_CLIENT_VERSION;
  }

  function shouldContinueAfterFailure(error, hasRemainingStrategies) {
    if (!hasRemainingStrategies) return false;

    return error.code === "rate_limited"
      || error.code === "bot_challenge"
      || error.code === "login_required"
      || error.code === "upstream_error";
  }

  function normalizeTranscriptError(error, method) {
    if (error instanceof TranscriptExtractionError) {
      return error;
    }

    if (error instanceof Error) {
      return new TranscriptExtractionError(
        "upstream_error",
        error.message,
        { retryable: true, method },
      );
    }

    return new TranscriptExtractionError(
      "upstream_error",
      String(error),
      { retryable: true, method },
    );
  }

  return {
    DEFAULT_LANGUAGES,
    extractInnertubeApiKeyFromHtml,
    extractJsonFromHtml,
    extractTranscriptFromPageContext,
    getPagePlayerResponse,
    looksLikeBotChallenge,
    parseTranscriptXml,
    selectTrack,
  };
}));
