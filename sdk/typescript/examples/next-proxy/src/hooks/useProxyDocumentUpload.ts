"use client";

import { useCallback, useEffect, useState } from "react";
import type { DocumentStatus } from "@chatvector/sdk";
import type { ProxyClient } from "../proxy-client.js";

const TERMINAL_STATUSES = new Set(["completed", "failed"]);
const POLL_INTERVAL_MS = 2_500;

export type UseProxyDocumentUploadResult = {
  upload: (file: File) => Promise<string | undefined>;
  documentId: string | null;
  status: DocumentStatus | null;
  isUploading: boolean;
  isProcessing: boolean;
  error: string | null;
  reset: () => void;
};

function isTerminalStatus(status: string | undefined): boolean {
  return status !== undefined && TERMINAL_STATUSES.has(status);
}

/**
 * Uploads through the app proxy, then tracks ingestion with SSE when possible
 * and authenticated polling as a fallback (same strategy as the demo UI).
 */
export function useProxyDocumentUpload(
  client: ProxyClient,
): UseProxyDocumentUploadResult {
  const [documentId, setDocumentId] = useState<string | null>(null);
  const [status, setStatus] = useState<DocumentStatus | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [usePollingFallback, setUsePollingFallback] = useState(false);

  const upload = useCallback(
    async (file: File): Promise<string | undefined> => {
      setIsUploading(true);
      setError(null);
      setUsePollingFallback(false);
      try {
        const uploaded = await client.uploadDocument(file);
        setDocumentId(uploaded.documentId);
        setStatus({
          documentId: uploaded.documentId,
          status: uploaded.status,
          createdAt: uploaded.createdAt,
          updatedAt: uploaded.updatedAt,
        });
        setIsProcessing(!isTerminalStatus(uploaded.status));
        return uploaded.documentId;
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "Upload failed");
        return undefined;
      } finally {
        setIsUploading(false);
      }
    },
    [client],
  );

  const reset = useCallback(() => {
    setDocumentId(null);
    setStatus(null);
    setIsProcessing(false);
    setError(null);
    setUsePollingFallback(false);
  }, []);

  const activeDocId = documentId;
  const watchingStatus = Boolean(
    activeDocId && status && !isTerminalStatus(status.status),
  );

  useEffect(() => {
    if (!activeDocId || !watchingStatus) {
      return;
    }

    let cancelled = false;
    let eventSource: EventSource | null = null;
    let interval: ReturnType<typeof setInterval> | null = null;

    const applyStatus = (payload: DocumentStatus) => {
      if (cancelled) return;
      setStatus(payload);
      if (isTerminalStatus(payload.status)) {
        setIsProcessing(false);
      }
    };

    const startPolling = () => {
      const poll = async () => {
        if (cancelled) return;
        try {
          const payload = await client.getDocumentStatus(activeDocId);
          applyStatus(payload);
          if (isTerminalStatus(payload.status) && interval) {
            clearInterval(interval);
          }
        } catch (cause) {
          if (!cancelled) {
            setError(
              cause instanceof Error ? cause.message : "Status poll failed",
            );
          }
        }
      };
      void poll();
      interval = setInterval(poll, POLL_INTERVAL_MS);
    };

    if (
      !usePollingFallback &&
      typeof window !== "undefined" &&
      window.EventSource
    ) {
      eventSource = new EventSource(client.documentStatusStreamUrl(activeDocId));

      eventSource.addEventListener("status", (event) => {
        if (cancelled) return;
        try {
          const payload = JSON.parse(event.data) as Record<string, unknown>;
          applyStatus(mapDocumentStatusPayload(activeDocId, payload));
          if (isTerminalStatus(String(payload.status))) {
            eventSource?.close();
          }
        } catch {
          /* ignore malformed payloads */
        }
      });

      eventSource.addEventListener("error", () => {
        if (cancelled) return;
        eventSource?.close();
        setUsePollingFallback(true);
      });
    } else {
      startPolling();
    }

    return () => {
      cancelled = true;
      eventSource?.close();
      if (interval) {
        clearInterval(interval);
      }
    };
  }, [activeDocId, client, watchingStatus, usePollingFallback]);

  return {
    upload,
    documentId,
    status,
    isUploading,
    isProcessing: watchingStatus || isProcessing,
    error,
    reset,
  };
}

function mapDocumentStatusPayload(
  documentId: string,
  payload: Record<string, unknown>,
): DocumentStatus {
  return {
    documentId,
    status: String(payload.status ?? "processing"),
    chunks:
      typeof payload.chunks === "object" && payload.chunks !== null
        ? (payload.chunks as Record<string, unknown>)
        : undefined,
    createdAt:
      typeof payload.created_at === "string"
        ? payload.created_at
        : typeof payload.createdAt === "string"
          ? payload.createdAt
          : null,
    updatedAt:
      typeof payload.updated_at === "string"
        ? payload.updated_at
        : typeof payload.updatedAt === "string"
          ? payload.updatedAt
          : null,
    queuePosition:
      typeof payload.queue_position === "number"
        ? payload.queue_position
        : typeof payload.queuePosition === "number"
          ? payload.queuePosition
          : undefined,
    statusDetail:
      typeof payload.status_detail === "string"
        ? payload.status_detail
        : typeof payload.statusDetail === "string"
          ? payload.statusDetail
          : undefined,
  };
}
