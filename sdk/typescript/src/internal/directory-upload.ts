import { lstat, readdir, realpath } from "node:fs/promises";
import path from "node:path";

async function assertPathUnderRoot(root: string, candidate: string): Promise<void> {
  if (candidate.split(path.sep).includes("..")) {
    throw new Error(`Rejected path traversal in upload path: ${candidate}`);
  }
  const resolvedRoot = path.resolve(root);
  const resolvedCandidate = path.resolve(candidate);
  const relative = path.relative(resolvedRoot, resolvedCandidate);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error(`Rejected upload path outside directory root: ${candidate}`);
  }
}

function matchesPattern(fileName: string, pattern: string): boolean {
  if (pattern === "*") {
    return true;
  }
  if (pattern.startsWith("*.")) {
    return fileName.endsWith(pattern.slice(1));
  }
  return fileName === pattern;
}

async function walkDirectory(
  directory: string,
  root: string,
  pattern: string,
  recursive: boolean,
  matches: string[],
): Promise<void> {
  const entries = await readdir(directory, { withFileTypes: true });
  for (const entry of entries) {
    const fullPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      if (recursive) {
        await walkDirectory(fullPath, root, pattern, recursive, matches);
      }
      continue;
    }
    if (!matchesPattern(entry.name, pattern)) {
      continue;
    }
    const stat = await lstat(fullPath);
    if (stat.isSymbolicLink()) {
      const resolved = await realpath(fullPath);
      await assertPathUnderRoot(root, resolved);
      const targetStat = await lstat(resolved);
      if (!targetStat.isFile()) {
        continue;
      }
      matches.push(resolved);
      continue;
    }
    if (!stat.isFile()) {
      continue;
    }
    const resolved = await realpath(fullPath);
    await assertPathUnderRoot(root, resolved);
    matches.push(resolved);
  }
}

export async function collectDirectoryUploadPaths(
  directoryPath: string,
  options: { pattern?: string; recursive?: boolean } = {},
): Promise<string[]> {
  const pattern = options.pattern ?? "*";
  const recursive = options.recursive ?? true;
  if (directoryPath.split(path.sep).includes("..")) {
    throw new Error("directoryPath must not contain '..' path segments.");
  }
  let resolvedDirectory: string;
  try {
    resolvedDirectory = await realpath(path.resolve(directoryPath));
  } catch {
    throw new Error(`Upload directory was not found: ${directoryPath}`);
  }
  const directoryStat = await lstat(resolvedDirectory).catch(() => undefined);
  if (directoryStat === undefined || !directoryStat.isDirectory()) {
    throw new Error(`Upload directory was not found: ${directoryPath}`);
  }

  const matches: string[] = [];
  await walkDirectory(resolvedDirectory, resolvedDirectory, pattern, recursive, matches);
  return matches.sort();
}
