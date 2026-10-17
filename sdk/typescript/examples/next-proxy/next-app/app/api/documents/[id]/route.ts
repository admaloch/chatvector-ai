import { NextResponse } from "next/server";
import { requireAppUserId } from "@/lib/app-auth";
import { chatVectorErrorResponse } from "@/lib/proxy-errors";
import { getChatVectorClient } from "@/lib/chatvector";

type RouteContext = { params: Promise<{ id: string }> };

export async function GET(
  request: Request,
  context: RouteContext,
): Promise<NextResponse> {
  const userId = requireAppUserId(request);
  if (userId instanceof NextResponse) {
    return userId;
  }

  const { id } = await context.params;

  try {
    const document = await getChatVectorClient().getDocumentStatus(id, {
      signal: request.signal,
    });

    return NextResponse.json({
      document: {
        documentId: document.documentId,
        status: document.status,
        chunks: document.chunks,
        createdAt: document.createdAt,
        updatedAt: document.updatedAt,
        queuePosition: document.queuePosition,
        statusDetail: document.statusDetail,
      },
    });
  } catch (error) {
    return chatVectorErrorResponse(error);
  }
}
