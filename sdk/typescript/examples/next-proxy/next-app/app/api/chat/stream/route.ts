import { NextResponse } from "next/server";
import { requireAppUserId } from "@/lib/app-auth";
import { chatVectorErrorResponse } from "@/lib/proxy-errors";

type ChatBody = {
  question?: string;
  docId?: string;
  sessionId?: string;
  matchCount?: number;
  scope?: "session" | "tenant";
};

function chatVectorBaseUrl(): string {
  const value = process.env.CHATVECTOR_BASE_URL?.trim();
  if (!value) {
    throw new Error("CHATVECTOR_BASE_URL must be set in the server environment");
  }
  return value.replace(/\/$/, "");
}

function chatVectorApiKey(): string {
  return process.env.CHATVECTOR_API_KEY?.trim() ?? "";
}

export async function POST(request: Request): Promise<Response> {
  const userId = requireAppUserId(request);
  if (userId instanceof NextResponse) {
    return userId;
  }

  let body: ChatBody;
  try {
    body = (await request.json()) as ChatBody;
  } catch {
    return NextResponse.json(
      {
        error: {
          code: "invalid_json",
          message: "Request body must be JSON",
        },
      },
      { status: 400 },
    );
  }

  const { question, docId, sessionId, matchCount, scope } = body;
  if (typeof question !== "string" || question.trim() === "") {
    return NextResponse.json(
      {
        error: {
          code: "question_required",
          message: "question is required",
        },
      },
      { status: 400 },
    );
  }
  if (typeof docId !== "string" || docId.trim() === "") {
    return NextResponse.json(
      {
        error: {
          code: "document_required",
          message: "docId is required",
        },
      },
      { status: 400 },
    );
  }

  try {
    const upstreamBody: Record<string, unknown> = {
      question: question.trim(),
      doc_id: docId.trim(),
    };
    if (sessionId?.trim()) upstreamBody.session_id = sessionId.trim();
    if (matchCount !== undefined) upstreamBody.match_count = matchCount;
    if (scope !== undefined) upstreamBody.scope = scope;

    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    };
    const apiKey = chatVectorApiKey();
    if (apiKey) {
      headers.Authorization = `Bearer ${apiKey}`;
    }

    const upstream = await fetch(`${chatVectorBaseUrl()}/chat/stream`, {
      method: "POST",
      headers,
      body: JSON.stringify(upstreamBody),
      signal: request.signal,
    });

    if (upstream.status === 400) {
      try {
        const payload = (await upstream.clone().json()) as {
          detail?: { code?: string };
          error?: { code?: string };
        };
        const code = payload.detail?.code ?? payload.error?.code;
        if (code === "streaming_disabled") {
          return NextResponse.json(
            {
              error: {
                code: "streaming_disabled",
                message: "Streaming is disabled upstream",
              },
            },
            { status: 400 },
          );
        }
      } catch {
        /* pass through raw body */
      }
    }

    if (!upstream.ok || !upstream.body) {
      return NextResponse.json(
        {
          error: {
            code: "stream_failed",
            message: "Chat stream failed",
          },
        },
        { status: upstream.status || 502 },
      );
    }

    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
      },
    });
  } catch (error) {
    return chatVectorErrorResponse(error);
  }
}
