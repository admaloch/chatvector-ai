import { describe, expect, it } from "vitest";

import { ChatVectorClient } from "../../src/index.js";
import { chatOkPayload } from "../fixtures/payloads.js";
import {
  captureRejection,
  createFetchMock,
  getFetchCall,
  getJsonBody,
  jsonResponse,
} from "../helpers/mock-fetch.js";

const DOCUMENT_ID = "550e8400-e29b-41d4-a716-446655440000";

function makeClient(fetch: typeof globalThis.fetch): ChatVectorClient {
  return new ChatVectorClient({
    baseUrl: "https://api.chatvector.test",
    apiKey: "test-key",
    fetch,
    retry: false,
  });
}

describe("retrieval and history config", () => {
  it("serializes nested retrieval and history objects", async () => {
    const fetch = createFetchMock(jsonResponse(chatOkPayload));
    await makeClient(fetch).chat({
      question: "Q",
      docId: DOCUMENT_ID,
      retrieval: {
        matchCount: 8,
        hybridEnabled: true,
        queryTransformationStrategy: "expand",
      },
      history: {
        retrievalHistoryWindow: 4,
        historyAwareRetrieval: false,
      },
    });

    expect(getJsonBody(getFetchCall(fetch).init)).toEqual({
      question: "Q",
      doc_id: DOCUMENT_ID,
      retrieval: {
        match_count: 8,
        hybrid_enabled: true,
        query_transformation_strategy: "expand",
      },
      history: {
        retrieval_history_window: 4,
        history_aware_retrieval: false,
      },
    });
  });

  it("omits empty config objects and undefined fields", async () => {
    const fetch = createFetchMock(jsonResponse(chatOkPayload));
    await makeClient(fetch).chat({
      question: "Q",
      docId: DOCUMENT_ID,
      retrieval: {},
      history: {},
    });

    const body = getJsonBody(getFetchCall(fetch).init);
    expect(body).not.toHaveProperty("retrieval");
    expect(body).not.toHaveProperty("history");
  });

  it("reuses the same config object across calls without mutation", async () => {
    const fetch = createFetchMock(
      jsonResponse(chatOkPayload),
      jsonResponse(chatOkPayload),
    );
    const client = makeClient(fetch);
    const retrieval = { hybridEnabled: true as const };

    await client.chat({ question: "Q", docId: DOCUMENT_ID, retrieval });
    await client.chat({ question: "Q", docId: DOCUMENT_ID, retrieval });

    expect(retrieval).toEqual({ hybridEnabled: true });
  });

  it("rejects matchCount=0 before fetch", async () => {
    const fetch = createFetchMock(jsonResponse(chatOkPayload));
    const error = await captureRejection(
      makeClient(fetch).chat({
        question: "Q",
        docId: DOCUMENT_ID,
        matchCount: 0,
      }),
    );
    expect(error).toBeInstanceOf(RangeError);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("chat without config objects matches prior optional-field behavior", async () => {
    const fetch = createFetchMock(jsonResponse(chatOkPayload));
    await makeClient(fetch).chat({
      question: chatOkPayload.question,
      docId: DOCUMENT_ID,
    });
    expect(getJsonBody(getFetchCall(fetch).init)).toEqual({
      question: chatOkPayload.question,
      doc_id: DOCUMENT_ID,
    });
  });
});
