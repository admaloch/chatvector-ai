import type { ChatSource, ChatStreamEvent, RetrievalDebugMetadata } from "@chatvector/sdk";

/**
 * Parse a fetch `ReadableStream` body from the application proxy's
 * `POST /api/chat/stream` route into SDK `ChatStreamEvent` values.
 */
export async function* iterProxyChatStreamEvents(
  stream: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<ChatStreamEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let eventName: string | null = null;
  let dataLines: string[] = [];

  const flushEvent = (): ChatStreamEvent | null => {
    if (eventName === null && dataLines.length === 0) {
      return null;
    }
    const event = mapProxySseEvent(eventName, dataLines.join("\n"));
    eventName = null;
    dataLines = [];
    return event;
  };

  try {
    while (true) {
      if (signal?.aborted) {
        break;
      }
      const { done, value } = await reader.read();
      if (done) {
        break;
      }

      buffer += decoder.decode(value, { stream: true });
      let newlineIndex = buffer.indexOf("\n");
      while (newlineIndex !== -1) {
        const line = buffer.slice(0, newlineIndex).replace(/\r$/, "");
        buffer = buffer.slice(newlineIndex + 1);
        newlineIndex = buffer.indexOf("\n");

        if (line === "") {
          const event = flushEvent();
          if (event !== null) {
            yield event;
          }
          continue;
        }

        if (line.startsWith("event:")) {
          eventName = line.slice("event:".length).trim() || null;
          continue;
        }

        if (line.startsWith("data:")) {
          dataLines.push(line.slice("data:".length).trimStart());
        }
      }
    }

    buffer += decoder.decode();
    if (buffer.length > 0) {
      const trailing = buffer.replace(/\r$/, "");
      if (trailing.startsWith("event:")) {
        eventName = trailing.slice("event:".length).trim() || null;
      } else if (trailing.startsWith("data:")) {
        dataLines.push(trailing.slice("data:".length).trimStart());
      }
    }

    const trailingEvent = flushEvent();
    if (trailingEvent !== null) {
      yield trailingEvent;
    }
  } finally {
    reader.releaseLock();
  }
}

function mapProxySseEvent(
  eventName: string | null,
  data: string,
): ChatStreamEvent | null {
  if (eventName === "done" || data === "[DONE]") {
    return null;
  }

  if (eventName === "error") {
    const payload = parseJsonObject(data);
    throw new ProxyStreamError(
      String(payload.code ?? "stream_error"),
      String(payload.message ?? "Streaming request failed"),
    );
  }

  if (eventName === "token") {
    const content = parseJsonString(data);
    if (typeof content !== "string") {
      return null;
    }
    return { type: "token", content };
  }

  if (eventName === "complete") {
    const payload = parseJsonObject(data);
    const retrievalDebug = mapRetrievalDebug(payload.retrieval_debug);
    return {
      type: "complete",
      sessionId:
        typeof payload.session_id === "string" ? payload.session_id : null,
      sources: mapSources(payload.sources),
      latencyMs: numberValue(payload.latency_ms),
      model: stringValue(payload.model),
      ...(retrievalDebug === undefined ? {} : { retrievalDebug }),
      _raw: payload,
    };
  }

  return null;
}

export class ProxyStreamError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "ProxyStreamError";
    this.code = code;
  }
}

export class StreamingDisabledError extends Error {
  constructor() {
    super("Streaming is disabled on the proxy or upstream API");
    this.name = "StreamingDisabledError";
  }
}

function parseJsonObject(data: string): Record<string, unknown> {
  try {
    const parsed: unknown = JSON.parse(data);
    return typeof parsed === "object" && parsed !== null
      ? (parsed as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}

function parseJsonString(data: string): unknown {
  try {
    return JSON.parse(data) as unknown;
  } catch {
    return data.length > 0 ? data : null;
  }
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function numberValue(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function mapSources(value: unknown): ChatSource[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .filter(
      (item): item is Record<string, unknown> =>
        typeof item === "object" && item !== null,
    )
    .map(mapSource);
}

function mapSource(raw: Record<string, unknown>): ChatSource {
  return {
    fileName:
      typeof raw.file_name === "string"
        ? raw.file_name
        : typeof raw.fileName === "string"
          ? raw.fileName
          : null,
    pageNumber:
      typeof raw.page_number === "number"
        ? raw.page_number
        : typeof raw.pageNumber === "number"
          ? raw.pageNumber
          : null,
    chunkIndex:
      typeof raw.chunk_index === "number"
        ? raw.chunk_index
        : typeof raw.chunkIndex === "number"
          ? raw.chunkIndex
          : null,
  };
}

function mapRetrievalDebug(
  value: unknown,
): RetrievalDebugMetadata | undefined {
  if (typeof value !== "object" || value === null) {
    return undefined;
  }
  return value as RetrievalDebugMetadata;
}
