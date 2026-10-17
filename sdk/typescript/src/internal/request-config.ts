import type { HistoryConfig, RetrievalConfig } from "../models.js";

const MATCH_COUNT_MIN = 1;
const MATCH_COUNT_MAX = 20;

export function validateMatchCount(value: number): void {
  if (value < MATCH_COUNT_MIN || value > MATCH_COUNT_MAX) {
    throw new RangeError(
      `Invalid matchCount=${value}. Expected an integer from ${MATCH_COUNT_MIN} to ${MATCH_COUNT_MAX}.`,
    );
  }
}

export function serializeRetrievalConfig(
  config: RetrievalConfig,
): Record<string, unknown> | undefined {
  const payload: Record<string, unknown> = {};
  if (config.matchCount !== undefined) {
    validateMatchCount(config.matchCount);
    payload.match_count = config.matchCount;
  }
  if (config.scope !== undefined) {
    payload.scope = config.scope;
  }
  if (config.hybridEnabled !== undefined) {
    payload.hybrid_enabled = config.hybridEnabled;
  }
  if (config.rerankingEnabled !== undefined) {
    payload.reranking_enabled = config.rerankingEnabled;
  }
  if (config.rerankerProvider !== undefined) {
    payload.reranker_provider = config.rerankerProvider;
  }
  if (config.queryTransformationEnabled !== undefined) {
    payload.query_transformation_enabled = config.queryTransformationEnabled;
  }
  if (config.queryTransformationStrategy !== undefined) {
    payload.query_transformation_strategy = config.queryTransformationStrategy;
  }
  if (config.skipHistoryTransform !== undefined) {
    payload.skip_history_transform = config.skipHistoryTransform;
  }
  if (config.preset !== undefined) {
    payload.preset = config.preset;
  }
  return Object.keys(payload).length > 0 ? payload : undefined;
}

export function serializeHistoryConfig(
  config: HistoryConfig,
): Record<string, unknown> | undefined {
  const payload: Record<string, unknown> = {};
  if (config.retrievalHistoryWindow !== undefined) {
    payload.retrieval_history_window = config.retrievalHistoryWindow;
  }
  if (config.generationHistoryWindow !== undefined) {
    payload.generation_history_window = config.generationHistoryWindow;
  }
  if (config.historyAwareRetrieval !== undefined) {
    payload.history_aware_retrieval = config.historyAwareRetrieval;
  }
  if (config.maxGenerationHistoryChars !== undefined) {
    payload.max_generation_history_chars = config.maxGenerationHistoryChars;
  }
  return Object.keys(payload).length > 0 ? payload : undefined;
}

export function applyRequestConfigFields(
  body: Record<string, unknown>,
  retrieval?: RetrievalConfig,
  history?: HistoryConfig,
): void {
  if (retrieval !== undefined) {
    const nested = serializeRetrievalConfig(retrieval);
    if (nested !== undefined) {
      body.retrieval = nested;
    }
  }
  if (history !== undefined) {
    const nested = serializeHistoryConfig(history);
    if (nested !== undefined) {
      body.history = nested;
    }
  }
}
