# Next.js proxy + React hooks (experimental)

This example shows how a browser application can use ChatVector **without**
ever holding a ChatVector API key. The browser talks only to your application
origin (`/api/*`); a Node.js proxy (Next.js route handlers in `next-app/`) holds
`CHATVECTOR_API_KEY` server-side.

> [!WARNING]
> **Threat model:** Treat `CHATVECTOR_API_KEY` like a database password. It must
> live only in server environment variables read by the proxy process. Browser
> bundles, `NEXT_PUBLIC_*` variables, client-side hooks, and JSON responses must
> never contain the ChatVector key. Application users authenticate to **your**
> app (session/JWT); the proxy maps that identity to ChatVector calls and
> enforces document/session ownership in your datastore before forwarding.

## Layout

| Path | Role |
| --- | --- |
| `src/proxy-client.ts` | Browser `ProxyClient` — fetch wrapper for `/api/*` |
| `src/hooks/` | `useProxyChat`, `useProxySessionManager`, `useProxyDocumentUpload` |
| `next-app/` | Minimal Next.js App Router proxy routes using `@chatvector/sdk` |
| `tests/` | Mocked streaming test + static client-bundle safety scan |

Hooks import **types only** from `@chatvector/sdk` so model shapes stay aligned
with the server SDK without pulling server runtime code into the browser.

## Security model (summary)

1. **Browser** — calls same-origin `/api/chat`, `/api/documents`, etc. Sends
   your app auth placeholder (`x-user-id` in this demo; replace with cookies or
   bearer tokens issued by your auth layer).
2. **Proxy** — validates the application user, attaches the server-only
   ChatVector bearer token, forwards requests, and strips upstream secrets from
   error payloads.
3. **ChatVector API** — sees only the proxy's server credential.

Streaming (`POST /api/chat/stream`) and document status SSE
(`GET /api/documents/:id/status/stream`) are proxied as raw SSE bodies when
enabled upstream. Hooks fall back to polling when SSE is unavailable (for
example when custom auth headers are required and `EventSource` cannot send
them).

## Run tests

From the repository root, build the TypeScript SDK once so type imports resolve:

```bash
cd sdk/typescript
npm ci
npm run build

cd examples/next-proxy
npm ci
npm test
```

The suite includes:

- `useProxyChat` mocked streaming accumulation
- A static scan of `src/` proving no `CHATVECTOR_API_KEY`, `ChatVectorClient`,
  or runtime `@chatvector/sdk` imports in browser code

## Run the Next.js proxy (optional)

```bash
cd sdk/typescript
npm ci && npm run build

cd examples/next-proxy/next-app
cp .env.example .env.local
# Set CHATVECTOR_BASE_URL and CHATVECTOR_API_KEY in .env.local (server only)
npm ci
npm run dev
```

Wire the hooks into a client component with `ProxyClient` pointed at the same
origin (default). See `next-app/README.md` for route details.

## Related examples

- TypeScript Fastify proxy: [`../fastify-proxy/`](../fastify-proxy/)
- Python FastAPI proxy: [`../../../python/examples/fastapi-proxy/`](../../../python/examples/fastapi-proxy/)
