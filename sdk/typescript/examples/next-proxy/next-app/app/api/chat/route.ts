import { NextResponse } from "next/server";
import type { ChatRequest } from "@chatvector/sdk";
import { requireAppUserId } from "@/lib/app-auth";
import { chatVectorErrorResponse } from "@/lib/proxy-errors";
import { getChatVectorClient } from "@/lib/chatvector";

type ChatBody = {
  question?: string;
  docId?: string;
  sessionId?: string;
  matchCount?: number;
  scope?: "session" | "tenant";
};

export async function POST(request: Request): Promise<NextResponse> {
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

  const { question, docId, matchCount, scope } = body;
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
    const chatVector = getChatVectorClient();
    let sessionId = body.sessionId?.trim();
    if (!sessionId) {
      const session = await chatVector.createSession(undefined, {
        signal: request.signal,
      });
      sessionId = session.id;
    }

    const chatRequest: ChatRequest = {
      question: question.trim(),
      docId: docId.trim(),
      sessionId,
    };
    if (matchCount !== undefined) chatRequest.matchCount = matchCount;
    if (scope !== undefined) chatRequest.scope = scope;

    const response = await chatVector.chat(chatRequest, { signal: request.signal });
    return NextResponse.json({ sessionId, response });
  } catch (error) {
    return chatVectorErrorResponse(error);
  }
}
