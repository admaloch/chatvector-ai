import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useChat } from "./useChat";
import { DEFAULT_RETRIEVAL_SETTINGS } from "../retrievalSettings";
import { DocumentNotFoundError, StreamingDisabledError } from "../api";

const mockSendMessageStream = vi.fn();
const mockSendMessage = vi.fn();
const mockGetSessionHistory = vi.fn();
const mockGetSession = vi.fn();
const mockGetDocumentStatus = vi.fn();
const mockGetUploadedDocument = vi.fn();

vi.mock("../api", () => ({
  deleteDocument: vi.fn(),
  sendMessage: (...args: unknown[]) => mockSendMessage(...args),
  sendMessageStream: (...args: unknown[]) => mockSendMessageStream(...args),
  getSessionHistory: (...args: unknown[]) => mockGetSessionHistory(...args),
  getSession: (...args: unknown[]) => mockGetSession(...args),
  getDocumentStatus: (...args: unknown[]) => mockGetDocumentStatus(...args),
  DocumentNotFoundError: class DocumentNotFoundError extends Error {},
  ChatError: class ChatError extends Error {
    code = "unexpected";
  },
  StreamingDisabledError: class StreamingDisabledError extends Error {
    name = "StreamingDisabledError";
  },
}));

vi.mock("./useDocumentPolling", () => ({
  useDocumentPolling: () => ({
    status: "ready" as const,
    stage: "completed",
    completedStages: [],
    chunks: undefined,
    awaitingProcessing: false,
    processingTime: undefined,
    errorMessage: undefined,
    queuePosition: undefined,
  }),
}));

vi.mock("../documentStore", () => ({
  saveUploadedDocument: vi.fn(),
  removeUploadedDocument: vi.fn(),
  getUploadedDocument: (...args: unknown[]) => mockGetUploadedDocument(...args),
}));

const WELCOME_TEXT =
  "Hello! I'm ChatVector. Upload a document and I'll help you find answers from it.";
const DOCUMENT_GONE_NOTICE =
  "The document attached to this session is no longer available. You can upload another.";
const DOCUMENT_UNAVAILABLE_NOTICE =
  "The document attached to this session could not be checked. You can upload another.";
const OPAQUE_RESTORE_NOTICE =
  "Could not restore the session document. You can upload a new one.";

function sessionPayload(sessionId: string, documentIds: string[] = []) {
  return {
    id: sessionId,
    created_at: "2026-01-01T00:00:00Z",
    last_active: "2026-01-01T00:00:00Z",
    metadata: {},
    document_ids: documentIds,
  };
}

describe("useChat", () => {
  beforeEach(() => {
    mockSendMessageStream.mockReset();
    mockSendMessage.mockReset();
    mockGetSessionHistory.mockResolvedValue({ messages: [] });
    mockGetSession.mockResolvedValue(sessionPayload("session-a"));
    mockGetDocumentStatus.mockReset();
    mockGetUploadedDocument.mockReset();
    mockGetUploadedDocument.mockReturnValue(undefined);
  });

  it("ignores a second synchronous send while a stream is in flight", async () => {
    mockSendMessageStream.mockImplementation(async function* () {
      yield { type: "token", text: "Hello" };
      yield {
        type: "complete",
        sources: [],
        latency_ms: 10,
        model: "m",
        _raw: {},
      };
    });

    const { result } = renderHook(
      ({ sessionId }) => useChat(sessionId, DEFAULT_RETRIEVAL_SETTINGS),
      { initialProps: { sessionId: "session-a" } },
    );

    await waitFor(() => expect(result.current.historyLoading).toBe(false));

    act(() => {
      result.current.handleUploadAccepted({
        fileName: "guide.pdf",
        documentId: "doc-1",
        statusEndpoint: "/documents/doc-1/status",
      });
      result.current.setInput("First question");
    });

    await waitFor(() =>
      expect(result.current.attachment?.status).toBe("ready"),
    );

    await act(async () => {
      void result.current.handleSend();
      void result.current.handleSend();
    });

    expect(mockSendMessageStream).toHaveBeenCalledTimes(1);

    await waitFor(() => expect(result.current.inflight).toBe(false));
  });

  it("applies session history after switching sessions mid-flight", async () => {
    let releaseStream: (() => void) | undefined;
    const streamGate = new Promise<void>((resolve) => {
      releaseStream = resolve;
    });

    mockSendMessageStream.mockImplementation(async function* () {
      await streamGate;
      yield { type: "token", text: "stale" };
    });

    mockGetSessionHistory.mockImplementation(async (sessionId: string) => ({
      messages:
        sessionId === "session-b"
          ? [{ id: "m-1", role: "assistant", content: "History for B" }]
          : [],
    }));
    mockGetSession.mockImplementation(async (sessionId: string) => ({
      id: sessionId,
      created_at: "2026-01-01T00:00:00Z",
      last_active: "2026-01-01T00:00:00Z",
      metadata: {},
      document_ids: [],
    }));

    const { result, rerender } = renderHook(
      ({ sessionId }) => useChat(sessionId, DEFAULT_RETRIEVAL_SETTINGS),
      { initialProps: { sessionId: "session-a" } },
    );

    await waitFor(() => expect(result.current.historyLoading).toBe(false));

    act(() => {
      result.current.handleUploadAccepted({
        fileName: "guide.pdf",
        documentId: "doc-1",
        statusEndpoint: "/documents/doc-1/status",
      });
      result.current.setInput("Question");
    });

    await waitFor(() =>
      expect(result.current.attachment?.status).toBe("ready"),
    );

    await act(async () => {
      void result.current.handleSend();
    });

    rerender({ sessionId: "session-b" });

    await waitFor(() =>
      expect(result.current.messages.some((m) => m.text === "History for B")).toBe(
        true,
      ),
    );

    act(() => {
      releaseStream?.();
    });

    await waitFor(() => expect(result.current.inflight).toBe(false));
    expect(result.current.messages.some((m) => m.text.includes("stale"))).toBe(
      false,
    );
  });

  it("attaches retrieval_debug from stream complete _raw", async () => {
    mockSendMessageStream.mockImplementation(async function* () {
      yield {
        type: "complete",
        sources: [{ file_name: "a.pdf", page_number: 1, chunk_index: 0, score: 0.9 }],
        latency_ms: 120,
        model: "m",
        _raw: {
          retrieval_debug: { original_query: "What is RAG?" },
        },
      };
    });

    const { result } = renderHook(() =>
      useChat("session-a", DEFAULT_RETRIEVAL_SETTINGS),
    );

    await waitFor(() => expect(result.current.historyLoading).toBe(false));

    act(() => {
      result.current.handleUploadAccepted({
        fileName: "guide.pdf",
        documentId: "doc-1",
        statusEndpoint: "/documents/doc-1/status",
      });
      result.current.setInput("What is RAG?");
    });

    await waitFor(() =>
      expect(result.current.attachment?.status).toBe("ready"),
    );

    await act(async () => {
      await result.current.handleSend();
    });

    await waitFor(() =>
      expect(
        result.current.messages.some(
          (m) => m.retrieval_debug?.original_query === "What is RAG?",
        ),
      ).toBe(true),
    );
  });

  it("ignores sync fallback answer after session switch and keeps B history", async () => {
    let resolveSend:
      | ((value: {
          answer: string;
          question: string;
          chunks: number;
          sources: [];
          model: string;
        }) => void)
      | undefined;

    mockSendMessageStream.mockImplementation(async function* () {
      throw new StreamingDisabledError();
    });

    mockSendMessage.mockImplementation((_question, _docId, options) => {
      return new Promise((resolve, reject) => {
        const onAbort = () => {
          reject(new DOMException("Aborted", "AbortError"));
        };
        options?.signal?.addEventListener("abort", onAbort, { once: true });
        resolveSend = resolve;
      });
    });

    mockGetSessionHistory.mockImplementation(async (sessionId: string) => ({
      messages:
        sessionId === "session-b"
          ? [{ id: "m-1", role: "assistant", content: "History for B" }]
          : [],
    }));
    mockGetSession.mockImplementation(async (sessionId: string) => ({
      id: sessionId,
      created_at: "2026-01-01T00:00:00Z",
      last_active: "2026-01-01T00:00:00Z",
      metadata: {},
      document_ids: [],
    }));

    const { result, rerender } = renderHook(
      ({ sessionId }) => useChat(sessionId, DEFAULT_RETRIEVAL_SETTINGS),
      { initialProps: { sessionId: "session-a" } },
    );

    await waitFor(() => expect(result.current.historyLoading).toBe(false));

    act(() => {
      result.current.handleUploadAccepted({
        fileName: "guide.pdf",
        documentId: "doc-1",
        statusEndpoint: "/documents/doc-1/status",
      });
      result.current.setInput("Question");
    });

    await waitFor(() =>
      expect(result.current.attachment?.status).toBe("ready"),
    );

    await act(async () => {
      void result.current.handleSend();
    });

    rerender({ sessionId: "session-b" });

    await waitFor(() =>
      expect(result.current.messages.some((m) => m.text === "History for B")).toBe(
        true,
      ),
    );

    await act(async () => {
      resolveSend?.({
        answer: "Stale sync answer",
        question: "Question",
        chunks: 1,
        sources: [],
        model: "m",
      });
    });

    await waitFor(() => expect(result.current.inflight).toBe(false));
    expect(
      result.current.messages.some((m) => m.text === "Stale sync answer"),
    ).toBe(false);
    expect(
      result.current.messages.some((m) =>
        m.text.includes("Something went wrong"),
      ),
    ).toBe(false);
    expect(mockSendMessage).toHaveBeenCalledWith(
      "Question",
      "doc-1",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("does not apply buffered tokens from session A after switching to session B", async () => {
    let rafCallback: FrameRequestCallback | null = null;
    const rafSpy = vi
      .spyOn(window, "requestAnimationFrame")
      .mockImplementation((callback) => {
        rafCallback = callback;
        return 1;
      });
    const cancelSpy = vi
      .spyOn(window, "cancelAnimationFrame")
      .mockImplementation(() => {});

    let releaseFirstStream: (() => void) | undefined;
    const firstStreamGate = new Promise<void>((resolve) => {
      releaseFirstStream = resolve;
    });

    mockSendMessageStream
      .mockImplementationOnce(async function* () {
        await firstStreamGate;
        yield { type: "token", text: "stale-buffer" };
      })
      .mockImplementationOnce(async function* () {
        yield { type: "token", text: "fresh" };
        yield {
          type: "complete",
          sources: [],
          latency_ms: 1,
          model: "m",
          _raw: {},
        };
      });

    mockGetSessionHistory.mockImplementation(async (sessionId: string) => ({
      messages:
        sessionId === "session-b"
          ? [{ id: "m-1", role: "assistant", content: "History for B" }]
          : [],
    }));
    mockGetSession.mockImplementation(async (sessionId: string) => ({
      id: sessionId,
      created_at: "2026-01-01T00:00:00Z",
      last_active: "2026-01-01T00:00:00Z",
      metadata: {},
      document_ids: [],
    }));

    const { result, rerender } = renderHook(
      ({ sessionId }) => useChat(sessionId, DEFAULT_RETRIEVAL_SETTINGS),
      { initialProps: { sessionId: "session-a" } },
    );

    await waitFor(() => expect(result.current.historyLoading).toBe(false));

    act(() => {
      result.current.handleUploadAccepted({
        fileName: "guide.pdf",
        documentId: "doc-1",
        statusEndpoint: "/documents/doc-1/status",
      });
      result.current.setInput("Question A");
    });

    await waitFor(() =>
      expect(result.current.attachment?.status).toBe("ready"),
    );

    await act(async () => {
      void result.current.handleSend();
    });

    await act(async () => {
      releaseFirstStream?.();
    });

    await waitFor(() => expect(rafCallback).not.toBeNull());

    rerender({ sessionId: "session-b" });

    await waitFor(() =>
      expect(result.current.messages.some((m) => m.text === "History for B")).toBe(
        true,
      ),
    );

    act(() => {
      rafCallback?.(0);
    });

    expect(
      result.current.messages.some((m) => m.text.includes("stale-buffer")),
    ).toBe(false);

    act(() => {
      result.current.handleUploadAccepted({
        fileName: "guide.pdf",
        documentId: "doc-1",
        statusEndpoint: "/documents/doc-1/status",
      });
      result.current.setInput("Question B");
    });

    await waitFor(() =>
      expect(result.current.attachment?.status).toBe("ready"),
    );

    await act(async () => {
      await result.current.handleSend();
    });

    await waitFor(() =>
      expect(result.current.messages.some((m) => m.text.includes("fresh"))).toBe(
        true,
      ),
    );
    expect(
      result.current.messages.some((m) => m.text.includes("stale-buffer")),
    ).toBe(false);

    rafSpy.mockRestore();
    cancelSpy.mockRestore();
  });

  it("restores a missing document as an empty attachment and keeps history", async () => {
    mockGetSessionHistory.mockResolvedValue({
      messages: [
        { id: "m-1", role: "user", content: "Where is the appendix?" },
        { id: "m-2", role: "assistant", content: "Page 4." },
      ],
    });
    mockGetSession.mockResolvedValue(sessionPayload("session-a", ["doc-missing"]));
    mockGetDocumentStatus.mockRejectedValue(new DocumentNotFoundError("missing"));

    const { result } = renderHook(() =>
      useChat("session-a", DEFAULT_RETRIEVAL_SETTINGS),
    );

    await waitFor(() => expect(result.current.historyLoading).toBe(false));

    expect(result.current.attachment).toBeNull();
    expect(result.current.sessionNotice).toBe(DOCUMENT_GONE_NOTICE);
    expect(result.current.messages.map((message) => message.text)).toEqual([
      "Where is the appendix?",
      "Page 4.",
    ]);
    expect(result.current.messages.some((message) => message.text === WELCOME_TEXT)).toBe(
      false,
    );
    expect(result.current.inflight).toBe(false);
    expect(mockGetDocumentStatus).toHaveBeenCalledWith("/documents/doc-missing/status");
  });

  it.each([
    ["non-404 status error", new Error("Status check failed: 500")],
    ["network error", new TypeError("Failed to fetch")],
  ])(
    "restores an unfetchable document (%s) without the opaque restore sentence",
    async (_label, error) => {
      mockGetSessionHistory.mockResolvedValue({
        messages: [{ id: "m-1", role: "user", content: "Earlier question" }],
      });
      mockGetSession.mockResolvedValue(sessionPayload("session-a", ["doc-down"]));
      mockGetDocumentStatus.mockRejectedValue(error);

      const { result } = renderHook(() =>
        useChat("session-a", DEFAULT_RETRIEVAL_SETTINGS),
      );

      await waitFor(() => expect(result.current.historyLoading).toBe(false));

      expect(result.current.attachment).toBeNull();
      expect(result.current.sessionNotice).toBe(DOCUMENT_UNAVAILABLE_NOTICE);
      expect(result.current.sessionNotice).not.toBe(OPAQUE_RESTORE_NOTICE);
      expect(result.current.messages.map((message) => message.text)).toEqual([
        "Earlier question",
      ]);
      expect(result.current.inflight).toBe(false);
    },
  );
});
