import {
  ChatVectorAPIError,
  ChatVectorRateLimitError,
  ChatVectorTimeoutError,
} from "@chatvector/sdk";
import { NextResponse } from "next/server";

export function chatVectorErrorResponse(error: unknown): NextResponse {
  if (error instanceof ChatVectorRateLimitError) {
    const headers: Record<string, string> = {};
    if (error.retryAfterMs !== undefined) {
      headers["Retry-After"] = String(Math.ceil(error.retryAfterMs / 1_000));
    }
    return NextResponse.json(
      {
        error: {
          code: error.code ?? "chatvector_rate_limited",
          message: "The document service is rate limited; try again later",
          retryAfterMs: error.retryAfterMs,
        },
      },
      { status: 429, headers },
    );
  }

  if (error instanceof ChatVectorAPIError) {
    const statusCode =
      error instanceof ChatVectorTimeoutError
        ? 504
        : error.kind === "auth"
          ? 502
          : error.statusCode && error.statusCode >= 400 && error.statusCode < 600
            ? error.statusCode
            : 502;

    return NextResponse.json(
      {
        error: {
          code: error.code ?? "chatvector_error",
          message:
            error instanceof ChatVectorTimeoutError
              ? "The document service timed out"
              : "The document service request failed",
        },
      },
      { status: statusCode },
    );
  }

  if (error instanceof Error && error.name === "AbortError") {
    return NextResponse.json(
      {
        error: {
          code: "request_cancelled",
          message: "Request cancelled",
        },
      },
      { status: 499 },
    );
  }

  return NextResponse.json(
    {
      error: {
        code: "internal_error",
        message: "Internal server error",
      },
    },
    { status: 500 },
  );
}
