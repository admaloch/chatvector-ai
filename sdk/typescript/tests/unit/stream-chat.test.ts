import { describe, expect, it } from "vitest";

import {
  ChatVectorAPIError,
  ChatVectorAuthError,
  ChatVectorClient,
  ChatVectorRateLimitError,
  ChatVectorTimeoutError,
} from "../../src/index.js";
import { DOCUMENT_ID } from "../fixtures/payloads.js";
import {
  captureRejection,
  createFetchMock,
  flushAsyncWork,
  getFetchCall,
  getJsonBody,
  jsonResponse,
  sseResponse,
} from "../helpers/mock-fetch.js";

function makeClient(fetch: typeof globalThis.fetch): ChatVectorClient {
  return new ChatVectorClient({
    baseUrl: "https://api.chatvector.test",
    apiKey: "test-key",
    fetch,
    retry: false,
  });
}

const successSse = [
  'event: token',
  'data: "Hello"',
  "",
  'event: token',
  'data: " world"',
  "",
  "event: complete",
  'data: {"session_id":"session-1","sources":[{"file_name":"guide.pdf","page_number":1,"chunk_index":0,"score":0.9,"score_type":"vector"}],"latency_ms":120,"model":"gpt-test"}',
  "",
  "event: done",
  "data: [DONE]",
  "",
].join("\n");

describe("streamChat", () => {
  it("posts to /chat/stream and parses token and complete events", async () => {
    const fetch = createFetchMock(sseResponse(successSse));
    const events = [];
    for await (const event of makeClient(fetch).streamChat({
      question: "Summarize this",
      docId: DOCUMENT_ID,
      matchCount: 4,
      sessionId: "session-1",
      scope: "tenant",
    })) {
      events.push(event);
    }

    const { url, init } = getFetchCall(fetch);
    expect(url).toBe("https://api.chatvector.test/chat/stream");
    expect(init.method).toBe("POST");
    expect(getJsonBody(init)).toEqual({
      question: "Summarize this",
      doc_id: DOCUMENT_ID,
      match_count: 4,
      session_id: "session-1",
      scope: "tenant",
    });
    expect(events).toEqual([
      { type: "token", content: "Hello" },
      { type: "token", content: " world" },
      {
        type: "complete",
        sessionId: "session-1",
        sources: [
          {
            fileName: "guide.pdf",
            pageNumber: 1,
            chunkIndex: 0,
            score: 0.9,
            scoreType: "vector",
          },
        ],
        latencyMs: 120,
        model: "gpt-test",
      },
    ]);
  });

  it("ignores legacy done events", async () => {
    const fetch = createFetchMock(
      sseResponse(['event: done', "data: [DONE]", ""].join("\n")),
    );
    const events = [];
    for await (const event of makeClient(fetch).streamChat({
      question: "Q",
      docId: DOCUMENT_ID,
    })) {
      events.push(event);
    }
    expect(events).toEqual([]);
  });

  it("maps structured stream errors to typed SDK errors", async () => {
    const fetch = createFetchMock(
      sseResponse(
        [
          'event: error',
          'data: {"type":"error","code":"llm_rate_limited","message":"Too many requests"}',
          "",
        ].join("\n"),
      ),
    );
    const error = await captureRejection(
      (async () => {
        for await (const _event of makeClient(fetch).streamChat({
          question: "Q",
          docId: DOCUMENT_ID,
        })) {
          // drain
        }
      })(),
    );
    expect(error).toBeInstanceOf(ChatVectorRateLimitError);
  });

  it.each([
    ["llm_missing_api_key", ChatVectorAuthError],
    ["llm_invalid_api_key", ChatVectorAuthError],
    ["llm_timeout_or_connection", ChatVectorTimeoutError],
    ["provider_failed", ChatVectorAPIError],
  ])("maps stream error code %s", async (code, ErrorClass) => {
    const fetch = createFetchMock(
      sseResponse(
        [
          "event: error",
          `data: {"type":"error","code":"${code}","message":"Stream failed"}`,
          "",
        ].join("\n"),
      ),
    );
    const error = await captureRejection(
      (async () => {
        for await (const _event of makeClient(fetch).streamChat({
          question: "Q",
          docId: DOCUMENT_ID,
        })) {
          // drain
        }
      })(),
    );
    expect(error).toBeInstanceOf(ErrorClass);
  });

  it("raises HTTP errors before bytes are consumed", async () => {
    const fetch = createFetchMock(
      jsonResponse({ detail: "Streaming disabled" }, { status: 400 }),
    );
    const error = await captureRejection(
      (async () => {
        for await (const _event of makeClient(fetch).streamChat({
          question: "Q",
          docId: DOCUMENT_ID,
        })) {
          // drain
        }
      })(),
    );
    expect(error).toBeInstanceOf(ChatVectorAPIError);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("does not replay a retryable response because streaming chat is mutating", async () => {
    const fetch = createFetchMock(
      jsonResponse({ detail: "busy" }, { status: 503 }),
    );
    await captureRejection(
      (async () => {
        for await (const _event of new ChatVectorClient({
          baseUrl: "https://api.chatvector.test",
          fetch,
          retry: { maxRetries: 5 },
        }).streamChat({ question: "Q", docId: DOCUMENT_ID })) {
          // drain
        }
      })(),
    );
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("aborts mid-stream without making another request", async () => {
    const controller = new AbortController();
    const encoder = new TextEncoder();
    const fetch = createFetchMock((_input, init) => {
      const signal = init?.signal;
      const firstChunk = encoder.encode('event: token\ndata: "partial"\n\n');
      const stream = new ReadableStream<Uint8Array>({
        start(streamController) {
          streamController.enqueue(firstChunk);
          if (signal === undefined) {
            streamController.close();
            return;
          }
          const fail = (): void => {
            streamController.error(
              "reason" in signal
                ? signal.reason
                : new DOMException("The operation was aborted", "AbortError"),
            );
          };
          if (signal.aborted) {
            fail();
            return;
          }
          signal.addEventListener("abort", fail, { once: true });
        },
      });
      return new Response(stream, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      });
    });

    const iterator = makeClient(fetch).streamChat(
      { question: "Q", docId: DOCUMENT_ID },
      { signal: controller.signal },
    )[Symbol.asyncIterator]();

    const first = await iterator.next();
    expect(first.value).toEqual({ type: "token", content: "partial" });
    controller.abort(new DOMException("Caller disconnected", "AbortError"));

    const error = await captureRejection(iterator.next());
    expect(error).toBeInstanceOf(DOMException);
    expect(fetch).toHaveBeenCalledTimes(1);
    await flushAsyncWork();
  });
});
