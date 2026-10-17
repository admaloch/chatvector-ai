import type {
  FeedbackItem,
  FeedbackListResponse,
  ListFeedbackInput,
  PostFeedbackInput,
  RequestOptions,
} from "../models.js";
import { HttpClient } from "../internal/http.js";
import { isRecord, signalOption, stringValue } from "../internal/utils.js";

export class FeedbackResource {
  constructor(private readonly http: HttpClient) {}

  async postFeedback(
    input: PostFeedbackInput,
    options: RequestOptions = {},
  ): Promise<FeedbackItem> {
    const body: Record<string, unknown> = {
      type: input.type,
      rating: input.rating,
      session_id: input.sessionId,
    };
    if (input.comment !== undefined) body.comment = input.comment;
    if (input.messageId !== undefined) body.message_id = input.messageId;
    if (input.documentId !== undefined) body.document_id = input.documentId;
    if (input.chunkIndex !== undefined) body.chunk_index = input.chunkIndex;

    const payload = await this.http.requestJson("POST", "/feedback", {
      json: body,
      ...signalOption(options.signal),
    });
    return mapFeedbackItem(payload);
  }

  async listFeedback(
    input: ListFeedbackInput = {},
    options: RequestOptions = {},
  ): Promise<FeedbackListResponse> {
    const params = new URLSearchParams();
    if (input.sessionId !== undefined) {
      params.set("session_id", input.sessionId);
    }
    if (input.createdAfter !== undefined) {
      params.set("created_after", input.createdAfter);
    }
    if (input.createdBefore !== undefined) {
      params.set("created_before", input.createdBefore);
    }
    if (input.limit !== undefined) {
      params.set("limit", String(input.limit));
    }
    const query = params.toString();
    const path = query ? `/feedback?${query}` : "/feedback";
    const payload = await this.http.requestJson("GET", path, signalOption(options.signal));
    const feedback = Array.isArray(payload.feedback)
      ? payload.feedback.filter(isRecord).map(mapFeedbackItem)
      : [];
    return { feedback, _raw: payload };
  }
}

function mapFeedbackItem(payload: Record<string, unknown>): FeedbackItem {
  const item: FeedbackItem = {
    id: stringValue(payload.id),
    type: stringValue(payload.type),
    rating: typeof payload.rating === "number" ? payload.rating : 0,
    sessionId: stringValue(payload.session_id),
    createdAt: stringValue(payload.created_at),
    _raw: payload,
  };
  if (typeof payload.comment === "string" || payload.comment === null) {
    item.comment = payload.comment;
  }
  if (typeof payload.message_id === "string" || payload.message_id === null) {
    item.messageId = payload.message_id;
  }
  if (typeof payload.document_id === "string" || payload.document_id === null) {
    item.documentId = payload.document_id;
  }
  if (typeof payload.chunk_index === "number" || payload.chunk_index === null) {
    item.chunkIndex = payload.chunk_index;
  }
  return item;
}
