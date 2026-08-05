"use client";

import { useEffect, useMemo, useState } from "react";
import { Layers, Loader2, FileText } from "lucide-react";
import {
  sendBatchMessage,
  sendSynthesizedBatchMessage,
  ChatError,
  type BatchResultItem,
} from "../lib/api";
import { BatchResultCard } from "../components/batch/BatchResultCard";
import BatchPageSkeleton from "../components/batch/BatchPageSkeleton";
import RetrievalSettingsPanel from "../components/RetrievalSettingsPanel";
import BatchResultSkeleton from "./BatchResultSkeleton";
import { EmptyState } from "../components/ui/EmptyState";
import { InlineAlert } from "../components/ui/InlineAlert";
import { SegmentedControl } from "../components/ui/SegmentedControl";
import { getUploadedDocuments, type StoredDocument } from "../lib/documentStore";
import { useRetrievalSettings } from "../lib/hooks/useRetrievalSettings";

type BatchMode = "compare" | "synthesize";

const BATCH_MODE_OPTIONS: { value: BatchMode; label: string }[] = [
  { value: "compare", label: "Compare" },
  { value: "synthesize", label: "Synthesize" },
];

export default function BatchPage() {
  const [documents, setDocuments] = useState<StoredDocument[]>([]);
  const [documentsLoaded, setDocumentsLoaded] = useState(false);
  const { settings, setScope, setMatchCount, loaded: retrievalLoaded } = useRetrievalSettings();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [mode, setMode] = useState<BatchMode>("compare");
  const [question, setQuestion] = useState("");
  const [inflight, setInflight] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<BatchResultItem[] | null>(null);
  const [summary, setSummary] = useState<{
    count: number;
    success: number;
    failure: number;
  } | null>(null);

  useEffect(() => {
    setDocuments(getUploadedDocuments());
    setDocumentsLoaded(true);
  }, []);

  const nameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const doc of documents) map.set(doc.documentId, doc.fileName);
    return map;
  }, [documents]);

  const selectedDocIds = useMemo(
    () => documents.map((d) => d.documentId).filter((id) => selected.has(id)),
    [documents, selected]
  );

  const synthesizeTitle = useMemo(() => {
    if (selectedDocIds.length === 1) {
      const docId = selectedDocIds[0];
      return nameById.get(docId) ?? docId;
    }
    return `Across ${selectedDocIds.length} documents`;
  }, [selectedDocIds, nameById]);

  const toggle = (documentId: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(documentId)) next.delete(documentId);
      else next.add(documentId);
      return next;
    });
  };

  const canSubmit = question.trim().length > 0 && selected.size > 0 && !inflight;

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setError(null);
    setResults(null);
    setSummary(null);
    setInflight(true);
    try {
      const response =
        mode === "compare"
          ? await sendBatchMessage(question.trim(), selectedDocIds, {
              matchCount: settings.matchCount,
              scope: settings.scope,
            })
          : await sendSynthesizedBatchMessage(question.trim(), selectedDocIds, {
              matchCount: settings.matchCount,
              scope: settings.scope,
            });
      setResults(response.results);
      setSummary({
        count: response.count,
        success: response.success_count,
        failure: response.failure_count,
      });
    } catch (e) {
      setError(
        e instanceof ChatError ? e.message : "Something went wrong. Please try again."
      );
    } finally {
      setInflight(false);
    }
  };

  return (
    <div
      className="mx-auto w-full max-w-4xl px-4 py-10 text-foreground"
      aria-busy={!documentsLoaded || !retrievalLoaded}
    >
      <div className="mb-8">
        <div className="flex items-center gap-2 text-accent">
          <Layers size={20} />
          <span className="text-sm font-medium uppercase tracking-wide">
            Batch Query
          </span>
        </div>
        <h1 className="mt-2 text-3xl font-bold">Ask one question across many documents</h1>
        <p className="mt-2 max-w-2xl text-muted">
          Select documents you&apos;ve uploaded in the chat, enter a single
          question, and choose how ChatVector should answer.
        </p>
      </div>

      {!documentsLoaded || !retrievalLoaded ? (
        <BatchPageSkeleton />
      ) : documents.length === 0 ? (
        <EmptyState
          icon={FileText}
          title="No documents yet."
          description="Upload a document on the chat page first — it'll show up here automatically."
          action={{ href: "/chat", label: "Go to chat" }}
        />
      ) : (
        <div className="flex flex-col gap-6">
          <div>
            <p className="mb-2 text-sm font-medium">Mode</p>
            <SegmentedControl
              name="batch-mode"
              ariaLabel="Batch query mode"
              value={mode}
              onChange={setMode}
              options={BATCH_MODE_OPTIONS}
            />
            <p className="mt-2 max-w-2xl text-sm text-muted">
              {mode === "compare" ? (
                <>
                  <strong className="text-foreground">Compare</strong> sends one
                  query per document and shows a separate answer card for each —
                  useful for seeing what each file contributes. Each document is
                  answered independently from its own retrieved content; prior
                  chat or batch turns in this session are not used.
                </>
              ) : (
                <>
                  <strong className="text-foreground">Synthesize</strong> sends
                  one query across all selected documents and returns a single
                  combined answer with citations from every contributing file —
                  best for cross-document questions.
                </>
              )}
            </p>
          </div>

          <div>
            <label
              htmlFor="batch-question"
              className="mb-2 block text-sm font-medium"
            >
              Question
            </label>
            <textarea
              id="batch-question"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              rows={3}
              placeholder={
                mode === "synthesize"
                  ? "e.g. What's the expense process for visiting Apex Manufacturing, and are there known dashboard bugs?"
                  : "e.g. What are the key takeaways?"
              }
              className="w-full resize-y rounded-lg border border-border bg-surface px-4 py-3 text-base text-foreground outline-none focus:border-accent"
            />
          </div>

          <div>
            <p className="mb-2 text-sm font-medium">
              Documents{" "}
              <span className="font-normal text-muted">
                ({selected.size} selected)
              </span>
            </p>
            <ul className="flex flex-col gap-2">
              {documents.map((doc) => (
                <li key={doc.documentId}>
                  <label className="flex cursor-pointer items-center gap-3 rounded-lg border border-border bg-surface px-4 py-3 transition-colors hover:border-accent">
                    <input
                      type="checkbox"
                      checked={selected.has(doc.documentId)}
                      onChange={() => toggle(doc.documentId)}
                      className="h-4 w-4 accent-[color:var(--accent)]"
                    />
                    <FileText size={16} className="shrink-0 text-muted" />
                    <span className="truncate text-sm">{doc.fileName}</span>
                    <span className="ml-auto truncate font-mono text-xs text-muted">
                      {doc.documentId.slice(0, 8)}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          </div>

          <RetrievalSettingsPanel
            settings={settings}
            onScopeChange={setScope}
            onMatchCountChange={setMatchCount}
          />

          <div>
            <button
              type="button"
              onClick={handleSubmit}
              disabled={!canSubmit}
              className="inline-flex cursor-pointer items-center gap-2 rounded-lg bg-accent px-5 py-2.5 font-medium text-surface transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {inflight && <Loader2 size={16} className="animate-spin" />}
              {inflight
                ? "Querying..."
                : mode === "compare"
                  ? "Run batch query"
                  : "Synthesize answer"}
            </button>
          </div>

          {error && <InlineAlert>{error}</InlineAlert>}

          {summary && mode === "compare" && (
            <div className="flex flex-wrap gap-4 rounded-lg border border-border bg-surface px-4 py-3 text-sm">
              <span>
                <strong>{summary.count}</strong> total
              </span>
              <span className="text-green-500">
                <strong>{summary.success}</strong> succeeded
              </span>
              <span className={summary.failure > 0 ? "text-red-500" : "text-muted"}>
                <strong>{summary.failure}</strong> failed
              </span>
            </div>
          )}

          <div aria-busy={inflight}>
            {inflight && mode === "synthesize" && <BatchResultSkeleton />}

            {inflight && mode === "compare" && (
              <div className="grid gap-4 md:grid-cols-2">
                {Array.from({ length: selectedDocIds.length }).map((_, index) => (
                  <BatchResultSkeleton key={index} />
                ))}
              </div>
            )}

            {!inflight && results && mode === "synthesize" && results[0] && (
              <BatchResultCard result={results[0]} title={synthesizeTitle} />
            )}

            {!inflight && results && mode === "compare" && (
              <div className="grid gap-4 md:grid-cols-2">
                {results.map((result, index) => {
                  const docId = result.doc_ids[0];
                  const name =
                    (docId && nameById.get(docId)) || docId || "Unknown document";

                  return (
                    <BatchResultCard
                      key={`${docId ?? "doc"}-${index}`}
                      result={result}
                      title={name}
                    />
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}