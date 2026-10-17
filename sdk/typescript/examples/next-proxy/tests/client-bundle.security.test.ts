import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const exampleRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const clientRoot = path.join(exampleRoot, "src");

const FORBIDDEN_PATTERNS = [
  /CHATVECTOR_API_KEY/,
  /ChatVectorClient/,
  /process\.env\.(?:CHATVECTOR|NEXT_PUBLIC_CHATVECTOR)/,
];

const RUNTIME_SDK_IMPORT =
  /import\s+(?!type\s)(?:[\w*{}\s,]+\s+from\s+)?["']@chatvector\/sdk["']/;

function collectSourceFiles(dir: string): string[] {
  const entries = readdirSync(dir, { withFileTypes: true });
  const files: string[] = [];
  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...collectSourceFiles(fullPath));
      continue;
    }
    if (/\.(ts|tsx)$/.test(entry.name)) {
      files.push(fullPath);
    }
  }
  return files;
}

describe("client bundle safety", () => {
  it("does not reference ChatVector server credentials or runtime SDK imports", () => {
    const files = collectSourceFiles(clientRoot);
    expect(files.length).toBeGreaterThan(0);

    for (const file of files) {
      const source = readFileSync(file, "utf8");
      const relative = path.relative(exampleRoot, file);

      for (const pattern of FORBIDDEN_PATTERNS) {
        expect(source, `${relative} must not match ${pattern}`).not.toMatch(
          pattern,
        );
      }

      expect(
        source,
        `${relative} must not runtime-import @chatvector/sdk`,
      ).not.toMatch(RUNTIME_SDK_IMPORT);
    }
  });
});
