import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ChatPage from "./page";
import { buildChatRequestBody } from "../lib/buildChatBody";

const mockSendMessageStream = vi.fn();

vi.mock("../lib/hooks/useSessionManager", () => ({
  useSessionManager: () => ({
    sessions: [
      {
        id: "session-a",
        createdAt: 1,
        displayLabel: "Test session",
      },
    ],
    activeSessionId: "session-a",
    createNewSession: vi.fn(),
    switchSession: vi.fn(),
    deleteSession: vi.fn(),
    isLoaded: true,
    error: null,
    isCreating: false,
    deletingSessionId: null,
    noteUserQuestion: vi.fn(),
    noteDocumentAttached: vi.fn(),
    noteSessionHistory: vi.fn(),
  }),
}));

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  return {
    ...actual,
    fetchRetrievalDiscovery: vi.fn().mockResolvedValue({
      presets: [],
      reranker_providers: ["similarity"],
    }),
    sendMessageStream: (...args: unknown[]) => mockSendMessageStream(...args),
    getSessionHistory: vi.fn().mockResolvedValue({ messages: [] }),
    getSession: vi.fn().mockResolvedValue({
      id: "session-a",
      created_at: "2026-01-01T00:00:00Z",
      last_active: "2026-01-01T00:00:00Z",
      metadata: {},
      document_ids: ["doc-1"],
    }),
    getDocumentStatus: vi.fn().mockResolvedValue({
      status: "completed",
      document_id: "doc-1",
      file_name: "notes.txt",
    }),
  };
});

vi.mock("../lib/documentStore", () => ({
  saveUploadedDocument: vi.fn(),
  removeUploadedDocument: vi.fn(),
  getUploadedDocument: vi.fn().mockReturnValue({
    fileName: "notes.txt",
    documentId: "doc-1",
    statusEndpoint: "/documents/doc-1/status",
  }),
}));

vi.mock("../lib/session", () => ({
  getSessionId: () => "test-session-id",
  setSessionId: vi.fn(),
}));

function wireBodyFromSendOptions(
  question: string,
  docId: string,
  options: Record<string, unknown>
) {
  return buildChatRequestBody({
    question,
    docId,
    sessionId: options.sessionId as string | null | undefined,
    debugRetrieval: options.debugRetrieval as boolean | undefined,
    retrieval:
      (options.retrieval as Parameters<typeof buildChatRequestBody>[0]["retrieval"]) ??
      { explicit: {} },
  });
}

describe("ChatPage advanced retrieval wire payload", () => {
  beforeEach(() => {
    sessionStorage.clear();
    HTMLElement.prototype.scrollIntoView = vi.fn();
    mockSendMessageStream.mockReset();
    mockSendMessageStream.mockImplementation(async function* () {
      yield { type: "token", text: "Answer" };
      yield {
        type: "complete",
        sources: [],
        latency_ms: 1,
        model: "m",
        _raw: {},
      };
    });
  });

  afterEach(() => {
    sessionStorage.clear();
  });

  it("omits retrieval overrides after opening the drawer without changing controls", async () => {
    const user = userEvent.setup();
    render(<ChatPage />);

    await waitFor(() =>
      expect(
        screen.getByPlaceholderText(/Ask about your document/i)
      ).not.toBeDisabled()
    );

    await user.click(screen.getByTestId("advanced-retrieval-open"));
    expect(screen.getByTestId("advanced-retrieval-drawer")).toBeInTheDocument();

    const input = screen.getByPlaceholderText(/Ask about your document/i);
    await user.type(input, "What is in the document?");
    await user.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => expect(mockSendMessageStream).toHaveBeenCalled());

    const [question, docId, options] = mockSendMessageStream.mock.calls[0] as [
      string,
      string,
      Record<string, unknown>,
    ];
    const wire = wireBodyFromSendOptions(question, docId, options);

    expect(wire.question).toBe("What is in the document?");
    expect(wire.doc_id).toBe("doc-1");
    expect(wire.session_id).toBe("session-a");
    expect(wire).not.toHaveProperty("scope");
    expect(wire).not.toHaveProperty("match_count");
    expect(wire).not.toHaveProperty("hybrid_enabled");
    expect(wire).not.toHaveProperty("query_transformation_strategy");
    expect(wire).not.toHaveProperty("history");
  });

  it("does not enable query transformation on the wire from strategy alone", async () => {
    sessionStorage.setItem(
      "chatvector-advanced-retrieval-explicit",
      JSON.stringify({
        explicit: { queryTransformationStrategy: "expand" },
      })
    );

    const user = userEvent.setup();
    render(<ChatPage />);

    await waitFor(() =>
      expect(
        screen.getByPlaceholderText(/Ask about your document/i)
      ).not.toBeDisabled()
    );

    await user.click(screen.getByTestId("advanced-retrieval-open"));

    const strategySelect = screen.getByRole("combobox", {
      name: /transform strategy/i,
    });
    expect(strategySelect).toBeDisabled();

    const input = screen.getByPlaceholderText(/Ask about your document/i);
    await user.type(input, "Transform?");
    await user.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => expect(mockSendMessageStream).toHaveBeenCalled());

    const [question, docId, options] = mockSendMessageStream.mock.calls[0] as [
      string,
      string,
      Record<string, unknown>,
    ];
    const wire = wireBodyFromSendOptions(question, docId, options);
    expect(wire).not.toHaveProperty("query_transformation_strategy");
    expect(wire).not.toHaveProperty("query_transformation_enabled");
  });
});
