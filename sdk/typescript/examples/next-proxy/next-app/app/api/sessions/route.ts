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
    const session = await getChatVectorClient().createSession(undefined, {
      signal: request.signal,
    });
    return NextResponse.json({ session });
  } catch (error) {
    return chatVectorErrorResponse(error);
  }
}
