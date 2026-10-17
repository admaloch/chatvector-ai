import { mkdtemp, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChatVectorRateLimitError } from "../../src/errors.js";
import { runBulkUploads } from "../../src/internal/bulk-upload.js";
import { collectDirectoryUploadPaths } from "../../src/internal/directory-upload.js";
import { ChatVectorClient } from "../../src/client.js";
import { createFetchMock, jsonResponse } from "../helpers/mock-fetch.js";
import { uploadAcceptedPayload } from "../fixtures/payloads.js";

const BASE_URL = "https://api.chatvector.test";

function makeClient(fetch: ReturnType<typeof createFetchMock>) {
  return new ChatVectorClient({ baseUrl: BASE_URL, fetch });
}

describe("uploadMany", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("never exceeds the concurrency cap", async () => {
    let inFlight = 0;
    let maxInFlight = 0;
    const items = Array.from({ length: 12 }, (_, index) => ({
      data: new Uint8Array([index]),
      fileName: `file-${index}.txt`,
    }));
    const summary = await runBulkUploads(
      items,
      async () => {
        inFlight += 1;
        maxInFlight = Math.max(maxInFlight, inFlight);
        await new Promise((resolve) => {
          setTimeout(resolve, 25);
        });
        inFlight -= 1;
        return {
          documentId: "doc-test",
          status: "queued",
          _raw: uploadAcceptedPayload,
        };
      },
      async () => undefined,
      { concurrency: 3 },
    );

    expect(maxInFlight).toBeLessThanOrEqual(3);
    expect(summary.successCount).toBe(12);
  });

  it("continues after a per-file failure and reports the summary", async () => {
    const fetch = createFetchMock(
      jsonResponse(uploadAcceptedPayload, { status: 202 }),
      jsonResponse({ code: "queue_full", message: "full" }, { status: 503 }),
    );
    const summary = await makeClient(fetch).uploadMany([
      { data: new Uint8Array([1]), fileName: "a.txt" },
      { data: new Uint8Array([2]), fileName: "b.txt" },
    ]);

    expect(summary.successCount).toBe(1);
    expect(summary.failureCount).toBe(1);
    expect(summary.results[1]?.success).toBe(false);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("surfaces HTTP 429 per file without a retry storm", async () => {
    const fetch = createFetchMock(
      jsonResponse({ message: "slow down" }, { status: 429 }),
      jsonResponse({ message: "slow down" }, { status: 429 }),
    );
    const summary = await makeClient(fetch).uploadMany([
      { data: new Uint8Array([1]), fileName: "a.txt" },
      { data: new Uint8Array([2]), fileName: "b.txt" },
    ]);

    expect(fetch).toHaveBeenCalledTimes(2);
    expect(summary.failureCount).toBe(2);
    expect(summary.results[0]?.error).toBeInstanceOf(ChatVectorRateLimitError);
  });
});

describe("uploadDirectory", () => {
  it("rejects symlink escapes", async () => {
    const root = await mkdtemp(join(tmpdir(), "cv-bulk-"));
    const outside = join(tmpdir(), `cv-outside-${Date.now()}.txt`);
    await writeFile(outside, "secret");
    await writeFile(join(root, "inside.txt"), "ok");
    await symlink(outside, join(root, "escape.txt"));
    await expect(collectDirectoryUploadPaths(root)).rejects.toThrow(/outside directory root/);
    await rm(root, { recursive: true, force: true });
    await rm(outside, { force: true });
  });
});
