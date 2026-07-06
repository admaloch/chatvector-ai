import { API_BASE, authHeaders } from "./api";

export type ComponentHealth = {
  status: "ok" | "error";
  latency_ms?: number;
  error?: string;
  cached?: boolean;
  checked_at?: string;
};

export type SystemStatus = {
  status: "healthy" | "degraded" | "unhealthy";
  components: {
    api: string;
    database: string;
    queue: string;
    embeddings: string;
    llm: string;
  };
  health_checks: {
    embedding: ComponentHealth;
    llm: ComponentHealth;
    redis?: ComponentHealth;
  };
  metrics: {
    document_queue: number;
    workers_active: number;
    memory_usage: number;
    documents_indexed: number | null;
    total_queries: number | null;
  };
  uptime: string;
  version: string;
};

export type StatusFetchErrorKind =
  | "network"
  | "unexpected_response"
  | "invalid_json"
  | "http_error";

export class StatusFetchError extends Error {
  readonly kind: StatusFetchErrorKind;
  readonly httpStatus?: number;
  readonly contentType?: string;

  constructor(
    message: string,
    kind: StatusFetchErrorKind,
    options?: { httpStatus?: number; contentType?: string }
  ) {
    super(message);
    this.name = "StatusFetchError";
    this.kind = kind;
    this.httpStatus = options?.httpStatus;
    this.contentType = options?.contentType;
  }
}

function mediaType(contentType: string | null): string {
  if (!contentType) return "unknown";
  return contentType.split(";")[0]?.trim().toLowerCase() || "unknown";
}

function isJsonMediaType(contentType: string | null): boolean {
  const type = mediaType(contentType);
  return type === "application/json" || type.endsWith("+json");
}

function looksLikeHtml(contentType: string | null, body: string): boolean {
  if (mediaType(contentType) === "text/html") return true;
  const trimmed = body.trimStart();
  return trimmed.startsWith("<") || trimmed.startsWith("<!");
}

function formatHttpDetail(httpStatus: number, contentType: string | null): string {
  return `HTTP ${httpStatus} · Expected JSON but received ${mediaType(contentType)}.`;
}

export async function getSystemStatus(): Promise<SystemStatus> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/status`, {
      method: "GET",
      headers: {
        Accept: "application/json",
        ...authHeaders(),
      },
      cache: "no-store",
    });
  } catch (err) {
    if (process.env.NODE_ENV === "development") {
      console.error("[status] Network request failed:", err);
    }
    throw new StatusFetchError(
      "Unable to connect to the ChatVector backend.\n\nCheck that the backend is running and the API URL is configured correctly.",
      "network"
    );
  }

  const contentType = res.headers.get("content-type");
  const body = await res.text();

  if (!body.trim()) {
    if (process.env.NODE_ENV === "development") {
      console.error("[status] Empty response body:", {
        status: res.status,
        contentType,
      });
    }
    throw new StatusFetchError(
      `The backend returned an empty response.\n\nHTTP ${res.status} · No response body.`,
      "unexpected_response",
      { httpStatus: res.status, contentType: contentType ?? undefined }
    );
  }

  if (looksLikeHtml(contentType, body)) {
    if (process.env.NODE_ENV === "development") {
      console.error("[status] Unexpected HTML response:", {
        status: res.status,
        contentType,
        bodyPreview: body.slice(0, 200),
      });
    }
    throw new StatusFetchError(
      `The backend returned an unexpected response.\n\n${formatHttpDetail(res.status, contentType)}`,
      "unexpected_response",
      { httpStatus: res.status, contentType: contentType ?? undefined }
    );
  }

  if (!isJsonMediaType(contentType)) {
    if (process.env.NODE_ENV === "development") {
      console.error("[status] Unexpected non-JSON response:", {
        status: res.status,
        contentType,
        bodyPreview: body.slice(0, 200),
      });
    }
    throw new StatusFetchError(
      `The backend returned an unexpected response.\n\n${formatHttpDetail(res.status, contentType)}`,
      "unexpected_response",
      { httpStatus: res.status, contentType: contentType ?? undefined }
    );
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch (err) {
    if (process.env.NODE_ENV === "development") {
      console.error("[status] Invalid JSON response:", {
        status: res.status,
        contentType,
        err,
        bodyPreview: body.slice(0, 200),
      });
    }
    throw new StatusFetchError(
      `The backend response could not be read.\n\nHTTP ${res.status} · The response was not valid JSON.`,
      "invalid_json",
      { httpStatus: res.status, contentType: contentType ?? undefined }
    );
  }

  if (!res.ok) {
    if (process.env.NODE_ENV === "development") {
      console.error("[status] Non-success HTTP status with JSON body:", {
        status: res.status,
        contentType,
        body: parsed,
      });
    }
    throw new StatusFetchError(
      `The backend returned an error response.\n\nHTTP ${res.status} · ${res.statusText || "Request failed"}.`,
      "http_error",
      { httpStatus: res.status, contentType: contentType ?? undefined }
    );
  }

  return parsed as SystemStatus;
}
