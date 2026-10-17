"use client";

import { useCallback, useState } from "react";
import type { Session } from "@chatvector/sdk";
import type { ProxyClient } from "../proxy-client.js";

export type UseProxySessionManagerOptions = {
  /** Persist session id in `localStorage` under this key when set. */
  storageKey?: string;
  initialSessionId?: string | null;
};

function readStoredSessionId(storageKey: string | undefined): string | null {
  if (!storageKey || typeof window === "undefined") {
    return null;
  }
  try {
    return window.localStorage.getItem(storageKey);
  } catch {
    return null;
  }
}

function writeStoredSessionId(
  storageKey: string | undefined,
  sessionId: string | null,
): void {
  if (!storageKey || typeof window === "undefined") {
    return;
  }
  try {
    if (sessionId) {
      window.localStorage.setItem(storageKey, sessionId);
    } else {
      window.localStorage.removeItem(storageKey);
    }
  } catch {
    /* ignore quota / privacy mode */
  }
}

/**
 * Keeps a ChatVector session id on the client while delegating creation to the
 * application proxy (`POST /api/sessions`).
 */
export function useProxySessionManager(
  client: ProxyClient,
  options: UseProxySessionManagerOptions = {},
) {
  const { storageKey, initialSessionId = null } = options;
  const [sessionId, setSessionIdState] = useState<string | null>(() => {
    return initialSessionId ?? readStoredSessionId(storageKey);
  });
  const [isCreating, setIsCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const setSessionId = useCallback(
    (next: string | null) => {
      setSessionIdState(next);
      writeStoredSessionId(storageKey, next);
    },
    [storageKey],
  );

  const ensureSession = useCallback(
    async (signal?: AbortSignal): Promise<string> => {
      if (sessionId) {
        return sessionId;
      }
      setIsCreating(true);
      setError(null);
      try {
        const session: Session = await client.createSession(signal);
        setSessionId(session.id);
        return session.id;
      } catch (cause) {
        const message =
          cause instanceof Error ? cause.message : "Failed to create session";
        setError(message);
        throw cause;
      } finally {
        setIsCreating(false);
      }
    },
    [client, sessionId, setSessionId],
  );

  const resetSession = useCallback(() => {
    setSessionId(null);
    setError(null);
  }, [setSessionId]);

  return {
    sessionId,
    setSessionId,
    ensureSession,
    resetSession,
    isCreating,
    error,
  };
}
