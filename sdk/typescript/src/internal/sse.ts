import {
  ChatVectorAPIError,
  ChatVectorAuthError,
  ChatVectorRateLimitError,
  ChatVectorTimeoutError,
} from "../errors.js";
import type { ChatSource, ChatStreamEvent } from "../models.js";
import { isRecord, stringValue } from "./utils.js";

const DONE_PAYLOAD = "[DONE]";

type StreamErrorPayload = {
  code: string;
  message: string;
  raw: Record<string, unknown>;
};

export function mapStreamError(error: StreamErrorPayload): ChatVectorAPIError {
  const message = error.message || "ChatVector streaming request failed.";
  const details = error.raw;

  if (error.code === "llm_missing_api_key" || error.code === "llm_invalid_api_key") {
    return new ChatVectorAuthError(message, { details });
  }
  if (error.code === "llm_rate_limited") {
    return new ChatVectorRateLimitError(message, { details });
  }
  if (error.code === "llm_timeout_or_connection") {
    return new ChatVectorTimeoutError(message, { details });
  }
  return new ChatVectorAPIError(message, {
    ...(error.code ? { code: error.code } : {}),
    details,
  });
}

export async function* iterChatStreamEvents(
  response: Response,
  signal?: AbortSignal,
): AsyncGenerator<ChatStreamEvent> {
  if (response.body === null) {
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let eventName: string | null = null;
  let dataLines: string[] = [];

  const dispatchBufferedEvent = (): ChatStreamEvent | null => {
    if (eventName === null && dataLines.length === 0) {
      return null;
    }
    const event = dispatchSseEvent(eventName, dataLines.join("\n"));
    eventName = null;
    dataLines = [];
    return event;
  };

  try {
    while (true) {
      if (signal?.aborted) {
        throw signal.reason ?? new DOMException("The operation was aborted", "AbortError");
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
          const event = dispatchBufferedEvent();
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
          dataLines.push(line.slice("data:".length).trim());
        }
      }
    }

    buffer += decoder.decode();
    if (buffer.length > 0) {
      const trailingLine = buffer.replace(/\r$/, "");
      if (trailingLine.startsWith("event:")) {
        eventName = trailingLine.slice("event:".length).trim() || null;
      } else if (trailingLine.startsWith("data:")) {
        dataLines.push(trailingLine.slice("data:".length).trim());
      }
    }

    const trailingEvent = dispatchBufferedEvent();
    if (trailingEvent !== null) {
      yield trailingEvent;
    }
  } finally {
    try {
      await reader.cancel();
    } catch {
      // Cancellation errors are not authoritative for callers.
    }
  }
}

function dispatchSseEvent(
  eventName: string | null,
  data: string,
): ChatStreamEvent | null {
  if (eventName === "done" || data === DONE_PAYLOAD) {
    return null;
  }

  if (eventName === "error") {
    const payload = parseJsonObject(data);
    throw mapStreamError(parseStreamErrorPayload(payload));
  }

  if (eventName === "token") {
    const content = JSON.parse(data) as unknown;
    if (typeof content !== "string") {
      throw new ChatVectorAPIError(
        "ChatVector returned an unexpected token event payload.",
        { details: content },
      );
    }
    return { type: "token", content };
  }

  if (eventName === "complete") {
    const payload = parseJsonObject(data);
    return {
      type: "complete",
      sessionId: nullableString(payload.session_id),
      sources: mapSources(payload.sources),
      latencyMs: numberValue(payload.latency_ms),
      model: stringValue(payload.model),
    };
  }

  throw new ChatVectorAPIError(
    "ChatVector returned an unexpected streaming event.",
    { details: { event: eventName, data } },
  );
}

function parseStreamErrorPayload(payload: Record<string, unknown>): StreamErrorPayload {
  return {
    code: stringValue(payload.code),
    message: stringValue(payload.message),
    raw: payload,
  };
}

function parseJsonObject(data: string): Record<string, unknown> {
  let payload: unknown;
  try {
    payload = JSON.parse(data) as unknown;
  } catch {
    throw new ChatVectorAPIError(
      "ChatVector returned a non-JSON streaming event payload.",
      { details: { data } },
    );
  }

  if (!isRecord(payload)) {
    throw new ChatVectorAPIError(
      "ChatVector returned an unexpected streaming event payload.",
      { details: payload },
    );
  }
  return payload;
}

function mapSources(value: unknown): ChatSource[] {
  return Array.isArray(value)
    ? value.filter(isRecord).map((source) => {
        const result: ChatSource = {
          fileName: nullableString(source.file_name),
          pageNumber: nullableNumber(source.page_number),
          chunkIndex: nullableNumber(source.chunk_index),
        };
        if (source.score === null || typeof source.score === "number") {
          result.score = source.score;
        }
        if (source.score_type === null || typeof source.score_type === "string") {
          result.scoreType = source.score_type;
        }
        return result;
      })
    : [];
}

function numberValue(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function nullableString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function nullableNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
