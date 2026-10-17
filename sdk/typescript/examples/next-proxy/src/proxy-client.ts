import type {
  ChatResponse,
  ChatStreamEvent,
  DocumentStatus,
  RetrievalScope,
  Session,
} from "@chatvector/sdk";
import {
  iterProxyChatStreamEvents,
  ProxyStreamError,
  StreamingDisabledError,
} from "./internal/proxy-sse.js";

export type ProxyClientOptions = {
  /** Application origin serving `/api/*` routes (defaults to same-origin). */
  baseUrl?: string;
  /** Application user id sent as `x-user-id` (placeholder for your session/JWT). */
  getUserId?: () => string | null;
  fetchImpl?: typeof fetch;
};

export type ProxyChatRequest = {
  question: string;
  docId: string;
  sessionId?: string;
  matchCount?: number;
  scope?: RetrievalScope;
};

export type ProxyUploadResult = {
  documentId: string;
  status: string;
  createdAt?: string | null;
  updatedAt?: string | null;
};

export type ProxyApplicationError = {
  error: {
    code: string;
    message: string;
  };
};

/**
 * Browser-safe HTTP client that talks only to your application's `/api/*`
 * proxy routes. Never accepts or sends a ChatVector API key.
 */
export class ProxyClient {
  private readonly baseUrl: string;
  private readonly getUserId: () => string | null;
  private readonly fetchImpl: typeof fetch;

  constructor(options: ProxyClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? "").replace(/\/$/, "");
    this.getUserId = options.getUserId ?? (() => null);
    this.fetchImpl = options.fetchImpl ?? fetch.bind(globalThis);
  }

  async createSession(signal?: AbortSignal): Promise<Session> {
    const response = await this.requestJson<{ session: Session }>(
      "POST",
      "/api/sessions",
      { signal, json: {} },
    );
    return response.session;
  }

  async uploadDocument(
    file: File,
    options: { signal?: AbortSignal } = {},
  ): Promise<ProxyUploadResult> {
    const headers = this.applicationHeaders();
    const form = new FormData();
    form.append("file", file, file.name);

    const response = await this.fetchImpl(`${this.baseUrl}/api/documents`, {
      method: "POST",
      headers,
      body: form,
      signal: options.signal,
    });

    if (!response.ok) {
      throw await this.errorFromResponse(response);
    }

    const payload = (await response.json()) as {
      document: {
        id: string;
        status: string;
        createdAt?: string | null;
        updatedAt?: string | null;
      };
    };

    return {
      documentId: payload.document.id,
      status: payload.document.status,
      createdAt: payload.document.createdAt,
      updatedAt: payload.document.updatedAt,
    };
  }

  async getDocumentStatus(
    documentId: string,
    options: { signal?: AbortSignal } = {},
  ): Promise<DocumentStatus> {
    const response = await this.requestJson<{ document: DocumentStatus }>(
      "GET",
      `/api/documents/${encodeURIComponent(documentId)}`,
      { signal: options.signal },
    );
    return response.document;
  }

  async chat(
    request: ProxyChatRequest,
    options: { signal?: AbortSignal } = {},
  ): Promise<{ sessionId: string; response: ChatResponse }> {
    const response = await this.requestJson<{
      sessionId: string;
      response: ChatResponse;
    }>("POST", "/api/chat", {
      signal: options.signal,
      json: {
        question: request.question,
        docId: request.docId,
        sessionId: request.sessionId,
        matchCount: request.matchCount,
        scope: request.scope,
      },
    });
    return response;
  }

  async *streamChat(
    request: ProxyChatRequest,
    options: { signal?: AbortSignal } = {},
  ): AsyncGenerator<ChatStreamEvent> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...this.applicationHeaders(),
    };

    const response = await this.fetchImpl(`${this.baseUrl}/api/chat/stream`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        question: request.question,
        docId: request.docId,
        sessionId: request.sessionId,
        matchCount: request.matchCount,
        scope: request.scope,
      }),
      signal: options.signal,
    });

    if (response.status === 400) {
      try {
        const body = (await response.json()) as ProxyApplicationError;
        if (body.error?.code === "streaming_disabled") {
          throw new StreamingDisabledError();
        }
      } catch (error) {
        if (error instanceof StreamingDisabledError) {
          throw error;
        }
      }
    }

    if (!response.ok) {
      throw await this.errorFromResponse(response);
    }

    if (!response.body) {
      throw new ProxyStreamError("empty_body", "No response body for stream");
    }

    yield* iterProxyChatStreamEvents(response.body, options.signal);
  }

  documentStatusStreamUrl(documentId: string): string {
    return `${this.baseUrl}/api/documents/${encodeURIComponent(documentId)}/status/stream`;
  }

  private applicationHeaders(): Record<string, string> {
    const userId = this.getUserId()?.trim();
    if (!userId) {
      return {};
    }
    return { "x-user-id": userId };
  }

  private async requestJson<T>(
    method: string,
    path: string,
    options: { signal?: AbortSignal; json?: unknown } = {},
  ): Promise<T> {
    const headers: Record<string, string> = {
      ...this.applicationHeaders(),
    };
    const init: RequestInit = {
      method,
      headers,
      signal: options.signal,
    };
    if (options.json !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.json);
    }

    const response = await this.fetchImpl(`${this.baseUrl}${path}`, init);
    if (!response.ok) {
      throw await this.errorFromResponse(response);
    }
    return (await response.json()) as T;
  }

  private async errorFromResponse(response: Response): Promise<Error> {
    try {
      const body = (await response.json()) as ProxyApplicationError;
      if (body.error?.message) {
        return new Error(body.error.message);
      }
    } catch {
      /* fall through */
    }
    return new Error(`Request failed with status ${response.status}`);
  }
}

export { ProxyStreamError, StreamingDisabledError };
