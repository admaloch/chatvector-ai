import { NextResponse } from "next/server";
import { requireAppUserId } from "@/lib/app-auth";
import { chatVectorErrorResponse } from "@/lib/proxy-errors";
import { getChatVectorClient } from "@/lib/chatvector";

export async function POST(request: Request): Promise<NextResponse> {
  const userId = requireAppUserId(request);
  if (userId instanceof NextResponse) {
    return userId;
  }

  try {
    const formData = await request.formData();
    const file = formData.get("file");
    if (!(file instanceof File)) {
      return NextResponse.json(
        {
          error: {
            code: "file_required",
            message: "Multipart field 'file' is required",
          },
        },
        { status: 400 },
      );
    }

    const data = new Uint8Array(await file.arrayBuffer());
    const chatVector = getChatVectorClient();
    const uploaded = await chatVector.uploadDocument(
      {
        data,
        fileName: file.name,
        contentType: file.type || undefined,
      },
      { signal: request.signal },
    );
    const ready = await chatVector.waitForReady(uploaded.documentId, {
      signal: request.signal,
      timeoutMs: 60_000,
      pollIntervalMs: 2_000,
    });

    return NextResponse.json(
      {
        document: {
          id: uploaded.documentId,
          status: ready.status,
          createdAt: ready.createdAt,
          updatedAt: ready.updatedAt,
        },
      },
      { status: 201 },
    );
  } catch (error) {
    return chatVectorErrorResponse(error);
  }
}
