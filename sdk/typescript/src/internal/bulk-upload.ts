import {
  ChatVectorAPIError,
  ChatVectorRateLimitError,
} from "../errors.js";
import type {
  BulkUploadResult,
  BulkUploadSummary,
  DocumentResponse,
  UploadInput,
} from "../models.js";
import { throwIfAborted } from "./time.js";

export type UploadManyItem = UploadInput | string;

export type UploadManyWorkerOptions = {
  concurrency?: number;
  failFast?: boolean;
  retryOnRateLimit?: boolean;
  maxRateLimitRetries?: number;
  idempotencyKeyForPath?: (path: string) => string | undefined;
  onProgress?: (result: BulkUploadResult, summary: BulkUploadSummary) => void;
  waitForReady?: boolean;
  waitForReadyTimeoutMs?: number;
  waitForReadyPollIntervalMs?: number;
  signal?: AbortSignal;
};

function buildSummary(results: BulkUploadResult[]): BulkUploadSummary {
  const successCount = results.filter((item) => item.success).length;
  return {
    count: results.length,
    successCount,
    failureCount: results.length - successCount,
    results,
  };
}

function itemLabel(input: UploadManyItem): string {
  if (typeof input === "string") {
    return input;
  }
  if ("path" in input) {
    return input.path;
  }
  return input.fileName;
}

function normalizeInput(input: UploadManyItem): UploadInput {
  if (typeof input === "string") {
    return { path: input };
  }
  return input;
}

function rateLimitSleepMs(error: ChatVectorRateLimitError): number {
  if (error.retryAfterMs !== undefined && error.retryAfterMs > 0) {
    return Math.min(error.retryAfterMs, 60_000);
  }
  return 1_000;
}

async function uploadWithOptionalRateLimitRetry(
  upload: () => Promise<DocumentResponse>,
  options: {
    retryOnRateLimit: boolean;
    maxRateLimitRetries: number;
    signal?: AbortSignal;
  },
): Promise<DocumentResponse> {
  let attempts = 0;
  while (true) {
    throwIfAborted(options.signal);
    try {
      return await upload();
    } catch (error) {
      if (
        !(error instanceof ChatVectorRateLimitError) ||
        !options.retryOnRateLimit ||
        attempts >= options.maxRateLimitRetries
      ) {
        throw error;
      }
      attempts += 1;
      await new Promise((resolve) => {
        setTimeout(resolve, rateLimitSleepMs(error));
      });
    }
  }
}

class ConcurrencyGate {
  private inFlight = 0;
  private readonly queue: Array<() => void> = [];

  constructor(private readonly limit: number) {}

  async acquire(): Promise<void> {
    if (this.inFlight < this.limit) {
      this.inFlight += 1;
      return;
    }
    await new Promise<void>((resolve) => {
      this.queue.push(() => {
        this.inFlight += 1;
        resolve();
      });
    });
  }

  release(): void {
    this.inFlight -= 1;
    const next = this.queue.shift();
    if (next !== undefined) {
      next();
    }
  }

  get active(): number {
    return this.inFlight;
  }
}

export async function runBulkUploads(
  items: readonly UploadManyItem[],
  uploadDocument: (
    input: UploadInput,
    signal?: AbortSignal,
  ) => Promise<DocumentResponse>,
  waitForReady: (
    documentId: string,
    signal?: AbortSignal,
    timeoutMs?: number,
    pollIntervalMs?: number,
  ) => Promise<void>,
  options: UploadManyWorkerOptions = {},
): Promise<BulkUploadSummary> {
  const concurrency = options.concurrency ?? 5;
  if (concurrency < 1) {
    throw new RangeError("concurrency must be at least 1");
  }
  const maxRateLimitRetries = options.maxRateLimitRetries ?? 1;
  if (maxRateLimitRetries < 0) {
    throw new RangeError("maxRateLimitRetries must be non-negative");
  }

  if (items.length === 0) {
    return buildSummary([]);
  }

  const results: Array<BulkUploadResult | undefined> = new Array(items.length);
  let abort = false;
  const gate = new ConcurrencyGate(Math.min(concurrency, items.length));

  const materialize = (): BulkUploadResult[] =>
    results.map((item, index) =>
      item ??
      ({
        path: itemLabel(items[index]!),
        success: false,
        error: new ChatVectorAPIError("Bulk upload did not run."),
      } satisfies BulkUploadResult),
    );

  const runOne = async (index: number): Promise<void> => {
    const label = itemLabel(items[index]!);
    if (options.failFast && abort) {
      const skipped: BulkUploadResult = {
        path: label,
        success: false,
        error: new ChatVectorAPIError(
          "Bulk upload aborted after failFast failure.",
        ),
      };
      results[index] = skipped;
      options.onProgress?.(skipped, buildSummary(materialize()));
      return;
    }

    const normalized = normalizeInput(items[index]!);
    const idempotencyKey = options.idempotencyKeyForPath?.(label);
    const uploadInput: UploadInput =
      idempotencyKey === undefined
        ? normalized
        : { ...normalized, idempotencyKey };

    try {
      const retryOptions = {
        retryOnRateLimit: options.retryOnRateLimit ?? false,
        maxRateLimitRetries,
        ...(options.signal === undefined ? {} : { signal: options.signal }),
      };
      const document = await uploadWithOptionalRateLimitRetry(
        () => uploadDocument(uploadInput, options.signal),
        retryOptions,
      );
      if (options.waitForReady && document.documentId) {
        await waitForReady(
          document.documentId,
          options.signal,
          options.waitForReadyTimeoutMs,
          options.waitForReadyPollIntervalMs,
        );
      }
      const result: BulkUploadResult = {
        path: label,
        success: true,
        document,
      };
      results[index] = result;
      options.onProgress?.(result, buildSummary(materialize()));
    } catch (error) {
      const apiError =
        error instanceof ChatVectorAPIError
          ? error
          : new ChatVectorAPIError(
              error instanceof Error ? error.message : "Upload failed",
              { cause: error },
            );
      const result: BulkUploadResult = {
        path: label,
        success: false,
        error: apiError,
      };
      results[index] = result;
      if (options.failFast) {
        abort = true;
      }
      options.onProgress?.(result, buildSummary(materialize()));
    }
  };

  await Promise.all(
    items.map(async (_item, index) => {
      throwIfAborted(options.signal);
      if (options.failFast && abort) {
        await runOne(index);
        return;
      }
      await gate.acquire();
      try {
        await runOne(index);
      } finally {
        gate.release();
      }
    }),
  );

  return buildSummary(materialize());
}
