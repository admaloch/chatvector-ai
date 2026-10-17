import { NextResponse } from "next/server";

export function requireAppUserId(request: Request): string | NextResponse {
  const userId = request.headers.get("x-user-id")?.trim();
  if (!userId) {
    return NextResponse.json(
      {
        error: {
          code: "application_auth_required",
          message: "Authenticate with the application before using this route",
        },
      },
      { status: 401 },
    );
  }
  return userId;
}
