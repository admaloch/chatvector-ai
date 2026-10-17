import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProxyClient } from "../src/proxy-client.js";
import { useProxyChat } from "../src/hooks/useProxyChat.js";

function sseResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

describe("useProxyChat", () => {
  it("accumulates mocked streaming tokens from the proxy", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/chat/stream") && init?.method === "POST") {
        return sseResponse([
          'event: token\ndata: "Hello"\n\n',
          'event: token\ndata: " world"\n\n',
          'event: complete\ndata: {"session_id":"sess-1","sources":[],"latency_ms":12,"model":"test"}\n\n',
        ]);
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    const client = new ProxyClient({
      baseUrl: "http://app.test",
      getUserId: () => "user-1",
      fetchImpl,
    });

    const onSessionId = vi.fn();
    const { result } = renderHook(() =>
      useProxyChat({ client, onSessionId }),
    );

    await act(async () => {
      await result.current.send({
        question: "Hi",
        docId: "doc-1",
      });
    });

    await waitFor(() => {
      expect(result.current.isStreaming).toBe(false);
    });

    expect(result.current.answer).toBe("Hello world");
    expect(result.current.error).toBeNull();
    expect(onSessionId).toHaveBeenCalledWith("sess-1");
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(String(fetchImpl.mock.calls[0]?.[0])).toBe(
      "http://app.test/api/chat/stream",
    );
  });
});
