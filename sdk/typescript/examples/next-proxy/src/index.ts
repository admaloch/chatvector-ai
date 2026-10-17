/**
 * Experimental proxy-oriented React helpers for browser applications.
 * Not published as `@chatvector/react` in v1.
 */

export { ProxyClient, ProxyStreamError, StreamingDisabledError } from "./proxy-client.js";
export type {
  ProxyApplicationError,
  ProxyChatRequest,
  ProxyClientOptions,
  ProxyUploadResult,
} from "./proxy-client.js";
export { useProxyChat } from "./hooks/useProxyChat.js";
export type { UseProxyChatOptions, UseProxyChatResult } from "./hooks/useProxyChat.js";
export { useProxyDocumentUpload } from "./hooks/useProxyDocumentUpload.js";
export type { UseProxyDocumentUploadResult } from "./hooks/useProxyDocumentUpload.js";
export { useProxySessionManager } from "./hooks/useProxySessionManager.js";
export type { UseProxySessionManagerOptions } from "./hooks/useProxySessionManager.js";
