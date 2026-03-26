const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

require(path.resolve(__dirname, "../features/youtube-transcript/shared/result.js"));
require(path.resolve(__dirname, "../features/youtube-transcript/shared/url.js"));
const transcriptDomain = require(path.resolve(__dirname, "../features/youtube-transcript/domain/transcript.js"));
const normalizeDomain = require(path.resolve(__dirname, "../features/youtube-transcript/domain/normalize.js"));
const backgroundService = require(path.resolve(__dirname, "../features/youtube-transcript/background/service.js"));

const captionedPageFixture = fs.readFileSync(
  path.resolve(__dirname, "fixtures/captioned-page.html"),
  "utf8",
);
const captionedTranscriptFixture = fs.readFileSync(
  path.resolve(__dirname, "fixtures/captioned-transcript.xml"),
  "utf8",
);
const noCaptionsFixture = fs.readFileSync(
  path.resolve(__dirname, "fixtures/no-captions-page.html"),
  "utf8",
);
const innertubeBootstrapFixture = 'window["ytcfg"]={"INNERTUBE_API_KEY":"test-api-key","INNERTUBE_CONTEXT_CLIENT_VERSION":"2.20250312.01.00"};';

function createContext(scriptText, fetchImpl, url) {
  return {
    win: {
      location: {
        href: url || "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
      },
    },
    doc: {
      querySelectorAll(selector) {
        assert.equal(selector, "script");
        return [{ textContent: scriptText }];
      },
    },
    fetch: fetchImpl,
  };
}

test("extractTranscriptFromPageContext succeeds for a captioned public YouTube page fixture", async () => {
  const context = createContext(
    captionedPageFixture,
    async (url) => {
      assert.match(String(url), /timedtext/);
      return {
        ok: true,
        status: 200,
        async text() {
          return captionedTranscriptFixture;
        },
      };
    },
  );

  const result = await transcriptDomain.extractTranscriptFromPageContext(context, {});
  assert.equal(result.success, true);
  assert.equal(result.videoId, "dQw4w9WgXcQ");
  assert.equal(result.title, "Public Captioned Demo");
  assert.equal(result.language, "en");
  assert.equal(result.method, "page-player-response");
  assert.match(result.transcript, /\[00:00\] Hello world/);
  assert.match(result.transcript, /\[00:03\] From captions/);
});

test("extractTranscriptFromPageContext returns no_captions when no caption tracks exist", async () => {
  const context = createContext(
    noCaptionsFixture,
    async (url) => {
      const value = String(url);
      if (value.includes("youtubei/v1/player")) {
        return {
          ok: true,
          status: 200,
          async json() {
            return {
              playabilityStatus: { status: "OK" },
              videoDetails: { title: "No Captions Demo" },
            };
          },
        };
      }

      return {
        ok: true,
        status: 200,
        async text() {
          return innertubeBootstrapFixture;
        },
      };
    },
  );

  const result = await transcriptDomain.extractTranscriptFromPageContext(context, {});
  assert.equal(result.success, false);
  assert.equal(result.code, "no_captions");
});

test("extractTranscriptFromPageContext falls back to Innertube after page player reports no captions", async () => {
  const innertubePlayerData = {
    playabilityStatus: { status: "OK" },
    captions: {
      playerCaptionsTracklistRenderer: {
        captionTracks: [
          {
            baseUrl: "https://www.youtube.com/api/timedtext?v=dQw4w9WgXcQ&lang=en",
            languageCode: "en",
          },
        ],
      },
    },
    videoDetails: {
      title: "Fallback Demo",
    },
  };

  const context = createContext(
    noCaptionsFixture,
    async (url, options = {}) => {
      const value = String(url);
      if (value.includes("youtubei/v1/player")) {
        assert.equal(options.method, "POST");
        return {
          ok: true,
          status: 200,
          async json() {
            return innertubePlayerData;
          },
        };
      }

      if (value.includes("timedtext")) {
        return {
          ok: true,
          status: 200,
          async text() {
            return captionedTranscriptFixture;
          },
        };
      }

      return {
        ok: true,
        status: 200,
        async text() {
          return innertubeBootstrapFixture;
        },
      };
    },
  );

  const result = await transcriptDomain.extractTranscriptFromPageContext(context, {});
  assert.equal(result.success, true);
  assert.equal(result.method, "page-innertube");
  assert.equal(result.title, "Fallback Demo");
  assert.match(result.transcript, /\[00:00\] Hello world/);
});

test("validateActiveTab rejects non-YouTube pages before any injection work", () => {
  const validation = backgroundService.validateActiveTab({
    url: "https://example.com/not-youtube",
  });

  assert.equal(validation.ok, false);
  assert.equal(validation.result.success, false);
  assert.equal(validation.result.code, "invalid_url");
});

test("normalizeTranscriptToAddPayload maps transcript success into the context engine contract", () => {
  const payload = normalizeDomain.normalizeTranscriptToAddPayload({
    success: true,
    videoId: "dQw4w9WgXcQ",
    title: "Public Captioned Demo",
    url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    transcript: "[00:00] Hello world",
    language: "en",
    isGenerated: false,
    method: "page-player-response",
  }, "video-notes");

  assert.deepEqual(payload, {
    text: "[00:00] Hello world",
    collection: "video-notes",
    source: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    source_type: "youtube_transcript",
    metadata: {
      videoId: "dQw4w9WgXcQ",
      title: "Public Captioned Demo",
      url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
      language: "en",
      isGenerated: false,
      method: "page-player-response",
    },
    tags: ["youtube-transcript"],
  });
});

test("addActiveTabTranscript preserves unauthorized add failures for popup auth recovery", async () => {
  const originalChrome = global.chrome;
  const originalFetch = global.fetch;

  global.chrome = {
    scripting: {
      async executeScript(options) {
        if (options.files) {
          return [];
        }

        return [{
          result: {
            success: true,
            videoId: "dQw4w9WgXcQ",
            title: "Public Captioned Demo",
            url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            transcript: "[00:00] Hello world",
            language: "en",
            isGenerated: false,
            method: "page-player-response",
          },
        }];
      },
    },
  };

  global.fetch = async () => ({
    ok: false,
    status: 401,
    async json() {
      return { detail: "Unauthorized" };
    },
  });

  try {
    const response = await backgroundService.addActiveTabTranscript({
      apiBase: "http://localhost:11811",
      authHeaders(extra = {}) {
        return extra;
      },
      collection: "video-notes",
      tabId: 123,
      url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    });

    assert.equal(response.success, false);
    assert.equal(response.authFailed, true);
    assert.equal(response.statusCode, 401);
    assert.equal(response.error, "Unauthorized");
    assert.equal(response.transcriptResult.code, "upstream_error");
  } finally {
    global.chrome = originalChrome;
    global.fetch = originalFetch;
  }
});
