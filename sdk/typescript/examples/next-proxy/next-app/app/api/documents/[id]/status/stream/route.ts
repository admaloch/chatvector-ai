import { NextResponse } from "next/server";
import { requireAppUserId } from "@/lib/app-auth";
import { chatVectorErrorResponse } from "@/lib/proxy-errors";

type RouteContext = { params: Promise<{ id: string }> };

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

export async function GET(
  request: Request,
  context: RouteContext,
): Promise<Response> {
  const userId = requireAppUserId(request);
  if (userId instanceof NextResponse) {
    return userId;
  }

  const { id } = await context.params;

  try {
    const headers: Record<string, string> = {
      Accept: "text/event-stream",
    };
    const apiKey = chatVectorApiKey();
    if (apiKey) {
      headers.Authorization = `Bearer ${apiKey}`;
    }

    const upstream = await fetch(
      `${chatVectorBaseUrl()}/documents/${encodeURIComponent(id)}/status/stream`,
      {
        method: "GET",
        headers,
        signal: request.signal,
      },
    );

    if (!upstream.ok || !upstream.body) {
      return NextResponse.json(
        {
          error: {
            code: "status_stream_failed",
            message: "Document status stream failed",
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
