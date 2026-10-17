import { describe, expect, it } from "vitest";

import { ChatVectorClient } from "../../src/index.js";
import { SESSION_ID } from "../fixtures/payloads.js";
import {
  createFetchMock,
  getFetchCall,
  jsonResponse,
} from "../helpers/mock-fetch.js";

function makeClient(fetch: typeof globalThis.fetch): ChatVectorClient {
  return new ChatVectorClient({
    baseUrl: "https://api.chatvector.test",
    apiKey: "test-key",
    fetch,
    retry: false,
  });
}

describe("feedback SDK", () => {
  it("postFeedback sends POST /feedback with snake_case body", async () => {
    const fetch = createFetchMock(
      jsonResponse({
        id: "fb-1",
        type: "answer",
        rating: 1,
        session_id: SESSION_ID,
        message_id: "msg-1",
        created_at: "2026-01-01T00:00:00Z",
      }),
    );
    const item = await makeClient(fetch).postFeedback({
      type: "answer",
      rating: 1,
      sessionId: SESSION_ID,
      messageId: "msg-1",
    });
    const call = getFetchCall(fetch);
    expect(call.url).toBe("https://api.chatvector.test/feedback");
    expect(call.init.method).toBe("POST");
    expect(JSON.parse(String(call.init.body))).toMatchObject({
      type: "answer",
      rating: 1,
      session_id: SESSION_ID,
      message_id: "msg-1",
    });
    expect(item.id).toBe("fb-1");
    expect(item.sessionId).toBe(SESSION_ID);
  });

  it("listFeedback passes query filters", async () => {
    const fetch = createFetchMock(
      jsonResponse({
        feedback: [
          {
            id: "fb-2",
            type: "citation",
            rating: -1,
            session_id: SESSION_ID,
            document_id: "doc-1",
            chunk_index: 0,
            created_at: "2026-01-02T00:00:00Z",
          },
        ],
      }),
    );
    const listed = await makeClient(fetch).listFeedback({
      sessionId: SESSION_ID,
      limit: 10,
    });
    expect(getFetchCall(fetch).url).toBe(
      `https://api.chatvector.test/feedback?session_id=${encodeURIComponent(SESSION_ID)}&limit=10`,
    );
    expect(listed.feedback[0]?.type).toBe("citation");
    expect(listed.feedback[0]?.chunkIndex).toBe(0);
  });
});
